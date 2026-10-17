import time

from conftest import FakeClock, FakeDescriber, FakeDetector, FakeWallClock
from fastapi.testclient import TestClient

from config import Settings
from detector import STOCK_MODEL, Detector, FallbackDetector, ModelInfo
from main import create_app


class LoadableDetector(FakeDetector):
    """Stands in for a YOLO adapter: loading reports the model's file and classes."""

    def __init__(self, path: str, classes: tuple[str, ...], fail: bool = False) -> None:
        super().__init__()
        self.name = path
        self.path, self.classes, self.fail = path, classes, fail

    def load(self) -> str:
        if self.fail:
            raise FileNotFoundError(f"{self.path} not found")
        self.model_info = ModelInfo(self.path, f"sha-of-{self.path}", self.classes, None)
        return self.device


def _health(settings: Settings, models: dict[str, LoadableDetector]) -> dict[str, object]:
    def factory(path: str, imgsz: int) -> Detector:
        return models[path]

    detector = FallbackDetector(factory, settings)
    app = create_app(detector, FakeDescriber(), FakeClock(), FakeWallClock(), settings)
    with TestClient(app) as c:
        for _ in range(100):
            body: dict[str, object] = c.get("/health").json()
            if body["ready"] or body["load_error"]:
                return body
            time.sleep(0.05)
    raise AssertionError("never finished loading")


def _stock() -> LoadableDetector:
    return LoadableDetector(STOCK_MODEL, ("person", "car"))


def test_health_shows_the_swapped_in_model() -> None:
    settings = Settings(model_path="c.onnx", alert_labels="stairs")
    models = {"c.onnx": LoadableDetector("c.onnx", ("stairs", "door")), STOCK_MODEL: _stock()}
    health = _health(settings, models)
    assert health["ready"] is True
    assert health["model_file"] == "c.onnx"
    assert health["model_sha256"] == "sha-of-c.onnx"
    assert health["class_names"] == ["stairs", "door"]
    assert health["fallback"] is None


def test_missing_weights_fall_back_to_stock_and_say_so() -> None:
    settings = Settings(model_path="gone.pt")
    models = {"gone.pt": LoadableDetector("gone.pt", (), fail=True), STOCK_MODEL: _stock()}
    health = _health(settings, models)
    assert health["ready"] is True
    assert health["model_file"] == STOCK_MODEL
    assert health["class_names"] == ["person", "car"]
    assert "gone.pt" in str(health["fallback"])
    assert "FileNotFoundError" in str(health["fallback"])


def test_model_without_an_alert_label_falls_back_to_stock_and_says_so() -> None:
    settings = Settings(model_path="c.pt", alert_labels="person")
    models = {"c.pt": LoadableDetector("c.pt", ("stairs",)), STOCK_MODEL: _stock()}
    health = _health(settings, models)
    assert health["model_file"] == STOCK_MODEL
    assert "person" in str(health["fallback"])
    assert "c.pt" in str(health["fallback"])


def test_stock_weights_failing_too_is_a_load_error() -> None:
    settings = Settings(model_path="gone.pt")
    models = {
        "gone.pt": LoadableDetector("gone.pt", (), fail=True),
        STOCK_MODEL: LoadableDetector(STOCK_MODEL, (), fail=True),
    }
    health = _health(settings, models)
    assert health["ready"] is False
    assert "FileNotFoundError" in str(health["load_error"])
