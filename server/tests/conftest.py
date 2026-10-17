import io
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from PIL import Image

from detector import NO_MODEL, Loaded
from domain import Box, Detection


def wait_for(condition: Callable[[], bool], timeout_s: float = 5.0) -> None:
    """Poll until `condition()` is true; fail the test after `timeout_s`."""
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout_s} s")
        time.sleep(0.01)


def jpeg_bytes(width: int = 64, height: int = 48) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "gray").save(buf, format="JPEG")
    return buf.getvalue()


def det(label: str = "person", conf: float = 0.6, cx: float = 0.1) -> Detection:
    return Detection(label, conf, Box(cx - 0.05, 0.2, cx + 0.05, 0.8))


class FakeDetector:
    name = "fake"
    device = "cpu"

    def __init__(self, script: list[list[Detection]] | None = None) -> None:
        self.script = script or []
        self.detect_threads: list[int] = []

    def load(self) -> Loaded:
        return Loaded(self.device, self.name, NO_MODEL)

    def detect(self, image: Image.Image) -> list[Detection]:
        self.detect_threads.append(threading.get_ident())
        return self.script.pop(0) if self.script else []


class FakeDescriber:
    async def describe(self, jpeg: bytes) -> str:
        return "a chair"


class FakeClock:
    """Integer ms. Each call advances `tick` ms; tick=0 gives a clock moved only by hand."""

    def __init__(self, tick: int = 10) -> None:
        self.t = 1000
        self.tick = tick

    def now_ms(self) -> int:
        self.t += self.tick
        return self.t


class FakeWallClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)
