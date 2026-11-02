"""A real chat initiator connects a restricted browser to its existing run."""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from astrbot.core.agent_teams.chat import ChatCaller
from astrbot.core.message.components import Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.dashboard.api.workflow_assets import router
from astrbot.dashboard.responses import ApiError, error
from astrbot.dashboard.services.auth_service import AuthService
from tests.agent_teams.test_chat_invocations import chat_env as chat_env


@pytest_asyncio.fixture
async def browser_http(chat_env, monkeypatch):
    """Expose the actual routes and JWT dependency without owner overrides.

    Args:
        chat_env: Real application service and persistent database.
        monkeypatch: Disables optional external telemetry through its public setting.

    Yields:
        The HTTP client and isolated JWT signing key.
    """
    monkeypatch.setenv("ASTRBOT_DISABLE_METRICS", "1")
    service, _, _, _ = chat_env
    app = FastAPI()
    app.state.jwt_secret = "isolated-chat-browser-fixture-key-at-least-32-bytes"
    config = {
        "dashboard": {
            "username": "operator",
            "password": "test-hash",
            "password_storage_upgraded": False,
        }
    }
    app.state.services = SimpleNamespace(
        workflow_assets=service, auth=AuthService(service.db, config)
    )
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def api_error_handler(request, exc):
        return JSONResponse(error(exc.message, exc.data), status_code=exc.status_code)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://chat-browser-test"
    ) as client:
        yield client, app.state.jwt_secret


async def send_chat_command(service, text, sender="12345", source="12345", group=False):
    """Dispatch the real command with trusted platform identity and source.

    Args:
        service: Actual shared service and SQLite persistence.
        text: Incoming explicit command.
        sender: Actual OneBot sender ID.
        source: Actual private conversation ID.
        group: Use a real group event with an independently identified sender.

    Returns:
        The consumed platform event and externally sent messages.
    """
    from astrbot.builtin_stars.builtin_commands.main import Main

    sent = []

    async def external_send(**payload):
        sent.append(payload)

    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
    if group:
        message.group_id = source
    message.self_id = "9999"
    message.sender = MessageMember(sender, "Caller")
    message.message_id = "chat-browser-command"
    message.message_str = text
    message.message = [Plain(text)]
    event = AiocqhttpMessageEvent(
        text,
        message,
        PlatformMetadata("aiocqhttp", "test", "qq-main"),
        source,
        SimpleNamespace(send_private_msg=external_send, send_group_msg=external_send),
    )
    command = Main(
        SimpleNamespace(
            workflow_service=service,
            astrbot_config_mgr=SimpleNamespace(
                get_conf_info=lambda source: {"id": "default"}
            ),
        )
    )
    await command.asset(event, "")
    assert event.is_stopped()
    return event, sent


