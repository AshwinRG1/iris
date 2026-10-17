"""Spoken descriptions (ticket 08), driven through POST /frame with a scripted FakeDescriber."""

import io
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import (
    FakeDescriber,
    FakeDetector,
    FakeWallClock,
    SettableClock,
    det,
    jpeg_bytes,
    wait_for,
)
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from config import Settings
from deps import Runtime
from domain import Box, Detection
from main import create_app

LEFT = det(conf=0.8, cx=0.1)
RIGHT = det(conf=0.7, cx=0.9)
SECRET = "sekret-gemini-key"


class Rig:
    def __init__(self, describer: FakeDescriber | None, clock: SettableClock, **env: Any) -> None:
        self.detector = FakeDetector()
        settings = Settings(gemini_api_key=SecretStr(SECRET), **env)
        app = create_app(self.detector, describer, clock, FakeWallClock(), settings)
        self.clock = clock
        self.client = TestClient(app)
        self.runtime: Runtime = app.state.runtime
        self.describer = describer

    def frame(self, t: int, dets: list[Detection]) -> dict[str, Any]:
        self.detector.script.append(dets)
        self.clock.t = t
        r = self.client.post("/frame", files={"file": ("f.jpg", jpeg_bytes(), "image/jpeg")})
        assert r.status_code == 200, r.text
        reply: dict[str, Any] = r.json()
        return reply

    def alert_at(self, t: int, dets: list[Detection]) -> dict[str, Any]:
        self.frame(t - 400, dets)
        reply = self.frame(t, dets)
        assert reply["alert"] is not None
        return reply

    def events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = self.client.get("/events?since=0").json()["events"]
        return events

    def described(self, n: int) -> None:
        wait_for(lambda: sum(e["description"] is not None for e in self.events()) >= n)

    def run(self, t0: int, t1: int, dets: list[Detection]) -> None:
        """Frames every 400 ms from t0 to t1, as the glasses send them."""
        for t in range(t0, t1 + 1, 400):
            self.frame(t, dets)

    def idle(self) -> None:
        wait_for(lambda: not self.runtime.describe_tasks)

    def release(self) -> None:
        """Let a gated describer answer. The gate belongs to the app's loop."""
        assert self.client.portal is not None and self.describer is not None
        assert self.describer.gate is not None
        self.client.portal.call(self.describer.gate.set)


Make = Callable[..., Rig]


@pytest.fixture
def make() -> Iterator[Make]:
    rigs: list[Rig] = []

    def _make(
        describer: FakeDescriber | None, clock: SettableClock | None = None, **env: Any
    ) -> Rig:
        rig = Rig(describer, clock or SettableClock(0), **env)
        rig.client.__enter__()
        rigs.append(rig)
        return rig

    yield _make
    for r in rigs:
        r.client.__exit__(None, None, None)


def test_alert_reply_is_instant_and_the_description_arrives_on_the_event(make: Make) -> None:
    d = FakeDescriber("Possible person to your left.")
    rig = make(d)
    reply = rig.alert_at(1000, [LEFT])
    assert reply["description"] is None and reply["description_id"] is None
    rig.described(1)
    event = rig.events()[0]
    assert event["description"] == "Possible person to your left."
    assert event["description_id"] == 1
    assert len(d.calls) == 1


def test_scenario_12_a_describer_slower_than_the_timeout_leaves_null(
    make: Make, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="iris")
    rig = make(FakeDescriber("late", gated=True), gemini_timeout_s=0.1)
    rig.alert_at(1000, [LEFT])
    wait_for(lambda: "TimeoutError" in caplog.text)
    assert rig.events()[0]["description"] is None
    assert rig.frame(2000, [LEFT])["description"] is None


@pytest.mark.parametrize(
    "describer",
    [FakeDescriber(error=RuntimeError(f"boom {SECRET}")), FakeDescriber("   ")],
    ids=["raises", "empty"],
)
def test_failure_or_empty_text_leaves_null_and_logs_a_warning(
    make: Make, caplog: pytest.LogCaptureFixture, describer: FakeDescriber
) -> None:
    caplog.set_level(logging.INFO, logger="iris")
    rig = make(describer)
    rig.alert_at(1000, [LEFT])
    wait_for(lambda: any(r.levelno == logging.WARNING for r in caplog.records))
    assert rig.events()[0]["description"] is None
    assert SECRET not in caplog.text


