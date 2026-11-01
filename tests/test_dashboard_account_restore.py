import asyncio
import copy
import json
import zipfile

import httpx
import pytest
import pytest_asyncio
from sqlmodel import select

from astrbot.core.backup.importer import AstrBotImporter
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.config.default import VERSION
from astrbot.core.db.po import (
    ChatUIProject,
    DashboardAccount,
    PlatformMessageHistory,
    PlatformSession,
    Preference,
)
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.core.umop_config_router import UmopConfigRouter
from astrbot.core.utils.auth_password import hash_md5_dashboard_password
from astrbot.core.utils.shared_preferences import SharedPreferences
from astrbot.dashboard.server import AstrBotDashboard
from tests.test_dashboard_accounts import PASSWORD
from tests.test_dashboard_accounts import account_dashboard as account_dashboard
from tests.test_dashboard_accounts import fake_core_lifecycle as fake_core_lifecycle


@pytest_asyncio.fixture
async def legacy_archive(account_dashboard, tmp_path, request):
    """Create a real old-format archive before administrator migration."""
    server, core, db = account_dashboard
    owner = "original-admin"
    session = await db.create_platform_session(creator=owner, platform_id="webchat")
    project = await db.create_chatui_project(creator=owner, title="Restored project")
    await db.insert_platform_message_history(
        platform_id="webchat",
        user_id=session.session_id,
        content={
            "type": "user",
            "message": [{"type": "plain", "text": "original backup history"}],
        },
        sender_id=owner,
    )
    umo = f"webchat:FriendMessage:webchat!{owner}!{session.session_id}"
    await db.insert_preference_or_update(
        "global", "global", "umop_config_routing", {"val": {umo: "default"}}
    )
    data = {}
    async with db.get_db() as connection:
        for model in (
            PlatformSession,
            ChatUIProject,
            PlatformMessageHistory,
            Preference,
        ):
            rows = (await connection.execute(select(model))).scalars().all()
            data[model.__tablename__] = [row.model_dump(mode="json") for row in rows]
    config = copy.deepcopy(dict(core.astrbot_config))
    if getattr(request, "param", "pbkdf2") == "md5":
        config["dashboard"]["pbkdf2_password"] = ""
        config["dashboard"]["password"] = hash_md5_dashboard_password(PASSWORD)
        config["dashboard"]["password_storage_upgraded"] = False
    path = tmp_path / "legacy-admin.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json", json.dumps({"astrbot_version": VERSION, "directories": []})
        )
        archive.writestr("databases/main_db.json", json.dumps(data))
        archive.writestr("config/cmd_config.json", json.dumps(config))
    return path, session.session_id, project.project_id, umo, config


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_archive", ["pbkdf2", "md5"], indirect=True)
async def test_legacy_restore_polling_cannot_bootstrap_from_renamed_runtime(
    account_dashboard, legacy_archive, tmp_path, monkeypatch
):
    """Pause after clearing tables, poll over HTTP, restore and restart."""
    server, core, db = account_dashboard
    archive, session_id, project_id, umo, restored_config = legacy_archive
    service = server.asgi_app.state.services.backups
    service.backup_dir = str(tmp_path)
    service.data_dir = str(tmp_path)
    cleared = asyncio.Event()
    release = asyncio.Event()
    import_tasks = []
    import_database = AstrBotImporter._import_main_database

    async def paused_import(self, data, session=None):
        import_tasks.append(asyncio.current_task())
        cleared.set()
        await release.wait()
        return await import_database(self, data, session=session)

    monkeypatch.setattr(AstrBotImporter, "_import_main_database", paused_import)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.asgi_app), base_url="http://testserver"
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "original-admin", "password": PASSWORD},
        )
        account_id = login.json()["data"]["account_id"]
        headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
        changed = await client.patch(
            "/api/v1/auth/account",
            headers=headers,
            json={"password": PASSWORD, "new_username": "renamed-admin"},
        )
        assert changed.json()["status"] == "ok"
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "renamed-admin", "password": PASSWORD},
        )
        headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
        started = await client.post(
            f"/api/v1/backups/{archive.name}/import",
            headers=headers,
            json={"confirmed": True},
        )
        task_id = started.json()["data"]["task_id"]
        await asyncio.wait_for(cleared.wait(), timeout=5)
        polls = [
            asyncio.create_task(client.get(path, headers=headers))
            for path in (
                f"/api/v1/backups/tasks/{task_id}",
                f"/api/backup/progress?task_id={task_id}",
            )
        ]
        await asyncio.sleep(0.03)
        assert all(not poll.done() for poll in polls)
        # The previous identity remains visible until the restore commits.
        async with db.get_db() as connection:
            accounts = (
                (await connection.execute(select(DashboardAccount))).scalars().all()
            )
        assert [(account.account_id, account.legacy_owner) for account in accounts] == [
            (account_id, "original-admin")
        ]
        release.set()
        await asyncio.wait_for(import_tasks[0], timeout=10)
        responses = await asyncio.wait_for(asyncio.gather(*polls), timeout=10)
        assert all(response.status_code == 401 for response in responses)
        assert service.backup_tasks[task_id]["status"] == "completed"
        # Recovery authentication must use the restored credentials immediately,
        # before the mandatory runtime restart (T11 full-restore semantics).
        assert core.astrbot_config["dashboard"]["username"] == "original-admin"
        async with db.get_db() as connection:
            accounts = (
                (await connection.execute(select(DashboardAccount))).scalars().all()
            )
        assert len(accounts) == 1
        assert accounts[0].account_id == account_id
        assert accounts[0].legacy_owner == "original-admin"

    reopened = SQLiteDatabase(db.db_path)
    await reopened.initialize()
    reopened.inited = True
    core.astrbot_config = AstrBotConfig(core.astrbot_config.config_path)
    preferences = SharedPreferences(
        reopened, str(tmp_path / "restored-preferences.json")
    )
    core.umop_config_router = UmopConfigRouter(preferences)
    await core.umop_config_router.initialize()
    core.platform_message_history_manager = PlatformMessageHistoryManager(reopened)
    restarted = AstrBotDashboard(core, reopened, asyncio.Event())
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted.asgi_app),
            base_url="http://testserver",
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"username": "original-admin", "password": PASSWORD},
            )
            identity = login.json()["data"]
            assert identity["account_id"] == account_id
            assert identity["resource_owner"] == "original-admin"
            assert identity["password_upgrade_required"] == (
                not bool(restored_config["dashboard"].get("pbkdf2_password"))
            )
            headers = {"Authorization": f"Bearer {identity['token']}"}
            history = await client.get(
                f"/api/v1/chat/sessions/{session_id}", headers=headers
            )
            assert "original backup history" in history.text
            projects = await client.get("/api/v1/chat/projects", headers=headers)
            assert project_id in projects.text
            routes = await client.get("/api/v1/config-routes", headers=headers)
            assert umo in routes.text and "default" in routes.text
    finally:
        await preferences.close()
        await reopened.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failure", "cancel"])
