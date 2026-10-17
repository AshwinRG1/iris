"""Core types. Standard library only."""

from dataclasses import dataclass
from typing import Literal

Side = Literal["left", "ahead", "right"]


@dataclass(frozen=True)
class Box:
    """Normalized 0..1 corners."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def side(self) -> Side:
        cx = (self.x1 + self.x2) / 2
        if cx < 1 / 3:
            return "left"
        if cx > 2 / 3:
            return "right"
        return "ahead"


@dataclass(frozen=True)
class Detection:
    label: str
    conf: float
    box: Box

    @property
    def side(self) -> Side:
        return self.box.side


@dataclass(frozen=True)
class Tuning:
    """Alert policy tuning. Times are integer ms; the environment speaks seconds."""

    conf_alert: float = 0.35
    window: int = 3
    needed: int = 2
    cooldown_ms: int = 4000
    same_side_repeat_ms: int = 10000
    window_max_age_ms: int = 2000

    def __post_init__(self) -> None:
        if not 0 < self.conf_alert <= 1:
            raise ValueError("conf_alert must be in (0, 1]")
        if not 1 <= self.needed <= self.window:
            raise ValueError("needed must satisfy 1 <= needed <= window")
        for name in ("cooldown_ms", "same_side_repeat_ms", "window_max_age_ms"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ValueError(f"{name} must be an integer >= 0")
        if self.window_max_age_ms <= 0:
            raise ValueError("window_max_age_ms must be > 0")


@dataclass(frozen=True)
class Alert:
    """A fired alert: its text, the strongest hit it names, and the time the policy used."""

    text: str
    hit: Detection
    t_ms: int


@dataclass(frozen=True)
class PolicyState:
    recent: tuple[tuple[int, bool], ...] = ()  # (t_ms, hit) pairs
    last_alert_ms: int | None = None
    last_alert_side: Side | None = None
