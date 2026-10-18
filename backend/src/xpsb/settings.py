"""User settings, persisted as JSON in the data directory."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

AiModel = Literal["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]


class AiSettings(BaseModel):
    enabled: bool = True
    model: AiModel = "claude-opus-5-5"
    job_limit_usd: float = Field(default=5.0, ge=0, le=1000)
    monthly_limit_usd: float = Field(default=20.0, ge=0, le=10000)


ImagerySourceChoice = Literal["auto", "bayern_dop40", "nrw_dop", "custom", "none"]


class ImagerySettings(BaseModel):
    # "auto": the built-in open source that covers the area, if any.
    source: ImagerySourceChoice = "auto"
    custom_type: Literal["wms", "xyz"] = "wms"
    custom_max_zoom: int = Field(default=19, ge=10, le=23)  # XYZ only
    custom_url: str = Field(default="", max_length=1000)
    custom_layer: str = Field(default="", max_length=200)
    custom_attribution: str = Field(default="", max_length=300)
    # The user confirms they may use the custom source for this purpose.
    custom_rights_confirmed: bool = False


class Settings(BaseModel):
    language: Literal["de", "en"] = "de"
    xplane_path: str | None = None
    dsftool_path: str | None = None
    ai: AiSettings = Field(default_factory=AiSettings)
    imagery: ImagerySettings = Field(default_factory=ImagerySettings)
    # Off until the user opts in (no network traffic without consent).
    update_check: bool = False


class SettingsStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def load(self) -> Settings:
        with self._lock:
            if not self._path.exists():
                return Settings()
            try:
                return Settings.model_validate(json.loads(self._path.read_text(encoding="utf-8")))
            except (ValueError, ValidationError):
                # A corrupt file must not brick the app; keep it for inspection.
                self._path.replace(self._path.with_suffix(".corrupt.json"))
                return Settings()

    def save(self, settings: Settings) -> Settings:
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        return settings
