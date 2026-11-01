"""Real-account WebSocket consumption of configuration, media and live controls."""

import asyncio
import base64
import errno
import io
import shutil
import wave
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.message.components import File
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.sources.webchat import webchat_event
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.utils.active_event_registry import active_event_registry
from astrbot.core.utils.webchat_access import (
    DashboardPrincipal,
    dashboard_account_context,
    webchat_resource_owner,
)
from astrbot.dashboard.services.chat_service import LocalUploadFile
from tests.test_multi_user_attachments import attachment_chat as attachment_chat
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import login
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat
from tests.test_multi_user_chat_config import configured_chat as configured_chat
from tests.test_multi_user_runtime import ASGIStream, ASGIWebSocket


@pytest.mark.asyncio
async def test_websocket_media_and_public_selections(
    attachment_chat, configured_chat, tmp_path
):
    client, server, core, db, admin, users = attachment_chat
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    sessions, uploads = [], []
    headers = [{"Authorization": f"Bearer {user['token']}"} for user in users]
    for i in range(2):
        sessions.append(
            (await client.get("/api/v1/chat/sessions/new", headers=headers[i])).json()[
                "data"
            ]["session_id"]
        )
        uploads.append(
            (
                await client.post(
                    "/api/v1/files",
                    headers=headers[i],
                    files={
                        "file": (
                            "input.txt",
                            f"private-input-{i}".encode(),
                            "text/plain",
                        )
                    },
                )
            ).json()["data"]
        )

    async def model(i):
        sender, sid, payload = await webchat_queue_mgr.get_or_create_queue(
            sessions[i]
        ).get()
        event = adapter.create_event(
            await adapter.convert_message((sender, sid, payload))
        )

        async def generate(current):
            assert not current.is_admin()
            assert (
                core.umop_config_router.get_conf_id_for_umop(current.unified_msg_origin)
                == ("research", "writing")[i]
            )
            assert payload["thinking_effort"] == ("low", "xhigh")[i]
            assert (
                payload["selected_provider"] == "gpt-mini"
                and payload["selected_model"] == "gpt-4o-mini"
            )
            part = next(part for part in payload["message"] if part["type"] == "file")
            assert Path(part["path"]).read_bytes() == f"private-input-{i}".encode()
            back = webchat_queue_mgr.get_or_create_back_queue(
                payload["message_id"], sid
            )
            await back.put(
                {"type": "plain", "data": f"selected-profile-{i}", "streaming": True}
            )

        waking = WakingCheckStage()
        context = PipelineContext(
            core.astrbot_config, core.plugin_manager, "default", db
        )
        await waking.initialize(context)
        pipeline = PipelineScheduler(context)
        pipeline.stages = [waking, SimpleNamespace(process=generate)]
        await pipeline.execute(event)

    workers = []
    try:
        async with AsyncExitStack() as stack:
            sockets = [
                await stack.enter_async_context(
                    ASGIWebSocket(server.asgi_app, path, user["token"])
                )
                for path, user in zip(
                    ["/api/v1/unified-chat/ws", "/api/unified_chat/ws"], users
                )
            ]
            for i, socket in enumerate(sockets):
                base = {
                    "ct": "chat",
                    "t": "send",
                    "session_id": sessions[i],
                    "message": [
                        {
                            "type": "file",
                            "attachment_id": uploads[1 - i]["attachment_id"],
                        }
                    ],
                }
                await socket.send(base)
                assert (await socket.event("error"))["code"] == "PROCESSING_ERROR"
                assert (
                    await db.count_platform_message_history("webchat", sessions[i]) == 0
                )
                assert webchat_queue_mgr.get_or_create_queue(sessions[i]).empty()
                for fields in [
                    {"config_id": "missing"},
                    {"selected_provider": "missing", "selected_model": "secret"},
                    {"active_preset": "server-admin"},
                ]:
                    await socket.send(
                        {
                            **base,
                            "message": [{"type": "plain", "text": "reject"}],
                            **fields,
                        }
                    )
                    await socket.event("error")
                    assert (
                        await db.count_platform_message_history("webchat", sessions[i])
                        == 0
                    )
            workers = [asyncio.create_task(model(i)) for i in range(2)]
            for i, socket in enumerate(sockets):
                await socket.send(
                    {
                        "ct": "chat",
                        "t": "send",
                        "session_id": sessions[i],
                        "config_id": ("research", "writing")[i],
                        "thinking_effort": ("low", "xhigh")[i],
                        "selected_provider": "gpt-mini",
                        "selected_model": "gpt-4o-mini",
                        "message": [
                            {"type": "plain", "text": "hello"},
                            {
                                "type": "file",
                                "attachment_id": uploads[i]["attachment_id"],
                            },
                        ],
                    }
                )
            await asyncio.wait_for(asyncio.gather(*workers), 10)
            for i, socket in enumerate(sockets):
                assert (await socket.event("plain"))["data"] == f"selected-profile-{i}"
                await socket.event("end")
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for sid in sessions:
            webchat_queue_mgr.remove_queues(sid)


