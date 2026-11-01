import asyncio
import copy
from pathlib import Path

import httpx
import jwt
import pyotp
import pytest
from sqlmodel import select

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.db.po import DashboardAccount
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.core.utils.auth_password import hash_dashboard_password
from astrbot.dashboard.server import AstrBotDashboard
from astrbot.dashboard.services.live_chat_service import LiveChatAuthError
from tests.fixtures.dashboard_accounts import PASSWORD
from tests.fixtures.dashboard_accounts import account_dashboard as account_dashboard
from tests.fixtures.dashboard_accounts import fake_core_lifecycle as fake_core_lifecycle


@pytest.mark.asyncio
async def test_admin_upgrade_rename_and_restart_preserve_original_resources(
    account_dashboard, tmp_path
):
    """Exercise legacy data through login, rename, send and database reopen."""
    server, core, db = account_dashboard
    owner = "original-admin"
    session = await db.create_platform_session(creator=owner, platform_id="webchat")
    other = await db.create_platform_session(creator="unclaimed", platform_id="webchat")
    project = await db.create_chatui_project(creator=owner, title="Original project")
    attachment_path = tmp_path / "original.txt"
    attachment_path.write_text("original attachment", encoding="utf-8")
    attachment = await db.insert_attachment(str(attachment_path), "file", "text/plain")
    await db.insert_platform_message_history(
        platform_id="webchat",
        user_id=session.session_id,
        content={
            "type": "user",
            "message": [{"type": "file", "attachment_id": attachment.attachment_id}],
        },
        sender_id=owner,
        sender_name=owner,
    )
    umo = f"webchat:FriendMessage:webchat!{owner}!{session.session_id}"
    await core.umop_config_router.update_route(umo, "default")
    transport = httpx.ASGITransport(app=server.asgi_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": owner, "password": PASSWORD, "role": "user"},
        )
        identity = login.json()["data"]
        assert identity["role"] == "admin"
        assert identity["resource_owner"] == owner
        headers = {"Authorization": f"Bearer {identity['token']}"}
        before = await client.get(
            f"/api/v1/chat/sessions/{session.session_id}", headers=headers
        )
        assert attachment.attachment_id in before.text
        renamed = await client.patch(
            "/api/v1/auth/account",
            headers=headers,
            json={"password": PASSWORD, "new_username": "renamed-admin"},
        )
        assert renamed.json()["status"] == "ok"
        assert (
            await client.get("/api/v1/chat/sessions", headers=headers)
        ).status_code == 401
        login = await client.post(
            "/api/auth/login", json={"username": "renamed-admin", "password": PASSWORD}
        )
        current = login.json()["data"]
        assert current["account_id"] == identity["account_id"]
        assert current["resource_owner"] == owner
        headers = {"Authorization": f"Bearer {current['token']}"}
        for path in (
            f"/api/v1/chat/sessions/{session.session_id}",
            f"/api/chat/get_session?session_id={session.session_id}",
        ):
            response = await client.get(path, headers=headers)
            assert response.json()["status"] == "ok", response.text
            assert attachment.attachment_id in response.text
        projects = await client.get("/api/v1/chat/projects", headers=headers)
        assert project.project_id in projects.text
        assert (
            await client.get(
                f"/api/v1/chat/sessions/{other.session_id}", headers=headers
            )
        ).json()["status"] == "error"
        assert core.umop_config_router.umop_to_conf_id == {umo: "default"}

        async def model_reply():
            sender, target, message = await webchat_queue_mgr.get_or_create_queue(
                session.session_id
            ).get()
            assert sender == owner and target == session.session_id
            back = webchat_queue_mgr.get_or_create_back_queue(
                message["message_id"], target
            )
            await back.put(
                {
                    "type": "plain",
                    "data": "reply after rename",
                    "message_id": message["message_id"],
                    "streaming": True,
                }
            )
            await back.put({"type": "end", "message_id": message["message_id"]})

        worker = asyncio.create_task(model_reply())
        response = await asyncio.wait_for(
            client.post(
                "/api/chat/send",
                headers=headers,
                json={
                    "session_id": session.session_id,
                    "message": "hello after rename",
                },
            ),
            timeout=10,
        )
        await asyncio.wait_for(worker, timeout=10)
        assert "reply after rename" in response.text, response.text
        webchat_queue_mgr.remove_queues(session.session_id)
        saved = await db.get_platform_session_by_id(session.session_id)
        assert saved.creator == owner

    # Restart the HTTP app, configuration and database, then repeat migration.
    await db.engine.dispose()
    reopened = SQLiteDatabase(db.db_path)
    await reopened.initialize()
    reopened.inited = True
    core.astrbot_config = AstrBotConfig(core.astrbot_config.config_path)
    core.platform_message_history_manager = PlatformMessageHistoryManager(reopened)
    restarted = AstrBotDashboard(core, reopened, asyncio.Event())
    try:
        first = await restarted.asgi_app.state.services.auth.accounts.ensure_admin()
        second = await restarted.asgi_app.state.services.auth.accounts.ensure_admin()
        assert first.account_id == second.account_id == identity["account_id"]
        assert first.resource_owner == owner
        async with reopened.get_db() as connection:
            accounts = (
                (await connection.execute(select(DashboardAccount))).scalars().all()
            )
        assert len(accounts) == 1
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted.asgi_app),
            base_url="http://testserver",
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"username": "renamed-admin", "password": PASSWORD},
            )
            headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
            response = await client.get(
                f"/api/v1/chat/sessions/{session.session_id}", headers=headers
            )
            assert "hello after rename" in response.text
            assert "reply after rename" in response.text
            assert attachment.attachment_id in response.text
    finally:
        await reopened.engine.dispose()


