"""Chat decisions on exact artifacts inside a real nested workflow."""

import asyncio
import json

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.chat import ChatCaller
from tests.agent_teams.test_chat_browser_access import send_chat_command
from tests.agent_teams.test_chat_invocations import chat_env as chat_env


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_nested_chat_approval_pins_version_and_propagates_decision(
    chat_env, action
):
    """Expose and decide the child request using its initiating chat identity.

    Args:
        chat_env: Shared real workflow service, persistence, and controlled executor.
        action: Approval or rejection of the exact requested version.
    """
    service, role, _, _ = chat_env
    event, _ = await send_chat_command(service, "/asset whoami")
    caller = ChatCaller(
        platform_id=event.get_platform_id(),
        sender_id=event.get_sender_id(),
        source=event.unified_msg_origin,
        profile_id="default",
    )
    child = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Child review",
                "result_task_id": "approval",
                "tasks": [
                    {
                        "id": "report",
                        "name": "Report",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Write the report.",
                    },
                    {
                        "id": "approval",
                        "name": "Review",
                        "human": {
                            "kind": "approval",
                            "question": "Approve the report?",
                            "artifact_task_id": "report",
                        },
                    },
                ],
                "edges": [{"source": "report", "target": "approval"}],
            }
        },
    )
    parent = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Parent",
                "result_task_id": "child",
                "tasks": [
                    {
                        "id": "child",
                        "name": "Child",
                        "workflow": {"asset_id": child["asset_id"], "version": 1},
                        "output_contract": child["definition"]["tasks"][1][
                            "output_contract"
                        ],
                    },
                ],
            }
        },
    )
    await service.chat.save_access(
        "operator", parent["asset_id"], {**caller.model_dump(), "version": 1}
    )
    accepted = await service.chat.invoke(caller, parent["asset_id"], {})
    run_id = accepted["run_id"]
    for _ in range(500):
        run = await service.get_run("operator", run_id)
        if run["todos"]:
            break
        await asyncio.sleep(0.01)
    assert len(run["todos"]) == 1, run
    todo = run["todos"][0]
    assert todo["scope_run_id"] != run_id
    ref = todo["artifact_ref"]
    assert ref["version"] == 1
    projection = await service.chat.get_run(caller, run_id, include_todos=True)
    assert projection["todos"][0]["todo_id"] == todo["todo_id"]
    assert projection["todos"][0]["artifact_ref"] == ref
    assert "snapshot" not in json.dumps(projection)
    preview = await service.chat.read_browser_artifact(caller, run_id, todo["todo_id"])
    assert preview["content"] == {"text": "Verified final report."}
    await service.revise_artifact(
        "operator",
        ref["artifact_id"],
        {
            "expected_version": 1,
            "request_id": "revision",
            "content": {"text": "Different version"},
        },
    )
    notices = []

    async def send(actual, text):
        notices.append(text)
        return True

    await service.chat.deliver_pending(send, run_id)
    assert todo["todo_id"] in "\n".join(notices)
    assert ref["artifact_id"] in "\n".join(notices)
    payload = {"action": action, "artifact_ref": ref}
    if action == "reject":
        payload["feedback"] = "Please clarify the evidence."
    bad = {**payload, "artifact_ref": {**ref, "version": 2}}
    command = f"/asset answer {run_id} {todo['todo_id']} "
    _, rejected_version = await send_chat_command(service, command + json.dumps(bad))
    assert "Artifact version does not match" in str(rejected_version)
    assert (await service.get_run("operator", run_id))["todos"][0][
        "status"
    ] == "pending"
    if action == "reject":
        _, missing_feedback = await send_chat_command(
            service, command + json.dumps({"action": "reject", "artifact_ref": ref})
        )
        assert "Rejection requires feedback" in str(missing_feedback)
        assert (await service.get_run("operator", run_id))["todos"][0][
            "status"
        ] == "pending"
    await send_chat_command(service, command + json.dumps(payload))
    for _ in range(500):
        run = await service.get_run("operator", run_id)
        if run["status"] in {"completed", "blocked", "failed"}:
            break
        await asyncio.sleep(0.01)
    decided = run["todos"][0]
    assert decided["response"]["action"] == action
    assert decided["response"]["artifact_ref"] == ref
    assert decided["decision_source"]["caller"] == caller.model_dump()
    if action == "approve":
        assert run["status"] == "completed", run
        assert run["result"]["approved"] is True
        assert decided["status"] == "approved"
        assert (await service.export_artifact("operator", **ref))["content"] == preview[
            "content"
        ]
    else:
        assert run["status"] == "completed", run
        assert run["result"] == {"text": payload["feedback"], "approved": False}
        assert decided["status"] == "rejected"
        assert decided["response"]["feedback"] == payload["feedback"]
    with pytest.raises(RoleAssetError):
        await service.export_artifact("operator", ref["artifact_id"], 2)


