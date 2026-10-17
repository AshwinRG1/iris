import threading
from dataclasses import dataclass
from typing import Any

import pytest
from conftest import (
    FakeClock,
    FakeDescriber,
    FakeDetector,
    FakeWallClock,
    det,
    jpeg_bytes,
    wait_for,
)
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from config import Settings
from detector import (
    STOCK_MODEL,
    Detector,
    FallbackDetector,
    Loaded,
    ModelInfo,
    ZeroShotDetector,
    require_alert_label,
)
from main import build_detector, create_app


@dataclass
class Model:
    """What a fake factory knows about one model file."""

    classes: tuple[str, ...] = ("person",)
    error: Exception | None = None
    release: threading.Event | None = None


class FakeYolo(FakeDetector):
    """Stands in for the YOLO adapter: fails or reports its classes like the real one."""

    def __init__(self, path: str, model: Model, labels: frozenset[str]) -> None:
        super().__init__()
        self.path, self.model, self.labels = path, model, labels

    def load(self) -> Loaded:
        if self.model.release:
            self.model.release.wait(5)
        if self.model.error:
            raise self.model.error
        require_alert_label(self.model.classes, self.labels)
        info = ModelInfo(self.path.rsplit("/", 1)[-1], f"sha-{self.path}", self.model.classes, None)
        return Loaded("cpu", self.path.rsplit("/", 1)[-1].split(".")[0], info)


def _app(settings: Settings, models: dict[str, Model]) -> TestClient:
    def factory(path: str, imgsz: int) -> Detector:
        return FakeYolo(path, models[path], settings.alert_label_set)

    detector = FallbackDetector(factory, settings)
    app = create_app(detector, FakeDescriber(), FakeClock(), FakeWallClock(), settings)
    return TestClient(app)


def _settled(c: TestClient) -> dict[str, Any]:
    def done() -> bool:
        body = c.get("/health").json()
        return bool(body["ready"] or body["load_error"])

    wait_for(done)
    health: dict[str, Any] = c.get("/health").json()
    return health


STOCK = Model(("person", "car"))


def test_health_shows_the_swapped_in_model_by_basename() -> None:
    settings = Settings(model_path="/srv/weights/c.onnx", alert_labels="stairs")
    models = {"/srv/weights/c.onnx": Model(("stairs", "door")), STOCK_MODEL: STOCK}
    with _app(settings, models) as c:
        health = _settled(c)
    assert health["ready"] is True
    assert health["detector"] == "c"
    assert health["model_file"] == "c.onnx"
    assert health["model_sha256"] == "sha-/srv/weights/c.onnx"
    assert health["class_names"] == ["stairs", "door"]
    assert health["fallback"] is None


def test_health_has_nulls_before_the_model_finishes_loading() -> None:
    release = threading.Event()
    settings = Settings(model_path="c.pt")
    try:
        with _app(settings, {"c.pt": Model(release=release)}) as c:
            health = c.get("/health").json()
            assert health["ready"] is False
            assert health["model_file"] is None
            assert health["model_sha256"] is None
            assert health["class_names"] is None
            assert health["fallback"] is None
            release.set()
            assert _settled(c)["class_names"] == ["person"]
    finally:
        release.set()


def test_missing_weights_fall_back_to_stock_without_leaking_the_path() -> None:
    settings = Settings(model_path="/home/iris/secret/gone.pt")
    err = FileNotFoundError("/home/iris/secret/gone.pt")
    models = {settings.model_path: Model(error=err), STOCK_MODEL: STOCK}
    with _app(settings, models) as c:
        response = c.get("/health")
        health = _settled(c)
        text = c.get("/health").text
    assert response.status_code == 200
    assert health["ready"] is True
    assert health["model_file"] == STOCK_MODEL
    assert health["class_names"] == ["person", "car"]
    assert health["fallback"] == "load_failed: FileNotFoundError"
    assert "gone" not in text
    assert "secret" not in text


def test_factory_that_raises_falls_back_too() -> None:
    settings = Settings(model_path="c.pt")

    def factory(path: str, imgsz: int) -> Detector:
        if path == "c.pt":
            raise RuntimeError("cannot build")
        return FakeYolo(path, STOCK, settings.alert_label_set)

    app = create_app(
        FallbackDetector(factory, settings),
        FakeDescriber(),
        FakeClock(),
        FakeWallClock(),
        settings,
    )
    with TestClient(app) as c:
        health = _settled(c)
    assert health["ready"] is True
    assert health["fallback"] == "load_failed: RuntimeError"


def test_model_without_an_alert_label_falls_back_to_stock() -> None:
    settings = Settings(model_path="c.pt", alert_labels="person")
    models = {"c.pt": Model(("stairs",)), STOCK_MODEL: STOCK}
    with _app(settings, models) as c:
        health = _settled(c)
    assert health["model_file"] == STOCK_MODEL
    assert health["fallback"] == "no_alert_label"


