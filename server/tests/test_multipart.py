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


def test_events_since_422_names_the_field_and_echoes_nothing() -> None:
    with _client() as c:
        r = c.get("/events", params={"since": -1})
        r2 = c.get("/events", params={"since": MARKER})
    assert r.status_code == 422
    assert r2.status_code == 422
    assert "since" in r.json()["detail"]
    assert "since" in r2.json()["detail"]
    assert MARKER not in r2.text
    assert "input" not in r.text
    assert "ctx" not in r.text


def _multipart(boundary: str, *parts: tuple[str, bytes], closing: bool = True) -> bytes:
    body = b""
    for headers, data in parts:
        body += f"--{boundary}\r\n{headers}\r\n\r\n".encode() + data + b"\r\n"
    return body + (f"--{boundary}--\r\n".encode() if closing else b"")


FILE_HEADERS = 'Content-Disposition: form-data; name="file"'


def test_boundary_over_256_bytes_is_422_not_500() -> None:
    boundary = "b" * 300
    body = _multipart(boundary, (FILE_HEADERS, jpeg_bytes()))
    with _client() as c:
        _ready(c)
        r = c.post(
            "/frame",
            content=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )
    assert r.status_code == 422


def test_a_thousand_tiny_parts_before_file_is_422() -> None:
    parts = [(f'Content-Disposition: form-data; name="p{i}"', b"x") for i in range(1000)]
    body = _multipart("bb", *parts, (FILE_HEADERS, jpeg_bytes()))
    with _client() as c:
        _ready(c)
        r = c.post(
            "/frame", content=body, headers={"content-type": "multipart/form-data; boundary=bb"}
        )
    assert r.status_code == 422


def test_parts_after_file_are_not_parsed() -> None:
    # 1000 parts after `file` would trip the part cap if they were parsed.
    parts = [(f'Content-Disposition: form-data; name="p{i}"', b"x") for i in range(1000)]
    body = _multipart("bb", (FILE_HEADERS, jpeg_bytes()), *parts)
    with _client() as c:
        _ready(c)
        r = c.post(
            "/frame", content=body, headers={"content-type": "multipart/form-data; boundary=bb"}
        )
    assert r.status_code == 200, r.text


def test_media_type_and_boundary_name_are_case_insensitive() -> None:
    body = _multipart("bb", (FILE_HEADERS, jpeg_bytes()))
    with _client() as c:
        _ready(c)
        r = c.post(
            "/frame", content=body, headers={"content-type": "MULTIPART/FORM-DATA; Boundary=bb"}
        )
    assert r.status_code == 200, r.text


def test_file_part_cut_off_mid_data_is_422() -> None:
    body = _multipart("bb", (FILE_HEADERS, jpeg_bytes()), closing=False)[:-40]
    with _client() as c:
        _ready(c)
        r = c.post(
            "/frame", content=body, headers={"content-type": "multipart/form-data; boundary=bb"}
        )
    assert r.status_code == 422


def test_crlf_and_0xff_payload_is_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_decoder_input(monkeypatch)
    sent = b"\xff\xd8\r\n\xff\xff\r\n\r\n--bb\r\n\xff\x00\r\n"
    with _client() as c:
        _ready(c)
        for filename in (None, "f.jpg"):
            c.post("/frame", files={"file": (filename, sent, "image/jpeg")})
    assert seen == [sent, sent]


def test_chunked_body_over_limit_is_413_once_ready() -> None:
    def chunks() -> Any:
        for _ in range(30):
            yield b"\0" * 100_000

    with _client() as c:
        _ready(c)
        r = c.post(
            "/frame",
            content=chunks(),
            headers={"content-type": "multipart/form-data; boundary=bb"},
        )
    assert r.status_code == 413


def test_lying_small_content_length_with_large_body_is_413_once_ready() -> None:
    body = _multipart("bb", (FILE_HEADERS, b"\0" * 3_000_000))
    with _client() as c:
        _ready(c)
        r = c.post(
            "/frame",
            content=body,
            headers={
                "content-type": "multipart/form-data; boundary=bb",
                "content-length": "100",
            },
        )
    assert r.status_code == 413


def test_openapi_documents_the_upload_and_string_422() -> None:
    with _client() as c:
        spec = c.get("/openapi.json").json()
    op = spec["paths"]["/frame"]["post"]
    body = op["requestBody"]
    assert body["required"] is True
    schema = body["content"]["multipart/form-data"]["schema"]
    assert schema["required"] == ["file"]
    assert schema["properties"]["file"] == {"type": "string", "format": "binary"}
    ref = op["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
    assert spec["components"]["schemas"][ref.rsplit("/", 1)[1]]["properties"]["detail"] == {
        "type": "string",
        "title": "Detail",
    }


def test_body_over_limit_without_filename_is_413() -> None:
    with _client(Settings(max_upload_bytes=1000)) as c:
        r = c.post("/frame", files={"file": (None, b"\0" * 5000, "image/jpeg")})
    assert r.status_code == 413


def test_key_is_checked_before_the_body_is_parsed() -> None:
    with _client(Settings(iris_api_key="k")) as c:
        r = c.post("/frame", files={"other": (None, b"x", "image/jpeg")})
    assert r.status_code == 401