@pytest.mark.asyncio
async def test_live_controls_media_ownership_and_credential_cleanup(
    attachment_chat, configured_chat, tmp_path, monkeypatch
):
    client, server, core, db, admin, users = attachment_chat
    service = server.asgi_app.state.services.chat
    live = server.asgi_app.state.services.live_chat
    live.attachments_dir, live.webchat_img_dir = (
        service.attachments_dir,
        service.webchat_img_dir,
    )
    monkeypatch.setattr(webchat_event, "db_helper", db)
    monkeypatch.setattr(webchat_event, "attachments_dir", service.attachments_dir)
    core.plugin_manager.context.provider_manager = SimpleNamespace(
        stt_provider_insts=[
            SimpleNamespace(
                get_text=AsyncMock(return_value="controlled voice"),
                meta=lambda: SimpleNamespace(type="controlled-stt"),
            )
        ]
    )
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    core.astrbot_config["admins_id"] = [user["resource_owner"] for user in users]
    headers = [{"Authorization": f"Bearer {user['token']}"} for user in users]
    admin_headers = {"Authorization": f"Bearer {admin['token']}"}
    sessions = [
        (await client.get("/api/v1/chat/sessions/new", headers=h)).json()["data"][
            "session_id"
        ]
        for h in headers
    ]
    release = [asyncio.Event(), asyncio.Event()]
    started = [asyncio.Event(), asyncio.Event()]
    workers = []
    wav = io.BytesIO()
    with wave.open(wav, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 160)
    audio_b64 = base64.b64encode(wav.getvalue()).decode()
    output = tmp_path / "live-result.txt"
    output.write_text("private generated media", encoding="utf-8")

    async def model(i):
        incoming = await webchat_queue_mgr.get_or_create_queue(sessions[i]).get()
        sender, sid, payload = incoming
        event = adapter.create_event(await adapter.convert_message(incoming))

        async def generate(current):
            assert not current.is_admin()
            assert (
                current.get_extra("_dashboard_account").account_id
                == users[i]["account_id"]
            )
            assert (
                core.umop_config_router.get_conf_id_for_umop(current.unified_msg_origin)
                == ("research", "writing")[i]
            )
            assert payload["thinking_effort"] == ("low", "xhigh")[i]
            await current.send(
                MessageChain(chain=[File(file=str(output), name=f"live-{i}.txt")])
            )
            await webchat_queue_mgr.put_back_queue(
                payload["message_id"],
                {
                    "type": "audio_chunk",
                    "message_id": payload["message_id"],
                    "data": audio_b64,
                    "text": f"live-private-{i}",
                },
            )
            started[i].set()
            stop = asyncio.create_task(
                active_event_registry.get_background_stop_signal(current).wait()
            )
            done = asyncio.create_task(release[i].wait())
            try:
                await asyncio.wait({stop, done}, return_when=asyncio.FIRST_COMPLETED)
                # Late output after consumer disconnection must remain owned.
                await current.send(
                    MessageChain(chain=[File(file=str(output), name=f"late-{i}.txt")])
                )
            finally:
                stop.cancel()
                done.cancel()
                await asyncio.gather(stop, done, return_exceptions=True)

        context = PipelineContext(
            core.astrbot_config, core.plugin_manager, "default", db
        )
        waking = WakingCheckStage()
        await waking.initialize(context)
        pipeline = PipelineScheduler(context)
        pipeline.stages = [waking, SimpleNamespace(process=generate)]
        await pipeline.execute(event)

    try:
        async with AsyncExitStack() as stack:
            # Both the official live transport and the compatibility unified transport.
            sockets = [
                await stack.enter_async_context(
                    ASGIWebSocket(server.asgi_app, path, user["token"])
                )
                for path, user in zip(
                    ["/api/v1/live-chat/ws", "/api/unified_chat/ws"], users
                )
            ]
            systems = [
                await stack.enter_async_context(
                    ASGIStream(
                        server.asgi_app,
                        f"/api/v1/chat/sessions/{sid}/system-stream",
                        user["token"],
                    )
                )
                for sid, user in zip(sessions, users)
            ]
            for i, socket in enumerate(sockets):
                await socket.send(
                    {"ct": "live", "t": "bind", "session_id": sessions[1 - i]}
                )
                assert (await socket.event("error"))["code"] == "PERMISSION_DENIED"
                origin = f"webchat:FriendMessage:webchat!{users[i]['resource_owner']}!{sessions[i]}"
                prior = core.umop_config_router.get_conf_id_for_umop(origin)
                for selection in [
                    {
                        "config_id": "research",
                        "selected_provider": "missing",
                        "selected_model": "private",
                    },
                    {"config_id": "research", "thinking_effort": " "},
                    {"active_preset": "admin-only"},
                    {"_dashboard_account": users[1 - i]["account_id"]},
                ]:
                    await socket.send(
                        {
                            "ct": "live",
                            "t": "bind",
                            "session_id": sessions[i],
                            **selection,
                        }
                    )
                    await socket.event("error")
                    assert core.umop_config_router.get_conf_id_for_umop(origin) == prior
                    assert (
                        await db.count_platform_message_history("webchat", sessions[i])
                        == 0
                    )
                await socket.send(
                    {
                        "ct": "live",
                        "t": "bind",
                        "session_id": sessions[i],
                        "config_id": ("research", "writing")[i],
                        "thinking_effort": ("low", "xhigh")[i],
                        "selected_provider": "gpt-mini",
                        "selected_model": "gpt-4o-mini",
                    }
                )
                assert (await socket.event("session_bound"))["session_id"] == sessions[
                    i
                ]
            workers = [asyncio.create_task(model(i)) for i in range(2)]
            for i, socket in enumerate(sockets):
                for message in [
                    {"t": "start_speaking", "stamp": str(i)},
                    {
                        "t": "speaking_part",
                        "data": base64.b64encode(b"\x00\x00" * 160).decode(),
                    },
                    {"t": "end_speaking", "stamp": str(i)},
                ]:
                    await socket.send({"ct": "live", **message})
                assert (await socket.event("user_msg"))["data"][
                    "text"
                ] == "controlled voice"
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in started)), 10
            )
            for i, system in enumerate(systems):
                assert f"live-{1 - i}.txt" not in "".join(system.frames)
            parts = []
            for i, socket in enumerate(sockets):
                parts.append((await socket.event("attachment"))["data"])
                assert (await socket.event("response"))["data"] == audio_b64
                for account in [users[1 - i], admin]:
                    denied = await client.get(
                        f"/api/v1/files/{parts[i]['attachment_id']}/content",
                        headers={"Authorization": f"Bearer {account['token']}"},
                    )
                    assert denied.json()["status"] == "error"
                own = await client.get(
                    f"/api/v1/files/{parts[i]['attachment_id']}/content",
                    headers=headers[i],
                )
                assert own.content == output.read_bytes()
            # Revoked credentials must not be able to interrupt an existing core run.
            reset = await client.post(
                f"/api/v1/accounts/{users[0]['account_id']}/reset-password",
                headers=admin_headers,
                json={"password": "ResetAccountPassword123!"},
            )
            assert reset.json()["status"] == "ok"
            await sockets[0].send({"ct": "live", "t": "interrupt"})
            assert (await sockets[0].event("stop_play"))["code"] == 1008
            assert not workers[0].done() and not workers[1].done()
            for path in [
                "/api/v1/live-chat/ws",
                "/api/live_chat/ws",
                "/api/v1/unified-chat/ws",
                "/api/unified_chat/ws",
            ]:
                old = await stack.enter_async_context(
                    ASGIWebSocket(server.asgi_app, path, users[0]["token"])
                )
                assert (await old.event("session_bound"))["code"] == 1008
            fresh = await login(
                client, users[0]["username"], "ResetAccountPassword123!"
            )
            renewed = await stack.enter_async_context(
                ASGIWebSocket(server.asgi_app, "/api/live_chat/ws", fresh["token"])
            )
            await renewed.send({"t": "bind", "session_id": sessions[0]})
            assert (await renewed.event("session_bound"))["session_id"] == sessions[0]
            release[0].set()
            await asyncio.wait_for(workers[0], 5)
            assert not workers[1].done()
            await sockets[1].send({"ct": "live", "t": "interrupt"})
            assert (await sockets[1].event("stop_play"))["t"] == "stop_play"
            await asyncio.wait_for(workers[1], 5)
            disabled = await client.post(
                f"/api/v1/accounts/{users[1]['account_id']}/disable",
                headers=admin_headers,
            )
            assert disabled.json()["status"] == "ok"
            old = await stack.enter_async_context(
                ASGIWebSocket(
                    server.asgi_app, "/api/v1/live-chat/ws", users[1]["token"]
                )
            )
            assert (await old.event("session_bound"))["code"] == 1008
            history = await client.get(
                f"/api/v1/chat/sessions/{sessions[0]}",
                headers={"Authorization": f"Bearer {fresh['token']}"},
            )
            assert "late-0.txt" in history.text and "live-1.txt" not in history.text
            assert history.text.count('"type":"record"') == 1
            assert history.text.count('"filename":"live-0.txt"') == 1
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for sid in sessions:
            webchat_queue_mgr.remove_queues(sid)