@pytest.mark.asyncio
async def test_legacy_credentials_are_limited_and_account_state_is_authoritative(
    account_dashboard,
):
    """Reject arbitrary legacy identities, role claims, stale and disabled tokens."""
    server, core, db = account_dashboard
    app = server.asgi_app
    secret = core.astrbot_config["dashboard"]["jwt_secret"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for name, expected in (("original-admin", 200), ("unknown", 401)):
            token = jwt.encode(
                {"username": name, "role": "admin"}, secret, algorithm="HS256"
            )
            for path in ("/api/v1/chat/sessions", "/api/chat/sessions"):
                response = await client.get(
                    path, headers={"Authorization": f"Bearer {token}"}
                )
                assert response.status_code == expected, response.text
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "original-admin", "password": PASSWORD},
        )
        data = login.json()["data"]
        payload = jwt.decode(data["token"], secret, algorithms=["HS256"])
        async with db.get_db() as connection:
            account = await connection.get(DashboardAccount, data["account_id"])
            account.enabled = False
            connection.add(account)
            await connection.commit()
        for path in ("/api/v1/chat/sessions", "/api/chat/sessions"):
            response = await client.get(
                path, headers={"Authorization": f"Bearer {data['token']}"}
            )
            assert response.status_code == 401
        with pytest.raises(LiveChatAuthError, match="Invalid token"):
            await app.state.services.live_chat.authenticate_token(data["token"])
        async with db.get_db() as connection:
            account = await connection.get(DashboardAccount, data["account_id"])
            account.enabled = True
            account.token_version += 1
            connection.add(account)
            await connection.commit()
        response = await client.get(
            "/api/v1/chat/sessions",
            headers={"Authorization": f"Bearer {data['token']}"},
        )
        assert response.status_code == 401
        assert payload["sub"] == data["account_id"]


@pytest.mark.asyncio
async def test_migrated_admin_retains_totp_and_password_upgrade(account_dashboard):
    """Use real TOTP verification and the existing password security upgrade."""
    server, core, db = account_dashboard
    secret = pyotp.random_base32()
    core.astrbot_config["dashboard"]["totp"] = {
        "enable": True,
        "secret": secret,
        "recovery_code_hash": "test-recovery-hash",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.asgi_app), base_url="http://testserver"
    ) as client:
        payload = {"username": "original-admin", "password": PASSWORD}
        response = await client.post("/api/v1/auth/login", json=payload)
        assert response.status_code == 401
        assert response.json()["data"]["totp_required"]
        payload["code"] = pyotp.TOTP(secret).now()
        response = await client.post("/api/auth/login", json=payload)
        assert response.json()["status"] == "ok"
        data = response.json()["data"]
        assert data["role"] == "admin"
        assert data["resource_owner"] == "original-admin"


