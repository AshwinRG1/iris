"""Rolling latency stats and the JSONL frames log. Standard library only."""

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from domain import Detection, Side

log = logging.getLogger("iris")

_FPS_WINDOW_MS = 10_000
_PERCENTILE_WINDOW_MS = 60_000
_KEEP = 100
_LOG_RETRY_S = 30.0
EVENTS_KEPT = 200


def nearest_rank[T: float](values: list[T], q: float) -> T | None:
    """The q-quantile by the nearest-rank method; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(q * len(ordered)), 1) - 1]


@dataclass
class LatencyStats:
    """The last 100 frames: when each arrived (ms, monotonic) and its server ms.

    fps and the percentiles also look only at a window ending now, so the percentiles cover
    the last 100 frames that arrived within 60 s: at 2.5 fps the cap is the tighter limit
    after about 40 s, and an idle server's percentiles return to None.
    """

    _frames: deque[tuple[int, int]] = field(default_factory=lambda: deque(maxlen=_KEEP))

    def record(self, arrived_ms: int, server_ms: int) -> None:
        self._frames.append((arrived_ms, server_ms))

    def fps(self, now_ms: int) -> float:
        """Frames per second over the window ending now; a stopped stream decays to 0."""
        arrivals = [t for t, _ in self._frames if now_ms - _FPS_WINDOW_MS <= t <= now_ms]
        if len(arrivals) < 2 or now_ms <= min(arrivals):
            return 0.0
        return round((len(arrivals) - 1) / ((now_ms - min(arrivals)) / 1000), 2)

    def server_ms_percentile(self, q: float, now_ms: int) -> int | None:
        """Over frames that arrived in the 60 s ending now; None when there are none."""
        recent = [ms for t, ms in self._frames if now_ms - _PERCENTILE_WINDOW_MS <= t <= now_ms]
        return nearest_rank(recent, q)


class JsonlLog:
    """One JSON line per record; `name` ("frames", "events") labels its warnings.

    A failing disk never fails a frame.

    After a failure it warns once, then skips writes for 30 s before trying again.
    """

    def __init__(self, path: Path, name: str) -> None:
        self._path = path
        self._name = name
        self._dir_ready = False
        self._retry_at = 0.0
        self._ensure_dir()

    def _fail(self, e: Exception) -> None:
        log.warning("%s log unavailable, retrying in %.0f s: %s", self._name, _LOG_RETRY_S, e)
        self._retry_at = time.monotonic() + _LOG_RETRY_S

    def _ensure_dir(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._dir_ready = True
        except (OSError, TypeError, ValueError) as e:
            self._fail(e)

    def append(self, line: dict[str, Any]) -> None:
        if time.monotonic() < self._retry_at:
            return
        if not self._dir_ready:
            self._ensure_dir()
            if not self._dir_ready:
                return
        try:
            with self._path.open("a") as f:
                f.write(json.dumps(line) + "\n")
        except (OSError, TypeError, ValueError) as e:
            self._fail(e)


@dataclass(frozen=True)
class Snapshot:
    """The last frame as the live view shows it. Replaced whole, never edited."""

    frame_id: int
    published_ms: int  # when the result was published, not when the frame arrived
    at: datetime
    jpeg: bytes
    detections: tuple[Detection, ...]


@dataclass(frozen=True)
class Event:
    """One fired alert. A description replaces the event with a described copy."""

    id: int
    t_ms: int
    at: datetime
    side: Side
    conf: float
    description: str | None = None
    description_id: int | None = None  # counts up from 1 per boot, one per description


class EventRing:
    """The newest 200 events, ids counting up from 1. Mutated only on the event loop."""

    def __init__(self) -> None:
        self._events: deque[Event] = deque(maxlen=EVENTS_KEPT)
        self._last_id = 0
        self._last_description_id = 0

    def add(self, t_ms: int, at: datetime, side: Side, conf: float) -> Event:
        self._last_id += 1
        event = Event(self._last_id, t_ms, at, side, conf)
        self._events.append(event)
        return event

    def latest(self) -> Event | None:
        return self._events[-1] if self._events else None

    def describe(self, event_id: int, text: str) -> Event | None:
        """Replace the event with a described copy; None if it already left the ring."""
        for i, event in enumerate(self._events):
            if event.id == event_id:
                self._last_description_id += 1
                described = replace(
                    event, description=text, description_id=self._last_description_id
                )
                self._events[i] = described
                return described
        return None

    def since(self, event_id: int) -> tuple[Event, ...]:
        """A snapshot of the events newer than `event_id`, oldest first."""
        return tuple(e for e in self._events if e.id > event_id)


def event_json(e: Event) -> dict[str, Any]:
    """The wire and log shape of an event; `at` is UTC ISO 8601."""
    return {
        "id": e.id,
        "t_ms": e.t_ms,
        "at": e.at.isoformat(),
        "side": e.side,
        "conf": e.conf,
        "description": e.description,
        "description_id": e.description_id,
    }
