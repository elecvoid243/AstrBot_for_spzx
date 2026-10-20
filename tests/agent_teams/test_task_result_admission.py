"""Intrinsic task result admission through persisted public asset services."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import time

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", ["coze", "deerflow", "dify", "dashscope"])
@pytest.mark.parametrize("declaration", [None, []])
@pytest.mark.parametrize(
    "kind",
    ["single", "graph", "coordinator", "allowed", "nested_team", "nested_workflow"],
)
async def test_default_text_task_requires_result_protocol_before_dispatch(
    tmp_path, runner, declaration, kind
):
    """Reject an otherwise configured remote runner before creating active work.

    Args:
        tmp_path: Isolated durable database and public observation record.
        runner: Integrated executor without the result protocol.
        declaration: Omitted or explicitly empty optional role requirements.
        kind: Public task, graph, autonomous or nested asset entry.
    """
    db = SQLiteDatabase(str(tmp_path / "assets.db"))

    async def inventory():
        return {
            "profiles": [
                {
                    "id": "default",
                    "runner_type": runner,
                    "runner_config": {
                        "coze_api_key": "controlled-test-key",
                        "bot_id": "controlled-bot",
                        "dify_api_type": "chat",
                        "dashscope_app_id": "controlled-app",
                        "dashscope_api_key": "controlled-test-key",
                    },
                },
                {"id": "local"},
            ],
            "models": [{"id": "model-a"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=inventory)
    definition = {"name": "Researcher", "prompt": "Check evidence."}
    if declaration is not None:
        definition["runner_capabilities"] = declaration
    role = await roles.save_role("alice", {"definition": definition})
    calls = []

    async def deliver(session_id, text, context=None, **kwargs):
        calls.append(text)
        return kwargs["message_id"]

    async def collect(session_id, message_id, member_id):
        return "Ordinary answer.", [{"type": "plain", "text": "Ordinary answer."}]

    service = WorkflowService(
        db,
        roles,
        ports_factory=lambda owner: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda sid: False,
            emit=lambda e: None,
        ),
    )
    try:
        local = await roles.save_role(
            "alice",
            {
                "definition": {"name": "Coordinator", "prompt": "Coordinate evidence."},
                "installation": {
                    "profile_id": "local",
                    "bindings": {"model": "model-a"},
                },
            },
        )
        ref = {"role_id": role["role_id"], "role_version": 1}
        local_ref = {"role_id": local["role_id"], "role_version": 1}
        task = {"name": "Research", **ref, "task": "Check evidence."}
        team = {
            "kind": "autonomous",
            "name": "Team",
            "coordinator": ref if kind == "coordinator" else local_ref,
            "allowed_roles": [local_ref] if kind == "coordinator" else [ref],
        }
        definition = (
            task
            if kind == "single"
            else {
                "name": "Graph",
                "tasks": [{"id": "research", "member": "research", **task}],
            }
            if kind == "graph"
            else team
        )
        if kind == "nested_team":
            child = await service.save_asset("alice", {"definition": team})
            definition = {
                "name": "Parent graph",
                "required_inputs": ["goal"],
                "tasks": [
                    {
                        "id": "team",
                        "name": "Child team",
                        "team": {"asset_id": child["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    }
                ],
            }
        elif kind == "nested_workflow":
            child = await service.save_asset("alice", {"definition": task})
            definition = {
                **team,
                "allowed_roles": [local_ref],
                "allowed_workflows": [
                    {
                        "asset_id": child["asset_id"],
                        "version": 1,
                        "result_task_id": "result",
                    }
                ],
            }
        asset = await service.save_asset(
            "alice",
            {"definition": definition},
        )
        readiness = await service.check_readiness("alice", asset["asset_id"], 1)
        error = None
        run = None
        try:
            run = await service.start_run(
                "alice",
                asset["asset_id"],
                {
                    "version": 1,
                    "inputs": {"goal": "Check evidence."}
                    if definition.get("kind") == "autonomous"
                    or definition.get("required_inputs")
                    else {},
                },
            )
        except RoleAssetError as exc:
            error = exc.fields
        if run is not None:
            for _ in range(200):
                run = await service.get_run("alice", run["run_id"])
                if run["status"] != "running":
                    break
                await asyncio.sleep(0.01)
        observed = {
            "readiness": readiness,
            "rejected": error,
            "calls": calls,
            "run": run,
        }
        (tmp_path / "admission.json").write_text(
            json.dumps(observed, indent=2), encoding="utf-8"
        )
        assert readiness["ready"] is False, observed
        assert (await roles.resolve_role("alice", role["role_id"], 1))["ready"] is True
        assert readiness["issues"] == error
        assert "task_result" in error[0]["message"]
        assert error[0]["path"] == (
            "definition.tasks.0.role_id"
            if kind == "graph"
            else "definition.coordinator.role_id"
            if kind == "coordinator"
            else "definition.allowed_roles.0.role_id"
            if kind in {"allowed", "nested_team"}
            else "definition.role_id"
        )
        assert calls == []
        assert (await service.list_runs("alice"))["runs"] == []
        assert await db.get_platform_sessions_by_creator("alice") == []
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.parametrize("runner", ["coze", "deerflow", "dify", "dashscope"])
def test_unsupported_frozen_task_is_blocked_after_process_loss(tmp_path, runner):
    """Keep old evidence readable while refusing incompatible frozen execution.

    Args:
        tmp_path: Isolated durable history and independent process observations.
        runner: Historical executor missing the intrinsic task result capability.
    """
    args = [
        sys.executable,
        "-m",
        "tests.agent_teams.frozen_capability_worker",
        str(tmp_path),
    ]
    environment = {**os.environ, "PYTHONUTF8": "1"}
    with (tmp_path / "first.log").open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [*args, "seed", runner],
            stdout=output,
            stderr=subprocess.STDOUT,
            env=environment,
        )
        try:
            deadline = time.monotonic() + 30
            while not (tmp_path / "kill-ready.json").exists():
                assert process.poll() is None, (tmp_path / "first.log").read_text(
                    encoding="utf-8"
                )
                assert time.monotonic() < deadline
                time.sleep(0.05)
        finally:
            process.kill()
            process.wait(timeout=10)
    restarted = subprocess.run(
        [*args, "recover", runner],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env=environment,
    )
    (tmp_path / "second.log").write_text(
        restarted.stdout + restarted.stderr, encoding="utf-8"
    )
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
    original = json.loads((tmp_path / "before.json").read_text(encoding="utf-8"))
    assert evidence["after"]["status"] == "blocked"
    assert evidence["after"]["snapshot"] == original["snapshot"]
    assert evidence["after"]["state"]["attempt_id"] == "attempt-original"
    assert evidence["after"]["recovery"]["boundary"] == "remote_running"
    assert evidence["after"]["recovery"]["available"] is False
    assert "task_result" in evidence["after"]["recovery"]["reason"]
    assert evidence["after"]["result"] is None
    assert evidence["error"]["status"] == 409
    assert evidence["error"]["fields"] == [
        {
            "path": "definition.role_id",
            "code": "INCOMPATIBLE",
            "message": f"{'dify (chat)' if runner == 'dify' else runner} does not support task_result",
        }
    ]
    assert not (tmp_path / "dispatch.jsonl").exists()


@pytest.mark.asyncio
async def test_import_derives_result_protocol_even_when_manifest_omits_it(tmp_path):
    """Keep import candidates and pending nested assets consistent with launch.

    Args:
        tmp_path: Isolated durable source and target installations.
    """
    db = SQLiteDatabase(str(tmp_path / "import.db"))
    catalog = {
        "profiles": [
            {
                "id": "default",
                "runner_type": "dify",
                "runner_config": {"dify_api_type": "workflow"},
            }
        ],
        "models": [],
        "personas": [],
        "tools": [],
        "skills": [],
        "knowledge_bases": [],
    }

    async def inventory():
        return catalog

    roles = RoleAssetService(db, catalog_loader=inventory)
    service = WorkflowService(
        db, roles, ports_factory=lambda owner: pytest.fail("Import must not dispatch")
    )
    try:
        role = await roles.save_role(
            "alice", {"definition": {"name": "Remote", "prompt": "Check evidence."}}
        )
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Evidence",
                    "role_id": role["role_id"],
                    "role_version": 1,
                    "task": "Check evidence.",
                }
            },
        )
        package = await service.export_asset("alice", asset["asset_id"], 1)
        dependency = package["roles"][0]["dependencies"][0]
        assert dependency["capabilities"] == ["task_result"]
        untrusted = copy.deepcopy(package)
        untrusted["roles"][0]["dependencies"][0]["capabilities"] = []
        catalog["profiles"][0]["runner_config"]["dify_api_type"] = "chat"
        key = f"{role['role_id']}:1"
        payload = {
            "package": untrusted,
            "installations": {key: {"profile_id": "default"}},
        }
        preview = await service.preview_import("bob", payload)
        assert preview["ready"] is False
        assert preview["roles"][0]["dependencies"][0]["capabilities"] == ["task_result"]
        assert preview["roles"][0]["dependencies"][0]["candidates"] == []
        installed = await service.import_asset("bob", {**payload, "save_pending": True})
        readiness = await service.check_readiness(
            "bob", installed["asset"]["asset_id"], 1
        )
        assert readiness["ready"] is False
        with pytest.raises(RoleAssetError):
            await service.start_run(
                "bob", installed["asset"]["asset_id"], {"version": 1}
            )
        assert (await service.list_runs("bob"))["runs"] == []
        assert await db.get_platform_sessions_by_creator("bob") == []
    finally:
        await service.shutdown()
        await db.engine.dispose()
