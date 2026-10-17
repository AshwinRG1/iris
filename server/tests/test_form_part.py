"""read_part on its own: byte identity across chunk sizes, and what counts as malformed."""

from collections.abc import AsyncIterator

import pytest

from form_part import MalformedForm, read_part

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


CT = "multipart/form-data; boundary=bb"
TRICKY = b"\xff\xd8\r\n\r\n--\r\n--b\r\n\r\n--bx\xff\xfe\x00--bb-\r\n"


def _body(*parts: tuple[str, bytes]) -> bytes:
    out = b""
    for headers, data in parts:
        out += f"--bb\r\n{headers}\r\n\r\n".encode() + data + b"\r\n"
    return out + b"--bb--\r\n"


async def _stream(body: bytes, size: int) -> AsyncIterator[bytes]:
    for i in range(0, len(body), size):
        yield body[i : i + size]


@pytest.mark.parametrize("size", [1, 3, 7, 10**6])
async def test_first_file_part_wins_and_bytes_are_identical(size: int) -> None:
    body = _body(
        ('content-DISPOSITION: form-data; name="other"', b"zzz"),
        ('content-DISPOSITION: form-data; name="file"', TRICKY),
        ('Content-Disposition: form-data; name="file"; filename="x.jpg"', b"second"),
    )
    assert await read_part(CT, _stream(body, size), "file") == TRICKY


async def test_cut_off_file_part_is_malformed() -> None:
    body = _body(('Content-Disposition: form-data; name="file"', b"abcdef" * 10))
    with pytest.raises(MalformedForm):
        await read_part(CT, _stream(body[:-30], 7), "file")


async def test_oversized_boundary_is_malformed() -> None:
    ct = "multipart/form-data; boundary=" + "b" * 300
    with pytest.raises(MalformedForm):
        await read_part(ct, _stream(b"x", 1), "file")


async def test_missing_part_is_malformed() -> None:
    body = _body(('Content-Disposition: form-data; name="other"', b"x"))
    with pytest.raises(MalformedForm):
        await read_part(CT, _stream(body, 5), "file")
