"""Account restoration and permanent cleanup through production HTTP admission."""

import asyncio
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from astrbot.core import file_token_service, sp
from astrbot.core.db.po import (
    Attachment,
    ChatUIProject,
    ConversationV2,
    CronJob,
    DashboardAccount,
    PlatformMessageHistory,
    PlatformSession,
    Preference,
    ProviderStat,
    SessionProjectRelation,
    UmoAlias,
    WebChatThread,
)
from astrbot.core.goal import goal_service
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.utils.webchat_access import DashboardPrincipal
from astrbot.core.workspace import normalize_umo_for_workspace
from astrbot.dashboard.services import account_cleanup
from tests.test_multi_user_chat import USER_PASSWORD, login
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat
from tests.test_multi_user_runtime import ASGIStream, ASGIWebSocket


@pytest.mark.asyncio
async def test_enable_keeps_resources_and_never_revives_old_tokens(ordinary_chat):
    client, server, core, db, admin, users = ordinary_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    target = users[0]
    admin_headers = {"Authorization": f"Bearer {admin['token']}"}
    headers = {"Authorization": f"Bearer {target['token']}"}
    created = await client.get("/api/v1/chat/sessions/new", headers=headers)
    session_id = created.json()["data"]["session_id"]
    base = f"/api/v1/accounts/{target['account_id']}"
    assert (
        await client.post(base + "/disable", headers=admin_headers)
    ).status_code == 200
    response = await client.post(base + "/enable", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["data"]["resource_owner"] == target["resource_owner"]
    assert response.json()["data"]["enabled"] is True
    assert (
        await client.get("/api/v1/auth/session", headers=headers)
    ).status_code == 401
    assert (
        await client.post(base + "/enable", headers=admin_headers)
    ).status_code == 200
    fresh = await login(client, target["username"], USER_PASSWORD)
    assert fresh["account_id"] == target["account_id"]
    assert (
        await client.get(
            f"/api/v1/chat/sessions/{session_id}",
            headers={"Authorization": f"Bearer {fresh['token']}"},
        )
    ).status_code == 200
    async with db.get_db() as session:
        account = await session.get(DashboardAccount, target["account_id"])
        assert account.enabled and account.token_version == 2


@pytest.mark.asyncio
async def test_lifecycle_protects_administrators_self_and_other_accounts(ordinary_chat):
    client, server, core, db, admin, users = ordinary_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    client.cookies.clear()
    for method, suffix in [("POST", "/enable"), ("DELETE", "")]:
        for identity in [None, *users]:
            headers = (
                {"Authorization": f"Bearer {identity['token']}"} if identity else {}
            )
            result = await client.request(
                method,
                f"/api/v1/accounts/{users[0]['account_id']}{suffix}",
                headers=headers,
            )
            assert result.status_code == (403 if identity else 401)
        for target in [admin["account_id"], "missing-account"]:
            result = await client.request(
                method,
                f"/api/v1/accounts/{target}{suffix}",
                headers={"Authorization": f"Bearer {admin['token']}"},
            )
            assert result.status_code == 404
    async with db.get_db() as session:
        assert (
            len((await session.execute(select(DashboardAccount))).scalars().all()) == 3
        )


@pytest.mark.asyncio
async def test_delete_cleans_all_owned_chat_data_and_retains_foreign_resources(
    ordinary_chat, tmp_path, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    chat = server.asgi_app.state.services.chat
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setattr(
        account_cleanup, "get_astrbot_workspaces_path", lambda: str(root)
    )
    chat.attachments_dir = str(tmp_path / "attachments")
    chat.webchat_img_dir = str(tmp_path / "images")
    Path(chat.attachments_dir).mkdir()
    target = users[0]
    owner = target["resource_owner"]
    identities = [owner, users[1]["resource_owner"], admin["resource_owner"]]
    origins = []
    managed = []
    files = []
    async with db.get_db() as session:
        for index, creator in enumerate(identities):
            sid = f"lifecycle-{index}"
            umo = f"webchat:FriendMessage:webchat!{creator}!{sid}"
            origins.append(umo)
            workspace = root / normalize_umo_for_workspace(umo)
            workspace.mkdir()
            (workspace / "private.txt").write_text(f"workspace-{index}")
            managed.append(workspace)
            attachment = Path(chat.attachments_dir) / f"file-{index}.txt"
            attachment.write_text(f"attachment-{index}")
            files.append(attachment)
            for row in [
                PlatformSession(session_id=sid, creator=creator),
                WebChatThread(
                    thread_id=f"thread-{index}",
                    creator=creator,
                    parent_session_id=sid,
                    parent_message_id=0,
                    base_checkpoint_id="",
                    selected_text="private thread",
                ),
                ConversationV2(
                    platform_id="webchat",
                    user_id=umo,
                    content=[{"content": "private content"}],
                ),
                ConversationV2(
                    platform_id="webchat",
                    user_id=f"webchat:FriendMessage:webchat!{creator}!thread-{index}",
                    content=[],
                ),
                PlatformMessageHistory(
                    platform_id="webchat", user_id=sid, content={"text": "private main"}
                ),
                PlatformMessageHistory(
                    platform_id="webchat_thread",
                    user_id=f"thread-{index}",
                    content={"text": "private side"},
                ),
                Attachment(
                    owner=creator,
                    path=str(attachment),
                    type="file",
                    mime_type="text/plain",
                ),
                ChatUIProject(
                    project_id=f"project-{index}",
                    creator=creator,
                    title="private project",
                    workspace_type="project",
                ),
                SessionProjectRelation(session_id=sid, project_id=f"project-{index}"),
                Preference(
                    scope="webchat_session",
                    scope_id=sid,
                    key="model_selection",
                    value={"val": {"thinking_effort": "xhigh"}},
                ),
                Preference(
                    scope="dashboard_account",
                    scope_id=creator,
                    key="quick_messages",
                    value={"val": "private preference"},
                ),
                Preference(
                    scope="umo",
                    scope_id=umo,
                    key="private",
                    value={"val": "private setting"},
                ),
                ProviderStat(umo=umo, provider_id="provider"),
                UmoAlias(
                    umo=umo, creator_sender_id=creator, user_alias="private alias"
                ),
                CronJob(
                    name="Owned schedule",
                    job_type="active_agent",
                    payload={"session": umo},
                ),
            ]:
                session.add(row)
        external = tmp_path / "custom-host-directory"
        external.mkdir()
        (external / "host.txt").write_text("must survive")
        session.add(
            ChatUIProject(
                project_id="external",
                creator=owner,
                title="External",
                workspace_type="custom",
                workspace_path=str(external),
            )
        )
        # External attachment metadata can be removed; its host file is not managed.
        session.add(
            Attachment(
                owner=owner,
                path=str(external / "host.txt"),
                type="file",
                mime_type="text/plain",
            )
        )
        session.add(
            PlatformSession(session_id="qq-sentinel", platform_id="qq", creator=owner)
        )
        session.add(
            ConversationV2(
                platform_id="qq", user_id="qq:FriendMessage:sentinel", content=[]
            )
        )
        session.add(
            PlatformMessageHistory(
                platform_id="qq",
                user_id="qq-sentinel",
                sender_id=owner,
                content={"text": "QQ sentinel"},
            )
        )
        # Orphans have no live session row but still belong to the account.
        orphan_umo = f"webchat:FriendMessage:webchat!{owner}!orphan"
        session.add(
            ConversationV2(platform_id="webchat", user_id=orphan_umo, content=[])
        )
        session.add(
            PlatformMessageHistory(
                platform_id="webchat", user_id="orphan", content={"text": "orphan"}
            )
        )
        await session.commit()
    project_workspace = root / "project_project-0"
    project_workspace.mkdir()
    (project_workspace / "code.py").write_text("private project")
    orphan_workspace = root / normalize_umo_for_workspace(orphan_umo)
    orphan_workspace.mkdir()
    upload = chat.chunked_uploads.init_session(
        owner=owner,
        purpose="chat",
        filename="private",
        original_filename="private",
        total_size=1,
    )
    # Confine upload cleanup to the temporary test directory.
    old_dir = upload.chunk_dir
    old_dir.rmdir()
    chat.chunked_uploads.chunks_root = tmp_path / "chunks"
    upload.chunk_dir = chat.chunked_uploads.chunks_root / upload.id
    upload.chunk_dir.mkdir(parents=True)
    (upload.chunk_dir / "0").write_bytes(b"x")
    async with file_token_service.lock:
        file_token_service.staged_files["delete-token"] = (
            str(files[0]),
            9999999999,
            False,
        )
        file_token_service.account_owners["delete-token"] = owner
    await chat.umop_config_router.update_route(origins[0], "default")
    await sp.session_put(origins[0], "cached", "private")
    await goal_service.set_goal(origins[0], "Private ongoing goal")
    await goal_service.set_goal(origins[1], "Other user's ongoing goal")
    transfer = server.asgi_app.state.services.session_transfer
    transfer.temp_dir = tmp_path / "imports"
    transfer.temp_dir.mkdir()
    archive = transfer.temp_dir / "pending.zip"
    archive.write_bytes(b"private imported data")
    transfer.pending_imports["owned-import"] = SimpleNamespace(
        owner=owner, zip_path=archive
    )
    cancelled = asyncio.Event()

    async def running_work():
        try:
            await asyncio.Event().wait()
        finally:
            await sp.session_put(origins[0], "late-write", "must be removed")
            cancelled.set()

    task = asyncio.create_task(running_work())
    await asyncio.sleep(0)
    chat.chat_runs["owned-run"] = SimpleNamespace(
        username=owner, session_id="lifecycle-0", task=task
    )
    response = await client.delete(
        f"/api/v1/accounts/{target['account_id']}",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert response.status_code == 200, response.text
    assert cancelled.is_set() and task.done()
    assert not files[0].exists() and all(path.exists() for path in files[1:])
    assert not managed[0].exists() and all(path.exists() for path in managed[1:])
    assert not project_workspace.exists() and not orphan_workspace.exists()
    assert (external / "host.txt").read_text() == "must survive"
    assert not upload.chunk_dir.exists()
    assert not archive.exists() and "owned-import" not in transfer.pending_imports
    assert await goal_service.goals.get(origins[0]) is None
    assert await goal_service.goals.get(origins[1]) is not None
    assert "delete-token" not in file_token_service.staged_files
    assert origins[0] not in chat.umop_config_router.umop_to_conf_id
    assert await sp.session_get(origins[0], "late-write") is None
    async with db.get_db() as session:
        assert await session.get(DashboardAccount, target["account_id"]) is None
        for model in [PlatformSession, ConversationV2, PlatformMessageHistory]:
            rows = (await session.execute(select(model))).scalars().all()
            assert (
                len(rows)
                == {PlatformSession: 3, ConversationV2: 5, PlatformMessageHistory: 5}[
                    model
                ]
            )
        for model in [
            WebChatThread,
            Attachment,
            ChatUIProject,
            SessionProjectRelation,
            ProviderStat,
            UmoAlias,
            CronJob,
        ]:
            assert len((await session.execute(select(model))).scalars().all()) == 2
        preferences = (await session.execute(select(Preference))).scalars().all()
        assert not any(
            row.scope_id in [owner, origins[0], "lifecycle-0"] for row in preferences
        )
    assert (
        await client.get(
            "/api/v1/auth/session",
            headers={"Authorization": f"Bearer {target['token']}"},
        )
    ).status_code == 401
    recreated = await client.post(
        "/api/v1/accounts",
        headers={"Authorization": f"Bearer {admin['token']}"},
        json={"username": target["username"], "password": USER_PASSWORD},
    )
    assert recreated.status_code == 200
    assert recreated.json()["data"]["resource_owner"] != owner
    fresh = await login(client, target["username"], USER_PASSWORD)
    assert (
        await client.get(
            "/api/v1/chat/sessions",
            headers={"Authorization": f"Bearer {fresh['token']}"},
        )
    ).json()["data"] == []


@pytest.mark.asyncio
async def test_failed_cleanup_remains_disabled_and_retries_without_losing_metadata(
    ordinary_chat, tmp_path, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    owner = users[0]["resource_owner"]
    root = tmp_path / "workspaces"
    target = root / normalize_umo_for_workspace(
        f"webchat:FriendMessage:webchat!{owner}!retry"
    )
    target.mkdir(parents=True)
    (target / "private.txt").write_text("private data")
    monkeypatch.setattr(
        account_cleanup, "get_astrbot_workspaces_path", lambda: str(root)
    )
    async with db.get_db() as session:
        session.add(PlatformSession(session_id="retry", creator=owner))
        await session.commit()
    real_rmtree = account_cleanup.shutil.rmtree
    monkeypatch.setattr(
        account_cleanup.shutil,
        "rmtree",
        lambda path: (_ for _ in ()).throw(PermissionError("locked workspace")),
    )
    headers = {"Authorization": f"Bearer {admin['token']}"}
    path = f"/api/v1/accounts/{users[0]['account_id']}"
    failed = await client.delete(path, headers=headers)
    assert failed.status_code == 500 and "retry deletion" in failed.text
    async with db.get_db() as session:
        account = await session.get(DashboardAccount, users[0]["account_id"])
        assert account is not None and not account.enabled
        assert (
            await session.execute(
                select(PlatformSession).where(PlatformSession.session_id == "retry")
            )
        ).scalar_one() is not None
    assert target.exists()
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": users[0]["username"], "password": USER_PASSWORD},
        )
    ).status_code == 401
    monkeypatch.setattr(account_cleanup.shutil, "rmtree", real_rmtree)
    assert (await client.delete(path, headers=headers)).status_code == 200
    assert not target.exists()


@pytest.mark.asyncio
async def test_delete_drains_inflight_mutation_and_blocks_enable_during_cleanup(
    ordinary_chat, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    owner = users[0]["resource_owner"]
    started = asyncio.Event()
    settled = asyncio.Event()

    async def admitted_mutation(username, platform_id):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            async with db.get_db() as session:
                session.add(PlatformSession(session_id="late-admitted", creator=owner))
                await session.commit()
            settled.set()

    monkeypatch.setattr(
        server.asgi_app.state.services.chat, "new_session", admitted_mutation
    )
    request = asyncio.create_task(
        client.get(
            "/api/v1/chat/sessions/new",
            headers={"Authorization": f"Bearer {users[0]['token']}"},
        )
    )
    await asyncio.wait_for(started.wait(), 5)
    cleanup_started = asyncio.Event()
    cleanup_continue = asyncio.Event()
    from astrbot.dashboard.api import accounts

    real_cleanup = accounts.delete_account_data

    async def paused_cleanup(app, account):
        cleanup_started.set()
        await cleanup_continue.wait()
        await real_cleanup(app, account)

    monkeypatch.setattr(accounts, "delete_account_data", paused_cleanup)
    path = f"/api/v1/accounts/{users[0]['account_id']}"
    headers = {"Authorization": f"Bearer {admin['token']}"}
    deletion = asyncio.create_task(client.delete(path, headers=headers))
    await asyncio.wait_for(cleanup_started.wait(), 5)
    assert (await client.post(path + "/enable", headers=headers)).status_code == 409
    assert (await client.delete(path, headers=headers)).status_code == 409
    cleanup_continue.set()
    response = await asyncio.wait_for(deletion, 15)
    assert response.status_code == 200, response.text
    await asyncio.gather(request, return_exceptions=True)
    assert settled.is_set()
    async with db.get_db() as session:
        assert (
            not (
                await session.execute(
                    select(PlatformSession).where(PlatformSession.creator == owner)
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["disable", "delete"])
async def test_queued_turn_cannot_start_after_account_revocation(
    ordinary_chat, operation
):
    client, server, core, db, admin, users = ordinary_chat
    user = users[0]
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    event = adapter.create_event(
        await adapter.convert_message(
            (
                user["resource_owner"],
                "queued",
                {"message": "queued question", "message_id": "queued-message"},
            )
        )
    )
    event.set_extra(
        "_dashboard_account",
        DashboardPrincipal(
            account_id=user["account_id"],
            resource_owner=user["resource_owner"],
            role="user",
            login_name=user["username"],
        ),
    )
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    headers = {"Authorization": f"Bearer {admin['token']}"}
    path = f"/api/v1/accounts/{user['account_id']}"
    response = await (
        client.delete(path, headers=headers)
        if operation == "delete"
        else client.post(path + "/disable", headers=headers)
    )
    assert response.status_code == 200
    scheduler = PipelineScheduler(
        PipelineContext(core.astrbot_config, core.plugin_manager, "default", db)
    )
    executed = []

    async def stage_run(_event):
        executed.append(_event)

    scheduler._process_stages = stage_run
    await scheduler.execute(event)
    assert event.is_stopped() and not executed


@pytest.mark.asyncio
async def test_delete_closes_owned_stream_while_other_account_stream_stays_open(
    ordinary_chat,
):
    client, server, core, db, admin, users = ordinary_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    sessions = [
        (
            await client.get(
                "/api/v1/chat/sessions/new",
                headers={"Authorization": f"Bearer {user['token']}"},
            )
        ).json()["data"]["session_id"]
        for user in users
    ]
    owned = await ASGIStream(
        server.asgi_app,
        f"/api/v1/chat/sessions/{sessions[0]}/system-stream",
        users[0]["token"],
    ).__aenter__()
    async with ASGIStream(
        server.asgi_app,
        f"/api/v1/chat/sessions/{sessions[1]}/system-stream",
        users[1]["token"],
    ) as foreign:
        response = await client.delete(
            f"/api/v1/accounts/{users[0]['account_id']}",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
        assert response.status_code == 200, response.text
        await asyncio.wait_for(owned.task, 5)
        assert not foreign.task.done()
        assert users[0]["account_id"] not in server.asgi_app.state.account_requests


@pytest.mark.asyncio
async def test_delete_stops_only_owned_managed_shell_and_removes_output(
    ordinary_chat, tmp_path, monkeypatch
):
    from astrbot.core.computer import computer_client
    from astrbot.core.computer.booters import local

    client, server, core, db, admin, users = ordinary_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    shell = local.LocalShellComponent()
    monkeypatch.setattr(computer_client, "local_booter", SimpleNamespace(shell=shell))
    monkeypatch.setattr(local, "get_astrbot_system_tmp_path", lambda: str(tmp_path))
    script = tmp_path / "sleep.py"
    script.write_text("import time\ntime.sleep(60)\n")
    args = [sys.executable, str(script)]
    command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
    spec = (
        local.ShellSpec("cmd", os.environ.get("COMSPEC", "cmd.exe"), ("/c",))
        if os.name == "nt"
        else None
    )
    started = []
    try:
        for index, user in enumerate(users):
            await shell.exec_managed(
                command,
                owner_id=f"webchat:FriendMessage:webchat!{user['resource_owner']}!shell-{index}",
                creator_id=user["resource_owner"],
                creator_is_admin=False,
                sandboxed=False,
                cwd=str(tmp_path),
                yield_time_ms=0,
                shell_spec=spec,
            )
            started.append(list(shell._sessions.values())[-1])
        response = await client.delete(
            f"/api/v1/accounts/{users[0]['account_id']}",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
        assert response.status_code == 200, response.text
        assert started[0].process.returncode is not None
        assert not started[0].output_path.exists()
        assert started[0].session_id not in shell._sessions
        assert started[1].process.returncode is None
        assert started[1].output_path.exists()
    finally:
        await shell.shutdown_sessions()


@pytest.mark.asyncio
async def test_delete_closes_owned_websocket_and_stops_detached_voice_work(
    ordinary_chat,
):
    client, server, core, db, admin, users = ordinary_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    live = server.asgi_app.state.services.live_chat
    socket = await ASGIWebSocket(
        server.asgi_app, "/api/v1/unified-chat/ws", users[0]["token"]
    ).__aenter__()
    # Wait for authentication and registration after the accept handshake.
    deadline = asyncio.get_running_loop().time() + 5
    while not live.sessions:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.01)
    voice = next(iter(live.sessions.values()))
    settled = asyncio.Event()

    async def voice_work():
        try:
            await asyncio.Event().wait()
        finally:
            async with db.get_db() as session:
                session.add(
                    PlatformSession(
                        session_id="late-voice", creator=users[0]["resource_owner"]
                    )
                )
                await session.commit()
            settled.set()

    voice.live_request_task = asyncio.create_task(voice_work())
    await asyncio.sleep(0)
    response = await client.delete(
        f"/api/v1/accounts/{users[0]['account_id']}",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert response.status_code == 200, response.text
    await asyncio.wait_for(socket.task, 5)
    assert any(
        frame["type"] == "websocket.close" and frame["code"] == 1008
        for frame in socket.frames
    )
    assert settled.is_set() and not live.sessions
    async with db.get_db() as session:
        assert (
            not (
                await session.execute(
                    select(PlatformSession).where(
                        PlatformSession.creator == users[0]["resource_owner"]
                    )
                )
            )
            .scalars()
            .all()
        )
