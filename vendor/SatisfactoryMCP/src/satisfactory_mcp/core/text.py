"""The text helpers that are NOT presentation.

Everything else that shapes a response -- tables, envelopes, footers, truncation -- lives in
``presenters.text.primitives`` and is forbidden to domain code. These four are here because a
domain result owns sentences of its own (``DiffRow.note``, ``SaveIdentity.age_note``, a refusal
``core.saveio`` raises), and no layer that owns one may import a presenter.
"""

from __future__ import annotations

import time

__all__ = ["ago", "num", "played", "plural", "span", "stamp"]


def played(seconds: float | None) -> str:
    """A playtime as hours and whole minutes: ``121h04m``.

    Playtime, not wall clock, is the axis this project measures a world on, so it is
    spelled one way wherever it appears.
    """
    s = int(seconds or 0)
    return f"{s // 3600}h{s % 3600 // 60:02d}m"


def span(seconds: float | None) -> str:
    """A gap between two moments, unsigned, in one or two coarse units: ``40s``, ``18m``,
    ``2h05m``. For a gap of hours, the same shape ``played`` uses."""
    s = int(abs(seconds or 0))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return played(s)


def num(value: float | None, places: int = 2) -> str:
    """Compact number: no trailing zeros, no scientific notation."""
    if value is None:
        return "-"
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.{places}f}".rstrip("0").rstrip(".")


def stamp(mtime_ns: int | None) -> str | None:
    """A file write as local wall-clock time, to the minute, or ``None`` for no mtime. Local
    on purpose: "08:28" must be the same "08:28" the player's own save dialog shows."""
    if not mtime_ns:
        return None
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime_ns / 1e9))


def ago(mtime_ns: int | None, now_s: float | None = None) -> str | None:
    """How long ago a file was written, as one coarse human unit, or ``None`` for no mtime.

    Clamped at zero because an autosave can land between ``stat`` and ``now``, and
    "-1 min ago" reads as a bug rather than as a fresh file.
    """
    if not mtime_ns:
        return None
    secs = max(0.0, (time.time() if now_s is None else now_s) - mtime_ns / 1e9)
    if secs < 60:
        return "under a minute ago"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 48 * 3600:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)} days ago"


def plural(name: str, count: int) -> str:
    """Pluralise a building or item name."""
    if count == 1:
        return name
    if name.endswith("y") and name[-2:-1] not in "aeiou":
        return name[:-1] + "ies"
    return name + ("es" if name.endswith(("s", "x", "z", "ch", "sh")) else "s")
