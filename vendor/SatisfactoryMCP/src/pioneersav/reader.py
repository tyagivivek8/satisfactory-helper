"""Primitive reads over a save file's byte stream: Unreal's little-endian fixed-width
integers and length-prefixed strings. This is the only module that touches ``struct``.

The string encoding is the one thing worth knowing before reading anything else. A string
is an int32 length followed by bytes, and **the sign of the length is the encoding**:
positive means ASCII/Latin-1 at one byte per character, negative means UTF-16LE at two.
Either way the count INCLUDES the trailing null, which is stripped.
"""

from __future__ import annotations

import struct

from .errors import ParseError

__all__ = ["Reader"]


class Reader:
    """A cursor over ``bytes``, reading Unreal's primitives in order."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def __len__(self) -> int:
        return len(self.data)

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def _take(self, count: int) -> bytes:
        end = self.pos + count
        if count < 0 or end > len(self.data):
            raise ParseError(f"read of {count} at {self.pos} runs past end ({len(self.data)})")
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk

    def bytes(self, count: int) -> bytes:
        return self._take(count)

    def skip(self, count: int) -> None:
        self._take(count)

    def i8(self) -> int:
        return self._take(1)[0]

    def i32(self) -> int:
        return struct.unpack_from("<i", self._take(4))[0]

    def u32(self) -> int:
        return struct.unpack_from("<I", self._take(4))[0]

    def i64(self) -> int:
        return struct.unpack_from("<q", self._take(8))[0]

    def u64(self) -> int:
        return struct.unpack_from("<Q", self._take(8))[0]

    def f32(self) -> float:
        return struct.unpack_from("<f", self._take(4))[0]

    def f64(self) -> float:
        return struct.unpack_from("<d", self._take(8))[0]

    def string(self) -> str:
        """Length-prefixed string; the sign of the length picks the encoding.

        Decoded with ``errors="replace"`` rather than strictly, so that one odd byte in a
        cosmetic field does not lose a 44,000-object save.
        """
        count = self.i32()
        if count == 0:
            return ""
        if count > 0:
            return self._take(count)[:-1].decode("latin-1", errors="replace")
        return self._take(-count * 2)[:-2].decode("utf-16-le", errors="replace")

    def vector3(self) -> tuple[float, float, float]:
        return (self.f64(), self.f64(), self.f64())

    def vector3_f32(self) -> tuple[float, float, float]:
        return (self.f32(), self.f32(), self.f32())