def test_several_people_make_one_call_listing_every_box(make: Make) -> None:
    d = FakeDescriber("Three possible people.")
    rig = make(d)
    crowd = [det(conf=0.61, cx=0.5), det(conf=0.92, cx=0.1), det(conf=0.71, cx=0.15)]
    rig.alert_at(1000, crowd)
    rig.described(1)
    assert len(d.calls) == 1
    assert d.calls[0][1] == "Boxes: left 0.92, left 0.71, ahead 0.61"
    assert len(rig.events()) == 1


def test_the_jpeg_sent_has_the_boxes_drawn(make: Make) -> None:
    d = FakeDescriber("x")
    rig = make(d)
    rig.alert_at(1000, [LEFT])  # the box spans x 3..10 and y 9..38 of the 64x48 frame
    rig.described(1)
    sent = Image.open(io.BytesIO(d.calls[0][0])).convert("RGB")
    plain = Image.open(io.BytesIO(jpeg_bytes())).convert("RGB")
    assert sent.size == plain.size
    assert sent.getpixel((4, 24)) != plain.getpixel((4, 24))  # on the box edge
    assert sent.getpixel((58, 40)) == plain.getpixel((58, 40))  # far from every box


def test_unchanged_scene_gets_no_new_call_inside_or_after_the_cooldown(make: Make) -> None:
    d = FakeDescriber("one")
    rig = make(d)
    rig.alert_at(1000, [LEFT])
    rig.described(1)
    rig.idle()
    rig.alert_at(11_000, [LEFT])  # same-side repeat, inside the 15 s describe cooldown
    assert not rig.runtime.describe_tasks  # a task is created inside the /frame handler
    rig.alert_at(21_000, [LEFT])  # after the cooldown, but the same scene
    assert not rig.runtime.describe_tasks
    assert len(d.calls) == 1


def test_new_person_or_side_after_the_cooldown_is_described_again(make: Make) -> None:
    d = FakeDescriber("text")
    rig = make(d)
    rig.alert_at(1000, [LEFT])
    rig.described(1)
    rig.alert_at(21_000, [LEFT, RIGHT])
    wait_for(lambda: len(d.calls) == 2)
    rig.described(2)
    rig.alert_at(41_000, [RIGHT])  # sides changed again
    wait_for(lambda: len(d.calls) == 3)


def test_changed_scene_inside_the_cooldown_makes_no_call(make: Make) -> None:
    d = FakeDescriber("text")
    rig = make(d)
    rig.alert_at(1000, [LEFT])
    rig.described(1)
    rig.idle()
    rig.alert_at(6000, [RIGHT])
    assert not rig.runtime.describe_tasks
    assert len(d.calls) == 1


def test_failed_attempt_still_starts_the_cooldown(make: Make) -> None:
    d = FakeDescriber(error=RuntimeError("down"))
    rig = make(d)
    rig.alert_at(1000, [LEFT])
    wait_for(lambda: len(d.calls) == 1)
    rig.idle()
    rig.alert_at(6000, [RIGHT])
    assert not rig.runtime.describe_tasks
    assert len(d.calls) == 1


def test_second_alert_while_one_is_in_flight_starts_no_second_call(make: Make) -> None:
    d = FakeDescriber("slow", gated=True)
    rig = make(d, describe_cooldown_s=0, describe_max_age_s=10)
    rig.alert_at(1000, [LEFT])
    wait_for(lambda: len(d.calls) == 1)
    rig.alert_at(6000, [RIGHT])
    assert len(rig.runtime.describe_tasks) == 1 and len(d.calls) == 1
    rig.release()
    rig.idle()
    assert len(d.calls) == 1


def test_stale_answer_is_discarded(make: Make, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="iris")
    clock = SettableClock(0)
    d = FakeDescriber("too late", clock=clock, advance_ms=5001)
    rig = make(d, clock)
    rig.alert_at(1000, [LEFT])
    wait_for(lambda: "stale" in caplog.text)
    assert rig.events()[0]["description"] is None


def test_description_ids_increase_and_show_only_on_the_latest_fresh_event(make: Make) -> None:
    rig = make(FakeDescriber("said"))
    rig.alert_at(1000, [LEFT])
    rig.described(1)
    reply = rig.frame(3000, [])
    assert (reply["description"], reply["description_id"]) == ("said", 1)
    rig.alert_at(21_000, [LEFT, RIGHT])
    rig.described(2)
    assert [e["description_id"] for e in rig.events()] == [1, 2]
    assert rig.frame(23_000, [])["description_id"] == 2
    old = rig.frame(40_000, [])  # event 2 is 19 s old
    assert (old["description"], old["description_id"]) == (None, None)


