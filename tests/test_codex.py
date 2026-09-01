from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from satisfactory_helper.codex import CodexRunner
from satisfactory_helper.config import PROJECT_ROOT
from satisfactory_helper.models import ChatRequest
from satisfactory_helper.prompts import build_prompt
from satisfactory_helper.providers import (
    SUPPORTED_CODEX_CLI_VERSION,
    codex_version_is_supported,
)


def test_codex_cli_pin_matches_packaging_and_accepts_only_the_supported_version() -> None:
    bundled_version = (PROJECT_ROOT / "packaging" / "codex-version.txt").read_text(
        encoding="utf-8"
    ).strip()

    assert bundled_version == SUPPORTED_CODEX_CLI_VERSION
    assert codex_version_is_supported(f"codex-cli {SUPPORTED_CODEX_CLI_VERSION}")
    assert codex_version_is_supported(SUPPORTED_CODEX_CLI_VERSION)
    assert not codex_version_is_supported("codex-cli 0.152.0")


@pytest.mark.asyncio
async def test_provider_subprocess_accepts_a_large_ndjson_event(tmp_path: Path) -> None:
    process = await CodexRunner._create_process(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x' * 70000 + '\\n')",
        ],
        dict(os.environ),
        tmp_path,
    )
    assert process.stdout is not None

    line = await process.stdout.readline()

    assert len(line) >= 70_001
    assert line.endswith(b"\n")
    assert await process.wait() == 0


def test_provider_failure_prefers_the_structured_error_over_stderr_noise() -> None:
    message = CodexRunner._failure_message(
        "Claude",
        1,
        "You've hit your session limit - resets 11pm",
        "strict mode: use allowUnionTypes",
    )

    assert message == "You've hit your session limit - resets 11pm"


def test_codex_command_is_ephemeral_read_only_and_uses_the_wrapper(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        codex_executable="codex",
        codex_model=None,
        snapshot_view_root=PROJECT_ROOT / ".local-data" / "snapshots" / "view",
        docs_path=tmp_path / "en-US.json",
        engine_data_root=PROJECT_ROOT / ".local-data" / "engine" / "data",
    )
    runtime = SimpleNamespace(settings=settings)
    runner = CodexRunner(runtime)  # type: ignore[arg-type]
    command = runner.command(tmp_path / "answer.json", tmp_path)

    assert command[:2] == ["codex", "exec"]
    assert "--ephemeral" in command
    assert "--approve-for-me" in command
    assert "--sandbox" not in command
    assert "--ignore-user-config" in command
    joined = " ".join(command)
    assert "satisfactory-helper-mcp" in joined
    assert str(PROJECT_ROOT) in joined
    assert "SaveGames" not in joined
    assert settings.snapshot_view_root.as_posix() in joined
    assert settings.engine_data_root.as_posix() in joined


def test_provider_command_can_use_an_immutable_request_snapshot(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        codex_executable="codex",
        codex_model=None,
        snapshot_view_root=PROJECT_ROOT / ".local-data" / "snapshots" / "view",
        docs_path=tmp_path / "en-US.json",
        engine_data_root=PROJECT_ROOT / ".local-data" / "engine" / "data",
    )
    runner = CodexRunner(SimpleNamespace(settings=settings))  # type: ignore[arg-type]
    pinned = PROJECT_ROOT / ".local-data" / "snapshots" / "pinned" / ("a" * 64)

    command = runner.command(
        tmp_path / "answer.json", tmp_path, snapshot_root=pinned
    )

    joined = " ".join(command)
    assert pinned.as_posix() in joined
    assert settings.snapshot_view_root.as_posix() not in joined


def test_claude_command_is_restricted_nonpersistent_and_uses_selected_model(
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        codex_executable="codex",
        codex_model=None,
        claude_executable="claude",
        claude_model=None,
        snapshot_view_root=PROJECT_ROOT / ".local-data" / "snapshots" / "view",
        docs_path=tmp_path / "en-US.json",
        engine_data_root=PROJECT_ROOT / ".local-data" / "engine" / "data",
    )
    runner = CodexRunner(SimpleNamespace(settings=settings))  # type: ignore[arg-type]

    command = runner.command(
        tmp_path / "answer.json",
        tmp_path,
        provider="claude",
        model="sonnet",
    )

    assert command[0] == "claude"
    assert "--restricted" in command
    assert "--strict-mcp-config" in command
    assert "--no-session-persistence" in command
    assert command[command.index("--allowedTools") + 1] == "mcp__satisfactory__*"
    assert command[command.index("--model") + 1] == "sonnet"
    joined = " ".join(command)
    assert "satisfactory-helper-mcp" in joined
    assert "SaveGames" not in joined
    mcp_config = json.loads(command[command.index("--mcp-config") + 1])
    assert str(settings.snapshot_view_root) in mcp_config["mcpServers"]["satisfactory"]["args"]
    claude_schema = json.loads(command[command.index("--json-schema") + 1])
    metric_value = claude_schema["properties"]["metrics"]["items"]["properties"]["value"]
    assert metric_value == {"anyOf": [{"type": "number"}, {"type": "string"}]}


