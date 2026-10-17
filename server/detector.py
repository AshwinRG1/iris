"""Detector protocol, the YOLO and zero-shot adapters, and the fallback to stock YOLO26s.

ultralytics and torch are imported only inside the adapters, so tests and CI don't need them.
"""

import hashlib
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from PIL import Image

from config import Settings
from domain import Box, Detection

log = logging.getLogger("iris")

STOCK_MODEL = "yolo26s.pt"
STOCK_IMGSZ = 640


@dataclass(frozen=True)
class ModelInfo:
    """What the open /health reports about the model. None means unknown (e.g. before load)."""

    file: str | None  # basename only
    sha256: str | None
    class_names: tuple[str, ...] | None
    fallback: str | None  # "load_failed: <ExceptionTypeName>" or "no_alert_label"


NO_MODEL = ModelInfo(None, None, None, None)


@dataclass(frozen=True)
class Loaded:
    """What load() hands back from the inference slot. The loop stores it on Runtime."""

    device: str  # the device actually in use
    name: str
    info: ModelInfo


class NoAlertLabelError(Exception):
    """The model can't detect any alert label."""


class Detector(Protocol):
    name: str  # fixed at construction; load() reports the name actually in use
    device: str  # the requested device

    def load(self) -> Loaded:
        """Load the model and warm it up. Blocking; called off the event loop.

        Must not publish state the loop reads: the caller stores the result on Runtime.
        """
        ...

    def detect(self, image: Image.Image) -> list[Detection]:
        """Unfiltered detections with normalized boxes."""
        ...


def choose_model(
    class_names: Sequence[str], alert_labels: frozenset[str]
) -> Literal["use", "fall_back"]:
    """Use the model only if it can detect at least one alert label (case-insensitive)."""
    have = {n.lower() for n in class_names}
    return "use" if have.intersection(label.lower() for label in alert_labels) else "fall_back"


def require_alert_label(class_names: Sequence[str], alert_labels: frozenset[str]) -> None:
    if choose_model(class_names, alert_labels) == "fall_back":
        raise NoAlertLabelError(
            f"model classes {list(class_names)} include none of the alert labels "
            f"{sorted(alert_labels)}"
        )


def normalize_class_names(names: Mapping[int, str] | Sequence[str]) -> tuple[str, ...]:
    """Class names in class-id order, from ultralytics' dict or a plain list."""
    if isinstance(names, Mapping):
        return tuple(str(names[i]) for i in sorted(names))
    return tuple(str(n) for n in names)


def file_sha256(path: Path) -> str | None:
    """Hex digest of the file, or None if there is no such file."""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def to_normalized_box(xyxy: tuple[float, float, float, float], width: int, height: int) -> Box:
    x1, y1, x2, y2 = xyxy

    def clamp(v: float) -> float:
        return min(1.0, max(0.0, v))

    return Box(clamp(x1 / width), clamp(y1 / height), clamp(x2 / width), clamp(y2 / height))


def pick_device(override: str, cuda: bool, mps: bool, onnx: bool = False) -> str:
    if override:
        return override
    if onnx:  # ONNX files are served on CPU (ADR 0003)
        return "cpu"
    if cuda:
        return "cuda"
    return "mps" if mps else "cpu"


def auto_device(override: str, onnx: bool = False) -> str:
    """pick_device with the real hardware probe. Imports torch, so adapters call it in load()."""
    import torch

    return pick_device(
        override,
        cuda=torch.cuda.is_available(),
        mps=torch.backends.mps.is_available(),
        onnx=onnx,
    )


