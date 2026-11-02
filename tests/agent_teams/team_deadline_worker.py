"""Deterministic native tool deadlines over the existing real pipeline bootstrap."""

import asyncio
import json
import os
import sys
import time
from functools import partial
from pathlib import Path

import httpx
import psutil
from pytest import MonkeyPatch

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent_teams.autonomous_tools import TeamManageTool
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor


async def run_deadline_scenario(
    folder, endpoint, action, scenario, service, roles, context, adapter
):
    """Exercise wait and uncertain dispatch through real public boundaries.

    Args:
        folder: Isolated persistent database and phase evidence directory.
        endpoint: Independent external model, effect and response-gate server.
        action: Initial execution or reconstruction after actual process loss.
        scenario: Wait exemption, dispatch response loss or normal control.
        service: Existing real workflow runtime from the main-pipeline bootstrap.
        roles: Manager-backed role and resource service.
        context: Actual plugin context used by the main pipeline.
        adapter: Actual WebChat adapter used to create the tool-call event.
    """
    identity = {
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "creation_time": psutil.Process().create_time(),
        "argv": sys.argv,
        "cwd": str(Path.cwd()),
        "action": action,
    }
    (folder / f"deadline-runtime-{action}.json").write_text(
        json.dumps(identity), encoding="utf-8"
    )
    patch = MonkeyPatch()
    try:
        if action == "start":
            base = (await roles.list_roles("alice"))["roles"][0]
            writer = await roles.save_role(
                "alice",
                {
                    "definition": {
                        **base["definition"],
                        "name": "Deadline writer",
                        "max_instances": 1,
                        "max_parallel": 1,
                    },
                    "installation": base["installation"],
                },
            )
            coordinator = await roles.save_role(
                "alice",
                {
                    "definition": {
                        **base["definition"],
                        "name": "Deadline coordinator",
                        "max_steps": 20,
                        "max_instances": 1,
                        "max_parallel": 1,
                        # The negative case must reach the result gate before
                        # its ordinary deadline; it deliberately exceeds it.
                        "tool_call_timeout": 5,
                    },
                    "installation": base["installation"],
                },
            )
            asset = await service.save_asset(
                "alice",
                {
                    "definition": {
                        "kind": "autonomous",
                        "name": "Native deadline contract",
                        "coordinator": {
                            "id": "coordinator",
                            "role_id": coordinator["role_id"],
                            "role_version": 1,
                        },
                        "allowed_roles": [
                            {
                                "id": "writer",
                                "role_id": writer["role_id"],
                                "role_version": 1,
                            }
                        ],
                        "max_parallel": 1,
                        "max_members": 2,
                        "max_tasks": 3,
                    }
                },
            )
            if scenario == "team_deadline_unknown":
                original_call = TeamManageTool.call

                async def gated_call(tool, context, **kwargs):
                    """Lose only delivery of a real committed native tool result.

                    Args:
                        tool: Original native coordinator tool instance.
                        context: Actual pipeline tool execution context.
                        **kwargs: Original public coordinator decision.

                    Returns:
                        The unmodified original JSON result if the gate opens.
                    """
                    result = await original_call(tool, context, **kwargs)
                    if kwargs.get("request_id") == "dispatch-one":
                        observed = {
                            "result": result,
                            "arguments": kwargs,
                            "returned_at": time.time(),
                            "deadline": context.tool_call_timeout,
                        }
                        (folder / "dispatch-returned.json").write_text(
                            json.dumps(observed), encoding="utf-8"
                        )
                        # This is the native tool's return boundary. The real
                        # service, scheduler and durable operation already ran.
                        async with httpx.AsyncClient(timeout=None) as client:
                            await client.post(endpoint + "/return-gate", json=observed)
                    return result

                patch.setattr(TeamManageTool, "call", gated_call)
            run = await service.start_run(
                "alice",
                asset["asset_id"],
                {"version": 1, "inputs": {"goal": "deadline-one"}},
            )
            run_id = run["run_id"]
            (folder / "deadline-run.json").write_text(
                json.dumps({"run_id": run_id}), encoding="utf-8"
            )
            if scenario == "team_deadline_wait":
                deadline = time.monotonic() + 40
                while True:
                    current = await service.get_run("alice", run_id)
                    caller = current["tasks"][0]
                    if (
                        caller["state"].get("execution_id")
                        and (folder / "coordinator-held.json").exists()
                    ):
                        break
                    assert time.monotonic() < deadline, current
                    await asyncio.sleep(0.02)
                manage = partial(
                    service.manage_team,
                    "alice",
                    caller["run_id"],
                    caller["state"]["attempt_id"],
                    caller["state"]["execution_id"],
                )
                member = await manage(
                    {
                        "action": "create_member",
                        "request_id": "setup-member",
                        "preset_id": "writer",
                        "name": "Writer",
                    }
                )
                child = await manage(
                    {
                        "action": "dispatch",
                        "request_id": "setup-dispatch",
                        "member_id": member["member_id"],
                        "name": "Slow member",
                        "task": "deadline-one",
                    }
                )
                tool = TeamManageTool(manage, current["snapshot"]["allowed_roles"])
                event = adapter.create_event(
                    await adapter.convert_message(
                        ("alice", "deadline-wait", {"message_id": "wait-call"})
                    )
                )
                tool_context = ContextWrapper(
                    context=AstrAgentContext(context=context, event=event),
                    tool_call_timeout=0.5,
                    tool_call_timeout_exclude=[],
                )
                started = time.monotonic()
                (folder / "wait-started.json").write_text(
                    json.dumps({"started": started, "child": child}), encoding="utf-8"
                )
                results = []
                async for result in FunctionToolExecutor.execute(
                    tool,
                    tool_context,
                    action="wait",
                    request_id="wait-slow-member",
                    task_ids=[child["task_id"]],
                ):
                    if result is not None:
                        results.append(result.model_dump(mode="json"))
                observed = await service.get_run("alice", run_id)
                evidence = {
                    "deadline": 0.5,
                    "elapsed": time.monotonic() - started,
                    "result": results,
                    "after": observed,
                    "child": child,
                    "scope": "Public executor/service wait integration, not full root completion.",
                }
                (folder / "deadline-evidence.json").write_text(
                    json.dumps(evidence), encoding="utf-8"
                )
                await service.control_run(
                    "alice", run_id, {"action": "cancel", "request_id": "wait-cleanup"}
                )
                return
            deadline = time.monotonic() + 60
            while True:
                current = await service.get_run("alice", run_id)
                if scenario == "team_deadline_unknown":
                    caller = current["tasks"][0]
                    unknown = next(
                        (c for c in caller["tool_calls"] if c["status"] == "unknown"),
                        None,
                    )
                    children = current["tasks"][1:]
                    if (
                        current["status"] == "blocked"
                        and unknown
                        and children
                        and all(child["status"] == "completed" for child in children)
                    ):
                        (folder / "deadline-before.json").write_text(
                            json.dumps(current), encoding="utf-8"
                        )
                        (folder / "kill-ready.json").write_text(
                            json.dumps({"run_id": run_id, "call": unknown}),
                            encoding="utf-8",
                        )
                        await asyncio.Future()
                elif current["status"] == "completed":
                    (folder / "deadline-evidence.json").write_text(
                        json.dumps({"after": current}), encoding="utf-8"
                    )
                    return
                assert time.monotonic() < deadline, current
                await asyncio.sleep(0.02)
        else:
            saved = json.loads((folder / "kill-ready.json").read_text(encoding="utf-8"))
            before = await service.get_run("alice", saved["run_id"])
            caller = before["tasks"][0]
            call = next(c for c in caller["tool_calls"] if c["status"] == "unknown")
            observed = json.loads(
                (folder / "dispatch-returned.json").read_text(encoding="utf-8")
            )
            decision = {
                "request_id": "confirm-observed-dispatch",
                "action": "confirm_result",
                "evidence": "Original native tool JSON captured after the real service committed.",
                "result": observed["result"],
            }
            await service.resolve_tool_call(
                "alice", caller["run_id"], call["record_id"], decision
            )
            await service.resolve_tool_call(
                "alice", caller["run_id"], call["record_id"], decision
            )
            deadline = time.monotonic() + 60
            while True:
                current = await service.get_run("alice", saved["run_id"])
                if current["status"] == "completed":
                    break
                assert time.monotonic() < deadline, current
                await asyncio.sleep(0.02)
            before_duplicate = json.loads(
                json.dumps(await service.get_run("alice", saved["run_id"]))
            )
            (folder / "duplicate-before.json").write_text(
                json.dumps(before_duplicate), encoding="utf-8"
            )
            await service.resolve_tool_call(
                "alice", caller["run_id"], call["record_id"], decision
            )
            after_duplicate = await service.get_run("alice", saved["run_id"])
            (folder / "duplicate-after.json").write_text(
                json.dumps(after_duplicate), encoding="utf-8"
            )
            for key in (
                "status",
                "control",
                "snapshot",
                "state",
                "result_ref",
                "result",
                "usage",
                "events",
                "messages",
                "members",
                "relationships",
                "tool_calls",
                "attempts",
            ):
                assert after_duplicate[key] == before_duplicate[key], key
            assert len(after_duplicate["tasks"]) == len(before_duplicate["tasks"])
            for old, new in zip(
                before_duplicate["tasks"], after_duplicate["tasks"], strict=True
            ):
                for key in (
                    "run_id",
                    "snapshot",
                    "state",
                    "result_ref",
                    "result",
                    "usage",
                    "events",
                    "messages",
                    "process",
                    "tool_calls",
                    "attempts",
                    "cursor",
                    "read_artifacts",
                ):
                    assert new[key] == old[key], (old["run_id"], key)
            (folder / "deadline-evidence.json").write_text(
                json.dumps(
                    {
                        "before": before,
                        "before_last_duplicate": before_duplicate,
                        "after_last_duplicate": after_duplicate,
                        "after": after_duplicate,
                        "decision": decision,
                    }
                ),
                encoding="utf-8",
            )
    finally:
        patch.undo()
