"""Generation controls exercised through production ASGI and real account storage."""

import asyncio
import json
import time
import uuid
from contextlib import AsyncExitStack
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit

import pytest

from astrbot.core import sp
from astrbot.core.db.po import DashboardAccount
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.goal.goal_service import build_goal_turn_event
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
    orphan_run_registry,
    webchat_queue_mgr,
)
from astrbot.core.subagent_manager import SubAgentManager, SubAgentRunHandle
from astrbot.core.tools.interactive_choice_registry import registry
from astrbot.core.utils.active_event_registry import active_event_registry
from astrbot.core.utils.webchat_access import dashboard_account_context
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat


class ASGIStream:
    """Observe a response without buffering its entire stream.

    Args:
        app: Production Dashboard ASGI application.
        path: Public HTTP path to request.
        token: Credential obtained through the real login endpoint.
        body: Optional JSON body; supplying it selects POST.
    """

    def __init__(self, app, path, token, body=None):
        self.app, self.path, self.token, self.body = app, path, token, body
        self.events = asyncio.Queue()
        self.started = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.frames = []
        self.status = None

    async def __aenter__(self):
        url = urlsplit(self.path)
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {
                    "type": "http.request",
                    "body": json.dumps(self.body).encode()
                    if self.body is not None
                    else b"",
                    "more_body": False,
                }
            await self.disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self.started.set()
            elif message["type"] == "http.response.body":
                raw = message.get("body", b"").decode()
                self.frames.append(raw)
                for line in raw.splitlines():
                    if line.startswith("data: "):
                        await self.events.put(json.loads(line[6:]))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "POST" if self.body is not None else "GET",
            "scheme": "http",
            "path": url.path,
            "raw_path": url.path.encode(),
            "query_string": url.query.encode(),
            "root_path": "",
            "headers": [
                (b"authorization", f"Bearer {self.token}".encode()),
                (b"content-type", b"application/json"),
            ],
            "client": ("127.0.0.1", 10114),
            "server": ("testserver", 80),
        }
        self.task = asyncio.create_task(self.app(scope, receive, send))
        await asyncio.wait_for(self.started.wait(), 15)
        assert self.status == 200, self.frames
        return self

    async def event(self, kind):
        """Read until the requested public SSE event arrives."""
        while True:
            event = await asyncio.wait_for(self.events.get(), 10)
            if event.get("type") == kind:
                return event

    async def __aexit__(self, *_):
        self.disconnected.set()
        await asyncio.wait_for(self.task, 10)


