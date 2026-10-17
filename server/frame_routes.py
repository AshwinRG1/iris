"""POST /frame and GET /health."""

import asyncio
import io
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel

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


def _decode_and_detect(detector: Detector, jpeg: bytes) -> list[Detection]:
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    return detector.detect(image)


@router.post("/frame")
async def frame(
    file: UploadFile,
    deps: Annotated[Deps, Depends(get_deps)],
    runtime: Annotated[Runtime, Depends(get_runtime)],
) -> FrameReply:
    if not runtime.ready:
        raise HTTPException(status_code=503, detail="model not ready")
    started = deps.clock.now_ms()
    jpeg = await file.read()
    found = await asyncio.get_running_loop().run_in_executor(
        runtime.slot, _decode_and_detect, deps.detector, jpeg
    )
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


@router.get("/health")
async def health(
    deps: Annotated[Deps, Depends(get_deps)],
    runtime: Annotated[Runtime, Depends(get_runtime)],
) -> Health:
    return Health(ready=runtime.ready, detector=deps.detector.name, device=deps.detector.device)
