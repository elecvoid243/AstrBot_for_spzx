"""Autonomous assets use the shared public asset and run boundary."""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts


@pytest.mark.asyncio
async def test_team_launch_freezes_allowed_roles_without_creating_unused_contexts(
    tmp_path,
):
    """Launch a pinned team with one coordinator and frozen member capabilities.

    Args:
        tmp_path: Isolated real database directory.
    """
    db = SQLiteDatabase(str(tmp_path / "team.db"))

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    coordinator = await roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Coordinator",
                "prompt": "Coordinate evidence",
                "provider_id": "model",
            },
            "installation": {"profile_id": "default"},
        },
    )
    writer = await roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Writer",
                "prompt": "Original evidence",
                "provider_id": "model",
                "max_instances": 2,
            },
            "installation": {"profile_id": "default"},
        },
    )
    blocked = asyncio.Event()

    async def deliver(*args, **kwargs):
        return kwargs["message_id"]

    async def collect(*args):
        await blocked.wait()
        return "Done", []

    service = WorkflowService(
        db,
        roles,
        ports_factory=lambda username: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda sid: False,
            emit=lambda event: None,
        ),
    )
    try:
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "kind": "autonomous",
                    "name": "Research team",
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
                    "max_members": 3,
                    "max_parallel": 1,
                    "max_tasks": 8,
                }
            },
        )
        run = await service.start_run(
            "alice",
            asset["asset_id"],
            {"version": 1, "inputs": {"goal": "Research energy"}},
        )
        assert len(run["tasks"]) == 1
        assert run["tasks"][0]["snapshot"]["coordinator"] is True
        assert (
            run["snapshot"]["allowed_roles"]["writer"]["effective"]["prompt"]
            == "Original evidence"
        )
        assert len(await db.get_platform_sessions_by_creator("alice")) == 1
        assert run["tasks"][0]["snapshot"]["task"] == "Research energy"
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest_asyncio.fixture
async def team_env(tmp_path):
    """Provide public services with real persistence and a controlled Agent boundary.

    Args:
        tmp_path: Isolated database directory.

    Yields:
        Team assets, public service and bound execution observations.
    """
    db = SQLiteDatabase(str(tmp_path / "team.db"))
    inventory = {
        "profiles": [{"id": "default"}],
        "models": [{"id": "model"}],
        "personas": [],
        "tools": [],
        "skills": [],
        "knowledge_bases": [],
    }

    async def catalog():
        return inventory

    roles = RoleAssetService(db, catalog_loader=catalog)
    coordinator = await roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Coordinator",
                "prompt": "Coordinate",
                "provider_id": "model",
                "max_steps": 20,
            },
            "installation": {"profile_id": "default"},
        },
    )
    writer = await roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Writer",
                "prompt": "Original writer",
                "provider_id": "model",
                "max_instances": 2,
                "max_parallel": 1,
                "max_steps": 4,
            },
            "installation": {"profile_id": "default"},
        },
    )
    observations = []
    framings = []
    bindings = {}
    script = None
    errors = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        bindings[session_id] = binding
        observations.append((binding, text))
        framings.append(context)
        return message_id

    async def collect(session_id, message_id, member_id):
        """Run the coordinator plan or submit evidence from an authorized member.

        Args:
            session_id: Isolated execution session containing the registered tools.
            message_id: Delivered task message identity supplied by the runtime.
            member_id: Runtime member identity supplied by the collection port.

        Returns:
            Collected reply and empty attachments after the task tool calls finish.
        """
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        try:
            if bindings[session_id].node_id == "coordinator":
                await env.script(tools, bindings[session_id])
            else:
                from astrbot.core.agent_teams.assets import RoleAssetError

                caller = await service.get_run("alice", bindings[session_id].run_id)
                with pytest.raises(RoleAssetError) as forbidden:
                    await service.manage_team(
                        "alice",
                        caller["run_id"],
                        caller["state"]["attempt_id"],
                        caller["state"]["execution_id"],
                        {
                            "action": "create_member",
                            "request_id": "member-attack",
                            "preset_id": "writer",
                            "name": "Unauthorized",
                        },
                    )
                assert forbidden.value.status == 403
                if getattr(env, "member_release", None) is not None:
                    env.member_started.set()
                    await env.member_release.wait()
                await tools["submit_task_result"].call(
                    None, text="Evidence " * 2000 + "COMPLETE_END"
                )
        except Exception as exc:
            errors.append(exc)
            raise
        return "Done", []

    service = WorkflowService(
        db,
        roles,
        ports_factory=lambda username: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda sid: False,
            emit=lambda event: None,
            reply_timeout=5,
        ),
    )
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Team",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": coordinator["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "writer", "role_id": writer["role_id"], "role_version": 1}
                ],
                "max_parallel": 1,
                "max_members": 3,
                "max_tasks": 8,
            }
        },
    )
    env = SimpleNamespace(
        db=db,
        roles=roles,
        service=service,
        asset=asset,
        writer=writer,
        coordinator=coordinator,
        inventory=inventory,
        observations=observations,
        framings=framings,
        bindings=bindings,
        script=script,
        errors=errors,
    )
    try:
        yield env
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_all_declared_inputs_reach_the_coordinator_and_remain_frozen(team_env):
    """Deliver literal declared values and retain them after an asset edit/reopen.

    Args:
        team_env: Public services, real SQLite and the external execution boundary.
    """
    import json

    env = team_env
    ready, release = asyncio.Event(), asyncio.Event()

    async def script(tools, binding):
        """Keep the coordinator live while its launch definition is edited.

        Args:
            tools: Tools provided to this coordinator execution.
            binding: Actual execution identity from the public collection port.
        """
        ready.set()
        await release.wait()
        await tools["submit_task_result"].call(None, text="Complete")

    env.script = script
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                **env.asset["definition"],
                "required_inputs": ["constraints", "goal", "notes"],
            }
        },
    )
    inputs = {
        "goal": "Research {constraints}.\nPreserve the literal {goal}.",
        "constraints": 'ONLY_USE_2025_DATA\nKeep {"year": 2025} and {notes}.',
        "notes": "First line.\n第二行 {constraints}\n  Preserve spacing.",
    }
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": inputs}
    )
    await asyncio.wait_for(ready.wait(), 5)
    framing = env.framings[0]
    assert "Declared run inputs (JSON):\n" in framing
    decoded, _ = json.JSONDecoder().raw_decode(
        framing.split("Declared run inputs (JSON):\n", 1)[1]
    )
    assert decoded == inputs
    assert env.observations[0][1] == inputs["goal"]
    await env.service.save_asset(
        "alice",
        {
            "definition": {
                **asset["definition"],
                "required_inputs": ["goal", "replacement"],
            },
            "expected_version": 1,
        },
        asset["asset_id"],
    )
    await env.service.shutdown()
    reopened = WorkflowService(env.db, env.roles)
    try:
        restored = await reopened.get_run("alice", run["run_id"])
        assert restored["snapshot"]["inputs"] == inputs
        assert restored["snapshot"]["team_definition"]["required_inputs"] == [
            "constraints",
            "goal",
            "notes",
        ]
        assert restored["tasks"][0]["snapshot"]["inputs"] == inputs
    finally:
        await reopened.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt_size", [16, 50_000])
