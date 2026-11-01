"""Account admission and first-turn chat through the production HTTP app."""

import asyncio
import json
from urllib.parse import urlencode
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlmodel import select

from astrbot.core import file_token_service, sp
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db.po import DashboardAccount
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.core.utils.webchat_access import webchat_resource_owner
from astrbot.dashboard.server import AstrBotDashboard
from tests.fixtures.dashboard_accounts import PASSWORD
from tests.fixtures.dashboard_accounts import account_dashboard as account_dashboard
from tests.fixtures.dashboard_accounts import fake_core_lifecycle as fake_core_lifecycle

USER_PASSWORD = "OrdinaryAccount123!"


async def login(client, username, password):
    """Authenticate through HTTP and return the server's identity envelope.

    Args:
        client: HTTP client connected to the production ASGI app.
        username: Login name.
        password: Plain login password.

    Returns:
        Account identity and signed credential issued by the server.
    """
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest_asyncio.fixture
async def ordinary_chat(account_dashboard, monkeypatch):
    """Create two ordinary accounts through the administrator API."""
    server, core, db = account_dashboard
    monkeypatch.setattr(sp, "db_helper", db)
    monkeypatch.setattr(sp, "_cache", {})
    monkeypatch.setattr(sp, "_cache_initialized", True)
    core.conversation_manager = ConversationManager(db)
    server.asgi_app.state.services.conversations.conv_mgr = core.conversation_manager
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.asgi_app), base_url="http://testserver"
    ) as client:
        admin = await login(client, "original-admin", PASSWORD)
        admin_headers = {"Authorization": f"Bearer {admin['token']}"}
        users = []
        for name in ("first-user", "second-user"):
            created = await client.post(
                "/api/v1/accounts",
                headers=admin_headers,
                json={"username": name, "password": USER_PASSWORD},
            )
            assert created.status_code == 200, created.text
            assert "password" not in created.text
            user = await login(client, name, USER_PASSWORD)
            assert (
                user["role"] == "user" and user["resource_owner"] == user["account_id"]
            )
            assert user["resource_owner"] != name
            users.append(user)
        client.cookies.clear()
        yield client, server, core, db, admin, users


@pytest.mark.asyncio
async def test_accounts_are_admin_only_unique_and_never_assign_roles(ordinary_chat):
    client, server, core, db, admin, users = ordinary_chat
    admin_headers = {"Authorization": f"Bearer {admin['token']}"}
    for name in ("first-user", "original-admin"):
        duplicate = await client.post(
            "/api/v1/accounts",
            headers=admin_headers,
            json={"username": name, "password": USER_PASSWORD},
        )
        assert duplicate.status_code == 409
    invalid = await client.post(
        "/api/v1/accounts",
        headers=admin_headers,
        json={
            "username": "forged",
            "password": USER_PASSWORD,
            "role": "admin",
            "legacy_owner": "original-admin",
        },
    )
    assert invalid.status_code == 422
    listing = await client.get("/api/v1/accounts", headers=admin_headers)
    assert {account["username"] for account in listing.json()["data"]} == {
        "first-user",
        "second-user",
    }
    assert "password_hash" not in listing.text and "token_version" not in listing.text
    headers = {"Authorization": f"Bearer {users[0]['token']}"}
    for method in ("GET", "POST"):
        denied = await client.request(
            method,
            "/api/v1/accounts",
            headers=headers,
            json={"username": "intruder", "password": USER_PASSWORD},
        )
        assert denied.status_code == 403
    async with db.get_db() as connection:
        assert (
            len((await connection.execute(select(DashboardAccount))).scalars().all())
            == 3
        )
    # Creating a login and renaming the admin share one lock, including config save.
    outcomes = await asyncio.gather(
        client.post(
            "/api/v1/accounts",
            headers=admin_headers,
            json={"username": "contended-login", "password": USER_PASSWORD},
        ),
        client.patch(
            "/api/v1/auth/account",
            headers=admin_headers,
            json={"password": PASSWORD, "new_username": "contended-login"},
        ),
    )
    async with db.get_db() as connection:
        bootstrap = (
            await connection.execute(
                select(DashboardAccount).where(
                    DashboardAccount.bootstrap_key == "dashboard"
                )
            )
        ).scalar_one()
    assert core.astrbot_config["dashboard"]["username"] == bootstrap.username
    assert sum(response.json()["status"] == "ok" for response in outcomes) == 1


