from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..codex import CodexRunner
from ..engine import Runtime
from ..models import ChatRequest

router = APIRouter(prefix="/api", tags=["planning"])


@router.post("/chat")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    runtime: Runtime = request.app.state.runtime
    try:
        current_token, snapshot, pinned_root = await runtime.pin_planning_snapshot(
            payload.save_token
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not pin the selected save snapshot: {exc}",
        ) from exc
    runner = CodexRunner(runtime)
    return StreamingResponse(
        runner.stream(
            payload,
            save_token=current_token,
            snapshot_name=snapshot.source_name,
            snapshot_root=pinned_root,
        ),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
