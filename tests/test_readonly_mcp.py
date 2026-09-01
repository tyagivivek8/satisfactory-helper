from __future__ import annotations

from satisfactory_helper.readonly_mcp import (
    _factory_aspects,
    _factory_source_scope_error,
    _scenario,
    plan_production,
    production_detail,
)


def test_factory_inspection_normalizes_common_planner_aliases() -> None:
    assert _factory_aspects("summary,production,floors,belts,power") == (
        "summary,balance,inputs,outputs,links,power"
    )


def test_factory_plan_rejects_unrestricted_and_resource_only_sources() -> None:
    assert _factory_source_scope_error("slab:0", None)
    assert _factory_source_scope_error("slab:0", ["all"])
    assert _factory_source_scope_error(
        "slab:0", ["resource:Iron Ore", "resource:Coal"]
    )
    assert _factory_source_scope_error("slab:0", ["near:-1563,-404,650"]) is None
    assert _factory_source_scope_error("slab:0", ["node:BP_ResourceNode115"]) is None


def test_factory_planning_tools_fail_fast_before_reading_world_data() -> None:
    plan = plan_production("Motor", 10, factory="slab:0", sources=["all"])
    detail = production_detail(
        "Motor", 10, "buses", factory="slab:0", sources=["resource:Iron Ore"]
    )

    assert "LOCAL SOURCE SCOPE REQUIRED" in plan
    assert "Do not retry with sources=['all']" in plan
    assert "LOCAL SOURCE SCOPE REQUIRED" in detail


def test_scenario_exposes_existing_node_overclock_capacity_when_allowed() -> None:
    normal = _scenario("Motor", 10, ["node:BP_ResourceNode115"])
    overclocked = _scenario(
        "Motor", 10, ["node:BP_ResourceNode115"], max_extractor_clock=2.5
    )

    assert "extractor_clocks" not in normal
    assert overclocked["extractor_clocks"] == [1.0, 2.5]
