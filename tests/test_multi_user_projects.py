"""Personal projects, branches and side chats through real Dashboard HTTP."""

import asyncio
import json
from pathlib import Path

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.umop_config_router import UmopConfigRouter
from astrbot.core.utils.shared_preferences import SharedPreferences
from tests.fixtures.dashboard_accounts import account_dashboard as account_dashboard
from tests.fixtures.dashboard_accounts import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_attachments import attachment_chat as attachment_chat
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat
from tests.test_multi_user_chat_config import configured_chat as configured_chat


async def send_reply(
    client,
    core,
    account,
    session_id,
    *,
    thread=False,
    legacy=False,
    message="hello",
    config_id=None,
):
    """Replace external model output while exercising real sends and persistence.

    Args:
        client: HTTP client bound to the production ASGI app.
        core: Real configuration and conversation services.
        account: Identity returned by actual login.
        session_id: Owned main session or thread.
        thread: Use the thread send route.
        legacy: Use its compatible Dashboard endpoint.
        message: Public user message, including attachment IDs.
        config_id: Explicit profile selection for the send.

    Returns:
        SSE response and the model's effective configuration ID.
    """
    effective = []

    async def model():
        sender, target, payload = await webchat_queue_mgr.get_or_create_queue(
            session_id
        ).get()
        assert sender == account["resource_owner"] and target == session_id
        assert payload["_dashboard_account"].resource_owner == sender
        origin = f"webchat:FriendMessage:webchat!{sender}!{target}"
        effective.append(core.umop_config_router.get_conf_id_for_umop(origin))
        cid = await core.conversation_manager.get_curr_conversation_id(origin)
        if cid is None:
            cid = await core.conversation_manager.new_conversation(
                origin, platform_id="webchat"
            )
        conversation = await core.conversation_manager.get_conversation(origin, cid)
        history = json.loads(conversation.history or "[]")
        history.extend(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": f"private-reply-{sender}"},
                {
                    "role": "_checkpoint",
                    "content": {"id": payload["llm_checkpoint_id"]},
                },
            ]
        )
        await core.conversation_manager.update_conversation(
            origin, cid, history=history
        )
        await webchat_queue_mgr.put_back_queue(
            payload["message_id"],
            {"type": "plain", "data": f"private-reply-{sender}", "streaming": True},
        )
        await webchat_queue_mgr.put_back_queue(payload["message_id"], {"type": "end"})

    if thread:
        path = (
            "/api/chat/thread/send"
            if legacy
            else f"/api/v1/chat/threads/{session_id}/messages"
        )
        body = {"message": message, "config_id": config_id, "thinking_effort": "low"}
        if legacy:
            body["thread_id"] = session_id
    else:
        path = "/api/v1/chat"
        body = {"session_id": session_id, "message": message, "config_id": config_id}
    worker = asyncio.create_task(model())
    try:
        response = await asyncio.wait_for(
            client.post(
                path, headers={"Authorization": f"Bearer {account['token']}"}, json=body
            ),
            10,
        )
        assert f"private-reply-{account['resource_owner']}" in response.text, (
            response.text
        )
        await asyncio.wait_for(worker, 5)
        return response, effective[0]
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_personal_projects_branches_threads_and_attachment_persistence(
    configured_chat, attachment_chat
):
    client, server, core, db, admin, users = configured_chat
    service = server.asgi_app.state.services.chat
    service.conv_mgr = core.conversation_manager
    saved = []
    for index, account in enumerate([*users, admin]):
        headers = {"Authorization": f"Bearer {account['token']}"}
        profile = ("research", "writing", "default")[index]
        project = (
            await client.post(
                "/api/v1/chat/projects",
                headers=headers,
                json={"title": f"private-project-{index}", "workspace_type": "project"},
            )
        ).json()["data"]
        pid = project["project_id"]
        sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
            "data"
        ]["session_id"]
        added = await client.post(
            f"/api/v1/chat/projects/{pid}/sessions/{sid}", headers=headers
        )
        assert added.json()["status"] == "ok", added.text
        upload = (
            await client.post(
                "/api/v1/files",
                headers=headers,
                files={
                    "file": (
                        "private.txt",
                        f"attachment-{index}".encode(),
                        "text/plain",
                    )
                },
            )
        ).json()["data"]
        await send_reply(
            client,
            core,
            account,
            sid,
            config_id=profile,
            message=[{"type": "file", "attachment_id": upload["attachment_id"]}],
        )
        history = (
            await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
        ).json()["data"]["history"]
        bot = next(row for row in history if row["content"]["type"] == "bot")
        response = await client.post(
            f"/api/v1/chat/sessions/{sid}/messages/{bot['id']}/branch", headers=headers
        )
        assert response.json()["status"] == "ok", response.text
        branch_id = response.json()["data"]["session_id"]
        branch = await client.get(f"/api/v1/chat/sessions/{branch_id}", headers=headers)
        assert upload["attachment_id"] in branch.text
        assert (
            await db.get_project_by_session(branch_id, account["resource_owner"])
        ).project_id == pid
        binding = await client.get(
            f"/api/v1/chat/sessions/{branch_id}/config", headers=headers
        )
        assert binding.json()["data"]["config_id"] == profile
        _, actual = await send_reply(client, core, account, branch_id)
        assert actual == profile
        threads = []
        for side_chat in (False, True):
            body = {"session_id": sid, "side_chat": side_chat}
            if not side_chat:
                body.update(parent_message_id=bot["id"], selected_text="private answer")
            response = await client.post(
                "/api/chat/thread/create" if side_chat else "/api/v1/chat/threads",
                headers=headers,
                json=body,
            )
            assert response.json()["status"] == "ok", response.text
            tid = response.json()["data"]["thread_id"]
            threads.append(tid)
            binding = await client.get(
                f"/api/v1/chat/sessions/{tid}/config", headers=headers
            )
            assert binding.json()["data"]["config_id"] == profile
            _, actual = await send_reply(
                client,
                core,
                account,
                tid,
                thread=True,
                legacy=side_chat,
                message=[{"type": "file", "attachment_id": upload["attachment_id"]}],
            )
            assert actual == profile
            response = await client.get(f"/api/v1/chat/threads/{tid}", headers=headers)
            assert upload["attachment_id"] in response.text
            changed = await client.put(
                f"/api/v1/chat/sessions/{tid}/config",
                headers=headers,
                json={"config_id": "default"},
            )
            assert changed.json()["status"] == "ok", changed.text
            assert (
                await client.get(f"/api/v1/chat/sessions/{sid}/config", headers=headers)
            ).json()["data"]["config_id"] == profile
        members = await client.get(
            f"/api/v1/chat/projects/{pid}/sessions", headers=headers
        )
        assert {row["session_id"] for row in members.json()["data"]} == {sid, branch_id}
        assert branch_id in members.text and str(bot["id"]) in members.text
        listed = await client.get(
            f"/api/v1/chat/sessions/{sid}/side-threads", headers=headers
        )
        assert [row["thread_id"] for row in listed.json()["data"]["threads"]] == [
            threads[1]
        ]
        saved.append((account, pid, sid, bot["id"], branch_id, threads, upload))

    for account, pid, sid, mid, bid, threads, upload in saved:
        headers = {"Authorization": f"Bearer {account['token']}"}
        projects = (
            await client.get("/api/chatui_project/list", headers=headers)
        ).json()["data"]
        assert [p["project_id"] for p in projects] == [pid]
        preview = await client.get(
            f"/api/v1/files/{upload['attachment_id']}/content", headers=headers
        )
        assert b"attachment-" in preview.content
        for actor, other_pid, other_sid, other_mid, _, _, other_upload in saved:
            if actor is account:
                continue
            other_headers = {"Authorization": f"Bearer {actor['token']}"}
            requests = [
                ("GET", f"/api/v1/chat/projects/{pid}", None),
                ("PATCH", f"/api/v1/chat/projects/{pid}", {"title": "stolen"}),
                ("DELETE", f"/api/v1/chat/projects/{pid}", None),
                ("GET", f"/api/chatui_project/get_sessions?project_id={pid}", None),
                (
                    "POST",
                    "/api/chatui_project/add_session",
                    {"project_id": pid, "session_id": other_sid},
                ),
                ("POST", f"/api/v1/chat/projects/{other_pid}/sessions/{sid}", None),
                ("POST", "/api/chatui_project/remove_session", {"session_id": sid}),
                (
                    "POST",
                    f"/api/v1/chat/sessions/{other_sid}/messages/{mid}/branch",
                    None,
                ),
                (
                    "POST",
                    "/api/v1/chat/threads",
                    {
                        "session_id": other_sid,
                        "parent_message_id": mid,
                        "selected_text": "forged",
                    },
                ),
                (
                    "POST",
                    "/api/chat/thread/create",
                    {"session_id": sid, "side_chat": True},
                ),
                ("GET", f"/api/v1/chat/projects/{pid}/workspace/files", None),
            ]
            for tid in threads:
                requests.extend(
                    [
                        ("GET", f"/api/v1/chat/threads/{tid}", None),
                        ("DELETE", f"/api/v1/chat/threads/{tid}", None),
                        ("POST", f"/api/v1/chat/threads/{tid}/stop", None),
                        ("GET", f"/api/chat/thread/get?thread_id={tid}", None),
                        (
                            "POST",
                            "/api/chat/thread/send",
                            {"thread_id": tid, "message": "stolen"},
                        ),
                        (
                            "PUT",
                            f"/api/v1/chat/sessions/{tid}/config",
                            {"config_id": "research"},
                        ),
                    ]
                )
            for method, path, body in requests:
                denied = await client.request(
                    method, path, headers=other_headers, json=body
                )
                assert (
                    denied.status_code >= 400 or denied.json()["status"] == "error"
                ), (method, path, denied.text)
                assert (
                    f"private-project-{saved.index((account, pid, sid, mid, bid, threads, upload))}"
                    not in denied.text
                )
                assert f"private-reply-{account['resource_owner']}" not in denied.text
            for path, body in [
                (
                    f"/api/v1/chat/threads/{threads[1]}/messages",
                    {
                        "message": [
                            {
                                "type": "file",
                                "attachment_id": other_upload["attachment_id"],
                            }
                        ]
                    },
                ),
                (
                    "/api/chat/thread/send",
                    {
                        "thread_id": threads[0],
                        "message": [
                            {
                                "type": "file",
                                "attachment_id": other_upload["attachment_id"],
                            }
                        ],
                    },
                ),
            ]:
                before = await db.count_platform_message_history(
                    "webchat_thread", threads[0]
                )
                denied = await client.post(path, headers=headers, json=body)
                assert denied.json()["status"] == "error", denied.text
                assert (
                    await db.count_platform_message_history(
                        "webchat_thread", threads[0]
                    )
                    == before
                )
        assert (await db.get_chatui_project_by_id(pid)).title.startswith(
            "private-project-"
        )
        assert (
            await db.get_project_by_session(sid, account["resource_owner"])
        ).project_id == pid

    await db.engine.dispose()
    reopened = SQLiteDatabase(db.db_path)
    await reopened.initialize()
    preferences = SharedPreferences(reopened)
    router = UmopConfigRouter(preferences)
    await router.initialize()
    try:
        for account, pid, sid, mid, bid, threads, upload in saved:
            assert (
                await reopened.get_project_by_session(bid, account["resource_owner"])
            ).project_id == pid
            for tid in threads:
                assert (
                    await reopened.get_webchat_thread_by_id(tid)
                ).parent_session_id == sid
                assert (
                    await reopened.count_platform_message_history("webchat_thread", tid)
                    == 2
                )
                assert (
                    router.get_conf_id_for_umop(
                        f"webchat:FriendMessage:webchat!{account['resource_owner']}!{tid}"
                    )
                    == "default"
                )
            assert Path(
                (await reopened.get_attachment_by_id(upload["attachment_id"])).path
            ).exists()
    finally:
        await preferences.close()
        await reopened.engine.dispose()
    # Deleting a child leaves both parent attachments and another account intact.
    account, pid, sid, mid, bid, threads, upload = saved[0]
    headers = {"Authorization": f"Bearer {account['token']}"}
    for tid in threads:
        deleted = await client.post(
            "/api/chat/thread/delete", headers=headers, json={"thread_id": tid}
        )
        assert deleted.json()["status"] == "ok", deleted.text
    assert (
        await client.get(
            f"/api/v1/files/{upload['attachment_id']}/content", headers=headers
        )
    ).status_code == 200
    detached = await client.delete(
        f"/api/v1/chat/projects/sessions/{bid}", headers=headers
    )
    assert detached.json()["status"] == "ok", detached.text
    assert await db.get_project_by_session(bid, account["resource_owner"]) is None
    changed = await client.patch(
        f"/api/v1/chat/projects/{pid}", headers=headers, json={"title": "renamed"}
    )
    assert changed.json()["status"] == "ok", changed.text
    deleted = await client.delete(f"/api/v1/chat/projects/{pid}", headers=headers)
    assert deleted.json()["status"] == "ok", deleted.text
    assert await db.get_platform_session_by_id(sid) is not None
    for _, other_pid, *_ in saved[1:]:
        assert await db.get_chatui_project_by_id(other_pid) is not None


