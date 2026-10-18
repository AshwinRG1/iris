"""Alert policy scenarios from the spec, driven through POST /frame (times in ms)."""

from pathlib import Path

import pytest
from conftest import FakeClock, FakeDescriber, FakeDetector, FakeWallClock, det, jpeg_bytes
from fastapi.testclient import TestClient
from pydantic import ValidationError

from config import Settings
from domain import Detection, Tuning
from main import create_app

Frames = list[tuple[int, list[Detection]]]


def run(frames: Frames, settings: Settings | None = None) -> dict[int, str]:
    """Post each (t_ms, detections) frame at that time; return {t_ms: alert} for alerts."""
    clock = FakeClock(tick=0)
    detector = FakeDetector([dets for _, dets in frames])
    app = create_app(detector, FakeDescriber(), clock, FakeWallClock(), settings or Settings())
    alerts: dict[int, str] = {}
    with TestClient(app) as client:
        for t, _ in frames:
            clock.t = t
            r = client.post("/frame", files={"file": ("f.jpg", jpeg_bytes(), "image/jpeg")})
            assert r.status_code == 200, r.text
            if r.json()["alert"] is not None:
                alerts[t] = r.json()["alert"]
    return alerts


HIT = [det(conf=0.6, cx=0.1)]
MISS: list[Detection] = []


def test_two_hits_in_a_row_alert_on_the_second() -> None:  # scenario 1
    assert run([(0, HIT), (400, HIT)]) == {400: "possible person, left"}


def test_one_dropped_detection_does_not_reset_the_evidence() -> None:  # scenario 2
    assert run([(0, HIT), (400, MISS), (800, HIT)]) == {800: "possible person, left"}


def test_two_hits_three_frames_apart_do_not_alert() -> None:  # scenario 3
    assert run([(0, HIT), (400, MISS), (800, MISS), (1200, HIT)]) == {}


def test_detections_below_conf_alert_return_but_never_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # scenario 4
    # The defaults are equal (0.10), so raise CONF_ALERT to open a gap above CONF_DETECT.
    monkeypatch.setenv("CONF_ALERT", "0.30")
    weak = [det(conf=0.18, cx=0.1)]  # kept (>= CONF_DETECT 0.10), below CONF_ALERT 0.30
    assert run([(t, weak) for t in range(0, 2000, 400)], Settings()) == {}


def test_with_the_default_thresholds_every_kept_detection_is_a_hit() -> None:
    faint = [det(conf=0.10, cx=0.1)]
    assert run([(0, faint), (400, faint)]) == {400: "possible person, left"}


def test_empty_clip_has_no_alerts() -> None:  # scenario 10
    assert run([(t, MISS) for t in range(0, 4000, 400)]) == {}


def test_most_confident_hit_in_the_current_frame_names_the_side() -> None:  # scenario 9
    both = [det(conf=0.6, cx=0.1), det(conf=0.8, cx=0.9)]
    assert run([(0, both), (400, both)]) == {400: "possible person, right"}


def test_same_side_person_in_view_is_announced_every_10_seconds() -> None:  # scenario 5
    alerts = run([(t, HIT) for t in range(0, 15601, 400)])
    assert alerts == {400: "possible person, left", 10400: "possible person, left"}


def test_other_side_waits_for_the_cooldown() -> None:  # scenario 6
    right = [det(conf=0.8, cx=0.9)]
    frames: Frames = [(0, HIT), (400, HIT)]
    frames += [(t, MISS) for t in range(800, 2000, 400)]
    frames += [(t, right) for t in range(2000, 4801, 400)]
    assert run(frames) == {400: "possible person, left", 4400: "possible person, right"}


def test_hits_from_before_a_pause_are_forgotten() -> None:  # scenario 7
    assert run([(0, HIT), (5000, HIT), (5400, HIT)]) == {5400: "possible person, left"}


@pytest.mark.parametrize(
    "env",
    [
        {"NEEDED": "4"},  # needed > window
        {"NEEDED": "0"},
        {"CONF_ALERT": "0"},
        {"CONF_ALERT": "1.5"},
        {"CONF_ALERT": "0.05"},  # below the default CONF_DETECT of 0.10
        {"COOLDOWN_S": "inf"},
        {"COOLDOWN_S": "nan"},
        {"COOLDOWN_S": "-1"},
        {"SAME_SIDE_REPEAT_S": "-0.5"},
        {"WINDOW_MAX_AGE_S": "0"},
    ],
)
def test_impossible_tuning_is_refused_at_startup(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValidationError):
        Settings()


def test_tuning_comes_from_the_environment_in_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COOLDOWN_S", "1.5")
    monkeypatch.setenv("SAME_SIDE_REPEAT_S", "2")
    monkeypatch.setenv("NEEDED", "1")
    alerts = run([(0, HIT), (1000, HIT), (1500, HIT), (2000, HIT), (3000, HIT)], Settings())
    # needed=1: the first hit alerts; 1500 ms cooldown; same side again only after 2 s.
    assert list(alerts) == [0, 2000]


def test_a_hit_exactly_2_seconds_old_still_counts() -> None:
    assert run([(0, HIT), (2000, HIT)]) == {2000: "possible person, left"}


def test_a_hit_older_than_2_seconds_is_forgotten() -> None:
    assert run([(0, HIT), (2001, HIT)]) == {}


def test_a_miss_frame_never_alerts_even_if_the_window_holds_a_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEEDED", "1")
    monkeypatch.setenv("COOLDOWN_S", "0.1")
    monkeypatch.setenv("SAME_SIDE_REPEAT_S", "0.1")
    assert run([(0, HIT), (500, MISS)], Settings()) == {0: "possible person, left"}


def test_a_frame_arriving_with_an_older_time_does_not_break_the_policy() -> None:
    right = [det(conf=0.6, cx=0.9)]
    frames: Frames = [(0, HIT), (400, HIT), (300, HIT), (4400, right), (4800, right)]
    # 300 is treated as 400: no alert, no error; later frames behave as normal.
    assert run(frames) == {400: "possible person, left", 4800: "possible person, right"}


def test_a_hit_on_another_side_counts_toward_the_window() -> None:  # scenario 14
    right = [det(conf=0.6, cx=0.9)]
    assert run([(0, HIT), (400, right)]) == {400: "possible person, right"}


def test_detection_thresholds_default_to_the_recall_tuning() -> None:
    fields = Settings.model_fields
    assert fields["conf_detect"].default == 0.10
    assert fields["conf_alert"].default == 0.10
    assert Settings().tuning.conf_alert == 0.10
    assert Tuning().conf_alert == 0.10


def test_env_example_lists_the_same_detection_thresholds() -> None:
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    values = dict(line.split("=", 1) for line in example.splitlines() if line.startswith("CONF_"))
    assert float(values["CONF_DETECT"]) == Settings.model_fields["conf_detect"].default
    assert float(values["CONF_ALERT"]) == Settings.model_fields["conf_alert"].default
