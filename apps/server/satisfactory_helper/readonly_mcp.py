"""Curated, non-persistent MCP surface for the embedded Codex planner.

The upstream project exposes a broad expert toolbox, including a few label/plan persistence
operations. This surface composes the read-only calls Satisfactory Helper needs and does not
register any mutation-capable tool. The smaller schemas also keep each subscription request
focused on factory planning instead of shipping dozens of unrelated tool descriptions.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP
from satisfactory_mcp.interfaces.mcp.app import _state
from satisfactory_mcp.interfaces.mcp.tools.factories import (
    factory_health,
    factory_map,
    factory_query,
    list_factories,
)
from satisfactory_mcp.interfaces.mcp.tools.floors import factory_floors
from satisfactory_mcp.interfaces.mcp.tools.gamedata import alternates_for_item, recipe_detail
from satisfactory_mcp.interfaces.mcp.tools.harddrives import (
    advise_hard_drive_pick,
    list_pending_hard_drive_choices,
)
from satisfactory_mcp.interfaces.mcp.tools.planning import (
    diff_vs_save,
    plan_factory,
    plan_layout,
    rank_unlocks,
)
from satisfactory_mcp.interfaces.mcp.tools.progression import phase_requirements
from satisfactory_mcp.interfaces.mcp.tools.spatial import rank_build_sites, search_resource_nodes
from satisfactory_mcp.interfaces.mcp.tools.world import power_report, world_summary

from .site_profile import DEFAULT_RADIUS_M, build_site_profile

mcp = FastMCP("satisfactory-readonly")

_FACTORY_ASPECT_ALIASES = {
    "production": ("balance", "inputs", "outputs"),
    "belts": ("links",),
    "floors": (),  # inspect_factory always appends factory_floors below.
}
_FACTORY_ASPECTS = {
    "summary",
    "machines",
    "recipes",
    "buildings",
    "balance",
    "inputs",
    "outputs",
    "internal",
    "power",
    "nodes",
    "links",
    "issues",
}


def _factory_aspects(aspects: str) -> str:
    requested = [part.strip().lower() for part in aspects.split(",") if part.strip()]
    normalized: list[str] = []
    for aspect in requested:
        candidates = _FACTORY_ASPECT_ALIASES.get(aspect, (aspect,))
        for candidate in candidates:
            if candidate in _FACTORY_ASPECTS and candidate not in normalized:
                normalized.append(candidate)
    if not normalized:
        normalized = ["summary", "balance", "inputs", "outputs", "nodes", "links", "power"]
    return ",".join(normalized)


def _factory_source_scope_error(factory: str | None, sources: list[str] | None) -> str | None:
    if not factory:
        return None
    localized = ("near:", "node:", "bbox:", "grid:", "region:")
    if sources and any(source.strip().lower().startswith(localized) for source in sources):
        return None
    return (
        "# LOCAL SOURCE SCOPE REQUIRED\n"
        "This is a factory-scoped expansion, so unrestricted whole-map sources are disabled. "
        "Do not retry with sources=['all'] or resource-only selectors. First inspect the "
        "factory balance, inputs, nodes and links; then plan with a near:x,y,radius scope or "
        "specific node:<id> selectors. Start with the existing site and widen once only when "
        "the local scope is proven infeasible."
    )


@mcp.tool(structured_output=False)
def current_world(as_of: str | None = None) -> str:
    """Current save identity, progress, unlock count, power, and parser integrity notes."""
    return world_summary(as_of=as_of)


@mcp.tool(structured_output=False)
def discover_factories(as_of: str | None = None) -> str:
    """Find existing production sites and recovered foundation platforms/floors."""
    return "\n\n".join(
        (
            list_factories(as_of=as_of),
            factory_map(as_of=as_of, limit=20, show="all"),
            factory_floors(as_of=as_of, limit=20),
        )
    )


@mcp.tool(structured_output=False)
def inspect_factory(
    factory: str,
    aspects: str = "summary,balance,inputs,outputs,nodes,links,power,machines,issues",
    as_of: str | None = None,
) -> str:
    """Inspect one named/proposed factory, including machines, health, and floor membership."""
    normalized_aspects = _factory_aspects(aspects)
    return "\n\n".join(
        (
            factory_query(factory=factory, of=normalized_aspects, as_of=as_of, limit=25),
            factory_health(factory=factory, as_of=as_of, limit=25),
            factory_floors(factory=factory, as_of=as_of, limit=25),
        )
    )


@mcp.tool(structured_output=False)
def inspect_site(
    focus: str,
    x_m: float | None = None,
    y_m: float | None = None,
    radius_m: float = DEFAULT_RADIUS_M,
    as_of: str | None = None,
) -> str:
    """Inventory one physical site by occupied level, including machines and storage.

    ``focus`` accepts the same label or machine selector as ``inspect_factory``. For a
    product made at several places, either inspect the returned anchor candidates and ask
    which one the player means, or rerun with both ``x_m`` and ``y_m``. ``radius_m`` is an
    explicit evidence boundary: if the result reports edge placements, enlarge it before
    claiming complete counts. Each occupied level includes a site-local order, the global
    platform floor ordinal used by the map, saved recipes, and storage contents.
    """
    try:
        st = _state(None, None, as_of)
        profile = build_site_profile(
            st,
            focus=focus,
            x_m=x_m,
            y_m=y_m,
            radius_m=radius_m,
        )
    except Exception as exc:
        return f"could not inspect site: {exc}"
    return json.dumps(profile, indent=2, ensure_ascii=False)


def _scenario(
    target_item: str,
    rate_per_min: float,
    sources: list[str] | None,
    max_extractor_clock: float = 1.0,
) -> dict:
    scenario = {
        "objective": "min_machines",
        "target_item": target_item,
        "sources": sources,
        "exports": [target_item],
        "export_minimums": {target_item: rate_per_min},
    }
    if max_extractor_clock > 1.0:
        scenario["extractor_clocks"] = [1.0, max_extractor_clock]
    return scenario


def _extractor_clock_error(max_extractor_clock: float) -> str | None:
    if 1.0 <= max_extractor_clock <= 2.5:
        return None
    return "max_extractor_clock must be between 1.0 and 2.5"


@mcp.tool(structured_output=False)
def plan_production(
    target_item: str,
    rate_per_min: float,
    factory: str | None = None,
    sources: list[str] | None = None,
    max_extractor_clock: float = 1.0,
    as_of: str | None = None,
) -> str:
    """Solve an unlocked-tech production target, lay it out by floor, and diff machines.

    ``max_extractor_clock`` may be raised to at most 2.5 only when the player permits using
    Power Shards. This lets the solver evaluate added capacity on existing nodes before it
    recommends another extraction site.
    """
    scope_error = _factory_source_scope_error(factory, sources)
    if scope_error:
        return scope_error
    clock_error = _extractor_clock_error(max_extractor_clock)
    if clock_error:
        return clock_error
    scenario = _scenario(target_item, rate_per_min, sources, max_extractor_clock)
    return "\n\n".join(
        (
            "## SOLVED PRODUCTION\n"
            + plan_factory(**scenario, as_of=as_of, logistics_items=[target_item], limit=25),
            "## FLOOR LAYOUT\n"
            + plan_layout(**scenario, as_of=as_of, factory=factory, detail="floors", limit=30),
            "## CHANGES VS CURRENT FACTORY\n"
            + diff_vs_save(**scenario, as_of=as_of, factory=factory, limit=30),
        )
    )


@mcp.tool(structured_output=False)
def production_detail(
    target_item: str,
    rate_per_min: float,
    detail: str,
    factory: str | None = None,
    sources: list[str] | None = None,
    max_extractor_clock: float = 1.0,
    as_of: str | None = None,
) -> str:
    """Show one extra solved-plan view: blocks, buses, trunks, materials, or floors."""
    if detail not in {"blocks", "buses", "trunks", "materials", "floors"}:
        return "detail must be blocks, buses, trunks, materials, or floors"
    scope_error = _factory_source_scope_error(factory, sources)
    if scope_error:
        return scope_error
    clock_error = _extractor_clock_error(max_extractor_clock)
    if clock_error:
        return clock_error
    return plan_layout(
        **_scenario(target_item, rate_per_min, sources, max_extractor_clock),
        as_of=as_of,
        factory=factory,
        detail=detail,
        limit=30,
    )


@mcp.tool(structured_output=False)
def useful_unlocks(
    target_item: str,
    rate_per_min: float,
    sources: list[str] | None = None,
    search: str | None = None,
    max_extractor_clock: float = 1.0,
    as_of: str | None = None,
) -> str:
    """Rank currently locked alternate recipes by measured value to this exact target."""
    clock_error = _extractor_clock_error(max_extractor_clock)
    if clock_error:
        return clock_error
    return rank_unlocks(
        **_scenario(target_item, rate_per_min, sources, max_extractor_clock),
        search=search,
        as_of=as_of,
        limit=15,
    )


@mcp.tool(structured_output=False)
def pending_hard_drives(as_of: str | None = None) -> str:
    """List the unclaimed hard-drive choices currently stored in this save."""
    return list_pending_hard_drive_choices(as_of=as_of)


@mcp.tool(structured_output=False)
def choose_hard_drive(hard_drive_id: int, as_of: str | None = None) -> str:
    """Compare one pending drive's live choices by counterfactual production value."""
    return advise_hard_drive_pick(hard_drive_id=hard_drive_id, as_of=as_of)


