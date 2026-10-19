"""Explicit asset output must retain the actual platform audience."""

import asyncio
import json
from types import SimpleNamespace

import httpx
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
from astrbot.core.platform.sources.discord.discord_platform_adapter import (
    DiscordPlatformAdapter,
)
from astrbot.core.platform.sources.misskey.misskey_adapter import MisskeyPlatformAdapter
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    QQOfficialPlatformAdapter,
)
from astrbot.core.platform.sources.satori.satori_adapter import SatoriPlatformAdapter
from astrbot.core.platform.sources.slack.slack_adapter import SlackAdapter
from astrbot.core.platform.sources.webchat import webchat_adapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import WebChatQueueMgr
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
@pytest.mark.parametrize(
    "kind",
    [
        "discord",
        "discord_no_channel",
        "slack",
        "misskey",
        "misskey_api_error",
        "webchat",
    ],
)
async def test_strict_source_delivery_rejects_adapter_no_send_paths(
    tmp_path, monkeypatch, kind
):
    """Keep adapter refusal distinct from its legacy ordinary-chat return value.

    Args:
        tmp_path: Isolated attachment storage.
        monkeypatch: Controls only the external persistence boundary.
        kind: Adapter with a known unavailable transport or persistence path.
    """

    async def unavailable(*args, **kwargs):
        raise RuntimeError("EXTERNAL_BOUNDARY_UNAVAILABLE")

    async def missing_channel(*args):
        return None

    if kind.startswith("discord"):
        adapter = object.__new__(DiscordPlatformAdapter)
        adapter.config = {"id": "instance"}
        adapter.bot_self_id = "9999"
        adapter.client = SimpleNamespace(
            user=SimpleNamespace(display_name="Bot")
            if kind == "discord_no_channel"
            else None,
            get_channel=lambda channel_id: None,
            fetch_channel=missing_channel,
        )
    elif kind == "slack":
        adapter = object.__new__(SlackAdapter)
        adapter.metadata = PlatformMetadata(kind, "test", "instance")
        adapter.web_client = SimpleNamespace(chat_postMessage=unavailable)
    elif kind.startswith("misskey"):
        adapter = object.__new__(MisskeyPlatformAdapter)
        adapter.config = {"id": "instance"}
        adapter.api = (
            SimpleNamespace(send_message=unavailable)
            if kind == "misskey_api_error"
            else None
        )
        adapter._user_cache = {}
        adapter.max_message_length = 3000
        adapter.enable_file_upload = False
    else:
        adapter = object.__new__(webchat_adapter.WebChatAdapter)
        adapter.metadata = PlatformMetadata(kind, "test", "instance")
        adapter._webchat_queue_mgr = WebChatQueueMgr()
        adapter.attachments_dir = tmp_path
        monkeypatch.setattr(
            webchat_adapter,
            "db_helper",
            SimpleNamespace(
                insert_attachment=unavailable,
                insert_platform_message_history=unavailable,
            ),
        )
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[adapter]),
        get_config=lambda **kwargs: {},
    )
    source = (
        "instance:OtherMessage:chat%user-a"
        if kind.startswith("misskey")
        else "instance:FriendMessage:123"
    )
    assert not await Context.send_message(
        context,
        source,
        MessageChain().message("SAFE_FINAL"),
        require_exact_source=True,
    )
    assert await Context.send_message(
        context, source, MessageChain().message("LEGACY_ORDINARY_CHAT")
    )


