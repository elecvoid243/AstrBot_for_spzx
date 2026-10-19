"""Persist applicable package contracts through real execution and recovery."""

import asyncio
import copy

import pytest

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [False, True])
async def test_scoped_contract_rechecks_and_recovers_frozen_context(
    workflow_env, tmp_path, required
):
    """Keep the original contract and binding when current versions are changed.

    Args:
        workflow_env: Public persistent services with external model ports.
        tmp_path: Directory containing the SQLite database reopened for recovery.
        required: Restore the old child contract instead of using an unrelated asset.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "portable-model"
    tool = {
        "id": "tool-a",
        "fingerprint": "portable-evidence",
        "capabilities": ["evidence"],
    }
    env.inventory["tools"] = [tool]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Evidence reader",
                "prompt": "Read evidence",
                "tools": [],
            },
            "installation": {"bindings": {"model": "model-a"}},
        },
    )
    source = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Evidence",
                "task": "Read evidence",
                "role_id": role["role_id"],
                "role_version": 1,
            }
        },
    )
    package = await env.service.export_asset("alice", source["asset_id"], 1)
    package["format_version"] = 2
    package["roles"][0]["dependencies"].append(
        {
            "slot": "prerequisite",
            "kind": "tools",
            "fingerprint": "portable-evidence",
            "capabilities": ["evidence"],
            "logical_slots": ["evidence_source"],
            "uses": {f"{source['asset_id']}:1": ["evidence_source"]},
        }
    )
    child = (await env.service.import_asset("bob", {"package": package}))["asset"]
    original = await env.service.export_asset("bob", child["asset_id"], 1)
    upgraded = copy.deepcopy(package)
    upgraded["version"] = 2
    upgraded["roles"][0]["dependencies"] = [
        dep for dep in upgraded["roles"][0]["dependencies"] if dep["kind"] != "tools"
    ]
    newer = (await env.service.import_asset("bob", {"package": upgraded}))["asset"]
    selected = await env.service.save_asset(
        "bob",
        {
            "definition": {**child["definition"], "name": "Selected role context"},
            **({"expected_version": newer["version"]} if required else {}),
        },
        child["asset_id"] if required else None,
    )
    if not required:
        env.inventory["tools"] = []
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    ports = env.service.ports_factory("bob")

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        """Save one real Attempt checkpoint before its next resource boundary.

        Args:
            session_id: Frozen member session identity.
            text: Submitted task text.
            context: Normal execution context.
            execution_token: Authorized execution binding.
            message_id: Current delivery identity.

        Returns:
            The original delivery identity after execution permission is checked.
        """
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        if binding.checkpoint is None:
            calls.append(message_id)
            await binding.save_checkpoint(
                {
                    "phase": "response",
                    "invocation_id": "scoped-evidence-call",
                    "model_calls": 1,
                    "steps_used": 1,
                    "messages": [],
                    "tools": [],
                    "response": {"role": "assistant", "completion_text": "Evidence"},
                }
            )
            entered.set()
            await release.wait()
        else:
            assert binding.checkpoint["invocation_id"] == "scoped-evidence-call"
        await binding.admit_work()
        return message_id

    ports.deliver = deliver
    service = WorkflowService(env.db, env.roles, ports_factory=lambda username: ports)
    reopened_db = None
    try:
        run = await service.start_run(
            "bob", selected["asset_id"], {"version": selected["version"]}
        )
        await asyncio.wait_for(entered.wait(), 2)
        before = await service.get_run("bob", run["run_id"])
        frozen = copy.deepcopy(before["snapshot"])
        if required:
            env.inventory["tools"] = []
        else:
            env.inventory["models"][0]["fingerprint"] = "incompatible"
        release.set()
        for _ in range(200):
            blocked = await service.get_run("bob", run["run_id"])
            if blocked["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert blocked["status"] == "blocked", blocked
        assert blocked["snapshot"] == frozen
        assert blocked["recovery"]["available"]
        assert len(calls) == 1
        if required:
            env.inventory["tools"] = [{**tool, "id": "replacement-tool"}]
            source_role = original["roles"][0]
            choices = {
                f"{source_role['role_id']}:{source_role['version']}": {
                    "profile_id": "default",
                    "bindings": {
                        dep["slot"]: "model-a"
                        if dep["kind"] == "models"
                        else "replacement-tool"
                        for dep in source_role["dependencies"]
                        if dep["kind"] != "profiles"
                    },
                }
            }
            await service.import_asset(
                "bob", {"package": original, "installations": choices}
            )
            assert (
                await service.check_readiness(
                    "bob", selected["asset_id"], selected["version"]
                )
            )["ready"]
            await service.save_asset(
                "bob",
                {
                    "definition": newer["definition"],
                    "expected_version": selected["version"],
                },
                child["asset_id"],
            )
        await service.shutdown()
        await env.db.engine.dispose()
        reopened_db = SQLiteDatabase(str(tmp_path / "workflows.db"))
        service = WorkflowService(
            reopened_db,
            RoleAssetService(reopened_db, catalog_loader=env.roles.catalog_loader),
            ports_factory=lambda username: ports,
        )
        env.inventory["models"][0]["fingerprint"] = "portable-model"
        legacy_role = copy.deepcopy(frozen["role"])
        legacy_role.pop("package_dependencies")
        with pytest.raises(RoleAssetError):
            await service.roles.validate_frozen_role(legacy_role)
        if required:
            await service.recover_run("bob", run["run_id"])
            for _ in range(200):
                still_blocked = await service.get_run("bob", run["run_id"])
                if still_blocked["status"] != "running":
                    break
                await asyncio.sleep(0.01)
            assert still_blocked["status"] == "blocked", still_blocked
            assert still_blocked["snapshot"] == frozen
            assert len(calls) == 1
            env.inventory["tools"].append(tool)
        await service.recover_run("bob", run["run_id"])
        for _ in range(200):
            recovered = await service.get_run("bob", run["run_id"])
            if recovered["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert recovered["status"] == "completed", recovered
        assert recovered["snapshot"] == frozen
        assert recovered["state"]["attempt_id"] == before["state"]["attempt_id"]
        assert len(calls) == 1
        assert await service.export_asset("bob", child["asset_id"], 1) == original
    finally:
        release.set()
        await service.shutdown()
        if reopened_db is not None:
            await reopened_db.engine.dispose()