def test_answer_for_an_event_that_is_no_longer_the_latest_is_dropped(
    make: Make, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="iris")
    d = FakeDescriber("said", gated=True)
    rig = make(d, describe_max_age_s=10)
    rig.alert_at(1000, [LEFT])
    wait_for(lambda: len(d.calls) == 1)
    rig.alert_at(6000, [RIGHT])  # event 2; no call may start while event 1 is in flight
    rig.release()
    rig.idle()
    assert "a newer alert fired" in caplog.text
    assert [e["description"] for e in rig.events()] == [None, None]
    assert rig.frame(7000, [])["description_id"] is None
    # The dropped answer never marked its scene as described.
    rig.alert_at(21_000, [LEFT])
    wait_for(lambda: len(d.calls) == 2)
    rig.idle()


def test_same_scene_is_described_again_after_a_failure(make: Make) -> None:
    d = FakeDescriber(error=RuntimeError("down"))
    rig = make(d)
    rig.alert_at(1000, [LEFT])
    wait_for(lambda: len(d.calls) == 1)
    rig.idle()
    d.error, d.text = None, "back"
    rig.alert_at(21_000, [LEFT])
    rig.described(1)
    assert len(d.calls) == 2


def test_events_since_returns_the_described_copy(make: Make) -> None:
    rig = make(FakeDescriber("said"))
    rig.alert_at(1000, [LEFT])
    assert rig.client.get("/events?since=0").json()["events"][0]["description"] in (None, "said")
    rig.described(1)
    later = rig.client.get("/events?since=0").json()["events"]
    assert (later[0]["id"], later[0]["description"], later[0]["description_id"]) == (1, "said", 1)


def test_http_status_error_logs_the_status_code_only(
    make: Make, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="iris")
    request = httpx.Request("POST", f"https://example.test/?key={SECRET}")
    error = httpx.HTTPStatusError(
        f"boom {SECRET}", request=request, response=httpx.Response(429, request=request)
    )
    rig = make(FakeDescriber(error=error))
    rig.alert_at(1000, [LEFT])
    wait_for(lambda: "HTTPStatusError" in caplog.text)
    assert "429" in caplog.text
    assert SECRET not in caplog.text and "boom" not in caplog.text
    rig.idle()


def test_shutdown_cancels_a_running_description_and_closes_the_describer() -> None:
    d = FakeDescriber("never", gated=True)
    rig = Rig(d, SettableClock(0))
    with rig.client:
        rig.alert_at(1000, [LEFT])
        wait_for(lambda: len(d.calls) == 1)
    assert d.cancelled and d.closed
    assert not rig.runtime.describe_tasks


def test_health_stays_ready_and_never_shows_the_key(make: Make) -> None:
    rig = make(FakeDescriber("x"))
    wait_for(lambda: rig.client.get("/health").json()["ready"])
    assert SECRET not in rig.client.get("/health").text


