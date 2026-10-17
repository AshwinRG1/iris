"""Request body size cap, enforced as the body streams in."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_TOO_LARGE = "request body too large"


class _BodyTooLarge(BaseException):  # noqa: N818
    # A BaseException, so FastAPI's body parsing (any Exception becomes 400) lets it through.
    pass


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
        too_large = JSONResponse({"detail": _TOO_LARGE}, status_code=413)
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await too_large(scope, receive, send)
            return
        seen = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if response_started:
                raise
            await too_large(scope, receive, send)
