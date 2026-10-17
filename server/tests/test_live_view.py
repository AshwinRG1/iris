import json
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
    det,
    jpeg_bytes,
)
from fastapi.testclient import TestClient

from config import Settings
from deps import Clock, WallClock
from domain import Detection
from main import create_app

LIVE_PATHS = ["/", "/latest.jpg", "/latest", "/events"]


def _client(
    script: list[list[Detection]] | None = None,
    clock: Clock | None = None,
    wall: WallClock | None = None,
) -> TestClient:
    app = create_app(
        FakeDetector(script),
        FakeDescriber(),
        clock or FakeClock(),
        wall or FakeWallClock(),
        Settings(),
    )
    return TestClient(app)


def _post(c: TestClient, body: bytes | None = None, key: str | None = None) -> dict[str, Any]:
    headers = {"X-Iris-Key": key} if key else {}
    r = c.post(
        "/frame", files={"file": ("f.jpg", body or jpeg_bytes(), "image/jpeg")}, headers=headers
    )
    assert r.status_code == 200, r.text
    reply: dict[str, Any] = r.json()
    return reply


def _alert_twice(c: TestClient) -> None:
    assert _post(c)["alert"] is None
    assert _post(c)["alert"] == "possible person, left"


# --- /latest and /latest.jpg


def test_latest_before_any_frame_is_empty_and_jpg_is_404() -> None:
    with _client() as c:
        r = c.get("/latest")
        assert r.status_code == 200
        body = r.json()
        assert body["frame_id"] == 0
        assert body["detections"] == []
        assert body["at"] is None
        assert c.get("/latest.jpg").status_code == 404


def test_latest_returns_the_last_frame_with_normalized_boxes() -> None:
    script = [[det(conf=0.71, cx=0.1)], [det(label="person", conf=0.30, cx=0.9)]]
    with _client(script) as c:
        _post(c)
        _post(c)
        body = c.get("/latest").json()
    assert body["frame_id"] == 2
    assert body["at"] == "2026-01-01T00:00:00+00:00"
    assert body["detections"] == [
        {
            "label": "person",
            "conf": 0.3,
            "side": "right",
            "box": {"x1": pytest.approx(0.85), "y1": 0.2, "x2": pytest.approx(0.95), "y2": 0.8},
        }
    ]


def test_latest_carries_what_the_page_needs_to_color_hits_and_show_age() -> None:
    clock = SettableClock(1000)
    with _client([[det()]], clock=clock) as c:
        _post(c)
        clock.t = 3500
        body = c.get("/latest").json()
    assert body["tuning"]["conf_alert"] == 0.35
    assert body["age_ms"] == 2500


def test_latest_jpg_is_the_last_frame_bytes() -> None:
    first, second = jpeg_bytes(64, 48), jpeg_bytes(80, 60)
    with _client() as c:
        _post(c, first)
        assert c.get("/latest.jpg").content == first
        _post(c, second)
        r = c.get("/latest.jpg")
    assert r.content == second
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "no-store"


# --- events


def test_no_event_without_an_alert() -> None:
    with _client([[det()]]) as c:
        _post(c)
        assert c.get("/events").json() == []


def test_alert_appends_an_event() -> None:
    script = [[det(conf=0.6, cx=0.1)], [det(conf=0.8, cx=0.1)]]
    with _client(script, clock=SettableClock(1000)) as c:
        _alert_twice(c)
        events = c.get("/events").json()
    assert events == [
        {
            "id": 1,
            "t_ms": 1000,
            "at": "2026-01-01T00:00:00+00:00",
            "side": "left",
            "conf": 0.8,
            "description": None,
        }
    ]


def test_event_log_line_is_written_beside_the_frames_log(log_dir: Path) -> None:
    with _client([[det()], [det()]]) as c:
        _alert_twice(c)
    lines = (log_dir / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == [1]
    assert json.loads(lines[0])["side"] == "left"
    assert (log_dir / "frames.jsonl").exists()


def test_events_since_returns_only_newer_ones() -> None:
    clock = SettableClock(0)
    with _client([[det()]] * 4, clock=clock) as c:
        for t in (0, 100, 20_000, 20_100):  # the second alert is past cooldown and repeat limit
            clock.t = t
            _post(c)
        assert [e["id"] for e in c.get("/events").json()] == [1, 2]
        assert [e["id"] for e in c.get("/events?since=1").json()] == [2]
        assert c.get("/events?since=2").json() == []


def test_events_keep_the_newest_two_hundred() -> None:
    clock = SettableClock(0)
    with _client([[det()]] * 1000, clock=clock) as c:
        for i in range(1000):  # a pair of hit frames alerts; the next pair is 20 s on
            clock.t = (i // 2) * 20_000 + (i % 2) * 100
            _post(c)
        events = c.get("/events").json()
    assert len(events) == 200
    assert events[-1]["id"] == 500
    assert events[0]["id"] == 301


def test_event_time_comes_from_the_frames_wall_clock() -> None:
    with _client([[det()], [det()]], wall=SteppingWallClock()) as c:
        _alert_twice(c)
        event = c.get("/events").json()[0]
        latest = c.get("/latest").json()
    assert event["at"] == latest["at"]


# --- the page


def test_page_is_served_as_html() -> None:
    with _client() as c:
        r = c.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<canvas" in r.text


def test_page_escapes_what_it_renders_and_forwards_its_key() -> None:
    with _client() as c:
        html = c.get("/").text
    assert "innerHTML" not in html  # server text goes in through textContent only
    assert 'new URLSearchParams(location.search).get("key")' in html
    for path in ("/latest.jpg", "/latest", "/events", "/health"):
        assert path in html


# --- the key


def _keyed(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("IRIS_API_KEY", "s3cret")
    app = create_app(FakeDetector(), FakeDescriber(), FakeClock(), FakeWallClock(), Settings())
    return TestClient(app)


@pytest.mark.parametrize("path", LIVE_PATHS)
def test_key_set_rejects_without_key_and_accepts_query_or_header(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    with _keyed(monkeypatch) as c:
        _post(c, key="s3cret")
        assert c.get(path).status_code == 401
        assert c.get(path, params={"key": "wrong"}).status_code == 401
        assert c.get(path, params={"key": "s3cret"}).status_code == 200
        assert c.get(path, headers={"X-Iris-Key": "s3cret"}).status_code == 200


@pytest.mark.parametrize("path", LIVE_PATHS)
def test_empty_header_does_not_override_a_valid_query_key(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    with _keyed(monkeypatch) as c:
        _post(c, key="s3cret")
        r = c.get(path, params={"key": "s3cret"}, headers={"X-Iris-Key": ""})
        assert r.status_code == 200


def test_a_wrong_header_is_not_rescued_by_a_valid_query_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _keyed(monkeypatch) as c:
        r = c.get("/events", params={"key": "s3cret"}, headers={"X-Iris-Key": "nope"})
        assert r.status_code == 401


@pytest.mark.parametrize("path", LIVE_PATHS)
def test_no_key_set_leaves_the_live_view_open(path: str) -> None:
    with _client() as c:
        _post(c)
        assert c.get(path).status_code == 200


def test_health_stays_open_when_a_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    with _keyed(monkeypatch) as c:
        assert c.get("/health").status_code == 200
