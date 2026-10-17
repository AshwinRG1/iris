"""Pull the raw bytes of one named part out of a multipart/form-data body.

Starlette's form parser decodes a part with no filename as text, which would corrupt a JPEG.
This keeps every part as bytes and ignores the parts it was not asked for.
"""

from collections.abc import AsyncIterator

from python_multipart.multipart import MultipartParser, parse_options_header


class MalformedForm(Exception):
    """The body is not multipart/form-data, is cut short, or has no part with that name."""


async def read_part(content_type: str, body: AsyncIterator[bytes], name: str) -> bytes:
    kind, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        raise MalformedForm("not multipart/form-data")

    wanted = name.encode()
    field = b""
    headers: dict[bytes, bytes] = {}
    chunks: list[bytes] = []
    found = False
    taking = False

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
        taking = False

    parser = MultipartParser(
        boundary,
        {
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
    )
    try:
        async for chunk in body:
            parser.write(chunk)
        parser.finalize()
    except Exception as e:  # python-multipart raises its own error types on bad framing
        raise MalformedForm("malformed multipart body") from e
    if not found:
        raise MalformedForm(f"no {name} part")
    return b"".join(chunks)
