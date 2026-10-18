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
MIN_RETRY_S = 0.5  # less budget than this left: don't start the fallback
MAX_SIDE = 640  # the long side sent to Gemini; smaller is faster


def annotate(jpeg: bytes, kept: list[Detection]) -> bytes:
    """The frame with every kept box drawn and labeled with its side, as a new JPEG."""
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)  # never upscales
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
    deadline_ms = event.t_ms + s.describe_max_age_s * 1000  # one budget for both attempts

    def remaining_s() -> float:
        return max((deadline_ms - deps.clock.now_ms()) / 1000, 0)

    def budget_s() -> float:
        return min(s.gemini_timeout_s, remaining_s())

    boxes = boxes_line(kept)
    fallback = deps.fallback_describer
    try:
        async with asyncio.timeout(budget_s()):
            # Pure image work shares the single slot, at most once per describe cooldown.
            image = await asyncio.get_running_loop().run_in_executor(
                runtime.slot, annotate, jpeg, kept
            )
    except Exception as e:  # drawn once and reused, so there is nothing to retry without it
        log.warning("description failed while drawing: %s", type(e).__name__)
        return
    try:
        async with asyncio.timeout(budget_s()):
            text = await describer.describe(image, boxes)
    except Exception as e:  # never the message: it could carry request details
        code = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
        failure = type(e).__name__ + ("" if code is None else f" {code}")
        # Only a timeout or a 503 is worth another model; a 400, 403 or 429 would fail again.
        retry = isinstance(e, TimeoutError) or code == 503
        if not retry or fallback is None:
            log.warning("description failed: %s", failure)
            return
        if remaining_s() < MIN_RETRY_S:
            log.warning("description failed: %s; too little time left for the fallback", failure)
            return
        log.warning(
            "description: main model failed (%s); trying fallback %s",
            failure,
            s.gemini_fallback_model.strip(),
        )
        try:
            async with asyncio.timeout(budget_s()):
                text = await fallback.describe(image, boxes)
        except Exception as e2:
            code2 = e2.response.status_code if isinstance(e2, httpx.HTTPStatusError) else None
            log.warning(
                "description failed: %s", type(e2).__name__ + ("" if code2 is None else f" {code2}")
            )
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
