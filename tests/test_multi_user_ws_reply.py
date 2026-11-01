"""Owned quote consumption through both public Dashboard WebSocket routes."""

import asyncio
import copy
from types import SimpleNamespace

import pytest

from astrbot.core.message.components import Reply
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat
from tests.test_multi_user_chat_config import configured_chat as configured_chat
from tests.test_multi_user_runtime import ASGIWebSocket


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/unified-chat/ws", "/api/unified_chat/ws"])
async def test_websocket_quotes_validate_records_before_binding_or_history(
    configured_chat, path
):
    client, server, core, db, admin, users = configured_chat
    user = users[0]
    headers = {"Authorization": f"Bearer {user['token']}"}
    sessions = []
    for account in [user, user, users[1]]:
        response = await client.get(
            "/api/v1/chat/sessions/new",
            headers={"Authorization": f"Bearer {account['token']}"},
        )
        sessions.append(response.json()["data"]["session_id"])
    sources = []
    for platform, sid in [
        ("webchat", sessions[0]),
        ("webchat", sessions[1]),
        ("webchat", sessions[2]),
        ("qq", sessions[0]),
    ]:
        sources.append(
            await db.insert_platform_message_history(
                platform_id=platform,
                user_id=sid,
                content={
                    "type": "bot",
                    "message": [{"type": "plain", "text": "quoted context"}],
                },
                sender_id="bot",
                sender_name="bot",
            )
        )
    service = server.asgi_app.state.services.chat
    prior_routes = copy.deepcopy(core.umop_config_router.umop_to_conf_id)
    prior_runs = dict(service.chat_runs)
    worker = None
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    core.astrbot_config["admins_id"] = [user["resource_owner"]]

    async def model():
        incoming = await webchat_queue_mgr.get_or_create_queue(sessions[0]).get()
        event = adapter.create_event(await adapter.convert_message(incoming))
        context = PipelineContext(
            core.astrbot_config, core.plugin_manager, "default", db
        )
        waking = WakingCheckStage()
        await waking.initialize(context)

        async def generate(current):
            assert not current.is_admin()
            quote = next(
                part for part in current.get_messages() if isinstance(part, Reply)
            )
            assert str(quote.id) == str(sources[0].id)
            assert quote.message_str == "quoted context"
            assert (
                core.umop_config_router.get_conf_id_for_umop(current.unified_msg_origin)
                == "research"
            )
            back = webchat_queue_mgr.get_or_create_back_queue(
                incoming[2]["message_id"], sessions[0]
            )
            await back.put(
                {"type": "plain", "data": "accepted owned quote", "streaming": True}
            )

        pipeline = PipelineScheduler(context)
        pipeline.stages = [waking, SimpleNamespace(process=generate)]
        await pipeline.execute(event)

    try:
        async with ASGIWebSocket(server.asgi_app, path, user["token"]) as socket:
            for quote in [
                {"message_id": sources[1].id},
                {"message_id": sources[2].id},
                {"message_id": sources[3].id},
                {"message_id": 9999999},
                {"message_id": "missing"},
                {"message_id": True},
                {"message_id": sources[0].id, "selected_text": {"role": "admin"}},
                {"message_id": sources[0].id, "path": "private"},
            ]:
                await socket.send(
                    {
                        "ct": "chat",
                        "t": "send",
                        "session_id": sessions[0],
                        "config_id": "research",
                        "message": [
                            {"type": "reply", "selected_text": "untrusted", **quote},
                            {"type": "plain", "text": "must reject"},
                        ],
                    }
                )
                await socket.event("error")
                assert (
                    await db.count_platform_message_history("webchat", sessions[0]) == 1
                )
                assert core.umop_config_router.umop_to_conf_id == prior_routes
                assert service.chat_runs == prior_runs
                assert not webchat_queue_mgr.has_queue(sessions[0])
            worker = asyncio.create_task(model())
            await socket.send(
                {
                    "ct": "chat",
                    "t": "send",
                    "session_id": sessions[0],
                    "config_id": "research",
                    "message": [
                        {
                            "type": "reply",
                            "message_id": str(sources[0].id),
                            "selected_text": "quoted context",
                        },
                        {"type": "plain", "text": "Explain this quote"},
                    ],
                }
            )
            assert (await socket.event("plain"))["data"] == "accepted owned quote"
            await socket.event("end")
            await asyncio.wait_for(worker, 5)
            history = await client.get(
                f"/api/v1/chat/sessions/{sessions[0]}", headers=headers
            )
            assert (
                "Explain this quote" in history.text
                and "accepted owned quote" in history.text
            )
    finally:
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        for sid in sessions:
            webchat_queue_mgr.remove_queues(sid)
