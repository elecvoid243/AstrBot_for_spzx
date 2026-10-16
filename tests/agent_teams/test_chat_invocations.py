"""Explicit chat invocations share the persisted asset and run services."""

import asyncio

import pytest
import pytest_asyncio

from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.chat import ChatCaller
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts


@pytest_asyncio.fixture
async def chat_env(tmp_path, monkeypatch):
    """Provide real services and a controlled external execution boundary.

    Args:
        tmp_path: Isolated database storage.
        monkeypatch: Disables optional telemetry outside the controlled boundary.

    Yields:
        Shared service, saved role, actual caller and external observations.
    """
    monkeypatch.setenv("ASTRBOT_DISABLE_METRICS", "1")
    db = SQLiteDatabase(str(tmp_path / "chat.db"))

    async def catalog():
        return {
            "profiles": [{"id": "default"}, {"id": "private"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    role = await roles.save_role(
        "operator",
        {
            "definition": {"name": "Writer", "prompt": "Use evidence."},
            "installation": {"bindings": {"model": "model"}},
        },
    )
    observations = []

    def ports(sender):
        async def deliver(session_id, text, context=None, **kwargs):
            observations.append({"sender": sender, "text": text})
            return kwargs["message_id"]

        async def collect(session_id, message_id, member_id):
            tool = AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )[0]
            assert (
                await tool.call(None, text="Verified final report.")
                == "Result accepted."
            )
            return "Done", [{"type": "think", "text": "PRIVATE_THOUGHT"}]

        return TeamPorts(deliver, collect, lambda sid: False, lambda event: None)

    service = WorkflowService(db, roles, ports_factory=ports)
    caller = ChatCaller(
        platform_id="qq-main",
        sender_id="astrbot",
        source="qq-main:FriendMessage:astrbot",
        profile_id="default",
    )
    try:
        yield service, role, caller, observations
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_only_explicitly_opened_pinned_assets_are_offered_to_the_actual_caller(
    chat_env,
):
    """Offer only a caller's explicitly opened fixed version.

    Args:
        chat_env: Real shared services, pinned role, caller and external execution port.
    """
    service, role, caller, observations = chat_env
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Research report",
                "role_id": role["role_id"],
                "role_version": 1,
                "task": "Research {topic}",
                "required_inputs": ["topic"],
            }
        },
    )
    assert (await service.chat.list_assets(caller))["assets"] == []
    await service.chat.save_access(
        "operator",
        asset["asset_id"],
        {
            "platform_id": "qq-main",
            "sender_id": "astrbot",
            "version": 1,
            "profile_id": "default",
            "source": caller.source,
        },
    )
    offered = (await service.chat.list_assets(caller))["assets"]
    assert [(a["name"], a["asset_id"], a["version"]) for a in offered] == [
        ("Research report", asset["asset_id"], 1)
    ]
    other = caller.model_copy(update={"platform_id": "qq-other"})
    assert (await service.chat.list_assets(other))["assets"] == []
    assert (await service.list_runs("operator"))["runs"] == []
    assert observations == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["single", "workflow", "autonomous"])
