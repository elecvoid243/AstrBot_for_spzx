"""Caller-visible remote access contract against the production ASGI app/SQLite."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from astrbot.core.db.po import ChatUIProject, RemoteChatCredential, RemoteChatExposure
from tests.fixtures.dashboard_accounts import PASSWORD
from tests.test_multi_user_chat import (
    account_dashboard as account_dashboard,
)
from tests.test_multi_user_chat import (
    fake_core_lifecycle as fake_core_lifecycle,
)
from tests.test_multi_user_chat import (
    login,
)
from tests.test_multi_user_chat import (
    ordinary_chat as ordinary_chat,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [True, False])
async def test_remote_switch_persists_through_system_config_saves(
    ordinary_chat, initial
):
    client, server, core, db, admin, users = ordinary_chat
    headers = {"Authorization": f"Bearer {admin['token']}"}
    core.astrbot_config["chatui"]["remote_sessions_enabled"] = initial
    core.astrbot_config.save_config()
    credential = await client.post(
        "/api/v1/chat/remote-access/credentials",
        headers=headers,
        json={"name": "Settings test"},
    )
    remote = {"Authorization": f"RemoteSession {credential.json()['data']['key']}"}
    config = (await client.get("/api/v1/system-config", headers=headers)).json()[
        "data"
    ]["config"]
    config["chatui"]["remote_sessions_enabled"] = not initial
    for field, value in ((None, None), ("timezone", "UTC"), ("log_level", "DEBUG")):
        if field:
            config[field] = value
        response = await client.put(
            "/api/v1/system-config", headers=headers, json=config
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ok", response.text
        reloaded = (await client.get("/api/v1/system-config", headers=headers)).json()[
            "data"
        ]["config"]
        assert reloaded["chatui"]["remote_sessions_enabled"] is not initial
        stored = json.loads(
            Path(core.astrbot_config.config_path).read_text(encoding="utf-8-sig")
        )
        assert stored["chatui"]["remote_sessions_enabled"] is not initial
        if field:
            assert stored[field] == value
        gated = await client.get("/api/v1/remote-chat/sessions", headers=remote)
        assert gated.status_code == (403 if initial else 200), gated.text


@pytest.mark.asyncio
async def test_remote_discovery_exposure_and_complete_paged_history(ordinary_chat):
    client, server, core, db, admin, users = ordinary_chat
    admin_headers = {"Authorization": f"Bearer {admin['token']}"}
    headers = [{"Authorization": f"Bearer {user['token']}"} for user in users]
    sessions = []
    for owner_headers in (headers[0], headers[0], headers[1]):
        response = await client.get("/api/v1/chat/sessions/new", headers=owner_headers)
        assert response.status_code == 200, response.text
        sessions.append(response.json()["data"]["session_id"])
    first, hidden, foreign = sessions
    project = await client.post(
        "/api/v1/chat/projects", headers=headers[0], json={"title": "Shared project"}
    )
    project_id = project.json()["data"]["project_id"]
    for sid in (first, hidden):
        added = await client.post(
            f"/api/v1/chat/projects/{project_id}/sessions/{sid}", headers=headers[0]
        )
        assert added.status_code == 200, added.text
    secrets = []
    for owner_headers in headers:
        created = await client.post(
            "/api/v1/chat/remote-access/credentials",
            headers=owner_headers,
            json={"name": "Direct binding"},
        )
        assert created.status_code == 200, created.text
        assert created.headers["Cache-Control"] == "no-store"
        secrets.append(created.json()["data"])
    remote = {"Authorization": f"RemoteSession {secrets[0]['key']}"}
    second_remote = {"Authorization": f"RemoteSession {secrets[1]['key']}"}
    another_binding = await client.post(
        "/api/v1/chat/remote-access/credentials",
        headers=headers[0],
        json={"name": "Another direct binding"},
    )
    another_remote = {
        "Authorization": f"RemoteSession {another_binding.json()['data']['key']}"
    }
    assert core.astrbot_config["chatui"]["remote_sessions_enabled"] is False
    disabled = await client.get("/api/v1/remote-chat/sessions", headers=remote)
    assert disabled.status_code == 403
    denied = await client.put(
        "/api/v1/chat/remote-access/enabled", headers=headers[0], json={"enabled": True}
    )
    assert denied.status_code == 403
    enabled = await client.put(
        "/api/v1/chat/remote-access/enabled",
        headers=admin_headers,
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert (await client.get("/api/v1/remote-chat/sessions", headers=remote)).json()[
        "data"
    ] == []
    for owner_headers, mode, ids in (
        (headers[0], "selected", [first]),
        (headers[1], "all", []),
    ):
        configured = await client.put(
            "/api/v1/chat/remote-access/exposure",
            headers=owner_headers,
            json={"mode": mode, "session_ids": ids},
        )
        assert configured.status_code == 200, configured.text
    listing = await client.get("/api/v1/remote-chat/sessions", headers=remote)
    assert {item["session_id"] for item in listing.json()["data"]} == {first}
    listing = await client.get("/api/v1/remote-chat/sessions", headers=second_remote)
    assert {item["session_id"] for item in listing.json()["data"]} == {foreign}
    projects = await client.get("/api/v1/remote-chat/projects", headers=remote)
    assert projects.json()["data"] == [
        {"project_id": project_id, "title": "Shared project", "emoji": "📁"}
    ]
    project_sessions = await client.get(
        f"/api/v1/remote-chat/projects/{project_id}/sessions", headers=remote
    )
    assert {item["session_id"] for item in project_sessions.json()["data"]} == {first}
    for path in (
        f"/api/v1/remote-chat/sessions/{hidden}/history",
        f"/api/v1/remote-chat/sessions/{foreign}/history",
        "/api/v1/remote-chat/projects/guessed-id/sessions",
    ):
        assert (await client.get(path, headers=remote)).status_code == 404
    assert (
        await client.get(
            f"/api/v1/remote-chat/projects/{project_id}/sessions", headers=second_remote
        )
    ).status_code == 404
    # Full message text survives paging; another session's cursor cannot select it.
    text = "complete history " * 1000
    for i in range(5):
        await db.insert_platform_message_history(
            "webchat",
            first,
            {"type": "user", "message": [{"type": "text", "text": f"{i}:{text}"}]},
            sender_id=users[0]["resource_owner"],
            sender_name="First",
        )
    foreign_message = await db.insert_platform_message_history(
        "webchat",
        foreign,
        {"type": "user", "message": [{"type": "text", "text": "FOREIGN SECRET"}]},
    )
    records = []
    before = None
    while True:
        response = await client.get(
            f"/api/v1/remote-chat/sessions/{first}/history",
            headers=remote,
            params={"limit": 2, **({"before_id": before} if before else {})},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert set(data) == {"history", "has_more", "next_before_id"}
        records.extend(data["history"])
        if not data["has_more"]:
            break
        assert data["next_before_id"] != before
        before = data["next_before_id"]
    assert len(records) == len({record["id"] for record in records}) == 5
    assert {record["content"]["message"][0]["text"] for record in records} == {
        f"{i}:{text}" for i in range(5)
    }
    injected_cursor = await client.get(
        f"/api/v1/remote-chat/sessions/{first}/history",
        headers=remote,
        params={"before_id": foreign_message.id},
    )
    assert "FOREIGN SECRET" not in injected_cursor.text
    # All mode includes future local sessions and archived ordinary sessions.
    await client.put(
        "/api/v1/chat/remote-access/exposure", headers=headers[0], json={"mode": "all"}
    )
    fresh = await client.get("/api/v1/chat/sessions/new", headers=headers[0])
    fresh_id = fresh.json()["data"]["session_id"]
    listing = await client.get("/api/v1/remote-chat/sessions", headers=remote)
    assert {item["session_id"] for item in listing.json()["data"]} == {
        first,
        hidden,
        fresh_id,
    }
    # Updating exposure invalidates previously readable IDs immediately.
    await client.put(
        "/api/v1/chat/remote-access/exposure",
        headers=headers[0],
        json={"mode": "selected", "session_ids": [hidden]},
    )
    assert (
        await client.get(
            f"/api/v1/remote-chat/sessions/{first}/history", headers=remote
        )
    ).status_code == 404
    assert (
        await client.get(
            f"/api/v1/remote-chat/sessions/{hidden}/history", headers=remote
        )
    ).status_code == 200
    invalid_selection = await client.put(
        "/api/v1/chat/remote-access/exposure",
        headers=headers[0],
        json={"mode": "selected", "session_ids": [foreign]},
    )
    assert invalid_selection.status_code == 404
    # The stored verifier is distinct from plaintext; management never returns it.
    state = await client.get("/api/v1/chat/remote-access", headers=headers[0])
    assert secrets[0]["key"] not in state.text and "key_hash" not in state.text
    async with db.get_db() as connection:
        stored = await connection.get(RemoteChatCredential, secrets[0]["credential_id"])
        assert stored.account_id == users[0]["account_id"]
        assert stored.key_hash == hashlib.sha256(secrets[0]["key"].encode()).hexdigest()
    for method, path, payload in (
        (
            "DELETE",
            f"/api/v1/chat/remote-access/credentials/{secrets[0]['credential_id']}",
            None,
        ),
        (
            "PUT",
            "/api/v1/chat/remote-access/exposure",
            {"mode": "all", "account_id": users[0]["account_id"]},
        ),
    ):
        response = await client.request(method, path, headers=headers[1], json=payload)
        assert response.status_code in {404, 422}
    await client.put(
        "/api/v1/chat/remote-access/enabled",
        headers=admin_headers,
        json={"enabled": False},
    )
    assert (
        await client.get("/api/v1/remote-chat/sessions", headers=remote)
    ).status_code == 403
    assert (await client.get("/api/v1/chat/remote-access", headers=headers[0])).json()[
        "data"
    ]["session_ids"] == [hidden]
    await client.put(
        "/api/v1/chat/remote-access/enabled",
        headers=admin_headers,
        json={"enabled": True},
    )
    assert (
        await client.get("/api/v1/remote-chat/sessions", headers=remote)
    ).status_code == 200
    revoked = await client.delete(
        f"/api/v1/chat/remote-access/credentials/{secrets[0]['credential_id']}",
        headers=headers[0],
    )
    assert revoked.status_code == 200
    assert (
        await client.get("/api/v1/remote-chat/sessions", headers=remote)
    ).status_code == 401
    assert (
        await client.get("/api/v1/remote-chat/sessions", headers=second_remote)
    ).status_code == 200
    assert (
        await client.get("/api/v1/remote-chat/sessions", headers=another_remote)
    ).status_code == 200


@pytest.mark.asyncio
async def test_remote_discovers_exposed_sessions_in_more_than_100_projects(
    ordinary_chat,
):
    client, server, core, db, admin, users = ordinary_chat
    core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
    user_headers = {"Authorization": f"Bearer {users[0]['token']}"}
    async with db.get_db() as connection:
        for index in range(102):
            connection.add(
                ChatUIProject(
                    project_id=f"project-{index}",
                    creator=users[0]["resource_owner"],
                    title=f"Project {index}",
                    updated_at=datetime(2026, 1, 1) + timedelta(days=index),
                )
            )
        connection.add(
            ChatUIProject(
                project_id="foreign-project",
                creator=users[1]["resource_owner"],
                title="Foreign secret",
            )
        )
        await connection.commit()
    ids = []
    for project_id in ("project-0", "foreign-project"):
        session = await db.create_platform_session(creator=users[0]["resource_owner"])
        await db.add_session_to_project(session.session_id, project_id)
        ids.append(session.session_id)
    await client.put(
        "/api/v1/chat/remote-access/exposure",
        headers=user_headers,
        json={"mode": "all"},
    )
    credential = (
        await client.post(
            "/api/v1/chat/remote-access/credentials",
            headers=user_headers,
            json={"name": "Projects"},
        )
    ).json()["data"]
    remote = {"Authorization": f"RemoteSession {credential['key']}"}
    rows = (await client.get("/api/v1/remote-chat/sessions", headers=remote)).json()[
        "data"
    ]
    by_id = {item["session_id"]: item for item in rows}
    assert by_id[ids[0]]["project_id"] == "project-0"
    assert by_id[ids[0]]["project_name"] == "Project 0"
    assert by_id[ids[1]]["project_id"] is None
    projects = await client.get("/api/v1/remote-chat/projects", headers=remote)
    assert projects.json()["data"] == [
        {"project_id": "project-0", "title": "Project 0", "emoji": "📁"}
    ]
    result = await client.get(
        "/api/v1/remote-chat/projects/project-0/sessions", headers=remote
    )
    assert result.status_code == 200
    assert [row["session_id"] for row in result.json()["data"]] == [ids[0]]
    assert (
        await client.get(
            "/api/v1/remote-chat/projects/foreign-project/sessions", headers=remote
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_remote_uses_frozen_admin_owner_and_excludes_internal_sessions(
    ordinary_chat,
):
    client, server, core, db, admin, users = ordinary_chat
    core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
    headers = {"Authorization": f"Bearer {admin['token']}"}
    own = await db.create_platform_session(creator=admin["resource_owner"])
    await db.update_platform_session(own.session_id, archived=1)
    internal = await db.create_platform_session(
        creator=admin["resource_owner"], is_group=1
    )
    imported_remote = await db.create_platform_session(
        creator=admin["resource_owner"], platform_id="remote-webchat"
    )
    renamed = await client.patch(
        "/api/v1/auth/account",
        headers=headers,
        json={"password": PASSWORD, "new_username": "renamed-admin"},
    )
    assert renamed.status_code == 200, renamed.text
    identity = await login(client, "renamed-admin", PASSWORD)
    assert identity["resource_owner"] == admin["resource_owner"] == "original-admin"
    headers = {"Authorization": f"Bearer {identity['token']}"}
    await client.put(
        "/api/v1/chat/remote-access/exposure", headers=headers, json={"mode": "all"}
    )
    credential = (
        await client.post(
            "/api/v1/chat/remote-access/credentials",
            headers=headers,
            json={"name": "Admin binding"},
        )
    ).json()["data"]
    remote = {"Authorization": f"RemoteSession {credential['key']}"}
    listing = await client.get("/api/v1/remote-chat/sessions", headers=remote)
    assert [item["session_id"] for item in listing.json()["data"]] == [own.session_id]
    for sid in (internal.session_id, imported_remote.session_id):
        assert (
            await client.get(
                f"/api/v1/remote-chat/sessions/{sid}/history", headers=remote
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_remote_keys_never_fall_back_or_authorize_other_surfaces(ordinary_chat):
    client, server, core, db, admin, users = ordinary_chat
    admin_headers = {"Authorization": f"Bearer {admin['token']}"}
    core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
    global_key = await client.post(
        "/api/v1/api-keys",
        headers=admin_headers,
        json={"name": "Global", "scopes": ["chat"]},
    )
    assert global_key.status_code == 200, global_key.text
    raw_global = global_key.json()["data"]["api_key"]
    client.cookies.set("astrbot_dashboard_jwt", "expired-or-invalid")
    for headers in (
        admin_headers,
        {"X-API-Key": raw_global},
        {"Authorization": f"RemoteSession {raw_global}"},
        {"Authorization": "RemoteSession invalid"},
        {},
    ):
        response = await client.get("/api/v1/remote-chat/sessions", headers=headers)
        assert response.status_code == 401, response.text
    # A valid key is unaffected by stale ambient cookies or irrelevant API key headers.
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {users[0]['token']}"}
    credential = (
        await client.post(
            "/api/v1/chat/remote-access/credentials",
            headers=headers,
            json={"name": "Binding"},
        )
    ).json()["data"]
    remote = {"Authorization": f"RemoteSession {credential['key']}"}
    client.cookies.set("astrbot_dashboard_jwt", "expired-or-invalid")
    assert (
        await client.get(
            "/api/v1/remote-chat/sessions", headers={**remote, "X-API-Key": "unrelated"}
        )
    ).status_code == 200
    client.cookies.clear()
    for path in (
        "/api/v1/chat/sessions",
        "/api/v1/chat/remote-access",
        "/api/v1/chat/projects",
        "/api/v1/files/guessed",
    ):
        assert (await client.get(path, headers=remote)).status_code in {401, 403}
    # Global keys cannot manage credentials or exposure even with an ambient login.
    client.cookies.set("astrbot_dashboard_jwt", admin["token"])
    assert (
        await client.get("/api/v1/chat/remote-access", headers=remote)
    ).status_code == 401
    assert (
        await client.get(
            "/api/v1/chat/remote-access", headers={"X-API-Key": raw_global}
        )
    ).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["disable", "reset-password", "delete"])
async def test_account_invalidations_revoke_remote_access(ordinary_chat, operation):
    client, server, core, db, admin, users = ordinary_chat
    core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
    admin_headers = {"Authorization": f"Bearer {admin['token']}"}
    user_headers = {"Authorization": f"Bearer {users[0]['token']}"}
    await client.put(
        "/api/v1/chat/remote-access/exposure",
        headers=user_headers,
        json={"mode": "all"},
    )
    credential = (
        await client.post(
            "/api/v1/chat/remote-access/credentials",
            headers=user_headers,
            json={"name": "Binding"},
        )
    ).json()["data"]
    remote = {"Authorization": f"RemoteSession {credential['key']}"}
    assert (
        await client.get("/api/v1/remote-chat/sessions", headers=remote)
    ).status_code == 200
    account_path = f"/api/v1/accounts/{users[0]['account_id']}"
    if operation == "delete":
        response = await client.delete(account_path, headers=admin_headers)
    else:
        response = await client.post(
            f"{account_path}/{operation}",
            headers=admin_headers,
            json={"password": "NewPassword123!"}
            if operation == "reset-password"
            else None,
        )
    assert response.status_code == 200, response.text
    assert (
        await client.get("/api/v1/remote-chat/sessions", headers=remote)
    ).status_code == 401
    if operation == "disable":
        await client.post(f"{account_path}/enable", headers=admin_headers)
        assert (
            await client.get("/api/v1/remote-chat/sessions", headers=remote)
        ).status_code == 401
    if operation == "delete":
        async with db.get_db() as connection:
            assert (
                await connection.get(RemoteChatExposure, users[0]["account_id"]) is None
            )
            remaining = await connection.execute(
                select(RemoteChatCredential).where(
                    RemoteChatCredential.account_id == users[0]["account_id"]
                )
            )
            assert remaining.scalars().all() == []
