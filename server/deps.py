"""Shared dependencies and runtime state. Mutated only on the event loop."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from fastapi import Request

from config import Settings
from detector import Detector
from domain import PolicyState


# Placeholder: moves to the describer module in a later ticket.
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


@dataclass
class Runtime:
    device: str
    ready: bool = False
    load_error: str | None = None
    last_frame_id: int = 0
    policy: PolicyState = field(default_factory=PolicyState)
    # The single inference slot: one worker thread, so the model is never used concurrently.
    slot: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=1))


def get_deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    return deps


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime
