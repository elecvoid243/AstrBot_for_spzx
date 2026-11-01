"""Detached live turns retain their existing queue, identity and recovery stream."""

import asyncio
import base64
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from astrbot.core.message.components import File, Json, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.sources.webchat import webchat_event
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
    orphan_run_registry,
    webchat_queue_mgr,
)
from astrbot.core.utils.active_event_registry import active_event_registry
from tests.test_multi_user_attachments import attachment_chat as attachment_chat
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import login
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat
from tests.test_multi_user_runtime import ASGIStream, ASGIWebSocket


@pytest_asyncio.fixture
async def detachable_live(attachment_chat, monkeypatch):
    """Use production account/storage boundaries with controlled speech recognition."""
    client, server, core, db, admin, users = attachment_chat
    chat = server.asgi_app.state.services.chat
    live = server.asgi_app.state.services.live_chat
    live.attachments_dir, live.webchat_img_dir = (
        chat.attachments_dir,
        chat.webchat_img_dir,
    )
    monkeypatch.setattr(webchat_event, "db_helper", db)
    monkeypatch.setattr(webchat_event, "attachments_dir", chat.attachments_dir)
    core.plugin_manager.context.provider_manager = SimpleNamespace(
        stt_provider_insts=[
            SimpleNamespace(
                get_text=AsyncMock(return_value="owned voice input"),
                meta=lambda: SimpleNamespace(type="controlled-stt"),
            )
        ]
    )
    yield attachment_chat, live
    # A failed assertion must not leave a detached persistence task alive.
    tasks = [
        session.live_request_task
        for session in list(live.sessions.values())
        if session.live_request_task is not None
        and not session.live_request_task.done()
    ]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["plain", "audio"])
