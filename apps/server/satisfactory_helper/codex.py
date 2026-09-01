from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .config import PROJECT_ROOT
from .engine import Runtime
from .models import ChatRequest
from .prompts import build_prompt

SCHEMA_PATH = Path(__file__).parent / "schemas" / "factory_answer.schema.json"
DEFAULT_TIMEOUT_SECONDS = 15 * 60
PROGRESS_HEARTBEAT_SECONDS = 20
PRE_RAIL_RAW_ROUTE_LIMIT_M = 600.0
MAX_CORRECTION_RETRIES = 2
# Claude's final stream-json event carries the structured answer and result metadata in
# one newline-delimited record. Detailed factory plans can exceed asyncio's 64 KiB default
# even though the answer itself is valid, so keep a bounded but practical per-event limit.
PROVIDER_EVENT_LIMIT_BYTES = 4 * 1024 * 1024

RAW_TRANSFER_ITEMS = frozenset(
    {
        "bauxite",
        "caterium ore",
        "coal",
        "copper ore",
        "crude oil",
        "iron ore",
        "limestone",
        "nitrogen gas",
        "raw quartz",
        "sam",
        "sulfur",
        "uranium",
        "water",
    }
)
TRANSFER_REQUEST_MARKERS = (
    "belt",
    "bring",
    "feed",
    "import",
    "move",
    "route",
    "send",
    "ship",
    "transfer",
    "use",
)
LONG_DISTANCE_REQUEST_MARKERS = (
    "by train",
    "from a remote",
    "from the distant",
    "use a remote",
    "use bp_resourcenode",
    "use the distant",
    "use the far",
    "with a train",
)
TAPPED_EXTRACTION_SHARE_MARKERS = (
    "feed both factories from",
    "share the existing extractor",
    "share the existing miner",
    "split the existing extractor",
    "split the existing miner",
    "use the existing extractor for the new",
    "use the existing miner for the new",
)
PLAYER_SUPPLIED_ROUTE_MARKERS = (
    "already brought",
    "already connected",
    "already fed",
    "already routed",
    "belted",
    "brought",
    "connected",
    "fed",
    "routed",
    "via belt",
    "via pipe",
)


def _toml_string(value: str) -> str:
    return json.dumps(value.replace("\\", "/"))


def _toml_array(values: list[str]) -> str:
    return "[" + ",".join(_toml_string(value) for value in values) + "]"


def _line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


class CodexRunner:
    def __init__(
        self,
        runtime: Runtime,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.runtime = runtime
        self.timeout_seconds = timeout_seconds
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.validator = Draft202012Validator(self.schema)

    def command(
        self,
        output: Path,
        scratch: Path,
        *,
        provider: str = "codex",
        model: str | None = None,
        snapshot_root: Path | None = None,
    ) -> list[str]:
        settings = self.runtime.settings
        selected_snapshot_root = snapshot_root or settings.snapshot_view_root
        mcp_tool_args = [
            "--snapshot-root",
            str(selected_snapshot_root),
            "--docs",
            str(settings.docs_path),
            "--data-root",
            str(settings.engine_data_root),
        ]
        if getattr(sys, "frozen", False):
            mcp_command = sys.executable
            mcp_args = ["mcp", *mcp_tool_args]
        else:
            mcp_command = "uv"
            mcp_args = [
                "run",
                "--directory",
                str(PROJECT_ROOT),
                "satisfactory-helper-mcp",
                *mcp_tool_args,
            ]
        if provider == "claude":
            claude_schema = {
                key: value for key, value in self.schema.items() if key != "$schema"
            }
            mcp_config = {
                "mcpServers": {
                    "satisfactory": {
                        "type": "stdio",
                        "command": mcp_command,
                        "args": mcp_args,
                    }
                }
            }
            command = [
                settings.claude_executable,
                "--print",
                "--output-format",
                "stream-json",
                "--verbose",
                "--no-session-persistence",
                "--restricted",
                "--permission-mode",
                "dontAsk",
                "--allowedTools",
                "mcp__satisfactory__*",
                "--setting-sources",
                "project",
                "--disable-slash-commands",
                "--no-chrome",
                "--strict-mcp-config",
                "--mcp-config",
                json.dumps(mcp_config, ensure_ascii=False, separators=(",", ":")),
                "--json-schema",
                json.dumps(claude_schema, ensure_ascii=False, separators=(",", ":")),
            ]
            selected_model = model or settings.claude_model
            if selected_model:
                command.extend(["--model", selected_model])
            return command

        command = [
            settings.codex_executable,
            "exec",
            "--approve-for-me",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--ephemeral",
            "--json",
            "--output-schema",
            str(SCHEMA_PATH),
            "--output-last-message",
            str(output),
            "--cd",
            str(scratch),
            "--config",
            f"mcp_servers.satisfactory.command={_toml_string(mcp_command)}",
            "--config",
            f"mcp_servers.satisfactory.args={_toml_array(mcp_args)}",
            "--config",
            "mcp_servers.satisfactory.startup_timeout_sec=90",
        ]
        selected_model = model or settings.codex_model
        if selected_model:
            command.extend(["--model", selected_model])
        command.append("-")
        return command

    @staticmethod
    async def _create_process(
        command: list[str], environment: dict[str, str], scratch: Path
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            cwd=str(scratch),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            limit=PROVIDER_EVENT_LIMIT_BYTES,
        )

    @staticmethod
    def _failure_message(
        provider_name: str, return_code: int, last_failure: str, stderr: str
    ) -> str:
        # A provider's structured failure is authoritative. Stderr may also contain
        # harmless CLI diagnostics (for example, a schema strictness warning).
        return (
            last_failure
            or stderr[-2_000:]
            or f"{provider_name} exited with code {return_code}"
        )

    async def stream(
        self,
        request: ChatRequest,
        *,
        save_token: str,
        snapshot_name: str,
        snapshot_root: Path | None = None,
        _retry: int = 0,
        _policy_feedback: str | None = None,
    ) -> AsyncIterator[bytes]:
        provider = request.provider
        provider_name = "Claude" if provider == "claude" else "Codex"
        provider_status = getattr(self.runtime, provider)
        if not provider_status.get("ready"):
            yield _line(
                {
                    "type": "error",
                    "message": (
                        "Claude is not signed in. Run `claude auth login` and retry."
                        if provider == "claude"
                        else "Codex is not signed in. Run `codex login` and retry."
                    ),
                }
            )
            return

        run_id = uuid.uuid4().hex
        run_root = PROJECT_ROOT / ".local-data" / provider / run_id
        scratch = run_root / "scratch"
        output = run_root / "answer.json"
        event_log = run_root / "events.jsonl"
        stderr_log = run_root / "stderr.log"
        scratch.mkdir(parents=True, exist_ok=True)
        prompt = build_prompt(request, save_token=save_token, snapshot_name=snapshot_name)
        if _policy_feedback:
            prompt += (
                "\n\nHOST VALIDATION FAILURE FROM THE PREVIOUS DRAFT:\n"
                + _policy_feedback
                + "\nThe previous draft was discarded. Correct this exact policy failure; "
                "do not merely rephrase the same plan."
            )
        (run_root / "request.json").write_text(
            json.dumps(
                {
                    "save_token": save_token,
                    "snapshot_filename": snapshot_name,
                    "snapshot_root": str(snapshot_root) if snapshot_root else None,
                    "request": request.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        command = self.command(
            output,
            scratch,
            provider=provider,
            model=request.model,
            snapshot_root=snapshot_root,
        )
        environment = os.environ.copy()
        environment["SATISFACTORY_SAVES"] = str(
            snapshot_root or self.runtime.settings.snapshot_view_root
        )
        environment["SATISFACTORY_DOCS"] = str(self.runtime.settings.docs_path)
        environment["PYTHONUTF8"] = "1"

        yield _line(
            {
                "type": "status",
                "stage": "starting",
                "message": f"Pinning the current snapshot and starting {provider_name}…",
            }
        )
        process: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        stdout_task: asyncio.Task[bytes] | None = None
        started = time.monotonic()
        last_failure = ""
        completed_mcp_tools: set[str] = set()
        pending_claude_tools: dict[str, dict[str, Any]] = {}
        try:
            process = await self._create_process(command, environment, scratch)
            assert process.stdin and process.stdout and process.stderr
            process.stdin.write(prompt.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            stderr_task = asyncio.create_task(process.stderr.read())

            while True:
                remaining = self.timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    minutes = max(1, round(self.timeout_seconds / 60))
                    raise TimeoutError(
                        f"Factory planning exceeded the {minutes}-minute limit"
                    )
                if stdout_task is None:
                    stdout_task = asyncio.create_task(process.stdout.readline())
                try:
                    raw = await asyncio.wait_for(
                        asyncio.shield(stdout_task),
                        timeout=min(PROGRESS_HEARTBEAT_SECONDS, remaining),
                    )
                except TimeoutError:
                    elapsed_seconds = max(1, int(time.monotonic() - started))
                    if elapsed_seconds < 60:
                        elapsed_label = f"{elapsed_seconds}s"
                    else:
                        minutes, seconds = divmod(elapsed_seconds, 60)
                        elapsed_label = f"{minutes}m {seconds:02d}s"
                    yield _line(
                        {
                            "type": "status",
                            "stage": "thinking",
                            "message": (
                                "Still solving against the pinned save "
                                f"({elapsed_label} elapsed)\u2026"
                            ),
                        }
                    )
                    continue
                stdout_task = None
                if not raw:
                    break
                with event_log.open("ab") as audit:
                    audit.write(raw)
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if provider == "claude":
                    structured_output = self._claude_structured_output(event)
                    if structured_output is not None:
                        output.write_text(
                            json.dumps(structured_output, ensure_ascii=False),
                            encoding="utf-8",
                        )
                    if event.get("type") == "result" and event.get("is_error"):
                        last_failure = str(
                            event.get("result") or event.get("subtype") or "Claude failed"
                        )[-2_000:]
                    if event.get("type") == "system" and event.get("subtype") == "init":
                        yield _line(
                            {
                                "type": "status",
                                "stage": "thinking",
                                "message": "Reading your world…",
                            }
                        )
                    for call in self._claude_mcp_calls(event):
                        pending_claude_tools[call["id"]] = call
                        yield _line(
                            {
                                "type": "tool",
                                "stage": "running",
                                "message": f"Checking {call['tool'].replace('_', ' ')}…",
                            }
                        )
                    for result in self._claude_mcp_results(event, pending_claude_tools):
                        completed_mcp_tools.add(result["tool"])
                        yield _line(
                            {
                                "type": "tool",
                                "stage": "complete",
                                "message": f"Checked {result['tool'].replace('_', ' ')}",
                            }
                        )
                    continue
                if event.get("type") == "error":
                    last_failure = str(event.get("message", ""))[-2_000:]
                elif event.get("type") == "turn.failed":
                    failure = event.get("error")
                    if isinstance(failure, dict):
                        last_failure = str(failure.get("message", ""))[-2_000:]
                progress = self._progress_event(event)
                if progress is not None:
                    yield _line(progress)
                tool = self._completed_mcp_tool(event)
                if tool is not None:
                    completed_mcp_tools.add(tool)
            return_code = await asyncio.wait_for(
                process.wait(),
                timeout=max(1, self.timeout_seconds - (time.monotonic() - started)),
            )
            stderr_bytes = await stderr_task
            if stderr_bytes:
                stderr_log.write_bytes(stderr_bytes)
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            if return_code != 0:
                message = self._failure_message(
                    provider_name, return_code, last_failure, stderr
                )
                yield _line({"type": "error", "message": message})
                return
            if not output.is_file():
                yield _line(
                    {"type": "error", "message": f"{provider_name} returned no final plan."}
                )
                return
            answer = json.loads(output.read_text(encoding="utf-8"))
            errors = sorted(self.validator.iter_errors(answer), key=lambda error: list(error.path))
            if errors:
                detail = "; ".join(error.message for error in errors[:5])
                if _retry < MAX_CORRECTION_RETRIES:
                    yield _line(
                        {
                            "type": "status",
                            "stage": "correcting_schema",
                            "message": (
                                "The draft shape was invalid; correcting it automatically..."
                            ),
                        }
                    )
                    async for chunk in self.stream(
                        request,
                        save_token=save_token,
                        snapshot_name=snapshot_name,
                        snapshot_root=snapshot_root,
                        _retry=_retry + 1,
                        _policy_feedback=f"Plan schema validation failed: {detail}",
                    ):
                        yield chunk
                    return
                yield _line(
                    {"type": "error", "message": f"Plan schema validation failed: {detail}"}
                )
                return
            if not completed_mcp_tools:
                if _retry == 0:
                    yield _line(
                        {
                            "type": "status",
                            "stage": "reconnecting",
                            "message": "Game tools missed the first connection; retrying once…",
                        }
                    )
                    async for chunk in self.stream(
                        request,
                        save_token=save_token,
                        snapshot_name=snapshot_name,
                        snapshot_root=snapshot_root,
                        _retry=1,
                    ):
                        yield chunk
                    return
                yield _line(
                    {
                        "type": "error",
                        "message": (
                            "The Satisfactory tools did not connect after an automatic retry. "
                            "The unsupported answer was discarded; please retry the request."
                        ),
                    }
                )
                return
            if answer.get("save_token") != save_token:
                yield _line(
                    {
                        "type": "error",
                        "message": (
                            f"{provider_name} answered against a different save token; "
                            "refresh and retry."
                        ),
                    }
                )
                return
            yield _line({"type": "answer", "data": answer})
        except TimeoutError as exc:
            yield _line({"type": "error", "message": str(exc)})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield _line(
                {"type": "error", "message": f"Could not run {provider_name}: {exc}"}
            )
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
            if stdout_task is not None and not stdout_task.done():
                stdout_task.cancel()
            shutil.rmtree(scratch, ignore_errors=True)

    @staticmethod
    def _claude_structured_output(event: dict[str, Any]) -> dict[str, Any] | None:
        if event.get("type") != "result" or event.get("is_error"):
            return None
        structured = event.get("structured_output")
        if isinstance(structured, dict):
            return structured
        result = event.get("result")
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    @staticmethod
    def _claude_mcp_tool_name(value: object) -> str | None:
        name = str(value or "")
        prefix = "mcp__satisfactory__"
        return name[len(prefix) :] if name.startswith(prefix) else None

    @classmethod
    def _claude_mcp_calls(cls, event: dict[str, Any]) -> list[dict[str, Any]]:
        if event.get("type") != "assistant":
            return []
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        calls: list[dict[str, Any]] = []
        for row in content or ():
            if not isinstance(row, dict) or row.get("type") != "tool_use":
                continue
            tool = cls._claude_mcp_tool_name(row.get("name"))
            tool_id = row.get("id")
            if tool is None or not isinstance(tool_id, str):
                continue
            arguments = row.get("input")
            calls.append(
                {
                    "id": tool_id,
                    "tool": tool,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                }
            )
        return calls

    @staticmethod
    def _claude_result_text(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(
                str(row.get("text", ""))
                for row in value
                if isinstance(row, dict) and row.get("type") == "text"
            )
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return ""

    @classmethod
    def _claude_mcp_results(
        cls,
        event: dict[str, Any],
        pending: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if event.get("type") != "user":
            return []
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        results: list[dict[str, Any]] = []
        for row in content or ():
            if not isinstance(row, dict) or row.get("type") != "tool_result":
                continue
            call = pending.pop(str(row.get("tool_use_id") or ""), None)
            if call is None or row.get("is_error"):
                continue
            results.append(
                {
                    "tool": call["tool"],
                    "arguments": call["arguments"],
                    "text": cls._claude_result_text(row.get("content")),
                }
            )
        return results

    @staticmethod
    def _progress_event(event: dict[str, Any]) -> dict[str, str] | None:
        kind = str(event.get("type", ""))
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = str(item.get("type", ""))
        if kind == "thread.started":
            return {"type": "status", "stage": "thinking", "message": "Reading your world…"}
        if item_type in {"mcp_tool_call", "tool_call"}:
            tool = str(item.get("tool") or item.get("name") or "world tool")
            if kind.endswith("started"):
                return {
                    "type": "tool",
                    "stage": "running",
                    "message": f"Checking {tool.replace('_', ' ')}…",
                }
            if kind.endswith("completed"):
                return {
                    "type": "tool",
                    "stage": "complete",
                    "message": f"Checked {tool.replace('_', ' ')}",
                }
        if kind == "turn.failed":
            return {"type": "status", "stage": "failed", "message": "Codex could not finish."}
        return None

    @staticmethod
    def _completed_mcp_tool(event: dict[str, Any]) -> str | None:
        if event.get("type") != "item.completed":
            return None
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
            return None
        if item.get("error"):
            return None
        tool = item.get("tool") or item.get("name")
        return str(tool) if tool else None

    @classmethod
    def _completed_mcp_result(cls, event: dict[str, Any]) -> dict[str, Any] | None:
        tool = cls._completed_mcp_tool(event)
        if tool is None:
            return None
        item = event["item"]
        result = item.get("result")
        content = result.get("content") if isinstance(result, dict) else None
        text_parts = [
            str(row.get("text", ""))
            for row in content or ()
            if isinstance(row, dict) and row.get("type") == "text"
        ]
        arguments = item.get("arguments")
        return {
            "tool": tool,
            "arguments": arguments if isinstance(arguments, dict) else {},
            "text": "\n".join(text_parts),
        }

    @staticmethod
    def _requires_site_profile(answer: dict[str, Any]) -> bool:
        actions = [row for row in answer.get("actions") or () if isinstance(row, dict)]
        if (
            answer.get("overall_status") == "blocked"
            and not answer.get("floors")
            and all(action.get("kind") == "manual_check" for action in actions)
        ):
            # A truthful failure can still echo the player's requested site in ``target``.
            # Requiring inspect_site here turns an unavailable snapshot into a pointless
            # retry and then hides the useful blocked explanation behind a host error.
            return False
        if answer.get("floors"):
            return True
        target = answer.get("target")
        if isinstance(target, dict) and any(
            target.get(key) is not None for key in ("site", "floor")
        ):
            return True
        for action in actions:
            if action.get("site") is not None or action.get("coordinates") is not None:
                return True
            if action.get("from_floor") is not None or action.get("to_floor") is not None:
                return True
        return False

    @staticmethod
    def _normalize_policy_text(value: object) -> str:
        return " ".join(str(value or "").casefold().split())

    @classmethod
    def _split_transfer_items(cls, value: object) -> list[str]:
        normalized = cls._normalize_policy_text(value)
        return [
            item.strip(" .")
            for item in re.split(r"\s*(?:,|;|\band\b)\s*", normalized)
            if item.strip(" .")
        ]

    @classmethod
    def _player_supplied_raw_input(cls, item: object, player_context: str) -> bool:
        """Whether the player explicitly says this raw feed already reaches the site."""
        normalized_item = cls._normalize_policy_text(item)
        item_names = {normalized_item}
        for prefix in ("raw ", "crude "):
            if normalized_item.startswith(prefix):
                item_names.add(normalized_item[len(prefix) :])
        for suffix in (" ore", " gas"):
            if normalized_item.endswith(suffix):
                item_names.add(normalized_item[: -len(suffix)])
        mentions_item = any(
            len(name) >= 4 and re.search(rf"\b{re.escape(name)}\b", player_context)
            for name in item_names
        )
        return mentions_item and any(
            marker in player_context for marker in PLAYER_SUPPLIED_ROUTE_MARKERS
        )

    @classmethod
    def _resource_node_statuses(
        cls, tool_results: list[dict[str, Any]]
    ) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for result in tool_results:
            if result.get("tool") != "find_resource_site":
                continue
            for line in str(result.get("text", "")).splitlines():
                cells = line.split("\t")
                if len(cells) < 8 or cells[0] == "node_id":
                    continue
                status = cls._normalize_policy_text(cells[7])
                if status in {"free", "tapped"}:
                    statuses[cls._normalize_policy_text(cells[0])] = status
        return statuses

    @classmethod
    def _canonical_factory_site(cls, value: object) -> str:
        """Drop floor/level qualifiers without merging distinct physical factory names."""
        site = cls._normalize_policy_text(value)
        markers = (" global floor", " floors ", " floor ", " site level ", " level ")
        cuts = [site.index(marker) for marker in markers if marker in site]
        if cuts:
            site = site[: min(cuts)]
        return site.rstrip(" ,-:")

    @classmethod
    def _local_source_policy_error(
        cls,
        answer: dict[str, Any],
        request: ChatRequest,
        tool_results: list[dict[str, Any]],
    ) -> str | None:
        """Reject pre-rail raw routes that bypass the player's practical local radius."""
        rail_unlocked = any(
            row.get("tool") == "progression_and_power"
            and "rail_logistics=unlocked" in str(row.get("text", ""))
            for row in tool_results
        )
        player_messages = [request.message]
        player_messages.extend(
            str(row.get("content", ""))
            for row in request.conversation
            if isinstance(row, dict) and row.get("role") == "user"
        )
        player_context = "\n".join(
            cls._normalize_policy_text(row) for row in player_messages
        )
        explicit_long_route = False
        for marker in LONG_DISTANCE_REQUEST_MARKERS:
            marker_at = player_context.find(marker)
            if marker_at < 0:
                continue
            prefix = player_context[max(0, marker_at - 16) : marker_at]
            if not any(
                negative in prefix for negative in ("don't ", "do not ", "never ", "not ")
            ):
                explicit_long_route = True
                break

        node_distances: dict[str, float] = {}
        for result in tool_results:
            if result.get("tool") != "find_resource_site":
                continue
            for line in str(result.get("text", "")).splitlines():
                cells = line.split("\t")
                if len(cells) < 8 or cells[0] == "node_id":
                    continue
                try:
                    distance = float(cells[2].removesuffix("m"))
                except ValueError:
                    continue
                node_distances[cells[0].casefold()] = distance

        target = answer.get("target") if isinstance(answer.get("target"), dict) else {}
        target_item = cls._normalize_policy_text(target.get("item"))
        target_rate = target.get("rate_per_min")
        actions = [row for row in answer.get("actions") or () if isinstance(row, dict)]
        required_raw_items: set[str] = set()
        for action in actions:
            if action.get("transfer_purpose") != "raw_input":
                continue
            required_raw_items.update(cls._split_transfer_items(action.get("transfer_item")))

        # A production solve is a blank-slate counterfactual. For a same-factory plan the
        # agent may inspect that solve, then deliberately reuse or rebalance standing lines
        # instead. Treating every extractor in the exploratory solve as selected made valid
        # reuse plans impossible to return. New-factory plans do select the solved chain, so
        # its raw inputs remain mandatory there.
        if cls._normalize_policy_text(answer.get("factory_strategy")) == "new_factory":
            for result in tool_results:
                if result.get("tool") != "plan_production":
                    continue
                arguments = result.get("arguments")
                if not isinstance(arguments, dict):
                    continue
                if cls._normalize_policy_text(arguments.get("target_item")) != target_item:
                    continue
                planned_rate = arguments.get("rate_per_min")
                if (
                    isinstance(target_rate, (int, float))
                    and isinstance(planned_rate, (int, float))
                    and abs(float(target_rate) - float(planned_rate)) > 0.01
                ):
                    continue
                for line in str(result.get("text", "")).splitlines():
                    cells = line.split("\t")
                    if len(cells) < 4:
                        continue
                    process = cells[2]
                    if " on " not in process or not any(
                        extractor in process for extractor in ("Miner", "Extractor", "Pump")
                    ):
                        continue
                    raw_item = process.split(" on ", 1)[1]
                    for purity in ("impure ", "normal ", "pure "):
                        if raw_item.casefold().startswith(purity):
                            raw_item = raw_item[len(purity) :]
                            break
                    required_raw_items.add(cls._normalize_policy_text(raw_item))

        raw_inputs = answer.get("raw_inputs")
        if not isinstance(raw_inputs, list):
            return "The plan must declare its raw_inputs capacity ledger."
        declared_raw_items = {
            cls._normalize_policy_text(row.get("item"))
            for row in raw_inputs
            if isinstance(row, dict)
        }
        missing_items = sorted(required_raw_items - declared_raw_items)
        if missing_items:
            return (
                "The selected production solve uses raw inputs with no capacity ledger: "
                + ", ".join(missing_items)
                + ". Declare exact nodes, distances, rates, saved/final clocks, shards, and "
                "whether capacity comes from spare output, overclocking, rebalancing, or a "
                "new extractor."
            )

        for row in raw_inputs:
            if not isinstance(row, dict):
                continue
            item = str(row.get("item") or "raw input")
            strategy = row.get("strategy")
            player_supplied = cls._player_supplied_raw_input(item, player_context)
            sources = row.get("sources")
            if not isinstance(sources, list) or not sources:
                return f"{item} must name at least one exact raw source."
            source_rate = sum(
                float(source.get("rate_per_min", 0))
                for source in sources
                if isinstance(source, dict)
                and isinstance(source.get("rate_per_min"), (int, float))
            )
            declared_rate = row.get("rate_per_min")
            if not isinstance(declared_rate, (int, float)) or abs(
                source_rate - float(declared_rate)
            ) > 0.1:
                return (
                    f"{item} source rates total {source_rate:g}/min but the ledger declares "
                    f"{declared_rate!r}/min."
                )

            has_overclock = False
            for source in sources:
                if not isinstance(source, dict):
                    continue
                node_id = cls._normalize_policy_text(source.get("node_id"))
                distance = source.get("distance_m")
                saved_clock = source.get("saved_clock_percent")
                final_clock = source.get("final_clock_percent")
                shards = source.get("power_shards")
                if not isinstance(distance, (int, float)):
                    return f"{item} source {node_id!r} must declare its verified distance."
                if node_id in node_distances and abs(
                    float(distance) - node_distances[node_id]
                ) > 2:
                    return (
                        f"{item} source {node_id!r} declares {distance:g} m, but the resource "
                        f"search verified {node_distances[node_id]:g} m."
                    )
                if not isinstance(saved_clock, (int, float)) or not isinstance(
                    final_clock, (int, float)
                ):
                    return f"{item} source {node_id!r} must declare saved and final clocks."
                expected_shards = math.ceil(max(0.0, float(final_clock) - 100.0) / 50.0)
                if shards != expected_shards:
                    return (
                        f"{item} source {node_id!r} at {final_clock:g}% needs "
                        f"{expected_shards} Power Shard(s), not {shards!r}."
                    )
                if (
                    float(saved_clock) > 0
                    and float(final_clock) > float(saved_clock) + 0.01
                ) or (float(saved_clock) == 0 and float(final_clock) > 100.01):
                    has_overclock = True
                if (
                    float(distance) > PRE_RAIL_RAW_ROUTE_LIMIT_M
                    and not explicit_long_route
                    and not player_supplied
                ):
                    if not rail_unlocked:
                        return (
                            f"{item} source {node_id!r} is {distance:g} m away while rail is "
                            f"locked; the current limit is {PRE_RAIL_RAW_ROUTE_LIMIT_M:g} m. "
                            "Rebalance same-site outputs, inspect unlocked recipe alternatives, "
                            "overclock a nearer tapped node, or site the final-product factory "
                            "nearer its raw resources."
                        )
                    if source.get("transport_mode") != "train":
                        return (
                            f"{item} source {node_id!r} is {distance:g} m away after rail "
                            "unlock but does not use train transport."
                        )
            if strategy == "overclock" and not has_overclock:
                return f"{item} is marked overclock but no source clock increases."
            if strategy != "overclock" and has_overclock:
                return (
                    f"{item} increases a source clock but is classified as {strategy!r}, "
                    "not overclock."
                )
            effect = cls._normalize_policy_text(row.get("effect"))
            if strategy == "player_supplied" and not player_supplied:
                return (
                    f"{item} is marked player_supplied, but the player did not explicitly "
                    "say that this raw belt or pipe already reaches the target site."
                )
            if strategy == "nameplate_spare" and "nameplate" not in effect:
                return (
                    f"{item} claims spare capacity without a fixed nameplate-rate equation."
                )
            if strategy == "rebalanced_output":
                has_before_after = "before" in effect and "after" in effect
                has_from_to = " from " in f" {effect} " and " to " in f" {effect} "
                if not has_before_after and not has_from_to and "->" not in effect:
                    return (
                        f"{item} rebalancing must state exact before and after output rates."
                    )
                if not any(
                    isinstance(action, dict)
                    and action.get("kind")
                    in {"remove", "set_recipe", "change_clock", "reroute"}
                    for action in answer.get("actions") or ()
                ):
                    return (
                        f"{item} claims rebalanced output without a matching physical action."
                    )

        for index, action in enumerate(actions):
            if action.get("transfer_purpose") != "raw_input":
                continue
            action_id = str(action.get("id") or f"action {index + 1}")
            declared_distance = action.get("source_distance_m")
            if not isinstance(declared_distance, (int, float)):
                return f"{action_id} must declare the verified raw-source distance."

            source = cls._normalize_policy_text(action.get("source_site"))
            matched = [
                distance
                for node_id, distance in node_distances.items()
                if node_id in source
            ]
            if matched:
                verified_distance = max(matched)
                if abs(float(declared_distance) - verified_distance) > 2:
                    return (
                        f"{action_id} declares {declared_distance:g} m, but the resource "
                        f"search verified {verified_distance:g} m."
                    )
            else:
                verified_distance = float(declared_distance)

            if (
                verified_distance <= PRE_RAIL_RAW_ROUTE_LIMIT_M
                or explicit_long_route
                or any(
                    cls._player_supplied_raw_input(item, player_context)
                    for item in cls._split_transfer_items(action.get("transfer_item"))
                )
            ):
                continue
            if not rail_unlocked:
                return (
                    f"{action_id} uses a raw source {verified_distance:g} m away while rail "
                    f"is locked; the current limit is {PRE_RAIL_RAW_ROUTE_LIMIT_M:g} m. "
                    "Rebalance same-site outputs, inspect unlocked recipe alternatives, "
                    "overclock a nearer tapped node, or site the final-product factory nearer "
                    "its raw resources."
                )
            if action.get("transport_mode") != "train":
                return (
                    f"{action_id} uses a raw source {verified_distance:g} m away after rail "
                    "unlock but does not use train transport."
                )
        return None

    @classmethod
    def _material_transfer_policy_error(
        cls,
        answer: dict[str, Any],
        request: ChatRequest,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> str | None:
        """Reject undeclared or unauthorized material routes between physical factories."""
        player_messages = [request.message]
        player_messages.extend(
            str(row.get("content", ""))
            for row in request.conversation
            if isinstance(row, dict) and row.get("role") == "user"
        )
        player_context = "\n".join(cls._normalize_policy_text(row) for row in player_messages)
        tool_results = tool_results or []
        node_statuses = cls._resource_node_statuses(tool_results)

        strategy = cls._normalize_policy_text(answer.get("factory_strategy"))
        # Factory identity is a structured decision. Free-text adjectives such as
        # "dedicated Quickwire floor" cannot safely reclassify an in-site expansion.
        is_new_factory = strategy == "new_factory"
        explicitly_shares_tapped_extraction = any(
            marker in player_context for marker in TAPPED_EXTRACTION_SHARE_MARKERS
        )
        if is_new_factory and not explicitly_shares_tapped_extraction:
            tapped_sources = sorted(
                {
                    cls._normalize_policy_text(source.get("node_id"))
                    for raw_input in answer.get("raw_inputs") or ()
                    if isinstance(raw_input, dict)
                    for source in raw_input.get("sources") or ()
                    if isinstance(source, dict)
                    and node_statuses.get(
                        cls._normalize_policy_text(source.get("node_id"))
                    )
                    == "tapped"
                }
            )
            if tapped_sources:
                return (
                    "A new factory cannot split output from tapped extractor(s) already "
                    "serving another factory: "
                    + ", ".join(tapped_sources)
                    + ". Added overclock output is still owned by the existing factory. "
                    "Use untapped local nodes, move the final-product factory to a viable "
                    "free-node cluster, or obtain explicit player permission to share those "
                    "exact extractors."
                )

        for index, action in enumerate(answer.get("actions") or ()):  # pragma: no branch
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or f"action {index + 1}")
            purpose = cls._normalize_policy_text(action.get("transfer_purpose"))
            if action.get("kind") not in {"reroute", "keep"}:
                if purpose and purpose != "none":
                    return f"{action_id} declares a material transfer but is not a reroute action."
                continue

            item = cls._normalize_policy_text(action.get("transfer_item"))
            items = cls._split_transfer_items(action.get("transfer_item"))
            source = cls._normalize_policy_text(action.get("source_site"))
            destination = cls._normalize_policy_text(action.get("destination_site"))
            if not item or not source or not destination or purpose in {"", "none"}:
                return (
                    f"{action_id} must declare its item, source site, destination site, "
                    "and transfer purpose."
                )

            crosses_factory_boundary = cls._canonical_factory_site(
                source
            ) != cls._canonical_factory_site(destination)
            if not crosses_factory_boundary:
                if purpose not in {"internal", "storage"}:
                    return f"{action_id} is site-local and must be marked internal or storage."
                continue

            if purpose == "raw_input":
                processed = [
                    candidate for candidate in items if candidate not in RAW_TRANSFER_ITEMS
                ]
                if processed:
                    return (
                        f"{action_id} labels processed {', '.join(processed)!r} as a "
                        "raw-resource input."
                    )
                continue
            if purpose == "storage":
                continue
            if purpose != "production_input":
                return (
                    f"{action_id} crosses from {source!r} to {destination!r} but is not "
                    "classified as raw input, storage, or production input."
                )

            quote = cls._normalize_policy_text(action.get("authorization_quote"))
            explicitly_requests_transfer = any(
                marker in quote for marker in TRANSFER_REQUEST_MARKERS
            ) or (" from " in f" {quote} " and " to " in f" {quote} ")
            if (
                len(quote) < 8
                or quote not in player_context
                or item not in quote
                or not explicitly_requests_transfer
            ):
                return (
                    f"{action_id} needs an exact player quote explicitly authorizing the "
                    f"cross-factory transfer of {item!r}."
                )
        return None