def test_chat_request_rejects_models_from_the_wrong_provider() -> None:
    assert ChatRequest(message="yo", provider="claude", model="sonnet").model == "sonnet"

    with pytest.raises(ValueError, match="Unsupported claude model"):
        ChatRequest(message="yo", provider="claude", model="gpt-5.6-luna")


def test_progress_events_do_not_forward_tool_arguments_or_outputs() -> None:
    event = {
        "type": "item.started",
        "item": {
            "type": "mcp_tool_call",
            "tool": "factory_floors",
            "arguments": {"private": "not for browser"},
            "result": "large private output",
        },
    }
    progress = CodexRunner._progress_event(event)
    assert progress == {
        "type": "tool",
        "stage": "running",
        "message": "Checking factory floors…",
    }
    assert "private" not in str(progress)


def test_only_successfully_completed_mcp_calls_count_as_world_evidence() -> None:
    success = {
        "type": "item.completed",
        "item": {"type": "mcp_tool_call", "tool": "discover_factories"},
    }
    failed = {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "tool": "discover_factories",
            "error": "server unavailable",
        },
    }
    shell = {
        "type": "item.completed",
        "item": {"type": "tool_call", "tool": "exec_command"},
    }

    assert CodexRunner._completed_mcp_tool(success) == "discover_factories"
    assert CodexRunner._completed_mcp_tool(failed) is None
    assert CodexRunner._completed_mcp_tool(shell) is None


def test_site_claims_require_the_unified_site_profile() -> None:
    assert CodexRunner._requires_site_profile(
        {"target": {"site": "steel tower", "floor": None}, "floors": [], "actions": []}
    )
    assert CodexRunner._requires_site_profile(
        {"target": {"site": None, "floor": None}, "floors": [{"floor": 3}], "actions": []}
    )
    assert not CodexRunner._requires_site_profile(
        {"target": {"site": None, "floor": None}, "floors": [], "actions": []}
    )


def test_truthful_blocked_answer_does_not_require_an_unavailable_site_profile() -> None:
    answer = {
        "overall_status": "blocked",
        "target": {"site": "selected steel factory", "floor": None},
        "floors": [],
        "actions": [{"kind": "manual_check", "site": "selected steel factory"}],
    }

    assert not CodexRunner._requires_site_profile(answer)


def test_prompt_makes_site_profile_authoritative_for_layout_claims() -> None:
    prompt = build_prompt(
        ChatRequest(message="spot my steel factory"),
        save_token="sav:test",
        snapshot_name="world.sav",
    )

    assert "call inspect_site" in prompt
    assert "Never substitute platform-wide factory_floors counts" in prompt
    assert "include storage-only levels" in prompt


def test_prompt_guards_against_recent_planning_errors() -> None:
    prompt = build_prompt(
        ChatRequest(message="make 10 motors per minute at my steel site"),
        save_token="sav:test",
        snapshot_name="world.sav",
    )

    assert "keep physically separated platforms named separately" in prompt
    assert "Never say the grid is currently overloaded" in prompt
    assert "calculate elevation only from explicit z_m values" in prompt
    assert "resource:Iron Ore" in prompt
    assert "recompute every affected flow" in prompt


def test_prompt_prioritizes_existing_and_local_supply_over_pure_remote_nodes() -> None:
    prompt = build_prompt(
        ChatRequest(message="add motors without disturbing my existing steel outputs"),
        save_token="sav:test",
        snapshot_name="world.sav",
    )

    assert "rewire or change recipes/clocks inside the target factory" in prompt
    assert "only_free=false before searching only_free=true" in prompt
    assert "Purity and one fewer miner do not outweigh" in prompt
    assert 'Never use sources=["all"]' in prompt
    assert "total straight-line transport before minimizing machine count" in prompt
    assert "why each closer existing or free option was rejected" in prompt
    assert "saved extractor at 100% is not fully overclocked" in prompt
    assert "new_total_clock = saved_clock" in prompt
    assert "Do not ask them to choose a mode" in prompt
    assert "hard maximum for a new raw-resource route" in prompt
    assert "reduce or stop sensible whole machine groups" in prompt
    assert "player permits practical overclocking up to 250%" in prompt
    assert "newly unlocked recipe" in prompt
    assert 'capacity_basis must always be "nameplate"' in prompt
    assert "backed up by full storage" in prompt


