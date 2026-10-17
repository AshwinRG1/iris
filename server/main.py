"""App factory and the module-level `app` for `uvicorn main:app`."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from config import Settings
from deps import Clock, Deps, Describer, Runtime, WallClock, get_deps
from detector import Detector, YoloDetector
from frame_routes import router as frame_router

__all__ = ["Deps", "app", "create_app", "get_deps"]

log = logging.getLogger("iris")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    deps: Deps = app.state.deps
    runtime: Runtime = app.state.runtime

    async def load_model() -> None:
        try:
            # Off the loop, in the inference slot; the server answers 503 until it finishes.
            runtime.device = await asyncio.get_running_loop().run_in_executor(
                runtime.slot, deps.detector.load
            )
        except Exception as e:
            runtime.load_error = f"{type(e).__name__}: {e}"
            log.error("model failed to load; /frame stays 503: %s", runtime.load_error)
            return
        runtime.ready = True

    task = asyncio.create_task(load_model())
    yield
    task.cancel()
    # A load already running in its thread can't be interrupted; shutdown doesn't wait for it.
    await asyncio.gather(task, return_exceptions=True)
    runtime.slot.shutdown(wait=False, cancel_futures=True)


def create_app(
    detector: Detector,
    describer: Describer,
    clock: Clock,
    wall_clock: WallClock,
    settings: Settings,
) -> FastAPI:
    app = FastAPI(title="Iris server", lifespan=_lifespan)
    app.state.deps = Deps(detector, describer, clock, wall_clock, settings)
    app.state.runtime = Runtime(device=detector.device)
    app.include_router(frame_router)
    return app


class _MonotonicClock:
    def now_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


class _SystemWallClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _NoDescriber:
    async def describe(self, jpeg: bytes) -> str:
        return ""


def _build_app() -> FastAPI:
    s = Settings()
    detector = YoloDetector(s.model_path, s.imgsz, s.device, s.conf_detect)
    return create_app(detector, _NoDescriber(), _MonotonicClock(), _SystemWallClock(), s)


app = _build_app()
