"""POST /frame and GET /health."""

import asyncio
import io
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from PIL import Image
from pydantic import BaseModel

from alerts import decide
from auth import require_key_header
from deps import Deps, Runtime, get_deps, get_runtime
from describe import start_description
from describe_policy import note_frame
from detector import Detector
from domain import SPOKEN_MS, Detection, Side
from form_part import MalformedForm, read_part
from store import Event, Snapshot, event_json

router = APIRouter()
log = logging.getLogger("iris")


class ErrorDetail(BaseModel):
    detail: str


class BoxOut(BaseModel):
    x: float  # top-left, normalized 0..1
    y: float
    w: float
    h: float


class CenterOut(BaseModel):
    x: float
    y: float


class DetectionOut(BaseModel):
    label: str
    conf: float
    side: Side
    box: BoxOut
    center: CenterOut


class FrameReply(BaseModel):
    detections: list[DetectionOut]
    alert: str | None
    frame_id: int
    server_ms: int
    description: str | None = None
    description_id: int | None = None


MAX_SIDE_PX = 4096


def _spoken(event: Event | None, now_ms: int) -> tuple[str | None, int | None]:
    if event is None or event.description is None or now_ms - event.t_ms >= SPOKEN_MS:
        return None, None
    return event.description, event.description_id


class BadImage(Exception):
    """The body is not an image we will decode."""


def _decode_and_detect(detector: Detector, jpeg: bytes) -> list[Detection]:
    try:
        opened = Image.open(io.BytesIO(jpeg), formats=["JPEG"])
    except Exception as e:
        raise BadImage("body is not a decodable JPEG") from e
    # The header gives the size without decoding pixels, so a bomb is refused cheaply.
    if max(opened.size) > MAX_SIDE_PX:
        raise BadImage(f"image side over {MAX_SIDE_PX} px")
    try:
        opened.load()
        image = opened.convert("RGB")
    except Exception as e:
        raise BadImage("body is not a decodable JPEG") from e
    return detector.detect(image)


_FRAME_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {"file": {"type": "string", "format": "binary"}},
                    "required": ["file"],
                }
            }
        },
    }
}


@router.post(
    "/frame",
    dependencies=[Depends(require_key_header)],
    openapi_extra=_FRAME_BODY,
    responses={code: {"model": ErrorDetail} for code in (400, 401, 413, 422, 503)},
)
async def frame(
    request: Request,
    deps: Annotated[Deps, Depends(get_deps)],
    runtime: Annotated[Runtime, Depends(get_runtime)],
) -> FrameReply:
    if not runtime.ready:
        raise HTTPException(status_code=503, detail="model not ready")
    try:
        # The body streams through BodyLimitMiddleware, so this read is bounded.
        jpeg = await read_part(request.headers.get("content-type", ""), request.stream(), "file")
    except MalformedForm as e:
        log.info("422 on /frame: %s", e)  # the reason never holds request bytes
        raise HTTPException(
            status_code=422, detail="request is not a valid multipart upload with a file part"
        ) from e
    # Arrival time (the body is in), not completion time: the policy orders frames by arrival.
    started = deps.clock.now_ms()
    try:
        found = await asyncio.get_running_loop().run_in_executor(
            runtime.slot, _decode_and_detect, deps.detector, jpeg
        )
    except BadImage as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    labels = deps.settings.alert_label_set
    kept = [d for d in found if d.label.lower() in labels and d.conf >= deps.settings.conf_detect]
    runtime.describe_state = note_frame(
        runtime.describe_state, kept, started, deps.settings.describe_tuning.empty_reset_ms
    )
    runtime.policy, alert = decide(runtime.policy, kept, started, runtime.tuning)
    runtime.last_frame_id += 1
    # An alert makes a new latest event, which has no description yet.
    description, description_id = (
        (None, None) if alert else _spoken(runtime.events.latest(), started)
    )
    reply = FrameReply(
        detections=[
            DetectionOut(
                label=d.label,
                conf=d.conf,
                side=d.side,
                box=BoxOut(
                    x=d.box.x1,
                    y=d.box.y1,
                    w=d.box.x2 - d.box.x1,
                    h=d.box.y2 - d.box.y1,
                ),
                center=CenterOut(
                    x=(d.box.x1 + d.box.x2) / 2,
                    y=(d.box.y1 + d.box.y2) / 2,
                ),
            )
            for d in kept
        ],
        alert=alert.text if alert else None,
        frame_id=runtime.last_frame_id,
        server_ms=deps.clock.now_ms() - started,
        description=description,
        description_id=description_id,
    )
    runtime.stats.record(started, reply.server_ms)
    at = deps.wall_clock.now()
    runtime.latest = Snapshot(runtime.last_frame_id, deps.clock.now_ms(), at, jpeg, tuple(kept))
    runtime.frame_log.append({"at": at.isoformat(), **reply.model_dump()})
    if alert is not None:
        event = runtime.events.add(alert.t_ms, at, alert.hit.side, alert.hit.conf)
        runtime.event_log.append(event_json(event))
        start_description(deps, runtime, event, jpeg, kept)
    return reply


class Health(BaseModel):
    ready: bool
    detector: str
    device: str
    load_error: str | None
    auth: Literal["on", "off"]
    describe: Literal["on", "off"]  # the effective state: off also when the key is missing
    gemini_model: str | None  # the main model while on; never the key
    model_file: str | None
    model_sha256: str | None
    class_names: list[str] | None
    fallback: str | None
    commit: str | None
    fps: float
    server_ms_p50: int | None
    server_ms_p95: int | None


@router.get("/health")
async def health(
    deps: Annotated[Deps, Depends(get_deps)],
    runtime: Annotated[Runtime, Depends(get_runtime)],
) -> Health:
    info = runtime.model_info
    now_ms = deps.clock.now_ms()
    return Health(
        ready=runtime.ready,
        detector=runtime.name,
        device=runtime.device,
        load_error=runtime.load_error,
        auth="on" if deps.settings.iris_api_key else "off",
        describe="on" if deps.describer is not None else "off",
        gemini_model=deps.settings.gemini_model.strip() if deps.describer is not None else None,
        model_file=info.file,
        model_sha256=info.sha256,
        class_names=None if info.class_names is None else list(info.class_names),
        fallback=info.fallback,
        commit=deps.settings.commit,
        fps=runtime.stats.fps(now_ms),
        server_ms_p50=runtime.stats.server_ms_percentile(0.50, now_ms),
        server_ms_p95=runtime.stats.server_ms_percentile(0.95, now_ms),
    )
