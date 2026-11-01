"""Tool results consumed through production account HTTP and SQLite services."""

import asyncio
import hashlib
import importlib
import inspect
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from astrbot.core.computer import computer_client
from astrbot.core.computer.booters.local import LocalShellComponent
from astrbot.core.db.po import PlatformMessageHistory
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.skills.skill_guide import guide_state
from astrbot.core.tools.computer_tools import fs, recycle_restore
from astrbot.core.tools.computer_tools.edit_history import EditHistoryManager
from astrbot.core.utils.webchat_access import dashboard_account_context
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat
from tests.test_multi_user_projects import send_reply
from tests.test_multi_user_runtime import ASGIStream
from tests.test_recycle_restore import _make_i_bytes_v4


@pytest.mark.asyncio
@pytest.mark.parametrize("side_chat", [False, True])
async def test_thread_file_results_are_owned_live_and_after_reopening(
    ordinary_chat, monkeypatch, tmp_path, side_chat
):
    """Consume real thread output through HTTP, persistence and parent checks."""
    client, server, core, db, admin, users = ordinary_chat
    account = users[0]
    headers = {"Authorization": f"Bearer {account['token']}"}
    service = server.asgi_app.state.services.chat
    service.conv_mgr = core.conversation_manager
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    await send_reply(client, core, account, sid)
    parent_history = (
        await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
    ).json()["data"]["history"]
    parent_message = next(
        row["id"] for row in parent_history if row["content"]["type"] == "bot"
    )
    body = {"session_id": sid, "side_chat": side_chat}
    if not side_chat:
        body.update(parent_message_id=parent_message, selected_text="private result")
    created = await client.post("/api/v1/chat/threads", headers=headers, json=body)
    assert created.json()["status"] == "ok", created.text
    tid = created.json()["data"]["thread_id"]
    history = EditHistoryManager(tmp_path / "thread-backups")
    monkeypatch.setattr(
        "astrbot.dashboard.api.chat.get_history_manager", lambda: history
    )
    path = tmp_path / "thread-result.txt"
    path.write_bytes(b"original thread file")
    backup = history.save_backup(str(path), path.read_bytes(), runtime="local")
    path.write_bytes(b"private thread tool result")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    files = [
        {
            "path": str(path),
            "backup_id": backup.id,
            "sha256": checksum,
            "runtime": "local",
            "kind": "edit",
            "diff_available": True,
        }
    ]
    release = asyncio.Event()
    adapter = WebChatAdapter({}, {}, asyncio.Queue())

    async def generate():
        sender, target, payload = await webchat_queue_mgr.get_or_create_queue(tid).get()
        assert target == tid
        event = adapter.create_event(
            await adapter.convert_message((sender, target, payload))
        )
        context = PipelineContext(
            core.astrbot_config, core.plugin_manager, "default", db
        )
        waking = WakingCheckStage()
        await waking.initialize(context)

        async def tools(current):
            assert (
                dashboard_account_context.get().resource_owner
                == account["resource_owner"]
            )
            assert not current.is_admin()
            back = webchat_queue_mgr.get_or_create_back_queue(
                payload["message_id"], tid
            )
            await back.put(
                {
                    "type": "plain",
                    "chain_type": "file_changes",
                    "data": json.dumps({"files": files}),
                    "streaming": True,
                }
            )
            await release.wait()
            await back.put({"type": "end"})

        pipeline = PipelineScheduler(context)
        pipeline.stages = [waking, SimpleNamespace(process=tools)]
        await pipeline.execute(event)

    worker = asyncio.create_task(generate())
    try:
        async with ASGIStream(
            server.asgi_app,
            f"/api/v1/chat/threads/{tid}/messages",
            account["token"],
            {"message": "Edit my thread file"},
        ) as stream:
            bound = await stream.event("session_id")
            run_id = bound["run_id"]
            await stream.event("file_changes")
            source = {
                "session_id": tid,
                "run_id": run_id,
                "path": str(path),
                "backup_id": backup.id,
                "expect_sha256": checksum,
            }
            own = await client.post(
                "/api/v1/chat/file-changes/diff", headers=headers, json=source
            )
            assert "private thread tool result" in own.json()["data"]["diff"], own.text
            release.set()
            await stream.event("end")
        await asyncio.wait_for(worker, 10)
        reopened = SQLiteDatabase(str(tmp_path / "accounts.db"))
        await reopened.initialize()
        service.db = reopened
        try:
            saved = (
                await client.get(f"/api/v1/chat/threads/{tid}", headers=headers)
            ).json()["data"]["history"]
            bot = next(row for row in saved if row["content"].get("file_changes"))
            record = await reopened.get_platform_message_history_by_id(bot["id"])
            assert record.platform_id == "webchat_thread"
            source = {**source, "run_id": None, "message_id": bot["id"]}
            own = await client.post(
                "/api/v1/chat/file-changes/diff", headers=headers, json=source
            )
            assert "private thread tool result" in own.json()["data"]["diff"], own.text
            for other in [admin, users[1]]:
                denied = await client.post(
                    "/api/v1/chat/file-changes/diff",
                    headers={"Authorization": f"Bearer {other['token']}"},
                    json=source,
                )
                assert denied.status_code == 403
            for platform, imported in [("webchat", False), ("webchat_thread", True)]:
                forbidden = PlatformMessageHistory(
                    platform_id=platform,
                    user_id=tid,
                    content={**record.content, "_imported": imported},
                )
                async with reopened.get_db() as connection:
                    connection.add(forbidden)
                    await connection.commit()
                    await connection.refresh(forbidden)
                denied = await client.post(
                    "/api/v1/chat/file-changes/diff",
                    headers=headers,
                    json={**source, "message_id": forbidden.id},
                )
                assert denied.status_code == 403
            restored = await client.post(
                "/api/v1/chat/file-changes/restore", headers=headers, json=source
            )
            assert restored.json()["status"] == "ok", restored.text
            assert path.read_bytes() == b"original thread file"
            await reopened.delete_platform_session(sid)
            denied = await client.post(
                "/api/v1/chat/file-changes/status",
                headers=headers,
                json={
                    "session_id": tid,
                    "message_id": bot["id"],
                    "files": [{"path": str(path), "backup_id": backup.id}],
                },
            )
            assert denied.status_code == 403
        finally:
            service.db = db
            await reopened.engine.dispose()
    finally:
        release.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_actual_spcode_consumers_use_the_persisted_owned_workspace(
    ordinary_chat, monkeypatch, tmp_path
):
    """Exercise actual plugin handlers with the production workspace service."""
    root = os.environ.get("ASTRBOT_TEST_SPCODE_ROOT")
    if not root:
        pytest.skip("Set ASTRBOT_TEST_SPCODE_ROOT to the audited plugin checkout")
    package = types.ModuleType("t09_workspace_spcode")
    package.__path__ = [root]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    main = importlib.import_module(f"{package.__name__}.main")
    webapi = importlib.import_module(f"{package.__name__}.tools.webapi")
    project_module = importlib.import_module(
        f"{package.__name__}.tools.project.manager"
    )
    state = importlib.import_module(f"{package.__name__}.tools.project.state")
    terminal = importlib.import_module(f"{package.__name__}.tools.terminal.component")
    runtime = importlib.import_module(f"{package.__name__}.tools.terminal.runtime")
    export = importlib.import_module(f"{package.__name__}.tools.webapi.git_file_export")
    client, server, core, db, admin, users = ordinary_chat
    registered = []
    monkeypatch.setattr(
        core.star_context,
        "register_web_api",
        lambda route, view_handler, methods, desc: registered.append(
            (route, view_handler, methods, desc)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        core.star_context,
        "get_config",
        lambda umo: core.astrbot_config_mgr.get_conf(umo),
        raising=False,
    )
    monkeypatch.setattr(core.star_context, "registered_web_apis", registered)
    monkeypatch.setattr(export, "EXPORT_ROOT_OVERRIDE", tmp_path / "exports")
    plugin = main.SPCodeToolkit.__new__(main.SPCodeToolkit)
    plugin.context = core.star_context
    plugin._config = dict(main._DEFAULT_CONFIG)
    plugin._webapi_active = True
    plugin.project = project_module.ProjectManager(plugin)
    plugin.agentsmd = main.AgentsmdSubsystem(
        plugin=plugin, is_path_safe=main._is_path_safe
    )
    webapi.register_webapi_routes(plugin)
    manager = terminal.TerminalSessionManager()
    monkeypatch.setattr(runtime, "component", manager)
    identities = []
    try:
        for index, account in enumerate([admin, *users]):
            headers = {"Authorization": f"Bearer {account['token']}"}
            sid = (
                await client.get("/api/v1/chat/sessions/new", headers=headers)
            ).json()["data"]["session_id"]
            origin = f"webchat:FriendMessage:webchat!{account['resource_owner']}!{sid}"
            project = (
                await client.post(
                    "/api/v1/chat/projects",
                    headers=headers,
                    json={
                        "title": f"Owned project {index}",
                        "workspace_type": "project",
                        "spcode_no_agentsmd": True,
                        "spcode_no_codegraph": True,
                    },
                )
            ).json()["data"]
            added = await client.post(
                f"/api/v1/chat/projects/{project['project_id']}/sessions/{sid}",
                headers=headers,
            )
            assert added.json()["status"] == "ok", added.text
            (
                _,
                workspace,
            ) = await server.asgi_app.state.services.chat_projects.require_chat_workspace(
                account["resource_owner"], origin
            )
            workspace.mkdir(parents=True, exist_ok=True)
            path = workspace / "result.txt"
            path.write_text(f"private workspace {index}")
            await asyncio.to_thread(
                subprocess.run,
                ["git", "init", "-q", str(workspace)],
                check=True,
                capture_output=True,
            )
            await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", str(workspace), "add", "result.txt"],
                check=True,
                capture_output=True,
            )
            await asyncio.to_thread(
                subprocess.run,
                [
                    "git",
                    "-C",
                    str(workspace),
                    "-c",
                    "user.name=T09",
                    "-c",
                    "user.email=t09@example.invalid",
                    "commit",
                    "-qm",
                    "test result",
                ],
                check=True,
                capture_output=True,
            )
            base = "/api/v1/plugins/extensions/spcode"
            # Omit substep flags: the host forwards the stored safe values.
            loaded = await client.post(
                f"{base}/project-load",
                headers=headers,
                json={"umo": origin, "directory": str(workspace)},
            )
            assert loaded.json()["data"]["success"], loaded.text
            assert set(plugin.get_loaded_project(origin)["skipped_substeps"]) == {
                "agentsmd",
                "codegraph",
            }
            for route_base in [base, "/api/plug/spcode"]:
                own = await client.get(
                    f"{route_base}/file-browser",
                    headers=headers,
                    params={"umo": origin, "path": str(path)},
                )
                assert f"private workspace {index}" in own.text, own.text
                cached = await client.get(
                    f"{route_base}/file-browser",
                    headers={**headers, "If-None-Match": own.headers["etag"]},
                    params={"umo": origin, "path": str(path)},
                )
                assert cached.status_code == 304 and cached.content == b""
                status = await client.get(
                    f"{route_base}/project-status",
                    headers=headers,
                    params={"umo": origin},
                )
                assert status.json()["data"]["loaded"] is True, status.text
                assert "all_loaded_count" not in status.text
                forged = await client.post(
                    f"{route_base}/project-load",
                    headers=headers,
                    json={
                        "umo": origin,
                        "directory": str(workspace),
                        "no_codegraph": False,
                        "force": True,
                    },
                )
                assert forged.status_code == 403
                outside = await client.get(
                    f"{route_base}/file-browser",
                    headers=headers,
                    params={"umo": origin, "path": str(tmp_path)},
                )
                assert outside.status_code == 403
            exported = await client.post(
                f"{base}/git-file-export",
                headers=headers,
                json={"umo": origin, "path": "result.txt", "ref": "HEAD"},
            )
            assert exported.json()["data"]["success"], exported.text
            attachment = exported.json()["data"]["attachment_id"]
            own = await client.get(
                f"/api/v1/files/{attachment}/content", headers=headers
            )
            assert own.content == path.read_bytes(), own.text
            identities.append((account, sid, origin, workspace, attachment))
        for account, sid, origin, workspace, attachment in identities:
            headers = {"Authorization": f"Bearer {account['token']}"}
            for other, _, other_origin, other_workspace, _ in identities:
                if account is other:
                    continue
                for attempted_origin in [origin, other_origin]:
                    denied = await client.get(
                        "/api/v1/plugins/extensions/spcode/file-browser",
                        headers=headers,
                        params={
                            "umo": attempted_origin,
                            "path": str(other_workspace / "result.txt"),
                        },
                    )
                    assert denied.status_code == 403
                denied = await client.get(
                    f"/api/v1/files/{attachment}/content",
                    headers={"Authorization": f"Bearer {other['token']}"},
                )
                assert denied.status_code >= 400 or denied.json()["status"] == "error"
            # A stale or forged plugin runtime directory cannot replace the DB binding.
            info = state.get(origin)
            state.put(origin, {**info, "directory": str(tmp_path)})
            denied = await client.get(
                "/api/v1/plugins/extensions/spcode/file-browser",
                headers=headers,
                params={"umo": origin, "path": "result.txt"},
            )
            assert denied.status_code == 403
            state.put(origin, info)
        if os.name == "nt":
            account, sid, origin, workspace, _ = identities[1]
            headers = {"Authorization": f"Bearer {account['token']}"}
            base = "/api/v1/plugins/extensions/spcode/terminal"
            denied = await client.post(
                f"{base}/start",
                headers=headers,
                json={"umo": origin, "cwd": str(tmp_path), "shell": "cmd"},
            )
            assert denied.status_code == 403
            started = await client.post(
                f"{base}/start",
                headers=headers,
                json={"umo": origin, "cwd": str(workspace), "shell": "cmd"},
            )
            task_id = started.json()["data"]["session_id"]
            await client.post(
                f"{base}/input",
                headers=headers,
                json={
                    "umo": origin,
                    "session_id": task_id,
                    "chars": "echo PRIVATE_TERMINAL_RESULT\r\n",
                },
            )
            async with ASGIStream(
                server.asgi_app,
                f"{base}/stream?umo={quote(origin)}&session_id={task_id}",
                account["token"],
            ) as output:
                content = ""
                while "PRIVATE_TERMINAL_RESULT" not in content:
                    content += (await output.event("output"))["data"]
            for other, _, other_origin, _, _ in [identities[0], identities[2]]:
                foreign_headers = {"Authorization": f"Bearer {other['token']}"}
                denied = await client.post(
                    f"{base}/stop",
                    headers=foreign_headers,
                    json={"umo": origin, "session_id": task_id},
                )
                assert denied.status_code == 403
                mismatch = await client.post(
                    f"{base}/stop",
                    headers=foreign_headers,
                    json={"umo": other_origin, "session_id": task_id},
                )
                assert mismatch.json()["data"].get("error"), mismatch.text
                assert (await manager.owner_session(origin))["status"] == "running"
            ended = await client.post(
                f"{base}/stop",
                headers=headers,
                json={"umo": origin, "session_id": task_id},
            )
            assert not ended.json()["data"].get("error"), ended.text
        # A persisted project move rejects old files but can repair runtime state.
        account, sid, origin, old_workspace, _ = identities[1]
        headers = {"Authorization": f"Bearer {account['token']}"}
        base = "/api/v1/plugins/extensions/spcode"
        project_b = (
            await client.post(
                "/api/v1/chat/projects",
                headers=headers,
                json={
                    "title": "Moved project",
                    "workspace_type": "project",
                    "spcode_no_agentsmd": True,
                    "spcode_no_codegraph": True,
                },
            )
        ).json()["data"]
        moved = await client.post(
            f"/api/v1/chat/projects/{project_b['project_id']}/sessions/{sid}",
            headers=headers,
        )
        assert moved.json()["status"] == "ok", moved.text
        (
            _,
            workspace_b,
        ) = await server.asgi_app.state.services.chat_projects.require_chat_workspace(
            account["resource_owner"], origin
        )
        workspace_b.mkdir(parents=True, exist_ok=True)
        assert workspace_b != old_workspace
        denied = await client.get(
            f"{base}/file-browser",
            headers=headers,
            params={"umo": origin, "path": str(old_workspace / "result.txt")},
        )
        assert denied.status_code == 403
        stale_load = await client.post(
            f"{base}/project-load",
            headers=headers,
            json={"umo": origin, "directory": str(old_workspace), "force": True},
        )
        assert stale_load.status_code == 403
        loaded = await client.post(
            f"{base}/project-load",
            headers=headers,
            json={"umo": origin, "directory": str(workspace_b), "force": True},
        )
        assert loaded.json()["data"]["success"], loaded.text
        assert plugin.get_loaded_project(origin)["directory"] == str(workspace_b)
        # Editing the bound project's workspace has the same recovery path.
        edited = await client.patch(
            f"/api/v1/chat/projects/{project_b['project_id']}",
            headers=headers,
            json={"workspace_type": "session"},
        )
        assert edited.json()["status"] == "ok", edited.text
        (
            _,
            session_workspace,
        ) = await server.asgi_app.state.services.chat_projects.require_chat_workspace(
            account["resource_owner"], origin
        )
        session_workspace.mkdir(parents=True, exist_ok=True)
        assert session_workspace != workspace_b
        denied = await client.get(
            f"{base}/file-browser",
            headers=headers,
            params={"umo": origin, "path": str(workspace_b)},
        )
        assert denied.status_code == 403
        unloaded = await client.post(
            f"{base}/project-unload", headers=headers, json={"umo": origin}
        )
        assert unloaded.json()["data"]["success"], unloaded.text
        assert plugin.get_loaded_project(origin) is None
        loaded = await client.post(
            f"{base}/project-load",
            headers=headers,
            json={"umo": origin, "directory": str(session_workspace)},
        )
        assert loaded.json()["data"]["success"], loaded.text
        assert plugin.get_loaded_project(origin)["directory"] == str(session_workspace)
    finally:
        await manager.shutdown_sessions()
        state.reset()


