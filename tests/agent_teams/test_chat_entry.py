"""Real platform events enter through the built-in explicit command."""

import asyncio
from types import SimpleNamespace

import pytest

from astrbot.core.message.components import Plain
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.star import StarMetadata, star_map
from astrbot.core.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)
from tests.agent_teams.test_chat_invocations import chat_env as chat_env


@pytest.mark.asyncio
async def test_onebot_command_uses_platform_sender_source_and_named_inputs(
    chat_env, monkeypatch
):
    """Carry a real OneBot sender and exact JSON text through the built-in entry.

    Args:
        chat_env: Real shared services, pinned role, caller and external execution port.
        monkeypatch: Isolate the actual command registry used by waking checks.
    """
    service, role, caller, observations = chat_env
    monkeypatch.setattr(star_handlers_registry, "_handlers", [])
    monkeypatch.setattr(star_handlers_registry, "star_handlers_map", {})
    from astrbot.builtin_stars.builtin_commands.main import Main

    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Report",
                "required_inputs": ["topic"],
                "result_task_id": "report",
                "tasks": [
                    {
                        "id": "report",
                        "name": "Report",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Research {topic}",
                        "inputs": {"topic": {"input": "topic"}},
                    }
                ],
            }
        },
    )
    await service.chat.save_access(
        "operator",
        asset["asset_id"],
        {
            "platform_id": "qq-main",
            "sender_id": "12345",
            "source": "qq-main:FriendMessage:12345",
            "version": 1,
            "profile_id": "default",
        },
    )
    sent = []

    async def send_private_msg(**kwargs):
        sent.append(kwargs)

    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "9999"
    message.sender = MessageMember("12345", "operator")
    message.message_id = "incoming-command"
    message.message_str = '/asset Report | {"topic":"  wind   energy  "}'
    message.message = [Plain(message.message_str)]
    event = AiocqhttpMessageEvent(
        message.message_str,
        message,
        PlatformMetadata("aiocqhttp", "test", "qq-main"),
        "12345",
        SimpleNamespace(send_private_msg=send_private_msg),
    )
    context = SimpleNamespace(
        workflow_service=service,
        astrbot_config_mgr=SimpleNamespace(
            get_conf_info=lambda source: {"id": "default"},
        ),
    )
    command = Main(context)
    metadata = StarHandlerMetadata(
        EventType.AdapterMessageEvent,
        "asset",
        "asset",
        "builtin_commands",
        Main.asset,
        [],
    )
    entry = CommandFilter("asset", handler_md=metadata)
    metadata.event_filters = [entry]
    monkeypatch.setattr(star_handlers_registry, "_handlers", [])
    monkeypatch.setattr(star_handlers_registry, "star_handlers_map", {})
    star_handlers_registry.append(metadata)
    monkeypatch.setitem(
        star_map, "builtin_commands", StarMetadata(name="builtin_commands")
    )
    waking = WakingCheckStage()
    await waking.initialize(
        PipelineContext(
            {
                "wake_prefix": ["/"],
                "platform_settings": {},
                "admins_id": [],
            },
            SimpleNamespace(context=context),
            "default",
            service.db,
        )
    )
    await waking.process(event)
    assert event.get_extra("activated_handlers") == [metadata]
    await command.asset(event, **event.get_extra("handlers_parsed_params")["asset"])
    runs = (await service.list_runs("operator"))["runs"]
    assert len(runs) == 1
    run = await service.get_run("operator", runs[0]["run_id"])
    assert run["snapshot"]["initiator"] == "platform:qq-main:12345"
    assert run["snapshot"]["source"] == "qq-main:FriendMessage:12345"
    assert run["snapshot"]["inputs"] == {"topic": "  wind   energy  "}
    assert event.is_stopped()
    assert sent[0]["user_id"] == 12345
    assert "Accepted" in str(sent[0])
    for _ in range(200):
        if (await service.get_run("operator", run["run_id"]))["status"] == "completed":
            break
        await asyncio.sleep(0.01)

    async def final_to_source(actual, text):
        assert actual.source == event.unified_msg_origin
        await event.send(event.plain_result(text))
        return True

    await service.chat.deliver_pending(final_to_source)
    assert "Verified final report." in str(sent)
    assert "PRIVATE_THOUGHT" not in str(sent)
