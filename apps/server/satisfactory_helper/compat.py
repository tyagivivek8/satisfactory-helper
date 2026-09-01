"""Narrow compatibility shims for verified game/parser drift.

Satisfactory build 502094 lengthened the Unreal branch string stored in every
saveVersion-60 archive header. SatisfactoryMCP revision ade73e6 reads that string correctly,
but intentionally rejects any header whose total is not the old 59 bytes. We measure the
fully length-prefixed header at the front of the snapshot and set the parser's guard to that
measured value before parsing. All later archive headers are still walked field-by-field and
all surrounding declared lengths remain enforced.
"""

from __future__ import annotations

from pathlib import Path

from pioneersav.chunks import decompress_body
from pioneersav.header import read_info_bytes
from pioneersav.reader import Reader


def archive_header_length_from_body(body: bytes) -> int:
    if len(body) < 40:
        raise ValueError("inflated save body is too short to hold an archive header")
    reader = Reader(body, 8)  # first int64 is the body's self-declared size
    start = reader.pos
    fields = (reader.i32(), reader.i32(), reader.i32(), reader.i32())
    if fields[:3] != (0, 522, 1017):
        raise ValueError(f"save body has no version-60 archive header at offset 8: {fields[:3]}")
    reader.bytes(6)  # engine major/minor/patch
    reader.u32()  # changelist
    branch = reader.string()
    measured = reader.pos - start
    if not branch.startswith("++FactoryGame+") or not 40 <= measured <= 512:
        raise ValueError(
            f"archive header measurement is implausible: {measured} bytes, branch={branch!r}"
        )
    return measured


def archive_header_length(path: Path) -> int:
    data = path.read_bytes()
    info = read_info_bytes(data)
    body = decompress_body(data, info.body_offset, old=info.save_version < 52)
    return archive_header_length_from_body(body)


def patch_archive_header_guard(path: Path) -> int:
    measured = archive_header_length(path)
    import pioneersav.objects as objects

    objects.ARCHIVE_HEADER_LEN = measured
    return measured


def install_extractor_wrapper() -> None:
    import satisfactory_mcp.config as engine_config

    engine_config.EXTRACTOR_MODULE = "satisfactory_helper.extractor_wrapper"