@pytest.mark.asyncio
async def test_concurrent_runs_controls_questions_and_goals(ordinary_chat):
    client, server, core, db, admin, users = ordinary_chat
    app = server.asgi_app
    headers = [{"Authorization": f"Bearer {user['token']}"} for user in users]
    admin_headers = {"Authorization": f"Bearer {admin['token']}"}
    sessions = [
        (await client.get("/api/v1/chat/sessions/new", headers=h)).json()["data"][
            "session_id"
        ]
        for h in headers
    ]
    origins = [
        f"webchat:FriendMessage:webchat!{user['resource_owner']}!{sid}"
        for user, sid in zip(users, sessions)
    ]
    core.astrbot_config["admins_id"] = [user["resource_owner"] for user in users]
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    workers = []
    questions = {}
    ready = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    answers = {}
    subagent_answers = [[], []]
    # These are the controlled external subagent runners, reached through the real registry.

    async def model(index):
        sender, target, payload = await webchat_queue_mgr.get_or_create_queue(
            sessions[index]
        ).get()
        event = adapter.create_event(
            await adapter.convert_message((sender, target, payload))
        )
        waking = WakingCheckStage()
        await waking.initialize(
            PipelineContext(core.astrbot_config, core.plugin_manager, "default", db)
        )

        async def generate(current):
            assert not current.is_admin()
            assert (
                dashboard_account_context.get().account_id == users[index]["account_id"]
            )
            clone = build_goal_turn_event(current, "continue", "controlled-goal")
            assert clone.get_extra("_dashboard_account") == current.get_extra(
                "_dashboard_account"
            )
            back = webchat_queue_mgr.get_or_create_back_queue(
                payload["message_id"], target
            )
            await back.put(
                {
                    "type": "plain",
                    "data": f"private-prefix-{index}",
                    "streaming": True,
                    "message_id": payload["message_id"],
                }
            )
            future = asyncio.get_running_loop().create_future()
            questions[index] = future
            registry.add(
                f"question-{index}",
                current.unified_msg_origin,
                future,
                {
                    "type": "interactive_choice",
                    "prompt": f"private-question-{index}",
                    "options": [{"id": "a", "label": "A"}],
                },
                time.time(),
                time.time() + 300,
                payload["message_id"],
            )
            await back.put(
                {
                    "type": "plain",
                    "chain_type": "interactive_choice",
                    "data": json.dumps(
                        {
                            "request_id": f"question-{index}",
                            "spec": registry._pending[f"question-{index}"].spec,
                        }
                    ),
                    "streaming": True,
                }
            )

            def follow_up(*, message_text):
                subagent_answers[index].append(message_text)
                return SimpleNamespace(seq=len(subagent_answers[index]))

            session = SubAgentManager.get_session(current.unified_msg_origin)
            if session is None:
                from astrbot.core.subagent_manager import SubAgentSession

                session = SubAgentSession(session_id=current.unified_msg_origin)
                SubAgentManager._sessions[current.unified_msg_origin] = session
            session.subagent_runners[f"child-{index}"] = SubAgentRunHandle(
                current.unified_msg_origin,
                f"child-{index}",
                "controlled",
                SimpleNamespace(follow_up=follow_up),
                None,
            )
            ready[index].set()
            answers[index] = await future
            signal = active_event_registry.get_background_stop_signal(current)
            stopped = asyncio.create_task(signal.wait())
            finished = asyncio.create_task(release[index].wait())
            try:
                await asyncio.wait(
                    {stopped, finished}, return_when=asyncio.FIRST_COMPLETED
                )
                await back.put(
                    {
                        "type": "plain",
                        "data": f"private-finish-{index}",
                        "streaming": True,
                        "message_id": payload["message_id"],
                    }
                )
            finally:
                stopped.cancel()
                finished.cancel()
                await asyncio.gather(stopped, finished, return_exceptions=True)

        pipeline = PipelineScheduler(
            PipelineContext(core.astrbot_config, core.plugin_manager, "default", db)
        )
        pipeline.stages = [waking, SimpleNamespace(process=generate)]
        await pipeline.execute(event)

    try:
        async with AsyncExitStack() as stack:
            system = [
                await stack.enter_async_context(
                    ASGIStream(
                        app, f"/api/v1/chat/sessions/{sid}/system-stream", user["token"]
                    )
                )
                for sid, user in zip(sessions, users)
            ]
            initial = [
                await stack.enter_async_context(
                    ASGIStream(
                        app,
                        "/api/v1/chat" if i == 0 else "/api/chat/send",
                        user["token"],
                        {"session_id": sessions[i], "message": f"question-{i}"},
                    )
                )
                for i, user in enumerate(users)
            ]
            workers = [asyncio.create_task(model(i)) for i in range(2)]
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in ready)), 10
            )
            runs = []
            for i in range(2):
                state = (
                    await client.get(
                        f"/api/v1/chat/sessions/{sessions[i]}", headers=headers[i]
                    )
                ).json()["data"]
                runs.append(state["active_runs"][0]["run_id"])
                assert f"private-prefix-{i}" in json.dumps(
                    await initial[i].event("plain")
                )
                question_event = await initial[i].event("plain")
                assert question_event["chain_type"] == "interactive_choice"
                assert f"private-question-{i}" in question_event["data"]
            resumed = [
                await stack.enter_async_context(
                    ASGIStream(app, f"/api/v1/chat/runs/{run}/stream", user["token"])
                )
                for run, user in zip(runs, users)
            ]
            for i in range(2):
                assert f"private-prefix-{i}" in json.dumps(
                    await resumed[i].event("run_snapshot")
                )
                for h in [headers[1 - i], admin_headers]:
                    for path in [
                        f"/api/v1/chat/runs/{runs[i]}/stream",
                        f"/api/v1/chat/sessions/{sessions[i]}/system-stream",
                    ]:
                        denied = await client.get(path, headers=h)
                        assert (
                            denied.status_code in {200, 403}
                            and denied.json()["status"] == "error"
                        )
                        assert "private-prefix" not in denied.text
                    for path, body in [
                        (f"/api/v1/chat/sessions/{sessions[i]}/stop", {}),
                        ("/api/chat/stop", {"session_id": sessions[i]}),
                        (
                            "/api/v1/chat/subagent-follow-up",
                            {
                                "session_id": sessions[i],
                                "subagent_run_id": f"child-{i}",
                                "text": "intrusion",
                            },
                        ),
                    ]:
                        denied = await client.post(path, headers=h, json=body)
                        assert denied.json()["status"] == "error"
                    denied = await client.post(
                        f"/api/chat/interactive-choice/question-{i}",
                        headers=h,
                        json={"choice_id": "a"},
                    )
                    assert denied.status_code == 403 and not questions[i].done()
                    denied = await client.delete(
                        f"/api/chat/interactive-choice/question-{i}", headers=h
                    )
                    assert denied.status_code == 403 and not questions[i].done()
                assert not subagent_answers[i] and not workers[i].done()
                forged = origins[i].replace(
                    users[i]["resource_owner"], users[1 - i]["resource_owner"]
                )
                for origin in [origins[1 - i], forged, "webchat:FriendMessage:missing"]:
                    denied = await client.post(
                        "/api/chat/interactive-choice/pending",
                        headers=headers[i],
                        json={"session_id": origin},
                    )
                    assert (
                        denied.status_code == 403
                        and "private-question" not in denied.text
                    )
                pending = await client.post(
                    "/api/chat/interactive-choice/pending",
                    headers=headers[i],
                    json={"session_id": origins[i]},
                )
                assert (
                    pending.json()["data"]["pending"][0]["prompt"]
                    == f"private-question-{i}"
                )
                child = await client.post(
                    "/api/v1/chat/subagent-follow-up",
                    headers=headers[i],
                    json={
                        "session_id": sessions[i],
                        "subagent_run_id": f"child-{i}",
                        "text": f"child-answer-{i}",
                    },
                )
                assert child.json()["data"]["accepted"] is True
                wrong_child = await client.post(
                    "/api/v1/chat/subagent-follow-up",
                    headers=headers[i],
                    json={
                        "session_id": sessions[i],
                        "subagent_run_id": f"child-{1 - i}",
                        "text": "wrong child",
                    },
                )
                assert wrong_child.json()["data"]["accepted"] is False
                assert subagent_answers[i] == [f"child-answer-{i}"]
                answered = await client.post(
                    f"/api/chat/interactive-choice/question-{i}",
                    headers=headers[i],
                    json={"choice_id": "a", "free_text": f"answer-{i}"},
                )
                assert answered.json()["status"] == "ok"
            stopped = await client.post(
                f"/api/v1/chat/sessions/{sessions[0]}/stop", headers=headers[0], json={}
            )
            assert stopped.json()["data"]["stopped_count"] == 1
            await asyncio.wait_for(workers[0], 5)
            assert not workers[1].done()
            release[1].set()
            await asyncio.wait_for(workers[1], 5)
            for i in range(2):
                await initial[i].event("end")
                assert answers[i]["free_text"] == f"answer-{i}"
                assert f"private-prefix-{1 - i}" not in "".join(
                    initial[i].frames + resumed[i].frames
                )
                follow = await stack.enter_async_context(
                    ASGIStream(
                        app,
                        "/api/v1/chat",
                        users[i]["token"],
                        {"session_id": sessions[i], "message": f"next-{i}"},
                    )
                )
                next_worker = asyncio.create_task(model(i))
                questions[i] = None
                # The next run reaches a new question; it remains isolated and can be cancelled.
                await follow.event("plain")
                cancelled = await client.delete(
                    f"/api/chat/interactive-choice/question-{i}", headers=headers[i]
                )
                assert cancelled.json()["status"] == "ok"
                await asyncio.gather(next_worker, return_exceptions=True)

            # Goals persist in real KV storage, expose only the owner's state, and queue trusted turns.
            for i in range(2):
                set_goal = await client.post(
                    f"/api/v1/chat/sessions/{sessions[i]}/goal",
                    headers=headers[i],
                    json={"goal": f"private-goal-{i}", "max_turns": 3},
                )
                assert set_goal.json()["status"] == "ok", set_goal.text
                sender, target, payload = await webchat_queue_mgr.get_or_create_queue(
                    sessions[i]
                ).get()
                assert sender == users[i]["resource_owner"] and target == sessions[i]
                assert (
                    payload["_dashboard_account"].account_id == users[i]["account_id"]
                )
                assert payload["_api_key_allow_admin_role"] is False
                pushed = await system[i].event("goal_state_changed")
                assert pushed["data"]["goal"]["goal"] == f"private-goal-{i}"
                for h in [headers[1 - i], admin_headers]:
                    denied = await client.get(
                        f"/api/v1/chat/sessions/{sessions[i]}/goal", headers=h
                    )
                    assert (
                        denied.json()["status"] == "error"
                        and "private-goal" not in denied.text
                    )
                    denied = await client.post(
                        f"/api/v1/chat/sessions/{sessions[i]}/goal/actions",
                        headers=h,
                        json={"action": "clear"},
                    )
                    assert denied.json()["status"] == "error"
                paused = await client.post(
                    f"/api/v1/chat/sessions/{sessions[i]}/goal/actions",
                    headers=headers[i],
                    json={"action": "pause"},
                )
                assert paused.json()["data"]["goal"]["status"] == "paused"
                resumed_goal = await client.post(
                    f"/api/v1/chat/sessions/{sessions[i]}/goal/actions",
                    headers=headers[i],
                    json={"action": "resume"},
                )
                assert resumed_goal.json()["data"]["goal"]["status"] == "active"
                orphan_run_registry.register(f"runtime-orphan-{i}", sessions[i])
                orphan_run_registry.update(
                    f"runtime-orphan-{i}",
                    [{"type": "plain", "text": f"private-runtime-orphan-{i}"}],
                )
                for h in [headers[1 - i], admin_headers]:
                    denied = await client.get(
                        f"/api/v1/chat/runs/runtime-orphan-{i}/stream", headers=h
                    )
                    assert (
                        denied.json()["status"] == "error"
                        and "private-orphan" not in denied.text
                    )
                own_orphan = await stack.enter_async_context(
                    ASGIStream(
                        app,
                        f"/api/v1/chat/runs/runtime-orphan-{i}/stream",
                        users[i]["token"],
                    )
                )
                orphan_run_registry.publish(
                    f"runtime-orphan-{i}",
                    {
                        "type": "plain",
                        "data": f"orphan-live-{i}",
                        "message_id": f"runtime-orphan-{i}",
                    },
                )
                assert (await own_orphan.event("plain"))["data"] == f"orphan-live-{i}"
                orphan_run_registry.finish(f"runtime-orphan-{i}")
        await db.engine.dispose()
        reopened = SQLiteDatabase(db.db_path)
        await reopened.initialize()
        sp.db_helper = reopened
        sp._cache = {}
        try:
            for i, sid in enumerate(sessions):
                recovered = await client.get(
                    f"/api/v1/chat/sessions/{sid}/goal", headers=headers[i]
                )
                assert recovered.json()["data"]["goal"]["goal"] == f"private-goal-{i}"
        finally:
            sp.db_helper = db
            await reopened.engine.dispose()
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for i, sid in enumerate(sessions):
            registry.remove(f"question-{i}")
            SubAgentManager._sessions.pop(origins[i], None)
            orphan_run_registry.finish(f"runtime-orphan-{i}")
            webchat_queue_mgr.remove_queues(sid)


