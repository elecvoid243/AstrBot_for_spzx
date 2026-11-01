"""Full instance recovery through production authentication and real SQLite."""

import asyncio
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import jwt
import pytest
import pytest_asyncio
from sqlmodel import select

from astrbot.core import file_token_service
from astrbot.core.backup import exporter, importer
from astrbot.core.config.default import VERSION
from astrbot.core.db.po import Attachment, DashboardAccount, PlatformMessageHistory
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.core.utils.webchat_access import webchat_resource_owner
from astrbot.dashboard.server import AstrBotDashboard
from tests.fixtures.dashboard_accounts import PASSWORD
from tests.fixtures.dashboard_accounts import account_dashboard as account_dashboard
from tests.fixtures.dashboard_accounts import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import USER_PASSWORD, login
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat


@pytest_asyncio.fixture
async def recovery(ordinary_chat, tmp_path, monkeypatch):
    """Use isolated production data directories and the real full backup format."""
    client, server, core, db, admin, users = ordinary_chat
    backups = server.asgi_app.state.services.backups
    backups.backup_dir = str(tmp_path / "backups")
    backups.data_dir = str(tmp_path)
    Path(backups.backup_dir).mkdir()
    chat = server.asgi_app.state.services.chat
    chat.attachments_dir = str(tmp_path / "attachments")
    chat.webchat_img_dir = str(tmp_path / "webchat" / "imgs")
    Path(chat.attachments_dir).mkdir()
    Path(chat.webchat_img_dir).mkdir(parents=True)
    monkeypatch.setattr(
        exporter,
        "get_backup_directories",
        lambda: {"webchat": str(Path(chat.webchat_img_dir).parent)},
    )
    monkeypatch.setattr(
        importer,
        "get_backup_directories",
        lambda: {"webchat": str(tmp_path / "webchat")},
    )
    yield client, server, core, db, admin, users, backups
    if backups.restore_task and not backups.restore_task.done():
        backups.restore_task.cancel()
        await asyncio.gather(backups.restore_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("data_only", [False, True])
async def test_repeated_old_snapshot_retires_every_old_credential_and_preserves_owners(
    recovery, tmp_path, monkeypatch, data_only
):
    client, server, core, db, admin, users, backups = recovery
    resources = []
    for index, account in enumerate(users):
        headers = {"Authorization": f"Bearer {account['token']}"}
        sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
            "data"
        ]["session_id"]
        project = (
            await client.post(
                "/api/v1/chat/projects",
                headers=headers,
                json={"title": f"private-{index}"},
            )
        ).json()["data"]
        assert (
            await client.post(
                f"/api/v1/chat/projects/{project['project_id']}/sessions/{sid}",
                headers=headers,
            )
        ).json()["status"] == "ok"
        upload = (
            await client.post(
                "/api/v1/files",
                headers=headers,
                files={"file": ("private.txt", f"file-{index}".encode(), "text/plain")},
            )
        ).json()["data"]
        await client.put(
            f"/api/v1/chat/sessions/{sid}/config",
            headers=headers,
            json={"config_id": "default"},
        )
        await client.put(
            "/api/v1/chat/ui-settings/value",
            headers=headers,
            json={"value": "max" if index else "low"},
        )
        content = {
            "type": "bot",
            "message": [{"type": "plain", "text": f"private-{index}"}],
            "_imported": True,
            "file_changes": {
                "_canonical_path": "native authority must remain opaque",
                "items": [{"_recycle_entry": "entry", "_removed_sha256": "digest"}],
            },
        }
        record = await db.insert_platform_message_history(
            platform_id="webchat",
            user_id=sid,
            sender_id=account["resource_owner"],
            content=content,
        )
        thread = await db.create_webchat_thread(
            account["resource_owner"], sid, record.id, "checkpoint", "selected text"
        )
        await db.insert_platform_message_history(
            platform_id="webchat_thread",
            user_id=thread.thread_id,
            sender_id=account["resource_owner"],
            content={**content, "thread": True},
        )
        resources.append(
            (sid, upload, project["project_id"], content, thread.thread_id)
        )
    async with db.get_db() as session:
        accounts = (await session.execute(select(DashboardAccount))).scalars().all()
        disabled = next(a for a in accounts if a.account_id == users[1]["account_id"])
        disabled.enabled = False
        session.add(disabled)
        await session.commit()
        expected = {a.account_id: a.model_dump(mode="json") for a in accounts}
    headers = {"Authorization": f"Bearer {admin['token']}"}
    # Export through the production operation, including real files and identity rows.
    started = await client.post("/api/v1/backups", headers=headers)
    export_id = started.json()["data"]["task_id"]
    for _ in range(100):
        progress = (
            await client.get(f"/api/v1/backups/tasks/{export_id}", headers=headers)
        ).json()["data"]
        if progress["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert progress["status"] == "completed", progress
    archive = Path(backups.backup_dir) / progress["result"]["filename"]
    downloaded = await client.get(f"/api/v1/backups/{archive.name}", headers=headers)
    assert downloaded.content[:4] == b"PK\x03\x04"
    assert downloaded.headers["content-type"] == "application/zip"
    assert downloaded.headers["content-disposition"].startswith("inline;")
    assert archive.name in downloaded.headers["content-disposition"]
    if data_only:
        reduced = archive.with_name("data-only.zip")
        with (
            zipfile.ZipFile(archive) as source,
            zipfile.ZipFile(reduced, "w") as target,
        ):
            for name in source.namelist():
                if name != "config/cmd_config.json":
                    target.writestr(name, source.read(name))
        archive = reduced
        # A modern data-only archive must restore its own account credentials,
        # not allow ensure_admin to change it back to these runtime settings.
        core.astrbot_config["dashboard"]["username"] = "changed-runtime"
        core.astrbot_config.save_config()
        admin = await login(client, "changed-runtime", PASSWORD)
    prior_tokens = [a["token"] for a in [admin, *users]]
    prior_tokens.append(
        jwt.encode(
            {"username": "original-admin", "exp": 4102444800},
            core.astrbot_config["dashboard"]["jwt_secret"],
            algorithm="HS256",
        )
    )
    owner_token = webchat_resource_owner.set(users[0]["resource_owner"])
    try:
        old_file_token = await file_token_service.register_file(
            str(
                Path(backups.data_dir)
                / "attachments"
                / Path(
                    (
                        await db.get_attachment_by_id(resources[0][1]["attachment_id"])
                    ).path
                ).name
            ),
            single_use=False,
        )
    finally:
        webchat_resource_owner.reset(owner_token)
    for iteration in range(2):
        headers = {"Authorization": f"Bearer {admin['token']}"}
        restored = await client.post(
            f"/api/v1/backups/{archive.name}/import",
            headers=headers,
            json={"confirmed": True},
        )
        assert restored.json()["status"] == "ok", restored.text
        await asyncio.wait_for(backups.restore_task, 10)
        assert (
            backups.backup_tasks[restored.json()["data"]["task_id"]]["status"]
            == "completed"
        )
        client.cookies.clear()
        for token in prior_tokens:
            for path in (
                "/api/v1/auth/session",
                "/api/auth/totp/status",
                f"/api/backup/download?filename={archive.name}&token={token}",
            ):
                assert (
                    await client.get(path, headers={"Authorization": f"Bearer {token}"})
                ).status_code == 401
        assert await file_token_service.check_token_expired(old_file_token)
        admin = await login(client, "original-admin", PASSWORD)
        assert admin["account_id"] in expected and admin.get("restore_task_id")
        headers = {"Authorization": f"Bearer {admin['token']}"}
        progress = await client.get(
            f"/api/v1/backups/tasks/{restored.json()['data']['task_id']}",
            headers=headers,
        )
        assert progress.json()["data"]["status"] == "completed"
        assert progress.json()["data"]["restart_required"]
        assert (
            await client.get("/api/v1/chat/sessions", headers=headers)
        ).status_code == 503
        # Maintenance exports must not replace the login's restore receipt ID.
        later_export = await client.post("/api/v1/backups", headers=headers)
        export_task = later_export.json()["data"]["task_id"]
        for _ in range(100):
            if backups.backup_tasks[export_task]["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
        repeated_login = await login(client, "original-admin", PASSWORD)
        assert repeated_login["restore_task_id"] == restored.json()["data"]["task_id"]
        prior_tokens.append(admin["token"])
        async with db.get_db() as session:
            accounts = (await session.execute(select(DashboardAccount))).scalars().all()
            for a in accounts:
                for field in (
                    "account_id",
                    "legacy_owner",
                    "role",
                    "enabled",
                    "token_version",
                    "password_hash",
                    "username",
                ):
                    assert getattr(a, field) == expected[a.account_id][field]
        # Rebuild production app/DB as a process restart, then authenticate again.
        reopened = SQLiteDatabase(db.db_path)
        await reopened.initialize()
        reopened.inited = True
        core.platform_message_history_manager = PlatformMessageHistoryManager(reopened)
        core.umop_config_router.umop_to_conf_id.clear()
        await core.umop_config_router.initialize()
        monkeypatch.setattr(
            "astrbot.dashboard.services.backup_service.get_astrbot_backups_path",
            lambda: backups.backup_dir,
        )
        restarted = AstrBotDashboard(core, reopened, asyncio.Event())
        restarted.asgi_app.state.services.chat.attachments_dir = str(
            tmp_path / "attachments"
        )
        restarted.asgi_app.state.services.chat.webchat_img_dir = str(
            tmp_path / "webchat" / "imgs"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted.asgi_app),
            base_url="http://testserver",
        ) as new_client:
            current = await login(new_client, "first-user", USER_PASSWORD)
            h = {"Authorization": f"Bearer {current['token']}"}
            own = await new_client.get(
                f"/api/v1/chat/sessions/{resources[0][0]}", headers=h
            )
            assert "private-0" in own.text
            foreign = await new_client.get(
                f"/api/v1/chat/sessions/{resources[1][0]}", headers=h
            )
            assert "private-1" not in foreign.text
            file = await new_client.get(
                f"/api/v1/files/{resources[0][1]['attachment_id']}/content", headers=h
            )
            assert file.content == b"file-0"
            assert (
                await new_client.get("/api/v1/backups", headers=h)
            ).status_code == 403
            assert (
                await new_client.post(
                    "/api/v1/auth/login",
                    json={"username": "second-user", "password": USER_PASSWORD},
                )
            ).status_code == 401
        await reopened.engine.dispose()
        # Use this still-maintained first process to repeat the same old archive;
        # it must not rely on a monotonically increasing snapshot ver.
    async with db.get_db() as session:
        rows = (await session.execute(select(PlatformMessageHistory))).scalars().all()
        assert [row.content for row in rows] == [
            content for r in resources for content in (r[3], {**r[3], "thread": True})
        ]


@pytest.mark.asyncio
async def test_legacy_restore_registers_only_extracted_members(recovery, tmp_path):
    client, _, core, db, admin, users, backups = recovery
    residual = tmp_path / "attachments" / "residual.txt"
    residual.write_bytes(b"server residual must remain unavailable")
    archive = Path(backups.backup_dir) / "legacy.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(
            "manifest.json",
            json.dumps(
                {
                    "astrbot_version": VERSION,
                    "version": "1.1",
                    "directories": ["webchat"],
                }
            ),
        )
        package.writestr("databases/main_db.json", json.dumps({"attachments": []}))
        package.writestr(
            "config/cmd_config.json", json.dumps(dict(core.astrbot_config))
        )
        package.writestr("files/attachments/legacy.txt", b"legacy attachment")
        package.writestr("directories/webchat/imgs/legacy.png", b"legacy image")
        package.writestr("directories/webchat/../../escape.txt", b"must not extract")
    h = {"Authorization": f"Bearer {admin['token']}"}
    await client.post(
        f"/api/v1/backups/{archive.name}/import", headers=h, json={"confirmed": True}
    )
    await asyncio.wait_for(backups.restore_task, 10)
    assert backups.restore_phase == "restart_required"
    async with db.get_db() as session:
        rows = (await session.execute(select(Attachment))).scalars().all()
        assert {Path(r.path).name for r in rows} == {"legacy.txt", "legacy.png"}
        assert {r.owner for r in rows} == {admin["resource_owner"]}
    await db.initialize()
    async with db.get_db() as session:
        assert len((await session.execute(select(Attachment))).scalars().all()) == 2
    assert residual.exists() and not (tmp_path / "escape.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("maintenance", [False, True])
async def test_duplicate_import_does_not_release_first_response_barrier(
    recovery, monkeypatch, maintenance
):
    client, _, core, _, admin, _, backups = recovery
    archive = Path(backups.backup_dir) / "valid.zip"
    await exporter.AstrBotExporter(
        backups.db, config_path=core.astrbot_config.config_path
    ).export_all(backups.backup_dir)
    source = next(Path(backups.backup_dir).glob("astrbot_backup*.zip"))
    source.rename(archive)
    if maintenance:
        await client.post(
            f"/api/v1/backups/{archive.name}/import",
            headers={"Authorization": f"Bearer {admin['token']}"},
            json={"confirmed": True},
        )
        await asyncio.wait_for(backups.restore_task, 10)
        assert backups.restore_phase == "restart_required"
        admin = await login(client, "original-admin", PASSWORD)
    admitted = asyncio.Event()
    release = asyncio.Event()
    original = backups.import_backup

    async def paused(payload):
        result = original(payload)
        admitted.set()
        await release.wait()
        return result

    monkeypatch.setattr(backups, "import_backup", paused)
    h = {"Authorization": f"Bearer {admin['token']}"}
    first = asyncio.create_task(
        client.post(
            f"/api/v1/backups/{archive.name}/import",
            headers=h,
            json={"confirmed": True},
        )
    )
    await asyncio.wait_for(admitted.wait(), 3)
    second = await client.post(
        f"/api/v1/backups/{archive.name}/import", headers=h, json={"confirmed": True}
    )
    assert second.json()["status"] == "error"
    assert not backups.restore_request_done.is_set()
    assert backups.restore_phase == "pending"
    if maintenance:
        for path in ("/api/v1/chat/sessions", "/api/v1/system-config"):
            assert (await client.get(path, headers=h)).status_code == 503
    release.set()
    assert (await first).json()["status"] == "ok"
    await asyncio.wait_for(backups.restore_task, 10)
    assert backups.restore_phase == "restart_required"


@pytest.mark.asyncio
async def test_stop_timeout_never_clears_db_and_bad_retry_keeps_recovery(
    recovery, monkeypatch
):
    client, _, core, db, admin, _, backups = recovery
    archive = Path(
        await exporter.AstrBotExporter(
            db, config_path=core.astrbot_config.config_path
        ).export_all(backups.backup_dir)
    )
    old_key = core.astrbot_config["dashboard"]["jwt_secret"]
    cleanup = asyncio.Event()
    started = asyncio.Event()

    async def slow_writer():
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await cleanup.wait()

    task = asyncio.create_task(slow_writer())
    await started.wait()
    core.event_bus = SimpleNamespace(_pending_tasks={task})
    monkeypatch.setattr(
        "astrbot.dashboard.services.backup_service.RESTORE_STOP_TIMEOUT", 0.02
    )
    h = {"Authorization": f"Bearer {admin['token']}"}
    invalid = Path(backups.backup_dir) / "first-preflight-failure.zip"
    invalid.write_bytes(b"invalid archive")
    await client.post(
        f"/api/v1/backups/{invalid.name}/import", headers=h, json={"confirmed": True}
    )
    await asyncio.wait_for(backups.restore_task, 5)
    assert backups.restore_phase == "ready" and not task.done()
    await client.post(
        f"/api/v1/backups/{archive.name}/import", headers=h, json={"confirmed": True}
    )
    await asyncio.wait_for(backups.restore_task, 5)
    assert backups.restore_phase == "failed" and not backups.restore_mutated
    assert core.astrbot_config["dashboard"]["jwt_secret"] == old_key
    assert (await client.get("/api/v1/auth/session", headers=h)).status_code == 200
    async with db.get_db() as session:
        assert (
            len((await session.execute(select(DashboardAccount))).scalars().all()) == 3
        )
    # A bad retry after a partial restore must never remove its previous gate.
    backups.restore_mutated = True
    bad = Path(backups.backup_dir) / "bad.zip"
    bad.write_bytes(b"invalid archive")
    await client.post(
        f"/api/v1/backups/{bad.name}/import", headers=h, json={"confirmed": True}
    )
    await asyncio.wait_for(backups.restore_task, 5)
    assert backups.restore_phase == "failed" and backups.restore_mutated
    cleanup.set()
    await asyncio.wait_for(task, 3)
    monkeypatch.setattr(
        "astrbot.dashboard.services.backup_service.RESTORE_STOP_TIMEOUT", 15
    )
    await client.post(
        f"/api/v1/backups/{archive.name}/import", headers=h, json={"confirmed": True}
    )
    await asyncio.wait_for(backups.restore_task, 10)
    assert backups.restore_phase == "restart_required"


@pytest.mark.asyncio
async def test_pipeline_cleanup_finishes_before_database_replacement(recovery):
    from astrbot.core.pipeline.context import PipelineContext
    from astrbot.core.pipeline.scheduler import PipelineScheduler
    from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
    from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
        webchat_queue_mgr,
    )

    client, _, core, db, admin, users, backups = recovery
    h = {"Authorization": f"Bearer {users[0]['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=h)).json()["data"][
        "session_id"
    ]
    archive = Path(
        await exporter.AstrBotExporter(
            db, config_path=core.astrbot_config.config_path
        ).export_all(backups.backup_dir)
    )
    entered = asyncio.Event()
    cleaned = asyncio.Event()
    adapter = WebChatAdapter({}, {}, asyncio.Queue())

    async def model_turn(event):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            # A real final application write from the admitted old turn. It must
            # finish against the old dataset, then disappear with replacement.
            await db.insert_platform_message_history(
                platform_id="webchat",
                user_id=sid,
                sender_id=users[0]["resource_owner"],
                content={
                    "type": "bot",
                    "message": [{"type": "plain", "text": "late-old-output"}],
                },
            )
            cleaned.set()

    async def model():
        payload = await webchat_queue_mgr.get_or_create_queue(sid).get()
        event = adapter.create_event(await adapter.convert_message(payload))
        scheduler = PipelineScheduler(
            PipelineContext(core.astrbot_config, core.plugin_manager, "default", db)
        )
        scheduler.stages = [SimpleNamespace(process=model_turn)]
        await scheduler.execute(event)

    worker = asyncio.create_task(model())
    core.event_bus = SimpleNamespace(_pending_tasks={worker})
    sending = asyncio.create_task(
        client.post(
            "/api/v1/chat",
            headers=h,
            json={"session_id": sid, "message": "held external model"},
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    started = await client.post(
        f"/api/v1/backups/{archive.name}/import",
        headers={"Authorization": f"Bearer {admin['token']}"},
        json={"confirmed": True},
    )
    assert started.json()["status"] == "ok"
    await asyncio.wait_for(backups.restore_task, 10)
    await asyncio.gather(worker, sending, return_exceptions=True)
    assert cleaned.is_set() and worker.done() and sending.done()
    assert backups.restore_phase == "restart_required"
    async with db.get_db() as session:
        rows = (await session.execute(select(PlatformMessageHistory))).scalars().all()
    assert all("late-old-output" not in json.dumps(row.content) for row in rows)


@pytest.mark.asyncio
async def test_partial_restore_receipt_allows_admin_retry_after_interruption(
    recovery, tmp_path, monkeypatch
):
    client, _, core, db, admin, _, backups = recovery
    archive = Path(
        await exporter.AstrBotExporter(
            db, config_path=core.astrbot_config.config_path
        ).export_all(backups.backup_dir)
    )
    original = importer.AstrBotImporter._import_directories
    fail = True

    async def fail_after_identity_commit(self, *args):
        nonlocal fail
        if fail:
            fail = False
            raise OSError("Controlled failure after database/config commit")
        return await original(self, *args)

    monkeypatch.setattr(
        importer.AstrBotImporter, "_import_directories", fail_after_identity_commit
    )
    headers = {"Authorization": f"Bearer {admin['token']}"}
    await client.post(
        f"/api/v1/backups/{archive.name}/import",
        headers=headers,
        json={"confirmed": True},
    )
    await asyncio.wait_for(backups.restore_task, 10)
    assert backups.restore_phase == "failed" and backups.restore_mutated
    monkeypatch.setattr(
        "astrbot.dashboard.services.backup_service.get_astrbot_backups_path",
        lambda: backups.backup_dir,
    )
    # An interrupted config/DB publication must not rename the restored account.
    core.astrbot_config["dashboard"]["username"] = "stale-runtime-login"
    core.astrbot_config.save_config()
    restarted = AstrBotDashboard(core, db, asyncio.Event())
    recovering = restarted.asgi_app.state.services.backups
    recovering.data_dir = str(tmp_path)
    assert recovering.restore_phase == "failed"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted.asgi_app),
        base_url="http://testserver",
    ) as current:
        identity = await login(current, "original-admin", PASSWORD)
        assert identity["account_id"] == admin["account_id"]
        h = {"Authorization": f"Bearer {identity['token']}"}
        receipt = await current.get(
            f"/api/v1/backups/tasks/{identity['restore_task_id']}", headers=h
        )
        assert receipt.json()["data"]["status"] == "failed"
        assert (
            await current.get("/api/v1/chat/sessions", headers=h)
        ).status_code == 503
        bad = Path(backups.backup_dir) / "invalid-retry.zip"
        bad.write_bytes(b"invalid")
        await current.post(
            f"/api/v1/backups/{bad.name}/import", headers=h, json={"confirmed": True}
        )
        await asyncio.wait_for(recovering.restore_task, 10)
        assert recovering.restore_phase == "failed"
        assert recovering.restore_mutated
        rebuilt = type(recovering)(db, core)
        assert rebuilt.restore_phase == "failed" and rebuilt.restore_mutated
        await current.post(
            f"/api/v1/backups/{archive.name}/import",
            headers=h,
            json={"confirmed": True},
        )
        await asyncio.wait_for(recovering.restore_task, 10)
        assert recovering.restore_phase == "restart_required"
        identity = await login(current, "original-admin", PASSWORD)
        assert identity["account_id"] == admin["account_id"]


