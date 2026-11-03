"""Explicit coordinator artifact handoffs retain permissions and exact versions."""

import asyncio
import json

import pytest
from sqlalchemy import event as sql_event
from sqlalchemy.orm import Session

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.db.po import AgentWorkflowEvent
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize("pre_read", [True, False])
@pytest.mark.parametrize("source_location", ["pinned", "member"])
async def test_coordinator_hands_selected_pinned_text_to_independent_child(
    workflow_env, pre_read, source_location
):
    """Deliver selected v1 text while denying the child the complete artifact.

    Args:
        workflow_env: Public services backed by real SQLite persistence.
        pre_read: Whether the coordinator reads before explicitly handing off.
        source_location: A pinned initial input or an authorized member output.
    """
    env = workflow_env
    report = "Original complete evidence\n" * 1000 + "COMPLETE_END"
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                **env.asset["definition"],
                "tasks": [
                    {
                        **env.asset["definition"]["tasks"][0],
                        "output_contract": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "files": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {"file_id": {"type": "string"}},
                                        "required": ["file_id"],
                                    },
                                },
                            },
                            "required": ["text", "files"],
                        },
                    }
                ],
            }
        },
    )
    child_asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                **env.asset["definition"],
                "required_inputs": ["evidence"],
                "tasks": [
                    {
                        **env.asset["definition"]["tasks"][0],
                        "task": "Read {evidence}",
                        "inputs": {"evidence": {"input": "evidence"}},
                    }
                ],
            }
        },
    )
    bindings, delivered, receipts, child_reads = {}, {}, [], []
    source_ref = None
    original = None

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        """Bind the scripted external response to the real task execution.

        Args:
            session_id: Actual member session receiving the request.
            text: Frozen task text.
            context: Explicit runtime framing.
            execution_token: Public token identifying the actual binding.
            message_id: Persisted request identity.

        Returns:
            Original request identity acknowledged by the external boundary.
        """
        bindings[session_id] = AgentTeamExecutionRegistry.resolve(execution_token)
        delivered[session_id] = text
        return message_id

    async def collect(session_id, message_id, member_id):
        """Exercise source submission, coordinator handoff and child consumption.

        Args:
            session_id: Actual isolated member session.
            message_id: Persisted request being answered.
            member_id: Stable member identity.

        Returns:
            Completion text and external response parts.
        """
        nonlocal original, source_ref
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        caller = await env.service.get_run("alice", bindings[session_id].run_id)
        if (
            source_ref is None
            or caller["snapshot"]["definition"]["name"] == "Same-scope evidence"
        ):
            file = json.loads(
                await tools["store_task_file"].call(
                    None,
                    filename="evidence.txt",
                    content=report,
                )
            )
            original = {"text": report, "files": [{"file_id": file["file_id"]}]}
            await tools["submit_task_result"].call(
                None, result_json=json.dumps(original)
            )
        elif caller["snapshot"].get("coordinator"):
            if source_location == "member":
                member = json.loads(
                    await tools["team_manage"].call(
                        None,
                        action="create_member",
                        request_id="source-member",
                        preset_id="source",
                        name="Evidence source",
                    )
                )
                dispatched = json.loads(
                    await tools["team_manage"].call(
                        None,
                        action="dispatch",
                        request_id="source-task",
                        member_id=member["member_id"],
                        name="Same-scope evidence",
                        task="Produce complete evidence",
                        output_contract=asset["definition"]["tasks"][0][
                            "output_contract"
                        ],
                    )
                )
                observed = json.loads(
                    await tools["team_manage"].call(
                        None,
                        action="wait",
                        request_id="wait-source",
                        task_ids=[dispatched["task_id"]],
                    )
                )
                source_ref = observed["tasks"][0]["result_ref"]
                assert source_ref is not None, observed
            for _ in range(2 if pre_read else 1):
                read = json.loads(
                    await tools["read_task_artifact"].call(None, **source_ref)
                )
                assert read["content"] == original
                file = json.loads(
                    await tools["read_task_artifact"].call(
                        None, **source_ref, file_id=original["files"][0]["file_id"]
                    )
                )
                assert file["content"] == report
            await env.service.revise_artifact(
                "alice",
                source_ref["artifact_id"],
                {
                    "expected_version": 1,
                    "request_id": "source-v2",
                    "content": {**original, "text": "EDITED LATER"},
                },
            )
            decision = {
                "action": "call_workflow",
                "request_id": "handoff",
                "name": "Read v1",
                "asset_id": child_asset["asset_id"],
                "version": 1,
                "inputs": {"evidence": read["content"]["text"]},
                "input_artifacts": [source_ref],
            }
            for _ in range(2):
                response = await tools["team_manage"].call(None, **decision)
                assert not response.startswith("Error:"), response
                receipt = json.loads(response)
                receipts.append(receipt)
            waited = json.loads(
                await tools["team_manage"].call(
                    None,
                    action="wait",
                    request_id="wait-handoff",
                    task_ids=[receipts[0]["task_id"]],
                )
            )
            assert waited["tasks"][0]["status"] == "completed", waited["tasks"][0][
                "reason"
            ]
            assert waited["tasks"][0]["result"] == {"text": report}
            await tools["submit_task_result"].call(
                None, text="Delivered exact original"
            )
        else:
            for _ in range(2):
                read = await tools["read_task_artifact"].call(None, **source_ref)
                file = await tools["read_task_artifact"].call(
                    None, **source_ref, file_id=original["files"][0]["file_id"]
                )
                child_reads.append((read, file))
                assert read.startswith("Error") and file.startswith("Error")
            assert delivered[session_id] == f"Read {report}"
            accepted = await tools["submit_task_result"].call(None, text=report)
            assert accepted == "Result accepted.", accepted
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda _: False,
        emit=lambda _: None,
    )
    if source_location == "pinned":
        source = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
        source = await completed_run(env.service, source["run_id"])
        source_ref = source["result_ref"]
    else:
        source_ref = {}
    source_role = await env.roles.save_role(
        "alice",
        {
            "definition": {**env.role["definition"], "name": "Evidence source"},
            "installation": env.role["installation"],
        },
    )
    role = {"role_id": env.role["role_id"], "role_version": 1}
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Explicit handoff",
                "coordinator": {"id": "coordinator", **role},
                "allowed_roles": [
                    {
                        "id": "source",
                        "role_id": source_role["role_id"],
                        "role_version": 1,
                    }
                ],
                "max_parallel": 1,
                "allowed_workflows": [
                    {
                        "asset_id": child_asset["asset_id"],
                        "version": 1,
                    }
                ],
            }
        },
    )
    run = await env.service.start_run(
        "alice",
        team["asset_id"],
        {
            "version": 1,
            "inputs": {"goal": "PRIVATE_PARENT_GOAL"},
            "input_artifacts": [source_ref] if source_location == "pinned" else [],
        },
    )
    current = await completed_run(env.service, run["run_id"])
    assert current["status"] == "completed", [
        (task["status"], task["state"].get("reason"), task["state"].get("issues"))
        for scope in current["scopes"]
        for task in scope["tasks"]
    ]
    assert receipts[0] == receipts[1]
    assert len(child_reads) == 2 and len(current["scopes"]) == 1
    coordinator = next(
        task for task in current["tasks"] if task["snapshot"].get("coordinator")
    )
    call = next(
        task
        for task in current["tasks"]
        if task["snapshot"]["engine"] == "workflow_call_v2"
    )
    child = current["scopes"][0]["tasks"][0]
    assert current["scopes"][0]["snapshot"]["input_artifacts"] == [source_ref]
    assert child["snapshot"]["input_artifacts"] == []
    assert (
        child["snapshot"]["member"]["conversation_id"]
        != coordinator["snapshot"]["member"]["conversation_id"]
    )
    assert (
        call["result"] == {"text": report}
        and call["state"]["child_result_ref"] == child["result_ref"]
    )
    assert coordinator["read_artifacts"] == [source_ref]
    assert sum(event["kind"] == "artifact.read" for event in coordinator["events"]) == 1
    assert child["read_artifacts"] == []
    assert sum(event["kind"] == "child.returned" for event in current["events"]) == 1
    assert (await env.service.read_artifact("alice", source_ref["artifact_id"], 2))[
        "content"
    ]["text"] == "EDITED LATER"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rejection",
    [
        "undeclared",
        "version",
        "other_scope",
        "other_owner",
        "stale_attempt",
        "stale_executor",
        "later_reference",
        "read_save_failure",
    ],
)
async def test_rejected_handoff_cannot_save_read_or_dependent_call(
    workflow_env, rejection
):
    """Keep rejected references and failed read persistence outside execution.

    Args:
        workflow_env: Public services and actual SQLite transactions.
        rejection: Permission, execution ownership or storage failure to exercise.
    """
    env = workflow_env
    source = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    source = await completed_run(env.service, source["run_id"])
    ref = source["result_ref"]
    selected = ref
    if rejection == "version":
        selected = await env.service.revise_artifact(
            "alice",
            ref["artifact_id"],
            {
                "expected_version": 1,
                "request_id": "unshared-v2",
                "content": {"text": "Unshared version"},
            },
        )
    elif rejection in {"other_scope", "later_reference"}:
        other = await env.service.start_run(
            "alice", env.asset["asset_id"], {"version": 1}
        )
        selected = (await completed_run(env.service, other["run_id"]))["result_ref"]
    elif rejection == "other_owner":
        bob_role = await env.roles.save_role(
            "bob",
            {
                "definition": env.role["definition"],
                "installation": env.role["installation"],
            },
        )
        bob_asset = await env.service.save_asset(
            "bob",
            {
                "definition": {
                    **env.asset["definition"],
                    "tasks": [
                        {
                            **env.asset["definition"]["tasks"][0],
                            "role_id": bob_role["role_id"],
                        }
                    ],
                }
            },
        )
        other = await env.service.start_run(
            "bob", bob_asset["asset_id"], {"version": 1}
        )
        for _ in range(200):
            other = await env.service.get_run("bob", other["run_id"])
            if other["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert other["status"] == "completed"
        selected = other["result_ref"]
    role = {"role_id": env.role["role_id"], "role_version": 1}
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Restricted handoff",
                "coordinator": {"id": "coordinator", **role},
                "allowed_roles": [{"id": "writer", **role}],
                "max_parallel": 1,
                "allowed_workflows": [
                    {
                        "asset_id": env.asset["asset_id"],
                        "version": 1,
                    }
                ],
            }
        },
    )
    ready, release = asyncio.Event(), asyncio.Event()
    delivered = []

    async def collect(session_id, message_id, member_id):
        """Hold external execution while public decisions exercise rejection.

        Args:
            session_id: Active member session.
            message_id: Original request identity.
            member_id: Stable caller identity.

        Returns:
            External completion text and parts after the test releases execution.
        """
        delivered.append(session_id)
        ready.set()
        await release.wait()
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=lambda *args, **kwargs: asyncio.sleep(
            0, result=kwargs.get("message_id")
        ),
        collect=collect,
        is_busy=lambda _: False,
        emit=lambda _: None,
    )
    run = await env.service.start_run(
        "alice",
        team["asset_id"],
        {
            "version": 1,
            "inputs": {"goal": "Explicit permission only"},
            "input_artifacts": [] if rejection == "undeclared" else [ref],
        },
    )
    await asyncio.wait_for(ready.wait(), 2)
    caller = (await env.service.get_run("alice", run["run_id"]))["tasks"][0]
    decision = {
        "action": "call_workflow",
        "request_id": "handoff",
        "name": "Read only permitted input",
        "asset_id": env.asset["asset_id"],
        "version": 1,
        "input_artifacts": [ref, selected]
        if rejection == "later_reference"
        else [selected],
    }

    def reject_read_flush(session, flush_context, instances):
        """Fail actual storage before any dependent handoff can commit.

        Args:
            session: Real SQLAlchemy transaction being flushed.
            flush_context: SQLAlchemy's pending storage context.
            instances: Optional explicitly selected flush objects.
        """
        if any(
            isinstance(row, AgentWorkflowEvent)
            and row.kind == "artifact.read"
            and row.run_id == caller["run_id"]
            for row in session.new
        ):
            raise RuntimeError("Injected read persistence failure")

    attached = rejection == "read_save_failure"
    if attached:
        sql_event.listen(Session, "before_flush", reject_read_flush)
    try:
        with pytest.raises(RuntimeError if attached else RoleAssetError) as failed:
            await env.service.manage_team(
                "alice",
                caller["run_id"],
                "stale-attempt"
                if rejection == "stale_attempt"
                else caller["state"]["attempt_id"],
                "stale-executor"
                if rejection == "stale_executor"
                else caller["state"]["execution_id"],
                decision,
            )
        if not attached:
            assert failed.value.status == (
                404
                if rejection == "other_owner"
                else 409
                if rejection == "stale_executor"
                else 403
            )
        else:
            assert str(failed.value) == "Injected read persistence failure"
        observed = await env.service.get_run("alice", run["run_id"])
        assert len(observed["tasks"]) == 1 and observed["scopes"] == []
        assert observed["tasks"][0]["read_artifacts"] == []
        assert not any(
            event["kind"] == "team.call_workflow" for event in observed["events"]
        )
        assert delivered == [caller["snapshot"]["member"]["session_id"]]
        if attached:
            sql_event.remove(Session, "before_flush", reject_read_flush)
            attached = False
            receipts = [
                await env.service.manage_team(
                    "alice",
                    caller["run_id"],
                    caller["state"]["attempt_id"],
                    caller["state"]["execution_id"],
                    decision,
                )
                for _ in range(2)
            ]
            assert receipts[0] == receipts[1]
            observed = await env.service.get_run("alice", run["run_id"])
            assert len(observed["tasks"]) == 2
            assert observed["tasks"][0]["read_artifacts"] == [ref]
            assert (
                sum(
                    event["kind"] == "team.call_workflow"
                    for event in observed["events"]
                )
                == 1
            )
    finally:
        if attached:
            sql_event.remove(Session, "before_flush", reject_read_flush)
        await env.service.control_run(
            "alice", run["run_id"], {"action": "cancel", "request_id": "cleanup"}
        )
        release.set()


