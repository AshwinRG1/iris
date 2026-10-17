from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient

from config import Settings
from main import Deps, create_app, get_deps


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


def _fakes() -> tuple[FakeDetector, FakeDescriber, FakeClock, FakeWallClock, Settings]:
    return FakeDetector(), FakeDescriber(), FakeClock(), FakeWallClock(), Settings()


def test_factory_builds_app_that_serves_openapi() -> None:
    app = create_app(*_fakes())
    assert TestClient(app).get("/openapi.json").status_code == 200


def test_get_deps_returns_the_injected_fakes() -> None:
    fakes = _fakes()
    app = create_app(*fakes)
    seen: list[Deps] = []

    @app.get("/probe")
    def probe(deps: Annotated[Deps, Depends(get_deps)]) -> None:
        seen.append(deps)

    with TestClient(app) as client:
        assert client.get("/probe").status_code == 200
    deps = seen[0]
    assert (deps.detector, deps.describer, deps.clock, deps.wall_clock, deps.settings) == fakes