@pytest.mark.asyncio
async def test_workspace_binding_fields_and_corrupt_parent_relations(
    configured_chat, tmp_path
):
    client, server, core, db, admin, users = configured_chat
    service = server.asgi_app.state.services.chat_projects
    chat = server.asgi_app.state.services.chat
    chat.conv_mgr = core.conversation_manager
    account, foreign = users
    headers = {"Authorization": f"Bearer {account['token']}"}
    foreign_headers = {"Authorization": f"Bearer {foreign['token']}"}
    root = tmp_path / "configured-project"
    root.mkdir()
    (root / "private.txt").write_text("private workspace", encoding="utf-8")
    project = (
        await client.post(
            "/api/v1/chat/projects",
            headers=headers,
            json={
                "title": "Workspace",
                "workspace_type": "custom",
                "workspace_path": str(root),
                "spcode_no_codegraph": True,
            },
        )
    ).json()["data"]
    pid = project["project_id"]
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    other_sid = (
        await client.get("/api/v1/chat/sessions/new", headers=foreign_headers)
    ).json()["data"]["session_id"]
    await client.post(f"/api/v1/chat/projects/{pid}/sessions/{sid}", headers=headers)
    tid = (
        await client.post(
            "/api/v1/chat/threads",
            headers=headers,
            json={"session_id": sid, "side_chat": True},
        )
    ).json()["data"]["thread_id"]
    for resource_id in (sid, tid):
        umo = f"webchat:FriendMessage:webchat!{account['resource_owner']}!{resource_id}"
        resource, actual = await service.require_chat_workspace(
            account["resource_owner"], umo, directory=str(root / ".")
        )
        assert actual == root.resolve()
        assert (
            resource.session_id if resource_id == sid else resource.thread_id
        ) == resource_id
        safe = await service.validate_project_operation(
            account["resource_owner"],
            umo,
            "project-load",
            {"umo": umo, "directory": str(root), "force": True, "no_codegraph": True},
        )
        assert safe == {
            "umo": umo,
            "directory": str(root.resolve()),
            "force": True,
            "no_agentsmd": False,
            "no_codegraph": True,
        }
        for operation in ("project-status", "project-unload"):
            assert await service.validate_project_operation(
                account["resource_owner"], umo, operation
            ) == {"umo": umo}
        from astrbot.dashboard.services.chatui_project_service import (
            ChatUIProjectServiceError,
        )

        for body in [
            {"directory": str(tmp_path)},
            {"directory": str(root), "create": True},
            {"directory": str(root), "git_init": True},
            {"directory": str(root), "_owner": "forged"},
            {"directory": str(root), "no_codegraph": False},
            {"directory": str(root), "force": "true"},
        ]:
            with pytest.raises(ChatUIProjectServiceError):
                await service.validate_project_operation(
                    account["resource_owner"], umo, "project-load", body
                )
        with pytest.raises(ChatUIProjectServiceError):
            await service.require_chat_workspace(foreign["resource_owner"], umo)
        with pytest.raises(ChatUIProjectServiceError):
            await service.require_chat_workspace(
                account["resource_owner"], umo.replace("FriendMessage", "GroupMessage")
            )
    for suffix in (
        "workspace/files",
        "workspace/file?path=private.txt",
        "workspace/file/download?path=private.txt",
    ):
        path = f"/api/v1/chat/projects/{pid}/{suffix}"
        own = await client.get(path, headers=headers)
        assert own.status_code == 200 and "private" in own.text, own.text
        for actor in (foreign, admin):
            denied = await client.get(
                path, headers={"Authorization": f"Bearer {actor['token']}"}
            )
            assert "private workspace" not in denied.text
            assert denied.status_code >= 400 or denied.json()["status"] == "error"
    for path, body in [
        (
            "/api/v1/chat/projects",
            {"title": "forged", "creator": foreign["resource_owner"]},
        ),
        (
            "/api/v1/chat/threads",
            {"session_id": sid, "side_chat": True, "project_id": pid},
        ),
        (
            f"/api/v1/chat/threads/{tid}/messages",
            {"message": "forged", "_platform_history_id": "webchat"},
        ),
        (
            f"/api/v1/chat/threads/{tid}/messages",
            {"message": [{"type": "file", "path": str(root / "private.txt")}]},
        ),
    ]:
        response = await client.post(path, headers=headers, json=body)
        assert response.status_code >= 400, response.text
    # Even a malformed persisted association cannot turn an owned project into a foreign-session reader.
    await db.add_session_to_project(other_sid, pid)
    listed = await client.get(f"/api/v1/chat/projects/{pid}/sessions", headers=headers)
    assert other_sid not in listed.text
    foreign_umo = (
        f"webchat:FriendMessage:webchat!{foreign['resource_owner']}!{other_sid}"
    )
    with pytest.raises(ChatUIProjectServiceError):
        await service.require_chat_workspace(foreign["resource_owner"], foreign_umo)
    # Session-only workspaces are valid and never become an arbitrary submitted directory.
    await db.remove_session_from_project(other_sid)
    _, session_root = await service.require_chat_workspace(
        foreign["resource_owner"], foreign_umo
    )
    assert session_root != root.resolve()
    with pytest.raises(ChatUIProjectServiceError):
        await service.require_chat_workspace(
            foreign["resource_owner"], foreign_umo, directory=str(root)
        )
    with pytest.raises(ChatUIProjectServiceError):
        await service.validate_project_operation(
            foreign["resource_owner"],
            foreign_umo,
            "project-load",
            {"directory": str(session_root)},
        )
    orphan = await db.create_webchat_thread(
        creator=account["resource_owner"],
        parent_session_id=other_sid,
        parent_message_id=0,
        base_checkpoint_id="",
        selected_text="",
    )
    for path in [
        f"/api/v1/chat/threads/{orphan.thread_id}",
        f"/api/v1/chat/sessions/{orphan.thread_id}/config",
    ]:
        denied = await client.get(path, headers=headers)
        assert denied.json()["status"] == "error", denied.text


