from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from .compat import install_extractor_wrapper
from .config import PROJECT_ROOT, Settings
from .engine_data import prepare_engine_data, prepare_local_map
from .providers import SUPPORTED_CODEX_CLI_VERSION, codex_version_is_supported
from .snapshots import SnapshotFirewall, SnapshotRecord

ENGINE_REVISION = "ade73e6c4736937eb49cc54364def7d6b30873d6"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _codex_status(executable: str) -> dict[str, Any]:
    try:
        version_result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        version_detail = (version_result.stdout or version_result.stderr).strip()
        if version_result.returncode != 0 or not codex_version_is_supported(version_detail):
            installed = version_detail or "unknown"
            return {
                "ready": False,
                "auth": "incompatible",
                "detail": (
                    f"Codex {installed} is incompatible with this release. Install "
                    f"@openai/codex@{SUPPORTED_CODEX_CLI_VERSION}."
                ),
            }
        result = subprocess.run(
            [executable, "login", "status"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ready": False, "auth": "unavailable", "detail": str(exc)}
    detail = (result.stdout or result.stderr).strip()
    return {
        "ready": result.returncode == 0 and "Logged in" in detail,
        "auth": "chatgpt" if "ChatGPT" in detail else "unknown",
        "detail": detail,
    }


def _claude_status(executable: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [executable, "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ready": False, "auth": "unavailable", "detail": str(exc)}
    try:
        payload = json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError:
        payload = {}
    ready = result.returncode == 0 and payload.get("loggedIn") is True
    subscription = str(payload.get("subscriptionType") or "account").title()
    detail = (
        f"Logged in using Claude {subscription}"
        if ready
        else result.stderr.strip() or "Claude is not signed in"
    )
    return {
        "ready": ready,
        "auth": str(payload.get("authMethod") or "unknown"),
        "detail": detail,
    }


@dataclass(slots=True)
class Runtime:
    settings: Settings
    firewall: SnapshotFirewall
    engine_app: FastAPI
    snapshot: SnapshotRecord | None
    docs_sha256: str
    codex: dict[str, Any]
    claude: dict[str, Any]
    engine_data: dict[str, object]
    generation: int = 1
    warnings: list[str] = field(default_factory=list)
    _watch_task: asyncio.Task[None] | None = None
    _stop: asyncio.Event | None = None
    _snapshot_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _token_snapshots: dict[str, SnapshotRecord] = field(default_factory=dict)

    @classmethod
    def create(cls, settings: Settings | None = None) -> Runtime:
        settings = settings or Settings.load()
        firewall = SnapshotFirewall(settings.original_saves_root, settings.snapshot_root)
        snapshot: SnapshotRecord | None = None
        warnings: list[str] = []
        try:
            snapshot = firewall.snapshot_latest()
        except Exception as exc:
            warnings.append(f"Could not create initial snapshot: {exc}")

        settings.snapshot_view_root.mkdir(parents=True, exist_ok=True)
        os.environ["SATISFACTORY_HELPER_ORIGINAL_SAVES"] = str(settings.original_saves_root)
        os.environ["SATISFACTORY_SAVES"] = str(settings.snapshot_view_root)
        os.environ["SATISFACTORY_DOCS"] = str(settings.docs_path)

        engine_data = prepare_engine_data(settings)
        try:
            engine_data["map"] = prepare_local_map(settings)
        except Exception as exc:
            warnings.append(f"Could not generate the local in-game map: {exc}")
            engine_data["map"] = {
                "path": str(settings.map_asset_root),
                "current": False,
                "regenerated": False,
            }
        install_extractor_wrapper()
        from satisfactory_mcp.interfaces.web.app import create_app

        engine_app = create_app(prewarm=False)
        return cls(
            settings=settings,
            firewall=firewall,
            engine_app=engine_app,
            snapshot=snapshot,
            docs_sha256=_sha256(settings.docs_path),
            codex=_codex_status(settings.codex_executable),
            claude=_claude_status(settings.claude_executable),
            engine_data=engine_data,
            warnings=warnings,
        )

    async def start(self) -> None:
        if self._watch_task is not None:
            return
        self._stop = asyncio.Event()
        self._watch_task = asyncio.create_task(self._watch(), name="save-snapshot-watcher")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._watch_task is not None:
            await self._watch_task
        self._watch_task = None
        self._stop = None

    async def refresh(self) -> bool:
        async with self._snapshot_lock:
            previous = self.snapshot.source_sha256 if self.snapshot else None
            record = await asyncio.to_thread(self.firewall.snapshot_latest)
            self.snapshot = record
            changed = bool(record and record.source_sha256 != previous)
            if changed:
                self.generation += 1
            return changed

    async def _watch(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                await self.refresh()
            except Exception as exc:
                message = f"Autosave watcher: {exc}"
                if not self.warnings or self.warnings[-1] != message:
                    self.warnings.append(message)
                    self.warnings = self.warnings[-8:]
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.snapshot_poll_seconds
                )
            except TimeoutError:
                continue

    async def engine_payloads(self) -> dict[str, Any]:
        async with self._snapshot_lock:
            return await self._engine_payloads_locked()

    async def _engine_payloads_locked(self) -> dict[str, Any]:
        if self.snapshot is None:
            raise FileNotFoundError("No Satisfactory save snapshot is available")
        transport = httpx.ASGITransport(app=self.engine_app)
        paths = {
            "summary": "/api/summary",
            "factories": "/api/factories",
            "machines": "/api/machines",
            "floors": "/api/floors",
            "belts": "/api/belts",
            "pipes": "/api/pipes",
            "structures": "/api/structures",
            "storage": "/api/storage",
        }
        async with httpx.AsyncClient(transport=transport, base_url="http://engine") as client:
            responses = await asyncio.gather(*(client.get(path) for path in paths.values()))
        payload: dict[str, Any] = {}
        failures: list[str] = []
        for key, response in zip(paths, responses, strict=True):
            if response.is_success:
                payload[key] = response.json()
            else:
                detail = response.text[:500]
                failures.append(f"{key}: HTTP {response.status_code} {detail}")
        if failures:
            raise RuntimeError("Engine could not build the current world: " + "; ".join(failures))
        from .site_profile import storage_level_assignments

        state = self.engine_app.state.load_state(None, None)
        landmarks: list[dict[str, Any]] = []
        for row in state.projection.get("landmarks", ()):
            if not isinstance(row, dict):
                continue
            pos = row.get("pos")
            if not isinstance(pos, (list, tuple)) or len(pos) < 3:
                continue
            cls = str(row.get("cls") or "")
            building = state.game.buildings.get(cls)
            footprint = getattr(building, "footprint", None) if building else None
            landmarks.append(
                {
                    "instance_leaf": str(row.get("instance") or "").rsplit(".", 1)[-1],
                    "cls": cls,
                    "name": state.game.building_name(cls),
                    "x_m": round(float(pos[0]) / 100, 1),
                    "y_m": round(float(pos[1]) / 100, 1),
                    "z_m": round(float(pos[2]) / 100, 1),
                    "yaw": row.get("yaw"),
                    "w_m": round(footprint.width_m, 1) if footprint else None,
                    "l_m": round(footprint.depth_m, 1) if footprint else None,
                    "h_m": round(footprint.height_m, 1) if footprint else None,
                }
            )
        payload["landmarks"] = landmarks
        storage_levels = storage_level_assignments(state)
        for row in payload["storage"]["storage"]:
            level = storage_levels.get(row["instance_leaf"])
            row.update(
                level
                or {
                    "platform": None,
                    "global_floor": None,
                    "top_m": None,
                    "floor_assignment": None,
                }
            )
        payload["generation"] = self.generation
        payload["snapshot"] = self.snapshot.to_json()
        save_token = str(payload["summary"].get("save_token") or "")
        if save_token:
            self._remember_token_snapshot(save_token, self.snapshot)
        return payload

    def _remember_token_snapshot(self, save_token: str, snapshot: SnapshotRecord) -> None:
        self._token_snapshots[save_token] = snapshot
        while len(self._token_snapshots) > 16:
            self._token_snapshots.pop(next(iter(self._token_snapshots)))

    async def pin_planning_snapshot(
        self, requested_token: str | None
    ) -> tuple[str, SnapshotRecord, Path]:
        """Resolve a UI token to the exact immutable save bytes it was served with."""
        async with self._snapshot_lock:
            if requested_token and requested_token in self._token_snapshots:
                record = self._token_snapshots[requested_token]
                root = await asyncio.to_thread(self.firewall.pin, record)
                return requested_token, record, root

            if self.snapshot is None:
                raise FileNotFoundError("No Satisfactory save snapshot is available")
            transport = httpx.ASGITransport(app=self.engine_app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://engine"
            ) as client:
                response = await client.get("/api/summary")
            if not response.is_success:
                raise RuntimeError(response.text[:1_000])
            current_token = str(response.json()["save_token"])
            if requested_token and requested_token != current_token:
                raise LookupError(
                    "The autosave changed since this workspace loaded. Refresh before planning."
                )
            record = self.snapshot
            self._remember_token_snapshot(current_token, record)
            root = await asyncio.to_thread(self.firewall.pin, record)
            return current_token, record, root

    @property
    def engine_directory(self) -> Path:
        return PROJECT_ROOT / "vendor" / "SatisfactoryMCP"
