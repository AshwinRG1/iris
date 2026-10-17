"""Detector protocol and the YOLO adapter. ultralytics is imported only inside the adapter."""

import hashlib
import logging
from collections.abc import Callable, Sequence
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
    """What /health reports about the loaded model."""

    file: str
    sha256: str
    class_names: tuple[str, ...]
    fallback: str | None  # why the configured model was not used, if it wasn't


class Detector(Protocol):
    name: str
    device: str  # the requested device; load() returns the one actually in use
    model_info: ModelInfo  # meaningful after load()

    def load(self) -> str:
        """Load the model, warm it up and return the device in use.

        Blocking; called off the event loop. The caller stores the device.
        """
        ...

    def detect(self, image: Image.Image) -> list[Detection]:
        """Unfiltered detections with normalized boxes."""
        ...


def choose_model(
    class_names: Sequence[str], alert_labels: frozenset[str]
) -> Literal["use", "fall_back"]:
    """Use the model only if it can detect at least one alert label."""
    return "use" if alert_labels.intersection(class_names) else "fall_back"


def file_sha256(path: str) -> str:
    """Hex digest of the file, or "" if there is no such file."""
    p = Path(path)
    if not p.is_file():
        return ""
    digest = hashlib.sha256()
    with p.open("rb") as f:
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


class YoloDetector:
    def __init__(self, model_path: str, imgsz: int, device: str, min_conf: float) -> None:
        self.name = Path(model_path).stem
        self.device = device
        self._device = device  # used only by the inference slot thread
        self._model_path = model_path
        self._imgsz = imgsz
        # Applied as ultralytics' confidence floor. The pipeline filters by CONF_DETECT again.
        self._min_conf = min_conf
        self._model: Any = None
        self.model_info = ModelInfo(model_path, "", (), None)

    def load(self) -> str:
        import torch
        from ultralytics import YOLO

        self._device = pick_device(
            self.device,
            cuda=torch.cuda.is_available(),
            mps=torch.backends.mps.is_available(),
            onnx=self._model_path.endswith(".onnx"),
        )
        self._model = YOLO(self._model_path)
        names = self._model.names  # dict of class id to name
        self.model_info = ModelInfo(
            self._model_path,
            file_sha256(self._model_path),
            tuple(names[i] for i in sorted(names)),
            None,
        )
        self.detect(Image.new("RGB", (self._imgsz, self._imgsz)))
        return self._device

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
    """Loads the configured model; on missing weights or no alert label, loads stock YOLO26s."""

    def __init__(self, factory: ModelFactory, settings: Settings) -> None:
        self._factory = factory
        self._settings = settings
        self._active: Detector | None = None
        self.name = Path(settings.model_path).stem
        self.device = settings.device
        self.model_info = ModelInfo(settings.model_path, "", (), None)

    def load(self) -> str:
        s = self._settings
        active, device, reason = self._try(s.model_path, s.imgsz)
        if active is not None and (
            choose_model(active.model_info.class_names, s.alert_label_set) == "fall_back"
        ):
            names = list(active.model_info.class_names)
            labels = sorted(s.alert_label_set)
            reason = f"{s.model_path} has none of the alert labels {labels}; it has {names}"
            active = None
        if active is None:
            log.error("FALLING BACK to %s: %s", STOCK_MODEL, reason)
            active = self._factory(STOCK_MODEL, STOCK_IMGSZ)
            device = active.load()
            self.model_info = replace(active.model_info, fallback=reason)
        else:
            self.model_info = active.model_info
        self._active = active
        self.name = active.name
        return device

    def _try(self, path: str, imgsz: int) -> tuple[Detector | None, str, str | None]:
        detector = self._factory(path, imgsz)
        try:
            return detector, detector.load(), None
        except Exception as e:
            return None, "", f"{path} failed to load: {type(e).__name__}: {e}"

    def detect(self, image: Image.Image) -> list[Detection]:
        if self._active is None:
            raise RuntimeError("detect() called before load()")
        return self._active.detect(image)


class ZeroShotDetector:
    """Wraps D's plain `detect(image) -> [{"label", "conf", "box": [x1, y1, x2, y2]}]`."""

    def __init__(
        self, detect_fn: Callable[[Image.Image], list[dict[str, Any]]], device: str
    ) -> None:
        self.name = "zeroshot"
        self.device = device
        self.model_info = ModelInfo("zeroshot", "", (), None)
        self._detect = detect_fn

    def load(self) -> str:
        return self.device

    def detect(self, image: Image.Image) -> list[Detection]:
        out: list[Detection] = []
        for item in self._detect(image):
            x1, y1, x2, y2 = (min(1.0, max(0.0, float(v))) for v in item["box"])
            out.append(Detection(str(item["label"]), float(item["conf"]), Box(x1, y1, x2, y2)))
        return out