@pytest.mark.asyncio
async def test_sibling_scope_output_cannot_be_handed_to_workflow(workflow_env):
    """Reject an owner's visible sibling artifact outside the coordinator scope.

    Args:
        workflow_env: Public workflow service, actual scopes and bound Agent tools.
    """
    env = workflow_env
    role = {"role_id": env.role["role_id"], "role_version": 1}
    teams = []
    for name in ("Producer coordinator", "Consumer coordinator"):
        teams.append(
            await env.service.save_asset(
                "alice",
                {
                    "definition": {
                        "kind": "autonomous",
                        "name": name,
                        "coordinator": {"id": "coordinator", **role},
                        "allowed_roles": [{"id": "writer", **role}],
                        "allowed_workflows": [
                            {
                                "asset_id": env.asset["asset_id"],
                                "version": 1,
                            }
                        ],
                    }
                },
            )
        )
    graph = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Sibling scopes",
                "result_task_id": "consumer",
                "required_inputs": ["goal"],
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": name,
                        "name": name,
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    }
                    for name, team in zip(("producer", "consumer"), teams)
                ],
                "edges": [{"source": "producer", "target": "consumer"}],
            }
        },
    )
    bindings, rejections = {}, []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        """Keep public bindings for each actual sibling-scope executor.

        Args:
            session_id: Isolated member session.
            text: Frozen task text.
            context: Explicit runtime framing.
            execution_token: Public runtime binding token.
            message_id: Persisted request identity.

        Returns:
            Original acknowledged request identity.
        """
        bindings[session_id] = AgentTeamExecutionRegistry.resolve(execution_token)
        return message_id

    async def collect(session_id, message_id, member_id):
        """Submit one sibling and reject implicit access from the other.

        Args:
            session_id: Actual sibling member session.
            message_id: Persisted request identity.
            member_id: Stable member identity.

        Returns:
            External completion text and parts.
        """
        caller = await env.service.get_run("alice", bindings[session_id].run_id)
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        if caller["snapshot"]["definition"]["name"] == "Consumer coordinator":
            root = await env.service.get_run("alice", caller["control"]["root_run_id"])
            sibling = next(
                scope["tasks"][0]
                for scope in root["scopes"]
                if scope["snapshot"]["definition"]["name"] == "Producer coordinator"
            )
            ref = sibling["result_ref"]
            assert (await env.service.read_artifact("alice", **ref))["content"] == {
                "text": "Completed"
            }
            rejected = await tools["team_manage"].call(
                None,
                action="call_workflow",
                request_id="sibling",
                name="Unauthorized sibling handoff",
                asset_id=env.asset["asset_id"],
                version=1,
                input_artifacts=[ref],
            )
            assert "Artifact version is not a pinned task input" in rejected
            rejections.append(rejected)
        await tools["submit_task_result"].call(None, text="Completed")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda _: False,
        emit=lambda _: None,
    )
    run = await env.service.start_run(
        "alice",
        graph["asset_id"],
        {"version": 1, "inputs": {"goal": "No implicit sibling sharing"}},
    )
    current = await completed_run(env.service, run["run_id"])
    assert current["status"] == "completed", current
    assert len(rejections) == 1 and len(current["scopes"]) == 2
    assert not any(event["kind"] == "team.call_workflow" for event in current["events"])