async def test_team_overviews_preserve_member_identities_without_frozen_prompts(
    team_env, prompt_size
):
    """Keep member/relationship metadata lightweight while retaining full details.

    Args:
        team_env: Public services with real persisted members and tasks.
        prompt_size: Small or large role prompt, without changing the overview contract.
    """
    import json

    env = team_env
    prompts = {
        env.coordinator["role_id"]: "COORDINATOR_PRIVATE_PROMPT_" + "C" * prompt_size,
        env.writer["role_id"]: "WRITER_PRIVATE_PROMPT_" + "W" * prompt_size,
    }
    for role in [env.coordinator, env.writer]:
        await env.roles.save_role(
            "alice",
            {
                "definition": {
                    **role["definition"],
                    "prompt": prompts[role["role_id"]],
                },
                "installation": role["installation"],
                "expected_version": 1,
            },
            role["role_id"],
        )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                **env.asset["definition"],
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.coordinator["role_id"],
                    "role_version": 2,
                },
                "allowed_roles": [
                    {
                        "id": "writer",
                        "role_id": env.writer["role_id"],
                        "role_version": 2,
                    }
                ],
            }
        },
    )
    ready, release = asyncio.Event(), asyncio.Event()

    async def script(tools, binding):
        """Create an idle member and two dependent tasks before releasing capacity.

        Args:
            tools: Tools bound to the actual coordinator execution.
            binding: Public execution identity from the collection boundary.
        """
        members = []
        for index in range(2):
            members.append(
                json.loads(
                    await tools["team_manage"].call(
                        None,
                        action="create_member",
                        request_id=f"member-{index}",
                        preset_id="writer",
                        name=f"Writer {index}",
                    )
                )["member_id"]
            )
        tasks = []
        for index in range(2):
            tasks.append(
                json.loads(
                    await tools["team_manage"].call(
                        None,
                        action="dispatch",
                        request_id=f"task-{index}",
                        member_id=members[0],
                        name=f"Evidence {index}",
                        task="Gather evidence",
                        dependencies=tasks[:],
                    )
                )["task_id"]
            )
        ready.set()
        await release.wait()
        await tools["team_manage"].call(
            None, action="wait", request_id="wait", task_ids=tasks
        )
        await tools["submit_task_result"].call(None, text="Complete")

    env.script = script
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"goal": "Research"}}
    )
    await asyncio.wait_for(ready.wait(), 5)
    detailed = await env.service.get_run("alice", run["run_id"])
    overview = await env.service.get_run("alice", run["run_id"], include_details=False)
    listed = (await env.service.list_runs("alice"))["runs"][0]
    for lightweight in [overview, listed]:
        assert len(lightweight["members"]) == 3
        assert all(
            "system_prompt" not in member for member in lightweight["members"].values()
        )
        assert all(prompt not in json.dumps(lightweight) for prompt in prompts.values())
        assert {
            key: (
                member["member_id"],
                member["name"],
                member["session_id"],
                member["conversation_id"],
            )
            for key, member in lightweight["members"].items()
        } == {
            key: (
                member["member_id"],
                member["name"],
                member["session_id"],
                member["conversation_id"],
            )
            for key, member in detailed["members"].items()
        }
        assert lightweight["relationships"] == detailed["relationships"]
        assert len(lightweight["relationships"]) == 1
        assert [
            (task["node_id"], task["snapshot"]["definition"]["name"], task["status"])
            for task in lightweight["tasks"]
        ] == [
            (task["node_id"], task["snapshot"]["definition"]["name"], task["status"])
            for task in detailed["tasks"]
        ]
    assert {member["system_prompt"] for member in detailed["members"].values()} == set(
        prompts.values()
    )
    release.set()