@pytest.mark.asyncio
async def test_branch_copy_retains_untrusted_import_marker(configured_chat):
    client, server, core, db, admin, users = configured_chat
    chat = server.asgi_app.state.services.chat
    chat.conv_mgr = core.conversation_manager
    account = users[0]
    headers = {"Authorization": f"Bearer {account['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    await send_reply(client, core, account, sid)
    rows = await db.get_platform_message_history("webchat", sid)
    bot = next(row for row in rows if row.content["type"] == "bot")
    await db.update_platform_message_history(
        bot.id,
        content={
            **bot.content,
            "_imported": True,
            "file_changes": {"files": [{"path": "display-only.txt"}]},
        },
    )
    branch = await client.post(
        f"/api/v1/chat/sessions/{sid}/messages/{bot.id}/branch", headers=headers
    )
    assert branch.json()["status"] == "ok", branch.text
    branch_id = branch.json()["data"]["session_id"]
    history = (
        await client.get(f"/api/v1/chat/sessions/{branch_id}", headers=headers)
    ).json()["data"]["history"]
    copied = next(row["content"] for row in history if row["content"]["type"] == "bot")
    assert copied["_imported"] is True
    assert "_canonical_path" not in json.dumps(copied)


@pytest.mark.asyncio
async def test_side_chat_stream_resume_stop_and_delete_are_owner_scoped(
    configured_chat,
):
    from contextlib import AsyncExitStack

    from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
    from astrbot.core.utils.active_event_registry import active_event_registry
    from tests.test_multi_user_runtime import ASGIStream

    client, server, core, db, admin, users = configured_chat
    app = server.asgi_app
    app.state.services.chat.conv_mgr = core.conversation_manager
    threads = []
    for account in users:
        headers = {"Authorization": f"Bearer {account['token']}"}
        sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
            "data"
        ]["session_id"]
        tid = (
            await client.post(
                "/api/v1/chat/threads",
                headers=headers,
                json={"session_id": sid, "side_chat": True},
            )
        ).json()["data"]["thread_id"]
        threads.append(tid)
    release = [asyncio.Event(), asyncio.Event()]
    ready = [asyncio.Event(), asyncio.Event()]
    adapter = WebChatAdapter({}, {}, asyncio.Queue())

    async def model(index):
        sender, target, payload = await webchat_queue_mgr.get_or_create_queue(
            threads[index]
        ).get()
        event = adapter.create_event(
            await adapter.convert_message((sender, target, payload))
        )
        assert (
            event.get_extra("_dashboard_account").resource_owner
            == users[index]["resource_owner"]
        )
        active_event_registry.register(event)
        active_event_registry.register_agent_stop_callback(event, release[index].set)
        try:
            await webchat_queue_mgr.put_back_queue(
                payload["message_id"],
                {
                    "type": "plain",
                    "data": f"private-thread-prefix-{index}",
                    "streaming": True,
                },
            )
            ready[index].set()
            await release[index].wait()
            await webchat_queue_mgr.put_back_queue(
                payload["message_id"], {"type": "end"}
            )
        finally:
            active_event_registry.unregister(event)

    workers = []
    try:
        async with AsyncExitStack() as stack:
            initial = [
                await stack.enter_async_context(
                    ASGIStream(
                        app,
                        f"/api/v1/chat/threads/{tid}/messages",
                        account["token"],
                        {
                            "message": "hello",
                            "config_id": ("research", "writing")[index],
                        },
                    )
                )
                for index, (account, tid) in enumerate(zip(users, threads))
            ]
            workers = [asyncio.create_task(model(i)) for i in range(2)]
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in ready)), 10
            )
            runs = []
            for i, tid in enumerate(threads):
                own = await client.get(
                    f"/api/v1/chat/threads/{tid}",
                    headers={"Authorization": f"Bearer {users[i]['token']}"},
                )
                runs.append(own.json()["data"]["active_runs"][0]["run_id"])
                assert f"private-thread-prefix-{i}" in json.dumps(
                    await initial[i].event("plain")
                )
            resumed = [
                await stack.enter_async_context(
                    ASGIStream(app, f"/api/v1/chat/runs/{run}/stream", account["token"])
                )
                for run, account in zip(runs, users)
            ]
            for i in range(2):
                assert f"private-thread-prefix-{i}" in json.dumps(
                    await resumed[i].event("run_snapshot")
                )
                for actor in (users[1 - i], admin):
                    headers = {"Authorization": f"Bearer {actor['token']}"}
                    for method, path in [
                        ("GET", f"/api/v1/chat/runs/{runs[i]}/stream"),
                        ("POST", f"/api/v1/chat/threads/{threads[i]}/stop"),
                        ("DELETE", f"/api/v1/chat/threads/{threads[i]}"),
                    ]:
                        denied = await client.request(method, path, headers=headers)
                        assert denied.json()["status"] == "error", denied.text
                        assert "private-thread-prefix" not in denied.text
                assert not workers[i].done()
            stopped = await client.post(
                f"/api/v1/chat/threads/{threads[0]}/stop",
                headers={"Authorization": f"Bearer {users[0]['token']}"},
            )
            assert stopped.json()["data"]["stopped_count"] == 1
            await asyncio.wait_for(workers[0], 5)
            await initial[0].event("end")
            await resumed[0].event("end")
            assert not workers[1].done()
            deleted = await client.delete(
                f"/api/v1/chat/threads/{threads[0]}",
                headers={"Authorization": f"Bearer {users[0]['token']}"},
            )
            assert deleted.json()["status"] == "ok"
            assert await db.get_webchat_thread_by_id(threads[1]) is not None
            denied = await client.get(
                f"/api/v1/chat/runs/{runs[0]}/stream",
                headers={"Authorization": f"Bearer {users[0]['token']}"},
            )
            assert denied.json()["status"] == "error"
            release[1].set()
            await asyncio.wait_for(workers[1], 5)
            await initial[1].event("end")
            assert "private-thread-prefix-1" not in "".join(
                initial[0].frames + resumed[0].frames
            )
    finally:
        for event in release:
            event.set()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_parent", [False, True])
