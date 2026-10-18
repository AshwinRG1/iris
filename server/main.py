"""App factory and the module-level `app` for `uvicorn main:app`."""

import asyncio
import logging
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from config import Settings
from deps import Clock, Deps, Runtime, WallClock, get_deps
from detector import DetectFn, Detector, FallbackDetector, YoloDetector, ZeroShotDetector
from frame_routes import router as frame_router
from gemini import Closable, Describer, GeminiDescriber
from limits import BodyLimitMiddleware
from live_routes import router as live_router
from store import JsonlLog

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
    try:
        task.cancel()
        # A load already running in its thread can't be interrupted; shutdown doesn't wait for it.
        await asyncio.gather(task, return_exceptions=True)
        runtime.slot.shutdown(wait=False, cancel_futures=True)
        pending = list(runtime.describe_tasks)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        for describer in (deps.describer, deps.fallback_describer):
            if isinstance(describer, Closable):
                await describer.aclose()


def create_app(
    detector: Detector,
    describer: Describer | None,  # None: descriptions are off
    clock: Clock,
    wall_clock: WallClock,
    settings: Settings,
    fallback_describer: Describer | None = None,
) -> FastAPI:
    app = FastAPI(title="Iris server", lifespan=_lifespan)
    app.state.deps = Deps(detector, describer, fallback_describer, clock, wall_clock, settings)
    app.state.runtime = Runtime(
        device=detector.device,
        frame_log=JsonlLog(Path(settings.log_dir) / "frames.jsonl", "frames"),
        event_log=JsonlLog(Path(settings.log_dir) / "events.jsonl", "events"),
        tuning=settings.tuning,
        name=detector.name,
    )
    if not settings.iris_api_key:
        log.warning("IRIS_API_KEY is not set; authentication is off")
    app.add_exception_handler(RequestValidationError, _short_422)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_upload_bytes)
    app.include_router(frame_router)
    app.include_router(live_router)
    return app


async def _short_422(_request: Request, exc: Exception) -> JSONResponse:
    # FastAPI's default 422 copies the offending input back. This names where and why only.
    errors = cast(RequestValidationError, exc).errors()  # registered for this type only
    reasons = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors)
    return JSONResponse({"detail": reasons}, status_code=422)


class _MonotonicClock:
    def now_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


class _SystemWallClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


DEFAULT_PROMPT = Path(__file__).resolve().parent / "prompts" / "describe.txt"


def _read_prompt(s: Settings) -> str | None:
    path = Path(s.gemini_prompt_file) if s.gemini_prompt_file else DEFAULT_PROMPT
    try:
        prompt = path.read_text(encoding="utf-8")
        if not prompt.strip():
            raise ValueError("empty")
    except (OSError, ValueError) as e:  # UnicodeDecodeError is a ValueError
        log.warning(
            "GEMINI_PROMPT_FILE %s is unusable (%s); descriptions are off", path, type(e).__name__
        )
        return None
    return prompt


def build_fallback_describer(s: Settings) -> Describer | None:
    """The second model, tried once after a timeout or 503. Call only when the main one built.

    It sets no thinking level: Google lists none for the lite model, so it keeps its default.
    """
    model = s.gemini_fallback_model.strip()
    prompt = _read_prompt(s) if model else None
    if prompt is None:
        return None
    key = s.gemini_api_key.get_secret_value().strip()
    try:
        return GeminiDescriber(key, model, prompt, s.gemini_timeout_s, thinking_level=None)
    except Exception as e:
        log.warning("Gemini fallback could not be built (%s); no fallback", type(e).__name__)
        return None


def build_describer(s: Settings) -> Describer | None:
    """The Gemini adapter, or None with one log line naming why. Never raises or logs the key."""
    if s.describe == "off":
        log.info("DESCRIBE=off; descriptions are off")
        return None
    key = s.gemini_api_key.get_secret_value().strip()
    if not key:
        log.warning("GEMINI_API_KEY is not set; descriptions are off")
        return None
    if not s.gemini_model.strip():
        log.warning("GEMINI_MODEL is empty; descriptions are off")
        return None
    prompt = _read_prompt(s)
    if prompt is None:
        return None
    try:
        describer = GeminiDescriber(
            key, s.gemini_model.strip(), prompt, s.gemini_timeout_s, thinking_level="low"
        )
    except Exception as e:
        log.warning("Gemini client could not be built (%s); descriptions are off", type(e).__name__)
        return None
    log.info(
        "descriptions on: model %s, fallback %s, timeout %g s, cooldown %g s, max age %g s, "
        "empty reset %g s, move %g, area %g, scene reset %g s",
        s.gemini_model.strip(),
        s.gemini_fallback_model.strip() or "none",
        s.gemini_timeout_s,
        s.describe_cooldown_s,
        s.describe_max_age_s,
        s.describe_empty_reset_s,
        s.describe_move_frac,
        s.describe_area_frac,
        s.describe_scene_reset_s,
    )
    return describer


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


def configure_logging() -> None:
    """Send the app's INFO lines to stderr, so they reach journalctl. Safe to call twice."""
    logger = logging.getLogger("iris")
    if any(getattr(h, "_iris", False) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler._iris = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _build_app() -> FastAPI:
    configure_logging()
    s = Settings()
    describer = build_describer(s)
    fallback = build_fallback_describer(s) if describer is not None else None
    return create_app(
        build_detector(s), describer, _MonotonicClock(), _SystemWallClock(), s, fallback
    )


app = _build_app()
