"""POST /frame and GET /health."""

import asyncio
import io
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel

from alerts import decide
from auth import require_key_header
from deps import Deps, Runtime, get_deps, get_runtime
from detector import Detector
from domain import Detection, Side

router = APIRouter()


class DetectionOut(BaseModel):
    label: str
    conf: float
    side: Side


class FrameReply(BaseModel):
    detections: list[DetectionOut]
    alert: str | None
    frame_id: int
    server_ms: int


MAX_SIDE_PX = 4096


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


@router.post("/frame", dependencies=[Depends(require_key_header)])
async def frame(
    file: UploadFile,
    deps: Annotated[Deps, Depends(get_deps)],
    runtime: Annotated[Runtime, Depends(get_runtime)],
) -> FrameReply:
    if not runtime.ready:
        raise HTTPException(status_code=503, detail="model not ready")
    jpeg = await file.read()
    # Arrival time (the body is in), not completion time: the policy orders frames by arrival.
    started = deps.clock.now_ms()
    try:
        found = await asyncio.get_running_loop().run_in_executor(
            runtime.slot, _decode_and_detect, deps.detector, jpeg
        )
    except BadImage as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    labels = deps.settings.alert_label_set
    kept = [d for d in found if d.label in labels and d.conf >= deps.settings.conf_detect]
    runtime.policy, alert = decide(runtime.policy, kept, started, runtime.tuning)
    runtime.last_frame_id += 1
    reply = FrameReply(
        detections=[DetectionOut(label=d.label, conf=d.conf, side=d.side) for d in kept],
        alert=alert,
        frame_id=runtime.last_frame_id,
        server_ms=deps.clock.now_ms() - started,
    )
    runtime.stats.record(started, reply.server_ms)
    runtime.frame_log.append({"at": deps.wall_clock.now().isoformat(), **reply.model_dump()})
    return reply


class Health(BaseModel):
    ready: bool
    detector: str
    device: str
    load_error: str | None
    auth: Literal["on", "off"]
    commit: str | None
    fps: float
    server_ms_p50: int | None
    server_ms_p95: int | None


@router.get("/health")
async def health(
    deps: Annotated[Deps, Depends(get_deps)],
    runtime: Annotated[Runtime, Depends(get_runtime)],
) -> Health:
    return Health(
        ready=runtime.ready,
        detector=deps.detector.name,
        device=runtime.device,
        load_error=runtime.load_error,
        auth="on" if deps.settings.iris_api_key else "off",
        commit=deps.settings.commit,
        fps=runtime.stats.fps(deps.clock.now_ms()),
        server_ms_p50=runtime.stats.server_ms_percentile(0.50),
        server_ms_p95=runtime.stats.server_ms_percentile(0.95),
    )
