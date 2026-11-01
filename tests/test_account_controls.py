"""Ordinary-account revocation through the production Dashboard and SQLite."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from astrbot.core import file_token_service
from astrbot.core.db.po import DashboardAccount
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.core.utils.auth_password import verify_dashboard_password
from astrbot.core.utils.webchat_access import webchat_resource_owner
from astrbot.dashboard.server import AstrBotDashboard
from tests.test_multi_user_chat import (
    USER_PASSWORD,
    login,
)
from tests.test_multi_user_chat import (
    account_dashboard as account_dashboard,
)
from tests.test_multi_user_chat import (
    fake_core_lifecycle as fake_core_lifecycle,
)
from tests.test_multi_user_chat import (
    ordinary_chat as ordinary_chat,
)
from tests.test_multi_user_runtime import ASGIStream

NEW_PASSWORD = "ReplacementPassword123!"


async def send_echo(client, core, db, identity, session_id):
    """Replace only external model output while exercising the WebChat pipeline.

    Args:
        client: Production HTTP client.
        core: Dashboard core lifecycle.
        db: Temporary production database.
        identity: Logged-in account identity.
        session_id: Owned target session.

    Returns:
        HTTP response containing the streamed model reply.
    """

    async def model():
        incoming = await webchat_queue_mgr.get_or_create_queue(session_id).get()
        adapter = WebChatAdapter({}, {}, asyncio.Queue())
        event = adapter.create_event(await adapter.convert_message(incoming))

        async def reply(current_event):
            back = webchat_queue_mgr.get_or_create_back_queue(
                incoming[2]["message_id"], session_id
            )
            await back.put({"type": "plain", "data": "controlled private reply"})

        pipeline = PipelineScheduler(
            PipelineContext(core.astrbot_config, core.plugin_manager, "default", db)
        )
        pipeline.stages = [SimpleNamespace(process=reply)]
        await pipeline.execute(event)

    worker = asyncio.create_task(model())
    try:
        response, _ = await asyncio.wait_for(
            asyncio.gather(
                client.post(
                    "/api/v1/chat",
                    headers={"Authorization": f"Bearer {identity['token']}"},
                    json={"session_id": session_id, "message": "private question"},
                ),
                worker,
            ),
            timeout=10,
        )
        assert "controlled private reply" in response.text, response.text
        return response
    finally:
        if not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        webchat_queue_mgr.remove_queues(session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["reset-password", "disable"])
async def test_account_control_revokes_all_credentials_and_persists(
    ordinary_chat, tmp_path, operation
):
    client, server, core, db, admin, users = ordinary_chat
    sessions = []
    for identity in [*users, admin]:
        response = await client.get(
            "/api/v1/chat/sessions/new",
            headers={"Authorization": f"Bearer {identity['token']}"},
        )
        session_id = response.json()["data"]["session_id"]
        sessions.append(session_id)
        await send_echo(client, core, db, identity, session_id)

    private_file = tmp_path / "revocable.txt"
    private_file.write_text("protected download canary", encoding="utf-8")
    context = webchat_resource_owner.set(users[0]["resource_owner"])
    try:
        token = await file_token_service.register_file(
            str(private_file), single_use=False
        )
    finally:
        webchat_resource_owner.reset(context)
    paths = [
        "/api/v1/auth/session",
        "/api/v1/chat/sessions",
        "/api/chat/sessions",
        f"/api/v1/chat/sessions/{sessions[0]}/system-stream",
        "/api/v1/chat/runs/unavailable/stream",
        f"/api/v1/files/tokens/{token}",
        f"/api/file/{token}",
        "/api/v1/files/content?filename=revocable.txt",
        "/api/chat/get_file?filename=revocable.txt",
    ]
    old_headers = {"Authorization": f"Bearer {users[0]['token']}"}
    for path in paths:
        if path.endswith("/system-stream"):
            # The authorized event stream stays open; observe its headers and disconnect.
            async with ASGIStream(server.asgi_app, path, users[0]["token"]) as stream:
                assert stream.status == 200
            continue
        before = await client.get(path, headers=old_headers)
        assert before.status_code in {200, 403}, (path, before.text)
    admin_headers = {"Authorization": f"Bearer {admin['token']}"}
    changed = await client.post(
        f"/api/v1/accounts/{users[0]['account_id']}/{operation}",
        headers=admin_headers,
        **(
            {"json": {"password": NEW_PASSWORD}}
            if operation == "reset-password"
            else {}
        ),
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["enabled"] == (operation == "reset-password")
    assert "password_hash" not in changed.text and "token_version" not in changed.text
    history_count = await db.count_platform_message_history(
        platform_id="webchat", user_id=sessions[0]
    )
    for cookie in [False, True]:
        client.cookies.clear()
        headers = {} if cookie else old_headers
        if cookie:
            client.cookies.set("astrbot_dashboard_jwt", users[0]["token"])
        for path in paths:
            denied = await client.get(path, headers=headers)
            assert denied.status_code == 401, (cookie, path, denied.text)
            assert "protected download canary" not in denied.text
            assert "private question" not in denied.text
        for path in ["/api/v1/chat", "/api/chat/send"]:
            denied = await client.post(
                path,
                headers=headers,
                json={"session_id": sessions[0], "message": "revoked mutation"},
            )
            assert denied.status_code == 401
    client.cookies.clear()
    assert (
        await db.count_platform_message_history(
            platform_id="webchat", user_id=sessions[0]
        )
        == history_count
    )
    for path in ["/api/v1/auth/login", "/api/auth/login"]:
        denied = await client.post(
            path, json={"username": users[0]["username"], "password": USER_PASSWORD}
        )
        assert denied.status_code == 401
    for identity, session_id in zip([users[1], admin], sessions[1:]):
        assert (
            await client.get(
                "/api/v1/auth/session",
                headers={"Authorization": f"Bearer {identity['token']}"},
            )
        ).status_code == 200
        await send_echo(client, core, db, identity, session_id)

    await db.engine.dispose()
    reopened = SQLiteDatabase(db.db_path)
    await reopened.initialize()
    reopened.inited = True
    core.platform_message_history_manager = PlatformMessageHistoryManager(reopened)
    restarted = AstrBotDashboard(core, reopened, asyncio.Event())
    restarted.app.testing = True
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted.asgi_app),
            base_url="http://testserver",
        ) as fresh:
            assert (
                await fresh.get("/api/v1/auth/session", headers=old_headers)
            ).status_code == 401
            if operation == "reset-password":
                identity = await login(fresh, users[0]["username"], NEW_PASSWORD)
                assert identity["account_id"] == users[0]["account_id"]
                history = await fresh.get(
                    f"/api/v1/chat/sessions/{sessions[0]}",
                    headers={"Authorization": f"Bearer {identity['token']}"},
                )
                assert "controlled private reply" in history.text
                await send_echo(fresh, core, reopened, identity, sessions[0])
            else:
                assert (
                    await fresh.post(
                        "/api/auth/login",
                        json={
                            "username": users[0]["username"],
                            "password": USER_PASSWORD,
                        },
                    )
                ).status_code == 401
            for identity in [users[1], admin]:
                assert (
                    await fresh.get(
                        "/api/v1/auth/session",
                        headers={"Authorization": f"Bearer {identity['token']}"},
                    )
                ).status_code == 200
    finally:
        await reopened.engine.dispose()


@pytest.mark.asyncio
async def test_account_controls_reject_escalation_and_serialize_updates(ordinary_chat):
    client, server, core, db, admin, users = ordinary_chat
    admin_headers = {"Authorization": f"Bearer {admin['token']}"}
    target = users[0]["account_id"]
    for operation in ["reset-password", "disable"]:
        for caller in [None, *users]:
            response = await client.post(
                f"/api/v1/accounts/{target}/{operation}",
                headers={"Authorization": f"Bearer {caller['token']}"}
                if caller
                else {},
                json={"password": NEW_PASSWORD},
            )
            assert response.status_code == (403 if caller else 401)
        for invalid in [admin["account_id"], "missing-account"]:
            response = await client.post(
                f"/api/v1/accounts/{invalid}/{operation}",
                headers=admin_headers,
                json={"password": NEW_PASSWORD},
            )
            assert response.status_code == 404
    for body, expected in [
        ({"password": "weak"}, 400),
        ({"password": NEW_PASSWORD, "role": "admin"}, 422),
        ({"password": NEW_PASSWORD, "enabled": True}, 422),
    ]:
        response = await client.post(
            f"/api/v1/accounts/{target}/reset-password",
            headers=admin_headers,
            json=body,
        )
        assert response.status_code == expected
    async with db.get_db() as session:
        unchanged = await session.get(DashboardAccount, target)
        assert (
            unchanged.enabled
            and unchanged.role == "user"
            and unchanged.token_version == 0
        )
        assert verify_dashboard_password(unchanged.password_hash, USER_PASSWORD)
    results = await asyncio.gather(
        client.post(
            f"/api/v1/accounts/{target}/reset-password",
            headers=admin_headers,
            json={"password": NEW_PASSWORD},
        ),
        client.post(f"/api/v1/accounts/{target}/disable", headers=admin_headers),
    )
    assert all(response.status_code == 200 for response in results)
    repeated = await client.post(
        f"/api/v1/accounts/{target}/disable", headers=admin_headers
    )
    assert repeated.status_code == 200
    async with db.get_db() as session:
        account = await session.get(DashboardAccount, target)
        assert (
            not account.enabled
            and account.role == "user"
            and account.token_version == 2
        )
        assert verify_dashboard_password(account.password_hash, NEW_PASSWORD)
    reset_disabled = await client.post(
        f"/api/v1/accounts/{target}/reset-password",
        headers=admin_headers,
        json={"password": NEW_PASSWORD},
    )
    assert reset_disabled.status_code == 200
    assert reset_disabled.json()["data"]["enabled"] is False
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": users[0]["username"], "password": NEW_PASSWORD},
        )
    ).status_code == 401
