"""Settings, parsed from the environment once at startup."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True)

    detector: Literal["yolo", "zeroshot"] = "yolo"
    conf_detect: float = 0.25
    alert_labels: str = "person"  # comma-separated
    model_path: str = "yolo26s.pt"
    imgsz: int = 640
    device: str = ""  # empty: CUDA, then MPS, then CPU

    @property
    def alert_label_set(self) -> frozenset[str]:
        return frozenset(s.strip() for s in self.alert_labels.split(",") if s.strip())
