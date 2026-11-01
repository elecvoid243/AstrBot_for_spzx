"""Owned profile consumption and private input state through Dashboard HTTP."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.umop_config_router import UmopConfigRouter
from astrbot.core.utils.shared_preferences import SharedPreferences
from tests.fixtures.dashboard_accounts import account_dashboard as account_dashboard
from tests.fixtures.dashboard_accounts import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import USER_PASSWORD, login
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat


@pytest_asyncio.fixture
async def configured_chat(ordinary_chat, tmp_path):
    client, server, core, db, admin, users = ordinary_chat
    manager = core.astrbot_config_mgr
    for config_id in ("research", "writing"):
        config = AstrBotConfig(str(tmp_path / f"{config_id}.json"))
        config["provider"] = [{"id": "secret-provider", "api_key": "DO-NOT-EXPOSE"}]
        config["test_profile"] = config_id
        config.save_config()
        manager.confs[config_id] = config
        manager.abconf_data[config_id] = {
            "name": config_id.title(),
            "path": f"{config_id}.json",
        }
    # A pre-existing administrator wildcard must not override a user's choice.
    await core.umop_config_router.update_route("webchat:*:*", "writing")
    yield ordinary_chat


@pytest.mark.asyncio
async def test_two_users_consume_profiles_and_effort_without_shared_writes(
    configured_chat,
):
    client, server, core, db, admin, users = configured_chat
    config_before = copy.deepcopy(dict(core.astrbot_config))
    sessions = []
    for index, user in enumerate(users):
        headers = {"Authorization": f"Bearer {user['token']}"}
        response = await client.get("/api/v1/chat/configs", headers=headers)
        assert response.json()["status"] == "ok"
        options = response.json()["data"]["info_list"]
        assert {option["id"] for option in options} == {
            "default",
            "research",
            "writing",
        }
        assert all(
            set(option) == {"id", "name", "agent_runner_type"} for option in options
        )
        assert "DO-NOT-EXPOSE" not in response.text and "path" not in response.text
        models = await client.get("/api/v1/chat/models", headers=headers)
        assert models.json()["data"] == [
            {
                "id": "gpt-mini",
                "model": "gpt-4o-mini",
                "provider_source_id": "openai-source",
            }
        ]
        assert "test-key" not in models.text and "api_base" not in models.text
        session = await client.get("/api/v1/chat/sessions/new", headers=headers)
        sessions.append(session.json()["data"]["session_id"])
        response = await client.put(
            "/api/v1/chat/ui-settings/value",
            headers=headers,
            json={
                "value": ("low", "xhigh")[index],
                "active_preset": ("deepseek-v4", "qwen-3.8")[index],
            },
        )
        assert response.json()["data"]["value"] == ("low", "xhigh")[index]
        settings = await client.get("/api/v1/chat/ui-settings", headers=headers)
        assert (
            settings.json()["data"]["thinking_effort"]["active_preset"]
            == ("deepseek-v4", "qwen-3.8")[index]
        )
        assert settings.json()["data"]["can_edit"] is False
        assert (
            settings.json()["data"]["thinking_effort"]["value"]
            == ("low", "xhigh")[index]
        )
    adapter = WebChatAdapter({}, {}, asyncio.Queue())

    async def model_reply(index):
        sender, session_id, payload = await webchat_queue_mgr.get_or_create_queue(
            sessions[index]
        ).get()
        event = adapter.create_event(
            await adapter.convert_message((sender, session_id, payload))
        )
        effective = core.astrbot_config_mgr.get_conf(event.unified_msg_origin)
        waking = WakingCheckStage()
        await waking.initialize(
            PipelineContext(effective, core.plugin_manager, "default", db)
        )

        async def generate(current_event):
            assert effective["test_profile"] == ("research", "writing")[index]
            assert current_event.get_extra("thinking_effort") == ("low", "xhigh")[index]
            assert current_event.get_extra("selected_provider") == "gpt-mini"
            assert current_event.get_extra("selected_model") == "gpt-4o-mini"
            assert not current_event.is_admin()
            await webchat_queue_mgr.put_back_queue(
                payload["message_id"],
                {
                    "type": "plain",
                    "data": f"{effective['test_profile']}:{current_event.get_extra('thinking_effort')}",
                    "message_id": payload["message_id"],
                    "streaming": True,
                },
            )

        pipeline = PipelineScheduler(
            PipelineContext(effective, core.plugin_manager, "default", db)
        )
        pipeline.stages = [waking, SimpleNamespace(process=generate)]
        await pipeline.execute(event)

    # Send carries a validated selection itself: no prior async PUT is needed.
    results = await asyncio.wait_for(
        asyncio.gather(
            *[model_reply(index) for index in range(2)],
            *[
                client.post(
                    "/api/v1/chat" if index == 0 else "/api/chat/send",
                    headers={"Authorization": f"Bearer {user['token']}"},
                    json={
                        "session_id": sessions[index],
                        "message": "hello",
                        "config_id": ("research", "writing")[index],
                        "thinking_effort": ("low", "xhigh")[index],
                        "selected_provider": "gpt-mini",
                        "selected_model": "gpt-4o-mini",
                    },
                )
                for index, user in enumerate(users)
            ],
        ),
        timeout=10,
    )
    for index, response in enumerate(results[2:]):
        assert ("research:low", "writing:xhigh")[index] in response.text
        assert ("writing:xhigh", "research:low")[index] not in response.text
    assert dict(core.astrbot_config) == config_before
    assert core.umop_config_router.umop_to_conf_id["webchat:*:*"] == "writing"

    # Re-login and a fresh database/router recover only this account's state.
    await db.engine.dispose()
    reopened = SQLiteDatabase(db.db_path)
    await reopened.initialize()
    preferences = SharedPreferences(reopened)
    router = UmopConfigRouter(preferences)
    await router.initialize()
    for index, user in enumerate(users):
        identity = await login(client, user["username"], USER_PASSWORD)
        settings = await client.get(
            "/api/v1/chat/ui-settings",
            headers={"Authorization": f"Bearer {identity['token']}"},
        )
        assert (
            settings.json()["data"]["thinking_effort"]["value"]
            == ("low", "xhigh")[index]
        )
        umo = (
            f"webchat:FriendMessage:webchat!{user['resource_owner']}!{sessions[index]}"
        )
        assert router.get_conf_id_for_umop(umo) == ("research", "writing")[index]
    await preferences.close()
    await reopened.engine.dispose()


@pytest.mark.asyncio
async def test_binding_ownership_defaults_and_forgery_leave_no_mutations(
    configured_chat,
):
    client, server, core, db, admin, users = configured_chat
    headers = {"Authorization": f"Bearer {users[0]['token']}"}
    created = await client.get("/api/v1/chat/sessions/new", headers=headers)
    session_id = created.json()["data"]["session_id"]
    path = f"/api/v1/chat/sessions/{session_id}/config"
    response = await client.put(path, headers=headers, json={"config_id": "default"})
    assert response.json()["data"] == {"config_id": "default"}
    response = await client.get(path, headers=headers)
    assert response.json()["data"] == {"config_id": "default"}
    umo = f"webchat:FriendMessage:webchat!{users[0]['resource_owner']}!{session_id}"
    assert core.astrbot_config_mgr.get_conf(umo) is core.astrbot_config
    invalid_preset = await client.put(
        "/api/v1/chat/ui-settings/value",
        headers=headers,
        json={"value": "low", "active_preset": "forged-definition"},
    )
    assert invalid_preset.status_code == 400
    assert (
        await db.get_preference(
            "dashboard_account", users[0]["account_id"], "thinking_effort"
        )
        is None
    )
    routes_before = dict(core.umop_config_router.umop_to_conf_id)
    config_before = copy.deepcopy(dict(core.astrbot_config))
    for actor in (users[1], admin):
        other_headers = {"Authorization": f"Bearer {actor['token']}"}
        for method in ("GET", "PUT"):
            response = await client.request(
                method,
                path,
                headers=other_headers,
                json={"config_id": "research"} if method == "PUT" else None,
            )
            assert response.json()["status"] == "error"
            assert response.json().get("data") is None
    for payload in (
        {"config_id": "missing"},
        {"config_id": ["research"]},
        {"config_id": "research", "umo": "webchat:*:*"},
        {"config_id": "research", "owner": users[0]["resource_owner"]},
    ):
        response = await client.put(path, headers=headers, json=payload)
        assert response.status_code == 422 or response.json()["status"] == "error"
    for injected in (
        {"config_id": "missing"},
        {"thinking_effort": " "},
        {"username": "original-admin"},
        {"umo": "webchat:*:*"},
        {"selected_provider": "gpt-mini", "selected_model": "arbitrary-model"},
        {"selected_provider": "disabled-or-missing"},
        {"role": "admin"},
        {"_dashboard_account": {"role": "admin"}},
    ):
        response = await client.post(
            "/api/v1/chat",
            headers=headers,
            json={"session_id": session_id, "message": "canary", **injected},
        )
        assert response.status_code == 400 or response.json()["status"] == "error"
    for method, target, payload in (
        ("GET", "/api/v1/config-routes", None),
        ("PUT", "/api/v1/config-routes/webchat:*:*", {"config_id": "research"}),
        ("GET", "/api/config/umo_abconf_routes", None),
        (
            "POST",
            "/api/config/umo_abconf_route/update",
            {"umo": "webchat:*:*", "conf_id": "research"},
        ),
        ("GET", "/api/v1/config-profiles/default", None),
        ("PUT", "/api/v1/chat/ui-settings/presets", {"presets": []}),
    ):
        response = await client.request(method, target, headers=headers, json=payload)
        assert response.status_code == 403, (target, response.text)
    assert dict(core.astrbot_config) == config_before
    assert core.umop_config_router.umop_to_conf_id == routes_before
    assert await db.count_platform_message_history("webchat", session_id) == 0


@pytest.mark.asyncio
async def test_administrator_keeps_configuration_and_chat_consumption(configured_chat):
    client, server, core, db, admin, users = configured_chat
    headers = {"Authorization": f"Bearer {admin['token']}"}
    session = await client.get("/api/v1/chat/sessions/new", headers=headers)
    session_id = session.json()["data"]["session_id"]
    response = await client.put(
        f"/api/v1/chat/sessions/{session_id}/config",
        headers=headers,
        json={"config_id": "research"},
    )
    assert response.json()["data"] == {"config_id": "research"}
    settings = await client.get("/api/v1/chat/ui-settings", headers=headers)
    assert settings.json()["data"]["can_edit"] is True
    response = await client.put(
        "/api/v1/chat/ui-settings/presets",
        headers=headers,
        json={"presets": [], "active_preset": "", "value": "max"},
    )
    assert response.json()["status"] == "ok"
    assert core.astrbot_config["chatui"]["thinking_effort"]["value"] == "max"
