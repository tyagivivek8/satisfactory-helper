"""Keep game-derived spatial tables aligned with the installed Satisfactory build."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from satisfactory_mcp.core.gameassets.provenance import (
    installed_build,
    installed_build_from_exe,
    stale_artifacts,
)

from .config import PROJECT_ROOT, Settings

ENGINE_DATA_ENV = "SATISFACTORY_HELPER_ENGINE_DATA"
ENGINE_REVISION = "ade73e6c4736937eb49cc54364def7d6b30873d6"


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _path_value(value: object, *parts: str) -> object:
    for part in parts:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _current(data_root: Path, game_root: Path) -> tuple[bool, str]:
    try:
        pin, _ = installed_build(game_root)
    except Exception as exc:
        return False, f"could not identify installed game build: {exc}"
    if stale_artifacts(game_root, data_root):
        return False, pin
    try:
        collectibles = _read_json(data_root / "world_collectibles.json")
        collectible_pin = _path_value(
            collectibles, "_meta", "source", "placements", "game_build"
        )
    except (OSError, ValueError):
        return False, pin
    try:
        collectible_current = installed_build_from_exe(game_root)
    except Exception:
        collectible_current = None
    return collectible_pin == collectible_current, pin


def install_engine_data_override(data_root: Path | None = None) -> Path:
    chosen = data_root or Path(os.environ[ENGINE_DATA_ENV]).resolve()
    os.environ[ENGINE_DATA_ENV] = str(chosen)
    from satisfactory_mcp import config as upstream_config

    upstream_config.data_dir = lambda: chosen
    return chosen


def _run_generator(module: object, argv: list[str]) -> None:
    previous = sys.argv
    try:
        sys.argv = argv
        result = module.main()  # type: ignore[attr-defined]
    finally:
        sys.argv = previous
    if result:
        raise RuntimeError(f"{argv[0]} exited with code {result}")


def _generate(settings: Settings, staging_root: Path) -> None:
    from .compat import patch_archive_header_guard

    vendor_root = PROJECT_ROOT / "vendor" / "SatisfactoryMCP"
    data_root = staging_root / "data"
    data_root.mkdir(parents=True)

    vendor_text = str(vendor_root)
    if vendor_text not in sys.path:
        sys.path.insert(0, vendor_text)
    from tools import (
        gen_region_names,
        gen_resource_nodes,
        gen_world_collectibles,
        gen_world_resource_nodes,
    )

    game = str(settings.game_root)
    snapshot_view = str(settings.snapshot_view_root)
    snapshots = sorted(
        settings.snapshot_view_root.rglob("*.sav"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not snapshots:
        raise RuntimeError("cannot regenerate world tables without a safe save snapshot")
    patch_archive_header_guard(snapshots[0])

    gen_world_resource_nodes.ROOT = staging_root
    _run_generator(
        gen_world_resource_nodes,
        [
            "gen_world_resource_nodes.py",
            "--game",
            game,
            "--out",
            str(data_root / "world_resource_nodes.json"),
        ],
    )

    gen_resource_nodes.ROOT = staging_root
    _run_generator(gen_resource_nodes, ["gen_resource_nodes.py"])

    gen_world_collectibles.ROOT = staging_root
    _run_generator(
        gen_world_collectibles,
        [
            "gen_world_collectibles.py",
            snapshot_view,
            "--game",
            game,
            "--out",
            str(data_root / "world_collectibles.json"),
        ],
    )

    gen_region_names.ROOT = staging_root
    _run_generator(
        gen_region_names,
        [
            "gen_region_names.py",
            "--game",
            game,
            "--out",
            str(data_root / "region_names.json"),
            "--force",
        ],
    )


def _map_matches_install(output: Path, game_root: Path) -> bool:
    sidecar = output / "map.json"
    tile = output / "tiles" / "0" / "0_0.png"
    if not sidecar.is_file() or not tile.is_file():
        return False
    try:
        metadata = _read_json(sidecar)
        pinned = _path_value(
            metadata, "_meta", "sources", "map_slices", "game_version_pinned"
        )
        installed, _ = installed_build(game_root)
    except (OSError, ValueError, TypeError):
        return False
    return pinned == installed


def prepare_local_map(settings: Settings) -> dict[str, object]:
    """Return a private map generated from the currently installed game build."""
    output = settings.map_asset_root
    if _map_matches_install(output, settings.game_root):
        return {"path": str(output), "current": True, "regenerated": False}

    vendor_root = PROJECT_ROOT / "vendor" / "SatisfactoryMCP"
    vendor_text = str(vendor_root)
    if vendor_text not in sys.path:
        sys.path.insert(0, vendor_text)
    from tools import gen_map_image

    # Calibration reads the freshly generated resource-node table from the private
    # runtime data directory. No player-derived reference world is shipped in source.
    gen_map_image.ROOT = settings.engine_data_root.parent
    output.mkdir(parents=True, exist_ok=True)
    _run_generator(
        gen_map_image,
        [
            "gen_map_image.py",
            "--game",
            str(settings.game_root),
            "--size",
            "8192",
            "--out-dir",
            str(output),
            "--force",
        ],
    )
    return {
        "path": str(output),
        "current": _map_matches_install(output, settings.game_root),
        "regenerated": True,
    }


def _replace_directory(target: Path, build: Callable[[Path], None]) -> None:
    parent = target.parent.resolve()
    staging = parent / f"{target.name}.staging"
    retired = parent / f"{target.name}.retired"
    for path in (staging, retired):
        resolved = path.resolve()
        if not resolved.is_relative_to(parent):
            raise RuntimeError(f"Unsafe engine-data path: {resolved}")
        if resolved.exists():
            shutil.rmtree(resolved)
    build(staging)
    if target.exists():
        target.rename(retired)
    staging.rename(target)
    if retired.exists():
        shutil.rmtree(retired)


def prepare_engine_data(settings: Settings) -> dict[str, object]:
    """Return current private tables, regenerating only when the installed build changed."""
    target_root = settings.engine_data_root.parent
    data_root = settings.engine_data_root
    current, pin = _current(data_root, settings.game_root) if data_root.exists() else (False, "")
    regenerated = False
    if not current:
        _replace_directory(target_root, lambda staging: _generate(settings, staging))
        regenerated = True
        current, pin = _current(data_root, settings.game_root)
        if not current:
            raise RuntimeError("generated engine tables do not match the installed game build")

    marker = target_root / "satisfactory-helper.json"
    marker.write_text(
        json.dumps(
            {
                "engine_revision": ENGINE_REVISION,
                "game_build": pin,
                "regenerated_this_start": regenerated,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    install_engine_data_override(data_root)
    return {
        "path": str(data_root),
        "game_build": pin,
        "current": True,
        "regenerated": regenerated,
    }