async def test_deleting_one_branch_preserves_other_attachment(
    configured_chat, attachment_chat, delete_parent
):
    client, server, core, db, admin, users = configured_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    account = users[0]
    headers = {"Authorization": f"Bearer {account['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    uploaded = (
        await client.post(
            "/api/v1/files",
            headers=headers,
            files={"file": ("retained.txt", b"retained in other branch", "text/plain")},
        )
    ).json()["data"]
    aid = uploaded["attachment_id"]
    await send_reply(
        client, core, account, sid, message=[{"type": "file", "attachment_id": aid}]
    )
    rows = await db.get_platform_message_history("webchat", sid)
    bot = next(row for row in rows if row.content["type"] == "bot")
    branch = await client.post(
        f"/api/v1/chat/sessions/{sid}/messages/{bot.id}/branch", headers=headers
    )
    assert branch.json()["status"] == "ok", branch.text
    bid = branch.json()["data"]["session_id"]
    deleted_sid, retained_sid = (sid, bid) if delete_parent else (bid, sid)
    deleted = await client.delete(
        f"/api/v1/chat/sessions/{deleted_sid}", headers=headers
    )
    assert deleted.json()["status"] == "ok", deleted.text
    retained = await client.get(
        f"/api/v1/chat/sessions/{retained_sid}", headers=headers
    )
    assert aid in retained.text, retained.text
    attachment = await client.get(f"/api/v1/files/{aid}/content", headers=headers)
    assert attachment.status_code == 200, attachment.text
    assert attachment.content == b"retained in other branch"
    row = await db.get_attachment_by_id(aid)
    assert Path(row.path).exists()
    final = await client.delete(
        f"/api/v1/chat/sessions/{retained_sid}", headers=headers
    )
    assert final.json()["status"] == "ok", final.text
    assert not Path(row.path).exists()
    assert await db.get_attachment_by_id(aid) is None


