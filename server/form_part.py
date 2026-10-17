"""Pull the raw bytes of one named part out of a multipart/form-data body.

Starlette's form parser decodes a part with no filename as text, which would corrupt a JPEG.
This keeps every part as bytes and ignores the parts it was not asked for.

What counts as malformed: a wrong content type, no boundary, a boundary the parser rejects,
more than MAX_PARTS parts before the wanted one, a client that disconnects, or a wanted part
that never ends. `finalize()` does not check the end state, so a cut-off wanted part is rejected
only because its part never ends; a body missing just the closing `--boundary--` after the wanted
part has ended is accepted. Once the wanted part has ended, the rest of the body is not read.
The first part with the wanted name wins.
"""

from collections.abc import AsyncIterator

from python_multipart.exceptions import FormParserError  # base of MultipartParseError too
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import ClientDisconnect

MAX_PARTS = 16


class _Done(Exception):
    """Raised from a callback once the wanted part has ended, to stop the parser."""


class MalformedForm(Exception):
    """The body is not multipart/form-data, is cut short, or has no part with that name."""


async def read_part(content_type: str, body: AsyncIterator[bytes], name: str) -> bytes:
    kind, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if kind.lower() != b"multipart/form-data" or not boundary:
        raise MalformedForm("not multipart/form-data")

    wanted = name.encode()
    field = b""
    headers: dict[bytes, bytes] = {}
    chunks: list[bytes] = []
    found = False
    taking = False
    parts = 0

    def on_part_begin() -> None:
        nonlocal parts
        parts += 1
        if parts > MAX_PARTS:
            raise MalformedForm("too many parts")

    def on_header_field(data: bytes, start: int, end: int) -> None:
        nonlocal field
        field += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        key = field.lower()
        headers[key] = headers.get(key, b"") + data[start:end]

    def on_header_end() -> None:
        nonlocal field
        field = b""

    def on_headers_finished() -> None:
        nonlocal taking
        _, disposition = parse_options_header(headers.get(b"content-disposition", b""))
        # The first part with this name wins; a repeat is ignored.
        taking = disposition.get(b"name") == wanted and not found
        headers.clear()

    def on_part_data(data: bytes, start: int, end: int) -> None:
        if taking:
            chunks.append(data[start:end])

    def on_part_end() -> None:
        nonlocal found, taking
        if taking:
            found = True
            # Stop mid-chunk: parts after the wanted one are neither counted nor parsed.
            raise _Done
        taking = False

    callbacks = {
        "on_part_begin": on_part_begin,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
        "on_part_data": on_part_data,
        "on_part_end": on_part_end,
    }
    try:
        parser = MultipartParser(boundary, callbacks)  # type: ignore[arg-type]
        async for chunk in body:
            parser.write(chunk)
        parser.finalize()
    except _Done:
        return b"".join(chunks)
    except ClientDisconnect as e:
        raise MalformedForm("client disconnected") from e
    except FormParserError as e:
        raise MalformedForm(f"parser rejected the body ({type(e).__name__})") from e
    if not found:
        raise MalformedForm(f"no {name} part")
    return b"".join(chunks)