def test_described_event_is_logged_a_second_time(make: Make, log_dir: Path) -> None:
    rig = make(FakeDescriber("said"))
    rig.alert_at(1000, [LEFT])
    rig.described(1)
    lines = (log_dir / "events.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert '"description_id": 1' in lines[1] and '"id": 1' in lines[1]


def test_no_describer_means_descriptions_are_off(make: Make) -> None:
    rig = make(None)
    reply = rig.alert_at(1000, [LEFT])
    assert not rig.runtime.describe_tasks
    assert reply["description"] is None
    assert rig.events()[0]["description_id"] is None


# Scene change rules (cooldown 15 s, empty reset 3 s, move 0.25, area 0.5, reset 60 s).
A = det(conf=0.8, cx=0.06)  # left, centre x 0.06, area 0.06


def _described_at_400(make: Make) -> tuple[Rig, FakeDescriber]:
    d = FakeDescriber("said")
    rig = make(d)
    rig.alert_at(400, [A])
    rig.described(1)
    rig.idle()
    return rig, d


def test_count_or_sides_change_after_the_cooldown_makes_a_call(make: Make) -> None:
    rig, d = _described_at_400(make)
    rig.alert_at(16_000, [A, RIGHT])
    wait_for(lambda: len(d.calls) == 2)


def test_empty_then_refill_makes_a_call_after_the_cooldown(make: Make) -> None:
    rig, d = _described_at_400(make)
    rig.run(800, 4400, [])  # empty for 3.6 s
    rig.alert_at(16_000, [A])
    wait_for(lambda: len(d.calls) == 2)


def test_a_short_gap_does_not_forget_the_scene(make: Make) -> None:
    rig, d = _described_at_400(make)
    rig.run(800, 3200, [])  # empty for 2.4 s
    rig.alert_at(16_000, [A])
    assert not rig.runtime.describe_tasks and len(d.calls) == 1


def test_a_big_shift_of_the_centre_makes_a_call(make: Make) -> None:
    rig, d = _described_at_400(make)
    rig.alert_at(16_000, [det(conf=0.8, cx=0.32)])  # same side, 0.26 further right
    wait_for(lambda: len(d.calls) == 2)


def test_a_big_change_of_area_makes_a_call(make: Make) -> None:
    rig, d = _described_at_400(make)
    close = Detection("person", 0.8, Box(0.0, 0.1, 0.2, 0.9))  # area 0.16 against 0.06
    rig.alert_at(16_000, [close])
    wait_for(lambda: len(d.calls) == 2)


def test_small_moves_and_resizes_make_no_call(make: Make) -> None:
    rig, d = _described_at_400(make)
    rig.alert_at(16_000, [det(conf=0.8, cx=0.2)])  # shifts 0.14
    assert not rig.runtime.describe_tasks
    slightly_taller = Detection("person", 0.8, Box(0.01, 0.2, 0.11, 0.9))  # area 0.07
    rig.alert_at(30_000, [slightly_taller])
    assert not rig.runtime.describe_tasks and len(d.calls) == 1


def test_sixty_seconds_after_the_description_the_same_scene_is_described_again(
    make: Make,
) -> None:
    rig, d = _described_at_400(make)
    rig.alert_at(60_400, [A])
    wait_for(lambda: len(d.calls) == 2)


def test_a_person_standing_still_is_described_once_per_minute(make: Make) -> None:
    d = FakeDescriber("said")
    rig = make(d)
    rig.run(0, 400, [A])
    rig.described(1)
    rig.idle()
    rig.run(800, 59_600, [A])  # alerts at 10.4 s, 20.4 s ... 50.4 s
    assert len(rig.events()) == 6  # the alerts fired; only the scene rule held back the calls
    assert len(d.calls) == 1
    rig.run(60_000, 60_400, [A])  # the next alert is at 60.4 s
    wait_for(lambda: len(d.calls) == 2)


def test_a_swap_to_a_new_person_without_a_gap_waits_for_the_cooldown(make: Make) -> None:
    d = FakeDescriber("said")
    rig = make(d)
    rig.run(0, 400, [A])
    rig.described(1)
    rig.idle()
    other = det(conf=0.8, cx=0.32)  # same side, clearly elsewhere
    rig.run(800, 10_000, [A])
    rig.run(10_400, 10_400, [other])  # alerts here, 10 s after the last, inside the cooldown
    assert len(d.calls) == 1 and not rig.runtime.describe_tasks
    rig.run(10_800, 20_400, [other])  # alerts again at 20.4 s
    wait_for(lambda: len(d.calls) == 2)
    assert d.calls[1][1] == "Boxes: left 0.80"


def test_triggers_inside_the_cooldown_make_no_call(make: Make) -> None:
    rig, d = _described_at_400(make)
    rig.alert_at(5400, [A, det(conf=0.9, cx=0.9)])  # count and sides changed
    assert not rig.runtime.describe_tasks
    rig.alert_at(10_400, [det(conf=0.8, cx=0.32)])  # left again, moved
    assert not rig.runtime.describe_tasks and len(d.calls) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"describe_empty_reset_s": 0},
        {"describe_empty_reset_s": float("inf")},
        {"describe_move_frac": 0},
        {"describe_move_frac": 1.5},
        {"describe_area_frac": 0},
        {"describe_area_frac": float("nan")},
        {"describe_scene_reset_s": 0},
        {"describe_scene_reset_s": float("inf")},
    ],
)
def test_bad_scene_settings_fail_startup(bad: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        Settings(**bad)  # type: ignore[arg-type]


def test_a_late_answer_does_not_bring_back_a_scene_that_was_reset(make: Make) -> None:
    d = FakeDescriber("said", gated=True)
    rig = make(d)
    rig.alert_at(400, [A])
    wait_for(lambda: len(d.calls) == 1)
    rig.run(800, 4000, [])  # empty for 3.2 s while the call is out
    rig.frame(4400, [A])  # the person returns
    rig.release()
    rig.described(1)  # still spoken
    rig.idle()
    rig.alert_at(16_000, [A])
    wait_for(lambda: len(d.calls) == 2)


def test_a_pause_in_the_stream_does_not_reset_the_scene(make: Make) -> None:
    rig, d = _described_at_400(make)
    rig.frame(800, [])
    rig.frame(10_800, [])  # ten seconds with no frames at all
    rig.alert_at(16_000, [A])
    assert not rig.runtime.describe_tasks and len(d.calls) == 1
