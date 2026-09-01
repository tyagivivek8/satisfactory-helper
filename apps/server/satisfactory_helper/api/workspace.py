from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..engine import Runtime

router = APIRouter(prefix="/api", tags=["workspace"])


@router.get("/workspace")
async def workspace(request: Request) -> dict:
    runtime: Runtime = request.app.state.runtime
    try:
        return await runtime.engine_payloads()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
