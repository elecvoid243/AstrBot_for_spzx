"""A stale interaction must not suppress an available exact-source delivery."""

import json
from types import SimpleNamespace

import discord
import pytest

from astrbot.builtin_stars.builtin_commands.main import Main
from astrbot.core.agent_teams.chat import ChatCaller
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.discord.discord_platform_adapter import (
    DiscordPlatformAdapter,
)
from astrbot.core.platform.sources.discord.discord_platform_event import (
    DiscordPlatformEvent,
)
from astrbot.core.star.context import Context
from tests.agent_teams.test_chat_invocations import chat_env as chat_env


@pytest.mark.asyncio
async def test_expired_interaction_does_not_starve_available_source_delivery(
    chat_env, tmp_path
):
    """Recover the exact source after a live Discord interaction expires.

    Args:
        chat_env: Actual shared service and controlled external execution boundary.
        tmp_path: Isolated raw transport observations.
    """
    service, _, _, _ = chat_env
    channel_messages = []
    interaction_messages = []
    interaction_failures = []
    interaction_expired = False

    class ExternalChannel(discord.abc.Messageable):
        id = 4321
        name = "source-channel"
        guild = SimpleNamespace(name="source-guild")

        async def _get_channel(self):
            return self

        async def send(self, **payload):
            channel_messages.append({"channel_id": self.id, **payload})
            return SimpleNamespace(id=9876)

    channel = ExternalChannel()

    async def followup_send(**payload):
        if interaction_expired:
            interaction_failures.append(payload)
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"),
                {"code": 10015, "message": "Unknown Webhook"},
            )
        interaction_messages.append(payload)
        return SimpleNamespace(id=6789)

    client = SimpleNamespace(
        user=SimpleNamespace(display_name="Bot"),
        get_channel=lambda channel_id: channel if channel_id == channel.id else None,
    )
    adapter = object.__new__(DiscordPlatformAdapter)
    adapter.config = {"id": "discord-live"}
    adapter.bot_self_id = "9999"
    adapter.client = client
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[adapter]),
        get_config=lambda **kwargs: {},
        workflow_service=service,
        astrbot_config_mgr=SimpleNamespace(
            get_conf_info=lambda source: {"id": "default"}
        ),
    )
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Interaction delivery",
                "result_task_id": "question",
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
    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE
    message.group_id = "4321"
    message.self_id = "9999"
    message.sender = MessageMember("12345", "Initiator")
    message.message_id = "1111"
    message.message_str = "/asset " + asset["asset_id"]
    message.message = [Plain(message.message_str)]
    event = DiscordPlatformEvent(
        message.message_str,
        message,
        PlatformMetadata("discord", "probe", "discord-live"),
        "4321",
        client,
        interaction_followup_webhook=SimpleNamespace(send=followup_send),
    )
    caller = ChatCaller(
        platform_id=event.get_platform_id(),
        sender_id=event.get_sender_id(),
        source=event.unified_msg_origin,
        profile_id="default",
    )
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    await Main(context).asset(event, "")
    assert event.is_stopped()
    runs = (await service.list_runs("operator"))["runs"]
    assert len(runs) == 1
    run = await service.get_run("operator", runs[0]["run_id"])
    assert len(interaction_messages) == 2 and run["todos"][0]["status"] == "pending"

    interaction_expired = True
    await service.answer_todo(
        "operator",
        run["run_id"],
        run["todos"][0]["todo_id"],
        {"request_id": "answer", "action": "answer", "answer": "DISCORD_FINAL_RESULT"},
    )
    completed = await service.chat.get_run(caller, run["run_id"])
    assert completed["status"] == "completed"

    async def background(actual, text):
        return await Context.send_message(
            context,
            actual.source,
            MessageChain().message(text),
            require_exact_source=True,
        )

    positive = await background(caller, "POSITIVE_EXACT_SOURCE_CONTROL")
    assert (
        positive
        and len(channel_messages) == 1
        and channel_messages[0]["channel_id"] == 4321
    )
    first = await service.chat.deliver_pending(background)
    second = await service.chat.deliver_pending(background)
    observations = {
        "scope": "Actual Main command, SQLite WorkflowService, Discord event and exact-source adapter; only external channel and interaction transport controlled. No OS crash or live Discord claim.",
        "caller": caller.model_dump(),
        "run": completed,
        "accepted_interaction_messages": interaction_messages,
        "rejected_interaction_calls": interaction_failures,
        "positive_exact_source_accepted": positive,
        "first_background_deliveries": first,
        "second_background_deliveries": second,
        "channel_messages": channel_messages,
    }
    (tmp_path / "observations.json").write_text(
        json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert first + second == 1, (
        "Expired live interaction is retained forever and prevents available exact-source final delivery"
    )
    assert any(
        "DISCORD_FINAL_RESULT" in entry.get("content", "") for entry in channel_messages
    )

    assert await service.chat.deliver_pending(background) == 0