@pytest.mark.asyncio
async def test_generated_file_changes_are_owned_persistent_and_not_importable(
    ordinary_chat, tmp_path, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    history = EditHistoryManager(tmp_path / "backups")
    monkeypatch.setattr(
        "astrbot.dashboard.api.chat.get_history_manager", lambda: history
    )
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    results = []
    core.astrbot_config["admins_id"] = [u["resource_owner"] for u in users]
    for index, account in enumerate([admin, *users]):
        headers = {"Authorization": f"Bearer {account['token']}"}
        sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
            "data"
        ]["session_id"]
        target = tmp_path / f"private-{index}.txt"
        baseline = f"baseline-{index}\n".encode()
        current = f"private-result-{index}\n".encode()
        target.write_bytes(baseline)
        backup = history.save_backup(str(target), baseline, runtime="local")
        target.write_bytes(current)
        checksum = hashlib.sha256(current).hexdigest()

        async def generate():
            sender, target_id, payload = await webchat_queue_mgr.get_or_create_queue(
                sid
            ).get()
            event = adapter.create_event(
                await adapter.convert_message((sender, target_id, payload))
            )
            waking = WakingCheckStage()
            await waking.initialize(
                PipelineContext(core.astrbot_config, core.plugin_manager, "default", db)
            )

            async def tools(current_event):
                assert (
                    dashboard_account_context.get().resource_owner
                    == account["resource_owner"]
                )
                if account["role"] == "user":
                    assert not current_event.is_admin()
                back = webchat_queue_mgr.get_or_create_back_queue(
                    payload["message_id"], target_id
                )
                await back.put(
                    {
                        "type": "plain",
                        "chain_type": "file_changes",
                        "data": json.dumps(
                            {
                                "files": [
                                    {
                                        "path": str(target),
                                        "backup_id": backup.id,
                                        "sha256": checksum,
                                        "runtime": "local",
                                        "kind": "edit",
                                        "diff_available": True,
                                    }
                                ]
                            }
                        ),
                        "streaming": True,
                    }
                )
                await back.put(
                    {"type": "plain", "data": "Tool result ready", "streaming": True}
                )

            pipeline = PipelineScheduler(
                PipelineContext(core.astrbot_config, core.plugin_manager, "default", db)
            )
            pipeline.stages = [waking, SimpleNamespace(process=tools)]
            await pipeline.execute(event)

        worker = asyncio.create_task(generate())
        sent = await asyncio.wait_for(
            client.post(
                "/api/v1/chat",
                headers=headers,
                json={"session_id": sid, "message": "Use the configured tools"},
            ),
            15,
        )
        await worker
        assert '"type": "file_changes"' in sent.text
        records = await db.get_platform_message_history("webchat", sid)
        record = next(r for r in records if r.content.get("file_changes"))
        source = {"session_id": sid, "message_id": record.id}
        payload = {
            **source,
            "path": str(target),
            "backup_id": backup.id,
            "expect_sha256": checksum,
        }
        diff = await client.post(
            "/api/v1/chat/file-changes/diff", headers=headers, json=payload
        )
        assert diff.status_code == 200, diff.text
        assert f"private-result-{index}" in diff.json()["data"]["diff"]
        results.append((account, source, target, payload, record))

    for account, source, target, payload, record in results:
        own = {"Authorization": f"Bearer {account['token']}"}
        before = target.read_bytes()
        for other in [admin, *users]:
            if other is account:
                continue
            foreign = {"Authorization": f"Bearer {other['token']}"}
            for operation, body in [
                ("diff", payload),
                ("restore", payload),
                ("restore-removed", {**source, "path": str(target)}),
                (
                    "status",
                    {
                        **source,
                        "files": [
                            {"path": str(target), "backup_id": payload["backup_id"]}
                        ],
                    },
                ),
            ]:
                denied = await client.post(
                    f"/api/v1/chat/file-changes/{operation}", headers=foreign, json=body
                )
                assert denied.status_code == 403, denied.text
                assert before not in denied.content
                assert target.read_bytes() == before
        for forged in [
            {**payload, "path": str(tmp_path / "accounts.db")},
            {**payload, "backup_id": "foreign-backup"},
            {**payload, "expect_sha256": ""},
            {
                **payload,
                "message_id": results[
                    (results.index((account, source, target, payload, record)) + 1) % 3
                ][4].id,
            },
        ]:
            denied = await client.post(
                "/api/v1/chat/file-changes/diff", headers=own, json=forged
            )
            assert denied.status_code == 403, denied.text
        # The actual import boundary stamps this flag after parsing external content.
        imported = PlatformMessageHistory(
            platform_id="webchat",
            user_id=source["session_id"],
            content={**record.content, "_imported": True},
        )
        async with db.get_db() as connection:
            connection.add(imported)
            await connection.commit()
            await connection.refresh(imported)
        denied = await client.post(
            "/api/v1/chat/file-changes/restore",
            headers=own,
            json={**payload, "message_id": imported.id},
        )
        assert denied.status_code == 403
        assert target.read_bytes() == before
        # Legacy results without a content checksum cannot authorize an overwrite.
        legacy_content = json.loads(json.dumps(record.content))
        for item in legacy_content["file_changes"]["files"]:
            item["sha256"] = ""
        legacy = PlatformMessageHistory(
            platform_id="webchat", user_id=source["session_id"], content=legacy_content
        )
        async with db.get_db() as connection:
            connection.add(legacy)
            await connection.commit()
            await connection.refresh(legacy)
        refused = await client.post(
            "/api/v1/chat/file-changes/restore",
            headers=own,
            json={**payload, "message_id": legacy.id, "expect_sha256": ""},
        )
        assert refused.json()["status"] == "error", refused.text
        assert target.read_bytes() == before
        restored = await client.post(
            "/api/v1/chat/file-changes/restore", headers=own, json=payload
        )
        assert restored.json()["status"] == "ok", restored.text
        assert target.read_bytes().startswith(b"baseline-")

    reopened = SQLiteDatabase(str(tmp_path / "accounts.db"))
    await reopened.initialize()
    original_db = server.asgi_app.state.services.chat.db
    server.asgi_app.state.services.chat.db = reopened
    try:
        account, source, target, payload, record = results[0]
        status = await client.post(
            "/api/v1/chat/file-changes/status",
            headers={"Authorization": f"Bearer {account['token']}"},
            json={
                **source,
                "files": [{"path": str(target), "backup_id": payload["backup_id"]}],
            },
        )
        assert status.json()["data"]["files"][0]["reverted"] is True
    finally:
        server.asgi_app.state.services.chat.db = original_db
        await reopened.engine.dispose()


