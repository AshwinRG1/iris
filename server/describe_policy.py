"""When a description is allowed: a pure decision over state, scene and time (design section 8).

Standard library and domain only.
"""

from dataclasses import dataclass

from domain import Detection, Side

# What the rescuer would hear about: how many people, and from which sides.
Scene = tuple[int, frozenset[Side]]


@dataclass(frozen=True)
class DescribeState:
    last_started_ms: int | None = None  # attempts count, so a failing API isn't hammered
    last_scene: Scene | None = None  # set only when a description was attached


def scene_of(kept: list[Detection]) -> Scene:
    return len(kept), frozenset(d.side for d in kept)


def boxes_line(kept: list[Detection]) -> str:
    """The text beside the image, strongest box first: `Boxes: left 0.92, ahead 0.61`."""
    ranked = sorted(kept, key=lambda d: d.conf, reverse=True)
    return "Boxes: " + ", ".join(f"{d.side} {d.conf:.2f}" for d in ranked)


def may_describe(
    state: DescribeState, scene: Scene, now_ms: int, cooldown_ms: int, in_flight: bool
) -> bool:
    if in_flight or scene == state.last_scene:
        return False
    return state.last_started_ms is None or now_ms - state.last_started_ms >= cooldown_ms
