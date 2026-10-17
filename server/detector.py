"""Detector protocol and the YOLO adapter. ultralytics is imported only inside the adapter."""

import importlib
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from domain import Box, Detection


class Detector(Protocol):
    name: str
    device: str

    def load(self) -> None:
        """Load the model and warm it up. Blocking; called off the event loop."""
        ...

    def detect(self, image: Image.Image) -> list[Detection]:
        """Unfiltered detections with normalized boxes."""
        ...


def to_normalized_box(xyxy: tuple[float, float, float, float], width: int, height: int) -> Box:
    x1, y1, x2, y2 = xyxy

    def clamp(v: float) -> float:
        return min(1.0, max(0.0, v))

    return Box(clamp(x1 / width), clamp(y1 / height), clamp(x2 / width), clamp(y2 / height))


def pick_device(override: str, cuda: bool, mps: bool) -> str:
    if override:
        return override
    if cuda:
        return "cuda"
    return "mps" if mps else "cpu"


class YoloDetector:
    def __init__(self, model_path: str, imgsz: int, device: str, min_conf: float) -> None:
        self.name = Path(model_path).stem
        self.device = device
        self._model_path = model_path
        self._imgsz = imgsz
        self._min_conf = min_conf
        self._model: Any = None

    def load(self) -> None:
        # import_module keeps mypy happy whether or not the ML deps are installed (CI has none).
        torch: Any = importlib.import_module("torch")
        YOLO = importlib.import_module("ultralytics").YOLO

        self.device = pick_device(
            self.device,
            cuda=torch.cuda.is_available(),
            mps=torch.backends.mps.is_available(),
        )
        self._model = YOLO(self._model_path)
        self.detect(Image.new("RGB", (self._imgsz, self._imgsz)))

    def detect(self, image: Image.Image) -> list[Detection]:
        result = self._model.predict(
            image, imgsz=self._imgsz, device=self.device, conf=self._min_conf, verbose=False
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
