"""Settings, parsed from the environment once at startup."""

from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from domain import Tuning


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, populate_by_name=True)

    detector: Literal["yolo", "zeroshot"] = "yolo"
    conf_detect: float = 0.25
    alert_labels: str = "person"  # comma-separated
    model_path: str = "yolo26s.pt"
    imgsz: int = 640
    log_dir: str = "logs"
    commit: str | None = Field(default=None, validation_alias="IRIS_COMMIT")
    device: str = ""  # empty: CUDA, then MPS, then CPU
    iris_api_key: str = ""  # empty: no key checks anywhere
    # Caps the whole request body, multipart framing included.
    max_upload_bytes: int = Field(default=2_000_000, gt=0)

    # Alert policy tuning. Seconds in the environment, ms inside.
    conf_alert: float = 0.35
    window: int = 3
    needed: int = 2
    cooldown_s: float = Field(default=4.0, ge=0, allow_inf_nan=False)
    same_side_repeat_s: float = Field(default=10.0, ge=0, allow_inf_nan=False)
    window_max_age_s: float = Field(default=2.0, ge=0, allow_inf_nan=False)

    # Spoken descriptions (ticket 08). An empty key turns them off at startup.
    describe: Literal["on", "off"] = "on"
    gemini_api_key: SecretStr = SecretStr("")  # SecretStr keeps it out of repr and logs
    gemini_model: str = "gemini-3.8-flash"
    gemini_timeout_s: float = Field(default=6.0, gt=0, allow_inf_nan=False)
    describe_cooldown_s: float = Field(default=15.0, ge=0, allow_inf_nan=False)
    describe_max_age_s: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    gemini_prompt_file: str = ""  # empty: prompts/describe.txt beside the code

    @field_validator("alert_labels")
    @classmethod
    def _normalize_labels(cls, v: str) -> str:
        labels = sorted({s.strip().lower() for s in v.split(",") if s.strip()})
        if not labels:
            raise ValueError("ALERT_LABELS must name at least one label")
        return ",".join(labels)

    @model_validator(mode="after")
    def _check_tuning(self) -> Self:
        if self.conf_alert < self.conf_detect:
            raise ValueError("CONF_ALERT must be at least CONF_DETECT")
        self.tuning  # noqa: B018  (Tuning validates itself; a bad value fails startup here)
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
