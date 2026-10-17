import io
from datetime import UTC, datetime

from PIL import Image

from domain import Box, Detection


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

    def load(self) -> None:
        pass

    def detect(self, image: Image.Image) -> list[Detection]:
        return self.script.pop(0) if self.script else []


class FakeDescriber:
    async def describe(self, jpeg: bytes) -> str:
        return "a chair"


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000

    def now_ms(self) -> int:
        self.t += 10
        return self.t


class FakeWallClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)
