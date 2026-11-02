"""The initiating browser can retrieve only files in the published root result."""

import asyncio
import json

import jwt
import pytest

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.chat import ChatCaller
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_chat_browser_access import browser_http as browser_http
from tests.agent_teams.test_chat_browser_access import send_chat_command
from tests.agent_teams.test_chat_invocations import chat_env as chat_env


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["workflow", "autonomous"])
async def test_only_published_final_files_are_delivered_to_confirmed_initiator(
    chat_env, browser_http, kind
):
    """Deliver the completed contract's bytes without granting artifact browsing.

    Args:
        chat_env: Actual shared service and durable SQLite storage.
        browser_http: Actual HTTP and JWT dependencies with no owner override.
        kind: Unified workflow graph or autonomous team engine.
    """
    service, role, _, _ = chat_env
    client, signing_key = browser_http
    bindings = {}
    submitted = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        bindings[session_id] = AgentTeamExecutionRegistry.resolve(execution_token)
        return message_id

    async def collect(session_id, message_id, member_id):
        binding = bindings[session_id]
        task = await service.get_run("operator", binding.run_id)
        public = await service.store_file(
            "operator",
            binding.run_id,
            task["state"]["attempt_id"],
            "final-report.txt",
            b"COMPLETE_FINAL_FILE_CONTENT",
        )
        private = await service.store_file(
            "operator",
            binding.run_id,
            task["state"]["attempt_id"],
            "unpublished.txt",
            b"PRIVATE_UNPUBLISHED_FILE",
        )
        submitted.append((binding.run_id, public, private))
        tool = AgentTeamToolRegistry.get_tools("webchat:FriendMessage:" + session_id)[0]
        assert (
            await tool.call(
                None,
                result_json=json.dumps(
                    {
                        "text": "The report is attached.",
                        "files": [{"file_id": public["file_id"]}],
                    }
                ),
            )
            == "Result accepted."
        )
        return "Done", []

    service.ports_factory = lambda sender: TeamPorts(
        deliver, collect, lambda sid: False, lambda event: None
    )
    identity, _ = await send_chat_command(service, "/asset whoami")
    caller = ChatCaller(
        platform_id=identity.get_platform_id(),
        sender_id=identity.get_sender_id(),
        source=identity.unified_msg_origin,
        profile_id="default",
    )
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
    task = {
        "role_id": role["role_id"],
        "role_version": 1,
        "task": "Write the final report",
        "output_contract": contract,
    }
    if kind == "workflow":
        definition = {
            "name": "Published report",
            "result_task_id": "report",
            "tasks": [{"id": "report", "name": "Report", "member": "writer", **task}],
        }
    elif kind == "autonomous":
        definition = {
            "name": "Published report",
            "kind": "autonomous",
            "coordinator": {
                "id": "coordinator",
                "role_id": role["role_id"],
                "role_version": 1,
            },
            "allowed_roles": [
                {"id": "writer", "role_id": role["role_id"], "role_version": 1}
            ],
            "output_contract": contract,
        }
    asset = await service.save_asset("operator", {"definition": definition})
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    command = "/asset " + asset["asset_id"]
    if kind == "autonomous":
        command += ' | {"goal":"Write the final report"}'
    _, source_messages = await send_chat_command(service, command)
    runs = (await service.list_runs("operator"))["runs"]
    assert runs, source_messages
    run_id = runs[0]["run_id"]
    for _ in range(300):
        owner = await service.get_run("operator", run_id)
        if owner["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert owner["status"] == "completed"
    assert owner["todos"] == []
    origin, public, private = submitted[0]
    owner_headers = {
        "Authorization": "Bearer "
        + jwt.encode({"username": "operator"}, signing_key, algorithm="HS256")
    }
    old_path = (
        f"/api/v1/workflow_artifacts/{origin}/versions/1/files/{public['file_id']}"
    )
    owner_file = await client.get(old_path, headers=owner_headers)
    assert (
        owner_file.status_code == 200
        and owner_file.content == b"COMPLETE_FINAL_FILE_CONTENT"
    )
    await service.revise_artifact(
        "operator",
        origin,
        {
            "request_id": "private-revision",
            "expected_version": 1,
            "content": {
                "text": "Private revision",
                "files": [{"file_id": private["file_id"]}],
            },
        },
    )
    path = "/api/v1/workflow_chat_runs/" + run_id
    connection = (await client.post(path + "/connection")).json()["data"]
    headers = {
        "X-AstrBot-Chat-Session": connection["session_id"] + "." + connection["token"]
    }
    file_path = path + "/result/files/" + public["file_id"]
    assert (await client.get(file_path)).status_code == 401
    assert (await client.get(file_path, headers=headers)).status_code == 403
    await send_chat_command(service, connection["command"], sender="54321")
    assert (await client.get(file_path, headers=headers)).status_code == 403
    await send_chat_command(service, connection["command"])
    for download in [False, True]:
        response = await client.get(
            file_path, headers=headers, params={"download": download}
        )
        assert response.status_code == 200
        assert response.content == b"COMPLETE_FINAL_FILE_CONTENT"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in response.headers["content-security-policy"]
        assert response.headers["content-disposition"].startswith(
            "attachment" if download else "inline"
        )
    projection = (await client.get(path, headers=headers)).json()["data"]
    assert projection["result_files"] == [
        {**public, "artifact_id": origin, "version": 1}
    ]
    assert (
        await client.get(file_path, headers=headers, params={"version": 2})
    ).content == b"COMPLETE_FINAL_FILE_CONTENT"
    assert (
        await client.get(path + "/result/files/" + private["file_id"], headers=headers)
    ).status_code == 404
    assert (await client.get(old_path, headers=headers)).status_code == 401
    caller_headers = {
        "Authorization": "Bearer "
        + jwt.encode({"username": caller.principal}, signing_key, algorithm="HS256")
    }
    assert (await client.get(old_path, headers=caller_headers)).status_code == 404
    other_asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Private other run",
                "result_task_id": "gate",
                "tasks": [
                    {
                        "id": "gate",
                        "name": "Gate",
                        "human": {"kind": "question", "question": "Private?"},
                    }
                ],
            }
        },
    )
    other = await service.start_run("operator", other_asset["asset_id"], {"version": 1})
    assert (
        await client.get(
            f"/api/v1/workflow_chat_runs/{other['run_id']}/result/files/{public['file_id']}",
            headers=headers,
        )
    ).status_code == 404
    await service.chat.revoke_access(
        "operator", asset["asset_id"], caller.platform_id, caller.sender_id
    )
    assert (await client.get(path, headers=headers)).status_code == 403
    assert (await client.get(file_path, headers=headers)).status_code == 403