@pytest.mark.asyncio
async def test_initiator_connects_through_returned_entry_and_answers_original_todo(
    chat_env, browser_http
):
    """Authenticate through the source rather than widening owner HTTP routes.

    Args:
        chat_env: Actual shared service, persisted grants and caller identity.
        browser_http: Actual HTTP routes with isolated authentication.
    """
    service, _, _, _ = chat_env
    identity, _ = await send_chat_command(service, "/asset whoami")
    caller = ChatCaller(
        platform_id=identity.get_platform_id(),
        sender_id=identity.get_sender_id(),
        source=identity.unified_msg_origin,
        profile_id="default",
    )
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Caller decision",
                "result_task_id": "question",
                "tasks": [
                    {
                        "id": "question",
                        "name": "Region",
                        "human": {
                            "kind": "question",
                            "question": "Which region?",
                            "choices": ["Asia", "Europe"],
                        },
                    }
                ],
            }
        },
    )
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    _, source_messages = await send_chat_command(service, "/asset " + asset["asset_id"])
    run = (await service.list_runs("operator"))["runs"][0]
    owner_run = await service.get_run("operator", run["run_id"])
    original_todo = owner_run["todos"][0]["todo_id"]
    projection = await service.chat.get_run(caller, run["run_id"])
    returned_entry = urlsplit(projection["detail_path"].split("#", 1)[1])
    assert returned_entry.path == "/asset-run"
    assert parse_qs(returned_entry.query)["workflow_run"] == [run["run_id"]]
    assert projection["detail_path"] in str(source_messages)

    client, jwt_secret = browser_http
    old_path = "/api/v1/workflow_runs/" + run["run_id"]
    assert (await client.get(old_path)).status_code == 401
    caller_jwt = jwt.encode(
        {"username": caller.principal}, jwt_secret, algorithm="HS256"
    )
    assert (
        await client.get(old_path, headers={"Authorization": "Bearer " + caller_jwt})
    ).status_code == 401
    owner_jwt = jwt.encode({"username": "operator"}, jwt_secret, algorithm="HS256")
    assert (
        await client.get(old_path, headers={"Authorization": "Bearer " + owner_jwt})
    ).status_code == 200
    path = "/api/v1/workflow_chat_runs/" + run["run_id"]
    assert (await client.get(path)).status_code == 401
    assert (
        await client.get(path, headers={"Authorization": "Bearer " + owner_jwt})
    ).status_code == 401
    response = await client.post(path + "/connection")
    assert response.status_code == 200
    connection = response.json()["data"]
    headers = {
        "X-AstrBot-Chat-Session": connection["session_id"] + "." + connection["token"]
    }
    assert (await client.get(path + "/connection", headers=headers)).json()["data"][
        "confirmed"
    ] is False
    assert (await client.get(path, headers=headers)).status_code == 403
    await send_chat_command(
        service, connection["command"], sender="54321", source="54321"
    )
    assert (await client.get(path + "/connection", headers=headers)).json()["data"][
        "confirmed"
    ] is False
    await send_chat_command(service, connection["command"], source="other-conversation")
    assert (await client.get(path + "/connection", headers=headers)).json()["data"][
        "confirmed"
    ] is False
    await send_chat_command(service, connection["command"])
    detail = await client.get(path, headers=headers)
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert set(data) == {
        "run_id",
        "status",
        "result",
        "detail_path",
        "todos",
        "control_status",
        "result_files",
    }
    assert data["todos"][0]["todo_id"] == original_todo
    assert data["todos"][0]["question"] == "Which region?"
    assert data["todos"][0]["constraints"]["choices"] == ["Asia", "Europe"]
    assert "snapshot" not in detail.text and "owner_username" not in detail.text
    assert (
        await client.get(
            path,
            headers={"X-AstrBot-Chat-Session": connection["session_id"] + ".wrong"},
        )
    ).status_code == 401
    assert (
        await client.get("/api/v1/workflow_chat_runs/other-run", headers=headers)
    ).status_code == 404
    assert (
        await client.post(
            old_path + "/todos/" + original_todo + "/answer",
            headers=headers,
            json={"request_id": "not-dashboard", "action": "answer", "answer": "Asia"},
        )
    ).status_code == 401
    assert (
        await client.post(
            path + "/todos/" + original_todo + "/answer",
            headers=headers,
            json={
                "request_id": "spoof-owner",
                "action": "answer",
                "answer": "Asia",
                "owner_username": "operator",
            },
        )
    ).status_code == 422
    answer = await client.post(
        path + "/todos/" + original_todo + "/answer",
        headers=headers,
        json={"request_id": "caller-region", "action": "answer", "answer": "Asia"},
    )
    assert answer.status_code == 200
    assert answer.json()["data"]["status"] == "completed"
    after = await service.get_run("operator", run["run_id"])
    assert after["todos"][0]["todo_id"] == original_todo
    assert after["todos"][0]["response"]["answer"] == "Asia"
    assert len((await service.list_runs("operator"))["runs"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "profile", "expiry"])
