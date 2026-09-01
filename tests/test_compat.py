from __future__ import annotations

import struct

from pioneersav.reader import Reader
from satisfactory_helper.compat import archive_header_length_from_body


def _synthetic_archive_body() -> bytes:
    branch = b"++FactoryGame+rel-main-1.2.0\x00"
    archive = (
        struct.pack("<iiii", 0, 522, 1017, 3)
        + struct.pack("<HHH", 5, 6, 1)
        + struct.pack("<I", 502094)
        + struct.pack("<i", len(branch))
        + branch
    )
    return struct.pack("<q", len(archive)) + archive


def _lengthen_first_branch(body: bytes, suffix: str) -> bytes:
    reader = Reader(body, 8)
    reader.bytes(16 + 6 + 4)
    branch_start = reader.pos
    old_branch = reader.string()
    branch_end = reader.pos
    replacement_text = (old_branch + suffix).encode("latin-1") + b"\x00"
    replacement = len(replacement_text).to_bytes(4, "little", signed=True) + replacement_text
    changed = body[:branch_start] + replacement + body[branch_end:]
    return (len(changed) - 8).to_bytes(8, "little", signed=True) + changed[8:]


def test_archive_header_length_is_measured_from_its_branch_string() -> None:
    body = _synthetic_archive_body()
    assert archive_header_length_from_body(body) == 59
    assert archive_header_length_from_body(_lengthen_first_branch(body, "anniversary")) == 70
