"""POST /frame multipart handling and the 422 body, tested at the HTTP seam."""

import io
import os
from typing import Any

import pytest
from conftest import FakeClock, FakeDescriber, FakeDetector, FakeWallClock, jpeg_bytes, wait_for
from fastapi.testclient import TestClient
from PIL import Image

import frame_routes
from config import Settings
from main import create_app

MARKER = "SECRET-MARKER-9f3a"


def _client(settings: Settings | None = None) -> TestClient:
    app = create_app(
        FakeDetector(), FakeDescriber(), FakeClock(), FakeWallClock(), settings or Settings()
    )
    return TestClient(app)


def _ready(c: TestClient) -> None:
    wait_for(lambda: c.get("/health").json()["ready"])


def _noisy_jpeg() -> bytes:
    # High-entropy bytes, so a text decode of the part would visibly corrupt them.
    buf = io.BytesIO()
    Image.frombytes("RGB", (64, 48), os.urandom(64 * 48 * 3)).save(buf, format="JPEG")
    return buf.getvalue()


def _record_decoder_input(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    seen: list[bytes] = []
    real = frame_routes._decode_and_detect

    def spy(detector: Any, jpeg: bytes) -> Any:
        seen.append(jpeg)
        return real(detector, jpeg)

    monkeypatch.setattr(frame_routes, "_decode_and_detect", spy)
    return seen


@pytest.mark.parametrize("filename", [None, "f.jpg"])
def test_file_part_with_or_without_filename_arrives_byte_identical(
    monkeypatch: pytest.MonkeyPatch, filename: str | None
) -> None:
    seen = _record_decoder_input(monkeypatch)
    sent = _noisy_jpeg()
    with _client() as c:
        _ready(c)
        r = c.post("/frame", files={"file": (filename, sent, "image/jpeg")})
    assert r.status_code == 200, r.text
    assert seen == [sent]


def test_part_without_filename_and_corrupt_jpeg_is_400() -> None:
    with _client() as c:
        _ready(c)
        r = c.post("/frame", files={"file": (None, b"\xff\xd8\xff garbage", "image/jpeg")})
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str)


def test_non_jpeg_body_is_400() -> None:
    with _client() as c:
        _ready(c)
        r = c.post("/frame", files={"file": (None, b"not an image", "image/jpeg")})
    assert r.status_code == 400


def test_missing_file_part_is_422_with_string_detail() -> None:
    with _client() as c:
        _ready(c)
        r = c.post("/frame", files={"other": ("f.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)


def test_non_multipart_body_is_422_with_string_detail() -> None:
    with _client() as c:
        _ready(c)
        r = c.post("/frame", content=jpeg_bytes(), headers={"content-type": "image/jpeg"})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)


def test_422_never_repeats_the_upload() -> None:
    with _client() as c:
        _ready(c)
        r = c.post(
            "/frame",
            data={"note": MARKER},
            files={"other": (MARKER, MARKER.encode(), "image/jpeg")},
        )
        r2 = c.post("/frame", content=MARKER.encode(), headers={"content-type": "text/plain"})
    assert r.status_code == 422
    assert r2.status_code == 422
    assert MARKER not in r.text
    assert MARKER not in r2.text


def test_events_since_422_is_a_string_detail() -> None:
    with _client() as c:
        r = c.get("/events", params={"since": -1})
        r2 = c.get("/events", params={"since": MARKER})
    assert r.status_code == 422
    assert r2.status_code == 422
    assert isinstance(r.json()["detail"], str)
    assert MARKER not in r2.text


def test_body_over_limit_without_filename_is_413() -> None:
    with _client(Settings(max_upload_bytes=1000)) as c:
        r = c.post("/frame", files={"file": (None, b"\0" * 5000, "image/jpeg")})
    assert r.status_code == 413


def test_key_is_checked_before_the_body_is_parsed() -> None:
    with _client(Settings(iris_api_key="k")) as c:
        r = c.post("/frame", files={"other": (None, b"x", "image/jpeg")})
    assert r.status_code == 401