@pytest.mark.asyncio
async def test_failed_normal_restart_does_not_enter_restore_maintenance(recovery):
    from unittest.mock import AsyncMock

    client, _, core, _, admin, _, backups = recovery
    core.restart = AsyncMock(side_effect=RuntimeError("Controlled restart failure"))
    with pytest.raises(RuntimeError, match="Controlled restart failure"):
        await client.post(
            "/api/v1/system/restart",
            headers={"Authorization": f"Bearer {admin['token']}"},
        )
    assert backups.restore_phase == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "{",
        '{"task_id":"broken","task":{"type":"import"}}',
        '{"task_id":"broken","task":{"type":"import","status":"failed"}}',
    ],
)
async def test_unreadable_restore_receipt_keeps_admin_recovery(
    recovery, monkeypatch, content
):
    _, _, core, db, _, _, backups = recovery
    monkeypatch.setattr(
        "astrbot.dashboard.services.backup_service.get_astrbot_backups_path",
        lambda: backups.backup_dir,
    )
    (Path(backups.backup_dir) / ".restore-state.json").write_text(
        content, encoding="utf-8"
    )
    rebuilt = type(backups)(db, core)
    assert rebuilt.restore_phase == "failed" and rebuilt.restore_mutated
    assert rebuilt.get_progress(rebuilt.last_restore_task_id)["status"] == "failed"


