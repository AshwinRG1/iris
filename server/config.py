"""Settings, parsed from the environment once at startup."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True)

    conf_detect: float = 0.25
    alert_labels: str = "person"  # comma-separated
    model_path: str = "yolo26s.pt"
    imgsz: int = 640
    device: str = ""  # empty: CUDA, then MPS, then CPU
    iris_api_key: str = ""  # empty: no key checks anywhere
    max_upload_bytes: int = 2_000_000

    @property
    def alert_label_set(self) -> frozenset[str]:
        return frozenset(s.strip() for s in self.alert_labels.split(",") if s.strip())