class ASGIWebSocket:
    """Connect to a production WebSocket without replacing authentication.

    Args:
        app: Production Dashboard ASGI application.
        path: Public WebSocket endpoint.
        token: Credential obtained through the real login endpoint.
    """

    def __init__(self, app, path, token):
        self.app, self.path, self.token = app, path, token
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        self.frames = []

    async def __aenter__(self):
        async def send(message):
            self.frames.append(message)
            await self.outgoing.put(message)

        await self.incoming.put({"type": "websocket.connect"})
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "root_path": "",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": urlencode({"token": self.token}).encode(),
            "headers": [],
            "client": ("127.0.0.1", 10114),
            "server": ("testserver", 80),
            "subprotocols": [],
        }
        self.task = asyncio.create_task(self.app(scope, self.incoming.get, send))
        assert (await asyncio.wait_for(self.outgoing.get(), 10))[
            "type"
        ] == "websocket.accept"
        return self

    async def send(self, payload):
        await self.incoming.put(
            {"type": "websocket.receive", "text": json.dumps(payload)}
        )

    async def event(self, kind):
        while True:
            message = await asyncio.wait_for(self.outgoing.get(), 10)
            if message["type"] == "websocket.close":
                return message
            payload = json.loads(message["text"])
            if payload.get("type", payload.get("t")) == kind:
                return payload

    async def __aexit__(self, *_):
        await self.incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self.task, 10)