def test_prompt_keeps_factory_production_isolated_by_default() -> None:
    prompt = build_prompt(
        ChatRequest(message="make motors at my steel factory"),
        save_token="sav:test",
        snapshot_name="world.sav",
    )

    assert "Treat every physical production site" in prompt
    assert "never use a processed output from one factory" in prompt
    assert "Existing production in" in prompt
    assert "not a candidate source" in prompt
    assert "raw resources may be routed from UNTAPPED nodes" in prompt
    assert "sole destination is storage or an AWESOME Sink" in prompt
    assert "must copy the player's exact words" in prompt
    assert "tapped Miner, Extractor" in prompt
    assert "factory_strategy=\"new_factory\"" in prompt


def _reroute_action(
    *,
    item: str,
    source: str,
    destination: str,
    purpose: str,
    quote: str | None = None,
    distance_m: float | None = None,
    transport_mode: str = "none",
) -> dict[str, object]:
    return {
        "id": "route-material",
        "kind": "reroute",
        "transfer_item": item,
        "source_site": source,
        "destination_site": destination,
        "transfer_purpose": purpose,
        "authorization_quote": quote,
        "source_distance_m": distance_m,
        "transport_mode": transport_mode,
    }


def test_transfer_guard_rejects_unrequested_cross_factory_production_input() -> None:
    answer = {
        "actions": [
            _reroute_action(
                item="Rotor",
                source="Iron factory",
                destination="Steel factory",
                purpose="production_input",
            )
        ]
    }

    error = CodexRunner._material_transfer_policy_error(
        answer, ChatRequest(message="make motors at my steel factory")
    )

    assert error is not None
    assert "exact player quote" in error


def test_transfer_guard_allows_a_specific_player_requested_transfer() -> None:
    request_text = "belt rotors from iron factory to steel factory"
    answer = {
        "actions": [
            _reroute_action(
                item="Rotor",
                source="Iron factory",
                destination="Steel factory",
                purpose="production_input",
                quote=request_text,
            )
        ]
    }

    assert (
        CodexRunner._material_transfer_policy_error(
            answer, ChatRequest(message=request_text)
        )
        is None
    )


def test_transfer_guard_does_not_reclassify_a_dedicated_floor_as_a_new_factory() -> None:
    answer = {
        "factory_strategy": "same_factory",
        "headline": "Add a dedicated 400/min Quickwire floor",
        "summary": "A dedicated production deck inside the steel factory.",
        "target": {
            "site": "Steel factory floor 6",
            "factory": "Steel factory",
        },
        "raw_inputs": [],
        "actions": [],
    }

    assert (
        CodexRunner._material_transfer_policy_error(
            answer, ChatRequest(message="add a caterium floor to my steel factory")
        )
        is None
    )


def test_transfer_guard_allows_raw_inputs_and_storage_routes() -> None:
    answer = {
        "actions": [
            _reroute_action(
                item="Iron Ore",
                source="Iron node 168",
                destination="Steel factory",
                purpose="raw_input",
            ),
            _reroute_action(
                item="Motor",
                source="Steel factory",
                destination="Central storage",
                purpose="storage",
            ),
        ]
    }

    assert (
        CodexRunner._material_transfer_policy_error(
            answer, ChatRequest(message="make motors at my steel factory")
        )
        is None
    )


def test_transfer_guard_allows_keep_for_an_existing_raw_feed() -> None:
    action = _reroute_action(
        item="Caterium Ore",
        source="BP_ResourceNode121",
        destination="Steel factory",
        purpose="raw_input",
        distance_m=450,
        transport_mode="belt",
    )
    action["kind"] = "keep"

    assert (
        CodexRunner._material_transfer_policy_error(
            {"factory_strategy": "same_factory", "actions": [action]},
            ChatRequest(
                message="i have brought the caterium pure node via belts to my steel factory"
            ),
        )
        is None
    )