@pytest.mark.asyncio
async def test_two_users_chat_reopen_and_cannot_exchange_sessions(
    ordinary_chat, tmp_path
):
    client, server, core, db, admin, users = ordinary_chat
    sessions = []
    for user in users:
        response = await client.get(
            "/api/v1/chat/sessions/new",
            headers={"Authorization": f"Bearer {user['token']}"},
        )
        assert response.status_code == 200, response.text
        sessions.append(response.json()["data"]["session_id"])
    # Even a configured admin sender ID cannot promote a Dashboard member.
    core.astrbot_config["admins_id"] = [user["resource_owner"] for user in users]
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    waking = WakingCheckStage()
    await waking.initialize(
        PipelineContext(core.astrbot_config, core.plugin_manager, "default", db)
    )

    download_tokens = []

    async def model_reply(index):
        sender, target, payload = await webchat_queue_mgr.get_or_create_queue(
            sessions[index]
        ).get()
        event = adapter.create_event(
            await adapter.convert_message((sender, target, payload))
        )

        async def generate_reply(current_event):
            assert not current_event.is_admin()
            assert (
                sender == users[index]["resource_owner"] and target == sessions[index]
            )
            assert webchat_resource_owner.get() == sender
            assert (
                current_event.unified_msg_origin
                == f"webchat:FriendMessage:webchat!{sender}!{target}"
            )
            generated_file = tmp_path / f"generated-{index}.txt"
            generated_file.write_text(
                f"private generated content {index}", encoding="utf-8"
            )
            download_tokens.append(
                await file_token_service.register_file(
                    str(generated_file), single_use=False
                )
            )
            back = webchat_queue_mgr.get_or_create_back_queue(
                payload["message_id"], target
            )
            await back.put(
                {
                    "type": "plain",
                    "data": f"private reply {index}",
                    "message_id": payload["message_id"],
                    "streaming": True,
                }
            )

        pipeline = PipelineScheduler(
            PipelineContext(core.astrbot_config, core.plugin_manager, "default", db)
        )
        pipeline.stages = [waking, SimpleNamespace(process=generate_reply)]
        await pipeline.execute(event)

    workers = [asyncio.create_task(model_reply(index)) for index in range(2)]
    replies = await asyncio.wait_for(
        asyncio.gather(
            *workers,
            *[
                client.post(
                    "/api/v1/chat" if index == 0 else "/api/chat/send",
                    headers={"Authorization": f"Bearer {user['token']}"},
                    json={
                        "session_id": sessions[index],
                        "message": f"private question {index}",
                    },
                )
                for index, user in enumerate(users)
            ],
        ),
        timeout=10,
    )
    replies = replies[2:]
    for index, (user, reply) in enumerate(zip(users, replies)):
        assert f"private reply {index}" in reply.text, reply.text
        assert f"private reply {1 - index}" not in reply.text
        headers = {"Authorization": f"Bearer {user['token']}"}
        listing = await client.get("/api/v1/chat/sessions", headers=headers)
        assert (
            sessions[index] in listing.text and sessions[1 - index] not in listing.text
        )
        history = await client.get(
            f"/api/v1/chat/sessions/{sessions[index]}", headers=headers
        )
        assert (
            f"private question {index}" in history.text
            and f"private reply {index}" in history.text
        )
        for caller in (users[1 - index], admin):
            foreign_headers = {"Authorization": f"Bearer {caller['token']}"}
            for path in (
                f"/api/v1/chat/sessions/{sessions[index]}",
                f"/api/chat/get_session?session_id={sessions[index]}",
            ):
                denied = await client.get(path, headers=foreign_headers)
                assert denied.json()["status"] == "error"
                assert (
                    "private question" not in denied.text
                    and "private reply" not in denied.text
                )
            denied = await client.post(
                "/api/v1/chat",
                headers=foreign_headers,
                json={"session_id": sessions[index], "message": "foreign mutation"},
            )
            assert denied.status_code >= 400 or denied.json()["status"] == "error"
        webchat_queue_mgr.remove_queues(sessions[index])
    assert webchat_resource_owner.get() is None
    for token in download_tokens:
        assert file_token_service.account_owners[token] in {
            user["resource_owner"] for user in users
        }
        for caller in [None, admin, *users]:
            headers = {"Authorization": f"Bearer {caller['token']}"} if caller else {}
            response = await client.get(
                f"/api/v1/files/tokens/{token}", headers=headers
            )
            assert response.status_code in {401, 403}
            assert "private generated content" not in response.text
    # Reopen both the actual DB and HTTP app; new login retains original resources.
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
            for index, user in enumerate(users):
                identity = await login(fresh, user["username"], USER_PASSWORD)
                history = await fresh.get(
                    f"/api/v1/chat/sessions/{sessions[index]}",
                    headers={"Authorization": f"Bearer {identity['token']}"},
                )
                assert f"private reply {index}" in history.text
                assert identity["account_id"] == user["account_id"]
    finally:
        await reopened.engine.dispose()


