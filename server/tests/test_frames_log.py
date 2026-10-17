import json
from pathlib import Path

from conftest import (
    FakeClock,
    FakeDescriber,
    FakeDetector,
    FakeWallClock,
    ScriptedClock,
    det,
    jpeg_bytes,
)
from fastapi.testclient import TestClient

from config import Settings
from deps import Clock
from main import create_app


def _client(detector: FakeDetector, clock: Clock | None = None) -> TestClient:
    app = create_app(detector, FakeDescriber(), clock or FakeClock(), FakeWallClock(), Settings())
    return TestClient(app)


def _post(c: TestClient) -> None:
    r = c.post("/frame", files={"file": ("f.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 200


def _lines(log_dir: Path) -> list[dict[str, object]]:
    text = (log_dir / "frames.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines()]


def test_each_frame_appends_one_line_with_wall_clock_at(log_dir: Path) -> None:
    with _client(FakeDetector([[det(conf=0.71, cx=0.1)], []])) as c:
        _post(c)
        _post(c)
    first, second = _lines(log_dir)
    assert first == {
        "at": "2026-01-01T00:00:00+00:00",
        "frame_id": 1,
        "server_ms": 10,
        "detections": [{"label": "person", "conf": 0.71, "side": "left"}],
        "alert": None,
    }
    assert second["frame_id"] == 2
    assert second["detections"] == []


def test_failing_log_does_not_break_frame(log_dir: Path) -> None:
    log_dir.parent.mkdir(parents=True, exist_ok=True)
    log_dir.write_text("a file where the directory should be")
    with _client(FakeDetector()) as c:
        _post(c)


def test_health_before_any_frame_has_no_latency() -> None:
    with _client(FakeDetector()) as c:
        h = c.get("/health").json()
    assert h["fps"] == 0.0
    assert h["server_ms_p50"] is None
    assert h["server_ms_p95"] is None


def test_health_reports_p50_p95_over_recent_frames() -> None:
    # Per frame: start, end. Server ms are 10, 20, ... 200; frames start 1 s apart.
    values: list[int] = []
    for i in range(1, 21):
        start = i * 1000
        values += [start, start + i * 10]
    values.append(21_500)  # /health reads the clock once
    with _client(FakeDetector(), ScriptedClock(values)) as c:
        for _ in range(20):
            _post(c)
        h = c.get("/health").json()
    assert h["server_ms_p50"] == 100  # nearest rank 10 of 20
    assert h["server_ms_p95"] == 190  # nearest rank 19 of 20


def test_health_fps_counts_frames_in_the_last_ten_seconds() -> None:
    # Five frames 400 ms apart: 4 gaps in 1.6 s is 2.5 fps.
    values: list[int] = []
    for i in range(5):
        values += [i * 400, i * 400 + 5]
    values.append(2000)
    with _client(FakeDetector(), ScriptedClock(values)) as c:
        for _ in range(5):
            _post(c)
        assert c.get("/health").json()["fps"] == 2.5


def test_health_fps_is_zero_after_the_stream_stops() -> None:
    values: list[int] = []
    for i in range(3):
        values += [i * 400, i * 400 + 5]
    values.append(60_000)
    with _client(FakeDetector(), ScriptedClock(values)) as c:
        for _ in range(3):
            _post(c)
        assert c.get("/health").json()["fps"] == 0.0
