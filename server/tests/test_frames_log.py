import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    FakeClock,
    FakeDescriber,
    FakeDetector,
    FakeWallClock,
    SettableClock,
    SteppingWallClock,
    TimedDetector,
    det,
    jpeg_bytes,
)
from fastapi.testclient import TestClient

from config import Settings
from deps import Clock, WallClock
from main import create_app


def _client(
    detector: FakeDetector,
    clock: Clock | None = None,
    wall_clock: WallClock | None = None,
    settings: Settings | None = None,
) -> TestClient:
    app = create_app(
        detector,
        FakeDescriber(),
        clock or FakeClock(),
        wall_clock or FakeWallClock(),
        settings or Settings(),
    )
    return TestClient(app)


def _post(c: TestClient) -> dict[str, Any]:
    r = c.post("/frame", files={"file": ("f.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 200
    reply: dict[str, Any] = r.json()
    return reply


def _lines(log_dir: Path) -> list[dict[str, Any]]:
    text = (log_dir / "frames.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines()]


@contextmanager
def _timed(arrivals_and_costs: list[tuple[int, int]]) -> Iterator[tuple[TestClient, SettableClock]]:
    """Post one frame per (arrival ms, server ms) pair, then hand over the client."""
    clock = SettableClock()
    detector = TimedDetector(clock)
    with _client(detector, clock) as c:
        for arrival, cost in arrivals_and_costs:
            clock.t = arrival
            detector.cost_ms = cost
            _post(c)
        yield c, clock


def test_each_frame_appends_one_line_with_its_own_wall_clock_at(log_dir: Path) -> None:
    with _client(FakeDetector([[det(conf=0.71, cx=0.1)], []]), wall_clock=SteppingWallClock()) as c:
        _post(c)
        _post(c)
    first, second = _lines(log_dir)
    assert first == {
        "at": "2026-01-01T00:00:01+00:00",
        "frame_id": 1,
        "server_ms": 10,
        "detections": [{"label": "person", "conf": 0.71, "side": "left"}],
        "alert": None,
    }
    assert second["at"] == "2026-01-01T00:00:02+00:00"
    assert second["frame_id"] == 2
    assert second["detections"] == []


def test_failing_log_warns_once_and_frames_still_succeed(
    log_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log_dir.parent.mkdir(parents=True, exist_ok=True)
    log_dir.write_text("a file where the directory should be")
    with caplog.at_level(logging.WARNING, logger="iris"), _client(FakeDetector()) as c:
        first = _post(c)
        second = _post(c)
    assert (first["frame_id"], first["server_ms"]) == (1, 10)
    assert second["frame_id"] == 2
    warnings = [r for r in caplog.records if "frames log" in r.getMessage()]
    assert len(warnings) == 1


def test_failing_log_is_retried_after_thirty_seconds(
    log_dir: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr("store.time.monotonic", lambda: now[0])
    log_dir.parent.mkdir(parents=True, exist_ok=True)
    log_dir.write_text("a file where the directory should be")
    with caplog.at_level(logging.WARNING, logger="iris"), _client(FakeDetector()) as c:
        _post(c)
        now[0] += 29
        _post(c)
        assert len(caplog.records) == 1
        now[0] += 2
        _post(c)
    assert len(caplog.records) == 2


def test_health_before_any_frame_has_no_latency() -> None:
    with _client(FakeDetector()) as c:
        h = c.get("/health").json()
    assert h["fps"] == 0.0
    assert h["server_ms_p50"] is None
    assert h["server_ms_p95"] is None
    assert h["commit"] is None


def test_health_reports_the_commit_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_COMMIT", "abc1234")
    with _client(FakeDetector(), settings=Settings()) as c:
        assert c.get("/health").json()["commit"] == "abc1234"


@pytest.mark.parametrize(
    ("costs", "p50", "p95"),
    [
        # Nearest rank: n=7 gives ranks 4 and 7; n=10 gives ranks 5 and 10.
        ([10, 20, 30, 40, 50, 60, 70], 40, 70),
        ([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], 50, 100),
    ],
)
def test_health_percentiles_use_nearest_rank(costs: list[int], p50: int, p95: int) -> None:
    with _timed([(i * 400, cost) for i, cost in enumerate(costs)]) as (c, _):
        h = c.get("/health").json()
    assert (h["server_ms_p50"], h["server_ms_p95"]) == (p50, p95)


def test_health_percentiles_cover_only_the_last_hundred_frames() -> None:
    # Six slow frames, then a hundred fast ones: the slow ones must have rolled off.
    with _timed([(i * 400, 1000) for i in range(6)] + [(i * 400, 10) for i in range(6, 106)]) as (
        c,
        _,
    ):
        h = c.get("/health").json()
    assert (h["server_ms_p50"], h["server_ms_p95"]) == (10, 10)


def test_health_fps_is_frames_over_the_window_ending_now() -> None:
    # Five frames 400 ms apart, read at 2000 ms: 4 gaps over 2.0 s.
    with _timed([(i * 400, 5) for i in range(5)]) as (c, clock):
        clock.t = 2000
        assert c.get("/health").json()["fps"] == 2.0


def test_health_fps_decays_after_the_stream_stops() -> None:
    with _timed([(i * 400, 5) for i in range(5)]) as (c, clock):
        clock.t = 1600
        live = c.get("/health").json()["fps"]
        clock.t = 8000
        later = c.get("/health").json()["fps"]
        clock.t = 60_000
        gone = c.get("/health").json()["fps"]
    assert live == 2.5  # 4 gaps in 1.6 s
    assert 0.0 < later < live
    assert gone == 0.0


def test_health_fps_is_positive_when_frames_are_recorded_out_of_order() -> None:
    with _timed([(800, 5), (0, 5), (400, 5), (1600, 5), (1200, 5)]) as (c, clock):
        clock.t = 2000
        assert c.get("/health").json()["fps"] == 2.0
