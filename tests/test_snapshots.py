from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from satisfactory_helper.engine import Runtime
from satisfactory_helper.snapshots import SnapshotFirewall


def world_save_bytes(payload: bytes) -> bytes:
    return (14).to_bytes(4, byteorder="little", signed=True) + payload


def test_snapshotting_preserves_source_bytes_metadata_and_path(tmp_path: Path) -> None:
    originals = tmp_path / "originals"
    cache = tmp_path / "cache"
    source = originals / "account" / "world_autosave_0.sav"
    source.parent.mkdir(parents=True)
    source.write_bytes(world_save_bytes(b"synthetic-save-content" * 100))

    firewall = SnapshotFirewall(originals, cache, settle_seconds=0)
    before = firewall.source_fingerprint(source)
    record = firewall.snapshot_latest()
    after = firewall.source_fingerprint(source)

    assert record is not None
    assert before == after
    assert record.source_sha256 == before[2]
    snapshot = cache / record.snapshot_path
    assert snapshot.read_bytes() == source.read_bytes()
    assert snapshot.resolve().is_relative_to(cache.resolve())
    assert not snapshot.resolve().is_relative_to(originals.resolve())


def test_unchanged_source_reuses_the_existing_snapshot(tmp_path: Path) -> None:
    originals = tmp_path / "originals"
    cache = tmp_path / "cache"
    source = originals / "world.sav"
    originals.mkdir()
    source.write_bytes(world_save_bytes(b"same"))
    firewall = SnapshotFirewall(originals, cache, settle_seconds=0)

    first = firewall.snapshot_latest()
    second = firewall.snapshot_latest()

    assert first is second
    assert len(list((cache / "blobs").glob("*.sav"))) == 1


def test_pinned_request_keeps_exact_bytes_when_autosave_filename_rotates(
    tmp_path: Path,
) -> None:
    originals = tmp_path / "originals"
    cache = tmp_path / "cache"
    source = originals / "account" / "world_autosave_0.sav"
    source.parent.mkdir(parents=True)
    first_bytes = world_save_bytes(b"first-world-state")
    second_bytes = world_save_bytes(b"second-world-state")
    source.write_bytes(first_bytes)
    firewall = SnapshotFirewall(originals, cache, settle_seconds=0)

    first = firewall.snapshot_latest()
    assert first is not None
    pinned_root = firewall.pin(first)
    pinned_save = pinned_root / first.source_relative_path

    source.write_bytes(second_bytes)
    second = firewall.snapshot_latest()

    assert second is not None
    assert second.source_sha256 != first.source_sha256
    assert pinned_save.read_bytes() == first_bytes
    assert pinned_save.stat().st_mtime_ns == first.source_mtime_ns
    assert (cache / second.snapshot_path).read_bytes() == second_bytes
    assert source.read_bytes() == second_bytes


@pytest.mark.asyncio
async def test_planning_token_resolves_to_the_snapshot_served_with_that_token(
    tmp_path: Path,
) -> None:
    originals = tmp_path / "originals"
    cache = tmp_path / "cache"
    source = originals / "world_autosave_0.sav"
    originals.mkdir()
    first_bytes = world_save_bytes(b"ui-visible-world")
    source.write_bytes(first_bytes)
    firewall = SnapshotFirewall(originals, cache, settle_seconds=0)
    first = firewall.snapshot_latest()
    assert first is not None

    source.write_bytes(world_save_bytes(b"newer-autosave-world-state"))
    second = firewall.snapshot_latest()
    assert second is not None
    runtime = Runtime(
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        firewall=firewall,
        engine_app=FastAPI(),
        snapshot=second,
        docs_sha256="docs",
        codex={},
        claude={},
        engine_data={},
        _token_snapshots={"sav:served": first},
    )

    token, record, pinned_root = await runtime.pin_planning_snapshot("sav:served")

    assert token == "sav:served"
    assert record is first
    assert (pinned_root / first.source_relative_path).read_bytes() == first_bytes


def test_snapshot_root_may_not_be_inside_the_original_save_tree(tmp_path: Path) -> None:
    originals = tmp_path / "saves"
    originals.mkdir()
    with pytest.raises(ValueError, match="outside"):
        SnapshotFirewall(originals, originals / "snapshots")


def test_paths_outside_the_save_root_are_rejected(tmp_path: Path) -> None:
    originals = tmp_path / "saves"
    cache = tmp_path / "cache"
    outside = tmp_path / "outside.sav"
    originals.mkdir()
    outside.write_bytes(b"not in scope")
    firewall = SnapshotFirewall(originals, cache, settle_seconds=0)
    with pytest.raises(ValueError, match="outside"):
        firewall.source_fingerprint(outside)


def test_newer_server_manager_metadata_is_not_selected_as_a_world(tmp_path: Path) -> None:
    originals = tmp_path / "originals"
    cache = tmp_path / "cache"
    gameplay = originals / "account" / "world_autosave_0.sav"
    metadata = originals / "ServerManager_V2.sav"
    gameplay.parent.mkdir(parents=True)
    gameplay.write_bytes(world_save_bytes(b"world"))
    metadata.write_bytes(b"MSGF" + b"metadata")
    metadata.touch()

    record = SnapshotFirewall(originals, cache, settle_seconds=0).snapshot_latest()

    assert record is not None
    assert record.source_name == gameplay.name
    assert record.source_relative_path == str(gameplay.relative_to(originals))
