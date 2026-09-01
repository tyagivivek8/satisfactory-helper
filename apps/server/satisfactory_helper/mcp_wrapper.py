from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from .compat import install_extractor_wrapper
from .config import LOCAL_DATA_ROOT
from .engine_data import install_engine_data_override


def _exact_local_path(value: str, expected: Path, label: str) -> Path:
    chosen = Path(value).resolve()
    expected = expected.resolve()
    if chosen != expected:
        raise SystemExit(f"{label} must be the Satisfactory Helper private path: {expected}")
    return chosen


def _private_snapshot_root(value: str) -> Path:
    chosen = Path(value).resolve()
    rolling = (LOCAL_DATA_ROOT / "snapshots" / "view").resolve()
    if chosen == rolling:
        return chosen

    pins = (LOCAL_DATA_ROOT / "snapshots" / "pinned").resolve()
    try:
        relative = chosen.relative_to(pins)
    except ValueError as exc:
        raise SystemExit(
            f"snapshot root must be the rolling view or an immutable private pin: {rolling}"
        ) from exc
    if len(relative.parts) != 1 or not re.fullmatch(r"[0-9a-f]{64}", relative.name):
        raise SystemExit("immutable snapshot root must be addressed by its SHA-256")
    if not chosen.is_dir():
        raise SystemExit(f"immutable snapshot root is unavailable: {chosen}")
    return chosen


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-root", required=True)
    parser.add_argument("--docs", required=True)
    parser.add_argument("--data-root", required=True)
    args = parser.parse_args(argv)

    snapshot_root = _private_snapshot_root(args.snapshot_root)
    data_root = _exact_local_path(
        args.data_root, LOCAL_DATA_ROOT / "engine" / "data", "engine data root"
    )
    docs = Path(args.docs).resolve()
    if not docs.is_file() or docs.name.casefold() != "en-us.json":
        raise SystemExit(f"docs must name the installed en-US.json: {docs}")

    os.environ["SATISFACTORY_SAVES"] = str(snapshot_root)
    os.environ["SATISFACTORY_DOCS"] = str(docs)
    install_engine_data_override(data_root)
    install_extractor_wrapper()
    from .readonly_mcp import mcp

    mcp.run()


if __name__ == "__main__":
    main()
