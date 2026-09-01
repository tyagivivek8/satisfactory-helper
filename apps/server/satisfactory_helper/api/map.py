from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ..engine import Runtime

router = APIRouter(prefix="/api", tags=["map"])

DEFAULT_BOUNDS = {
    "x_min_m": -3247.0,
    "x_max_m": 4253.0,
    "y_min_m": -3750.0,
    "y_max_m": 3750.0,
}


def _sidecar(asset_root: Path) -> dict[str, Any]:
    path = asset_root / "map.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _whole(value: Any, default: int, minimum: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= minimum:
        return value
    return default


def _map_info(asset_root: Path) -> dict[str, Any]:
    payload = _sidecar(asset_root)
    metadata = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    tiles = metadata.get("tiles") if isinstance(metadata.get("tiles"), dict) else {}
    dense = metadata.get("tiles_2x") if isinstance(metadata.get("tiles_2x"), dict) else {}
    bounds = dict(DEFAULT_BOUNDS)
    for key in bounds:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            bounds[key] = float(value)

    tile_px = _whole(tiles.get("tile_px"), 256, 1)
    max_z = _whole(tiles.get("max_z"), 5)
    dense_tile_px = _whole(dense.get("tile_px"), 0, 1) or None
    dense_max_z = _whole(dense.get("max_z"), 0) if dense_tile_px else None
    available = (asset_root / "tiles" / "0" / "0_0.png").is_file()
    stamp_path = asset_root / "map.json"
    stamp = "missing"
    if stamp_path.is_file():
        stat = stamp_path.stat()
        stamp = f"{stat.st_size}:{stat.st_mtime_ns}:{tiles.get('game_version_pinned', '')}"
    version = hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12]
    return {
        "available": available,
        "source": "installed_game" if available else None,
        "bounds": bounds,
        "tile_px": tile_px,
        "max_z": max_z,
        "dense_tile_px": dense_tile_px,
        "dense_max_z": dense_max_z,
        "version": version,
        "reason": None if available else "The local game-map tile pyramid has not been generated.",
    }


@router.get("/map")
async def map_info(request: Request) -> dict[str, Any]:
    runtime: Runtime = request.app.state.runtime
    return _map_info(runtime.settings.map_asset_root)


@router.get("/maptiles/{z}/{x}/{y}")
async def map_tile(
    request: Request,
    z: int,
    x: int,
    y: int,
    density: int = Query(default=1, ge=1, le=2),
) -> FileResponse:
    runtime: Runtime = request.app.state.runtime
    info = _map_info(runtime.settings.map_asset_root)
    if not info["available"]:
        raise HTTPException(status_code=404, detail=info["reason"])

    use_dense = density == 2 and info["dense_tile_px"] and z <= info["dense_max_z"]
    max_z = info["dense_max_z"] if use_dense else info["max_z"]
    if z < 0 or z > max_z:
        raise HTTPException(status_code=404, detail="Map zoom is outside the generated pyramid.")
    span = 1 << z
    if not (0 <= x < span and 0 <= y < span):
        raise HTTPException(status_code=404, detail="Map tile is outside the generated pyramid.")

    tree = "tiles@2x" if use_dense else "tiles"
    path = runtime.settings.map_asset_root / tree / str(z) / f"{x}_{y}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Map tile is missing.")
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{info["version"]}"',
        },
    )