@pytest.mark.asyncio
async def test_full_restore_awaits_actual_scheduler_job_final_write(
    recovery, monkeypatch
):
    client, _, core, db, admin, users, backups = recovery
    from datetime import datetime, timezone

    from astrbot.core.cron.manager import CronJobManager

    cron = CronJobManager(db)
    core.cron_manager = cron
    await cron.start(SimpleNamespace())
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleaned = asyncio.Event()
    job_tasks = []
    before_clear = []

    async def controlled_external_job():
        job_tasks.append(asyncio.current_task())
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
            await release_cleanup.wait()
            await db.insert_platform_message_history(
                platform_id="webchat",
                user_id="old-cron-run",
                sender_id=users[0]["resource_owner"],
                content={
                    "type": "bot",
                    "message": [{"type": "plain", "text": "old cron final write"}],
                },
            )
            cleaned.set()

    job = await cron.add_basic_job(
        name="restore barrier",
        cron_expression="0 0 1 1 *",
        handler=controlled_external_job,
    )
    cron.scheduler.reschedule_job(
        job.job_id, trigger="date", run_date=datetime.now(timezone.utc)
    )
    await asyncio.wait_for(entered.wait(), 3)
    archive = Path(
        await exporter.AstrBotExporter(
            db, config_path=core.astrbot_config.config_path
        ).export_all(backups.backup_dir)
    )
    original = importer.AstrBotImporter._clear_main_db

    async def record_before_clear(self, *args, **kwargs):
        before_clear.append(cleaned.is_set() and all(task.done() for task in job_tasks))
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(importer.AstrBotImporter, "_clear_main_db", record_before_clear)

    async def finish_cancelled_external_call():
        await cancelled.wait()
        await asyncio.sleep(0.3)
        release_cleanup.set()

    release_task = asyncio.create_task(finish_cancelled_external_call())
    try:
        response = await client.post(
            f"/api/v1/backups/{archive.name}/import",
            headers={"Authorization": f"Bearer {admin['token']}"},
            json={"confirmed": True},
        )
        assert response.json()["status"] == "ok"
        await asyncio.wait_for(backups.restore_task, 10)
        await asyncio.wait_for(release_task, 5)
        await asyncio.gather(*job_tasks, return_exceptions=True)
        assert backups.restore_phase == "restart_required"
        assert before_clear == [True], (
            "Database replacement began while the real APScheduler job was still executing its final write"
        )
    finally:
        release_cleanup.set()
        await asyncio.gather(release_task, *job_tasks, return_exceptions=True)
        await cron.shutdown()


