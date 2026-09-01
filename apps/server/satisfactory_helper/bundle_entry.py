"""PyInstaller entry point for the self-contained Windows release."""

from __future__ import annotations

import os
import sys


def main() -> int:
    os.environ.setdefault("SATISFACTORY_HELPER_BUNDLED", "1")
    args = sys.argv[1:]

    if args and args[0] == "mcp":
        from satisfactory_helper.mcp_wrapper import main as mcp_main

        mcp_main(args[1:])
        return 0

    if len(args) >= 2 and args[:2] == ["-m", "satisfactory_helper.extractor_wrapper"]:
        from satisfactory_helper.extractor_wrapper import main as extractor_main

        return extractor_main(args[2:])

    from satisfactory_helper.main import cli

    cli(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