async def test_confirmed_browser_loses_read_and_answer_access_after_source_changes(
    chat_env, browser_http, tmp_path, monkeypatch, change
):
    """Recheck live grants and actual routing before each read or mutation.

    Args:
        chat_env: Real shared application service and SQLite.
        browser_http: Actual routes with no authenticated-owner override.
        tmp_path: Isolated configuration files.
        monkeypatch: Moves only the external clock for the expiry scenario.
        change: Grant revocation or a real source-profile routing update.
    """
    from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
    from astrbot.core.config import AstrBotConfig
    from astrbot.core.umop_config_router import UmopConfigRouter
    from astrbot.core.utils.shared_preferences import SharedPreferences

    service, _, _, _ = chat_env
    client, _ = browser_http
    identity, _ = await send_chat_command(service, "/asset whoami")
    caller = ChatCaller(
        platform_id=identity.get_platform_id(),
        sender_id=identity.get_sender_id(),
        source=identity.unified_msg_origin,
        profile_id="default",
    )
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Waiting",
                "tasks": [
                    {
                        "id": "question",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Ready?"},
                    }
                ],
            }
        },
    )
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    await send_chat_command(service, "/asset " + asset["asset_id"])
    run = (await service.list_runs("operator"))["runs"][0]
    path = "/api/v1/workflow_chat_runs/" + run["run_id"]
    connection = (await client.post(path + "/connection")).json()["data"]
    headers = {
        "X-AstrBot-Chat-Session": connection["session_id"] + "." + connection["token"]
    }
    await send_chat_command(service, connection["command"])
    todo = (await client.get(path, headers=headers)).json()["data"]["todos"][0]
    if change == "revoke":
        await service.chat.revoke_access(
            "operator", asset["asset_id"], caller.platform_id, caller.sender_id
        )
    elif change == "profile":
        preferences = SharedPreferences(service.db)
        routing = UmopConfigRouter(preferences)
        await routing.initialize()
        manager = AstrBotConfigManager(
            AstrBotConfig(str(tmp_path / "source-config.json")), routing, preferences
        )
        await manager.initialize()
        private = await manager.create_conf(name="Private source")
        await routing.update_routing_data({caller.source: private})
        service.roles.core_lifecycle = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[]),
            astrbot_config_mgr=manager,
        )
    else:
        monkeypatch.setattr(
            "astrbot.core.agent_teams.chat.time.time",
            lambda: connection["expires_at"] + 1,
        )
    denied_status = 401 if change == "expiry" else 403
    assert (await client.get(path, headers=headers)).status_code == denied_status
    assert (
        await client.get(path + "/result/files/unknown", headers=headers)
    ).status_code == denied_status
    assert (
        await client.post(
            path + "/todos/" + todo["todo_id"] + "/answer",
            headers=headers,
            json={"request_id": "denied", "action": "answer", "answer": "Unauthorized"},
        )
    ).status_code == denied_status
    original = await service.get_run("operator", run["run_id"])
    assert original["todos"][0]["todo_id"] == todo["todo_id"]
    assert original["todos"][0]["status"] == "pending"
    if change == "profile":
        await preferences.close()


