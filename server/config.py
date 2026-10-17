"""Settings, parsed from the environment once at startup."""

from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from domain import Tuning


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True)

    conf_detect: float = 0.25
    alert_labels: str = "person"  # comma-separated
    model_path: str = "yolo26s.pt"
    imgsz: int = 640
    device: str = ""  # empty: CUDA, then MPS, then CPU

    # Alert policy tuning. Seconds in the environment, ms inside.
    conf_alert: float = 0.35
    window: int = 3
    needed: int = 2
    cooldown_s: float = 4.0
    same_side_repeat_s: float = 10.0
    window_max_age_s: float = 2.0

    @model_validator(mode="after")
    def _check_tuning(self) -> Self:
        if not 0 < self.conf_alert <= 1:
            raise ValueError("CONF_ALERT must be in (0, 1]")
        if not 1 <= self.needed <= self.window:
            raise ValueError("NEEDED must satisfy 1 <= NEEDED <= WINDOW")
        return self

    @property
    def tuning(self) -> Tuning:
        return Tuning(
            conf_alert=self.conf_alert,
            window=self.window,
            needed=self.needed,
            cooldown_ms=round(self.cooldown_s * 1000),
            same_side_repeat_ms=round(self.same_side_repeat_s * 1000),
            window_max_age_ms=round(self.window_max_age_s * 1000),
        )

    @property
    def alert_label_set(self) -> frozenset[str]:
        return frozenset(s.strip() for s in self.alert_labels.split(",") if s.strip())
