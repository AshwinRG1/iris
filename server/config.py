"""Settings, parsed from the environment once at startup."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, populate_by_name=True)

    conf_detect: float = 0.25
    alert_labels: str = "person"  # comma-separated
    model_path: str = "yolo26s.pt"
    imgsz: int = 640
    log_dir: str = "logs"
    commit: str | None = Field(default=None, validation_alias="IRIS_COMMIT")
    device: str = ""  # empty: CUDA, then MPS, then CPU

    @property
    def alert_label_set(self) -> frozenset[str]:
        return frozenset(s.strip() for s in self.alert_labels.split(",") if s.strip())