@pytest.mark.asyncio
async def test_browser_previews_only_the_version_named_by_its_approval_request(
    chat_env, browser_http
):
    """Keep approval review scoped to its explicit artifact, without owner data.

    Args:
        chat_env: Real run engine and controlled external model boundary.
        browser_http: Actual source-authenticated HTTP routes.
    """
    service, role, _, _ = chat_env
    client, signing_key = browser_http
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
                "name": "Report approval",
                "tasks": [
                    {
                        "id": "report",
                        "name": "Report",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Write a report",
                    },
                    {
                        "id": "approval",
                        "name": "Approval",
                        "human": {
                            "kind": "approval",
                            "question": "Approve this exact report?",
                            "artifact_task_id": "report",
                        },
                    },
                ],
                "edges": [{"source": "report", "target": "approval"}],
            }
        },
    )
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    await send_chat_command(service, "/asset " + asset["asset_id"])
    run_id = (await service.list_runs("operator"))["runs"][0]["run_id"]
    for _ in range(200):
        owner = await service.get_run("operator", run_id)
        if owner["todos"]:
            break
        await asyncio.sleep(0.01)
    assert "Use evidence." in json.dumps(owner)
    todo = owner["todos"][0]
    artifact = todo["artifact_ref"]
    file = await service.store_file(
        "operator",
        artifact["artifact_id"],
        None,
        "private-revision.txt",
        b"UNAPPROVED_OTHER_VERSION",
    )
    await service.revise_artifact(
        "operator",
        artifact["artifact_id"],
        {
            "expected_version": 1,
            "request_id": "private-revision",
            "content": {"text": "UNAPPROVED_OTHER_VERSION"},
        },
    )
    path = "/api/v1/workflow_chat_runs/" + run_id
    connection = (await client.post(path + "/connection")).json()["data"]
    headers = {
        "X-AstrBot-Chat-Session": connection["session_id"] + "." + connection["token"]
    }
    await send_chat_command(service, connection["command"])
    detail = await client.get(path, headers=headers)
    assert "Use evidence." not in detail.text and "PRIVATE_THOUGHT" not in detail.text
    preview_path = path + "/todos/" + todo["todo_id"] + "/artifact"
    preview = await client.get(preview_path, headers=headers)
    assert preview.status_code == 200
    assert preview.json()["data"]["version"] == 1
    assert preview.json()["data"]["content"] == {"text": "Verified final report."}
    assert "UNAPPROVED_OTHER_VERSION" not in preview.text
    assert (
        await client.get(preview_path + "/files/" + file["file_id"], headers=headers)
    ).status_code == 404
    assert (
        await client.get(path + "/todos/other-todo/artifact", headers=headers)
    ).status_code == 404
    caller_jwt = jwt.encode(
        {"username": caller.principal}, signing_key, algorithm="HS256"
    )
    assert (
        await client.get(
            f"/api/v1/workflow_artifacts/{artifact['artifact_id']}/versions/2",
            headers={"Authorization": "Bearer " + caller_jwt},
        )
    ).status_code == 401
    approved = await client.post(
        path + "/todos/" + todo["todo_id"] + "/answer",
        headers=headers,
        json={
            "request_id": "approve-pinned",
            "action": "approve",
            "artifact_ref": artifact,
        },
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "completed"
    assert (await service.get_run("operator", run_id))["todos"][0]["todo_id"] == todo[
        "todo_id"
    ]


@pytest.mark.asyncio
async def test_other_authorized_group_sender_cannot_connect_or_read_the_initiators_run(
    chat_env, browser_http
):
    """A group-visible link never authenticates another sender as the initiator.

    Args:
        chat_env: Real application service and persisted source grants.
        browser_http: Actual scoped HTTP entry.
    """
    service, _, _, _ = chat_env
    client, _ = browser_http
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Group question",
                "tasks": [
                    {
                        "id": "question",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Ready?"},
                    }
                ],
            }
        },
    )
    callers = []
    for sender in ("12345", "54321"):
        event, messages = await send_chat_command(
            service, "/asset whoami", sender=sender, source="88888", group=True
        )
        caller = ChatCaller.model_validate_json(
            messages[0]["message"][0]["data"]["text"]
        )
        assert caller.sender_id == event.get_sender_id()
        assert caller.source == event.unified_msg_origin
        assert caller.source == "qq-main:GroupMessage:88888"
        callers.append(caller)
        await service.chat.save_access(
            "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
        )
    accepted = []
    for caller in callers:
        await send_chat_command(
            service,
            "/asset " + asset["asset_id"],
            sender=caller.sender_id,
            source="88888",
            group=True,
        )
        runs = (await service.list_runs("operator"))["runs"]
        run = next(
            item
            for item in runs
            if item["run_id"] not in {value[0] for value in accepted}
        )
        path = "/api/v1/workflow_chat_runs/" + run["run_id"]
        connection = (await client.post(path + "/connection")).json()["data"]
        headers = {
            "X-AstrBot-Chat-Session": connection["session_id"]
            + "."
            + connection["token"]
        }
        if not accepted:
            await send_chat_command(
                service,
                connection["command"],
                sender="54321",
                source="88888",
                group=True,
            )
            assert (await client.get(path, headers=headers)).status_code == 403
            await send_chat_command(
                service,
                connection["command"],
                sender="12345",
                source="99999",
                group=True,
            )
            assert (await client.get(path, headers=headers)).status_code == 403
        await send_chat_command(
            service,
            connection["command"],
            sender=caller.sender_id,
            source="88888",
            group=True,
        )
        assert (await client.get(path, headers=headers)).status_code == 200
        accepted.append((run["run_id"], headers))
    first_path = "/api/v1/workflow_chat_runs/" + accepted[0][0]
    first = (await client.get(first_path, headers=accepted[0][1])).json()["data"]
    assert (await client.get(first_path, headers=accepted[1][1])).status_code == 404
    assert (
        await client.post(
            first_path + "/todos/" + first["todos"][0]["todo_id"] + "/answer",
            headers=accepted[1][1],
            json={"request_id": "other-sender", "action": "answer", "answer": "No"},
        )
    ).status_code == 404
    assert (await service.get_run("operator", accepted[0][0]))["todos"][0][
        "status"
    ] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [False, True])