@pytest.mark.asyncio
async def test_skill_consumption_uses_owned_origin_and_never_skill_management(
    ordinary_chat, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    skill = SimpleNamespace(
        name="controlled",
        description="Configured skill",
        path="/skills/controlled/SKILL.md",
        source_type="local",
    )
    monkeypatch.setattr(
        core.star_context,
        "get_config",
        lambda **kwargs: core.astrbot_config,
        raising=False,
    )

    async def active(context, umo):
        return [skill], None

    async def inventory(context, umo):
        return [skill]

    monkeypatch.setattr("astrbot.dashboard.api.skills.resolve_active_skills", active)
    monkeypatch.setattr("astrbot.dashboard.api.skills.resolve_all_skills", inventory)
    guide_state._pending.clear()
    try:
        for account in [admin, *users]:
            headers = {"Authorization": f"Bearer {account['token']}"}
            sid = (
                await client.get("/api/v1/chat/sessions/new", headers=headers)
            ).json()["data"]["session_id"]
            umo = f"webchat:FriendMessage:webchat!{account['resource_owner']}!{sid}"
            for route in ["active", "inventory"]:
                response = await client.get(
                    f"/api/v1/skill-guide/{route}", headers=headers, params={"umo": umo}
                )
                assert response.json()["data"]["skills"][0]["name"] == "controlled"
            queued = await client.post(
                "/api/v1/skill-guide/load",
                headers=headers,
                json={"umo": umo, "skill_name": "controlled"},
            )
            assert queued.json()["data"]["queued"]
            for other in [admin, *users]:
                if other is account:
                    continue
                other_headers = {"Authorization": f"Bearer {other['token']}"}
                for route, body in [
                    ("load", {"umo": umo, "skill_name": "controlled"}),
                    ("clear", {"umo": umo}),
                ]:
                    denied = await client.post(
                        f"/api/v1/skill-guide/{route}", headers=other_headers, json=body
                    )
                    assert denied.status_code == 403
                denied = await client.get(
                    "/api/v1/skill-guide/active",
                    headers=other_headers,
                    params={"umo": umo},
                )
                assert denied.status_code == 403
            assert guide_state.drain(umo) == ["controlled"]
            if account["role"] == "user":
                for route in ["/api/v1/skills", "/api/skills/list", "/api/v1/plugins"]:
                    denied = await client.get(route, headers=headers)
                    assert denied.status_code == 403
                denied = await client.post(
                    "/api/v1/skill-guide/load",
                    headers=headers,
                    json={"umo": umo, "skill_name": "controlled", "role": "admin"},
                )
                assert denied.status_code == 400
    finally:
        guide_state._pending.clear()


@pytest.mark.asyncio
async def test_managed_terminal_output_and_controls_require_both_owners(
    ordinary_chat, tmp_path, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    shell = LocalShellComponent()
    monkeypatch.setattr(computer_client, "local_booter", SimpleNamespace(shell=shell))
    monkeypatch.setattr(
        "astrbot.core.computer.booters.local.get_astrbot_system_tmp_path",
        lambda: str(tmp_path),
    )
    sessions = []
    for account in [admin, *users]:
        headers = {"Authorization": f"Bearer {account['token']}"}
        sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
            "data"
        ]["session_id"]
        sessions.append((account, sid))
    try:
        for account, sid in sessions:
            origin = f"webchat:FriendMessage:webchat!{account['resource_owner']}!{sid}"
            command = f'"{Path(sys.executable).as_posix()}" -u -c "import time; print(123456, flush=True); time.sleep(45)"'
            result = await shell.exec_managed(
                command,
                owner_id=origin,
                creator_id=account["resource_owner"],
                creator_is_admin=account["role"] == "admin",
                sandboxed=False,
                yield_time_ms=100,
                timeout=60,
            )
            task_id = result["session_id"]
            headers = {"Authorization": f"Bearer {account['token']}"}
            base = f"/api/v1/chat/sessions/{sid}/shell-sessions"
            own = await client.get(base, headers=headers)
            assert own.json()["data"]["sessions"][0]["session_id"] == task_id
            output = await client.get(
                f"{base}/{task_id}/output",
                headers=headers,
                params={"yield_time_ms": 3000},
            )
            assert "123456" in output.json()["data"]["stdout"], output.text
            for other, other_sid in sessions:
                if other is account:
                    continue
                foreign_headers = {"Authorization": f"Bearer {other['token']}"}
                for attempted_sid in [sid, other_sid]:
                    attempted = f"/api/v1/chat/sessions/{attempted_sid}/shell-sessions/{task_id}"
                    for action in ["output", "terminate", "discard"]:
                        denied = await client.request(
                            "GET" if action == "output" else "POST",
                            f"{attempted}/{action}",
                            headers=foreign_headers,
                        )
                        assert (
                            denied.status_code >= 400
                            or denied.json()["status"] == "error"
                        )
                        assert "123456" not in denied.text
                state = await shell.peek_session_output(
                    owner_id=origin,
                    requester_id=account["resource_owner"],
                    requester_is_admin=True,
                    session_id=task_id,
                )
                assert state["status"] == "running"
            ended = await client.post(f"{base}/{task_id}/terminate", headers=headers)
            assert ended.json()["status"] == "ok"
            assert (await client.get(base, headers=headers)).json()["data"][
                "sessions"
            ] == []
            completed = await shell.exec_managed(
                f'"{Path(sys.executable).as_posix()}" -c "print(789)"',
                owner_id=origin,
                creator_id=account["resource_owner"],
                creator_is_admin=account["role"] == "admin",
                sandboxed=False,
                yield_time_ms=0,
                timeout=60,
            )
            finished_id = completed["session_id"]
            await asyncio.wait_for(shell._sessions[finished_id].wait_task, 10)
            removed = await client.post(
                f"{base}/{finished_id}/discard", headers=headers
            )
            assert removed.json()["data"]["removed"] is True
    finally:
        await shell.shutdown_sessions()


@pytest.mark.asyncio
async def test_active_file_result_survives_owned_stream_reconnect_only(
    ordinary_chat, tmp_path, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    account = users[0]
    headers = {"Authorization": f"Bearer {account['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    path = tmp_path / "live-result.txt"
    path.write_text("private-live-result")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    release = asyncio.Event()

    async def model():
        _, target, payload = await webchat_queue_mgr.get_or_create_queue(sid).get()
        await webchat_queue_mgr.put_back_queue(
            payload["message_id"],
            {
                "type": "plain",
                "chain_type": "file_changes",
                "data": json.dumps(
                    {
                        "files": [
                            {
                                "path": str(path),
                                "kind": "created",
                                "runtime": "local",
                                "sha256": checksum,
                                "backup_id": "",
                                "diff_available": True,
                            }
                        ]
                    }
                ),
                "streaming": True,
            },
        )
        await release.wait()
        await webchat_queue_mgr.put_back_queue(payload["message_id"], {"type": "end"})

    worker = asyncio.create_task(model())
    try:
        async with ASGIStream(
            server.asgi_app,
            "/api/v1/chat",
            account["token"],
            {"session_id": sid, "message": "Create a live tool result"},
        ) as initial:
            await initial.event("file_changes")
            state = (
                await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
            ).json()["data"]
            run_id = state["active_runs"][0]["run_id"]
            payload = {
                "session_id": sid,
                "run_id": run_id,
                "path": str(path),
                "backup_id": "",
                "expect_sha256": checksum,
            }
            diff = await client.post(
                "/api/v1/chat/file-changes/diff", headers=headers, json=payload
            )
            assert "private-live-result" in diff.json()["data"]["diff"]
            for other in [admin, users[1]]:
                denied = await client.post(
                    "/api/v1/chat/file-changes/diff",
                    headers={"Authorization": f"Bearer {other['token']}"},
                    json=payload,
                )
                assert denied.status_code == 403
                assert "private-live-result" not in denied.text
        async with ASGIStream(
            server.asgi_app, f"/api/v1/chat/runs/{run_id}/stream", account["token"]
        ) as resumed:
            snapshot = await resumed.event("run_snapshot")
            assert snapshot["data"]["content"]["file_changes"]["files"][0][
                "path"
            ] == str(path)
            diff = await client.post(
                "/api/v1/chat/file-changes/diff", headers=headers, json=payload
            )
            assert diff.json()["status"] == "ok"
            path.write_text("newer foreign replacement")
            stale = await client.post(
                "/api/v1/chat/file-changes/diff", headers=headers, json=payload
            )
            assert stale.json()["status"] == "error"
            assert "newer foreign replacement" not in stale.text
            release.set()
            await resumed.event("end")
    finally:
        release.set()
        await asyncio.wait_for(worker, 10)


@pytest.mark.asyncio
async def test_actual_plugin_handlers_require_trusted_origins_and_new_guard_markers(
    ordinary_chat, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    roots = [
        os.environ.get("ASTRBOT_TEST_SPCODE_ROOT"),
        os.environ.get("ASTRBOT_TEST_BROWSER_ROOT"),
    ]
    if not all(roots):
        pytest.skip(
            "Set both plugin checkout roots for the cross-repository contract test"
        )
    modules = []
    for name, root in zip(["t09_spcode", "t09_browser"], roots):
        package = types.ModuleType(name)
        package.__path__ = [str(Path(root).resolve())]
        monkeypatch.setitem(sys.modules, name, package)
        modules.append(
            importlib.import_module(
                f"{name}.tools.webapi" if name == "t09_spcode" else f"{name}.main"
            )
        )
    webapi, browser_module = modules
    plan_module = importlib.import_module("t09_spcode.tools.security.plan_mode")
    registered = []

    def register(route, view_handler, methods, desc):
        registered.append((route, view_handler, methods, desc))

    plugin = SimpleNamespace(
        _webapi_active=True,
        context=SimpleNamespace(register_web_api=register),
        _plan=plan_module.PlanModeController(
            lambda: {}, lambda umo: core.astrbot_config
        ),
    )
    webapi.register_webapi_routes(plugin)
    browser = browser_module.BrowserUsePlugin.__new__(browser_module.BrowserUsePlugin)
    browser.context = SimpleNamespace(get_db=lambda: db)
    browser._enabled = True
    browser._owner = None
    browser._webchat_owner = None
    browser._closed_state = None
    registered.append(
        ("/browser-use/status", browser.browser_observation, ["GET"], "owned browser")
    )
    monkeypatch.setattr(core.star_context, "registered_web_apis", registered)
    monkeypatch.setattr(
        core.star_context, "get_config", lambda umo: core.astrbot_config, raising=False
    )
    assert Path(inspect.getsourcefile(browser.browser_observation)).is_relative_to(
        Path(roots[1])
    )
    assert Path(inspect.getsourcefile(registered[0][1])).is_relative_to(Path(roots[0]))
    identities = []
    for account in [admin, *users]:
        headers = {"Authorization": f"Bearer {account['token']}"}
        sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
            "data"
        ]["session_id"]
        origin = f"webchat:FriendMessage:webchat!{account['resource_owner']}!{sid}"
        identities.append((account, sid, origin))
        for base in ["/api/v1/plugins/extensions", "/api/plug"]:
            own = await client.post(
                f"{base}/spcode/plan-mode",
                headers=headers,
                json={"umo": origin, "active": True},
            )
            assert own.json()["data"]["active"] is True, own.text
            assert "all_active_count" not in own.text
            observer = await client.get(
                f"{base}/browser-use/status", headers=headers, params={"chat_id": sid}
            )
            assert observer.json()["data"] == {"available": False}, observer.text
    for account, sid, origin in identities:
        for other, other_sid, other_origin in identities:
            if account is other:
                continue
            headers = {"Authorization": f"Bearer {other['token']}"}
            for base in ["/api/v1/plugins/extensions", "/api/plug"]:
                denied = await client.post(
                    f"{base}/spcode/plan-mode",
                    headers=headers,
                    json={"umo": origin, "active": False},
                )
                assert denied.status_code == 403
                denied = await client.get(
                    f"{base}/browser-use/status",
                    headers=headers,
                    params={"chat_id": sid},
                )
                assert denied.status_code == 403
            assert plugin._plan.is_active(origin)
        headers = {"Authorization": f"Bearer {account['token']}"}
        forged = await client.post(
            "/api/v1/plugins/extensions/spcode/plan-mode",
            headers=headers,
            json={"umo": origin, "active": False, "role": "admin"},
        )
        assert forged.status_code == 403
        assert plugin._plan.is_active(origin)
    # Existing profile enablement also applies to the audited consumer routes.
    core.astrbot_config["plugin_set"] = []
    account, sid, origin = identities[1]
    headers = {"Authorization": f"Bearer {account['token']}"}
    disabled = await client.post(
        "/api/v1/plugins/extensions/spcode/plan-mode",
        headers=headers,
        json={"umo": origin, "active": False},
    )
    assert disabled.status_code == 403
    assert plugin._plan.is_active(origin)
    disabled = await client.get(
        "/api/v1/plugins/extensions/browser-use/status",
        headers=headers,
        params={"chat_id": sid},
    )
    assert disabled.status_code == 403
    core.astrbot_config["plugin_set"] = ["*"]
    # A route name alone never admits an older installed implementation.
    plan_handler = next(
        h
        for r, h, methods, _ in registered
        if r == "/spcode/plan-mode" and "POST" in methods
    )
    monkeypatch.delattr(plan_handler, "__astrbot_chat_guard__")
    monkeypatch.delattr(browser.browser_observation.__func__, "__astrbot_chat_guard__")
    for account, sid, origin in identities:
        headers = {"Authorization": f"Bearer {account['token']}"}
        denied = await client.post(
            "/api/v1/plugins/extensions/spcode/plan-mode",
            headers=headers,
            json={"umo": origin, "active": False},
        )
        assert denied.status_code == 403
        assert plugin._plan.is_active(origin)
        denied = await client.get(
            "/api/v1/plugins/extensions/browser-use/status",
            headers=headers,
            params={"chat_id": sid},
        )
        assert denied.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["file", "directory"])
@pytest.mark.parametrize("platform", ["win32", "linux"])
async def test_exact_deleted_object_is_restored_without_consuming_newer_foreign_entry(
    ordinary_chat, tmp_path, monkeypatch, kind, platform
):
    client, server, core, db, admin, users = ordinary_chat
    monkeypatch.setattr(recycle_restore.sys, "platform", platform)
    trash = tmp_path / "trash"
    monkeypatch.setattr(recycle_restore, "_windows_drive_roots", lambda: [trash])
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    serial = 0

    def controlled_trash(raw):
        nonlocal serial
        serial += 1
        target = Path(raw)
        if platform == "win32":
            folder = trash / "$Recycle.Bin" / "synthetic-sid"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"$I{serial}.bin").write_bytes(_make_i_bytes_v4(str(target)))
            target.replace(folder / f"$R{serial}.bin")
        else:
            root = tmp_path / "Trash"
            (root / "info").mkdir(parents=True, exist_ok=True)
            (root / "files").mkdir(exist_ok=True)
            (root / "info" / f"{serial}.trashinfo").write_text(
                f"[Trash Info]\nPath={quote(str(target))}\nDeletionDate=2026-11-01T12:00:00\n"
            )
            target.replace(root / "files" / str(serial))

    monkeypatch.setattr(
        fs, "_load_send2trash", lambda: SimpleNamespace(send2trash=controlled_trash)
    )
    account = users[0]
    headers = {"Authorization": f"Bearer {account['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    target = tmp_path / "same-path"
    results = []
    for content in ["account-a-original", "account-b-newer"]:
        if kind == "directory":
            target.mkdir()
            (target / "nested.txt").write_text(content)
        else:
            target.write_text(content)
        result = fs._remove_path(str(target), confirm=True, max_items=50)
        assert result["ok"] and result["_recycle_entry"], result
        results.append(result)
    a, b = results
    assert a["_recycle_entry"] != b["_recycle_entry"]
    source = PlatformMessageHistory(
        platform_id="webchat",
        user_id=sid,
        content={
            "type": "bot",
            "file_changes": {
                "files": [
                    {
                        "path": str(target),
                        "runtime": "local",
                        "kind": "remove",
                        "_canonical_path": str(target.resolve()),
                        "_recycle_entry": a["_recycle_entry"],
                        "_removed_sha256": a["_removed_sha256"],
                    }
                ]
            },
        },
    )
    async with db.get_db() as connection:
        connection.add(source)
        await connection.commit()
        await connection.refresh(source)
    payload = {"session_id": sid, "message_id": source.id, "path": str(target)}
    for other in [admin, users[1]]:
        denied = await client.post(
            "/api/v1/chat/file-changes/restore-removed",
            headers={"Authorization": f"Bearer {other['token']}"},
            json=payload,
        )
        assert denied.status_code == 403
    forged = await client.post(
        "/api/v1/chat/file-changes/restore-removed",
        headers=headers,
        json={**payload, "_recycle_entry": b["_recycle_entry"]},
    )
    assert forged.status_code == 400
    # Both the official operation and the old plugin alias have the same source check.
    own = await client.post(
        "/api/plug/spcode/file-remove/restore", headers=headers, json=payload
    )
    assert own.json()["status"] == "ok", own.text
    actual = (
        (target / "nested.txt").read_text()
        if kind == "directory"
        else target.read_text()
    )
    assert actual == "account-a-original"
    remaining = recycle_restore.get_recycle_entries(str(target))
    assert b["_recycle_entry"] in remaining
    assert a["_recycle_entry"] not in remaining
    repeated = await client.post(
        "/api/v1/chat/file-changes/restore-removed", headers=headers, json=payload
    )
    assert repeated.json()["status"] == "error"
    assert b["_recycle_entry"] in recycle_restore.get_recycle_entries(str(target))
