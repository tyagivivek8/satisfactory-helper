from __future__ import annotations

from fastapi import APIRouter, Request

from ..engine import ENGINE_REVISION, Runtime
from ..models import SnapshotInfo, StatusResponse
from ..providers import PROVIDER_MODELS

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status", response_model=StatusResponse)
async def status(request: Request) -> StatusResponse:
    runtime: Runtime = request.app.state.runtime
    state = "ready"
    if runtime.snapshot is None or not any(
        provider.get("ready") for provider in (runtime.codex, runtime.claude)
    ):
        state = "blocked"
    elif runtime.warnings:
        state = "degraded"
    snapshot = SnapshotInfo.model_validate(runtime.snapshot.to_json()) if runtime.snapshot else None
    return StatusResponse(
        state=state,
        generation=runtime.generation,
        codex={
            **runtime.codex,
            "model": runtime.settings.codex_model or "Codex automatic default",
        },
        providers={
            "codex": {
                **runtime.codex,
                "label": "Codex",
                "model": runtime.settings.codex_model or "",
                "models": PROVIDER_MODELS["codex"],
            },
            "claude": {
                **runtime.claude,
                "label": "Claude",
                "model": runtime.settings.claude_model or "",
                "models": PROVIDER_MODELS["claude"],
            },
        },
        game_data={
            "path": str(runtime.settings.docs_path),
            "sha256": runtime.docs_sha256,
            "spatial_tables": runtime.engine_data,
        },
        save=snapshot,
        safety={
            "original_save_root": str(runtime.settings.original_saves_root),
            "engine_save_root": str(runtime.settings.snapshot_view_root),
            "originals_passed_to_parser": False,
            "save_root_write_access": False,
            "snapshot_only": True,
        },
        engine={
            "name": "SatisfactoryMCP",
            "revision": ENGINE_REVISION,
            "license": "PolyForm Noncommercial 1.0.0",
        },
        warnings=runtime.warnings,
    )


@router.post("/refresh")
async def refresh(request: Request) -> dict[str, object]:
    runtime: Runtime = request.app.state.runtime
    changed = await runtime.refresh()
    return {"changed": changed, "generation": runtime.generation}
