from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .providers import model_is_allowed


class SnapshotInfo(BaseModel):
    source_name: str
    source_relative_path: str
    source_size: int
    source_mtime_ns: int
    source_sha256: str
    snapshot_path: str
    created_at: datetime


class StatusResponse(BaseModel):
    state: Literal["ready", "degraded", "blocked"]
    generation: int
    codex: dict[str, Any]
    providers: dict[str, dict[str, Any]]
    game_data: dict[str, Any]
    save: SnapshotInfo | None
    safety: dict[str, Any]
    engine: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    save_token: str | None = None
    selected_factory: str | None = Field(default=None, max_length=300)
    selected_floor: int | None = None
    selected_site: dict[str, float | str] | None = None
    conversation: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    provider: Literal["codex", "claude"] = "codex"
    model: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_provider_model(self) -> ChatRequest:
        if not model_is_allowed(self.provider, self.model):
            raise ValueError(f"Unsupported {self.provider} model: {self.model}")
        return self
