"""POST /frame and GET /health."""

import asyncio
import io
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel

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
        opened = Image.open(io.BytesIO(jpeg))
        # The header gives the size without decoding pixels, so a bomb is refused cheaply.
        if max(opened.size) > MAX_SIDE_PX:
            raise BadImage(f"image side over {MAX_SIDE_PX} px")
        image = opened.convert("RGB")
    except BadImage:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as e:
        raise BadImage("body is not a decodable image") from e
    return detector.detect(image)


@router.post("/frame", dependencies=[Depends(require_key_header)])
async def frame(
    file: UploadFile,
    deps: Annotated[Deps, Depends(get_deps)],
    runtime: Annotated[Runtime, Depends(get_runtime)],
) -> FrameReply:
    if not runtime.ready:
        raise HTTPException(status_code=503, detail="model not ready")
    started = deps.clock.now_ms()
    jpeg = await file.read()
    try:
        found = await asyncio.get_running_loop().run_in_executor(
            runtime.slot, _decode_and_detect, deps.detector, jpeg
        )
    except BadImage as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    labels = deps.settings.alert_label_set
    kept = [d for d in found if d.label in labels and d.conf >= deps.settings.conf_detect]
    runtime.last_frame_id += 1
    return FrameReply(
        detections=[DetectionOut(label=d.label, conf=d.conf, side=d.side) for d in kept],
        alert=None,
        frame_id=runtime.last_frame_id,
        server_ms=deps.clock.now_ms() - started,
    )


class Health(BaseModel):
    ready: bool
    detector: str
    device: str
    load_error: str | None


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
    )