@pytest.mark.asyncio
async def test_side_thread_retains_shared_file_until_last_owned_reference_is_deleted(
    configured_chat, attachment_chat
):
    client, server, core, db, admin, users = configured_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    account, foreign = users
    headers = {"Authorization": f"Bearer {account['token']}"}
    foreign_headers = {"Authorization": f"Bearer {foreign['token']}"}
    sessions = []
    for _ in range(2):
        sessions.append(
            (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
                "data"
            ]["session_id"]
        )
    uploaded = (
        await client.post(
            "/api/v1/files",
            headers=headers,
            files={"file": ("shared.txt", b"owned shared bytes", "text/plain")},
        )
    ).json()["data"]
    aid = uploaded["attachment_id"]
    row = await db.get_attachment_by_id(aid)
    await send_reply(
        client,
        core,
        account,
        sessions[0],
        message=[{"type": "file", "attachment_id": aid}],
    )
    tid = (
        await client.post(
            "/api/v1/chat/threads",
            headers=headers,
            json={"session_id": sessions[1], "side_chat": True},
        )
    ).json()["data"]["thread_id"]
    await send_reply(
        client,
        core,
        account,
        tid,
        thread=True,
        message=[{"type": "file", "attachment_id": aid}],
    )
    # A forged foreign reference and malformed part cannot pin another owner's file.
    foreign_sid = (
        await client.get("/api/v1/chat/sessions/new", headers=foreign_headers)
    ).json()["data"]["session_id"]
    await db.insert_platform_message_history(
        platform_id="webchat",
        user_id=foreign_sid,
        sender_id="bot",
        sender_name="bot",
        content={
            "type": "bot",
            "message": [{"type": "file", "attachment_id": aid}, "malformed old part"],
        },
    )
    foreign_upload = (
        await client.post(
            "/api/v1/files",
            headers=foreign_headers,
            files={"file": ("foreign.txt", b"foreign bytes", "text/plain")},
        )
    ).json()["data"]
    deleted = await client.delete(
        f"/api/v1/chat/sessions/{sessions[0]}", headers=headers
    )
    assert deleted.json()["status"] == "ok", deleted.text
    retained = await client.get(f"/api/v1/files/{aid}/content", headers=headers)
    assert retained.content == b"owned shared bytes"
    assert (
        aid in (await client.get(f"/api/v1/chat/threads/{tid}", headers=headers)).text
    )
    final = await client.delete(f"/api/v1/chat/threads/{tid}", headers=headers)
    assert final.json()["status"] == "ok", final.text
    assert not Path(row.path).exists()
    assert await db.get_attachment_by_id(aid) is None
    assert (
        await client.get(
            f"/api/v1/files/{foreign_upload['attachment_id']}/content",
            headers=foreign_headers,
        )
    ).content == b"foreign bytes"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["retry", "edit"])