@pytest.mark.asyncio
@pytest.mark.parametrize("copy_fails", [False, True])
async def test_cross_device_live_recording_move_preserves_source_on_failure(
    attachment_chat, tmp_path, monkeypatch, copy_fails
):
    """Exercise the cross-device fallback without depending on drive letters."""
    client, server, core, db, admin, users = attachment_chat
    service = server.asgi_app.state.services.chat
    source = tmp_path / "recording.wav"
    source.write_bytes(b"original private recording")
    rename = shutil.os.rename
    move = shutil.move
    destinations = []

    def cross_device(src, dst, *args, **kwargs):
        if Path(src) == source:
            destinations.append(Path(dst))
            raise OSError(errno.EXDEV, "Cross-device link")
        return rename(src, dst, *args, **kwargs)

    def failed_copy(src, dst, *args, **kwargs):
        Path(dst).write_bytes(b"incomplete")
        raise OSError("Controlled copy failure")

    monkeypatch.setattr(shutil.os, "rename", cross_device)
    if copy_fails:
        monkeypatch.setattr(
            shutil, "move", lambda src, dst: move(src, dst, copy_function=failed_copy)
        )
    user = users[0]
    principal = DashboardPrincipal(
        user["account_id"], user["resource_owner"], "user", user["username"]
    )
    account_token = dashboard_account_context.set(principal)
    owner_token = webchat_resource_owner.set(principal.resource_owner)
    try:
        upload = LocalUploadFile(source, "voice.wav", "audio/wav")
        if copy_fails:
            with pytest.raises(OSError, match="Controlled copy failure"):
                await service.save_uploaded_file(upload)
            assert source.read_bytes() == b"original private recording"
            assert await db.get_attachment_by_path(str(destinations[0])) is None
        else:
            result = await service.save_uploaded_file(upload)
            assert not source.exists()
            row = await db.get_attachment_by_id(result["attachment_id"])
            assert row.owner == principal.resource_owner
            assert Path(row.path).read_bytes() == b"original private recording"
    finally:
        webchat_resource_owner.reset(owner_token)
        dashboard_account_context.reset(account_token)


