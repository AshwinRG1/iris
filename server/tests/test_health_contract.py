"""/health reply types, pinned to what the iOS app's IrisServerHealth decodes.

Swift decodes `ready`/`detector` as non-optional, the rest with decodeIfPresent. A wrong type in
any field fails the whole decode, so the types are asserted here in every server state.

docs/contract/health.json is a custom-model reply (classes match frame-extended.json). Its
`model_sha256` is a 64-hex placeholder: JSON cannot hold a comment, so this says so instead.
"""

import json
from typing import Any

import pytest
from conftest import (
    CONTRACT,
    BrokenLoad,
    FakeClock,
    FakeDescriber,
    FakeDetector,
    FakeWallClock,
    SlowLoad,
    jpeg_bytes,
    wait_for,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr

from config import Settings
from deps import Clock
from detector import Loaded, ModelInfo
from main import create_app

SECRET = "sekret-gemini-key"
FIXTURE = CONTRACT / "health.json"

_STR_OR_NULL = ("device", "load_error", "auth", "commit", "fallback", "describe", "gemini_model")
_INT_OR_NULL = ("server_ms_p50", "server_ms_p95")


def _fixture() -> dict[str, Any]:
    body: dict[str, Any] = json.loads(FIXTURE.read_text())
    return body


def check_health_types(body: dict[str, Any]) -> None:
    """Mirror of IrisServerHealth: raises AssertionError on the first wrong type."""
    assert "ready" in body and "detector" in body, sorted(body)
    assert type(body["ready"]) is bool, body["ready"]
    assert isinstance(body["detector"], str), body["detector"]
    # Optional in Swift (decodeIfPresent): absent or null is fine.
    for key in _STR_OR_NULL:
        assert body.get(key) is None or isinstance(body[key], str), (key, body[key])
    fps = body.get("fps")
    assert fps is None or type(fps) in (int, float), fps
    for key in _INT_OR_NULL:
        # Deliberately stricter than Swift: reject 12.0 so a rounding change shows up.
        assert body.get(key) is None or type(body[key]) is int, (key, body[key])


def check_server_guarantees(body: dict[str, Any]) -> None:
    """What this server promises beyond what Swift tolerates."""
    assert isinstance(body["device"], str), body["device"]
    assert body["auth"] in {"on", "off"}, body["auth"]
    assert body["describe"] in {"on", "off"}, body["describe"]
    assert body["fps"] is not None
    assert set(body) == set(_fixture()), "update docs/contract/health.json"


def check_all(body: dict[str, Any]) -> None:
    check_health_types(body)
    check_server_guarantees(body)


class _Detector(FakeDetector):
    def __init__(self, info: ModelInfo) -> None:
        super().__init__()
        self.info = info

    def load(self) -> Loaded:
        loaded = super().load()
        return Loaded(loaded.device, loaded.name, self.info)


def _app_client(detector: FakeDetector, clock: Clock | None = None) -> TestClient:
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


def test_health_types_while_loading(slow: SlowLoad) -> None:
    with _app_client(slow) as c:
        body = _health(c)
        assert body["ready"] is False
        check_all(body)


def test_health_types_when_ready() -> None:
    with _app_client(FakeDetector()) as c:
        wait_for(lambda: _health(c)["ready"])
        check_all(_health(c))


def test_health_types_after_frames() -> None:
    with _app_client(FakeDetector()) as c:
        wait_for(lambda: _health(c)["ready"])
        for _ in range(3):
            _post(c)
        body = _health(c)
    assert body["server_ms_p50"] is not None
    check_all(body)


def test_health_types_when_load_failed_and_nothing_leaks() -> None:
    broken = BrokenLoad(FileNotFoundError("/secret/path/model.onnx"))
    with _app_client(broken) as c:
        wait_for(lambda: _health(c)["load_error"] is not None)
        r = c.get("/health")
    body = r.json()
    assert body["ready"] is False
    assert body["load_error"] == "FileNotFoundError"
    assert "/secret/path/model.onnx" not in r.text
    check_all(body)


@pytest.mark.parametrize("fallback", ["no_alert_label", "load_failed: FileNotFoundError"])
def test_health_types_in_fallback(fallback: str) -> None:
    info = ModelInfo("iris-v1.onnx", "0" * 64, ("person",), fallback)
    with _app_client(_Detector(info)) as c:
        wait_for(lambda: _health(c)["ready"])
        body = _health(c)
    assert body["fallback"] == fallback
    check_all(body)


def test_fixture_passes_the_checkers() -> None:
    check_all(_fixture())


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("ready", None),
        ("ready", 1),
        ("detector", None),
        ("fps", True),
        ("fps", "2.5"),
        ("server_ms_p50", 12.0),
        ("server_ms_p50", 1.0),
        ("server_ms_p95", True),
        ("auth", False),
        ("device", 5),
        ("commit", 5),
    ],
)
def test_checker_rejects_wrong_types(key: str, bad: Any) -> None:
    with pytest.raises(AssertionError):
        check_health_types({**_fixture(), key: bad})


@pytest.mark.parametrize("key", ["ready", "detector"])
def test_checker_rejects_missing_required_field(key: str) -> None:
    body = _fixture()
    del body[key]
    with pytest.raises(AssertionError):
        check_health_types(body)


@pytest.mark.parametrize(
    ("key", "bad"),
    [("device", None), ("auth", "maybe"), ("fps", None), ("extra", 1)],
)
def test_server_guarantees_reject_violations(key: str, bad: Any) -> None:
    with pytest.raises(AssertionError):
        check_server_guarantees({**_fixture(), key: bad})


def test_server_guarantees_reject_missing_key() -> None:
    body = _fixture()
    del body["model_file"]
    with pytest.raises(AssertionError):
        check_server_guarantees(body)


def _health_with(describer: FakeDescriber | None, **env: Any) -> dict[str, Any]:
    settings = Settings(gemini_api_key=SecretStr(SECRET), **env)
    app = create_app(FakeDetector(), describer, FakeClock(), FakeWallClock(), settings)
    with TestClient(app) as c:
        wait_for(lambda: _health(c)["ready"])
        text = c.get("/health").text
    assert SECRET not in text
    body: dict[str, Any] = json.loads(text)
    check_all(body)
    return body


def test_health_reports_descriptions_on_with_the_model() -> None:
    body = _health_with(FakeDescriber("x"), gemini_model="gem-main", gemini_fallback_model="fb")
    assert (body["describe"], body["gemini_model"]) == ("on", "gem-main")


def test_health_reports_descriptions_off_with_no_model() -> None:
    body = _health_with(None)
    assert (body["describe"], body["gemini_model"]) == ("off", None)
