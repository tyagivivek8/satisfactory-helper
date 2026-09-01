from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from satisfactory_mcp.core.saveio import extract as upstream

from .compat import patch_archive_header_guard


def _install_landmark_capture() -> None:
    """Keep visual landmarks without filing them as production machines.

    Upstream intentionally drops the Space Elevator from its factory layers because it does
    not run a recipe. The 3D map still needs its saved transform, so capture that one actor
    into a separate projection layer while leaving factory clustering and balance untouched.
    """
    if getattr(upstream.extract, "_satisfactory_helper_landmarks", False):
        return
    original_extract = upstream.extract

    def extract_with_landmarks(path: str | Path) -> dict[str, Any]:
        landmarks: list[dict[str, Any]] = []
        original_iter_objects = upstream.iter_objects

        def iter_objects_with_landmarks(save: Any):
            for type_path, header, obj in original_iter_objects(save):
                cls = upstream.cls_of(type_path)
                if cls == "Build_SpaceElevator_C":
                    landmarks.append(
                        {
                            "cls": cls,
                            "instance": str(
                                getattr(header, "instanceName", None)
                                or getattr(obj, "instanceName", "")
                            ),
                            "pos": upstream.pos_of(header),
                            "yaw": upstream.yaw_of(getattr(header, "rotation", None)),
                        }
                    )
                yield type_path, header, obj

        upstream.iter_objects = iter_objects_with_landmarks
        try:
            payload = original_extract(path)
        finally:
            upstream.iter_objects = original_iter_objects
        payload["landmarks"] = landmarks
        return payload

    upstream.extract = extract_with_landmarks
    upstream.extract._satisfactory_helper_landmarks = True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] != "--list" and "--header-only" not in args:
        patch_archive_header_guard(Path(args[0]))
        _install_landmark_capture()
    return upstream.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
