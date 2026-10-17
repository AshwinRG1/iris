"""The background description of an alerting frame. Runs on the event loop."""

import asyncio
import io
import logging
from dataclasses import replace

from PIL import Image, ImageDraw

from deps import Deps, Runtime
from describe_policy import boxes_line, may_describe, scene_of
from domain import Detection
from gemini import Describer
from store import Event, event_json

log = logging.getLogger("iris")

_BOX_COLOR = "red"


def annotate(jpeg: bytes, kept: list[Detection]) -> bytes:
    """The frame with every kept box drawn and labeled with its side, as a new JPEG."""
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    draw = ImageDraw.Draw(image)
    w, h = image.size
    for d in kept:
        b = d.box
        corners = (b.x1 * w, b.y1 * h, b.x2 * w, b.y2 * h)
        draw.rectangle(corners, outline=_BOX_COLOR, width=3)
        draw.text((corners[0] + 4, corners[1] + 4), d.side, fill=_BOX_COLOR)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85)
    return out.getvalue()


def start_description(
    deps: Deps, runtime: Runtime, event: Event, jpeg: bytes, kept: list[Detection]
) -> None:
    """Start one background call for this alert when the rules allow it."""
    describer = deps.describer
    if describer is None:
        return
    s = deps.settings
    scene = scene_of(kept)
    in_flight = bool(runtime.describe_tasks)
    if not may_describe(
        runtime.describe_state, scene, event.t_ms, round(s.describe_cooldown_s * 1000), in_flight
    ):
        return
    runtime.describe_state = replace(runtime.describe_state, last_started_ms=event.t_ms)
    task = asyncio.create_task(_describe(deps, runtime, describer, event, jpeg, kept))
    runtime.describe_tasks.add(task)  # the loop holds tasks weakly
    task.add_done_callback(runtime.describe_tasks.discard)


async def _describe(
    deps: Deps,
    runtime: Runtime,
    describer: Describer,
    event: Event,
    jpeg: bytes,
    kept: list[Detection],
) -> None:
    s = deps.settings
    try:
        image = annotate(jpeg, kept)
        async with asyncio.timeout(s.gemini_timeout_s):
            text = await describer.describe(image, boxes_line(kept))
    except Exception as e:  # never the message: it could carry request details
        log.warning("description failed: %s", type(e).__name__)
        return
    if not text.strip():
        log.warning("description empty; leaving it null")
        return
    if deps.clock.now_ms() - event.t_ms > s.describe_max_age_s * 1000:
        log.warning("description stale; discarded")
        return
    described = runtime.events.describe(event.id, text)
    if described is None:
        log.warning("description dropped; event %d left the ring", event.id)
        return
    runtime.describe_state = replace(runtime.describe_state, last_scene=scene_of(kept))
    runtime.event_log.append(event_json(described))