class YoloDetector:
    def __init__(
        self,
        model_path: str,
        imgsz: int,
        device: str,
        min_conf: float,
        alert_labels: frozenset[str],
    ) -> None:
        self.name = Path(model_path).stem
        self.device = device
        self._device = device  # used only by the inference slot thread
        self._model_path = model_path
        self._imgsz = imgsz
        # Applied as ultralytics' confidence floor. The pipeline filters by CONF_DETECT again.
        self._min_conf = min_conf
        self._alert_labels = alert_labels
        self._model: Any = None

    def load(self) -> Loaded:
        from ultralytics import YOLO

        device = auto_device(self.device, onnx=self._model_path.endswith(".onnx"))
        model = YOLO(self._model_path)
        # Cheapest check first: before hashing the file and warming up.
        names = normalize_class_names(model.names)
        require_alert_label(names, self._alert_labels)
        self._model, self._device = model, device
        self.detect(Image.new("RGB", (self._imgsz, self._imgsz)))
        # The file the model actually loaded from (ultralytics may have downloaded it).
        source = Path(getattr(model, "ckpt_path", None) or self._model_path).resolve()
        info = ModelInfo(source.name, file_sha256(source), names, None)
        return Loaded(device, self.name, info)

    def detect(self, image: Image.Image) -> list[Detection]:
        result = self._model.predict(
            image, imgsz=self._imgsz, device=self._device, conf=self._min_conf, verbose=False
        )[0]
        height, width = result.orig_shape
        boxes = result.boxes
        return [
            Detection(
                result.names[int(cls)],
                float(conf),
                to_normalized_box((xyxy[0], xyxy[1], xyxy[2], xyxy[3]), width, height),
            )
            for xyxy, conf, cls in zip(
                boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist(), strict=True
            )
        ]


ModelFactory = Callable[[str, int], Detector]


class FallbackDetector:
    """Loads the configured model; if that fails or can't alert, loads stock YOLO26s instead."""

    def __init__(self, factory: ModelFactory, settings: Settings) -> None:
        self._factory = factory
        self._settings = settings
        self._active: Detector | None = None
        self.name = Path(settings.model_path).stem
        self.device = settings.device

    def load(self) -> Loaded:
        s = self._settings
        try:
            return self._build(s.model_path, s.imgsz)
        except Exception as e:
            if s.model_path == STOCK_MODEL:
                raise  # nothing further to fall back to; the caller reports load_error
            reason = (
                "no_alert_label"
                if isinstance(e, NoAlertLabelError)
                else f"load_failed: {type(e).__name__}"
            )
            log.error(
                "FALLING BACK to %s (%s): %s", STOCK_MODEL, reason, s.model_path, exc_info=True
            )
        stock = self._build(STOCK_MODEL, STOCK_IMGSZ)
        return replace(stock, info=replace(stock.info, fallback=reason))

    def _build(self, path: str, imgsz: int) -> Loaded:
        detector = self._factory(path, imgsz)
        loaded = detector.load()
        self._active = detector
        return loaded

    def detect(self, image: Image.Image) -> list[Detection]:
        if self._active is None:
            raise RuntimeError("detect() called before load()")
        return self._active.detect(image)


DetectFn = Callable[[Image.Image], list[dict[str, Any]]]


def _box_from_dict(raw: Any) -> Box:
    """D's box format, in one place.

    ASSUMPTION, confirm with D: a sequence [x1, y1, x2, y2] of floats normalized to 0..1.
    """
    x1, y1, x2, y2 = (float(v) for v in raw)
    if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in (x1, y1, x2, y2)):
        raise ValueError("box outside 0..1")
    return Box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


class ZeroShotDetector:
    """Wraps D's plain `detect(image) -> [{"label", "conf", "box"}]`; D never imports this."""

    def __init__(
        self,
        get_detect: Callable[[], DetectFn],
        device: str,
        resolve_device: Callable[[str], str] = auto_device,
    ) -> None:
        self.name = "zeroshot"
        self.device = device
        self._get_detect = get_detect  # imports D's module; called in load(), in the slot
        self._resolve_device = resolve_device
        self._detect: DetectFn | None = None
        self._warned: set[str] = set()

    def load(self) -> Loaded:
        device = self._resolve_device(self.device)
        detect = self._get_detect()
        detect(Image.new("RGB", (64, 64)))  # warmup
        self._detect = detect
        return Loaded(device, self.name, NO_MODEL)

    def detect(self, image: Image.Image) -> list[Detection]:
        if self._detect is None:
            raise RuntimeError("detect() called before load()")
        out: list[Detection] = []
        for item in self._detect(image):
            try:
                conf = float(item["conf"])
                if not math.isfinite(conf):
                    raise ValueError("non-finite conf")
                box = _box_from_dict(item["box"])
                out.append(Detection(str(item["label"]), min(1.0, max(0.0, conf)), box))
            except (KeyError, ValueError, TypeError) as e:
                key = f"{type(e).__name__}: {e}"
                if key not in self._warned:  # once per distinct problem, not per frame
                    self._warned.add(key)
                    log.warning("zeroshot: skipping a malformed detection (%s): %r", key, item)
        return out
