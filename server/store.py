"""Rolling latency stats and the JSONL frames log. Standard library only."""

import json
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("iris")

_FPS_WINDOW_MS = 10_000
_KEEP = 100


def _nearest_rank(sorted_values: list[int], q: float) -> int | None:
    if not sorted_values:
        return None
    return sorted_values[max(math.ceil(q * len(sorted_values)), 1) - 1]


@dataclass
class LatencyStats:
    """The last 100 frames: when each arrived (ms, monotonic) and its server ms."""

    _frames: deque[tuple[int, int]] = field(default_factory=lambda: deque(maxlen=_KEEP))

    def record(self, arrived_ms: int, server_ms: int) -> None:
        self._frames.append((arrived_ms, server_ms))

    def fps(self, now_ms: int) -> float:
        arrivals = [t for t, _ in self._frames if t >= now_ms - _FPS_WINDOW_MS]
        if len(arrivals) < 2 or arrivals[-1] == arrivals[0]:
            return 0.0
        return round((len(arrivals) - 1) / ((arrivals[-1] - arrivals[0]) / 1000), 2)

    def server_ms_percentile(self, q: float) -> int | None:
        return _nearest_rank(sorted(ms for _, ms in self._frames), q)


class FrameLog:
    """One JSON line per frame. A failing disk never fails a frame."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def append(self, line: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a") as f:
                f.write(json.dumps(line) + "\n")
        except OSError as e:
            log.warning("frames log write failed: %s", e)
