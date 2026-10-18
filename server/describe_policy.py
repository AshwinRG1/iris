"""When a description is allowed: a pure decision over state, scene and time (design section 8).

Standard library and domain only.
"""

from dataclasses import dataclass, replace

from domain import Detection, Side


@dataclass(frozen=True)
class SceneBox:
    """One person as the described scene remembers them.

    `cx` is the box centre as a share of the frame width, `area` a share of the frame area.
    """

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
    # The 60 s scene reset runs from the described alert's time, not from the answer's arrival.
    attached_ms: int | None = None
    empty_since_ms: int | None = None  # first frame of the current run of empty frames
    run_reset: bool = False  # this empty run already crossed the reset, so it counts once
    resets: int = 0  # an epoch: an answer that started before a reset must not set the scene
    last_frame_ms: int | None = None


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
    # A gap with no frames at all is a paused stream, not an empty view: it starts a new run.
    paused = state.last_frame_ms is not None and now_ms - state.last_frame_ms > empty_reset_ms
    state = replace(state, last_frame_ms=now_ms)
    if kept:
        return replace(state, empty_since_ms=None, run_reset=False)
    if state.empty_since_ms is None or paused:
        return replace(state, empty_since_ms=now_ms, run_reset=False)
    if state.run_reset or now_ms - state.empty_since_ms < empty_reset_ms:
        return state
    return replace(state, last_scene=None, resets=state.resets + 1, run_reset=True)


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
    # Per side, both lists are in centre order, and pairing in order is the nearest pairing.
    for side in {b.side for b in old}:
        pairs = zip(
            [b for b in old if b.side == side], [b for b in scene if b.side == side], strict=False
        )
        if sum(b.side == side for b in old) != sum(b.side == side for b in scene):
            return True
        for was, now in pairs:
            if abs(was.cx - now.cx) > tuning.move_frac:
                return True
            if was.area <= 0:
                if now.area > 0:
                    return True
            elif abs(now.area - was.area) / was.area > tuning.area_frac:
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
