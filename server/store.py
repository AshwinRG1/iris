"""Rolling latency stats and the JSONL frames log. Standard library only."""

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("iris")

_FPS_WINDOW_MS = 10_000
_KEEP = 100
_LOG_RETRY_S = 30.0


def nearest_rank[T: float](values: list[T], q: float) -> T | None:
    """The q-quantile by the nearest-rank method; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(q * len(ordered)), 1) - 1]


@dataclass
class LatencyStats:
    """The last 100 frames: when each arrived (ms, monotonic) and its server ms."""

    _frames: deque[tuple[int, int]] = field(default_factory=lambda: deque(maxlen=_KEEP))

    def record(self, arrived_ms: int, server_ms: int) -> None:
        self._frames.append((arrived_ms, server_ms))

    def fps(self, now_ms: int) -> float:
        """Frames per second over the window ending now; a stopped stream decays to 0."""
        arrivals = [t for t, _ in self._frames if now_ms - _FPS_WINDOW_MS <= t <= now_ms]
        if len(arrivals) < 2 or now_ms <= min(arrivals):
            return 0.0
        return round((len(arrivals) - 1) / ((now_ms - min(arrivals)) / 1000), 2)

    def server_ms_percentile(self, q: float) -> int | None:
        return nearest_rank([ms for _, ms in self._frames], q)


class FrameLog:
    """One JSON line per frame. A failing disk never fails a frame.

    After a failure it warns once, then skips writes for 30 s before trying again.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._dir_ready = False
        self._retry_at = 0.0
        self._ensure_dir()

    def _fail(self, e: Exception) -> None:
        log.warning("frames log unavailable, retrying in %.0f s: %s", _LOG_RETRY_S, e)
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