@mcp.tool(structured_output=False)
def recipe_options(item: str) -> str:
    """Compare normal and alternate recipes for an item using current game data."""
    options = alternates_for_item(item)
    return options


@mcp.tool(structured_output=False)
def recipe_inputs(recipe: str) -> str:
    """Get exact inputs, outputs, rate, machine, and unlock source for one recipe."""
    return recipe_detail(recipe)


@mcp.tool(structured_output=False)
def find_resource_site(
    resource: str,
    near: str | None = None,
    only_free: bool = False,
    as_of: str | None = None,
) -> str:
    """Find nearby existing and free resource nodes before considering a new remote site.

    ``near`` accepts x,y, me, or a factory selector. When ``only_free`` is true, the result
    still shows the combined nearby list first so an existing extractor is not accidentally
    ignored merely because the caller started by looking for new nodes.
    """
    nearby = search_resource_nodes(
        resource=resource,
        only_free=False,
        mode="nearest" if near else "fields",
        near=near,
        as_of=as_of,
        limit=15,
    )
    if near:
        sections = ["## NEARBY EXISTING AND FREE NODES\n" + nearby]
        if only_free:
            free = search_resource_nodes(
                resource=resource,
                only_free=True,
                mode="nearest",
                near=near,
                as_of=as_of,
                limit=15,
            )
            sections.append("## UNTAPPED NODES ONLY\n" + free)
        return "\n\n".join(sections)

    ranked = rank_build_sites(resource=resource, as_of=as_of, limit=8)
    return nearby + "\n\n" + ranked


@mcp.tool(structured_output=False)
def progression_and_power(as_of: str | None = None) -> str:
    """Current phase requirements and measured/nameplate power headroom."""
    st = _state(None, None, as_of)
    rail_recipes = {
        "Recipe_Locomotive_C",
        "Recipe_RailroadTrack_C",
        "Recipe_TrainStation_C",
    }
    rail_unlocked = rail_recipes.issubset(st.available_recipe_ids)
    logistics = (
        f"rail_logistics={'unlocked' if rail_unlocked else 'locked'} "
        f"local_raw_route_limit_m={2000 if rail_unlocked else 600}"
    )
    return (
        phase_requirements(as_of=as_of)
        + "\n\n"
        + logistics
        + "\n\n"
        + power_report(as_of=as_of)
    )
