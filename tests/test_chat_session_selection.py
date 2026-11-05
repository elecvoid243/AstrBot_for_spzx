"""Persistent ordinary-session choices across HTTP, runtime and background turns."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from astrbot.core import astr_main_agent as ama
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.goal.goal_service import goal_service
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.provider import Provider
from astrbot.dashboard.services.chat_service import (
    ChatService,
    ChatServiceError,
    build_webchat_unified_msg_origin,
)
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat


@pytest.mark.asyncio
async def test_owned_choices_survive_service_database_and_browser_rebuild(
    ordinary_chat, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    provider = MagicMock(spec=Provider)
    provider.get_model.return_value = "gpt-4o-mini"
    provider.provider_config = {"id": "gpt-mini"}
    core.provider_manager.inst_map["gpt-mini"] = provider
    other = MagicMock(spec=Provider)
    other.provider_config = {"id": "other"}
    core.provider_manager.providers_config.append(
        {
            "id": "other",
            "enable": True,
            "provider_source_id": "openai-source",
            "model": "model-other",
        }
    )
    core.provider_manager.inst_map["other"] = other
    owner = users[0]["resource_owner"]
    headers = {"Authorization": f"Bearer {users[0]['token']}"}
    sessions = []
    for _ in range(2):
        response = await client.get("/api/v1/chat/sessions/new", headers=headers)
        sessions.append(response.json()["data"]["session_id"])
    before = copy.deepcopy(dict(core.astrbot_config))
    paths = [f"/api/v1/chat/sessions/{sid}/selection" for sid in sessions]
    choices = [
        {
            "selected_provider": "gpt-mini",
            "selected_model": "gpt-4o-mini",
            "thinking_effort": "xhigh",
            "effort_preset": "qwen-3.8",
        },
        {
            "selected_provider": "other",
            "selected_model": "model-other",
            "thinking_effort": "32768",
            "effort_preset": None,
        },
    ]
    defaults = server.asgi_app.state.services.chat.get_chatui_thinking_effort()
    fallback = {
        "default_thinking_effort": defaults["value"],
        "default_effort_preset": defaults["active_preset"],
    }
    empty = {**dict.fromkeys(choices[0]), **fallback}
    initial = await client.get(paths[0], headers=headers)
    assert initial.json().get("data") == empty, initial.text
    for path, choice in zip(paths, choices):
        response = await client.put(path, headers=headers, json=choice)
        assert response.json() == {
            "status": "ok",
            "message": None,
            "data": {**choice, **fallback},
        }, response.text
    for caller in (users[1], admin):
        foreign = {"Authorization": f"Bearer {caller['token']}"}
        for method in ("GET", "PUT"):
            response = await client.request(
                method,
                paths[0],
                headers=foreign,
                **({"json": choices[1]} if method == "PUT" else {}),
            )
            assert response.json()["status"] == "error"
            assert "xhigh" not in response.text
    forged = await client.put(
        paths[0], headers=headers, json={**choices[0], "owner": owner}
    )
    assert forged.status_code == 422
    # A new database/service and a cookie-free HTTP client represent a restart
    # and another browser; neither can consult browser storage.
    reopened = SQLiteDatabase(db.db_path)
    await reopened.initialize()
    service = ChatService(reopened, core)
    monkeypatch.setattr(server.asgi_app.state.services, "chat", service)
    monkeypatch.setattr(
        ama, "_get_session_conv", AsyncMock(return_value=SimpleNamespace(history="[]"))
    )
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    calls = []

    async def consume(sid):
        item = await webchat_queue_mgr.get_or_create_queue(sid).get()
        event = adapter.create_event(await adapter.convert_message(item))
        selected = await ama._select_provider(
            event,
            SimpleNamespace(get_provider_by_id=core.provider_manager.inst_map.get),
        )
        req, _ = await ama.collect_initial_request(
            event, MagicMock(), ama.MainAgentBuildConfig(tool_call_timeout=60)
        )
        if selected is not None:
            await selected.text_chat(
                prompt=req.prompt, model=req.model, **req.llm_params
            )
            calls.append((req.model, req.llm_params))
        back = webchat_queue_mgr.get_or_create_back_queue(item[2]["message_id"], sid)
        await back.put({"type": "end", "message_id": item[2]["message_id"]})
        return event

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.asgi_app),
            base_url="http://fresh-browser",
        ) as browser:
            for path, choice in zip(paths, choices):
                assert (await browser.get(path, headers=headers)).json()["data"] == {
                    **choice,
                    **fallback,
                }
            worker = asyncio.create_task(consume(sessions[0]))
            response = await browser.post(
                "/api/v1/chat",
                headers=headers,
                json={"session_id": sessions[0], "message": "ordinary"},
            )
            await worker
            assert response.status_code == 200, response.text
        assert calls[-1] == ("gpt-4o-mini", {"thinking_effort": "xhigh"})
        worker = asyncio.create_task(consume(sessions[1]))
        response = await client.post(
            "/api/v1/chat",
            headers=headers,
            json={"session_id": sessions[1], "message": "second conversation"},
        )
        await worker
        assert response.status_code == 200, response.text
        assert calls[-1] == ("model-other", {"thinking_effort": "32768"})
        # Explicit single-turn values do not write persistent preferences.
        worker = asyncio.create_task(consume(sessions[0]))
        stream = await service.build_chat_stream(
            owner,
            {
                "session_id": sessions[0],
                "message": "override",
                "thinking_effort": "auto",
            },
        )
        await worker
        async for _ in stream:
            pass
        assert calls[-1] == ("gpt-4o-mini", {})
        worker = asyncio.create_task(consume(sessions[0]))
        stream = await service.build_chat_stream(
            owner,
            {
                "session_id": sessions[0],
                "message": "temporary model",
                "selected_provider": "other",
                "selected_model": "model-other",
            },
        )
        await worker
        async for _ in stream:
            pass
        assert calls[-1] == ("model-other", {"thinking_effort": "xhigh"})

        worker = asyncio.create_task(consume(sessions[0]))
        await service.inject_cron_turn(
            cid=sessions[0],
            username=owner,
            message_id="scheduled-test",
            text="scheduled",
        )
        await worker
        await service.chat_runs["scheduled-test"].task
        assert calls[-1] == ("gpt-4o-mini", {"thinking_effort": "xhigh"})
        session = await reopened.get_platform_session_by_id(sessions[0])
        worker = asyncio.create_task(consume(sessions[0]))
        await service._queue_goal_kickoff_turn(session, owner, "goal", "goal-test")
        await worker
        await asyncio.gather(
            *(run.task for run in service.chat_runs.values() if run.task)
        )
        assert calls[-1] == ("gpt-4o-mini", {"thinking_effort": "xhigh"})
        # Goal continuation clones deliberately clear source turn extras; resolve
        # the latest persisted preference rather than copying a one-turn value.
        source = adapter.create_event(
            await adapter.convert_message(
                (
                    owner,
                    sessions[0],
                    {
                        "message": [{"type": "plain", "text": "source"}],
                        "message_id": "source",
                        "thinking_effort": "low",
                    },
                )
            )
        )
        queued = asyncio.Queue()
        monkeypatch.setattr(
            goal_service, "_context", SimpleNamespace(get_event_queue=lambda: queued)
        )
        monkeypatch.setattr(goal_service, "_run_registrar", AsyncMock())
        await goal_service.inject_continuation(source, "continue", "goal-test")
        continuation = queued.get_nowait()
        assert continuation.get_extra("selected_model") == "gpt-4o-mini"
        assert continuation.get_extra("thinking_effort") == "xhigh"
        for path, choice in zip(paths, choices):
            assert (await client.get(path, headers=headers)).json()["data"] == {
                **choice,
                **fallback,
            }
        assert dict(core.astrbot_config) == before
        for invalid in (
            {"selected_provider": "missing"},
            {"selected_provider": "gpt-mini", "selected_model": "unadvertised"},
            {"thinking_effort": " "},
        ):
            response = await client.put(paths[0], headers=headers, json=invalid)
            assert response.json()["status"] == "error"
        core.provider_manager.inst_map.clear()
        assert (await client.get(paths[0], headers=headers)).json()["status"] == "error"
        with pytest.raises(ChatServiceError, match="unavailable"):
            await service.inject_cron_turn(
                cid=sessions[0], username=owner, message_id="invalid", text="invalid"
            )
        # Clearing stale preferences remains possible without loading the model.
        response = await client.put(paths[0], headers=headers, json={})
        assert response.json()["data"] == empty
        assert await service.validate_chat_selection(owner, sessions[0], {}) == empty
    finally:
        for run in list(service.chat_runs.values()):
            if run.task and not run.task.done():
                run.task.cancel()
                await asyncio.gather(run.task, return_exceptions=True)
        for sid in sessions:
            webchat_queue_mgr.remove_queues(sid)
        await reopened.engine.dispose()


@pytest.mark.asyncio
async def test_administrator_custom_model_override_is_persistent(ordinary_chat):
    client, server, core, db, admin, users = ordinary_chat
    core.provider_manager.inst_map["gpt-mini"] = MagicMock(spec=Provider)
    headers = {"Authorization": f"Bearer {admin['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    path = f"/api/v1/chat/sessions/{sid}/selection"
    choice = {
        "selected_provider": "gpt-mini",
        "selected_model": "other-valid-model",
        "thinking_effort": "0.75",
        "effort_preset": None,
    }
    saved = (await client.put(path, headers=headers, json=choice)).json()["data"]
    assert all(saved[key] == value for key, value in choice.items())
    assert (await client.get(path, headers=headers)).json()["data"] == saved
    with pytest.raises(ChatServiceError, match="Permission denied"):
        await server.asgi_app.state.services.chat.inject_cron_turn(
            cid=sid,
            username=users[0]["resource_owner"],
            message_id="forged",
            text="forged",
        )


@pytest.mark.asyncio
async def test_unset_sessions_share_account_defaults_with_background_turns(
    ordinary_chat, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    headers = {"Authorization": f"Bearer {users[0]['token']}"}
    owner = users[0]["resource_owner"]
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    path = f"/api/v1/chat/sessions/{sid}/selection"
    response = await client.put(
        "/api/v1/chat/ui-settings/value",
        headers=headers,
        json={"value": "24576", "active_preset": "qwen-3.8"},
    )
    assert response.json()["status"] == "ok", response.text
    choice = (await client.get(path, headers=headers)).json()["data"]
    assert choice["thinking_effort"] is None
    assert choice["default_thinking_effort"] == "24576"
    assert choice["default_effort_preset"] == "qwen-3.8"
    service = server.asgi_app.state.services.chat
    monkeypatch.setattr(service, "register_synthetic_chat_run", AsyncMock())
    try:
        await service.inject_cron_turn(
            cid=sid, username=owner, message_id="defaults", text="scheduled"
        )
        item = await webchat_queue_mgr.get_or_create_queue(sid).get()
        event = WebChatAdapter({}, {}, asyncio.Queue()).create_event(
            await WebChatAdapter({}, {}, asyncio.Queue()).convert_message(item)
        )
        monkeypatch.setattr(
            ama,
            "_get_session_conv",
            AsyncMock(return_value=SimpleNamespace(history="[]")),
        )
        req, _ = await ama.collect_initial_request(
            event, MagicMock(), ama.MainAgentBuildConfig(tool_call_timeout=60)
        )
        assert req.model is None
        assert req.llm_params == {"thinking_effort": "24576"}
        assert (
            await db.get_preference("webchat_session", sid, "model_selection") is None
        )
        # An explicit auto opts out of the account default for this session.
        response = await client.put(
            path, headers=headers, json={"thinking_effort": "auto"}
        )
        assert response.json()["data"]["thinking_effort"] == "auto"
        await service.inject_cron_turn(
            cid=sid, username=owner, message_id="auto", text="scheduled"
        )
        item = await webchat_queue_mgr.get_or_create_queue(sid).get()
        assert item[2]["thinking_effort"] == "auto"
    finally:
        webchat_queue_mgr.remove_queues(sid)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_kind", ["user", "legacy_admin"])
async def test_background_model_access_uses_persisted_owner_role(
    ordinary_chat, monkeypatch, owner_kind
):
    """HTTP and background entry points enforce the same owner's model access.

    Args:
        ordinary_chat: Production HTTP app backed by a temporary SQLite database.
        monkeypatch: Replaces run registration to inspect admitted queue payloads.
        owner_kind: Ordinary member or migrated administrator with a legacy owner.
    """
    from astrbot.core.utils.webchat_access import webchat_account_role

    client, server, core, db, admin, users = ordinary_chat
    account = users[0] if owner_kind == "user" else admin
    owner = account["resource_owner"]
    if owner_kind == "legacy_admin":
        assert owner != account["account_id"]
    headers = {"Authorization": f"Bearer {account['token']}"}
    core.provider_manager.inst_map["gpt-mini"] = MagicMock(spec=Provider)
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    path = f"/api/v1/chat/sessions/{sid}/selection"
    selected_model = (
        "gpt-4o-mini" if owner_kind == "user" else "administrator-custom-model"
    )
    response = await client.put(
        path,
        headers=headers,
        json={"selected_provider": "gpt-mini", "selected_model": selected_model},
    )
    assert response.json()["status"] == "ok", response.text
    provider = next(
        item
        for item in core.provider_manager.providers_config
        if item["id"] == "gpt-mini"
    )
    provider["model"] = "new-model"
    http_result = (await client.get(path, headers=headers)).json()
    service = server.asgi_app.state.services.chat
    register = AsyncMock()
    monkeypatch.setattr(service, "register_synthetic_chat_run", register)
    token = webchat_account_role.set(None)
    try:
        if owner_kind == "user":
            assert http_result["status"] == "error"
            assert http_result["message"] == "Chat model is unavailable"
            with pytest.raises(ChatServiceError, match="Chat model is unavailable"):
                await service.inject_cron_turn(
                    cid=sid, username=owner, message_id="owner-role", text="scheduled"
                )
            register.assert_not_awaited()
        else:
            assert http_result["status"] == "ok"
            assert http_result["data"]["selected_model"] == selected_model
            await service.inject_cron_turn(
                cid=sid, username=owner, message_id="owner-role", text="scheduled"
            )
            item = webchat_queue_mgr.get_or_create_queue(sid).get_nowait()
            assert item[2]["selected_model"] == selected_model
            register.assert_awaited_once()
    finally:
        webchat_account_role.reset(token)
        webchat_queue_mgr.remove_queues(sid)
