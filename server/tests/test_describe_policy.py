"""The describe policy as plain functions: no app, no clock, no network."""

from dataclasses import replace

from conftest import det

from describe_policy import (
    DescribeState,
    DescribeTuning,
    SceneBox,
    attached,
    may_describe,
    note_frame,
    scene_changed,
)
from domain import Detection

T = DescribeTuning(
    cooldown_ms=15_000, empty_reset_ms=3000, move_frac=0.25, area_frac=0.5, scene_reset_ms=60_000
)
LEFT = SceneBox("left", 0.10, 0.06)


def described(*boxes: SceneBox, at: int = 0) -> DescribeState:
    return attached(DescribeState(last_started_ms=at), tuple(boxes), at)


def test_nothing_described_yet_is_a_change() -> None:
    assert scene_changed(DescribeState(), (LEFT,), 0, T)


def test_the_same_scene_is_no_change() -> None:
    assert not scene_changed(described(LEFT), (LEFT,), 1000, T)


def test_in_flight_blocks_even_a_changed_scene() -> None:
    state = described(LEFT)
    changed = (LEFT, SceneBox("right", 0.9, 0.06))
    assert may_describe(state, changed, 20_000, T, in_flight=False)
    assert not may_describe(state, changed, 20_000, T, in_flight=True)


def test_cooldown_boundary_is_inclusive() -> None:
    state = described(LEFT)
    changed = (SceneBox("right", 0.9, 0.06),)
    assert not may_describe(state, changed, 14_999, T, in_flight=False)
    assert may_describe(state, changed, 15_000, T, in_flight=False)


def test_same_sides_but_a_different_count_per_side_is_a_change() -> None:
    old = (SceneBox("left", 0.05, 0.06), SceneBox("left", 0.2, 0.06), SceneBox("right", 0.9, 0.06))
    new = (SceneBox("left", 0.1, 0.06), SceneBox("right", 0.8, 0.06), SceneBox("right", 0.9, 0.06))
    assert scene_changed(described(*old), new, 1000, T)


def test_a_zero_area_described_box_changes_only_when_the_new_box_has_area() -> None:
    flat = SceneBox("left", 0.10, 0.0)
    assert not scene_changed(described(flat), (flat,), 1000, T)
    assert scene_changed(described(flat), (LEFT,), 1000, T)


def test_move_boundary_is_exactly_a_quarter_of_the_width() -> None:
    state = described(SceneBox("left", 0.0, 0.06))
    assert not scene_changed(state, (SceneBox("left", 0.25, 0.06),), 1000, T)
    assert scene_changed(state, (SceneBox("left", 0.2501, 0.06),), 1000, T)


def test_area_boundary_is_exactly_half_the_old_area() -> None:
    state = described(SceneBox("left", 0.1, 0.5))
    assert not scene_changed(state, (SceneBox("left", 0.1, 0.75),), 1000, T)
    assert not scene_changed(state, (SceneBox("left", 0.1, 0.25),), 1000, T)
    assert scene_changed(state, (SceneBox("left", 0.1, 0.7501),), 1000, T)


def test_scene_reset_boundary_is_exactly_sixty_seconds() -> None:
    state = described(LEFT)
    assert not scene_changed(state, (LEFT,), 59_999, T)
    assert scene_changed(state, (LEFT,), 60_000, T)


def test_pairing_per_side_in_order_is_the_nearest_pairing() -> None:
    # Paired in centre order the people moved 0.14 and 0.15: no change.
    old = (SceneBox("left", 0.10, 0.06), SceneBox("left", 0.35, 0.06))
    new = (SceneBox("left", 0.24, 0.06), SceneBox("left", 0.50, 0.06))
    assert not scene_changed(described(*old), new, 1000, T)


def kept(n: int) -> list[Detection]:
    return [det()] * n


def test_empty_run_resets_at_exactly_three_seconds_and_only_once() -> None:
    s = described(LEFT)
    s = note_frame(s, [], 1000, T.empty_reset_ms)
    s = note_frame(s, [], 3999, T.empty_reset_ms)
    assert s.last_scene is not None and s.resets == 0
    s = note_frame(s, [], 4000, T.empty_reset_ms)
    assert s.last_scene is None and s.resets == 1
    s = note_frame(s, [], 6000, T.empty_reset_ms)
    assert s.resets == 1  # one count per run


def test_a_detection_ends_the_empty_run() -> None:
    s = note_frame(described(LEFT), [], 1000, T.empty_reset_ms)
    s = note_frame(s, kept(1), 2000, T.empty_reset_ms)
    s = note_frame(s, [], 4500, T.empty_reset_ms)
    assert s.last_scene is not None  # a new run started at 4500


def test_a_new_run_counts_again_even_with_no_scene_to_forget() -> None:
    s = DescribeState()
    for t in (0, 3000, 3400, 3800):
        s = note_frame(s, [], t, T.empty_reset_ms)
    s = note_frame(s, kept(1), 4200, T.empty_reset_ms)
    for t in (4600, 7600):
        s = note_frame(s, [], t, T.empty_reset_ms)
    assert s.resets == 2


def test_a_pause_in_the_stream_is_not_empty_time() -> None:
    s = note_frame(described(LEFT), [], 1000, T.empty_reset_ms)
    s = note_frame(s, [], 11_000, T.empty_reset_ms)  # 10 s with no frames at all
    assert s.last_scene is not None and s.resets == 0
    s = note_frame(s, [], 14_000, T.empty_reset_ms)  # 3 s of real empty frames since the pause
    assert s.last_scene is None and s.resets == 1


def test_attached_keeps_the_epoch() -> None:
    s = replace(DescribeState(), resets=4)
    assert attached(s, (LEFT,), 5).resets == 4
