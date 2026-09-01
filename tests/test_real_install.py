from __future__ import annotations

import asyncio

import pytest


@pytest.mark.integration
def test_real_snapshot_projects_current_world_without_touching_original() -> None:
    from satisfactory_helper.main import app

    runtime = app.state.runtime
    if runtime.snapshot is None:
        pytest.skip("no local Satisfactory save")
    source = runtime.settings.original_saves_root / runtime.snapshot.source_relative_path
    before = runtime.firewall.source_fingerprint(source)

    payload = asyncio.run(runtime.engine_payloads())

    after = runtime.firewall.source_fingerprint(source)
    assert before == after
    assert payload["summary"]["header"]["save_version"] == 60
    assert payload["summary"]["header"]["build_version"] >= 502094
    kinds = ("machines", "extractors", "generators")
    assert sum(len(payload["machines"][kind]) for kind in kinds) > 0
    assert payload["floors"]["counts"]["platforms"] > 0
    assert payload["belts"]["count"] > 0
    assert payload["storage"]["count"] > 0
    assert any(row["global_floor"] is not None for row in payload["storage"]["storage"])
    assert runtime.snapshot.source_name == payload["summary"]["header"]["filename"]
