"""``/api/collectibles``: slugs, mercer spheres and the rest, filtered as the tool filters.

Every refusal this endpoint makes is ``collect_view``'s, so that the map and the MCP tool
cannot hold two opinions about one question.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from fastapi import APIRouter, Request

from ....domain.collectibles.service import collect_view
from ..serial import _fail, _state, _xyz

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- collectibles


class CollectibleRow(TypedDict):
    """One map placement, and what this save says about it.

    The three coordinates are not nullable: they come off the generated placement table,
    where a row without all three does not exist.

    ``observed`` is the placement table's scan of every save on disk rather than of the
    loaded one, and it is null both for a row this save has collected and for a state this
    build does not know. ``distance_m`` is populated only by ``mode=nearest``, the one mode
    that resolves an origin; elsewhere it is null rather than zero.
    """

    category: str
    name: str
    x_m: float
    y_m: float
    z_m: float
    collected: bool
    observed: str | None
    distance_m: float | None


class CollectiblesResponse(TypedDict):
    """The view ``collect_view`` decided.

    ``mode`` is a closed union because ``collect_view`` refuses anything outside these four
    before a view exists at all, so a fifth mode cannot reach the wire without the domain
    service changing -- and then it should be loud here.

    ``rows`` is a list and never null: the view answers ``None`` for ``mode=census``, which
    counts instead of listing, and the handler sends the empty list. ``counts`` is an open
    map because its keys are observed states and a save whose rows are all in one of them
    sends a one-key object -- a tally, not a schema. ``where`` is ``""`` for every mode that
    measures no distance.
    """

    mode: Literal["census", "collected", "remaining", "nearest"]
    group: str | None
    rows: list[CollectibleRow]
    counts: dict[str, int]
    hidden_pedestals: int
    save_only: bool
    where: str


@router.get("/collectibles", response_model=CollectiblesResponse)
def collectibles(
    request: Request,
    group: str | None = None,
    mode: str = "remaining",
    near: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Map placements, filtered exactly the way the MCP tool filters them.

    ``collect_view`` owns every refusal -- unknown mode, retired group, and the one that
    matters here: ``mode=remaining`` needs the generated placement table, and without it the
    honest answer is that refusal rather than a shorter list.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    view = collect_view(st, group, mode, near)
    if view.error:
        return _fail(view.error)

    rows = [
        {
            "category": r["category"],
            "name": r["name"],
            **_xyz(r["pos"]),
            "collected": r["collected"],
            "observed": r["observed"],
            "distance_m": round(r["distance_m"], 1) if r.get("distance_m") is not None else None,
        }
        for r in (view.rows or ())
    ]
    return {
        "mode": view.mode,
        "group": view.group,
        "rows": rows,
        "counts": view.counts,
        "hidden_pedestals": view.hidden,
        "save_only": view.save_only,
        "where": view.where,
    }