@pytest.mark.asyncio
async def test_coordinator_creates_two_instances_dispatches_and_reads_complete_results_at_capacity_one(
    team_env,
):
    """Dynamic tasks run during a coordinator wait and keep stable member contexts.

    Args:
        team_env: Public services backed by real SQLite.
    """
    env = team_env
    collected = []

    async def script(tools, binding):
        """Exercise idempotent decisions, serial members and complete artifact reads.

        Args:
            tools: Tools registered for the active coordinator execution.
            binding: Current coordinator execution identity from the public port.
        """
        import json

        manage = tools["team_manage"]
        members = []
        for i in range(2):
            request = {
                "action": "create_member",
                "request_id": f"member-{i}",
                "preset_id": "writer",
                "name": f"Writer {i}",
            }
            created = json.loads(await manage.call(None, **request))
            assert json.loads(await manage.call(None, **request)) == created
            members.append(created["member_id"])
        tasks = []
        for i, member_id in enumerate(members):
            tasks.append(
                json.loads(
                    await manage.call(
                        None,
                        action="dispatch",
                        request_id=f"task-{i}",
                        member_id=member_id,
                        name=f"Evidence {i}",
                        task=f"First {i}",
                    )
                )["task_id"]
            )
        assert "Error" in await tools["submit_task_result"].call(
            None, text="Premature completion"
        )
        waited = json.loads(
            await manage.call(
                None, action="wait", request_id="wait-first", task_ids=tasks
            )
        )
        assert all(
            task["result"]["text"].endswith("COMPLETE_END") for task in waited["tasks"]
        )
        following = json.loads(
            await manage.call(
                None,
                action="dispatch",
                request_id="follow-up",
                member_id=members[0],
                name="Follow-up",
                task="Use this run's history",
                dependencies=[tasks[0]],
            )
        )
        await manage.call(
            None, action="wait", request_id="wait-next", task_ids=[following["task_id"]]
        )
        inspected = json.loads(
            await manage.call(None, action="inspect", request_id="inspect")
        )
        collected.append(inspected)
        assert (
            await tools["submit_task_result"].call(None, text="Complete research")
            == "Result accepted."
        )

    env.script = script
    run = await env.service.start_run(
        "alice", env.asset["asset_id"], {"version": 1, "inputs": {"goal": "Research"}}
    )
    for _ in range(500):
        current = await env.service.get_run("alice", run["run_id"])
        if current["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert not env.errors, env.errors
    assert current["status"] == "completed", current
    assert len(current["tasks"]) == 4
    assert len(current["members"]) == 3
    assert await env.service.get_result("alice", run["run_id"]) == {
        "text": "Complete research"
    }
    turns = [
        (binding, text) for binding, text in env.observations if text != "Research"
    ]
    assert turns[0][0].conversation_id != turns[1][0].conversation_id
    assert turns[0][0].conversation_id == turns[2][0].conversation_id
    assert current["tasks"][3]["snapshot"]["input_artifacts"] == [
        {"artifact_id": current["tasks"][1]["run_id"], "version": 1}
    ]
    assert (
        collected[0]["tasks"][1]["result"]["text"]
        == "Evidence " * 2000 + "COMPLETE_END"
    )


@pytest.mark.asyncio
async def test_required_team_approval_blocks_root_completion(team_env):
    """Valid coordinator output cannot bypass required version approval.

    Args:
        team_env: Public services with real persistent human requests.
    """
    env = team_env
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                **env.asset["definition"],
                "approval_question": "Approve the final report?",
            }
        },
    )

    async def script(tools, binding):
        """Reject a circular dispatch before submitting the report for approval.

        Args:
            tools: Tools registered for the active coordinator execution.
            binding: Current coordinator execution identity from the public port.
        """
        import json

        member = json.loads(
            await tools["team_manage"].call(
                None,
                action="create_member",
                request_id="member",
                preset_id="writer",
                name="Writer",
            )
        )
        assert "Error" in await tools["team_manage"].call(
            None,
            action="dispatch",
            request_id="circular",
            member_id=member["member_id"],
            name="Circular task",
            task="Wait for final approval",
            dependencies=["approval"],
        )
        assert (
            await tools["submit_task_result"].call(None, text="Final report")
            == "Result accepted."
        )

    env.script = script
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"goal": "Research"}}
    )
    for _ in range(200):
        current = await env.service.get_run("alice", run["run_id"])
        if current["todos"]:
            break
        await asyncio.sleep(0.01)
    assert not env.errors
    assert current["status"] == "running"
    todo = current["todos"][0]
    assert todo["artifact_ref"] == {
        "artifact_id": current["tasks"][0]["run_id"],
        "version": 1,
    }
    approved = await env.service.answer_todo(
        "alice",
        run["run_id"],
        todo["todo_id"],
        {
            "request_id": "approval",
            "action": "approve",
            "artifact_ref": todo["artifact_ref"],
        },
    )
    assert approved["status"] == "completed"