@pytest.mark.asyncio
async def test_cold_qq_preserves_notifications_until_its_source_recovers(chat_env):
    """Recover both notices after rebuilding the service and native QQ cache.

    Args:
        chat_env: Actual shared service, SQLite and asset definitions.
    """
    from astrbot.core.agent_teams.workflows import WorkflowService

    service, _, _, _ = chat_env
    caller = ChatCaller(
        platform_id="qq-cold",
        sender_id="user-a",
        source="qq-cold:GroupMessage:group42",
        profile_id="default",
    )
    asset = await service.save_asset(
        "operator",
        {
            "definition": {
                "name": "Source receipt",
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
    accepted = await service.chat.invoke(caller, asset["asset_id"], {})
    run = await service.get_run("operator", accepted["run_id"])
    await service.answer_todo(
        "operator",
        run["run_id"],
        run["todos"][0]["todo_id"],
        {
            "request_id": "answer",
            "action": "answer",
            "answer": "QQ_FINAL_RESULT",
        },
    )
    await service.shutdown()
    reopened = WorkflowService(
        service.db, service.roles, ports_factory=service.ports_factory
    )
    outbound = []

    async def external_send(**payload):
        outbound.append(payload)
        return {"id": "external-response"}

    adapter = object.__new__(QQOfficialPlatformAdapter)
    adapter.config = {"id": "qq-cold"}
    adapter._session_last_message_id = {}
    adapter._session_scene = {}
    adapter._allow_group_proactive_send = True
    adapter.use_markdown_default = False
    adapter.client = SimpleNamespace(
        api=SimpleNamespace(post_group_message=external_send)
    )
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[adapter]),
        get_config=lambda **kwargs: {},
    )

    async def background(actual, text):
        return await Context.send_message(
            context,
            actual.source,
            MessageChain().message(text),
            require_exact_source=True,
        )

    try:
        assert await reopened.chat.deliver_pending(background) == 0
        assert outbound == []
        adapter.remember_session_scene("group42", "group")
        assert await background(caller, "POSITIVE_SOURCE_CONTROL")
        assert len(outbound) == 1 and outbound[0]["group_openid"] == "group42"
        assert await reopened.chat.deliver_pending(background) == 2
        assert len(outbound) == 3
        assert "QQ_FINAL_RESULT" in str(outbound[-1])
    finally:
        await reopened.shutdown()


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
@pytest.mark.parametrize("connection", ["long_connection", "long_connection_reject"])
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
    connection_mode = "long_connection"
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
    assert source_sent[-1][0] == "private-a-fresh"
    assert "PRIVATE_FINAL" in str(source_sent[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["native", "webhook_only", "no_initial_response"])
async def test_wecom_http_asset_notices_require_an_advertised_response_stream(
    chat_env, mode
):
    """Receive asset notices through real encrypted HTTP stream responses.

    Args:
        chat_env: Actual shared asset and human-request persistence.
        mode: A native response, webhook-only delivery, or no initial stream.
    """
    from astrbot.builtin_stars.builtin_commands.main import Main

    service, _, _, _ = chat_env
    adapter = WecomAIBotAdapter(
        {
            "id": "wecom-http",
            "port": 0,
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
            "msg_push_webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=OTHER_GROUP",
            "only_use_webhook_url_to_send": mode == "webhook_only",
            "wecomaibot_init_respond_text": ""
            if mode == "no_initial_response"
            else "Processing",
        },
        {},
        asyncio.Queue(),
    )
    webhook_sent = []

    async def webhook_post(payload):
        webhook_sent.append(payload)

    adapter.webhook_client.send_payload = webhook_post
    context = SimpleNamespace(
        workflow_service=service,
        astrbot_config_mgr=SimpleNamespace(
            get_conf_info=lambda source: {"id": "default"}
        ),
    )
    command = Main(context)
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
    inbound = {
        "msgtype": "text",
        "msgid": "asset",
        "from": {"userid": "12345"},
        "chattype": "single",
        "text": {"content": "/asset " + asset["asset_id"]},
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=adapter.server.get_app()),
            base_url="http://wecom-test",
        ) as client:

            async def callback(payload):
                """Send and decrypt one real platform HTTP callback.

                Args:
                    payload: Incoming user message or stream poll.

                Returns:
                    The decrypted response, or None for an unadvertised stream.
                """
                encrypted = await adapter.api_client.encrypt_message(
                    json.dumps(payload), "1234", "1234567890"
                )
                envelope = json.loads(encrypted)
                response = await client.post(
                    "/webhook/wecom-ai-bot",
                    content=encrypted,
                    params={
                        "msg_signature": envelope["msgsignature"],
                        "nonce": "1234",
                        "timestamp": "1234567890",
                    },
                )
                assert response.status_code == 200
                if response.text == "success":
                    return None
                returned = response.json()
                code, decoded = await adapter.api_client.decrypt_message(
                    response.content,
                    returned["msgsignature"],
                    returned["timestamp"],
                    returned["nonce"],
                )
                assert code == 0
                return decoded

            advertised = await callback(inbound)
            stream = next(iter(adapter.queue_mgr.queues))
            queued = adapter.queue_mgr.queues[stream].get_nowait()
            event = adapter.create_event(await adapter.convert_message(queued))
            caller = ChatCaller(
                platform_id=event.get_platform_id(),
                sender_id=event.get_sender_id(),
                source=event.unified_msg_origin,
                profile_id="default",
            )
            await service.chat.save_access(
                "operator", asset["asset_id"], {**caller.model_dump(), "version": 1}
            )
            await command.asset(event, asset["asset_id"])
            if mode != "native":
                assert advertised is None
                assert (await service.list_runs("operator"))["runs"] == []
                assert webhook_sent == []
                with pytest.raises(RuntimeError, match="original WeCom response"):
                    await event.send(event.plain_result("PRIVATE"), source_only=True)
                return
            assert advertised["stream"]["id"] == stream
            acceptance = await callback({"msgtype": "stream", "stream": {"id": stream}})
            assert "Accepted" in acceptance["stream"]["content"]
            run = (await service.list_runs("operator"))["runs"][0]
            run = await service.get_run("operator", run["run_id"])
            adapter.queue_mgr.remove_queues(stream)
            await service.answer_todo(
                "operator",
                run["run_id"],
                run["todos"][0]["todo_id"],
                {
                    "request_id": "answer",
                    "action": "answer",
                    "answer": "PRIVATE_HTTP_FINAL",
                },
            )
            status = {
                **inbound,
                "msgid": "status",
                "text": {"content": "/asset status " + run["run_id"]},
            }
            fresh_response = await callback(status)
            fresh_stream = fresh_response["stream"]["id"]
            queued = adapter.queue_mgr.queues[fresh_stream].get_nowait()
            fresh_event = adapter.create_event(await adapter.convert_message(queued))
            await command.asset(fresh_event, "status " + run["run_id"])
            final = await callback(
                {"msgtype": "stream", "stream": {"id": fresh_stream}}
            )
            assert "PRIVATE_HTTP_FINAL" in final["stream"]["content"]
            assert webhook_sent == []
    finally:
        await adapter.terminate()


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
