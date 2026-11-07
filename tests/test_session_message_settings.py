"""Instance policy, local chain budgets and ordinary ChatUI admission."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.builtin_stars.session_messages.tools import (
    AgentMessageSource,
    ListChatSessionsTool,
    ReadChatSessionTool,
    SendChatMessageTool,
    SessionMessageChain,
)
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.config.default import CONFIG_METADATA_2, DEFAULT_CONFIG
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.provider.entities import LLMResponse
from astrbot.dashboard.services.chat_service import ChatServiceError
from astrbot.dashboard.services.config_service import (
    ConfigProfileService,
    validate_config,
)
from tests.test_chat_session_creation import creation as creation
from tests.test_chat_session_messages import ControlledProvider, tool_call, wait_until
from tests.test_chat_session_messages import account_dashboard as account_dashboard
from tests.test_chat_session_messages import fake_core_lifecycle as fake_core_lifecycle
from tests.test_chat_session_messages import messaging as messaging
from tests.test_chat_session_messages import ordinary_chat as ordinary_chat


@pytest.mark.asyncio
async def test_instance_settings_persist_and_seed_existing_config(tmp_path):
    """Save via the same service as Settings and reload the actual config file.

    Args:
        tmp_path: Isolated configuration storage.
    """
    path = tmp_path / "cmd_config.json"
    path.write_text("{}", encoding="utf-8")
    defaults = {"session_messages": deepcopy(DEFAULT_CONFIG["session_messages"])}
    config = AstrBotConfig(config_path=str(path), default_config=defaults, schema=None)
    assert config["session_messages"] == {"enable": True, "max_chain_sends": 15}
    lifecycle = SimpleNamespace(
        astrbot_config_mgr=SimpleNamespace(confs={"default": config}),
        reload_pipeline_scheduler=AsyncMock(),
    )
    service = ConfigProfileService(lifecycle, runtime={})
    await service.update_profile(
        "default",
        {
            "session_messages": {"enable": False, "max_chain_sends": 23},
            "agent_runner": deepcopy(DEFAULT_CONFIG["agent_runner"]),
        },
    )
    reloaded = AstrBotConfig(
        config_path=str(path), default_config=defaults, schema=None
    )
    assert reloaded["session_messages"] == {"enable": False, "max_chain_sends": 23}
    metadata = service.get_system_schema()["metadata"]["system_group"]["metadata"]
    assert "session_messages.enable" in metadata["system"]["items"]
    assert "session_messages" not in json.dumps(
        service.get_profile_schema()["metadata"]
    )


@pytest.mark.parametrize("value", [0, -1, 1.5, True, False, None, "15", "", [], {}])
def test_limit_rejects_non_positive_or_non_integer_input(value):
    """Reject invalid budgets before generic config coercion changes their type.

    Args:
        value: Invalid public setting value.
    """
    errors, _ = validate_config(
        {"session_messages": {"max_chain_sends": value}}, CONFIG_METADATA_2, True
    )
    assert any("positive integer" in error for error in errors)


@pytest.mark.parametrize("settings", [None, [], False, {"enable": None}, {"enable": 1}])
def test_switch_rejects_invalid_input(settings):
    """Reject malformed settings and non-boolean switches.

    Args:
        settings: Invalid public settings object.
    """
    errors, _ = validate_config({"session_messages": settings}, CONFIG_METADATA_2, True)
    assert errors


@pytest.mark.asyncio
async def test_settings_api_is_admin_only_and_invalid_update_does_not_persist(creation):
    """Enforce administrator editing and atomic validation via real HTTP.

    Args:
        creation: Temporary HTTP app, accounts and real config manager.
    """
    m, _, _ = creation
    headers = {"Authorization": f"Bearer {m.admin['token']}"}
    config = (await m.client.get("/api/v1/system-config", headers=headers)).json()[
        "data"
    ]["config"]
    for actor in (m.headers, headers):
        invalid = deepcopy(config)
        invalid["session_messages"] = {"enable": False, "max_chain_sends": 0}
        result = await m.client.put(
            "/api/v1/system-config", headers=actor, json=invalid
        )
        assert result.status_code >= 400 or result.json()["status"] == "error"
        assert m.service.require_session_message_limit() == 15
    config["session_messages"] = {"enable": False, "max_chain_sends": 4}
    saved = await m.client.put("/api/v1/system-config", headers=headers, json=config)
    assert saved.json()["status"] == "ok", saved.text
    with pytest.raises(ChatServiceError, match="disabled"):
        m.service.require_session_message_limit()
    config["session_messages"]["enable"] = True
    saved = await m.client.put("/api/v1/system-config", headers=headers, json=config)
    assert saved.json()["status"] == "ok", saved.text
    assert m.service.require_session_message_limit() == 4


@pytest.mark.asyncio
async def test_disabled_policy_blocks_all_tools_and_direct_agent_receiving(creation):
    """Check both tool execution and receiving while ordinary human chat continues.

    Args:
        creation: Real account-owned sessions and trusted main Agent context.
    """
    m, context, invoke = creation
    cached = await invoke({"title": "Already created"}, "cached-create")
    m.ids.append(cached["session_id"])
    event = context.context.event
    a, b = m.ids[:2]
    chain = SessionMessageChain()
    source = AgentMessageSource(event.get_extra("_dashboard_account"), a, "A", chain)
    profile = deepcopy(dict(m.core.astrbot_config))
    profile["session_messages"] = {"enable": True, "max_chain_sends": 100}
    m.core.astrbot_config_mgr.confs["permissive-profile"] = profile
    selected = await m.client.put(
        f"/api/v1/chat/sessions/{a}/config",
        headers=m.headers,
        json={"config_id": "permissive-profile"},
    )
    assert selected.json()["status"] == "ok"
    m.core.astrbot_config["session_messages"]["enable"] = False
    for tool, arguments in (
        (ListChatSessionsTool(), {}),
        (ReadChatSessionTool(), {"session_id": b}),
        (SendChatMessageTool(), {"session_id": b, "text": "blocked"}),
    ):
        assert "disabled" in await tool.call(context, **arguments)
    assert "disabled" in await invoke({"title": "Blocked creation"})
    assert "disabled" in await invoke({"title": "Already created"}, "cached-create")
    with pytest.raises(ChatServiceError, match="disabled"):
        await m.service.build_chat_stream(
            m.user["resource_owner"],
            {"session_id": b, "message": "blocked"},
            agent_source=source,
        )
    assert chain.count == 0
    assert not await m.db.get_platform_message_history("webchat", b)
    response = await m.client.post(
        "/api/v1/chat",
        headers=m.headers,
        json={"session_id": a, "message": "ordinary human message"},
    )
    assert "Created session response" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disable", "lower", "raise"])
async def test_concurrent_admission_rechecks_instance_policy(
    creation, monkeypatch, change
):
    """Recheck policy after asynchronous preparation without overspending a slot.

    Args:
        creation: Trusted sender and ordinary admission service.
        monkeypatch: Pauses both sends before their final admission gate.
        change: Switch off, tighten the limit or attempt to loosen it.
    """
    m, context, _ = creation
    a, b = m.ids
    c = (
        await m.db.create_platform_session(creator=m.user["resource_owner"])
    ).session_id
    m.ids.append(c)
    settings = m.core.astrbot_config["session_messages"]
    settings["max_chain_sends"] = 3
    chain = SessionMessageChain(count=2, sessions={a})
    event = context.context.event
    event.set_extra("_session_message_chain", chain)
    prepared = []
    release = asyncio.Event()
    original = m.service.validate_chat_selection

    async def paused_selection(*args, **kwargs):
        result = await original(*args, **kwargs)
        prepared.append(args[1])
        await release.wait()
        return result

    monkeypatch.setattr(m.service, "validate_chat_selection", paused_selection)
    callback = webchat_queue_mgr._listener_callback
    await webchat_queue_mgr.clear_listener()
    tasks = [
        asyncio.create_task(m.service.send_session_message(event, sid, "racing input"))
        for sid in (b, c)
    ]
    try:
        await wait_until(lambda: len(prepared) == 2)
        if change == "disable":
            settings["enable"] = False
        else:
            settings["max_chain_sends"] = 2 if change == "lower" else 10
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        accepted = [result for result in results if isinstance(result, dict)]
        assert len(accepted) == (1 if change == "raise" else 0), results
        assert chain.count == (3 if change == "raise" else 2)
        if change != "disable":
            assert chain.max_sends == (2 if change == "lower" else 3)
        for sid in (b, c):
            history = await m.db.get_platform_message_history("webchat", sid)
            assert len(history) == sum(
                result["session_id"] == sid for result in accepted
            )
        webchat_queue_mgr.set_listener(callback)
        await wait_until(lambda: not m.service.chat_runs_by_session)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        webchat_queue_mgr.set_listener(callback)


@pytest.mark.asyncio
async def test_toggle_does_not_cancel_started_execution(creation):
    """Already started ordinary execution finishes after disabling Agent access.

    Args:
        creation: Real model execution and ChatUI output collector.
    """
    m, _, _ = creation
    started = asyncio.Event()
    release = asyncio.Event()

    async def respond(kwargs):
        started.set()
        await release.wait()
        return LLMResponse(role="assistant", completion_text="finished after disabling")

    m.providers["gpt-mini"] = ControlledProvider("gpt-mini", respond)
    request = asyncio.create_task(
        m.client.post(
            "/api/v1/chat",
            headers=m.headers,
            json={"session_id": m.ids[0], "message": "start"},
        )
    )
    try:
        await wait_until(started.is_set)
        m.core.astrbot_config["session_messages"]["enable"] = False
        release.set()
        response = await asyncio.wait_for(request, 10)
        assert "finished after disabling" in response.text
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
async def test_human_follow_up_starts_fresh_chain_on_active_agent_recipient(creation):
    """A real human can start a new chain while the recipient is still executing.

    Args:
        creation: Real main Agent tool loop, adapter and ordinary follow-up path.
    """
    m, context, _ = creation
    a, b = m.ids
    started = asyncio.Event()

    class WaitForHuman(FunctionTool):
        async def call(self, context, **kwargs):
            started.set()
            await wait_until(
                lambda: bool(
                    context.context.extra["main_agent_runner"]._pending_follow_ups
                )
            )
            return "human follow-up is ready"

    m.context.add_llm_tools(
        WaitForHuman(
            name="wait_for_human",
            description="Wait for a human follow-up.",
            parameters={"type": "object", "properties": {}},
        )
    )

    async def respond(kwargs):
        text = json.dumps(kwargs.get("contexts", []))
        if "human requested reply" in text:
            return LLMResponse(role="assistant", completion_text="reply accepted")
        if "fresh human follow-up" in text:
            return tool_call(
                "send_chat_message", {"session_id": a, "text": "human requested reply"}
            )
        return tool_call("wait_for_human", {})

    m.providers["gpt-mini"] = ControlledProvider("gpt-mini", respond)
    m.core.astrbot_config["session_messages"]["max_chain_sends"] = 1
    await m.service.send_session_message(
        context.context.event, b, "initial Agent input"
    )
    old_chain = context.context.event.get_extra("_session_message_chain")
    await wait_until(started.is_set)
    response = await asyncio.wait_for(
        m.client.post(
            "/api/v1/chat",
            headers=m.headers,
            json={"session_id": b, "message": "fresh human follow-up"},
        ),
        15,
    )
    assert response.status_code == 200
    await wait_until(lambda: not m.service.chat_runs_by_session)
    new_chain = m.events[0].get_extra("_session_message_chain")
    assert new_chain is not old_chain
    assert old_chain.count == new_chain.count == 1
    history = await m.db.get_platform_message_history("webchat", a)
    assert any(
        row.content.get("agent_source", {}).get("session_id") == b for row in history
    )
