"""Returned child files retain exact artifact revision and visibility semantics."""

import asyncio
import json

import pytest

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_returned_file_revision_preserves_authorization_inputs_and_approval(
    workflow_env,
):
    """Retain handed-off files without granting access to other stored files.

    Args:
        workflow_env: Public services with SQLite and an execution adapter.
    """
    env = workflow_env
    contract = {
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
    }
    worker = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "File worker",
                "prompt": "Produce evidence",
                "provider_id": "model-a",
            },
            "installation": {"profile_id": "default"},
        },
    )
    role = {"role_id": env.role["role_id"], "role_version": 1}
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "File team",
                "coordinator": {"id": "coordinator", **role},
                "allowed_roles": [
                    {"id": "worker", "role_id": worker["role_id"], "role_version": 1}
                ],
                "max_parallel": 1,
                "output_contract": contract,
            }
        },
    )
    parent = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Review child files",
                "result_task_id": "consumer",
                "required_inputs": ["goal"],
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": name,
                        "name": name,
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                        "output_contract": contract,
                    }
                    for name in ["call", "sibling"]
                ]
                + [
                    {
                        "id": "approval",
                        "name": "Approve original",
                        "human": {
                            "kind": "approval",
                            "question": "Approve v1?",
                            "artifact_task_id": "call",
                            "artifact_version": 1,
                        },
                    },
                    {
                        "id": "consumer",
                        "name": "Read original",
                        "member": "reader",
                        **role,
                        "task": "Read {report}",
                        "inputs": {"report": {"task_id": "call", "field": "text"}},
                    },
                ],
                "edges": [
                    {"source": "call", "target": "approval"},
                    {"source": "call", "target": "consumer"},
                    {"source": "sibling", "target": "consumer"},
                ],
            }
        },
    )
    bindings, files, reads, deliveries = {}, {}, [], []
    coordinator_results: asyncio.Queue[str] = asyncio.Queue()
    consumer_ready, release = asyncio.Event(), asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        """Observe the public execution identity at dispatch.

        Args:
            session_id: Isolated member conversation session.
            text: Literal task input.
            context: Frozen prompt supplied by the executor.
            execution_token: Bound public execution identity.
            message_id: Persisted delivery identity.

        Returns:
            The original delivery identity.
        """
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        bindings[session_id] = binding
        deliveries.append(binding.run_id)
        return message_id

    async def collect(session_id, message_id, member_id, owner):
        """Produce files through runtime tools and hold a pinned consumer.

        Args:
            session_id: Member session selecting its registered runtime tools.
            message_id: Original dispatched message.
            member_id: Actual execution member.
            owner: Initiating username, including the independent Bob run.

        Returns:
            A completed external response after explicit result submission.
        """
        binding = bindings[session_id]
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        if "team_manage" in tools:
            member = json.loads(
                await tools["team_manage"].call(
                    None,
                    action="create_member",
                    request_id="create-worker",
                    preset_id="worker",
                    name="File worker",
                )
            )
            task = json.loads(
                await tools["team_manage"].call(
                    None,
                    action="dispatch",
                    request_id="dispatch-worker",
                    member_id=member["member_id"],
                    name="Evidence",
                    task="Create evidence",
                )
            )
            await tools["team_manage"].call(
                None,
                action="wait",
                request_id="wait-worker",
                task_ids=[task["task_id"]],
            )
            returned = await env.service.store_file(
                "alice",
                binding.run_id,
                binding.turn_id,
                "report.txt",
                b"Complete original file COMPLETE_END",
            )
            unused = await env.service.store_file(
                "alice",
                binding.run_id,
                binding.turn_id,
                "unused.txt",
                b"Unsubmitted coordinator file",
            )
            files[binding.run_id] = [returned, unused]
            assert (
                await tools["submit_task_result"].call(
                    None,
                    result_json=json.dumps(
                        {
                            "text": "Original caption",
                            "files": [{"file_id": returned["file_id"]}],
                        }
                    ),
                )
                == "Result accepted."
            )
            coordinator_results.put_nowait(binding.run_id)
        else:
            if binding.node_id == "consumer":
                consumer_ready.set()
                await release.wait()
                current = await env.service.get_run("alice", binding.run_id)
                ref = current["snapshot"]["input_artifacts"][0]
                reads.append(
                    json.loads(await tools["read_task_artifact"].call(None, **ref))
                )
                assert "Error" in await tools["read_task_artifact"].call(
                    None, artifact_id=ref["artifact_id"], version=2
                )
            elif binding.node_id is not None:
                files[binding.run_id] = [
                    await env.service.store_file(
                        owner,
                        binding.run_id,
                        binding.turn_id,
                        "member.txt",
                        b"Unreturned member file",
                    )
                ]
            assert (
                await tools["submit_task_result"].call(None, text="Complete evidence")
                == "Result accepted."
            )
        return "Done", []

    env.service.ports_factory = lambda owner: TeamPorts(
        deliver=deliver,
        collect=lambda sid, mid, member: collect(sid, mid, member, owner),
        is_busy=lambda _: False,
        emit=lambda _: None,
    )
    started = await env.service.start_run(
        "alice",
        parent["asset_id"],
        {"version": 1, "inputs": {"goal": "Produce complete files"}},
    )
    # Root capacity serializes both nested teams. Bound each explicit submission
    # before timing consumer dispatch, so prerequisite work keeps its own deadline.
    submitted = {
        await asyncio.wait_for(coordinator_results.get(), 10) for _ in range(2)
    }
    await asyncio.wait_for(consumer_ready.wait(), 10)
    current = await env.service.get_run("alice", started["run_id"])
    assert len(submitted) == 2
    assert submitted == {
        task["run_id"]
        for scope in current["scopes"]
        for task in scope["tasks"]
        if task["node_id"] == "coordinator"
    }
    call = next(task for task in current["tasks"] if task["node_id"] == "call")
    sibling = next(task for task in current["tasks"] if task["node_id"] == "sibling")
    scope = next(
        scope
        for scope in current["scopes"]
        if scope["snapshot"]["parent_run_id"] == call["run_id"]
    )
    coordinator = next(
        task for task in scope["tasks"] if task["node_id"] == "coordinator"
    )
    member = next(task for task in scope["tasks"] if task["node_id"] != "coordinator")
    artifact_id = call["result_ref"]["artifact_id"]
    original = await env.service.read_artifact("alice", artifact_id, 1)
    file_id = original["content"]["files"][0]["file_id"]
    assert (
        await env.service.get_file("alice", artifact_id, 1, file_id, download=False)
    )["content"] == b"Complete original file COMPLETE_END"
    assert current["todos"][0]["artifact_ref"] == {
        "artifact_id": artifact_id,
        "version": 1,
    }

    other = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    other_done = await completed_run(env.service, other["run_id"])
    other_leaf = next(
        task for task in other_done["tasks"] if task["node_id"] == "writer"
    )
    other_file = await env.service.store_file(
        "alice", other_leaf["run_id"], None, "other.txt", b"Other run file"
    )
    bob_role = await env.roles.save_role(
        "bob",
        {
            "definition": {"name": "Bob", "prompt": "Write", "provider_id": "model-a"},
            "installation": {"profile_id": "default"},
        },
    )
    bob_asset = await env.service.save_asset(
        "bob",
        {
            "definition": {
                "name": "Bob report",
                "result_task_id": "writer",
                "tasks": [
                    {
                        "id": "writer",
                        "name": "Writer",
                        "member": "writer",
                        "role_id": bob_role["role_id"],
                        "role_version": 1,
                        "task": "Write",
                    }
                ],
            }
        },
    )
    bob = await env.service.start_run("bob", bob_asset["asset_id"], {"version": 1})
    for _ in range(200):
        bob_done = await env.service.get_run("bob", bob["run_id"])
        if bob_done["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert bob_done["status"] == "completed", bob_done
    bob_leaf = next(task for task in bob_done["tasks"] if task["node_id"] == "writer")
    bob_file = await env.service.store_file(
        "bob", bob_leaf["run_id"], None, "bob.txt", b"Other owner file"
    )
    unused = files[coordinator["run_id"]][1]
    rejected = [
        files[member["run_id"]][0],
        sibling["result"]["files"][0],
        other_file,
        bob_file,
    ]
    for index, forbidden in enumerate(rejected):
        with pytest.raises(RoleAssetError) as invalid:
            await env.service.revise_artifact(
                "alice",
                artifact_id,
                {
                    "expected_version": 1,
                    "request_id": f"reject-{index}",
                    "content": {
                        **original["content"],
                        "files": [
                            {"file_id": file_id},
                            {"file_id": forbidden["file_id"]},
                        ],
                    },
                },
            )
        assert invalid.value.fields == [
            {
                "path": "result.files.1.file_id",
                "code": "NOT_READY",
                "message": "File is not ready in this task's managed storage",
            }
        ]
        with pytest.raises(RoleAssetError) as invisible:
            await env.service.get_file(
                "alice", artifact_id, 1, forbidden["file_id"], download=False
            )
        assert invisible.value.status == 404

    request = {
        "expected_version": 1,
        "request_id": "caption-v2",
        "content": {**original["content"], "text": "Corrected caption"},
    }
    revision = await env.service.revise_artifact("alice", artifact_id, request)
    assert revision == {"artifact_id": artifact_id, "version": 2}
    assert await env.service.revise_artifact("alice", artifact_id, request) == revision
    assert await env.service.read_artifact("alice", artifact_id, 1) == original
    for version in [1, 2]:
        assert (
            await env.service.get_file(
                "alice", artifact_id, version, file_id, download=False
            )
        )["content"] == b"Complete original file COMPLETE_END"
    for version in [1, 2]:
        with pytest.raises(RoleAssetError) as invisible:
            await env.service.get_file(
                "alice", artifact_id, version, unused["file_id"], download=False
            )
        assert invisible.value.status == 404
    added = await env.service.revise_artifact(
        "alice",
        artifact_id,
        {
            "expected_version": 2,
            "request_id": "add-unused-v3",
            "content": {
                **request["content"],
                "files": [{"file_id": file_id}, {"file_id": unused["file_id"]}],
            },
        },
    )
    assert added == {"artifact_id": artifact_id, "version": 3}
    assert (
        await env.service.get_file(
            "alice", artifact_id, 3, unused["file_id"], download=False
        )
    )["content"] == b"Unsubmitted coordinator file"
    with pytest.raises(RoleAssetError) as owner:
        await env.service.revise_artifact("bob", artifact_id, request)
    assert owner.value.status == 404
    await env.service.answer_todo(
        "alice",
        started["run_id"],
        current["todos"][0]["todo_id"],
        {
            "request_id": "approve-v1",
            "action": "approve",
            "artifact_ref": current["todos"][0]["artifact_ref"],
        },
    )
    release.set()
    done = await completed_run(env.service, started["run_id"])
    assert done["status"] == "completed", done
    assert done["todos"][0]["artifact_ref"] == {
        "artifact_id": artifact_id,
        "version": 1,
    }
    assert reads[0]["content"] == {"text": original["content"]["text"]}
    assert (
        deliveries.count(
            next(task for task in done["tasks"] if task["node_id"] == "consumer")[
                "run_id"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                event
                for event in done["events"]
                if event["kind"] == "artifact.revised"
                and event["run_id"] == artifact_id
            ]
        )
        == 2
    )
    assert (await env.service.get_file("alice", artifact_id, 1, file_id))[
        "content"
    ] == b"Complete original file COMPLETE_END"
    for version in [2, 3]:
        with pytest.raises(RoleAssetError) as approval:
            await env.service.get_file("alice", artifact_id, version, file_id)
        assert approval.value.status == 409
