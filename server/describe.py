"""The background description of an alerting frame. Runs on the event loop."""

import asyncio
import io
import logging
from dataclasses import replace

import httpx
from PIL import Image, ImageDraw

from deps import Deps, Runtime
from describe_policy import attached, boxes_line, may_describe, scene_of
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
    scene = scene_of(kept)
    in_flight = bool(runtime.describe_tasks)
    if not may_describe(
        runtime.describe_state, scene, event.t_ms, deps.settings.describe_tuning, in_flight
    ):
        return
    runtime.describe_state = replace(runtime.describe_state, last_started_ms=event.t_ms)
    epoch = runtime.describe_state.resets
    task = asyncio.create_task(_describe(deps, runtime, describer, event, jpeg, kept, epoch))
    runtime.describe_tasks.add(task)  # the loop holds tasks weakly
    task.add_done_callback(runtime.describe_tasks.discard)


async def _describe(
    deps: Deps,
    runtime: Runtime,
    describer: Describer,
    event: Event,
    jpeg: bytes,
    kept: list[Detection],
    epoch: int,
) -> None:
    s = deps.settings
    try:
        async with asyncio.timeout(s.gemini_timeout_s):
            # Pure image work shares the single slot, at most once per describe cooldown.
            image = await asyncio.get_running_loop().run_in_executor(
                runtime.slot, annotate, jpeg, kept
            )
            text = await describer.describe(image, boxes_line(kept))
    except httpx.HTTPStatusError as e:  # the status only: the body and URL stay out of the log
        log.warning("description failed: %s %d", type(e).__name__, e.response.status_code)
        return
    except Exception as e:  # never the message: it could carry request details
        log.warning("description failed: %s", type(e).__name__)
        return
    text = text.strip()
    if not text:
        log.warning("description empty; leaving it null")
        return
    if deps.clock.now_ms() - event.t_ms > s.describe_max_age_s * 1000:
        log.warning("description stale; discarded")
        return
    latest = runtime.events.latest()
    if latest is None or latest.id != event.id:
        log.warning("description dropped; a newer alert fired")
        return
    described = runtime.events.describe(event.id, text)
    if described is None:
        log.warning("description dropped; event %d left the ring", event.id)
        return
    # The scene was reset while the call was out: the event is still described, but a person
    # who left and came back must not count as already described.
    if runtime.describe_state.resets == epoch:
        runtime.describe_state = attached(runtime.describe_state, scene_of(kept), event.t_ms)
    runtime.event_log.append(event_json(described))