async def test_branched_quotes_survive_source_deletion_edit_and_retry(
    configured_chat, attachment_chat, action
):
    client, server, core, db, admin, users = configured_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    account = users[0]
    headers = {"Authorization": f"Bearer {account['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    upload = (
        await client.post(
            "/api/v1/files",
            headers=headers,
            files={"file": ("quoted.txt", b"quoted attachment", "text/plain")},
        )
    ).json()["data"]
    await send_reply(
        client,
        core,
        account,
        sid,
        message=[{"type": "file", "attachment_id": upload["attachment_id"]}],
    )
    first = await db.get_platform_message_history("webchat", sid)
    source_bot = next(row for row in first if row.content["type"] == "bot")
    await send_reply(
        client,
        core,
        account,
        sid,
        message=[
            {
                "type": "reply",
                "message_id": source_bot.id,
                "selected_text": "my earlier answer",
            },
            {"type": "plain", "text": "follow the earlier quote"},
        ],
    )
    rows = await db.get_platform_message_history("webchat", sid)
    last_bot = max(
        (row for row in rows if row.content["type"] == "bot"), key=lambda row: row.id
    )
    last_user = max(
        (row for row in rows if row.content["type"] == "user"), key=lambda row: row.id
    )
    await db.update_platform_message_history(
        last_user.id, content={**last_user.content, "_imported": True}
    )
    response = await client.post(
        f"/api/v1/chat/sessions/{sid}/messages/{last_bot.id}/branch", headers=headers
    )
    assert response.json()["status"] == "ok", response.text
    bid = response.json()["data"]["session_id"]
    history = (
        await client.get(f"/api/v1/chat/sessions/{bid}", headers=headers)
    ).json()["data"]["history"]
    user = max(
        (row for row in history if row["content"]["type"] == "user"),
        key=lambda row: row["id"],
    )
    bot = max(
        (row for row in history if row["content"]["type"] == "bot"),
        key=lambda row: row["id"],
    )
    quote = next(part for part in user["content"]["message"] if part["type"] == "reply")
    copied_target = next(row for row in history if row["id"] == quote["message_id"])
    assert quote["message_id"] != source_bot.id
    assert quote["selected_text"] == "my earlier answer"
    assert copied_target["user_id"] == bid and copied_target["content"]["type"] == "bot"
    assert user["content"]["_imported"] is True
    deleted = await client.delete(f"/api/v1/chat/sessions/{sid}", headers=headers)
    assert deleted.json()["status"] == "ok", deleted.text
    assert (
        await client.get(
            f"/api/v1/files/{upload['attachment_id']}/content", headers=headers
        )
    ).content == b"quoted attachment"
    if action == "edit":
        edited = await client.patch(
            f"/api/v1/chat/sessions/{bid}/messages/{user['id']}",
            headers=headers,
            json={"content": user["content"]},
        )
        assert edited.json()["status"] == "ok", edited.text
        saved = edited.json()["data"]["message"]
        path = "/api/v1/chat"
        body = {
            "session_id": bid,
            "message": saved["content"]["message"],
            "_skip_user_history": True,
            "_llm_checkpoint_id": saved["llm_checkpoint_id"],
        }
    else:
        path = f"/api/v1/chat/sessions/{bid}/messages/{bot['id']}/regenerate"
        body = {}

    async def model():
        _, _, payload = await webchat_queue_mgr.get_or_create_queue(bid).get()
        await webchat_queue_mgr.put_back_queue(
            payload["message_id"],
            {"type": "plain", "data": "branch quote still works", "streaming": True},
        )
        await webchat_queue_mgr.put_back_queue(payload["message_id"], {"type": "end"})

    worker = asyncio.create_task(model())
    try:
        result = await asyncio.wait_for(
            client.post(path, headers=headers, json=body), 10
        )
        assert "branch quote still works" in result.text, result.text
        await asyncio.wait_for(worker, 5)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    stored = (await client.get(f"/api/v1/chat/sessions/{bid}", headers=headers)).json()[
        "data"
    ]["history"]
    saved_user = max(
        (row for row in stored if row["content"]["type"] == "user"),
        key=lambda row: row["id"],
    )
    assert (
        next(
            part for part in saved_user["content"]["message"] if part["type"] == "reply"
        )["message_id"]
        == copied_target["id"]
    )


