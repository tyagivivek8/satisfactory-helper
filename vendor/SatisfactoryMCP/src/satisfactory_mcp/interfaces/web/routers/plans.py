"""``/api/plans``: where each stored plan is to STAND.

The siting, and nothing else about the plan. A plan's contents are a solve -- ``PlanStore``
holds the request and re-solves on recall -- and this endpoint answers the one question the
map can draw: the rectangle the pad occupies on the ground.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.planning import siting as planning_siting
from ..serial import _fail, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------------ plans


class PlanSiting(TypedDict):
    """One stored plan's pad: centre, facing and extent, all in metres on save axes.

    NOT centimetres, and this is the one payload on this surface where that is not a bug.
    ``Siting`` records metres because a player typed them, so ``serial._m`` has nothing to
    do here -- see ``domain/planning/siting.py``.

    ``z_m`` is null wherever the origin was named by something with no height (a factory
    centroid, a bare ``x,y``); the pad is still a rectangle on the ground. ``source`` is
    ``"given"`` for a footprint the player measured and ``"layout"`` for the square
    ``plan_layout`` budgeted, which is the difference between a pad and an estimate.
    """

    name: str
    x_m: float
    y_m: float
    z_m: float | None
    yaw_deg: float
    width_m: float
    depth_m: float
    source: str
    origin_label: str
    factory: str


class PlansResponse(TypedDict):
    """What ``/api/plans`` sends on a 200. An error is a 4xx with ``{"error": ...}``."""

    plans: list[PlanSiting]
    stored: int


@router.get("/plans", response_model=PlansResponse)
def plans(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every stored plan that has been sited, as the rectangle it claims.

    A world with plans and no sitings answers ``{"plans": [], "stored": 3}``, which is why
    ``stored`` is here: an empty layer over three stored plans means "none of them has been
    sited yet", and an empty layer over no plans at all means the feature is unused.

    A siting with no footprint is NOT sent. ``site_plan`` always records one -- given or
    derived from the layout -- so a footprintless record is a hand-edited file, and an
    origin alone bounds nothing this layer could draw.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    rows = []
    for plan in st.plans.plans:
        sit = planning_siting.parse(plan)
        if sit is None or not sit.has_footprint:
            continue
        rows.append(
            {
                "name": plan.name,
                "x_m": sit.x_m,
                "y_m": sit.y_m,
                "z_m": sit.z_m,
                "yaw_deg": sit.yaw_deg,
                "width_m": sit.width_m,
                "depth_m": sit.depth_m,
                "source": sit.source,
                "origin_label": sit.origin_label,
                "factory": plan.factory,
            }
        )
    return {"plans": rows, "stored": len(st.plans.plans)}