def test_transfer_guard_accepts_a_combined_list_of_real_raw_items() -> None:
    answer = {
        "factory_strategy": "new_factory",
        "headline": "New motor factory",
        "summary": "Uses free nodes.",
        "target": {"site": "Motor site", "factory": "New motor factory"},
        "raw_inputs": _raw_ledger(
            node_id="BP_ResourceNode168",
            distance_m=100,
            transport_mode="belt",
        ),
        "actions": [
            _reroute_action(
                item="Iron Ore, Coal, and Copper Ore",
                source="BP_ResourceNode168",
                destination="Motor factory",
                purpose="raw_input",
            )
        ],
    }

    assert (
        CodexRunner._material_transfer_policy_error(
            answer,
            ChatRequest(message="make a motor factory"),
            _resource_results(),
        )
        is None
    )


def test_transfer_guard_rejects_tapped_extractors_for_a_new_factory() -> None:
    answer = {
        "factory_strategy": "new_factory",
        "headline": "Dedicated motor factory",
        "summary": "A physically isolated motor tower.",
        "target": {"site": "Motor site", "factory": "Dedicated motor factory"},
        "raw_inputs": _raw_ledger(
            node_id="BP_ResourceNode114",
            distance_m=66,
            transport_mode="belt",
            strategy="overclock",
            saved_clock=100,
            final_clock=150,
        ),
        "actions": [
            _reroute_action(
                item="Iron Ore",
                source="BP_ResourceNode114",
                destination="Motor factory",
                purpose="raw_input",
            )
        ],
    }

    error = CodexRunner._material_transfer_policy_error(
        answer,
        ChatRequest(message="where should i make motors"),
        _resource_results(),
    )

    assert error is not None
    assert "new factory cannot split output from tapped extractor" in error.casefold()
    assert "bp_resourcenode114" in error.casefold()


def test_transfer_guard_allows_explicit_tapped_extractor_sharing() -> None:
    request = "use the existing miner for the new motor factory"
    answer = {
        "factory_strategy": "new_factory",
        "headline": "New motor factory",
        "summary": "Uses the explicitly shared miner.",
        "target": {"site": "Motor site", "factory": "New motor factory"},
        "raw_inputs": _raw_ledger(
            node_id="BP_ResourceNode114",
            distance_m=66,
            transport_mode="belt",
            strategy="overclock",
            saved_clock=100,
            final_clock=150,
        ),
        "actions": [
            _reroute_action(
                item="Iron Ore",
                source="BP_ResourceNode114",
                destination="Motor factory",
                purpose="raw_input",
            )
        ],
    }

    assert (
        CodexRunner._material_transfer_policy_error(
            answer,
            ChatRequest(message=request),
            _resource_results(),
        )
        is None
    )


def test_claude_stream_events_capture_mcp_evidence_and_structured_output() -> None:
    call_event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "mcp__satisfactory__current_world",
                    "input": {"as_of": "sav:test"},
                }
            ]
        },
    }
    calls = CodexRunner._claude_mcp_calls(call_event)
    pending = {calls[0]["id"]: calls[0]}
    result_event = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": [{"type": "text", "text": "phase=2"}],
                }
            ]
        },
    }

    assert calls[0]["tool"] == "current_world"
    assert CodexRunner._claude_mcp_results(result_event, pending) == [
        {
            "tool": "current_world",
            "arguments": {"as_of": "sav:test"},
            "text": "phase=2",
        }
    ]
    assert CodexRunner._claude_structured_output(
        {"type": "result", "is_error": False, "structured_output": {"save_token": "sav:test"}}
    ) == {"save_token": "sav:test"}


def test_transfer_guard_treats_floor_qualified_labels_as_one_factory() -> None:
    answer = {
        "actions": [
            _reroute_action(
                item="Motor-chain materials",
                source="Steel factory floors 3 and 5",
                destination="Steel factory floors 5 and 7",
                purpose="internal",
            )
        ]
    }

    assert (
        CodexRunner._material_transfer_policy_error(
            answer, ChatRequest(message="make motors at my steel factory")
        )
        is None
    )


def test_transfer_guard_does_not_allow_processed_items_to_pose_as_raw_inputs() -> None:
    answer = {
        "actions": [
            _reroute_action(
                item="Rotor",
                source="Iron factory",
                destination="Steel factory",
                purpose="raw_input",
            )
        ]
    }

    error = CodexRunner._material_transfer_policy_error(
        answer, ChatRequest(message="make motors at my steel factory")
    )

    assert error is not None
    assert "processed 'rotor'" in error