@pytest.mark.asyncio
async def test_restricted_projection_whitelists_timeout_and_decision_source(chat_env):
    """Show policy and decision kind without exposing caller or owner identity.

    Args:
        chat_env: Shared service and real temporary database.
    """
    from astrbot.core.db.po import AgentWorkflowTodo

    service, _, _, _ = chat_env
    event, _ = await send_chat_command(service, "/asset whoami")
    caller = ChatCaller(
        platform_id=event.get_platform_id(),
        sender_id=event.get_sender_id(),
        source=event.unified_msg_origin,
        profile_id="default",
    )
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Waiting question",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Proceed?"},
                    }
                ],
            }
        },
    )
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    run = await service.chat.invoke(caller, asset["asset_id"], {})
    root = await service.get_run("operator", run["run_id"])
    todo_id = root["todos"][0]["todo_id"]
    async with service.db.get_db() as session:
        todo = await session.get(AgentWorkflowTodo, todo_id)
        todo.data = {
            **todo.data,
            "human_wait": {
                "timeout_seconds": 60,
                "timeout_action": "use_recommendation",
                "internal": "SECRET_POLICY",
            },
            "deadline": 1800000000,
            "timeout_reason": "missing_recommendation",
            "decision_source": {
                "type": "recommendation",
                "username": "SECRET_OWNER",
                "caller": {"sender_id": "SECRET_SENDER"},
                "recommendation_ref": {"private": "SECRET_REF"},
            },
        }
        session.add(todo)
        await session.commit()
    projection = await service.chat.get_run(caller, run["run_id"], include_todos=True)
    visible = projection["todos"][0]
    assert visible["human_wait"] == {
        "timeout_seconds": 60,
        "timeout_action": "use_recommendation",
    }
    assert visible["deadline"] == 1800000000
    assert visible["timeout_reason"] == "missing_recommendation"
    assert visible["decision_source"] == {"type": "recommendation"}
    assert "SECRET_" not in json.dumps(projection)


@pytest.mark.asyncio
async def test_long_chat_notice_retries_remaining_parts_without_splitting_command(
    chat_env,
):
    """Retain per-part receipts and keep the answer command together.

    Args:
        chat_env: Shared workflow service and database.
    """
    service, _, _, _ = chat_env
    event, _ = await send_chat_command(service, "/asset whoami")
    caller = ChatCaller(
        platform_id=event.get_platform_id(),
        sender_id=event.get_sender_id(),
        source=event.unified_msg_origin,
        profile_id="default",
    )
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Long question",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {
                            "kind": "question",
                            "question": "Evidence " + "X" * 2200,
                        },
                    }
                ],
            }
        },
    )
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    run = await service.chat.invoke(caller, asset["asset_id"], {})
    sent = []

    async def first(actual, text):
        if len(sent) == 2:
            return False
        sent.append(text)
        return True

    await service.chat.deliver_pending(first, run["run_id"])
    saved = sent[:]

    async def retry(actual, text):
        sent.append(text)
        return True

    await service.chat.deliver_pending(retry, run["run_id"])
    assert len(sent) > len(saved)
    assert sent.count(saved[0]) == 1 and sent.count(saved[1]) == 1
    assert all(len(text) <= 1500 for text in sent)
    assert any("/asset answer " in text and '"YOUR_ANSWER"' in text for text in sent)
