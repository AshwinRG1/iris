import asyncio
import io
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from detector import NO_MODEL, Loaded
from domain import Box, Detection


@pytest.fixture(autouse=True)
def _key_unset_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IRIS_API_KEY", raising=False)


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
    """Records each call. Scripted: answer `text`, sleep `delay_s`, push `clock` forward by
    `advance_ms` (a late answer), then raise `error` if set. The default answer is empty."""

    def __init__(
        self,
        text: str = "",
        *,
        error: Exception | None = None,
        delay_s: float = 0.0,
        clock: "SettableClock | None" = None,
        advance_ms: int = 0,
    ) -> None:
        self.text = text
        self.error = error
        self.delay_s = delay_s
        self.clock = clock
        self.advance_ms = advance_ms
        self.calls: list[tuple[bytes, str]] = []

    async def describe(self, jpeg: bytes, boxes: str) -> str:
        self.calls.append((jpeg, boxes))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.clock is not None:
            self.clock.t += self.advance_ms
        if self.error is not None:
            raise self.error
        return self.text


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


@pytest.fixture(autouse=True)
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test's frames log out of the working directory."""
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    return tmp_path / "logs"


class SettableClock:
    """Reads whatever `t` is. A test sets it per frame, so extra reads can't shift values."""

    def __init__(self, t: int = 0) -> None:
        self.t = t

    def now_ms(self) -> int:
        return self.t


class TimedDetector(FakeDetector):
    """Each detect() advances the clock by `cost_ms`, so the handler sees that server time."""

    def __init__(self, clock: SettableClock) -> None:
        super().__init__()
        self.clock = clock
        self.cost_ms = 10

    def detect(self, image: Image.Image) -> list[Detection]:
        self.clock.t += self.cost_ms
        return super().detect(image)


class SteppingWallClock:
    """Starts at 2026-01-01 and moves one second per read."""

    def __init__(self) -> None:
        self.t = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "contract"


class SlowLoad(FakeDetector):
    """load() blocks until `release` is set, so a test can look at the loading state."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def load(self) -> Loaded:
        self.release.wait(30)
        return super().load()


@pytest.fixture
def slow() -> Iterator[SlowLoad]:
    d = SlowLoad()
    yield d
    d.release.set()  # always, so a failing test never leaves the load thread waiting


class BrokenLoad(FakeDetector):
    """load() raises `error`; the default message stands in for a missing weights file."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self.error = error or RuntimeError("weights missing")

    def load(self) -> Loaded:
        raise self.error
