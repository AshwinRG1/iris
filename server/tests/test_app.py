from datetime import UTC, datetime

from fastapi.testclient import TestClient

from config import Settings
from main import create_app


class FakeDetector:
    name = "fake"


class FakeDescriber:
    async def describe(self, jpeg: bytes) -> str:
        return "a chair"


class FakeClock:
    def now_ms(self) -> int:
        return 0


class FakeWallClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


def test_factory_builds_app_that_serves_openapi() -> None:
    app = create_app(FakeDetector(), FakeDescriber(), FakeClock(), FakeWallClock(), Settings())
    assert TestClient(app).get("/openapi.json").status_code == 200
