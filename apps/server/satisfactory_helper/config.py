from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


def _resource_root() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root).resolve()
    return Path(__file__).resolve().parents[3]


def _local_data_root() -> Path:
    configured = os.environ.get("SATISFACTORY_HELPER_DATA")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            local_app_data = str(Path.home() / "AppData" / "Local")
        return (Path(local_app_data) / "Satisfactory Helper").resolve()
    return (_resource_root() / ".local-data").resolve()


def _provider_executable(name: str) -> str:
    configured = os.environ.get(f"SATISFACTORY_HELPER_{name.upper()}_EXECUTABLE", "").strip()
    if configured:
        return str(Path(configured).expanduser().resolve())
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).resolve().parent / "tools" / f"{name}.exe"
        if bundled.is_file():
            return str(bundled)
    return shutil.which(name) or shutil.which(f"{name}.exe") or name


PROJECT_ROOT = _resource_root()
LOCAL_DATA_ROOT = _local_data_root()

_DOCS_SUFFIX = Path("CommunityResources") / "Docs" / "en-US.json"
_INSTALL_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\Satisfactory"),
    Path(r"C:\Program Files\Epic Games\SatisfactoryEarlyAccess"),
    Path(r"D:\SteamLibrary\steamapps\common\Satisfactory"),
    Path(r"E:\SteamLibrary\steamapps\common\Satisfactory"),
    Path(r"G:\SteamLibrary\steamapps\common\Satisfactory"),
)


def _docs_path() -> Path:
    configured = os.environ.get("SATISFACTORY_DOCS")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"SATISFACTORY_DOCS does not name a file: {path}")
        return path
    for root in _INSTALL_CANDIDATES:
        candidate = root / _DOCS_SUFFIX
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "Satisfactory game data was not found. Set SATISFACTORY_DOCS to "
        r"<install>\CommunityResources\Docs\en-US.json."
    )


def _saves_root() -> Path:
    configured = os.environ.get("SATISFACTORY_HELPER_ORIGINAL_SAVES") or os.environ.get(
        "SATISFACTORY_SAVES"
    )
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = str(Path.home() / "AppData" / "Local")
    return (Path(local_app_data) / "FactoryGame" / "Saved" / "SaveGames").resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    original_saves_root: Path
    docs_path: Path
    snapshot_root: Path
    web_dist: Path
    codex_executable: str
    codex_model: str | None
    claude_executable: str
    claude_model: str | None
    port: int
    snapshot_poll_seconds: float

    @property
    def snapshot_view_root(self) -> Path:
        return self.snapshot_root / "view"

    @property
    def game_root(self) -> Path:
        return self.docs_path.parents[2]

    @property
    def engine_data_root(self) -> Path:
        return (LOCAL_DATA_ROOT / "engine" / "data").resolve()

    @property
    def map_asset_root(self) -> Path:
        return (self.engine_data_root / "local").resolve()

    @classmethod
    def load(cls) -> Settings:
        codex = _provider_executable("codex")
        claude = _provider_executable("claude")
        configured_model = os.environ.get("SATISFACTORY_HELPER_CODEX_MODEL", "").strip()
        configured_claude_model = os.environ.get(
            "SATISFACTORY_HELPER_CLAUDE_MODEL", ""
        ).strip()
        return cls(
            original_saves_root=_saves_root(),
            docs_path=_docs_path(),
            snapshot_root=(LOCAL_DATA_ROOT / "snapshots").resolve(),
            web_dist=(PROJECT_ROOT / "apps" / "web" / "dist").resolve(),
            codex_executable=codex,
            codex_model=configured_model or None,
            claude_executable=claude,
            claude_model=configured_claude_model or None,
            port=int(os.environ.get("SATISFACTORY_HELPER_PORT", "8713")),
            snapshot_poll_seconds=float(os.environ.get("SATISFACTORY_HELPER_POLL_SECONDS", "2")),
        )
