"""When a description is allowed: a pure decision over state, scene and time (design section 8).

Standard library and domain only.
"""

from dataclasses import dataclass, replace

from domain import Detection, Side


@dataclass(frozen=True)
class SceneBox:
    """One person as the described scene remembers them; all values are normalized."""

    side: Side
    cx: float
    area: float


Scene = tuple[SceneBox, ...]  # sorted by centre x


@dataclass(frozen=True)
class DescribeTuning:
    cooldown_ms: int
    empty_reset_ms: int
    move_frac: float
    area_frac: float
    scene_reset_ms: int


@dataclass(frozen=True)
class DescribeState:
    last_started_ms: int | None = None  # attempts count, so a failing API isn't hammered
    last_scene: Scene | None = None  # set only when a description was attached
    attached_ms: int | None = None  # the alert time of that description
    empty_since_ms: int | None = None  # first frame of the current run of empty frames


def scene_of(kept: list[Detection]) -> Scene:
    boxes = (
        SceneBox(d.side, (d.box.x1 + d.box.x2) / 2, (d.box.x2 - d.box.x1) * (d.box.y2 - d.box.y1))
        for d in kept
    )
    return tuple(sorted(boxes, key=lambda b: b.cx))


def boxes_line(kept: list[Detection]) -> str:
    """The text beside the image, strongest box first: `Boxes: left 0.92, ahead 0.61`."""
    ranked = sorted(kept, key=lambda d: d.conf, reverse=True)
    return "Boxes: " + ", ".join(f"{d.side} {d.conf:.2f}" for d in ranked)


def note_frame(
    state: DescribeState, kept: list[Detection], now_ms: int, empty_reset_ms: int
) -> DescribeState:
    """Every frame: a long enough run of empty frames forgets the described scene."""
    if kept:
        return state if state.empty_since_ms is None else replace(state, empty_since_ms=None)
    if state.empty_since_ms is None:
        return replace(state, empty_since_ms=now_ms)
    if now_ms - state.empty_since_ms >= empty_reset_ms and state.last_scene is not None:
        return replace(state, last_scene=None)
    return state


def attached(state: DescribeState, scene: Scene, t_ms: int) -> DescribeState:
    return replace(state, last_scene=scene, attached_ms=t_ms)


def scene_changed(state: DescribeState, scene: Scene, now_ms: int, tuning: DescribeTuning) -> bool:
    old = state.last_scene
    if old is None or state.attached_ms is None:
        return True
    if now_ms - state.attached_ms >= tuning.scene_reset_ms:
        return True
    if len(old) != len(scene) or {b.side for b in old} != {b.side for b in scene}:
        return True
    free = list(old)
    for b in scene:  # greedy: each person takes the nearest unused described one on their side
        same_side = [o for o in free if o.side == b.side]
        if not same_side:
            return True  # same sides, different head count per side
        match = min(same_side, key=lambda o: abs(o.cx - b.cx))
        free.remove(match)
        if abs(match.cx - b.cx) > tuning.move_frac:
            return True
        if match.area <= 0:
            if b.area > 0:
                return True
        elif abs(b.area - match.area) / match.area > tuning.area_frac:
            return True
    return False


def may_describe(
    state: DescribeState, scene: Scene, now_ms: int, tuning: DescribeTuning, in_flight: bool
) -> bool:
    if in_flight:
        return False
    if state.last_started_ms is not None and now_ms - state.last_started_ms < tuning.cooldown_ms:
        return False
    return scene_changed(state, scene, now_ms, tuning)