@pytest.mark.asyncio
async def test_full_restore_drains_scheduler_metadata_writes(recovery, monkeypatch):
    from astrbot.core.cron.manager import CronJobManager

    client, _, core, db, admin, _, backups = recovery
    cron = CronJobManager(db)
    core.cron_manager = cron
    await cron.start(SimpleNamespace())
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    before_clear = []
    update = db.update_cron_job

    async def delayed_metadata(job_id, **values):
        if set(values) == {"next_run_time"}:
            entered.set()
            await release.wait()
            result = await update(job_id, **values)
            finished.set()
            return result
        return await update(job_id, **values)

    monkeypatch.setattr(db, "update_cron_job", delayed_metadata)
    await cron.add_basic_job(
        name="future metadata", cron_expression="0 0 1 1 *", handler=lambda: None
    )
    await asyncio.wait_for(entered.wait(), 3)
    archive = Path(
        await exporter.AstrBotExporter(
            db, config_path=core.astrbot_config.config_path
        ).export_all(backups.backup_dir)
    )
    original = importer.AstrBotImporter._clear_main_db

    async def before_replace(self, *args, **kwargs):
        before_clear.append(finished.is_set())
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(importer.AstrBotImporter, "_clear_main_db", before_replace)
    try:
        await client.post(
            f"/api/v1/backups/{archive.name}/import",
            headers={"Authorization": f"Bearer {admin['token']}"},
            json={"confirmed": True},
        )
        await asyncio.sleep(0.05)
        assert not before_clear
        release.set()
        await asyncio.wait_for(backups.restore_task, 10)
        assert before_clear == [True]
        assert backups.restore_phase == "restart_required"
    finally:
        release.set()
        await cron.shutdown(wait_for_jobs=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quiesced, phase", [(False, "ready"), (True, "restarting"), (False, "restarting")]
)
async def test_restore_restart_outlives_the_retired_core(
    recovery, monkeypatch, quiesced, phase
):
    from unittest.mock import AsyncMock, Mock

    from astrbot.core.core_lifecycle import AstrBotCoreLifecycle

    _, _, core, db, _, _, backups = recovery
    core.db = db
    core.dashboard_shutdown_event = asyncio.Event()
    core.provider_manager.terminate = AsyncMock()
    core.platform_manager.terminate = AsyncMock()
    core.kb_manager = SimpleNamespace(terminate=AsyncMock())
    backups.restore_quiesced = quiesced
    backups.restore_phase = phase
    thread = Mock()
    monkeypatch.setattr("astrbot.core.core_lifecycle.threading.Thread", thread)
    await AstrBotCoreLifecycle.restart(core)
    assert thread.call_args.kwargs["daemon"] is (phase == "ready")
    assert core.dashboard_shutdown_event.is_set()