def _resource_results(*, rail: bool = False) -> list[dict[str, object]]:
    return [
        {
            "tool": "progression_and_power",
            "arguments": {},
            "text": f"rail_logistics={'unlocked' if rail else 'locked'}",
        },
        {
            "tool": "find_resource_site",
            "arguments": {"resource": "Iron Ore"},
            "text": (
                "node_id\tpurity\tdist to site\tgrid\tx,y(m)\tz(m)\trate\tstatus\n"
                "BP_ResourceNode114\tpure\t66m\tX1Y3\t-1524,-457\t7\t240\ttapped\n"
                "BP_ResourceNode168\tnormal\t806m\tX1Y4\t-1879,-1146\t7\t120\tfree"
            ),
        },
    ]


def _raw_ledger(
    *,
    node_id: str,
    distance_m: float,
    transport_mode: str,
    strategy: str = "new_extractor",
    saved_clock: float = 0,
    final_clock: float = 100,
) -> list[dict[str, object]]:
    return [
        {
            "item": "Iron Ore",
            "rate_per_min": 120,
            "strategy": strategy,
            "effect": (
                "Nameplate 240/min minus fixed existing demand 120/min leaves 120/min."
                if strategy == "nameplate_spare"
                else "New local extractor supplies 120/min."
            ),
            "sources": [
                {
                    "node_id": node_id,
                    "distance_m": distance_m,
                    "rate_per_min": 120,
                    "saved_clock_percent": saved_clock,
                    "final_clock_percent": final_clock,
                    "power_shards": max(0, int((final_clock - 51) // 50)),
                    "transport_mode": transport_mode,
                }
            ],
        }
    ]


def test_local_source_guard_rejects_pre_rail_distant_ore() -> None:
    answer = {
        "raw_inputs": _raw_ledger(
            node_id="BP_ResourceNode168",
            distance_m=806,
            transport_mode="belt",
        ),
        "actions": [
            _reroute_action(
                item="Iron Ore",
                source="BP_ResourceNode168",
                destination="Steel factory",
                purpose="raw_input",
                distance_m=806,
                transport_mode="belt",
            )
        ]
    }

    error = CodexRunner._local_source_policy_error(
        answer,
        ChatRequest(message="make motors at my steel factory"),
        _resource_results(),
    )

    assert error is not None
    assert "current limit is 600 m" in error
    assert "overclock a nearer tapped node" in error


def test_local_source_guard_allows_local_ore_and_post_rail_train() -> None:
    local = {
        "raw_inputs": _raw_ledger(
            node_id="BP_ResourceNode114",
            distance_m=66,
            transport_mode="belt",
            strategy="nameplate_spare",
            saved_clock=100,
            final_clock=100,
        ),
        "actions": [
            _reroute_action(
                item="Iron Ore",
                source="BP_ResourceNode114",
                destination="Steel factory",
                purpose="raw_input",
                distance_m=66,
                transport_mode="belt",
            )
        ]
    }
    distant_train = {
        "raw_inputs": _raw_ledger(
            node_id="BP_ResourceNode168",
            distance_m=806,
            transport_mode="train",
        ),
        "actions": [
            _reroute_action(
                item="Iron Ore",
                source="BP_ResourceNode168",
                destination="Steel factory",
                purpose="raw_input",
                distance_m=806,
                transport_mode="train",
            )
        ]
    }

    request = ChatRequest(message="make motors at my steel factory")
    assert CodexRunner._local_source_policy_error(local, request, _resource_results()) is None
    assert (
        CodexRunner._local_source_policy_error(
            distant_train, request, _resource_results(rail=True)
        )
        is None
    )


def test_local_source_guard_accepts_an_explicit_player_supplied_raw_belt() -> None:
    answer = {
        "raw_inputs": _raw_ledger(
            node_id="BP_ResourceNode121",
            distance_m=900,
            transport_mode="belt",
            strategy="player_supplied",
            saved_clock=100,
            final_clock=100,
        ),
        "actions": [
            _reroute_action(
                item="Caterium Ore",
                source="BP_ResourceNode121",
                destination="Steel factory",
                purpose="raw_input",
                distance_m=900,
                transport_mode="belt",
            )
        ],
    }
    answer["raw_inputs"][0]["item"] = "Caterium Ore"

    assert (
        CodexRunner._local_source_policy_error(
            answer,
            ChatRequest(
                message=(
                    "i have brought the caterium pure node via belts to my steel factory"
                )
            ),
            _resource_results(),
        )
        is None
    )


def test_local_source_guard_rejects_unclaimed_player_supplied_raw_belt() -> None:
    answer = {
        "raw_inputs": _raw_ledger(
            node_id="BP_ResourceNode121",
            distance_m=100,
            transport_mode="belt",
            strategy="player_supplied",
            saved_clock=100,
            final_clock=100,
        ),
        "actions": [],
    }
    answer["raw_inputs"][0]["item"] = "Caterium Ore"

    error = CodexRunner._local_source_policy_error(
        answer,
        ChatRequest(message="make quickwire at my steel factory"),
        _resource_results(),
    )

    assert error is not None
    assert "did not explicitly say" in error


def test_local_source_guard_splits_combined_raw_reroute_items() -> None:
    answer = {
        "raw_inputs": [
            *_raw_ledger(
                node_id="iron-node",
                distance_m=100,
                transport_mode="belt",
            ),
            {
                **_raw_ledger(
                    node_id="coal-node",
                    distance_m=100,
                    transport_mode="belt",
                )[0],
                "item": "Coal",
            },
        ],
        "actions": [
            _reroute_action(
                item="Iron Ore and Coal",
                source="local raw nodes",
                destination="Steel factory",
                purpose="raw_input",
                distance_m=100,
                transport_mode="belt",
            )
        ],
    }

    assert (
        CodexRunner._local_source_policy_error(
            answer,
            ChatRequest(message="make steel at my steel factory"),
            _resource_results(),
        )
        is None
    )


def test_local_source_guard_checks_declared_distance_against_tool_result() -> None:
    answer = {
        "raw_inputs": _raw_ledger(
            node_id="BP_ResourceNode168",
            distance_m=500,
            transport_mode="belt",
        ),
        "actions": [
            _reroute_action(
                item="Iron Ore",
                source="BP_ResourceNode168",
                destination="Steel factory",
                purpose="raw_input",
                distance_m=500,
                transport_mode="belt",
            )
        ]
    }

    error = CodexRunner._local_source_policy_error(
        answer,
        ChatRequest(message="make motors at my steel factory"),
        _resource_results(),
    )

    assert error is not None
    assert "resource search verified 806 m" in error


def test_local_source_guard_requires_every_raw_input_from_selected_solve() -> None:
    results = _resource_results()
    results.append(
        {
            "tool": "plan_production",
            "arguments": {"target_item": "Motor", "rate_per_min": 5},
            "text": (
                "build\tclock\tprocess\tbuilding\tMW\tnote\n"
                "1\t50%\tMiner Mk.2 on pure Iron Ore\tMiner Mk.2\t-5\t\n"
                "1\t25%\tMiner Mk.2 on pure Coal\tMiner Mk.2\t-2\t"
            ),
        }
    )
    answer = {
        "factory_strategy": "new_factory",
        "target": {"item": "Motor", "rate_per_min": 5},
        "raw_inputs": _raw_ledger(
            node_id="BP_ResourceNode114",
            distance_m=66,
            transport_mode="belt",
            strategy="nameplate_spare",
            saved_clock=100,
            final_clock=100,
        ),
        "actions": [],
    }

    error = CodexRunner._local_source_policy_error(
        answer,
        ChatRequest(message="make motors at my steel factory"),
        results,
    )

    assert error is not None
    assert "coal" in error
    assert "no capacity ledger" in error


def test_local_source_guard_ignores_unselected_solve_inputs_for_same_factory_reuse() -> None:
    results = _resource_results()
    results.append(
        {
            "tool": "plan_production",
            "arguments": {"target_item": "Motor", "rate_per_min": 5},
            "text": (
                "build\tclock\tprocess\tbuilding\tMW\tnote\n"
                "1\t25%\tMiner Mk.2 on pure Coal\tMiner Mk.2\t-2\t\n"
                "1\t5%\tMiner Mk.2 on pure Copper Ore\tMiner Mk.2\t-1\t\n"
                "1\t3%\tMiner Mk.2 on pure Caterium Ore\tMiner Mk.2\t-1\t"
            ),
        }
    )
    answer = {
        "factory_strategy": "same_factory",
        "target": {"item": "Motor", "rate_per_min": 5},
        "raw_inputs": _raw_ledger(
            node_id="BP_ResourceNode114",
            distance_m=66,
            transport_mode="belt",
            strategy="nameplate_spare",
            saved_clock=100,
            final_clock=100,
        ),
        "actions": [],
    }

    assert (
        CodexRunner._local_source_policy_error(
            answer,
            ChatRequest(message="reuse my steel factory for motors"),
            results,
        )
        is None
    )