@pytest.mark.asyncio
async def test_text_websocket_identity_controls_and_revoked_reconnect(ordinary_chat):
    client, server, core, db, admin, users = ordinary_chat
    headers = [{"Authorization": f"Bearer {user['token']}"} for user in users]
    sessions = [
        (await client.get("/api/v1/chat/sessions/new", headers=h)).json()["data"][
            "session_id"
        ]
        for h in headers
    ]
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    release = asyncio.Event()

    async def model(index):
        sender, sid, payload = await webchat_queue_mgr.get_or_create_queue(
            sessions[index]
        ).get()
        event = adapter.create_event(
            await adapter.convert_message((sender, sid, payload))
        )

        async def generate(current):
            assert (
                current.get_extra("_dashboard_account").account_id
                == users[index]["account_id"]
            )
            assert not current.is_admin()
            back = webchat_queue_mgr.get_or_create_back_queue(
                payload["message_id"], sid
            )
            await back.put(
                {"type": "plain", "data": f"ws-private-{index}", "streaming": True}
            )
            signal = active_event_registry.get_background_stop_signal(current)
            stop, finish = (
                asyncio.create_task(signal.wait()),
                asyncio.create_task(release.wait()),
            )
            try:
                await asyncio.wait({stop, finish}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                stop.cancel()
                finish.cancel()
                await asyncio.gather(stop, finish, return_exceptions=True)

        pipeline = PipelineScheduler(
            PipelineContext(core.astrbot_config, core.plugin_manager, "default", db)
        )
        pipeline.stages = [SimpleNamespace(process=generate)]
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
                await socket.send(
                    {"ct": "chat", "t": "bind", "session_id": sessions[1 - i]}
                )
                assert (await socket.event("error"))["code"] == "PERMISSION_DENIED"
                await socket.send(
                    {"ct": "chat", "t": "bind", "session_id": sessions[i]}
                )
                assert (await socket.event("session_bound"))["session_id"] == sessions[
                    i
                ]
                await socket.send(
                    {
                        "ct": "chat",
                        "t": "send",
                        "session_id": sessions[i],
                        "message_id": f"ws-client-{i}",
                        "message": [{"type": "plain", "text": "hello"}],
                        "username": "original-admin",
                    }
                )
                assert (await socket.event("error"))["code"] == "PERMISSION_DENIED"
            workers = [asyncio.create_task(model(i)) for i in range(2)]
            for i, socket in enumerate(sockets):
                await socket.send(
                    {
                        "ct": "chat",
                        "t": "send",
                        "session_id": sessions[i],
                        "message_id": f"ws-client-{i}",
                        "message": [{"type": "plain", "text": "hello"}],
                    }
                )
                assert (await socket.event("plain"))["data"] == f"ws-private-{i}"
            denied = await client.post(
                f"/api/v1/chat/sessions/{sessions[1]}/stop", headers=headers[0], json={}
            )
            assert denied.json()["status"] == "error" and not workers[1].done()
            stopped = await client.post(
                f"/api/v1/chat/sessions/{sessions[0]}/stop", headers=headers[0], json={}
            )
            assert stopped.json()["data"]["stopped_count"] == 1
            await asyncio.wait_for(workers[0], 5)
            assert not workers[1].done()
            await sockets[0].event("end")
            async with db.get_db() as connection:
                account = await connection.get(DashboardAccount, users[0]["account_id"])
                account.token_version += 1
                connection.add(account)
                await connection.commit()
            await sockets[0].send(
                {"ct": "chat", "t": "bind", "session_id": sessions[0]}
            )
            assert (await sockets[0].event("session_bound"))["code"] == 1008
            for path in [
                f"/api/v1/chat/sessions/{sessions[0]}/system-stream",
                "/api/v1/chat/runs/unknown/stream",
            ]:
                denied = await client.get(path, headers=headers[0])
                assert denied.status_code == 401
            release.set()
            await asyncio.wait_for(workers[1], 5)
            await sockets[1].event("end")
            for i, socket in enumerate(sockets):
                assert f"ws-private-{1 - i}" not in json.dumps(socket.frames)
    finally:
        release.set()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for sid in sessions:
            webchat_queue_mgr.remove_queues(sid)


@pytest.mark.asyncio
async def test_goal_clones_keep_effective_roles_through_real_pipeline(
    ordinary_chat, monkeypatch
):
    """Exercise initial HTTP messages and three automatic continuation rounds."""
    client, server, core, db, admin, users = ordinary_chat
    admin_headers = {"Authorization": f"Bearer {admin['token']}"}
    monkeypatch.setattr(
        core.astrbot_config_mgr,
        "get_conf_list",
        lambda: [{"id": "default", "name": "Default"}],
        raising=False,
    )
    keys = []
    for name, scopes in [
        ("goal-restricted", ["chat"]),
        ("goal-authorized", ["chat", "chat:admin"]),
    ]:
        created = await client.post(
            "/api/v1/api-keys",
            headers=admin_headers,
            json={"name": name, "scopes": scopes},
        )
        assert created.status_code == 200 and created.json()["status"] == "ok"
        keys.append(created.json()["data"]["api_key"])
    cases = [
        ("ordinary", users[0], None, False, True),
        ("administrator", admin, None, True, True),
        ("restricted-key", None, keys[0], False, True),
        ("authorized-key", None, keys[1], True, True),
        ("nonadmin-key-sender", None, keys[1], False, False),
    ]
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    for name, account, key, expected_admin, collision in cases:
        headers = (
            {"X-API-Key": key}
            if key
            else {"Authorization": f"Bearer {account['token']}"}
        )
        sid = (
            str(uuid.uuid4())
            if key
            else (
                await client.get("/api/v1/chat/sessions/new", headers=headers)
            ).json()["data"]["session_id"]
        )
        sender = account["resource_owner"] if account else f"goal-{name}"
        # A restricted key's request is accepted before a later admin-ID change.
        core.astrbot_config["admins_id"] = [sender] if expected_admin else []
        observed = []

        async def model():
            incoming = await asyncio.wait_for(
                webchat_queue_mgr.get_or_create_queue(sid).get(), 10
            )
            current = adapter.create_event(await adapter.convert_message(incoming))
            core.astrbot_config["admins_id"] = [sender] if collision else []
            context = PipelineContext(
                core.astrbot_config, core.plugin_manager, "default", db
            )
            waking = WakingCheckStage()
            await waking.initialize(context)

            async def observe(event):
                observed.append(event.is_admin())
                principal = dashboard_account_context.get()
                assert (principal.account_id if principal else None) == (
                    account["account_id"] if account else None
                )

            pipeline = PipelineScheduler(context)
            pipeline.stages = [waking, SimpleNamespace(process=observe)]
            await pipeline.execute(current)
            for _ in range(3):
                current = build_goal_turn_event(
                    current, "automatic goal continuation", "role-regression"
                )
                await pipeline.execute(current)

        worker = asyncio.create_task(model())
        try:
            if key:
                path = "/api/v1/chat"
                payload = {
                    "username": sender,
                    "session_id": sid,
                    "message": "start a controlled goal",
                }
            else:
                path = f"/api/v1/chat/sessions/{sid}/goal"
                payload = {"goal": "start a controlled goal", "max_turns": 3}
            response, _ = await asyncio.wait_for(
                asyncio.gather(
                    client.post(path, headers=headers, json=payload), worker
                ),
                15,
            )
            assert response.status_code == 200
            if key:
                assert "text/event-stream" in response.headers["content-type"]
            else:
                assert response.json()["status"] == "ok"
            assert observed == [expected_admin] * 4, (name, observed)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            webchat_queue_mgr.remove_queues(sid)
