"""Spoken descriptions (ticket 08), driven through POST /frame with a scripted FakeDescriber."""

import io
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

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
from domain import Detection
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
        events: list[dict[str, Any]] = self.client.get("/events?after=0").json()["events"]
        return events

    def described(self, n: int) -> None:
        wait_for(lambda: sum(e["description"] is not None for e in self.events()) >= n)

    def quiet(self) -> None:
        time.sleep(0.15)  # a call that would start has started by now


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
    rig = make(FakeDescriber("late", delay_s=2), gemini_timeout_s=0.1)
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
    rig.alert_at(11_000, [LEFT])  # same-side repeat, inside the 15 s describe cooldown
    rig.quiet()
    rig.alert_at(21_000, [LEFT])  # after the cooldown, but the same scene
    rig.quiet()
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
    rig.alert_at(6000, [RIGHT])
    rig.quiet()
    assert len(d.calls) == 1


def test_failed_attempt_still_starts_the_cooldown(make: Make) -> None:
    d = FakeDescriber(error=RuntimeError("down"))
    rig = make(d)
    rig.alert_at(1000, [LEFT])
    wait_for(lambda: len(d.calls) == 1)
    rig.alert_at(6000, [RIGHT])
    rig.quiet()
    assert len(d.calls) == 1


def test_second_alert_while_one_is_in_flight_starts_no_second_call(make: Make) -> None:
    d = FakeDescriber("slow", delay_s=0.6)
    rig = make(d, describe_cooldown_s=0)
    rig.alert_at(1000, [LEFT])
    wait_for(lambda: len(d.calls) == 1)
    rig.alert_at(6000, [RIGHT])
    rig.quiet()
    assert len(d.calls) == 1
    rig.described(1)


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


def test_a_newer_event_without_a_description_hides_the_older_one(make: Make) -> None:
    rig = make(FakeDescriber("said"))
    rig.alert_at(1000, [LEFT])
    rig.described(1)
    rig.alert_at(6000, [RIGHT])  # no description allowed inside the cooldown
    rig.quiet()
    assert rig.frame(7000, [])["description_id"] is None


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
    rig.quiet()
    assert reply["description"] is None
    assert rig.events()[0]["description_id"] is None
