"""Recipe search results as compact TSV.

The units are not comparable and must not be rendered as if they were: only part
recipes run in a machine, so only they have a per-minute rate. Building and manual
rows carry the per-craft amount and are suffixed ``/build`` and ``/craft`` so no row
can be misread as a throughput. See ``core.gamedata.search`` for the census rules.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData, Recipe
from ...core.gamedata.search import KINDS, Census, Hit
from ...core.gamedata.unlocks import granted_by_label
from . import primitives as render

__all__ = ["render_search"]

#: How one unit of each kind is measured. A part recipe is a throughput; the other
#: two are one-off costs, and their duration field is a constant 1.0 s.
_UNIT = {"part": "/min", "building": "/build", "manual": "/craft"}

#: What performs each kind. Doubles as the "machine" column, so no separate kind
#: column is needed.
_PERFORMED_BY = {"building": "build gun", "manual": "by hand"}


def _machine(game: GameData, r: Recipe) -> str:
    if r.kind != "part":
        return _PERFORMED_BY.get(r.kind, "-")
    b = game.machine(r)
    return b.name if b else "-"


def _flows(game: GameData, r: Recipe, side: str) -> str:
    per_min = r.kind == "part"
    return render.flows(
        (game.item_name(f.item), f.per_min if per_min else f.amount, False)
        for f in (r.ingredients if side == "in" else r.products)
    )


def render_search(
    game: GameData,
    hits: list[Hit],
    census: Census,
    subject: str,
    item_column: str = "",
    limit: int = 10,
    offset: int = 0,
    kind: str = "part",
    notes: list[str] | None = None,
) -> str:
    page = hits[offset : offset + render.clamp(limit)]
    show_qty = bool(item_column)
    show_status = any(h.unlocked is not None for h in hits)
    # Only part recipes have a rate at all, so the /min suffix goes in the header
    # when every row is one, and onto each cell when the page mixes kinds.
    mixed = any(h.recipe.kind != "part" for h in page)
    # A LOCKED row without this is a dead end, and on a page with nothing locked the
    # column is a column of blanks -- so it appears exactly where it answers something.
    show_granted = any(h.unlocked is False for h in page)

    headers = ["recipe", "machine"]
    if show_qty:
        headers.append(item_column)
    headers += ["in", "out"] if mixed else ["in/min", "out/min"]
    if show_status:
        headers.append("status")
    if show_granted:
        headers.append("granted by")

    rows = []
    for h in page:
        r = h.recipe
        row = [r.name, _machine(game, r)]
        if show_qty:
            row.append(f"{render.num(h.qty)}{_UNIT.get(r.kind, '')}")
        row += [_flows(game, r, "in"), _flows(game, r, "out")]
        if show_status:
            row.append("HAVE" if h.unlocked else ("LOCKED" if h.unlocked is False else "-"))
        if show_granted:
            row.append(granted_by_label(game, r, width=60) if h.unlocked is False else "")
        rows.append(row)

    all_notes = list(notes or [])
    # The counter-example this exists for: a Tier 7-9 BUILDING that eats Rubber is
    # invisible to a part-only view, and silence there reads as "nothing else".
    hidden = [
        f"{census.by_kind[k]} {k}"
        for k in KINDS
        if k != kind and census.by_kind.get(k) and kind not in ("all", "", None)
    ]
    if hidden:
        all_notes.append(
            f"kind={kind!r} hides " + " and ".join(hidden) + f" recipe(s) that also {subject}"
            " -- pass kind='building', 'manual' or 'all' to see them"
        )
    if census.events and not any("FICSMAS" in n for n in all_notes):
        all_notes.append(f"{census.events} FICSMAS event recipe(s) counted but not shown")
    if mixed:
        all_notes.append(
            "/min is throughput for one machine at 100% clock; /build and /craft are "
            "one-off costs and are NOT rates"
        )

    body = render.table(
        headers,
        rows,
        total=len(hits),
        offset=offset,
        hint="or narrow the query.",
    )
    return render.envelope(
        census.line(subject),
        body + "\n" + render.ids_footer((h.recipe.name, h.recipe.cls) for h in page),
        all_notes,
    )
