"""/health reply types, pinned to what the iOS app's IrisServerHealth decodes.

Swift decodes `ready`/`detector` as non-optional, `server_ms_p50/p95` as Int?, and `fps` as
Double?. A wrong type in any field fails the whole decode, so the types are asserted here.
"""

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    FakeClock,
    FakeDescriber,
    FakeDetector,
    FakeWallClock,
    SettableClock,
    TimedDetector,
    jpeg_bytes,
    wait_for,
)
from fastapi.testclient import TestClient

from config import Settings
from detector import Loaded, ModelInfo
from main import create_app

FIXTURE = Path(__file__).resolve().parents[2] / "docs" / "contract" / "health.json"

_STR_OR_NULL = ("device", "load_error", "auth", "commit", "fallback")
_INT_OR_NULL = ("server_ms_p50", "server_ms_p95")


def check_health_types(body: dict[str, Any]) -> None:
    """Mirror of IrisServerHealth: raises AssertionError on the first wrong type."""
    assert type(body["ready"]) is bool, body["ready"]
    assert isinstance(body["detector"], str), body["detector"]
    for key in _STR_OR_NULL:
        assert body[key] is None or isinstance(body[key], str), (key, body[key])
    assert type(body["fps"]) in (int, float), body["fps"]
    for key in _INT_OR_NULL:
        assert body[key] is None or type(body[key]) is int, (key, body[key])


class _Detector(FakeDetector):
    def __init__(self, info: ModelInfo | None = None) -> None:
        super().__init__()
        self.info = info

    def load(self) -> Loaded:
        loaded = super().load()
        return loaded if self.info is None else Loaded(loaded.device, loaded.name, self.info)


class _Blocked(FakeDetector):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def load(self) -> Loaded:
        self.release.wait(timeout=5)
        return super().load()


class _Failing(FakeDetector):
    def load(self) -> Loaded:
        raise FileNotFoundError("/secret/path/model.onnx")


def _app_client(detector: FakeDetector, clock: Any = None) -> TestClient:
    app = create_app(detector, FakeDescriber(), clock or FakeClock(), FakeWallClock(), Settings())
    return TestClient(app)


def _health(c: TestClient) -> dict[str, Any]:
    r = c.get("/health")
    assert r.status_code == 200
    body: dict[str, Any] = r.json()
    return body


def _post(c: TestClient) -> None:
    r = c.post("/frame", files={"file": ("f.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 200, r.text


def test_health_types_while_loading() -> None:
    d = _Blocked()
    with _app_client(d) as c:
        body = _health(c)
        assert body["ready"] is False
        check_health_types(body)
        d.release.set()


def test_health_types_when_ready() -> None:
    with _app_client(_Detector()) as c:
        wait_for(lambda: _health(c)["ready"])
        check_health_types(_health(c))


def test_health_types_after_frames() -> None:
    with _app_client(_Detector()) as c:
        wait_for(lambda: _health(c)["ready"])
        for _ in range(3):
            _post(c)
        body = _health(c)
    assert body["server_ms_p50"] is not None
    check_health_types(body)


def test_health_types_when_load_failed() -> None:
    with _app_client(_Failing()) as c:
        wait_for(lambda: _health(c)["load_error"] is not None)
        body = _health(c)
    assert body["ready"] is False
    check_health_types(body)


@pytest.mark.parametrize("fallback", ["no_alert_label", "load_failed: FileNotFoundError"])
def test_health_types_in_fallback(fallback: str) -> None:
    info = ModelInfo("yolo26s.onnx", "0" * 64, ("person",), fallback)
    with _app_client(_Detector(info)) as c:
        wait_for(lambda: _health(c)["ready"])
        body = _health(c)
    assert body["fallback"] == fallback
    check_health_types(body)


def test_fixture_passes_the_type_checker() -> None:
    check_health_types(json.loads(FIXTURE.read_text()))


def test_checker_rejects_wrong_types() -> None:
    good = json.loads(FIXTURE.read_text())
    for key, bad in [
        ("ready", None),
        ("ready", 1),
        ("detector", None),
        ("fps", True),
        ("server_ms_p50", 12.0),
        ("server_ms_p95", True),
        ("auth", False),
    ]:
        with pytest.raises(AssertionError):
            check_health_types({**good, key: bad})


def test_reply_keys_equal_fixture_keys() -> None:
    fixture = json.loads(FIXTURE.read_text())
    with _app_client(_Detector()) as c:
        wait_for(lambda: _health(c)["ready"])
        assert set(_health(c)) == set(fixture)


def test_percentiles_are_ints_for_varied_server_ms() -> None:
    clock = SettableClock(1000)
    d = TimedDetector(clock)
    app = create_app(d, FakeDescriber(), clock, FakeWallClock(), Settings())
    with TestClient(app) as c:
        wait_for(lambda: _health(c)["ready"])
        for cost in (7, 13, 21, 40, 95):  # odd count: p50 lands on one sample
            d.cost_ms = cost
            clock.t += 1000
            _post(c)
        body = _health(c)
    assert type(body["server_ms_p50"]) is int
    assert type(body["server_ms_p95"]) is int
    assert body["server_ms_p50"] == 21
    assert body["server_ms_p95"] == 95
    check_health_types(body)
