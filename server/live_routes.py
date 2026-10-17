"""The live view: GET /, /latest, /latest.jpg and /events. Read-only snapshots."""

import base64
import hashlib
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from auth import require_key_header_or_query
from deps import Deps, Runtime, get_deps, get_runtime
from domain import Side, Tuning
from store import Event


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    dependencies=[Depends(require_key_header_or_query), Depends(_no_store)],
    responses={401: {"description": "Missing or wrong key"}},
)


def _inline_hashes(html: str, tag: str) -> str:
    """CSP source list allowing exactly the page's own inline <tag> blocks."""
    blocks = re.findall(rf"<{tag}>(.*?)</{tag}>", html, flags=re.DOTALL)
    return " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(b.encode()).digest()).decode() + "'"
        for b in blocks
    )


# Older than this (about 7 frames at 2.5 fps), /latest drops its boxes and says so.
STALE_MS = 3000

_PAGE = (Path(__file__).parent / "static" / "live.html").read_text(encoding="utf-8")
_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; "
        f"script-src {_inline_hashes(_PAGE, 'script')}; "
        f"style-src {_inline_hashes(_PAGE, 'style')}"
    ),
}


class BoxOut(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class LatestDetection(BaseModel):
    label: str
    conf: float
    side: Side
    box: BoxOut


class Latest(BaseModel):
    frame_id: int
    at: datetime | None
    age_ms: int | None
    stale: bool = False
    detections: list[LatestDetection]
    tuning: Tuning


class Events(BaseModel):
    boot_id: str
    events: list[Event]


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def page() -> HTMLResponse:
    return HTMLResponse(_PAGE, headers=_PAGE_HEADERS)


@router.get("/latest")
async def latest(
    deps: Annotated[Deps, Depends(get_deps)],
    runtime: Annotated[Runtime, Depends(get_runtime)],
) -> Latest:
    snap = runtime.latest  # an immutable snapshot, replaced whole on the loop
    if snap is None:
        return Latest(frame_id=0, at=None, age_ms=None, detections=[], tuning=runtime.tuning)
    age_ms = max(0, deps.clock.now_ms() - snap.t_ms)
    stale = age_ms > STALE_MS
    return Latest(
        frame_id=snap.frame_id,
        at=snap.at,
        age_ms=age_ms,
        stale=stale,
        detections=[]
        if stale
        else [
            LatestDetection(label=d.label, conf=d.conf, side=d.side, box=BoxOut(**asdict(d.box)))
            for d in snap.detections
        ],
        tuning=runtime.tuning,
    )


@router.get(
    "/latest.jpg",
    summary="The last frame as JPEG",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "The last frame's JPEG bytes"},
        404: {"description": "No frame has arrived yet"},
    },
)
async def latest_jpg(runtime: Annotated[Runtime, Depends(get_runtime)]) -> Response:
    snap = runtime.latest
    if snap is None:
        raise HTTPException(status_code=404, detail="no frame yet")
    # X-Frame-Id lets the page check this image belongs to the /latest it is drawing.
    return Response(
        snap.jpeg,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Id": str(snap.frame_id),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/events")
async def events(
    runtime: Annotated[Runtime, Depends(get_runtime)],
    since: Annotated[int, Query(ge=0)] = 0,
) -> Events:
    """Events with id above `since`, oldest first.

    Only the newest 200 are kept, so when `since` is older than the oldest kept event the
    reply has a gap. `boot_id` changes on every server restart; ids restart from 1 then.
    """
    return Events(boot_id=runtime.boot_id, events=list(runtime.events.since(since)))
