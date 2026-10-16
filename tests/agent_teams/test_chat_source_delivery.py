"""Explicit asset output must retain the actual platform audience."""

from types import SimpleNamespace

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.chat import ChatCaller
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
    AiocqhttpAdapter,
)
from astrbot.core.platform.sources.misskey.misskey_adapter import MisskeyPlatformAdapter
from astrbot.core.platform.sources.satori.satori_adapter import SatoriPlatformAdapter
from astrbot.core.platform.sources.wecom_ai_bot.wecomai_adapter import WecomAIBotAdapter
from astrbot.core.platform.sources.wecom_ai_bot.wecomai_event import (
    WecomAIBotMessageEvent,
)
from astrbot.core.platform.sources.wecom_ai_bot.wecomai_queue_mgr import WecomAIQueueMgr
from astrbot.core.platform.sources.wecom_ai_bot.wecomai_webhook import (
    WecomAIBotWebhookClient,
)
from astrbot.core.star.context import Context
from tests.agent_teams.test_chat_invocations import chat_env as chat_env


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["wecom_ai_bot", "misskey", "satori"])
async def test_background_delivery_retains_pending_notices_when_audience_is_unknown(
    chat_env, kind
):
    """Reject real adapter fallback paths and retain results for a safe reconnect.

    Args:
        chat_env: Real shared services and persistence.
        kind: Adapter whose generic proactive API loses audience or account state.
    """
    service, _, _, _ = chat_env
    outbound = []

    async def external_send(*args, **kwargs):
        outbound.append((args, kwargs))
        return {"ok": True}

    if kind == "wecom_ai_bot":
        adapter = object.__new__(WecomAIBotAdapter)
        adapter.metadata = PlatformMetadata(kind, "test", "instance")
        adapter.webhook_client = WecomAIBotWebhookClient(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=OTHER_GROUP"
        )
        adapter.webhook_client.send_payload = external_send
        source = "instance:FriendMessage:12345"
    elif kind == "misskey":
        adapter = object.__new__(MisskeyPlatformAdapter)
        adapter.config = {"id": "instance"}
        adapter._user_cache = {}
        adapter.bot_self_id = "bot"
        adapter.max_message_length = 3000
        adapter.default_visibility = "public"
        adapter.enable_file_upload = True
        adapter.upload_folder = None
        adapter.local_only = False
        adapter.api = SimpleNamespace(create_note=external_send)
        source = "instance:OtherMessage:note%12345"
    else:
        adapter = object.__new__(SatoriPlatformAdapter)
        adapter.metadata = PlatformMetadata(kind, "test", "instance")
        adapter.logins = [
            {"platform": "telegram", "user": {"id": "other-bot"}},
            {"platform": "discord", "user": {"id": "original-bot"}},
        ]
        adapter.send_http_request = external_send
        source = "instance:FriendMessage:channel42"
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[adapter]),
        get_config=lambda **kwargs: {},
    )
    assert not await Context.send_message(
        context, source, MessageChain().message("PRIVATE"), require_exact_source=True
    )
    caller = ChatCaller(
        platform_id="instance", sender_id="12345", source=source, profile_id="default"
    )
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Private",
                "tasks": [
                    {
                        "id": "answer",
                        "name": "Answer",
                        "human": {"kind": "question", "question": "Private?"},
                    }
                ],
            }
        },
    )
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    accepted = await service.chat.invoke(caller, asset["asset_id"], {})
    run = await service.get_run("operator", accepted["run_id"])
    await service.answer_todo(
        "operator",
        run["run_id"],
        run["todos"][0]["todo_id"],
        {"request_id": "answer", "action": "answer", "answer": "PRIVATE_FINAL"},
    )

    async def background(actual, text):
        return await Context.send_message(
            context,
            actual.source,
            MessageChain().message(text),
            require_exact_source=True,
        )

    assert await service.chat.deliver_pending(background) == 0
    assert outbound == []
    reconnected = []

    async def exact_event(actual, text):
        assert actual == caller
        reconnected.append(text)
        return True

    assert await service.chat.deliver_pending(exact_event, run["run_id"]) == 2
    assert "PRIVATE_FINAL" in reconnected[-1]


