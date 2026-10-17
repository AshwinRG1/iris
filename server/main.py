"""App factory and the module-level `app` for `uvicorn main:app`."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI

from config import Settings
from deps import Clock, Deps, Describer, Runtime, WallClock, get_deps
from detector import DetectFn, Detector, FallbackDetector, YoloDetector, ZeroShotDetector
from frame_routes import router as frame_router
from limits import BodyLimitMiddleware
from live_routes import router as live_router
from store import FrameLog

__all__ = ["Deps", "app", "create_app", "get_deps"]

log = logging.getLogger("iris")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    deps: Deps = app.state.deps
    runtime: Runtime = app.state.runtime

    async def load_model() -> None:
        try:
            # Off the loop, in the inference slot; the server answers 503 until it finishes.
            loaded = await asyncio.get_running_loop().run_in_executor(
                runtime.slot, deps.detector.load
            )
        except Exception as e:
            # /health shows only the type; the message may carry paths and stays in the log.
            runtime.load_error = type(e).__name__
            log.error(
                "model failed to load; /frame stays 503: %s: %s",
                type(e).__name__,
                e,
                exc_info=True,
            )
            return
        runtime.device, runtime.name, runtime.model_info = loaded.device, loaded.name, loaded.info
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
    app.state.runtime = Runtime(
        device=detector.device,
        frame_log=FrameLog(Path(settings.log_dir) / "frames.jsonl"),
        event_log=FrameLog(Path(settings.log_dir) / "events.jsonl", "events"),
        tuning=settings.tuning,
        name=detector.name,
    )
    if not settings.iris_api_key:
        log.warning("IRIS_API_KEY is not set; authentication is off")
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_upload_bytes)
    app.include_router(frame_router)
    app.include_router(live_router)
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


def _import_zeroshot() -> DetectFn:
    from zeroshot import detect  # D's module; imported in the slot, only when selected

    return detect  # type: ignore[no-any-return]


def build_detector(
    s: Settings, get_zeroshot: Callable[[], DetectFn] = _import_zeroshot
) -> Detector:
    if s.detector == "zeroshot":
        return ZeroShotDetector(get_zeroshot, s.device)
    labels = s.alert_label_set
    return FallbackDetector(
        lambda path, imgsz: YoloDetector(path, imgsz, s.device, s.conf_detect, labels), s
    )


def _build_app() -> FastAPI:
    s = Settings()
    return create_app(build_detector(s), _NoDescriber(), _MonotonicClock(), _SystemWallClock(), s)


app = _build_app()
