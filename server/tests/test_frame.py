import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    FakeClock,
    FakeDescriber,
    FakeDetector,
    FakeWallClock,
    det,
    jpeg_bytes,
    wait_for,
)
from fastapi.testclient import TestClient
from PIL import Image

from config import Settings
from detector import Loaded
from domain import Detection
from main import create_app

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "contract"


def _client(detector: FakeDetector) -> TestClient:
    app = create_app(detector, FakeDescriber(), FakeClock(), FakeWallClock(), Settings())
    return TestClient(app)


def _post(client: TestClient, body: bytes | None = None) -> dict[str, Any]:
    r = client.post("/frame", files={"file": ("f.jpg", body or jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 200, r.text
    reply: dict[str, Any] = r.json()
    return reply


def test_quiet_frame_matches_contract_fixture() -> None:
    with _client(FakeDetector([[]])) as c:
        reply = _post(c)
    fixture = json.loads((CONTRACT / "frame-quiet.json").read_text())
    assert reply["detections"] == fixture["detections"]
    assert reply["alert"] is fixture["alert"]


def test_person_detection_is_returned_with_side_and_extra_fields() -> None:
    with _client(FakeDetector([[det(conf=0.71, cx=0.1)]])) as c:
        reply = _post(c)
    assert reply["detections"] == [{"label": "person", "conf": 0.71, "side": "left"}]
    assert reply["alert"] is None
    assert reply["frame_id"] == 1
    assert reply["server_ms"] == 10  # FakeClock advances 10 ms per call
    assert set(reply) <= {"detections", "alert", "frame_id", "server_ms", "description"}


def test_reply_with_detections_has_the_contract_shapes() -> None:
    fixture = json.loads((CONTRACT / "frame-alert.json").read_text())
    with _client(FakeDetector([[det(conf=0.38, cx=0.9)]])) as c:
        reply = _post(c)
    assert {"detections", "alert"} <= set(reply)
    assert set(reply) <= set(fixture) | {"frame_id", "server_ms"}
    for got, want in zip(reply["detections"], fixture["detections"], strict=False):
        assert got.keys() == want.keys()
        assert {k: type(v) for k, v in got.items()} == {k: type(v) for k, v in want.items()}
    assert reply["detections"][0]["side"] == "right"


def test_frame_ids_increase() -> None:
    with _client(FakeDetector()) as c:
        assert [_post(c)["frame_id"] for _ in range(3)] == [1, 2, 3]


def test_sides_follow_box_center() -> None:
    centers = [0.10, 0.50, 0.90, 1 / 3, 2 / 3]
    with _client(FakeDetector([[det(cx=x) for x in centers]])) as c:
        reply = _post(c)
    assert [d["side"] for d in reply["detections"]] == [
        "left",
        "ahead",
        "right",
        "ahead",
        "ahead",
    ]


def test_non_alert_label_is_dropped() -> None:
    with _client(FakeDetector([[det(label="car", conf=0.9)]])) as c:
        assert _post(c)["detections"] == []


def test_detection_below_conf_detect_is_dropped() -> None:
    with _client(FakeDetector([[det(conf=0.24), det(conf=0.25)]])) as c:
        assert [d["conf"] for d in _post(c)["detections"]] == [0.25]


def test_detection_runs_off_the_event_loop() -> None:
    detector = FakeDetector()
    loop_thread: list[int] = []

    app = create_app(detector, FakeDescriber(), FakeClock(), FakeWallClock(), Settings())

    @app.get("/thread")
    async def thread() -> None:
        loop_thread.append(threading.get_ident())

    with TestClient(app) as c:
        c.get("/thread")
        _post(c)
        _post(c)
    assert len(set(detector.detect_threads)) == 1  # the single inference slot
    assert detector.detect_threads[0] != loop_thread[0]


def test_detector_receives_the_decoded_image() -> None:
    seen: list[tuple[int, int]] = []

    class Spy(FakeDetector):
        def detect(self, image: Image.Image) -> list[Detection]:
            seen.append(image.size)
            return []

    with _client(Spy()) as c:
        _post(c, jpeg_bytes(80, 60))
    assert seen == [(80, 60)]


class SlowLoad(FakeDetector):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def load(self) -> Loaded:
        self.release.wait(5)
        return super().load()


@pytest.fixture
def slow() -> Iterator[SlowLoad]:
    d = SlowLoad()
    yield d
    d.release.set()


def test_not_ready_gives_503_then_ready(slow: SlowLoad) -> None:
    with _client(slow) as c:
        assert c.get("/health").json()["ready"] is False
        assert c.get("/health").json()["load_error"] is None
        r = c.post("/frame", files={"file": ("f.jpg", jpeg_bytes(), "image/jpeg")})
        assert r.status_code == 503
        slow.release.set()
        wait_for(lambda: c.get("/health").json()["ready"])
        health = c.get("/health").json()
        assert health["ready"] is True
        assert health["detector"] == "fake"
        assert health["device"] == "cpu"
        assert _post(c)["detections"] == []


class BrokenLoad(FakeDetector):
    def load(self) -> Loaded:
        raise RuntimeError("weights missing")


def test_load_failure_is_visible_and_frame_stays_503() -> None:
    with _client(BrokenLoad()) as c:
        wait_for(lambda: c.get("/health").json()["load_error"])
        health = c.get("/health").json()
        assert health["ready"] is False
        assert health["load_error"] == "RuntimeError"
        r = c.post("/frame", files={"file": ("f.jpg", jpeg_bytes(), "image/jpeg")})
        assert r.status_code == 503