async def test_failed_restore_rolls_back_accounts_and_releases_migration_lock(
    account_dashboard, legacy_archive, tmp_path, monkeypatch, outcome
):
    """A failed or cancelled restore leaves the original identity usable."""
    server, core, db = account_dashboard
    archive, session_id, _, _, _ = legacy_archive
    cleared = asyncio.Event()
    release = asyncio.Event()

    async def interrupted_import(self, data, session=None):
        cleared.set()
        await release.wait()
        raise RuntimeError("Controlled restore failure")

    monkeypatch.setattr(AstrBotImporter, "_import_main_database", interrupted_import)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.asgi_app), base_url="http://testserver"
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "original-admin", "password": PASSWORD},
        )
        identity = login.json()["data"]
        importer = AstrBotImporter(db, config_path=core.astrbot_config.config_path)
        pending = asyncio.create_task(importer.import_all(str(archive)))
        await asyncio.wait_for(cleared.wait(), timeout=5)
        if outcome == "cancel":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            release.set()
            result = await asyncio.wait_for(pending, timeout=5)
            assert not result.success
        response = await asyncio.wait_for(
            client.get(
                f"/api/v1/chat/sessions/{session_id}",
                headers={"Authorization": f"Bearer {identity['token']}"},
            ),
            timeout=5,
        )
        assert "original backup history" in response.text
        assert not db.dashboard_account_lock.locked()
        account = await server.asgi_app.state.services.auth.accounts.ensure_admin()
        assert account.account_id == identity["account_id"]
        assert account.resource_owner == "original-admin"


@pytest.mark.asyncio
async def test_new_backup_preserves_persisted_identity_fields(
    account_dashboard, legacy_archive, tmp_path
):
    """Archives with account data retain their stable identities and state."""
    server, core, db = account_dashboard
    archive_path, _, _, _, _ = legacy_archive
    admin = await server.asgi_app.state.services.auth.accounts.ensure_admin()
    user = DashboardAccount(
        username="disabled-user",
        password_hash=admin.password_hash,
        role="user",
        enabled=False,
        token_version=7,
    )
    path = tmp_path / "accounts-backup.zip"
    with (
        zipfile.ZipFile(archive_path) as original,
        zipfile.ZipFile(path, "w") as updated,
    ):
        for entry in original.namelist():
            data = original.read(entry)
            if entry == "databases/main_db.json":
                tables = json.loads(data)
                tables["dashboard_accounts"] = [
                    account.model_dump(mode="json") for account in (admin, user)
                ]
                data = json.dumps(tables).encode()
            updated.writestr(entry, data)
    result = await AstrBotImporter(
        db, config_path=core.astrbot_config.config_path
    ).import_all(str(path))
    assert result.success
    async with db.get_db() as connection:
        restored = (await connection.execute(select(DashboardAccount))).scalars().all()
    assert {
        account.account_id: (
            account.username,
            account.legacy_owner,
            account.role,
            account.enabled,
            account.token_version,
        )
        for account in restored
    } == {
        account.account_id: (
            account.username,
            account.legacy_owner,
            account.role,
            account.enabled,
            account.token_version,
        )
        for account in (admin, user)
    }
