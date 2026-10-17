"""Request body size cap, enforced as the body streams in."""

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_TOO_LARGE = "request body too large"


class BodyLimitMiddleware:
    """Reject a body over `max_bytes` with 413 without buffering it.

    A declared Content-Length over the cap is refused before any read. A body without one
    (chunked) or with a lying one is cut off as soon as the bytes received pass the cap.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await JSONResponse({"detail": _TOO_LARGE}, status_code=413)(scope, receive, send)
            return
        seen = 0

        async def limited_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise HTTPException(status_code=413, detail=_TOO_LARGE)
            return message

        await self.app(scope, limited_receive, send)
