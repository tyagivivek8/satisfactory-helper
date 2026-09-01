from __future__ import annotations

import json
from pathlib import Path

from satisfactory_helper import engine_data
from satisfactory_helper.api.map import DEFAULT_BOUNDS, _map_info


def test_map_info_falls_back_cleanly_when_assets_are_missing(tmp_path: Path) -> None:
    info = _map_info(tmp_path)

    assert info["available"] is False
    assert info["bounds"] == DEFAULT_BOUNDS
    assert info["source"] is None
    assert info["reason"]


def test_map_info_reads_generated_tile_metadata(tmp_path: Path) -> None:
    tile = tmp_path / "tiles" / "0" / "0_0.png"
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b"png")
    (tmp_path / "map.json").write_text(
        json.dumps(
            {
                "x_min_m": -1000,
                "x_max_m": 2000,
                "y_min_m": -1500,
                "y_max_m": 2500,
                "_meta": {
                    "tiles": {"tile_px": 256, "max_z": 5},
                    "tiles_2x": {"tile_px": 512, "max_z": 4},
                },
            }
        ),
        encoding="utf-8",
    )

    info = _map_info(tmp_path)

    assert info["available"] is True
    assert info["source"] == "installed_game"
    assert info["bounds"]["y_min_m"] == -1500.0
    assert info["tile_px"] == 256
    assert info["max_z"] == 5
    assert info["dense_tile_px"] == 512
    assert info["dense_max_z"] == 4


def test_generated_map_must_match_the_installed_game_build(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "local"
    tile = output / "tiles" / "0" / "0_0.png"
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b"png")
    (output / "map.json").write_text(
        json.dumps(
            {
                "_meta": {
                    "sources": {
                        "map_slices": {"game_version_pinned": "buildVersion 502094"}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        engine_data,
        "installed_build",
        lambda _game_root: ("buildVersion 502094", {}),
    )

    assert engine_data._map_matches_install(output, tmp_path)

    monkeypatch.setattr(
        engine_data,
        "installed_build",
        lambda _game_root: ("buildVersion 600000", {}),
    )
    assert not engine_data._map_matches_install(output, tmp_path)