@pytest.mark.asyncio
async def test_unadapted_operations_default_deny_and_internal_fields_are_not_inputs(
    ordinary_chat,
    tmp_path,
):
    client, server, core, db, admin, users = ordinary_chat
    user = users[0]
    headers = {"Authorization": f"Bearer {user['token']}"}
    session_id = (
        await client.get("/api/v1/chat/sessions/new", headers=headers)
    ).json()["data"]["session_id"]
    for key, value in {
        "username": "original-admin",
        "owner": "original-admin",
        "role": "admin",
        "umo": "webchat:FriendMessage:webchat!original-admin!x",
        "_platform_history_id": "elsewhere",
        "_skip_user_history": True,
        "_api_key_allow_admin_role": True,
    }.items():
        for path in ("/api/v1/chat", "/api/chat/send"):
            response = await client.post(
                path,
                headers=headers,
                json={"session_id": session_id, "message": "injection", key: value},
            )
            assert response.status_code == 400, (path, response.text)
    for method, path in [
        ("GET", "/api/v1/bots"),
        ("GET", "/api/v1/providers"),
        ("GET", "/api/v1/config-profiles"),
        ("GET", "/api/v1/plugins"),
        ("GET", "/api/v1/mcp/servers"),
        ("GET", "/api/v1/logs"),
        ("GET", "/api/v1/backups"),
        ("GET", "/api/config/get"),
        ("POST", "/api/apikey/create"),
        ("POST", "/api/v1/api-keys"),
        ("POST", "/api/auth/setup"),
        ("PATCH", "/api/v1/auth/account"),
        ("GET", "/api/v1/chat/sessions/archived"),
        ("GET", "/api/v1/chat/projects"),
        ("GET", f"/api/v1/chat/sessions/{session_id}/system-stream"),
        ("GET", "/api/v1/chat/runs/fake/stream"),
        ("GET", "/api/v1/files/tokens/opaque"),
        ("GET", "/api/file/opaque"),
        ("POST", "/api/v1/files/upload/init"),
        ("POST", "/api/v1/chat/import/stage"),
        ("POST", "/api/v1/chat/import/confirm"),
        ("GET", "/api/skill-guide/active"),
        ("GET", "/api/plugin/extensions"),
        ("POST", "/api/v1/chat/ui-settings/value"),
        ("GET", "/api/v1/unknown-management"),
    ]:
        response = await client.request(method, path, headers=headers)
        assert response.status_code == 403, (path, response.text)
    # Cookie and URL JWTs must not bypass admission or live account state.
    client.cookies.set("astrbot_dashboard_jwt", user["token"])
    assert (await client.get("/api/v1/backups")).status_code == 403
    client.cookies.clear()
    assert (
        await client.get("/api/backup/download?token=" + user["token"])
    ).status_code == 403
    secret = tmp_path / "private-token.txt"
    secret.write_text("private token canary", encoding="utf-8")
    owner_token = webchat_resource_owner.set(admin["resource_owner"])
    try:
        token = await file_token_service.register_file(str(secret), single_use=False)
    finally:
        webchat_resource_owner.reset(owner_token)
    assert (await client.get(f"/api/v1/files/tokens/{token}")).status_code == 401
    assert (
        await client.get(f"/api/v1/files/tokens/{token}", headers=headers)
    ).status_code == 403
    allowed = await client.get(
        f"/api/v1/files/tokens/{token}",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert allowed.text == "private token canary"
    # Independent API-key/platform tokens preserve their bearer-capability flow.
    public_token = await file_token_service.register_file(str(secret))
    assert (
        await client.get(f"/api/v1/files/tokens/{public_token}")
    ).text == "private token canary"
    assert (
        await db.count_platform_message_history(
            platform_id="webchat", user_id=session_id
        )
        == 0
    )
    assert not server.asgi_app.state.services.chat.chat_runs


@pytest.mark.asyncio
async def test_admin_daily_conversations_are_owner_scoped_before_pagination(
    ordinary_chat,
):
    client, server, core, db, admin, users = ordinary_chat
    own = await db.create_platform_session(
        creator=admin["resource_owner"], platform_id="webchat"
    )
    foreign = await db.create_platform_session(
        creator=users[0]["resource_owner"], platform_id="webchat"
    )
    origins = [
        f"webchat:FriendMessage:webchat!{creator}!{session.session_id}"
        for creator, session in (
            (admin["resource_owner"], own),
            (users[0]["resource_owner"], foreign),
        )
    ]
    own_conversation = await db.create_conversation(
        user_id=origins[0],
        platform_id="webchat",
        content=[{"role": "user", "content": "own text"}],
    )
    foreign_conversation = await db.create_conversation(
        user_id=origins[1],
        platform_id="webchat",
        content=[{"role": "user", "content": "foreign canary"}],
    )
    qq = await db.create_conversation(
        user_id="qq:FriendMessage:qq-user", platform_id="qq", content=[]
    )
    headers = {"Authorization": f"Bearer {admin['token']}"}
    for path in (
        "/api/v1/conversations?page_size=1",
        "/api/conversation/list?page_size=1",
    ):
        response = await client.get(path, headers=headers)
        assert response.json()["data"]["pagination"]["total"] == 2, response.text
        assert "foreign canary" not in response.text
    for method, path, payload in [
        (
            "POST",
            "/api/conversation/detail",
            {"user_id": origins[1], "cid": foreign_conversation.conversation_id},
        ),
        (
            "POST",
            "/api/conversation/delete",
            {"user_id": origins[1], "cid": foreign_conversation.conversation_id},
        ),
        (
            "POST",
            "/api/conversation/update_history",
            {
                "user_id": origins[1],
                "cid": foreign_conversation.conversation_id,
                "history": [],
            },
        ),
        (
            "POST",
            "/api/session/update-rule",
            {"umo": origins[1], "rule_key": "session_service_config", "rule_value": {}},
        ),
    ]:
        response = await client.request(method, path, headers=headers, json=payload)
        assert response.status_code >= 400 or response.json()["status"] == "error", (
            response.text
        )
        assert "foreign canary" not in response.text
    mixed = await client.post(
        "/api/conversation/delete",
        headers=headers,
        json={
            "conversations": [
                {"user_id": origins[0], "cid": own_conversation.conversation_id},
                {"user_id": origins[1], "cid": foreign_conversation.conversation_id},
            ]
        },
    )
    assert mixed.status_code >= 400 or mixed.json()["status"] == "error"
    assert (
        await db.get_conversation_by_id(foreign_conversation.conversation_id)
    ).content[0]["content"] == "foreign canary"
    assert (
        await db.get_conversation_by_id(own_conversation.conversation_id)
    ) is not None
    assert (await db.get_conversation_by_id(qq.conversation_id)) is not None


@pytest.mark.asyncio
async def test_websocket_entry_points_deny_unadapted_users_and_admin_foreign_bind(ordinary_chat, monkeypatch):
    """Drive real ASGI WebSocket routes without replacing authentication."""
    client, server, core, db, admin, users = ordinary_chat
    monkeypatch.setattr(core.astrbot_config_mgr, "get_conf_list", lambda: [], raising=False)
    for path in ("/api/v1/live-chat/ws", "/api/v1/unified-chat/ws", "/api/live_chat/ws", "/api/unified_chat/ws", "/api/v1/chat/ws"):
        incoming = asyncio.Queue()
        outgoing = []
        await incoming.put({"type": "websocket.connect"})
        async def send(event):
            outgoing.append(event)
        task = asyncio.create_task(server.asgi_app({
            "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "root_path": "",
            "path": path, "raw_path": path.encode(),
            "query_string": urlencode({"token": users[0]["token"], "api_key": users[0]["token"]}).encode(),
            "headers": [], "client": ("127.0.0.1", 1234), "server": ("testserver", 80), "subprotocols": [],
        }, incoming.get, send))
        await asyncio.wait_for(task, timeout=10)
        assert any(event.get("type") == "websocket.close" and event["code"] == 1008 for event in outgoing), outgoing
        assert not any(event.get("type") == "websocket.send" and "private" in event.get("text", "") for event in outgoing)
    foreign = await db.create_platform_session(creator=users[0]["resource_owner"], platform_id="webchat")
    incoming = asyncio.Queue()
    outgoing = []
    for event in [
        {"type": "websocket.connect"},
        {"type": "websocket.receive", "text": json.dumps({"ct":"chat", "t":"bind", "session_id":foreign.session_id})},
        {"type": "websocket.disconnect", "code": 1000},
    ]:
        await incoming.put(event)
    async def send_admin(event):
        outgoing.append(event)
    await asyncio.wait_for(server.asgi_app({
        "type": "websocket", "asgi": {"version":"3.0"}, "scheme":"ws", "root_path":"",
        "path":"/api/v1/unified-chat/ws", "raw_path":b"/api/v1/unified-chat/ws",
        "query_string":urlencode({"token":admin["token"]}).encode(), "headers":[],
        "client":("127.0.0.1",1234), "server":("testserver",80), "subprotocols":[],
    }, incoming.get, send_admin), timeout=10)
    payloads = [json.loads(event["text"]) for event in outgoing if event["type"] == "websocket.send"]
    assert any(payload.get("code") == "PERMISSION_DENIED" for payload in payloads)
    assert not any(payload.get("type") == "session_bound" for payload in payloads)
    assert not server.asgi_app.state.services.live_chat.sessions