async def test_chat_and_dashboard_share_execution_and_return_only_the_final_result(
    chat_env, kind
):
    """Preserve shared execution while releasing only the authorized final result.

    Args:
        chat_env: Real shared services, pinned role, caller and external execution port.
        kind: Single task, graph workflow or autonomous asset.
    """
    service, role, caller, observations = chat_env
    definition = {
        "name": "Report",
        "role_id": role["role_id"],
        "role_version": 1,
        "task": "Research {topic}",
        "required_inputs": ["topic"],
    }
    if kind == "workflow":
        definition = {
            "name": "Report",
            "required_inputs": ["topic"],
            "tasks": [
                {
                    "id": "write",
                    "name": "Write",
                    "member": "writer",
                    "role_id": role["role_id"],
                    "role_version": 1,
                    "task": "Research {topic}",
                }
            ],
        }
    elif kind == "autonomous":
        definition = {
            "kind": "autonomous",
            "name": "Report",
            "coordinator": {
                "role_id": role["role_id"],
                "role_version": 1,
            },
            "allowed_roles": [{"role_id": role["role_id"], "role_version": 1}],
        }
    asset = await service.save_asset("operator", {"definition": definition})
    await service.chat.save_access(
        "operator",
        asset["asset_id"],
        {
            "platform_id": caller.platform_id,
            "sender_id": caller.sender_id,
            "source": caller.source,
            "version": 1,
            "profile_id": "default",
        },
    )
    inputs = {"goal" if kind == "autonomous" else "topic": "energy"}
    accepted = await service.chat.invoke(caller, asset["asset_id"], inputs)
    assert accepted["status"] == "accepted"
    saved = await service.get_run("operator", accepted["run_id"])
    assert saved["snapshot"]["initiator"] == "platform:qq-main:astrbot"
    assert saved["snapshot"]["source"] == caller.source
    assert saved["snapshot"]["inputs"] == inputs
    dashboard = await service.start_run(
        "operator", asset["asset_id"], {"version": 1, "inputs": inputs}
    )
    for _ in range(300):
        returned = await service.chat.get_run(caller, accepted["run_id"])
        web = await service.get_run("operator", dashboard["run_id"])
        if returned["status"] == web["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert returned["status"] == web["status"] == "completed"
    assert returned["result"] == await service.get_result(
        "operator", dashboard["run_id"]
    )
    assert "PRIVATE_THOUGHT" not in str(returned)
    assert "Use evidence." not in str(returned)
    assert returned["detail_path"].endswith("workflow_run=" + accepted["run_id"])
    assert {o["sender"] for o in observations} == {
        "operator",
        "platform:qq-main:astrbot",
    }
    with pytest.raises(RoleAssetError, match="not found"):
        await service.get_run("astrbot", accepted["run_id"])
    with pytest.raises(RoleAssetError, match="not found"):
        await service.chat.get_run(
            caller.model_copy(update={"sender_id": "someone"}), accepted["run_id"]
        )


@pytest.mark.asyncio
async def test_name_ambiguity_and_required_inputs_are_clarified_before_any_run(
    chat_env,
):
    """Clarify explicit selection and missing input before any external effect.

    Args:
        chat_env: Real shared services, pinned role, caller and external execution port.
    """
    service, role, caller, observations = chat_env
    ids = []
    for _ in range(2):
        asset = await service.save_asset(
            "operator",
            {
                "definition": {
                    "name": "Report",
                    "role_id": role["role_id"],
                    "role_version": 1,
                    "task": "Research {topic}",
                    "required_inputs": ["topic"],
                }
            },
        )
        ids.append(asset["asset_id"])
        await service.chat.save_access(
            "operator",
            asset["asset_id"],
            {
                "platform_id": caller.platform_id,
                "sender_id": caller.sender_id,
                "source": caller.source,
                "version": 1,
                "profile_id": "default",
            },
        )
    ambiguous = await service.chat.invoke(caller, "Report", {"topic": "energy"})
    assert ambiguous["status"] == "clarification"
    assert {a["asset_id"] for a in ambiguous["choices"]} == set(ids)
    missing = await service.chat.invoke(caller, ids[0], {})
    assert missing["status"] == "clarification"
    assert missing["fields"] == [
        {
            "path": "inputs.topic",
            "code": "REQUIRED",
            "message": "Provide non-empty text",
        }
    ]
    assert (await service.list_runs("operator"))["runs"] == []
    assert observations == []


@pytest.mark.asyncio
async def test_source_profile_and_revoked_access_block_chat_execution_and_results(
    chat_env,
):
    """Keep unopened profiles and revoked assets outside the caller's access.

    Args:
        chat_env: Real shared services, pinned role, caller and external execution port.
    """
    service, role, caller, observations = chat_env
    private = await service.roles.save_role(
        "operator",
        {
            "definition": {"name": "Private", "prompt": "Private evidence."},
            "installation": {"profile_id": "private", "bindings": {"model": "model"}},
        },
    )
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Private asset",
                "role_id": private["role_id"],
                "role_version": 1,
                "task": "Read private resources",
            }
        },
    )
    await service.chat.save_access(
        "operator",
        asset["asset_id"],
        {
            "platform_id": caller.platform_id,
            "sender_id": caller.sender_id,
            "source": caller.source,
            "version": 1,
            "profile_id": "default",
        },
    )
    with pytest.raises(RoleAssetError, match="source profile"):
        await service.chat.invoke(caller, asset["asset_id"], {})
    assert (await service.list_runs("operator"))["runs"] == []
    assert observations == []
    await service.chat.revoke_access(
        "operator", asset["asset_id"], caller.platform_id, caller.sender_id
    )
    assert (await service.chat.list_assets(caller))["assets"] == []
    with pytest.raises(RoleAssetError, match="not available"):
        await service.chat.invoke(caller, asset["asset_id"], {})


