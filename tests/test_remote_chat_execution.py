"""Remote text admission uses ordinary ChatService runs and isolated databases."""

import asyncio
from uuid import uuid4

import pytest

from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.utils.webchat_access import DashboardPrincipal
from tests.test_remote_chat_connections import (  # noqa: F401
    account_dashboard,
    fake_core_lifecycle,
    ordinary_chat,
)
from tests.test_remote_chat_connections import (
    connected_instances as connected_instances,
)


@pytest.mark.asyncio
async def test_text_receipts_context_followup_history_and_revision(connected_instances):
    local, remote_instance, _ = connected_instances
    client, server, core, db, admin, users = local
    remote, remote_server, remote_core, remote_db, remote_admin, credential = (
        remote_instance
    )
    headers = {"Authorization": "Bearer " + users[0]["token"]}
    session = await remote_db.create_platform_session(
        creator=remote_admin["resource_owner"]
    )
    connection_id = (
        await client.post(
            "/api/v1/chat/remote-connections",
            headers=headers,
            json={
                "name": "Remote",
                "base_url": "http://remote",
                "key": credential["key"],
            },
        )
    ).json()["data"]["connection_id"]
    connection_path = f"/api/v1/chat/remote-connections/{connection_id}"
    revision = (
        await client.get("/api/v1/chat/remote-connections", headers=headers)
    ).json()["data"]["connections"][0]["revision"]
    path = connection_path + f"/sessions/{session.session_id}"
    identity = str(uuid4())
    body = {"text": "Remote human input", "revision": revision, "request_id": identity}
    response = await client.post(path + "/messages", headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "accepted"
    run_id = response.json()["data"]["run_id"]
    queue = webchat_queue_mgr.get_or_create_queue(session.session_id)
    sender, target, payload = await asyncio.wait_for(queue.get(), 2)
    assert sender == remote_admin["resource_owner"] and target == session.session_id
    assert isinstance(payload["_dashboard_account"], DashboardPrincipal)
    assert payload["_dashboard_account"].account_id == remote_admin["account_id"]
    assert payload["message_id"] == run_id
    # Repeating the same admission identity cannot enqueue duplicate execution.
    assert (await client.post(path + "/messages", headers=headers, json=body)).json()[
        "data"
    ]["status"] == "accepted"
    assert queue.empty()
    receipt = await client.get(
        path + "/receipts/" + identity, headers=headers, params={"revision": revision}
    )
    assert receipt.json()["data"]["status"] == "accepted"
    back = webchat_queue_mgr.get_or_create_back_queue(run_id, session.session_id)
    await back.put(
        {
            "type": "plain",
            "data": "streamed result",
            "streaming": True,
            "message_id": run_id,
        }
    )
    await asyncio.sleep(0.1)
    state = await remote_server.asgi_app.state.services.chat.get_session(sender, target)
    assert state["active_runs"]
    assert "streamed result" in str(state["active_runs"])
    assert not server.asgi_app.state.services.chat.chat_runs
    assert await db.count_platform_message_history("webchat", session.session_id) == 0
    # A follow-up admission remains an ordinary input on the same remote queue.
    follow_id = str(uuid4())
    follow = await client.post(
        path + "/messages",
        headers=headers,
        json={**body, "text": "Follow-up", "request_id": follow_id},
    )
    assert follow.json()["data"]["status"] == "accepted"
    follow_run_id = follow.json()["data"]["run_id"]
    assert (await queue.get())[2]["message_id"] == follow_run_id
    for run_id in (run_id, follow_run_id):
        await webchat_queue_mgr.get_or_create_back_queue(run_id, target).put(
            {"type": "end", "data": "", "message_id": run_id}
        )
    await asyncio.sleep(0.2)
    history = (await client.get(path + "/history", headers=headers)).json()["data"][
        "history"
    ]
    assert "streamed result" in str(history)
    assert credential["key"] not in str(history)
    # Old UI revisions cannot send after rebinding, even with identical metadata.
    await client.put(
        connection_path,
        headers=headers,
        json={"name": "Remote", "base_url": "http://remote"},
    )
    stale = await client.post(
        path + "/messages", headers=headers, json={**body, "request_id": str(uuid4())}
    )
    assert stale.status_code == 409 and queue.empty()


@pytest.mark.asyncio
async def test_revocation_during_preparation_rejects_without_queued_input(
    connected_instances, monkeypatch
):
    local, remote_instance, _ = connected_instances
    remote, remote_server, core, db, admin, credential = remote_instance
    session = await db.create_platform_session(creator=admin["resource_owner"])
    chat = remote_server.asgi_app.state.services.chat
    original = chat.validate_chat_selection
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(chat, "validate_chat_selection", paused)
    identity = str(uuid4())
    headers = {"Authorization": "RemoteSession " + credential["key"]}
    path = f"/api/v1/remote-chat/sessions/{session.session_id}"
    task = asyncio.create_task(
        remote.post(
            path + "/messages",
            headers=headers,
            json={"text": "No late admission", "request_id": identity},
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    await remote.delete(
        "/api/v1/chat/remote-access/credentials/" + credential["credential_id"],
        headers={"Authorization": "Bearer " + admin["token"]},
    )
    release.set()
    response = await asyncio.wait_for(task, 2)
    assert response.json()["data"]["status"] == "rejected"
    assert webchat_queue_mgr.get_or_create_queue(session.session_id).empty()
    assert not chat.chat_runs
    assert await db.count_platform_message_history("webchat", session.session_id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", ["key", "exposure", "switch"])
async def test_idle_remote_stream_closes_on_live_grant_change(
    connected_instances, revoke
):
    from starlette.requests import Request

    from astrbot.dashboard.api.remote_chat import remote_session_events

    local, instance, _ = connected_instances
    remote, server, core, db, admin, credential = instance
    service = server.asgi_app.state.services.remote_chat
    session = await db.create_platform_session(creator=admin["resource_owner"])
    account = await service.authenticate(credential["key"])
    request = Request(
        {
            "type": "http",
            "app": server.asgi_app,
            "headers": [
                (b"authorization", ("RemoteSession " + credential["key"]).encode())
            ],
        }
    )
    response = await remote_session_events(session.session_id, request, account)
    events = response.body_iterator
    assert '"snapshot"' in await anext(events)
    if revoke == "key":
        await service.revoke_credential(account, credential["credential_id"])
    elif revoke == "exposure":
        await service.set_exposure(account, "selected", [])
    else:
        core.astrbot_config["chatui"]["remote_sessions_enabled"] = False
    terminal = await asyncio.wait_for(anext(events), 1)
    assert '"access_error"' in terminal
    await events.aclose()


@pytest.mark.asyncio
async def test_unknown_receipt_and_no_credential_forwarding(connected_instances):
    local, instance, observed = connected_instances
    remote, server, core, db, admin, credential = instance
    session = await db.create_platform_session(creator=admin["resource_owner"])
    response = await remote.get(
        f"/api/v1/remote-chat/sessions/{session.session_id}/receipts/{uuid4()}",
        headers={"Authorization": "RemoteSession " + credential["key"]},
    )
    assert response.json()["data"]["status"] == "unknown"
    assert credential["key"] not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["key", "exposure", "local_switch", "remote_switch", "rebind"])
async def test_two_instance_network_stream_followup_stop_and_revocation(
    connected_instances, monkeypatch, boundary
):
    import json
    import socket

    import httpx
    import uvicorn

    from astrbot.dashboard.services import remote_chat_service
    from tests.fixtures.remote_chat_runtime import DeterministicChatRuntime

    local, instance, _ = connected_instances
    client, local_server, local_core, local_db, _, users = local
    remote_client, remote_server, remote_core, remote_db, admin, credential = instance
    monkeypatch.setattr(remote_chat_service, "httpx", httpx)
    servers, serving, sockets = [], [], []
    for app in (local_server.asgi_app, remote_server.asgi_app):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        sock.setblocking(False)
        sockets.append(sock)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        servers.append(server)
        serving.append(asyncio.create_task(server.serve(sockets=[sock])))
    runtime = DeterministicChatRuntime(remote_core, remote_db, delay=0.05, chunks=200)
    session = await remote_db.create_platform_session(creator=admin["resource_owner"])
    listener = asyncio.create_task(runtime.listen(session.session_id))
    try:
        for _ in range(200):
            if all(server.started for server in servers):
                break
            await asyncio.sleep(0.01)
        urls = [f"http://127.0.0.1:{sock.getsockname()[1]}" for sock in sockets]
        headers = {"Authorization": "Bearer " + users[0]["token"]}
        remote_headers = {"Authorization": "Bearer " + admin["token"]}
        async with httpx.AsyncClient(base_url=urls[0], timeout=10) as wire:
            identity = (
                await wire.post(
                    "/api/v1/chat/remote-connections",
                    headers=headers,
                    json={
                        "name": "Wire",
                        "base_url": urls[1],
                        "key": credential["key"],
                    },
                )
            ).json()["data"]["connection_id"]
            revision = next(
                row["revision"]
                for row in (
                    await wire.get("/api/v1/chat/remote-connections", headers=headers)
                ).json()["data"]["connections"]
                if row["connection_id"] == identity
            )
            path = f"/api/v1/chat/remote-connections/{identity}/sessions/{session.session_id}"
            body = {"text": "Initial", "revision": revision, "request_id": str(uuid4())}
            admitted = await wire.post(path + "/messages", headers=headers, json=body)
            assert admitted.json()["data"]["status"] == "accepted", admitted.text
            async with wire.stream(
                "GET", path + "/events", headers=headers, params={"revision": revision}
            ) as response:
                lines = response.aiter_lines()
                async for line in lines:
                    if line.startswith("data:") and "Remote progress" in line:
                        break
                assert response.status_code == 200
                follow = await wire.post(
                    path + "/messages",
                    headers=headers,
                    json={**body, "text": "Follow-up", "request_id": str(uuid4())},
                )
                assert follow.json()["data"]["status"] == "accepted"
                for _ in range(50):
                    if runtime.followups:
                        break
                    await asyncio.sleep(0.05)
                assert runtime.followups == ["Follow-up"]
            # Closing the observer does not cancel execution or its collector.
            assert remote_server.asgi_app.state.services.chat.chat_runs
            async with wire.stream(
                "GET", path + "/events", headers=headers, params={"revision": revision}
            ) as response:
                lines = response.aiter_lines()
                async for line in lines:
                    if line.startswith("data:"):
                        assert json.loads(line[5:])["type"] == "snapshot"
                        break
                if boundary == "key":
                    await remote_client.delete(
                        "/api/v1/chat/remote-access/credentials/" + credential["credential_id"], headers=remote_headers,
                    )
                elif boundary == "exposure":
                    await remote_client.put("/api/v1/chat/remote-access/exposure", headers=remote_headers, json={"mode": "selected", "session_ids": []})
                elif boundary == "local_switch":
                    local_core.astrbot_config["chatui"]["remote_sessions_enabled"] = False
                elif boundary == "remote_switch":
                    remote_core.astrbot_config["chatui"]["remote_sessions_enabled"] = False
                else:
                    await wire.put(f"/api/v1/chat/remote-connections/{identity}", headers=headers, json={"name": "Changed", "base_url": urls[1]})
                async for line in lines:
                    if '"access_error"' in line:
                        break
            assert remote_server.asgi_app.state.services.chat.chat_runs
            assert not local_server.asgi_app.state.services.chat.chat_runs
            stop = await remote_client.post(
                f"/api/v1/chat/sessions/{session.session_id}/stop",
                headers=remote_headers,
            )
            assert stop.json()["data"]["stopped_count"] > 0
            for _ in range(50):
                if not remote_server.asgi_app.state.services.chat.chat_runs:
                    break
                await asyncio.sleep(0.05)
            assert not remote_server.asgi_app.state.services.chat.chat_runs
            history = await remote_server.asgi_app.state.services.chat.get_session(
                admin["resource_owner"], session.session_id
            )
            assert "Follow-up received: Follow-up" in str(history["history"])
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*serving, return_exceptions=True)
        for sock in sockets:
            sock.close()


@pytest.mark.asyncio
async def test_lost_response_keeps_admission_and_receipt_without_resending(connected_instances, monkeypatch):
    _, instance, _ = connected_instances
    remote, server, core, db, admin, credential = instance
    session = await db.create_platform_session(creator=admin["resource_owner"])
    service = server.asgi_app.state.services.remote_chat
    original = service.chat.validate_chat_selection
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused(*args, **kwargs):
        selection = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return selection

    monkeypatch.setattr(service.chat, "validate_chat_selection", paused)
    request_id = str(uuid4())
    path = f"/api/v1/remote-chat/sessions/{session.session_id}"
    headers = {"Authorization": "RemoteSession " + credential["key"]}
    body = {"text": "Response may be lost", "request_id": request_id}
    request = asyncio.create_task(remote.post(path + "/messages", headers=headers, json=body))
    await asyncio.wait_for(entered.wait(), 2)
    request.cancel()
    await asyncio.gather(request, return_exceptions=True)
    release.set()
    receipt = service.receipts[(admin["account_id"], session.session_id, request_id)]
    await asyncio.wait_for(asyncio.shield(receipt["task"]), 2)
    status = await remote.get(path + "/receipts/" + request_id, headers=headers)
    assert status.json()["data"]["status"] == "accepted"
    assert (await remote.post(path + "/messages", headers=headers, json=body)).json()["data"]["status"] == "accepted"
    queue = webchat_queue_mgr.get_or_create_queue(session.session_id)
    assert queue.qsize() == 1
    assert (await queue.get())[2]["message_id"] == receipt["run_id"]
