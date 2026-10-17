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
