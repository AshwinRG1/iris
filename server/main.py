"""App factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fastapi import FastAPI, Request

from config import Settings


# Placeholders: these move to their owning modules (detector, describer) in later tickets.
class Detector(Protocol):
    name: str


class Describer(Protocol):
    async def describe(self, jpeg: bytes) -> str: ...


class Clock(Protocol):
    def now_ms(self) -> int: ...


class WallClock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True)
class Deps:
    detector: Detector
    describer: Describer
    clock: Clock
    wall_clock: WallClock
    settings: Settings


def get_deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    return deps


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The inference slot and the store are created here.
    yield


def create_app(
    detector: Detector,
    describer: Describer,
    clock: Clock,
    wall_clock: WallClock,
    settings: Settings,
) -> FastAPI:
    app = FastAPI(title="Iris server", lifespan=_lifespan)
    app.state.deps = Deps(detector, describer, clock, wall_clock, settings)
    return app
