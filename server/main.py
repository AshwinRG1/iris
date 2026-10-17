"""App factory."""

from datetime import datetime
from typing import Protocol

from fastapi import FastAPI

from config import Settings


class Detector(Protocol):
    name: str


class Describer(Protocol):
    async def describe(self, jpeg: bytes) -> str: ...


class Clock(Protocol):
    def now_ms(self) -> int: ...


class WallClock(Protocol):
    def now(self) -> datetime: ...


def create_app(
    detector: Detector,
    describer: Describer,
    clock: Clock,
    wall_clock: WallClock,
    settings: Settings,
) -> FastAPI:
    app = FastAPI(title="Iris server")
    app.state.detector = detector
    app.state.describer = describer
    app.state.clock = clock
    app.state.wall_clock = wall_clock
    app.state.settings = settings
    return app