@pytest.mark.asyncio
async def test_administrator_retains_live_voice_with_owned_media(
    attachment_chat, configured_chat, monkeypatch, tmp_path
):
    client, server, core, db, admin, users = attachment_chat
    service = server.asgi_app.state.services.chat
    live = server.asgi_app.state.services.live_chat
    live.attachments_dir = service.attachments_dir
    monkeypatch.setattr(webchat_event, "db_helper", db)
    monkeypatch.setattr(webchat_event, "attachments_dir", service.attachments_dir)
    core.plugin_manager.context.provider_manager = SimpleNamespace(
        stt_provider_insts=[
            SimpleNamespace(
                get_text=AsyncMock(return_value="administrator voice"),
                meta=lambda: SimpleNamespace(type="controlled-stt"),
            )
        ]
    )
    headers = {"Authorization": f"Bearer {admin['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    core.astrbot_config["admins_id"] = [admin["resource_owner"]]
    output = tmp_path / "admin-live.txt"
    output.write_bytes(b"private administrator media")
    adapter = WebChatAdapter({}, {}, asyncio.Queue())

    async def model():
        incoming = await webchat_queue_mgr.get_or_create_queue(sid).get()
        event = adapter.create_event(await adapter.convert_message(incoming))
        context = PipelineContext(
            core.astrbot_config, core.plugin_manager, "default", db
        )
        waking = WakingCheckStage()
        await waking.initialize(context)

        async def generate(current):
            assert current.is_admin()
            assert (
                current.get_extra("_dashboard_account").account_id
                == admin["account_id"]
            )
            await current.send(
                MessageChain(chain=[File(file=str(output), name="admin-live.txt")])
            )
            await webchat_queue_mgr.put_back_queue(
                incoming[2]["message_id"],
                {
                    "type": "plain",
                    "message_id": incoming[2]["message_id"],
                    "data": "administrator reply",
                    "streaming": True,
                },
            )

        pipeline = PipelineScheduler(context)
        pipeline.stages = [waking, SimpleNamespace(process=generate)]
        await pipeline.execute(event)

    worker = asyncio.create_task(model())
    try:
        async with ASGIWebSocket(
            server.asgi_app, "/api/v1/live-chat/ws", admin["token"]
        ) as socket:
            await socket.send({"t": "bind", "session_id": sid, "config_id": "research"})
            await socket.event("session_bound")
            for payload in [
                {"t": "start_speaking", "stamp": "admin"},
                {
                    "t": "speaking_part",
                    "data": base64.b64encode(b"\x00\x00" * 160).decode(),
                },
                {"t": "end_speaking", "stamp": "admin"},
            ]:
                await socket.send(payload)
            assert (await socket.event("user_msg"))["data"][
                "text"
            ] == "administrator voice"
            await asyncio.wait_for(worker, 5)
            part = (await socket.event("attachment"))["data"]
            assert (await socket.event("bot_msg"))["data"][
                "text"
            ] == "administrator reply"
            await socket.event("end")
            await asyncio.wait_for(worker, 5)
            row = await db.get_attachment_by_id(part["attachment_id"])
            assert row.owner == admin["resource_owner"]
            own = await client.get(
                f"/api/v1/files/{row.attachment_id}/content", headers=headers
            )
            assert own.content == output.read_bytes()
            denied = await client.get(
                f"/api/v1/files/{row.attachment_id}/content",
                headers={"Authorization": f"Bearer {users[0]['token']}"},
            )
            assert denied.json()["status"] == "error"
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        webchat_queue_mgr.remove_queues(sid)
