import io
import time
from collections.abc import Iterator
from typing import Any

from conftest import FakeClock, FakeDescriber, FakeDetector, FakeWallClock, jpeg_bytes
from fastapi.testclient import TestClient
from PIL import Image

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


def _png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("1", (width, height)).save(buf, format="PNG")
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
        r = _post(c, _png(4097, 8))
    assert r.status_code == 400
    assert "4096" in r.json()["detail"]


def test_side_of_exactly_4096_is_accepted() -> None:
    with _client(Settings()) as c:
        _wait_ready(c)
        r = _post(c, _png(4096, 8))
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