@pytest.mark.asyncio
async def test_exact_background_delivery_preserves_onebot_private_destination():
    """Keep a source-addressed adapter working through its real send path."""
    sent = []

    async def private_send(**kwargs):
        sent.append(kwargs)

    adapter = object.__new__(AiocqhttpAdapter)
    adapter.metadata = PlatformMetadata("aiocqhttp", "test", "qq")
    adapter.bot = SimpleNamespace(send_private_msg=private_send)
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[adapter]),
        get_config=lambda **kwargs: {},
    )
    assert await Context.send_message(
        context,
        "qq:FriendMessage:12345",
        MessageChain().message("PRIVATE_FINAL"),
        require_exact_source=True,
    )
    assert sent[0]["user_id"] == 12345
    assert "PRIVATE_FINAL" in str(sent[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connection", ["webhook", "long_connection", "long_connection_reject"]
)
async def test_wecom_asset_reply_uses_original_response_and_reconnects_after_expiry(
    chat_env, connection
):
    """Exercise actual command replies without the unrelated configured webhook.

    Args:
        chat_env: Real persisted asset/run services.
        connection: Existing source-specific queue or long-connection response.
    """
    from astrbot.builtin_stars.builtin_commands.main import Main

    service, _, _, _ = chat_env
    webhook_sent = []
    source_sent = []
    webhook = WecomAIBotWebhookClient(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=OTHER_GROUP"
    )

    async def webhook_post(payload):
        webhook_sent.append(payload)

    async def native_send(req_id, payload):
        source_sent.append((req_id, payload))
        return connection != "long_connection_reject" or req_id.endswith("fresh")

    webhook.send_payload = webhook_post
    queue = WecomAIQueueMgr()
    connection_mode = "webhook" if connection == "webhook" else "long_connection"
    queue.set_pending_response(
        "original", {"connection_mode": connection_mode, "req_id": "private-a"}
    )
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Private",
                "tasks": [
                    {
                        "id": "answer",
                        "name": "Answer",
                        "human": {"kind": "question", "question": "Private?"},
                    }
                ],
            }
        },
    )
    caller = ChatCaller(
        platform_id="wecom",
        sender_id="12345",
        source="wecom:FriendMessage:12345",
        profile_id="default",
    )
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.sender = MessageMember("12345", "A")
    message.message_id = "command"
    message.message_str = "/asset " + asset["asset_id"]
    message.raw_message = {"stream_id": "original"}
    event = WecomAIBotMessageEvent(
        message.message_str,
        message,
        PlatformMetadata("wecom_ai_bot", "test", "wecom"),
        "12345",
        None,
        queue,
        webhook,
        True,
        native_send,
    )
    context = SimpleNamespace(
        workflow_service=service,
        astrbot_config_mgr=SimpleNamespace(
            get_conf_info=lambda source: {"id": "default"}
        ),
    )
    command = Main(context)
    await command.asset(event, asset["asset_id"])
    assert webhook_sent == []
    run = (await service.list_runs("operator"))["runs"][0]
    run = await service.get_run("operator", run["run_id"])
    queue.remove_queues("original")
    await service.answer_todo(
        "operator",
        run["run_id"],
        run["todos"][0]["todo_id"],
        {"request_id": "answer", "action": "answer", "answer": "PRIVATE_FINAL"},
    )

    async def unavailable(actual, text):
        return False

    assert await service.chat.deliver_pending(unavailable) == 0
    queue.set_pending_response(
        "fresh", {"connection_mode": connection_mode, "req_id": "private-a-fresh"}
    )
    message.raw_message = {"stream_id": "fresh"}
    message.message_str = "/asset status " + run["run_id"]
    fresh = WecomAIBotMessageEvent(
        message.message_str,
        message,
        event.platform_meta,
        "12345",
        None,
        queue,
        webhook,
        True,
        native_send,
    )
    await command.asset(fresh, "status " + run["run_id"])
    assert webhook_sent == []
    if connection_mode == "long_connection":
        assert source_sent[-1][0] == "private-a-fresh"
        assert "PRIVATE_FINAL" in str(source_sent[-1])
    else:
        assert "PRIVATE_FINAL" in str(
            queue.get_or_create_back_queue("fresh").get_nowait()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,source",
    [
        ("satori", "instance:FriendMessage:channel42"),
        ("misskey", "instance:OtherMessage:note%12345"),
    ],
)
async def test_ambiguous_platform_sources_cannot_read_or_invoke_an_opened_asset(
    chat_env, kind, source
):
    """Do not authorize different audiences/accounts collapsed into one UMO.

    Args:
        chat_env: Actual shared application service.
        kind: Platform whose source lacks the necessary account/audience identity.
        source: Real source shape shared by the ambiguous conversations.
    """
    service, _, _, _ = chat_env
    caller = ChatCaller(
        platform_id="instance", sender_id="12345", source=source, profile_id="default"
    )
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Private",
                "tasks": [
                    {
                        "id": "answer",
                        "name": "Answer",
                        "human": {"kind": "question", "question": "Private?"},
                    }
                ],
            }
        },
    )
    await service.chat.save_access(
        "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
    )
    service.roles.core_lifecycle = SimpleNamespace(
        platform_manager=SimpleNamespace(
            platform_insts=[
                SimpleNamespace(meta=lambda: PlatformMetadata(kind, "test", "instance"))
            ]
        ),
        astrbot_config_mgr=SimpleNamespace(
            get_conf_info=lambda source: {"id": "default"}
        ),
    )
    assert (await service.chat.list_assets(caller))["assets"] == []
    with pytest.raises(RoleAssetError):
        await service.chat.invoke(caller, asset["asset_id"], {})
    assert (await service.list_runs("operator"))["runs"] == []