async def test_chat_command_answers_saved_request_only_for_initiator(chat_env, group):
    """Route explicit request IDs through the real command and source grant.

    Args:
        chat_env: Real shared services and database.
        group: Exercise both private and group platform events.
    """
    service, _, _, _ = chat_env
    event, _ = await send_chat_command(
        service, "/asset whoami", source="42", group=group
    )
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
                "name": "Two questions",
                "result_task_id": "first",
                "tasks": [
                    {
                        "id": "first",
                        "name": "First",
                        "human": {"kind": "question", "question": "First?"},
                    },
                    {
                        "id": "second",
                        "name": "Second",
                        "human": {"kind": "question", "question": "Second?"},
                    },
                ],
            }
        },
    )
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    accepted = await service.chat.invoke(caller, asset["asset_id"], {})
    run_id = accepted["run_id"]
    run = await service.get_run("operator", run_id)
    assert len(run["todos"]) == 2
    target = next(todo for todo in run["todos"] if todo["question"] == "First?")
    notices = []

    async def send(actual, text):
        notices.append(text)
        return True

    await service.chat.deliver_pending(send, run_id)
    assert sum("Human request" in text for text in notices) == 2
    assert all(todo["todo_id"] in "\n".join(notices) for todo in run["todos"])
    await service.chat.deliver_pending(send, run_id)
    assert sum("Human request" in text for text in notices) == 2
    command = f'/asset answer {run_id} {target["todo_id"]} {{"action":"answer","answer":"yes"}}'
    if group:
        await send_chat_command(
            service, command, sender="other", source="42", group=True
        )
        assert (await service.get_run("operator", run_id))["todos"][0][
            "status"
        ] == "pending"
    await send_chat_command(service, command, source="42", group=group)
    updated = await service.get_run("operator", run_id)
    answered = next(
        todo for todo in updated["todos"] if todo["todo_id"] == target["todo_id"]
    )
    assert answered["response"]["answer"] == "yes"
    assert answered["decision_source"]["caller"] == caller.model_dump()
    assert (
        next(todo for todo in updated["todos"] if todo["todo_id"] != target["todo_id"])[
            "status"
        ]
        == "pending"
    )


@pytest.mark.asyncio
async def test_failed_notice_retries_and_chat_competes_with_dashboard(chat_env):
    """Keep failed notices pending and persist only one competing decision.

    Args:
        chat_env: Real shared services and database.
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
                "name": "Competing answers",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Ready?"},
                    }
                ],
            }
        },
    )
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    accepted = await service.chat.invoke(caller, asset["asset_id"], {})
    run_id = accepted["run_id"]
    todo_id = (await service.get_run("operator", run_id))["todos"][0]["todo_id"]
    sent = []

    async def fail(actual, text):
        return False

    async def send(actual, text):
        sent.append(text)
        return True

    assert await service.chat.deliver_pending(fail, run_id) == 0
    await service.chat.deliver_pending(send, run_id)
    assert any(todo_id in text and "Ready?" in text for text in sent)
    from astrbot.core.agent_teams.assets import RoleAssetError

    results = await asyncio.gather(
        service.chat.answer_browser_todo(
            caller,
            run_id,
            todo_id,
            {"request_id": "chat", "action": "answer", "answer": "chat"},
        ),
        service.answer_todo(
            "operator",
            run_id,
            todo_id,
            {"request_id": "dashboard", "action": "answer", "answer": "dashboard"},
        ),
        return_exceptions=True,
    )
    assert (
        sum(
            isinstance(result, RoleAssetError) and result.status == 409
            for result in results
        )
        == 1
    )
    current = await service.get_run("operator", run_id)
    assert current["todos"][0]["response"]["answer"] in {"chat", "dashboard"}