def test_stock_model_without_an_alert_label_is_a_load_error() -> None:
    settings = Settings(model_path=STOCK_MODEL, alert_labels="stairs")
    with _app(settings, {STOCK_MODEL: STOCK}) as c:
        health = _settled(c)
        r = c.post("/frame", files={"file": ("f.jpg", jpeg_bytes(), "image/jpeg")})
    assert health["ready"] is False
    assert "NoAlertLabelError" in health["load_error"]
    assert r.status_code == 503


def test_stock_failing_to_load_is_not_retried_and_is_a_load_error() -> None:
    settings = Settings(model_path=STOCK_MODEL)
    loads: list[str] = []

    def factory(path: str, imgsz: int) -> Detector:
        loads.append(path)
        return FakeYolo(path, Model(error=OSError("disk")), settings.alert_label_set)

    app = create_app(
        FallbackDetector(factory, settings),
        FakeDescriber(),
        FakeClock(),
        FakeWallClock(),
        settings,
    )
    with TestClient(app) as c:
        health = _settled(c)
    assert loads == [STOCK_MODEL]
    assert health["ready"] is False
    assert health["load_error"].startswith("OSError")


def test_stock_failing_after_a_fallback_is_a_load_error() -> None:
    settings = Settings(model_path="gone.pt")
    models = {"gone.pt": Model(error=OSError()), STOCK_MODEL: Model(error=OSError())}
    with _app(settings, models) as c:
        health = _settled(c)
    assert health["ready"] is False
    assert health["load_error"] is not None


def test_alert_labels_match_class_names_case_insensitively() -> None:
    settings = Settings(model_path="c.pt", alert_labels="Person")
    with _app(settings, {"c.pt": Model(("PERSON", "Car"))}) as c:
        health = _settled(c)
    assert health["fallback"] is None
    assert health["class_names"] == ["PERSON", "Car"]


def test_frame_filter_is_case_insensitive() -> None:
    settings = Settings(alert_labels="PERSON")
    detector = FakeDetector([[det("Person"), det("car")]])
    app = create_app(detector, FakeDescriber(), FakeClock(), FakeWallClock(), settings)
    with TestClient(app) as c:
        wait_for(lambda: c.get("/health").json()["ready"])
        r = c.post("/frame", files={"file": ("f.jpg", jpeg_bytes(), "image/jpeg")})
    assert [d["label"] for d in r.json()["detections"]] == ["Person"]


@pytest.mark.parametrize("value", ["", " , ,"])
def test_empty_alert_labels_are_rejected_at_startup(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(alert_labels=value)


def _zero_shot_app(
    get_detect: Any, settings: Settings | None = None
) -> tuple[TestClient, ZeroShotDetector]:
    s = settings or Settings(detector="zeroshot")
    detector = ZeroShotDetector(get_detect, s.device, resolve_device=lambda d: d or "cpu")
    app = create_app(detector, FakeDescriber(), FakeClock(), FakeWallClock(), s)
    return TestClient(app), detector


def test_zero_shot_serves_frames_through_the_app() -> None:
    def fake_detect(image: Image.Image) -> list[dict[str, Any]]:
        return [
            {"label": "person", "conf": 0.9, "box": [0.1, 0.2, 0.3, 0.8]},
            {"label": "person", "conf": 0.9, "box": [0.1, 0.2, "x", 0.8]},
            {"label": "person", "conf": float("nan"), "box": [0.1, 0.2, 0.3, 0.8]},
            {"label": "person", "conf": 0.9, "box": [0.1, 0.2, 1.5, 0.8]},
            {"conf": 0.9, "box": [0.1, 0.2, 0.3, 0.8]},
        ]

    c, _ = _zero_shot_app(lambda: fake_detect)
    with c:
        health = _settled(c)
        r = c.post("/frame", files={"file": ("f.jpg", jpeg_bytes(), "image/jpeg")})
    assert health["ready"] is True
    assert health["detector"] == "zeroshot"
    assert health["device"] == "cpu"
    assert health["model_file"] is None
    assert health["class_names"] is None
    assert r.status_code == 200
    assert [(d["label"], d["side"]) for d in r.json()["detections"]] == [("person", "left")]


def test_zero_shot_import_failure_is_a_load_error_not_a_crash() -> None:
    def broken() -> Any:
        raise ImportError("No module named 'zeroshot'")

    c, _ = _zero_shot_app(broken)
    with c:
        health = _settled(c)
        r = c.post("/frame", files={"file": ("f.jpg", jpeg_bytes(), "image/jpeg")})
    assert health["ready"] is False
    assert health["load_error"].startswith("ImportError")
    assert r.status_code == 503


def test_zero_shot_warms_up_once_during_load() -> None:
    calls: list[tuple[int, int]] = []

    def fake_detect(image: Image.Image) -> list[dict[str, Any]]:
        calls.append(image.size)
        return []

    c, _ = _zero_shot_app(lambda: fake_detect)
    with c:
        _settled(c)
    assert len(calls) == 1


def test_detector_setting_selects_the_adapter() -> None:
    assert isinstance(
        build_detector(Settings(detector="zeroshot"), lambda: lambda image: []),
        ZeroShotDetector,
    )
    assert isinstance(build_detector(Settings()), FallbackDetector)
