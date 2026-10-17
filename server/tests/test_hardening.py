import io
import time
from collections.abc import Iterator
from typing import Any

import pytest
from conftest import FakeClock, FakeDescriber, FakeDetector, FakeWallClock, jpeg_bytes
from fastapi.testclient import TestClient
from PIL import Image, ImageFile

from auth import key_matches
from config import Settings
from main import create_app


def _client(settings: Settings) -> TestClient:
    app = create_app(FakeDetector(), FakeDescriber(), FakeClock(), FakeWallClock(), settings)
    return TestClient(app)


def _wait_ready(c: TestClient) -> None:
    deadline = time.monotonic() + 5
    while not c.get("/health").json()["ready"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)


def _post(c: TestClient, body: bytes, headers: dict[str, str] | None = None) -> Any:
    return c.post("/frame", files={"file": ("f.jpg", body, "image/jpeg")}, headers=headers)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="PNG")
    return buf.getvalue()


def test_non_image_body_is_400() -> None:
    with _client(Settings()) as c:
        _wait_ready(c)
        r = _post(c, b"definitely not a jpeg")
    assert r.status_code == 400
    assert "detail" in r.json()


def test_truncated_jpeg_is_400() -> None:
    with _client(Settings()) as c:
        _wait_ready(c)
        r = _post(c, jpeg_bytes(400, 300)[:200])
    assert r.status_code == 400


def test_body_over_limit_is_413() -> None:
    with _client(Settings()) as c:
        r = _post(c, b"\0" * 3_000_000)
    assert r.status_code == 413
    assert "detail" in r.json()


def test_chunked_body_over_limit_is_413() -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(30):
            yield b"\0" * 100_000

    with _client(Settings(max_upload_bytes=1000)) as c:
        r = c.post(
            "/frame",
            content=chunks(),
            headers={"content-type": "multipart/form-data; boundary=x"},
        )
    assert r.status_code == 413


def test_side_over_4096_is_400_from_the_header() -> None:
    with _client(Settings()) as c:
        _wait_ready(c)
        r = _post(c, jpeg_bytes(4097, 8))
    assert r.status_code == 400
    assert "4096" in r.json()["detail"]


def test_side_of_exactly_4096_is_accepted() -> None:
    with _client(Settings()) as c:
        _wait_ready(c)
        r = _post(c, jpeg_bytes(4096, 8))
    assert r.status_code == 200


def test_wrong_or_missing_key_is_401_when_key_set() -> None:
    with _client(Settings(iris_api_key="s3cret")) as c:
        _wait_ready(c)
        missing = _post(c, jpeg_bytes())
        wrong = _post(c, jpeg_bytes(), {"X-Iris-Key": "nope"})
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert "detail" in wrong.json()


def test_right_key_is_accepted() -> None:
    with _client(Settings(iris_api_key="s3cret")) as c:
        _wait_ready(c)
        r = _post(c, jpeg_bytes(), {"X-Iris-Key": "s3cret"})
    assert r.status_code == 200


def test_no_key_configured_checks_nothing() -> None:
    with _client(Settings()) as c:
        _wait_ready(c)
        r = _post(c, jpeg_bytes())
    assert r.status_code == 200


def test_health_stays_open_when_key_set() -> None:
    with _client(Settings(iris_api_key="s3cret")) as c:
        assert c.get("/health").status_code == 200


def test_png_body_is_400() -> None:
    with _client(Settings()) as c:
        _wait_ready(c)
        r = _post(c, _png())
    assert r.status_code == 400


def test_side_check_runs_before_any_pixel_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    body = jpeg_bytes(4097, 8)

    def boom(*_: object, **__: object) -> None:
        raise AssertionError("pixels decoded")

    monkeypatch.setattr(ImageFile.ImageFile, "load", boom)
    monkeypatch.setattr(Image.Image, "load", boom)
    monkeypatch.setattr(Image.Image, "convert", boom)
    with _client(Settings()) as c:
        _wait_ready(c)
        r = _post(c, body)
    assert r.status_code == 400
    assert "4096" in r.json()["detail"]


def test_decompression_bomb_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with _client(Settings()) as c:
        _wait_ready(c)
        r = _post(c, jpeg_bytes(64, 48))
    assert r.status_code == 400


def test_health_reports_auth_state() -> None:
    with _client(Settings()) as c:
        assert c.get("/health").json()["auth"] == "off"
    with _client(Settings(iris_api_key="k")) as c:
        assert c.get("/health").json()["auth"] == "on"


def test_unset_key_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="iris"):
        _client(Settings())
    assert "authentication is off" in caplog.text


def test_key_matches_right_key() -> None:
    assert key_matches("s3cret", "s3cret")


def test_key_matches_rejects_wrong_key_of_same_length() -> None:
    assert not key_matches("s3cret", "s3creT")


def test_key_matches_handles_non_ascii_without_raising() -> None:
    assert not key_matches("s3cret", "s3crét")
    assert key_matches("clé-é", "clé-é")


def test_key_matches_accepts_anything_when_unset() -> None:
    assert key_matches("", None)
    assert key_matches("", "whatever")
