"""Where the factory already is: infrastructure points, altitudes and clusters.

Plain functions over the build records rather than methods on a facet: none of the
three carries state, and the record list is the census's to hand over.
"""

from __future__ import annotations

from ..spatial import geo

__all__ = ["consumer_z", "infra_points", "sites"]


def infra_points(records: list[dict]) -> list[tuple[float, float]]:
    """XY of every built production building, for distance-to-infrastructure."""
    return [(r["pos"][0], r["pos"][1]) for r in records if r.get("pos")]


def consumer_z(
    records: list[dict], building_ids: tuple[str, ...] = ("Build_OilRefinery_C",)
) -> float | None:
    """Mean altitude of a consumer class, for pipe head-lift sign.

    Defaults to refineries because that is what a fluid field usually feeds.
    """
    zs = [r["pos"][2] for r in records if r.get("pos") and r["cls"] in building_ids]
    return sum(zs) / len(zs) if zs else None


def sites(records: list[dict], link_m: float = 300.0) -> list[dict]:
    """Cluster built production buildings into named-by-content sites."""
    placed = [r for r in records if r.get("pos")]
    points = [
        {"x": r["pos"][0], "y": r["pos"][1], "z": r["pos"][2], "kind": r["cls"], "rec": r}
        for r in placed
    ]
    out = []
    for c in geo.cluster(points, link_m=link_m):
        counts: dict[str, int] = {}
        for m in c.members:
            counts[m["kind"]] = counts.get(m["kind"], 0) + 1
        cx, cy, cz = c.centroid
        out.append(
            {
                "centroid": (round(cx), round(cy), round(cz)),
                "grid": geo.grid_cell(cx, cy),
                "direction": geo.direction_of(cx, cy),
                "buildings": counts,
                "count": c.size,
                "diameter_m": round(c.diameter_m),
            }
        )
    out.sort(key=lambda s: -s["count"])
    return out