@pytest.mark.asyncio
async def test_branch_does_not_remap_foreign_or_uncopied_quote_ids(configured_chat):
    client, server, core, db, admin, users = configured_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    account, foreign = users
    headers = {"Authorization": f"Bearer {account['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    await send_reply(client, core, account, sid)
    first = await db.get_platform_message_history("webchat", sid)
    target = next(row for row in first if row.content["type"] == "bot")
    user = next(row for row in first if row.content["type"] == "user")
    await send_reply(client, core, account, sid)
    later = max(
        await db.get_platform_message_history("webchat", sid), key=lambda row: row.id
    )
    foreign_session = await db.create_platform_session(
        creator=foreign["resource_owner"], platform_id="webchat"
    )
    foreign_message = await db.insert_platform_message_history(
        platform_id="webchat",
        user_id=foreign_session.session_id,
        sender_id="bot",
        sender_name="bot",
        content={
            "type": "bot",
            "message": [{"type": "plain", "text": "foreign secret"}],
        },
    )
    await db.update_platform_message_history(
        user.id,
        content={
            **user.content,
            "_imported": True,
            "message": [
                *user.content["message"],
                *[
                    {"type": "reply", "message_id": mid}
                    for mid in (
                        target.id,
                        later.id,
                        foreign_message.id,
                        "invalid",
                        foreign_message.id + 2,
                    )
                ],
            ],
        },
    )
    response = await client.post(
        f"/api/v1/chat/sessions/{sid}/messages/{target.id}/branch", headers=headers
    )
    assert response.json()["status"] == "ok", response.text
    bid = response.json()["data"]["session_id"]
    history = (
        await client.get(f"/api/v1/chat/sessions/{bid}", headers=headers)
    ).json()["data"]["history"]
    copied_user = next(row for row in history if row["content"]["type"] == "user")
    copied_bot = next(row for row in history if row["content"]["type"] == "bot")
    quotes = [
        part for part in copied_user["content"]["message"] if part["type"] == "reply"
    ]
    assert quotes == [{"type": "reply", "message_id": copied_bot["id"]}]
    assert copied_user["content"]["_imported"] is True
    assert "foreign secret" not in json.dumps(history)


@pytest.mark.asyncio
async def test_thread_preview_urls_stay_outside_public_attachment_contract(
    configured_chat, attachment_chat
):
    client, server, core, db, admin, users = configured_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    account = users[0]
    headers = {"Authorization": f"Bearer {account['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    tid = (
        await client.post(
            "/api/v1/chat/threads",
            headers=headers,
            json={"session_id": sid, "side_chat": True},
        )
    ).json()["data"]["thread_id"]
    upload = (
        await client.post(
            "/api/v1/files",
            headers=headers,
            files={"file": ("side.txt", b"owned side file", "text/plain")},
        )
    ).json()["data"]
    part = {
        "type": "file",
        "attachment_id": upload["attachment_id"],
        "filename": "side.txt",
    }
    rejected = await client.post(
        f"/api/v1/chat/threads/{tid}/messages",
        headers=headers,
        json={"message": [{**part, "embedded_url": "blob:local-preview"}]},
    )
    assert rejected.status_code == 400, rejected.text
    assert await db.count_platform_message_history("webchat_thread", tid) == 0
    await send_reply(client, core, account, tid, thread=True, message=[part])
    history = await client.get(f"/api/v1/chat/threads/{tid}", headers=headers)
    assert upload["attachment_id"] in history.text
    assert "embedded_url" not in history.text
    assert (
        await client.get(
            f"/api/v1/files/{upload['attachment_id']}/content", headers=headers
        )
    ).content == b"owned side file"