@pytest.mark.asyncio
async def test_runtime_authority_limits_and_frozen_templates_are_enforced(team_env):
    """Members and stale coordinators cannot manage the root or bypass bounds.

    Args:
        team_env: Public services and controlled task execution.
    """
    import json

    from astrbot.core.agent_teams.assets import RoleAssetError

    env = team_env
    coordinator_ready = asyncio.Event()
    release = asyncio.Event()
    asset = await env.service.save_asset(
        "alice", {"definition": {**env.asset["definition"], "max_tasks": 1}}
    )
    await env.roles.save_role(
        "alice",
        {
            "definition": {
                **env.writer["definition"],
                "prompt": "Edited writer",
                "max_instances": 1,
            },
            "installation": env.writer["installation"],
            "expected_version": 1,
        },
        env.writer["role_id"],
    )

    async def script(tools, binding):
        """Exercise frozen role bounds and retain a live owner for authority checks.

        Args:
            tools: Tools registered for the active coordinator execution.
            binding: Current coordinator execution identity from the public port.
        """
        manage = tools["team_manage"]
        assert "Error" in await manage.call(
            None,
            action="create_member",
            request_id="forbidden",
            preset_id="coordinator",
            name="Unauthorized",
        )
        members = []
        for index in range(2):
            members.append(
                json.loads(
                    await manage.call(
                        None,
                        action="create_member",
                        request_id=f"member-{index}",
                        preset_id="writer",
                        name=f"Writer {index}",
                    )
                )["member_id"]
            )
        assert "limit" in await manage.call(
            None,
            action="create_member",
            request_id="overflow",
            preset_id="writer",
            name="Writer 3",
        )
        task = json.loads(
            await manage.call(
                None,
                action="dispatch",
                request_id="first",
                member_id=members[0],
                name="Evidence",
                task="Work",
            )
        )
        assert "limit" in await manage.call(
            None,
            action="dispatch",
            request_id="second",
            member_id=members[1],
            name="Overflow",
            task="Work",
        )
        await manage.call(
            None, action="wait", request_id="wait", task_ids=[task["task_id"]]
        )
        coordinator_ready.set()
        await release.wait()
        assert (
            await tools["submit_task_result"].call(None, text="Complete")
            == "Result accepted."
        )

    env.script = script
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"goal": "Research"}}
    )
    await asyncio.wait_for(coordinator_ready.wait(), 5)
    current = await env.service.get_run("alice", run["run_id"])
    coord, member = current["tasks"]
    for task in [member, coord]:
        with pytest.raises(RoleAssetError):
            await env.service.manage_team(
                "alice",
                task["run_id"],
                task["state"]["attempt_id"],
                "stale-owner",
                {
                    "action": "create_member",
                    "request_id": "attack",
                    "preset_id": "writer",
                    "name": "Attack",
                },
            )
    assert member["snapshot"]["effective"]["prompt"] == "Original writer"
    assert member["snapshot"]["effective"]["max_instances"] == 2
    assert "team_manage" not in [
        tool.name
        for tool in AgentTeamToolRegistry.get_tools(member["snapshot"]["member"]["umo"])
    ]
    with pytest.raises(RoleAssetError):
        await env.service.submit_result(
            "alice", run["run_id"], member["state"]["attempt_id"], {"text": "End root"}
        )
    release.set()
    for _ in range(200):
        current = await env.service.get_run("alice", run["run_id"])
        if current["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert not env.errors, env.errors
    assert current["status"] == "completed"
    again = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"goal": "Research again"}}
    )
    assert (
        again["tasks"][0]["snapshot"]["member"]["conversation_id"]
        != coord["snapshot"]["member"]["conversation_id"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["continue", "cancel"])
async def test_whole_run_controls_apply_while_coordinator_waits(team_env, decision):
    """A local coordinator wait preserves global admission and cancellation.

    Args:
        team_env: Real persistence and public runtime.
        decision: Continue the paused run or permanently cancel it.
    """
    import json

    env = team_env
    env.member_release = asyncio.Event()
    env.member_started = asyncio.Event()

    async def script(tools, binding):
        """Wait for two serial members while external controls change the root.

        Args:
            tools: Tools registered for the active coordinator execution.
            binding: Current coordinator execution identity from the public port.
        """
        manage = tools["team_manage"]
        tasks = []
        for index in range(2):
            member = json.loads(
                await manage.call(
                    None,
                    action="create_member",
                    request_id=f"member-{index}",
                    preset_id="writer",
                    name=f"Writer {index}",
                )
            )
            tasks.append(
                json.loads(
                    await manage.call(
                        None,
                        action="dispatch",
                        request_id=f"task-{index}",
                        member_id=member["member_id"],
                        name=f"Evidence {index}",
                        task="Work",
                    )
                )["task_id"]
            )
        await manage.call(None, action="wait", request_id="wait", task_ids=tasks)
        await tools["submit_task_result"].call(None, text="Complete")

    env.script = script
    run = await env.service.start_run(
        "alice", env.asset["asset_id"], {"version": 1, "inputs": {"goal": "Research"}}
    )
    await asyncio.wait_for(env.member_started.wait(), 5)
    paused = await env.service.control_run(
        "alice", run["run_id"], {"request_id": "pause", "action": "pause"}
    )
    assert paused["tasks"][0]["state"]["capacity_released"] is True
    assert paused["tasks"][2]["status"] == "pending"
    assert paused["progress"]["running"] == 1
    env.member_release.set()
    if decision == "continue":
        for _ in range(200):
            current = await env.service.get_run("alice", run["run_id"])
            if current["tasks"][1]["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert current["tasks"][2]["status"] == "pending"
        assert len(env.observations) == 2
    current = await env.service.control_run(
        "alice", run["run_id"], {"request_id": "decision", "action": decision}
    )
    for _ in range(200):
        current = await env.service.get_run("alice", run["run_id"])
        if current["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert current["status"] == ("completed" if decision == "continue" else "cancelled")
    assert len(env.observations) == (3 if decision == "continue" else 2)
    assert not env.errors