@pytest.mark.asyncio
@pytest.mark.parametrize("setup", [False, True])
async def test_conflicting_login_name_does_not_change_admin_credentials(
    account_dashboard, setup
):
    """Reject duplicate names before writing config or changing credentials."""
    server, core, db = account_dashboard
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.asgi_app), base_url="http://testserver"
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "original-admin", "password": PASSWORD},
        )
        data = login.json()["data"]
        async with db.get_db() as connection:
            connection.add(
                DashboardAccount(
                    username="taken-name",
                    password_hash=hash_dashboard_password("OtherPassword123!"),
                )
            )
            await connection.commit()
        core.astrbot_config["dashboard"]["password_change_required"] = setup
        core.astrbot_config.save_config()
        original = copy.deepcopy(dict(core.astrbot_config))
        config_path = Path(core.astrbot_config.config_path)
        original_file = config_path.read_bytes()
        if setup:
            path = "/api/v1/auth/setup"
            body = {
                "username": "taken-name",
                "password": "ReplacementPass123!",
                "confirm_password": "ReplacementPass123!",
            }
        else:
            path = "/api/auth/account/edit"
            body = {
                "password": PASSWORD,
                "new_username": "taken-name",
                "new_password": "ReplacementPass123!",
                "confirm_password": "ReplacementPass123!",
            }
        response = await client.post(
            path, json=body, headers={"Authorization": f"Bearer {data['token']}"}
        )
        assert response.json()["status"] == "error"
        assert core.astrbot_config == original
        assert config_path.read_bytes() == original_file
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "original-admin", "password": PASSWORD},
        )
        assert login.json()["status"] == "ok"
        assert login.json()["data"]["account_id"] == data["account_id"]


@pytest.mark.asyncio
async def test_password_reset_during_totp_cannot_issue_current_version_token(
    account_dashboard, monkeypatch
):
    """A login waiting on second-factor work retains its verified version."""
    from astrbot.dashboard.services import auth_service

    server, core, db = account_dashboard
    secret = pyotp.random_base32()
    core.astrbot_config["dashboard"]["totp"] = {
        "enable": True,
        "secret": secret,
        "recovery_code_hash": "test-recovery-hash",
    }
    entered = asyncio.Event()
    release = asyncio.Event()
    verify = auth_service.verify_configured_2fa_code

    async def delayed_verify(*args, **kwargs):
        entered.set()
        await release.wait()
        return await verify(*args, **kwargs)

    monkeypatch.setattr(auth_service, "verify_configured_2fa_code", delayed_verify)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.asgi_app), base_url="http://testserver"
    ) as client:
        pending = asyncio.create_task(
            client.post(
                "/api/v1/auth/login",
                json={
                    "username": "original-admin",
                    "password": PASSWORD,
                    "code": pyotp.TOTP(secret).now(),
                },
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        core.astrbot_config["dashboard"]["pbkdf2_password"] = hash_dashboard_password(
            "ResetPassword123!"
        )
        core.astrbot_config.save_config()
        current = await server.asgi_app.state.services.auth.accounts.ensure_admin()
        release.set()
        response = await asyncio.wait_for(pending, timeout=5)
        token = response.json()["data"]["token"]
        payload = jwt.decode(
            token, core.astrbot_config["dashboard"]["jwt_secret"], algorithms=["HS256"]
        )
        assert payload["ver"] < current.token_version
        for path in ("/api/v1/chat/sessions", "/api/chat/sessions"):
            protected = await client.get(
                path, headers={"Authorization": f"Bearer {token}"}
            )
            assert protected.status_code == 401
