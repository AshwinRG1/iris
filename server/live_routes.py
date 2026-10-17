"""The live view: GET /, /latest, /latest.jpg and /events. Read-only snapshots."""

from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from auth import require_key_header_or_query
from deps import Deps, Runtime, get_deps, get_runtime
from domain import Side
from store import event_json

router = APIRouter(dependencies=[Depends(require_key_header_or_query)])

_PAGE = (Path(__file__).parent / "static" / "live.html").read_text()


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


class TuningOut(BaseModel):
    conf_alert: float
    window: int
    needed: int
    cooldown_ms: int
    same_side_repeat_ms: int
    window_max_age_ms: int


class Latest(BaseModel):
    frame_id: int
    at: str | None
    age_ms: int | None
    detections: list[LatestDetection]
    tuning: TuningOut


class EventOut(BaseModel):
    id: int
    t_ms: int
    at: str
    side: Side
    conf: float
    description: str | None


@router.get("/", response_class=HTMLResponse)
async def page() -> str:
    return _PAGE


@router.get("/latest")
async def latest(
    deps: Annotated[Deps, Depends(get_deps)],
    runtime: Annotated[Runtime, Depends(get_runtime)],
) -> Latest:
    snap = runtime.latest  # an immutable snapshot, replaced whole on the loop
    tuning = TuningOut(**asdict(runtime.tuning))
    if snap is None:
        return Latest(frame_id=0, at=None, age_ms=None, detections=[], tuning=tuning)
    return Latest(
        frame_id=snap.frame_id,
        at=snap.at.isoformat(),
        age_ms=deps.clock.now_ms() - snap.t_ms,
        detections=[
            LatestDetection(label=d.label, conf=d.conf, side=d.side, box=BoxOut(**asdict(d.box)))
            for d in snap.detections
        ],
        tuning=tuning,
    )


@router.get("/latest.jpg")
async def latest_jpg(runtime: Annotated[Runtime, Depends(get_runtime)]) -> Response:
    snap = runtime.latest
    if snap is None:
        raise HTTPException(status_code=404, detail="no frame yet")
    return Response(snap.jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/events")
async def events(
    runtime: Annotated[Runtime, Depends(get_runtime)], since: int = 0
) -> list[EventOut]:
    return [EventOut(**event_json(e)) for e in runtime.events.since(since)]
