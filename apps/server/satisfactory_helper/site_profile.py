"""Deterministic, site-scoped layout summaries for the planner.

Satisfactory stores placed actors and foundations, not a player's semantic "site".  This
module starts from a machine selector (usually a product), derives a local spatial anchor,
then inventories every machine and storage building inside one explicit circle.  Occupied
levels are mapped back to the recovered foundation bands, so callers receive both the
site-local order players use and the global band ordinal used by the map.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from satisfactory_mcp.domain.factories import floors as ffloors
from satisfactory_mcp.domain.factories import identity
from satisfactory_mcp.domain.factories.resolve import resolve_factory

ANCHOR_LINK_M = 100.0
DEFAULT_RADIUS_M = 120.0
MIN_RADIUS_M = 25.0
MAX_RADIUS_M = 500.0
LEVEL_SNAP_CM = 150.0
EDGE_SHARE = 0.9


def _leaf(instance: object) -> str:
    return str(instance or "").rsplit(".", 1)[-1]


def _metres(pos: tuple[float, float, float] | list[float]) -> tuple[float, float, float]:
    return (float(pos[0]) / 100.0, float(pos[1]) / 100.0, float(pos[2]) / 100.0)


def _bbox_centre(points_cm: list[tuple[float, float, float]]) -> tuple[float, float]:
    xs = [point[0] for point in points_cm]
    ys = [point[1] for point in points_cm]
    return ((min(xs) + max(xs)) / 200.0, (min(ys) + max(ys)) / 200.0)


def _anchor_candidates(st, machines: list[str]) -> list[dict[str, Any]]:
    positions = identity.positions(st.projection)
    clusters = identity.cluster_machines(machines, st.projection, link_m=ANCHOR_LINK_M)
    out: list[dict[str, Any]] = []
    for cluster in clusters:
        points = [positions[machine] for machine in cluster if machine in positions]
        if not points:
            continue
        centre = _bbox_centre(points)
        radius = max(
            math.hypot(point[0] / 100.0 - centre[0], point[1] / 100.0 - centre[1])
            for point in points
        )
        out.append(
            {
                "machines": len(points),
                "centre_m": [round(centre[0], 1), round(centre[1], 1)],
                "anchor_radius_m": round(radius, 1),
            }
        )
    out.sort(key=lambda row: (-int(row["machines"]), row["centre_m"]))
    return out


def _near_platform(platform: ffloors.Platform, x_cm: float, y_cm: float) -> bool:
    cx, cy = ffloors.cell_of(x_cm, y_cm)
    return any(
        (cx + dx, cy + dy) in platform.cell_set
        for dx in range(-ffloors.NEIGHBOUR_CELLS, ffloors.NEIGHBOUR_CELLS + 1)
        for dy in range(-ffloors.NEIGHBOUR_CELLS, ffloors.NEIGHBOUR_CELLS + 1)
    )


def _level_for(
    platforms: list[ffloors.Platform], pos_cm: tuple[float, float, float]
) -> tuple[ffloors.Platform, ffloors.Band, str] | None:
    """Match a building base to a local band without borrowing a distant lower deck.

    The upstream floor assignment deliberately finds the highest deck below an actor.  That
    is useful for route diagnostics, but a pivot over a deliberate hole can consequently be
    attributed to a storey far below it.  A site inventory instead requires the actor's base
    elevation to agree with the band.  Exact matches are distinguished from the wider 1.5 m
    tolerance so the evidence remains visible.
    """

    x_cm, y_cm, z_cm = pos_cm
    candidates: list[tuple[float, int, int, ffloors.Platform, ffloors.Band]] = []
    for platform in platforms:
        if not _near_platform(platform, x_cm, y_cm):
            continue
        for band in platform.bands:
            delta = abs(band.top_cm - z_cm)
            if delta <= LEVEL_SNAP_CM:
                candidates.append((delta, platform.index, band.ordinal, platform, band))
    if not candidates:
        return None
    delta, _platform_index, _ordinal, platform, band = min(candidates, key=lambda row: row[:3])
    mode = "foundation_exact" if delta <= ffloors.DECK_SLACK_CM else "elevation_matched"
    return platform, band, mode


def _machine_rows(st) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kind in ("machines", "extractors", "generators"):
        for row in st.projection.get(kind) or ():
            if not isinstance(row, dict) or not row.get("pos"):
                continue
            cls = str(row.get("cls") or "")
            recipe_id = str(row.get("recipe") or "")
            recipe = st.game.recipes.get(recipe_id)
            out.append(
                {
                    "instance": _leaf(row.get("instance")),
                    "kind": kind[:-1] if kind.endswith("s") else kind,
                    "building": st.game.building_name(cls) or cls or "Unknown",
                    "recipe": recipe.name if recipe is not None else None,
                    "pos_cm": tuple(float(value) for value in row["pos"][:3]),
                }
            )
    return out


def _storage_rows(st) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for holding in st.inventory.holdings():
        if holding.source != "storage" or holding.pos is None:
            continue
        items = [
            {
                "item": st.game.item_name(item) or item,
                "amount": int(amount) if float(amount).is_integer() else round(float(amount), 3),
            }
            for item, amount in holding.items
        ]
        out.append(
            {
                "instance": holding.instance,
                "kind": holding.kind,
                "building": st.game.building_name(holding.cls) or holding.cls or "Storage",
                "items": items,
                "total": (
                    int(holding.total)
                    if float(holding.total).is_integer()
                    else round(float(holding.total), 3)
                ),
                "pos_cm": holding.pos,
            }
        )
    return out


def _count_rows(counter: Counter) -> list[dict[str, Any]]:
    return [
        {"count": count, "name": name}
        for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _designation(machine_rows: list[dict], storage_rows: list[dict]) -> str:
    recipes = Counter(row["recipe"] for row in machine_rows if row.get("recipe"))
    if storage_rows and not machine_rows:
        return "storage base"
    if recipes:
        return " + ".join(name for name, _count in recipes.most_common(2))
    buildings = Counter(row["building"] for row in machine_rows)
    if buildings:
        return " + ".join(name for name, _count in buildings.most_common(2))
    return "occupied level"


def storage_level_assignments(st) -> dict[str, dict[str, Any] | None]:
    """Map every storage actor to the same recovered floor identity used by site profiles."""
    platforms, _index = ffloors._platforms(ffloors.foundation_tops(st.projection))
    out: dict[str, dict[str, Any] | None] = {}
    for row in _storage_rows(st):
        pos_cm = row["pos_cm"]
        level = _level_for(platforms, pos_cm)
        out[row["instance"]] = (
            {
                "platform": level[0].index,
                "global_floor": level[1].ordinal,
                "top_m": round(level[1].top_cm / 100.0, 1),
                "floor_assignment": level[2],
            }
            if level is not None
            else None
        )
    return out


def build_site_profile(
    st,
    *,
    focus: str,
    x_m: float | None = None,
    y_m: float | None = None,
    radius_m: float = DEFAULT_RADIUS_M,
) -> dict[str, Any]:
    """Return one explicit spatial inventory grounded in a pinned world state."""

    if (x_m is None) != (y_m is None):
        raise ValueError("x_m and y_m must be supplied together")
    if not MIN_RADIUS_M <= radius_m <= MAX_RADIUS_M:
        raise ValueError(f"radius_m must be between {MIN_RADIUS_M:g} and {MAX_RADIUS_M:g}")

    _name, selected = resolve_factory(st, focus)
    anchors = _anchor_candidates(st, selected)
    if x_m is None:
        if not anchors:
            raise ValueError(f"focus {focus!r} has no positioned machines")
        x_m, y_m = anchors[0]["centre_m"]
        basis = "largest spatial cluster selected by focus"
    else:
        x_m, y_m = float(x_m), float(y_m)
        basis = "explicit coordinates"

    tops = ffloors.foundation_tops(st.projection)
    platforms, _index = ffloors._platforms(tops)
    selected_set = set(selected)
    machine_rows: list[dict[str, Any]] = []
    infrastructure_rows: list[dict[str, Any]] = []
    storage_rows: list[dict[str, Any]] = []
    edge: list[dict[str, Any]] = []

    def include(row: dict[str, Any], destination: list[dict[str, Any]]) -> None:
        px_m, py_m, pz_m = _metres(row.pop("pos_cm"))
        distance = math.hypot(px_m - x_m, py_m - y_m)
        if distance > radius_m:
            return
        level = _level_for(platforms, (px_m * 100.0, py_m * 100.0, pz_m * 100.0))
        row.update(
            {
                "x_m": round(px_m, 1),
                "y_m": round(py_m, 1),
                "z_m": round(pz_m, 1),
                "distance_m": round(distance, 1),
                "anchor": row["instance"] in selected_set,
                "level": (
                    {
                        "platform": level[0].index,
                        "global_floor": level[1].ordinal,
                        "top_m": round(level[1].top_cm / 100.0, 1),
                        "minor": level[1].minor,
                        "assignment": level[2],
                    }
                    if level is not None
                    else None
                ),
            }
        )
        destination.append(row)
        if distance >= radius_m * EDGE_SHARE:
            edge.append(
                {
                    "instance": row["instance"],
                    "building": row["building"],
                    "distance_m": row["distance_m"],
                }
            )

    for row in _machine_rows(st):
        include(row, infrastructure_rows if row["kind"] == "extractor" else machine_rows)
    for row in _storage_rows(st):
        include(row, storage_rows)

    grouped_machines: dict[tuple[int, int, float] | None, list[dict]] = defaultdict(list)
    grouped_storage: dict[tuple[int, int, float] | None, list[dict]] = defaultdict(list)

    def key(row: dict) -> tuple[int, int, float] | None:
        level = row["level"]
        if level is None:
            return None
        return (level["platform"], level["global_floor"], level["top_m"])

    for row in machine_rows:
        grouped_machines[key(row)].append(row)
    for row in storage_rows:
        grouped_storage[key(row)].append(row)

    occupied_keys = sorted(
        (candidate for candidate in set(grouped_machines) | set(grouped_storage) if candidate),
        key=lambda candidate: (candidate[2], candidate[0], candidate[1]),
    )
    levels: list[dict[str, Any]] = []
    for site_level, level_key in enumerate(occupied_keys):
        machines = grouped_machines[level_key]
        storage = grouped_storage[level_key]
        buildings = Counter(row["building"] for row in machines)
        recipes = Counter(row["recipe"] for row in machines if row.get("recipe"))
        assignments = Counter(row["level"]["assignment"] for row in machines + storage)
        storage_items: Counter = Counter()
        for row in storage:
            for item in row["items"]:
                storage_items[item["item"]] += item["amount"]
        platform, global_floor, top_m = level_key
        levels.append(
            {
                "site_level": site_level,
                "platform": platform,
                "global_floor": global_floor,
                "top_m": top_m,
                "minor_foundation_band": bool((machines or storage)[0]["level"]["minor"]),
                "designation": _designation(machines, storage),
                "machine_count": len(machines),
                "storage_count": len(storage),
                "buildings": _count_rows(buildings),
                "recipes": _count_rows(recipes),
                "storage_items": _count_rows(storage_items),
                "assignment": dict(sorted(assignments.items())),
            }
        )

    unassigned = grouped_machines.get(None, []) + grouped_storage.get(None, [])
    points = machine_rows + infrastructure_rows + storage_rows
    extent = None
    if points:
        extent = {
            "min_x_m": min(row["x_m"] for row in points),
            "max_x_m": max(row["x_m"] for row in points),
            "min_y_m": min(row["y_m"] for row in points),
            "max_y_m": max(row["y_m"] for row in points),
        }
    ambiguous = False
    if basis != "explicit coordinates" and len(anchors) > 1:
        ambiguous = anchors[1]["machines"] >= anchors[0]["machines"] * 0.5

    return {
        "save": st.age_note,
        "focus": focus,
        "boundary": {
            "centre_m": [round(x_m, 1), round(y_m, 1)],
            "radius_m": round(radius_m, 1),
            "basis": basis,
            "extent_m": extent,
            "edge_placement_count": len(edge),
            "edge_placements": sorted(edge, key=lambda row: -row["distance_m"])[:12],
            "boundary_warning": (
                "placements touch the outer 10% of the circle; rerun with a larger radius "
                "before claiming complete site counts"
                if edge
                else None
            ),
        },
        "anchor": {
            "selected_machine_count": len(selected),
            "candidates": anchors[:8],
            "ambiguous": ambiguous,
            "ambiguity_rule": (
                "ask for or supply coordinates when the second anchor cluster is at least "
                "half the size of the first"
            ),
        },
        "counts": {
            "machines": len(machine_rows),
            "supporting_infrastructure": len(infrastructure_rows),
            "storage": len(storage_rows),
            "occupied_levels": len(levels),
            "unassigned_placements": len(unassigned),
        },
        "levels": levels,
        "supporting_infrastructure": [
            {
                "instance": row["instance"],
                "building": row["building"],
                "x_m": row["x_m"],
                "y_m": row["y_m"],
                "z_m": row["z_m"],
                "level": row["level"],
            }
            for row in sorted(
                infrastructure_rows,
                key=lambda row: (row["building"], row["instance"]),
            )
        ],
        "unassigned": [
            {
                "instance": row["instance"],
                "building": row["building"],
                "z_m": row["z_m"],
            }
            for row in sorted(unassigned, key=lambda row: (row["z_m"], row["building"]))[:25]
        ],
    }
