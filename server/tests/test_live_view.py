import json
import logging
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


def _events(c: TestClient, since: int | None = None) -> list[dict[str, Any]]:
    r = c.get("/events", params={} if since is None else {"since": since})
    assert r.status_code == 200, r.text
    events: list[dict[str, Any]] = r.json()["events"]
    return events


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
    assert body["at"] == "2026-01-01T00:00:00Z"
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
    assert body["tuning"]["conf_alert"] == 0.20
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
        assert _events(c) == []


def test_alert_appends_an_event() -> None:
    script = [[det(conf=0.6, cx=0.1)], [det(conf=0.8, cx=0.1)]]
    with _client(script, clock=SettableClock(1000)) as c:
        _alert_twice(c)
        events = _events(c)
    assert events == [
        {
            "id": 1,
            "t_ms": 1000,
            "at": "2026-01-01T00:00:00Z",
            "side": "left",
            "conf": 0.8,
            "description": None,
            "description_id": None,
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
        assert [e["id"] for e in _events(c)] == [1, 2]
        assert [e["id"] for e in _events(c, 1)] == [2]
        assert _events(c, 2) == []


def test_events_keep_the_newest_two_hundred() -> None:
    clock = SettableClock(0)
    with _client([[det()]] * 1000, clock=clock) as c:
        for i in range(1000):  # a pair of hit frames alerts; the next pair is 20 s on
            clock.t = (i // 2) * 20_000 + (i % 2) * 100
            _post(c)
        events = _events(c)
    assert [e["id"] for e in events] == list(range(301, 501))


def test_event_time_comes_from_the_frames_wall_clock() -> None:
    with _client([[det()], [det()]], wall=SteppingWallClock()) as c:
        _alert_twice(c)
        event = _events(c)[0]
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


# --- more at the seam


def test_since_edge_cases() -> None:
    clock = SettableClock(0)
    with _client([[det()]] * 1000, clock=clock) as c:
        for i in range(1000):  # 500 alerts, so ids 1..300 have rotated out
            clock.t = (i // 2) * 20_000 + (i % 2) * 100
            _post(c)
        assert c.get("/events", params={"since": -1}).status_code == 422
        assert c.get("/events", params={"since": "abc"}).status_code == 422
        assert _events(c, 500) == []
        assert _events(c, 10_000) == []
        assert _events(c, 10**30) == []
        # An id that has rotated out returns what survives: a gap, not an error.
        assert [e["id"] for e in _events(c, 5)] == list(range(301, 501))
        assert [e["id"] for e in _events(c, 498)] == [499, 500]


def test_boot_id_differs_between_app_instances() -> None:
    with _client() as a, _client() as b:
        first = a.get("/events").json()["boot_id"]
        assert first == a.get("/events").json()["boot_id"]
        assert first != b.get("/events").json()["boot_id"]


def test_event_side_matches_the_alert_when_hits_are_on_opposite_sides() -> None:
    script = [[det(conf=0.6, cx=0.1)], [det(conf=0.5, cx=0.1), det(conf=0.9, cx=0.9)]]
    with _client(script) as c:
        _post(c)
        assert _post(c)["alert"] == "possible person, right"
        event = _events(c)[0]
    assert (event["side"], event["conf"]) == ("right", 0.9)


def test_a_suppressed_alert_creates_no_event() -> None:
    clock = SettableClock(0)
    with _client([[det()]] * 3, clock=clock) as c:
        for t in (0, 100, 200):  # the third frame is inside the cooldown
            clock.t = t
            reply = _post(c)
        assert reply["alert"] is None
        assert [e["id"] for e in _events(c)] == [1]


def test_a_rejected_frame_does_not_replace_the_latest() -> None:
    good = jpeg_bytes()
    with _client([[det()]]) as c:
        _post(c, good)
        bad = c.post("/frame", files={"file": ("f.jpg", b"not a jpeg", "image/jpeg")})
        assert bad.status_code == 400
        assert c.get("/latest").json()["frame_id"] == 1
        assert c.get("/latest.jpg").content == good


def test_a_failing_events_log_does_not_fail_frames(
    log_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (log_dir / "events.jsonl").mkdir(parents=True)  # appending to a directory fails
    with caplog.at_level(logging.WARNING, logger="iris"), _client([[det()], [det()]]) as c:
        _alert_twice(c)
        assert [e["id"] for e in _events(c)] == [1]
    assert any("events log" in r.getMessage() for r in caplog.records)
    assert not any("frames log" in r.getMessage() for r in caplog.records)


def test_latest_jpg_names_its_frame_so_the_page_can_match_it_to_latest() -> None:
    with _client() as c:
        _post(c)
        _post(c)
        r = c.get("/latest.jpg")
        assert r.headers["x-frame-id"] == str(c.get("/latest").json()["frame_id"]) == "2"


def test_every_live_view_response_is_not_cached() -> None:
    with _client() as c:
        _post(c)
        for path in LIVE_PATHS:
            assert c.get(path).headers["cache-control"] == "no-store", path


def test_page_headers() -> None:
    with _client() as c:
        _post(c)
        h = c.get("/").headers
        assert c.get("/latest.jpg").headers["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer"
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    csp = h["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "img-src 'self' blob:" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp


def test_csp_hash_matches_the_pages_inline_script() -> None:
    import base64
    import hashlib
    import re

    with _client() as c:
        r = c.get("/")
    body = re.search(r"<script>(.*?)</script>", r.text, re.DOTALL)
    assert body
    digest = base64.b64encode(hashlib.sha256(body.group(1).encode()).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in r.headers["content-security-policy"]


def test_page_sends_the_key_as_a_header_never_to_health() -> None:
    with _client() as c:
        html = c.get("/").text
    assert '"X-Iris-Key"' in html
    assert "history.replaceState" in html
    assert "X-Frame-Id" in html


# --- stale boxes (ticket 12)


def _latest_after(age_ms: int) -> dict[str, Any]:
    clock = SettableClock(1000)
    with _client([[det()]], clock=clock) as c:
        _post(c)
        clock.t = 1000 + age_ms
        body: dict[str, Any] = c.get("/latest").json()
    return body


def test_latest_is_not_stale_just_after_a_frame_or_at_exactly_three_seconds() -> None:
    for body in (_latest_after(0), _latest_after(3000)):
        assert body["stale"] is False
        assert len(body["detections"]) == 1


def test_latest_past_three_seconds_is_stale_with_no_boxes_but_keeps_its_frame() -> None:
    body = _latest_after(3001)
    assert body["stale"] is True
    assert body["detections"] == []
    assert body["frame_id"] == 1
    assert body["at"] == "2026-01-01T00:00:00Z"
    assert body["age_ms"] == 3001


def test_latest_before_any_frame_is_not_stale() -> None:
    with _client() as c:
        assert c.get("/latest").json()["stale"] is False


def test_page_stops_drawing_boxes_and_marks_a_stale_frame() -> None:
    with _client() as c:
        html = c.get("/").text
    assert "latest.stale" in html
    assert "stale (no new frames)" in html
    assert "innerHTML" not in html
    # The stale early return that skips the boxes.
    assert "if (latest.stale) return;" in html
    # A new bitmap is owned by lastBitmap before drawing; the old one is closed once.
    assert "lastBitmap.close();" in html
    assert html.index("lastBitmap = bitmap;") < html.index("draw(bitmap, latest);")
    # A stale redraw reuses the kept image only when it is this frame's.
    assert "lastBitmapFrameId === latest.frame_id" in html
    # A restart resets the page's frame bookkeeping.
    assert "latest.boot_id !== frameBootId" in html
    assert "drawnFrameId = 0;" in html


def test_a_slow_inference_still_gives_a_fresh_latest() -> None:
    # Age counts from when the result was published, not from arrival.
    clock = SettableClock(1000)
    detector = TimedDetector(clock)
    detector.cost_ms = 4000
    app = create_app(detector, FakeDescriber(), clock, FakeWallClock(), Settings())
    with TestClient(app) as c:
        _post(c)
        body = c.get("/latest").json()
        assert body["stale"] is False
        assert body["age_ms"] == 0
        assert body["frame_id"] == 1
        clock.t += 3001
        assert c.get("/latest").json()["stale"] is True


def test_latest_boot_id_matches_events_and_differs_between_instances() -> None:
    with _client() as a, _client() as b:
        first = a.get("/latest").json()["boot_id"]
        assert first == a.get("/events").json()["boot_id"]
        assert first != b.get("/latest").json()["boot_id"]