@pytest.mark.asyncio
async def test_source_receives_acceptance_then_final_result_without_process_or_duplicate_delivery(
    chat_env,
):
    """Deliver accepted and completed notices without exposing member process.

    Args:
        chat_env: Real shared services, pinned role, caller and external execution port.
    """
    service, role, caller, observations = chat_env
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Report",
                "role_id": role["role_id"],
                "role_version": 1,
                "task": "Produce report",
            }
        },
    )
    await service.chat.save_access(
        "operator",
        asset["asset_id"],
        {
            "platform_id": caller.platform_id,
            "sender_id": caller.sender_id,
            "source": caller.source,
            "version": 1,
            "profile_id": "default",
        },
    )
    accepted = await service.chat.invoke(caller, asset["asset_id"], {})
    messages = []

    async def send(actual_caller, text):
        messages.append((actual_caller, text))
        return True

    await service.chat.deliver_pending(send)
    for _ in range(200):
        if (await service.chat.get_run(caller, accepted["run_id"]))[
            "status"
        ] == "completed":
            break
        await asyncio.sleep(0.01)
    await service.chat.deliver_pending(send)
    assert len(messages) == 2
    assert all(c == caller for c, _ in messages)
    assert "Accepted" in messages[0][1]
    assert "Verified final report." in messages[1][1]
    assert accepted["detail_path"] in messages[1][1]
    assert "PRIVATE_THOUGHT" not in str(messages)
    assert "Use evidence." not in str(messages)
    await service.chat.deliver_pending(send)
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_revocation_prevents_later_model_or_tool_work_and_result_delivery(
    chat_env,
):
    """Recheck revoked access when an existing human gate releases execution.

    Args:
        chat_env: Real shared services, pinned role, caller and external execution port.
    """
    service, role, caller, observations = chat_env
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Human gate",
                "tasks": [
                    {
                        "id": "question",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Region?"},
                    },
                    {
                        "id": "write",
                        "name": "Write",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Write evidence.",
                    },
                ],
                "edges": [{"source": "question", "target": "write"}],
            }
        },
    )
    await service.chat.save_access(
        "operator",
        asset["asset_id"],
        {
            "platform_id": caller.platform_id,
            "sender_id": caller.sender_id,
            "source": caller.source,
            "version": 1,
            "profile_id": "default",
        },
    )
    accepted = await service.chat.invoke(caller, asset["asset_id"], {})
    run = await service.get_run("operator", accepted["run_id"])
    assert run["todos"][0]["status"] == "pending"
    await service.chat.revoke_access(
        "operator", asset["asset_id"], caller.platform_id, caller.sender_id
    )
    await service.answer_todo(
        "operator",
        run["run_id"],
        run["todos"][0]["todo_id"],
        {
            "request_id": "region",
            "action": "answer",
            "answer": "Asia",
        },
    )
    for _ in range(200):
        run = await service.get_run("operator", run["run_id"])
        if run["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert observations == []
    assert run["status"] == "blocked"
    sent = []

    async def send(actual, text):
        sent.append(text)
        return True

    await service.chat.deliver_pending(send)
    assert sent == []


@pytest.mark.asyncio
async def test_a_retried_failed_run_still_returns_its_eventual_final_result(chat_env):
    """Retain source observation through a failed attempt and explicit retry.

    Args:
        chat_env: Real shared services, pinned role, caller and external execution port.
    """
    service, role, caller, observations = chat_env
    original_ports = service.ports_factory

    def fail_once(sender):
        ports = original_ports(sender)

        async def fail(*args):
            raise RuntimeError("External transport unavailable")

        ports.collect = fail
        return ports

    service.ports_factory = fail_once
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Report",
                "role_id": role["role_id"],
                "role_version": 1,
                "task": "Write",
            }
        },
    )
    await service.chat.save_access(
        "operator",
        asset["asset_id"],
        {
            "platform_id": caller.platform_id,
            "sender_id": caller.sender_id,
            "source": caller.source,
            "version": 1,
            "profile_id": "default",
        },
    )
    accepted = await service.chat.invoke(caller, asset["asset_id"], {})
    for _ in range(200):
        run = await service.get_run("operator", accepted["run_id"])
        if run["status"] == "failed":
            break
        await asyncio.sleep(0.01)
    assert run["status"] == "failed"
    messages = []

    async def send(actual, text):
        messages.append(text)
        return True

    await service.chat.deliver_pending(send)
    assert "failed" in messages[-1]
    service.ports_factory = original_ports
    await service.retry_task(
        "operator",
        run["run_id"],
        {
            "request_id": "retry",
            "attempt_id": run["state"]["attempt_id"],
            "input_policy": "original",
        },
    )
    for _ in range(200):
        run = await service.get_run("operator", run["run_id"])
        if run["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert run["status"] == "completed"
    await service.chat.deliver_pending(send)
    assert "Verified final report." in messages[-1]


@pytest.mark.asyncio
async def test_chat_cannot_read_unapproved_child_results_even_when_the_run_id_is_known(
    chat_env,
):
    """Enforce the root result boundary even for a caller knowing a child ID.

    Args:
        chat_env: Real shared services, pinned role, caller and external execution port.
    """
    service, role, caller, observations = chat_env
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Approved report",
                "tasks": [
                    {
                        "id": "write",
                        "name": "Write",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Write evidence.",
                    },
                    {
                        "id": "approve",
                        "name": "Approve",
                        "human": {
                            "kind": "approval",
                            "question": "Release this version?",
                            "artifact_task_id": "write",
                        },
                    },
                ],
                "edges": [{"source": "write", "target": "approve"}],
            }
        },
    )
    await service.chat.save_access(
        "operator",
        asset["asset_id"],
        {
            "platform_id": caller.platform_id,
            "sender_id": caller.sender_id,
            "source": caller.source,
            "version": 1,
            "profile_id": "default",
        },
    )
    accepted = await service.chat.invoke(caller, asset["asset_id"], {})
    for _ in range(200):
        run = await service.get_run("operator", accepted["run_id"])
        if run["todos"]:
            break
        await asyncio.sleep(0.01)
    assert run["todos"][0]["status"] == "pending"
    assert (await service.chat.get_run(caller, accepted["run_id"]))["result"] is None
    with pytest.raises(RoleAssetError, match="not found"):
        await service.chat.get_run(caller, run["tasks"][0]["run_id"])


@pytest.mark.asyncio
async def test_shared_launch_cannot_reuse_a_chat_grant_for_another_asset(chat_env):
    """Bind the delegated launch to the exact asset and version that was opened.

    Args:
        chat_env: Real shared services, pinned role, caller and external execution port.
    """
    service, role, caller, observations = chat_env
    assets = []
    for name in ("Opened", "Private"):
        assets.append(
            await service.save_asset(
                "operator",
                {
                    "definition": {
                        "name": name,
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Write",
                    }
                },
            )
        )
    await service.chat.save_access(
        "operator",
        assets[0]["asset_id"],
        {
            "platform_id": caller.platform_id,
            "sender_id": caller.sender_id,
            "source": caller.source,
            "version": 1,
            "profile_id": "default",
        },
    )
    with pytest.raises(RoleAssetError, match="grant"):
        await service.start_run(
            "operator",
            assets[1]["asset_id"],
            {"version": 1, "source": caller.source},
            chat={
                "caller": caller.model_dump(),
                "asset_id": assets[0]["asset_id"],
                "version": 1,
            },
        )
    assert (await service.list_runs("operator"))["runs"] == []
    assert observations == []