@pytest.mark.parametrize("detach", ["disconnect", "reset", "write-error"])
async def test_detached_live_turn_recovers_and_persists_once(
    detachable_live, monkeypatch, tmp_path, mode, detach
):
    (client, server, core, db, admin, users), live = detachable_live
    user = users[0]
    headers = {"Authorization": f"Bearer {user['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    output = tmp_path / "generated.txt"
    output.write_bytes(b"owned generated file")
    started, write_gate, release, tail = (asyncio.Event() for _ in range(4))
    write_failed = asyncio.Event()
    fail_sends = False
    run_ids, events = [], []
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    core.astrbot_config["admins_id"] = [user["resource_owner"]]

    async def app(scope, receive, send):
        async def output_frame(frame):
            if fail_sends and frame["type"] == "websocket.send":
                write_failed.set()
                raise RuntimeError("Controlled closed output socket")
            await send(frame)

        await server.asgi_app(scope, receive, output_frame)

    async def model():
        incoming = await webchat_queue_mgr.get_or_create_queue(sid).get()
        run_ids.append(incoming[2]["message_id"])
        event = adapter.create_event(await adapter.convert_message(incoming))
        events.append(event)
        context = PipelineContext(
            core.astrbot_config, core.plugin_manager, "default", db
        )
        waking = WakingCheckStage()
        await waking.initialize(context)

        async def generate(current):
            assert not current.is_admin()
            assert (
                current.get_extra("_dashboard_account").account_id == user["account_id"]
            )

            async def first_segment():
                if mode == "audio":
                    yield MessageChain(
                        chain=[Plain("AA=="), Json({"text": "before-detach "})],
                        type="audio_chunk",
                    )
                else:
                    yield MessageChain(chain=[Plain("before-detach ")])
                started.set()
                await write_gate.wait()
                yield MessageChain(
                    chain=[File(file=str(output), name="early-owned.txt")]
                )
                await release.wait()
                if mode == "audio":
                    yield MessageChain(
                        chain=[Plain("AA=="), Json({"text": "after-detach "})],
                        type="audio_chunk",
                    )
                else:
                    yield MessageChain(chain=[Plain("after-detach ")])

            await current.send_streaming(first_segment())
            await tail.wait()

            async def last_segment():
                if mode == "audio":
                    yield MessageChain(
                        chain=[Plain("AA=="), Json({"text": "tail-after-complete"})],
                        type="audio_chunk",
                    )
                else:
                    yield MessageChain(chain=[Plain("tail-after-complete")])
                yield MessageChain(
                    chain=[File(file=str(output), name="late-owned.txt")]
                )

            await current.send_streaming(last_segment())

        pipeline = PipelineScheduler(context)
        pipeline.stages = [waking, SimpleNamespace(process=generate)]
        await pipeline.execute(event)

    worker = asyncio.create_task(model())
    session = None
    try:
        async with AsyncExitStack() as stack:
            socket = await stack.enter_async_context(
                ASGIWebSocket(app, "/api/v1/live-chat/ws", user["token"])
            )
            await socket.send({"t": "bind", "session_id": sid})
            await socket.event("session_bound")
            for message in [
                {"t": "start_speaking", "stamp": "one"},
                {
                    "t": "speaking_part",
                    "data": base64.b64encode(b"\x00\x00" * 160).decode(),
                },
                {"t": "end_speaking", "stamp": "one"},
            ]:
                await socket.send(message)
            await socket.event("user_msg")
            await asyncio.wait_for(started.wait(), 5)
            session = next(iter(live.sessions.values()))
            cleaned = []
            cleanup = session.cleanup

            def observe_cleanup():
                cleaned.append(True)
                cleanup()

            monkeypatch.setattr(session, "cleanup", observe_cleanup)
            for _ in range(200):
                active = (
                    await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
                ).json()["data"]["active_runs"]
                if "before-detach" in str(active):
                    break
                await asyncio.sleep(0.005)
            assert "before-detach" in str(active)
            if detach == "reset":
                reset = await client.post(
                    f"/api/v1/accounts/{user['account_id']}/reset-password",
                    headers={"Authorization": f"Bearer {admin['token']}"},
                    json={"password": "RenewedAccountPassword123!"},
                )
                assert reset.json()["status"] == "ok"
                await socket.send({"t": "interrupt"})
                assert (await socket.event("stop_play"))["code"] == 1008
                old = await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
                assert old.status_code == 401
                fresh = await login(
                    client, user["username"], "RenewedAccountPassword123!"
                )
                token = fresh["token"]
                headers = {"Authorization": f"Bearer {token}"}
            else:
                token = user["token"]
                if detach == "write-error":
                    fail_sends = True
                    write_gate.set()
                    await asyncio.wait_for(write_failed.wait(), 5)
                await socket.__aexit__()
            old_frames = len(socket.frames)
            assert not worker.done() and not session.live_request_task.done()
            assert not active_event_registry.get_background_stop_signal(
                events[0]
            ).is_set()
            assert session.principal.account_id == user["account_id"]
            assert (
                session.username == user["resource_owner"]
                and session.conversation_id == sid
            )
            active = (
                await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
            ).json()["data"]["active_runs"]
            assert run_ids[0] in str(active) and "before-detach" in str(active)
            for account in [users[1], admin]:
                other_headers = {"Authorization": f"Bearer {account['token']}"}
                for path in [
                    f"/api/v1/chat/sessions/{sid}",
                    f"/api/v1/chat/runs/{run_ids[0]}/stream",
                ]:
                    denied = await client.get(path, headers=other_headers)
                    assert (
                        denied.json()["status"] == "error"
                        and "before-detach" not in denied.text
                    )
            resumed = await stack.enter_async_context(
                ASGIStream(
                    server.asgi_app, f"/api/v1/chat/runs/{run_ids[0]}/stream", token
                )
            )
            write_gate.set()
            release.set()
            frame = await resumed.event("audio_chunk" if mode == "audio" else "plain")
            assert "after-detach" in (
                frame.get("text", "") if mode == "audio" else frame["data"]
            )
            await resumed.event("complete")
            assert not resumed.task.done() and not session.live_request_task.done()
            tail.set()
            frame = await resumed.event("audio_chunk" if mode == "audio" else "plain")
            assert "tail-after-complete" in (
                frame.get("text", "") if mode == "audio" else frame["data"]
            )
            await resumed.event("end")
            await asyncio.wait_for(worker, 5)
            await asyncio.wait_for(session.live_request_task, 5)
            await asyncio.sleep(0)
            assert cleaned == [True]
            assert (
                session.temp_audio_path is None
                and session.session_id not in live.sessions
            )
            assert run_ids[0] not in webchat_queue_mgr.back_queues
            assert orphan_run_registry.get(run_ids[0]) is None
            assert len(socket.frames) == old_frames
            history = (
                await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
            ).json()["data"]
            assert history["active_runs"] == []
            parts = [
                part for row in history["history"] for part in row["content"]["message"]
            ]
            text = "".join(
                part.get("text", "") for part in parts if part["type"] == "plain"
            )
            for marker in ["before-detach", "after-detach", "tail-after-complete"]:
                assert text.count(marker) == 1
            assert sum(part["type"] == "record" for part in parts) == 1
            files = [part for part in parts if part["type"] == "file"]
            assert sorted(part["filename"] for part in files) == [
                "early-owned.txt",
                "late-owned.txt",
            ]
            for part in files:
                row = await db.get_attachment_by_id(part["attachment_id"])
                assert row.owner == user["resource_owner"]
                own = await client.get(
                    f"/api/v1/files/{row.attachment_id}/content", headers=headers
                )
                assert own.content == output.read_bytes()
                denied = await client.get(
                    f"/api/v1/files/{row.attachment_id}/content",
                    headers={"Authorization": f"Bearer {users[1]['token']}"},
                )
                assert denied.json()["status"] == "error"
    finally:
        write_gate.set()
        release.set()
        tail.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        if session is not None and session.live_request_task is not None:
            if not session.live_request_task.done():
                session.live_request_task.cancel()
            await asyncio.gather(session.live_request_task, return_exceptions=True)
        webchat_queue_mgr.remove_queues(sid)


@pytest.mark.asyncio
@pytest.mark.parametrize("stt_error", [False, True])
async def test_detached_stt_keeps_source_until_terminal_cleanup(
    detachable_live, monkeypatch, stt_error
):
    (client, server, core, db, admin, users), live = detachable_live
    entered, release = asyncio.Event(), asyncio.Event()
    paths = []

    async def stt(path):
        paths.append(Path(path))
        entered.set()
        await release.wait()
        assert paths[0].exists()
        if stt_error:
            raise RuntimeError("Controlled terminal STT error")
        return ""

    core.plugin_manager.context.provider_manager.stt_provider_insts[0].get_text = stt
    user = users[0]
    sid = (
        await client.get(
            "/api/v1/chat/sessions/new",
            headers={"Authorization": f"Bearer {user['token']}"},
        )
    ).json()["data"]["session_id"]
    async with ASGIWebSocket(
        server.asgi_app, "/api/v1/live-chat/ws", user["token"]
    ) as socket:
        await socket.send({"t": "bind", "session_id": sid})
        await socket.event("session_bound")
        for message in [
            {"t": "start_speaking", "stamp": "one"},
            {
                "t": "speaking_part",
                "data": base64.b64encode(b"\x00\x00" * 160).decode(),
            },
            {"t": "end_speaking", "stamp": "one"},
        ]:
            await socket.send(message)
        await asyncio.wait_for(entered.wait(), 5)
        session = next(iter(live.sessions.values()))
        cleanup = session.cleanup
        cleaned = []

        def observe_cleanup():
            cleaned.append(True)
            cleanup()

        monkeypatch.setattr(session, "cleanup", observe_cleanup)
    assert paths[0].exists() and not session.live_request_task.done()
    release.set()
    await asyncio.wait_for(session.live_request_task, 5)
    await asyncio.sleep(0)
    assert cleaned == [True] and not paths[0].exists()
    assert session.session_id not in live.sessions
    assert webchat_queue_mgr.list_back_request_ids(sid) == []
    assert orphan_run_registry.active_for_conversation(sid) == []


@pytest.mark.asyncio
async def test_terminal_live_persistence_error_releases_run(
    detachable_live, monkeypatch
):
    (client, server, core, db, admin, users), live = detachable_live
    user = users[0]
    headers = {"Authorization": f"Bearer {user['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    monkeypatch.setattr(
        live,
        "save_bot_message",
        AsyncMock(side_effect=RuntimeError("Controlled terminal persistence error")),
    )
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    run_ids = []

    async def model():
        incoming = await webchat_queue_mgr.get_or_create_queue(sid).get()
        run_ids.append(incoming[2]["message_id"])
        event = adapter.create_event(await adapter.convert_message(incoming))

        async def generate(current):
            async def chunks():
                yield MessageChain(chain=[Plain("reply before terminal error")])

            await current.send_streaming(chunks())

        pipeline = PipelineScheduler(
            PipelineContext(core.astrbot_config, core.plugin_manager, "default", db)
        )
        pipeline.stages = [SimpleNamespace(process=generate)]
        await pipeline.execute(event)

    worker = asyncio.create_task(model())
    try:
        async with ASGIWebSocket(
            server.asgi_app, "/api/v1/live-chat/ws", user["token"]
        ) as socket:
            await socket.send({"t": "bind", "session_id": sid})
            await socket.event("session_bound")
            for message in [
                {"t": "start_speaking", "stamp": "one"},
                {
                    "t": "speaking_part",
                    "data": base64.b64encode(b"\x00\x00" * 160).decode(),
                },
                {"t": "end_speaking", "stamp": "one"},
            ]:
                await socket.send(message)
            await socket.event("user_msg")
            session = next(iter(live.sessions.values()))
            error = await socket.event("error")
            assert "Controlled terminal persistence error" in error["data"]
            await asyncio.wait_for(session.live_request_task, 5)
            await asyncio.wait_for(worker, 5)
            assert session.live_request_task.exception() is None
            assert session.temp_audio_path is None
            assert run_ids[0] not in webchat_queue_mgr.back_queues
            assert orphan_run_registry.get(run_ids[0]) is None
            state = (
                await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
            ).json()["data"]
            assert state["active_runs"] == []
        assert session.session_id not in live.sessions
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        webchat_queue_mgr.remove_queues(sid)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["plain", "audio"])
@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_detached_core_exit_preserves_partial_output_and_releases_run(
    detachable_live, tmp_path, mode, failure
):
    (client, server, core, db, admin, users), live = detachable_live
    user = users[0]
    headers = {"Authorization": f"Bearer {user['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    output = tmp_path / "partial.txt"
    output.write_bytes(b"owned partial output")
    started, release = asyncio.Event(), asyncio.Event()
    original_error = RuntimeError("Controlled core failure after output")
    run_ids = []
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    core.astrbot_config["admins_id"] = [user["resource_owner"]]

    async def model():
        incoming = await webchat_queue_mgr.get_or_create_queue(sid).get()
        run_ids.append(incoming[2]["message_id"])
        event = adapter.create_event(await adapter.convert_message(incoming))
        context = PipelineContext(
            core.astrbot_config, core.plugin_manager, "default", db
        )
        waking = WakingCheckStage()
        await waking.initialize(context)

        async def generate(current):
            assert not current.is_admin()

            async def chunks():
                if mode == "audio":
                    yield MessageChain(
                        chain=[
                            Plain("AA=="),
                            Json({"text": "partial transcript before core exit"}),
                        ],
                        type="audio_chunk",
                    )
                else:
                    yield MessageChain(
                        chain=[Plain("partial transcript before core exit")]
                    )
                yield MessageChain(
                    chain=[File(file=str(output), name="partial-owned.txt")]
                )
                started.set()
                await release.wait()
                raise original_error

            await current.send_streaming(chunks())

        pipeline = PipelineScheduler(context)
        pipeline.stages = [waking, SimpleNamespace(process=generate)]
        await pipeline.execute(event)

    worker = asyncio.create_task(model())
    session = None
    try:
        async with ASGIWebSocket(
            server.asgi_app, "/api/v1/live-chat/ws", user["token"]
        ) as socket:
            await socket.send({"t": "bind", "session_id": sid})
            await socket.event("session_bound")
            for message in [
                {"t": "start_speaking", "stamp": "one"},
                {
                    "t": "speaking_part",
                    "data": base64.b64encode(b"\x00\x00" * 160).decode(),
                },
                {"t": "end_speaking", "stamp": "one"},
            ]:
                await socket.send(message)
            await socket.event("user_msg")
            await asyncio.wait_for(started.wait(), 5)
            await socket.event("attachment")
            session = next(
                item for item in live.sessions.values() if item.conversation_id == sid
            )
        assert not worker.done() and not session.live_request_task.done()
        async with ASGIStream(
            server.asgi_app, f"/api/v1/chat/runs/{run_ids[0]}/stream", user["token"]
        ) as resumed:
            if failure == "cancel":
                worker.cancel()
            else:
                release.set()
            outcomes = await asyncio.gather(worker, return_exceptions=True)
            if failure == "cancel":
                assert isinstance(outcomes[0], asyncio.CancelledError)
            else:
                assert outcomes[0] is original_error
            await resumed.event("end")
            await asyncio.wait_for(session.live_request_task, 5)
        assert session.temp_audio_path is None
        assert session.session_id not in live.sessions
        assert run_ids[0] not in webchat_queue_mgr.back_queues
        assert orphan_run_registry.get(run_ids[0]) is None
        result = (
            await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
        ).json()["data"]
        assert result["active_runs"] == [] and result["is_running"] is False
        parts = [
            part for row in result["history"] for part in row["content"]["message"]
        ]
        assert (
            sum(
                part.get("text", "").count("partial transcript before core exit")
                for part in parts
            )
            == 1
        )
        assert sum(part["type"] == "record" for part in parts) == 1
        files = [part for part in parts if part["type"] == "file"]
        assert len(files) == 1 and files[0]["filename"] == "partial-owned.txt"
        row = await db.get_attachment_by_id(files[0]["attachment_id"])
        assert row.owner == user["resource_owner"]
        downloaded = await client.get(
            f"/api/v1/files/{row.attachment_id}/content", headers=headers
        )
        assert downloaded.content == output.read_bytes()
        for other in [users[1], admin]:
            denied = await client.get(
                f"/api/v1/files/{row.attachment_id}/content",
                headers={"Authorization": f"Bearer {other['token']}"},
            )
            assert denied.json()["status"] == "error"
    finally:
        release.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        if session is not None and session.live_request_task is not None:
            if not session.live_request_task.done():
                session.live_request_task.cancel()
            await asyncio.gather(session.live_request_task, return_exceptions=True)
        webchat_queue_mgr.remove_queues(sid)
