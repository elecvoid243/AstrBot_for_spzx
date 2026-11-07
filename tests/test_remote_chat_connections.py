"""Direct outbound bindings over two isolated production ASGI/SQLite instances."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from sqlmodel import select

from astrbot.core.db.po import RemoteChatConnection
from astrbot.dashboard.services import remote_chat_service
from tests.fixtures.dashboard_accounts import PASSWORD
from tests.fixtures.dashboard_accounts import account_dashboard as dashboard_fixture
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


@pytest_asyncio.fixture
async def connected_instances(ordinary_chat, tmp_path):
    """Connect an isolated local service to an isolated remote production app."""
    client, server, core, db, admin, users = ordinary_chat
    remote_path = tmp_path / "remote"
    remote_path.mkdir()
    fixture = dashboard_fixture.__wrapped__(copy.copy(core), remote_path)
    remote_server, remote_core, remote_db = await anext(fixture)
    real_client = httpx.AsyncClient
    transport = httpx.ASGITransport(app=remote_server.asgi_app)
    async with real_client(transport=transport, base_url="http://remote") as remote:
        remote_admin = await login(remote, "original-admin", PASSWORD)
        remote_headers = {"Authorization": f"Bearer {remote_admin['token']}"}
        core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
        remote_core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
        credential = (
            await remote.post(
                "/api/v1/chat/remote-access/credentials",
                headers=remote_headers,
                json={"name": "Local binding"},
            )
        ).json()["data"]
        await remote.put(
            "/api/v1/chat/remote-access/exposure",
            headers=remote_headers,
            json={"mode": "all"},
        )
        observed = []

        async def forward(request):
            observed.append(request)
            return await transport.handle_async_request(request)

        proxy_httpx = SimpleNamespace(**vars(httpx))
        proxy_httpx.AsyncClient = lambda **kwargs: real_client(
            transport=httpx.MockTransport(forward), **kwargs
        )
        with patch.object(remote_chat_service, "httpx", proxy_httpx):
            yield (
                ordinary_chat,
                (
                    remote,
                    remote_server,
                    remote_core,
                    remote_db,
                    remote_admin,
                    credential,
                ),
                observed,
            )
    await fixture.aclose()


@pytest.mark.asyncio
async def test_account_bound_connections_paging_grants_and_local_login(
    connected_instances,
):
    local, remote_instance, observed = connected_instances
    client, server, core, db, admin, users = local
    remote, remote_server, remote_core, remote_db, remote_admin, credential = (
        remote_instance
    )
    headers = [{"Authorization": f"Bearer {user['token']}"} for user in users]
    remote_headers = {"Authorization": f"Bearer {remote_admin['token']}"}
    # The same remote instance bound twice is two distinct namespaces, never an imported local session.
    session = await remote_db.create_platform_session(
        creator=remote_admin["resource_owner"]
    )
    text = "complete untruncated history " * 1000
    for i in range(5):
        await remote_db.insert_platform_message_history(
            "webchat",
            session.session_id,
            {"type": "user", "message": [{"type": "text", "text": f"{i}:{text}"}]},
        )
    project = (
        await remote.post(
            "/api/v1/chat/projects",
            headers=remote_headers,
            json={"title": "Same project"},
        )
    ).json()["data"]["project_id"]
    await remote.post(
        f"/api/v1/chat/projects/{project}/sessions/{session.session_id}",
        headers=remote_headers,
    )
    ids = []
    for name in ("First machine", "Second machine"):
        response = await client.post(
            "/api/v1/chat/remote-connections",
            headers=headers[0],
            json={
                "name": name,
                "base_url": "http://192.168.1.20:6185/",
                "key": credential["key"],
            },
        )
        assert response.status_code == 200, response.text
        ids.append(response.json()["data"]["connection_id"])
        assert credential["key"] not in response.text
    listing = await client.get("/api/v1/chat/remote-connections", headers=headers[0])
    assert len(listing.json()["data"]["connections"]) == 2
    assert credential["key"] not in listing.text
    assert listing.headers["cache-control"] == "no-store"
    assert (
        await client.get("/api/v1/chat/remote-connections", headers=headers[1])
    ).json()["data"]["connections"] == []
    async with db.get_db() as connection:
        rows = (await connection.execute(select(RemoteChatConnection))).scalars().all()
        assert len(rows) == 2
        assert all(credential["key"] not in row.encrypted_key for row in rows)
    for identity in ids:
        path = f"/api/v1/chat/remote-connections/{identity}"
        rows = (await client.get(path + "/sessions", headers=headers[0])).json()["data"]
        assert (
            rows[0]["session_id"] == session.session_id
            and rows[0]["project_id"] == project
        )
        assert rows[0]["project_name"] == "Same project"
        assert (
            await client.get(path + "/sessions", headers=headers[1])
        ).status_code == 404
        assert (
            await client.put(
                path,
                headers=headers[1],
                json={"name": "stolen", "base_url": "http://remote"},
            )
        ).status_code == 404
        assert (await client.delete(path, headers=headers[1])).status_code == 404
        records, before = [], None
        while True:
            page = await client.get(
                path + f"/sessions/{session.session_id}/history",
                headers=headers[0],
                params={"limit": 2, **({"before_id": before} if before else {})},
            )
            assert page.status_code == 200, page.text
            data = page.json()["data"]
            records.extend(data["history"])
            if not data["has_more"]:
                break
            before = data["next_before_id"]
        assert len(records) == len({row["id"] for row in records}) == 5
        assert {row["content"]["message"][0]["text"] for row in records} == {
            f"{i}:{text}" for i in range(5)
        }
    assert all(
        request.headers["Authorization"] == "RemoteSession " + credential["key"]
        for request in observed
    )
    assert all("cookie" not in request.headers for request in observed)
    # Remote discovery never transits the remote account's own outbound bindings.
    await remote.post(
        "/api/v1/chat/remote-connections",
        headers=remote_headers,
        json={"name": "Third machine", "base_url": "http://third", "key": "arc_third"},
    )
    assert (
        len(
            (
                await client.get(
                    f"/api/v1/chat/remote-connections/{ids[0]}/sessions",
                    headers=headers[0],
                )
            ).json()["data"]
        )
        == 1
    )
    # Local off retains settings while rejecting management and outbound operations.
    core.astrbot_config["chatui"]["remote_sessions_enabled"] = False
    assert (
        len(
            (
                await client.get("/api/v1/chat/remote-connections", headers=headers[0])
            ).json()["data"]["connections"]
        )
        == 2
    )
    assert (
        await client.delete(
            f"/api/v1/chat/remote-connections/{ids[0]}", headers=headers[0]
        )
    ).status_code == 403
    assert (
        await client.get(
            f"/api/v1/chat/remote-connections/{ids[0]}/sessions", headers=headers[0]
        )
    ).status_code == 403
    core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
    remote_core.astrbot_config["chatui"]["remote_sessions_enabled"] = False
    response = await client.get(
        f"/api/v1/chat/remote-connections/{ids[0]}/sessions", headers=headers[0]
    )
    assert (
        response.status_code == 502
        and response.json()["data"]["code"] == "remote_disabled"
    )
    remote_core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
    await remote.put(
        "/api/v1/chat/remote-access/exposure",
        headers=remote_headers,
        json={"mode": "selected", "session_ids": []},
    )
    assert (
        await client.get(
            f"/api/v1/chat/remote-connections/{ids[0]}/sessions", headers=headers[0]
        )
    ).json()["data"] == []
    hidden = await client.get(
        f"/api/v1/chat/remote-connections/{ids[0]}/sessions/{session.session_id}/history",
        headers=headers[0],
    )
    assert (
        hidden.status_code == 502
        and hidden.json()["data"]["code"] == "remote_unavailable"
    )
    await remote.delete(
        f"/api/v1/chat/remote-access/credentials/{credential['credential_id']}",
        headers=remote_headers,
    )
    response = await client.get(
        f"/api/v1/chat/remote-connections/{ids[0]}/sessions", headers=headers[0]
    )
    assert (
        response.status_code == 502
        and response.json()["data"]["code"] == "remote_unauthorized"
    )
    assert (
        await client.get("/api/v1/chat/sessions", headers=headers[0])
    ).status_code == 200
    assert (
        await client.get(
            "/api/v1/chat/remote-connections",
            headers={"Authorization": "Bearer expired"},
        )
    ).status_code == 401
    # Deleting the local account removes only its bindings, with credentials/exposure too.
    deleted = await client.delete(
        f"/api/v1/accounts/{users[0]['account_id']}",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert deleted.status_code == 200, deleted.text
    async with db.get_db() as connection:
        assert (
            await connection.execute(select(RemoteChatConnection))
        ).scalars().all() == []


@pytest.mark.asyncio
async def test_validation_network_bounds_and_changed_connection(ordinary_chat):
    client, server, core, db, admin, users = ordinary_chat
    core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
    headers = {"Authorization": f"Bearer {users[0]['token']}"}
    for address in (
        "file:///tmp",
        "https://user:pass@remote",
        "http://remote/path",
        "http://remote?x=1",
        "http://remote/#x",
        "http://remote:99999",
        "http://remote\\evil",
        "http://remote/\n",
    ):
        response = await client.post(
            "/api/v1/chat/remote-connections",
            headers=headers,
            json={"name": "Invalid", "base_url": address, "key": "arc_test"},
        )
        assert response.status_code == 400, (address, response.text)
    identity = (
        await client.post(
            "/api/v1/chat/remote-connections",
            headers=headers,
            json={"name": "LAN", "base_url": "http://[::1]:6185", "key": "arc_test"},
        )
    ).json()["data"]["connection_id"]
    path = f"/api/v1/chat/remote-connections/{identity}"
    real_client = httpx.AsyncClient
    requests = []
    started, release = asyncio.Event(), asyncio.Event()
    mode = "redirect"

    async def handler(request):
        requests.append(request)
        if mode == "redirect":
            return httpx.Response(
                302, headers={"Location": "http://other-origin/steal"}
            )
        if mode == "offline":
            raise httpx.ConnectError("secret must not appear", request=request)
        if mode == "oversized":
            return httpx.Response(200, content=b"x" * (33 * 1024 * 1024))
        if mode == "timeout":
            raise httpx.ReadTimeout("bounded request", request=request)
        if mode == "unsupported":
            return httpx.Response(200, text="<html>legacy login</html>")
        started.set()
        await release.wait()
        return httpx.Response(200, json={"status": "ok", "data": []})

    proxy_httpx = SimpleNamespace(**vars(httpx))
    proxy_httpx.AsyncClient = lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs
    )
    with patch.object(remote_chat_service, "httpx", proxy_httpx):
        for mode, code in (
            ("redirect", "remote_protocol_error"),
            ("offline", "remote_offline"),
            ("unsupported", "remote_protocol_error"),
            ("oversized", "remote_protocol_error"),
            ("timeout", "remote_offline"),
        ):
            response = await client.get(path + "/sessions", headers=headers)
            assert (
                response.status_code == 502 and response.json()["data"]["code"] == code
            )
            assert "secret must not appear" not in response.text
        assert len(requests) == 5  # Never followed the redirect.
        mode = "delayed"
        pending = asyncio.create_task(client.get(path + "/sessions", headers=headers))
        await started.wait()
        await client.put(
            path,
            headers=headers,
            json={"name": "Rebound", "base_url": "http://new-origin", "key": "arc_new"},
        )
        release.set()
        response = await pending
        assert (
            response.status_code == 409
            and response.json()["data"]["code"] == "remote_unavailable"
        )

        started.clear()
        release.clear()
        pending = asyncio.create_task(client.get(path + "/sessions", headers=headers))
        await started.wait()
        await client.delete(path, headers=headers)
        release.set()
        response = await pending
        assert response.status_code == 409


@pytest.mark.asyncio
async def test_concurrent_rebinding_always_changes_revision(ordinary_chat, monkeypatch):
    """Make two HTTP saves read the same row before either commits."""
    from sqlalchemy.ext.asyncio import AsyncSession

    client, server, core, db, admin, users = ordinary_chat
    core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
    headers = {"Authorization": f"Bearer {users[0]['token']}"}
    created = await client.post(
        "/api/v1/chat/remote-connections",
        headers=headers,
        json={"name": "Initial", "base_url": "http://initial", "key": "arc_initial"},
    )
    identity = created.json()["data"]["connection_id"]
    original = AsyncSession.get
    first_read, both_read, allow_first, allow_second = (
        asyncio.Event() for _ in range(4)
    )
    reads = 0

    async def gated_get(session, entity, ident, **kwargs):
        nonlocal reads
        row = await original(session, entity, ident, **kwargs)
        if entity is RemoteChatConnection and ident == identity and reads < 2:
            reads += 1
            if reads == 1:
                first_read.set()
                await allow_first.wait()
            else:
                both_read.set()
                await allow_second.wait()
        return row

    monkeypatch.setattr(AsyncSession, "get", gated_get)
    path = f"/api/v1/chat/remote-connections/{identity}"
    first = asyncio.create_task(
        client.put(
            path, headers=headers, json={"name": "First", "base_url": "http://first"}
        )
    )
    await first_read.wait()
    second = asyncio.create_task(
        client.put(
            path,
            headers=headers,
            json={"name": "Second", "base_url": "http://second", "key": "arc_second"},
        )
    )
    await both_read.wait()
    allow_first.set()
    assert (await first).status_code == 200
    middle = (
        await client.get("/api/v1/chat/remote-connections", headers=headers)
    ).json()["data"]["connections"][0]
    allow_second.set()
    assert (await second).status_code == 200
    final = (
        await client.get("/api/v1/chat/remote-connections", headers=headers)
    ).json()["data"]["connections"][0]
    assert middle["base_url"] == "http://first" and final["base_url"] == "http://second"
    assert middle["revision"] != final["revision"]
