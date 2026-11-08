"""Session-scoped remote resources over two production applications and databases."""

import asyncio
import io
import time
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from PIL import Image

from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.tools.interactive_choice_registry import registry
from astrbot.dashboard.services.chat_service import build_webchat_unified_msg_origin
from tests.test_remote_chat_connections import (  # noqa: F401
    account_dashboard,
    fake_core_lifecycle,
    ordinary_chat,
)
from tests.test_remote_chat_connections import (
    connected_instances as connected_instances,
)


@pytest_asyncio.fixture
async def resources(connected_instances, tmp_path):
    """Bind one local account to a remote session with isolated attachment storage."""
    local, remote, observed = connected_instances
    client, server, core, db, admin, users = local
    target, target_server, _, target_db, target_admin, credential = remote
    for index, app in enumerate((server, target_server)):
        directory = tmp_path / str(index)
        directory.mkdir()
        app.asgi_app.state.services.chat.attachments_dir = str(directory)
    session = await target_db.create_platform_session(
        creator=target_admin["resource_owner"]
    )
    other = await target_db.create_platform_session(
        creator=target_admin["resource_owner"]
    )
    headers = {"Authorization": "Bearer " + users[0]["token"]}
    binding = (
        await client.post(
            "/api/v1/chat/remote-connections",
            headers=headers,
            json={
                "name": "Remote",
                "base_url": "http://remote",
                "key": credential["key"],
            },
        )
    ).json()["data"]["connection_id"]
    row = (await client.get("/api/v1/chat/remote-connections", headers=headers)).json()[
        "data"
    ]["connections"][0]
    path = f"/api/v1/chat/remote-connections/{binding}/sessions/{session.session_id}"
    yield local, remote, observed, session, other, headers, row, path


@pytest.mark.asyncio
async def test_ico_preview_mime_and_original_download_are_preserved(resources):
    local, remote, _, session, _, headers, binding, path = resources
    client = local[0]
    _, server, _, db, _, _ = remote
    image = io.BytesIO()
    Image.new("RGBA", (32, 32), (80, 140, 220, 255)).save(image, format="ICO")
    ico = image.getvalue()
    assert ico.startswith(b"\x00\x00\x01\x00")
    for index, mime in enumerate(
        ("image/x-icon", "image/vnd.microsoft.icon", "image/svg+xml")
    ):
        file = (
            Path(server.asgi_app.state.services.chat.attachments_dir)
            / f"preview-{index}.ico"
        )
        content = b"<svg onload='alert(1)'/>" if mime == "image/svg+xml" else ico
        file.write_bytes(content)
        attachment = await db.insert_attachment(str(file), "image", mime)
        await db.insert_platform_message_history(
            "webchat",
            session.session_id,
            {
                "type": "user",
                "message": [
                    {"type": "image", "attachment_id": attachment.attachment_id}
                ],
            },
        )
        response = await client.get(
            path + "/attachments/" + attachment.attachment_id,
            headers=headers,
            params={"revision": binding["revision"]},
        )
        assert response.status_code == 200, response.text
        assert response.content == content
        assert response.headers["content-type"] == (
            "application/octet-stream" if mime == "image/svg+xml" else mime
        )
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_upload_consumption_history_download_and_scope(resources):
    local, remote, observed, session, other, headers, row, path = resources
    client, server, _, local_db, _, users = local
    target, target_server, _, db, admin, credential = remote
    upload = await client.post(
        path + "/attachments",
        headers=headers,
        params={"revision": row["revision"]},
        files={"file": ("notes.txt", b"remote-only bytes", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    attachment_id = upload.json()["data"]["attachment_id"]
    attachment = await db.get_attachment_by_id(attachment_id)
    assert attachment.owner == admin["resource_owner"]
    assert await local_db.get_attachment_by_id(attachment_id) is None
    url = path + "/attachments/" + attachment_id
    assert (
        await client.get(url, headers=headers, params={"revision": row["revision"]})
    ).content == b"remote-only bytes"
    assert (
        await client.get(
            url,
            headers={"Authorization": "Bearer " + users[1]["token"]},
            params={"revision": row["revision"]},
        )
    ).status_code == 404
    other_url = url.replace(session.session_id, other.session_id)
    assert (
        await client.get(
            other_url, headers=headers, params={"revision": row["revision"]}
        )
    ).status_code == 502
    request_id = str(uuid4())
    body = {
        "text": "Read this",
        "attachment_ids": [attachment_id],
        "request_id": request_id,
        "revision": row["revision"],
    }
    sent = await client.post(path + "/messages", headers=headers, json=body)
    assert sent.json()["data"]["status"] == "accepted", sent.text
    queue = webchat_queue_mgr.get_or_create_queue(session.session_id)
    payload = (await queue.get())[2]
    file_part = next(part for part in payload["message"] if part["type"] == "file")
    assert file_part["attachment_id"] == attachment_id
    assert b"remote-only bytes" == Path(file_part["path"]).read_bytes()
    run_id = sent.json()["data"]["run_id"]
    await webchat_queue_mgr.get_or_create_back_queue(run_id, session.session_id).put(
        {"type": "end", "data": "", "message_id": run_id}
    )
    await asyncio.sleep(0.1)
    assert attachment_id in str(
        (await client.get(path + "/history", headers=headers)).json()["data"]
    )
    assert (await client.post(path + "/messages", headers=headers, json=body)).json()[
        "data"
    ]["status"] == "accepted"
    assert queue.empty()
    assert (
        await client.post(
            path + "/messages", headers=headers, json={**body, "attachment_ids": []}
        )
    ).status_code == 502
    # Rebinding invalidates old resource URLs and cannot leak a dedicated key.
    await client.put(
        f"/api/v1/chat/remote-connections/{row['connection_id']}",
        headers=headers,
        json={"name": "Changed", "base_url": "http://remote"},
    )
    assert (
        await client.get(url, headers=headers, params={"revision": row["revision"]})
    ).status_code == 409
    assert all(request.url.host == "remote" for request in observed)
    assert credential["key"] not in str(payload)


@pytest.mark.asyncio
async def test_existing_history_attachment_requires_exact_exposed_session(resources):
    local, remote, _, session, other, headers, row, path = resources
    client = local[0]
    target, server, _, db, admin, credential = remote
    file = Path(server.asgi_app.state.services.chat.attachments_dir) / "existing.txt"
    file.write_bytes(b"existing history")
    attachment = await db.insert_attachment(str(file), "file", "text/plain")
    # Merely knowing an ID, even on the same account, is insufficient.
    url = path + "/attachments/" + attachment.attachment_id
    assert (
        await client.get(url, headers=headers, params={"revision": row["revision"]})
    ).status_code == 502
    await db.insert_platform_message_history(
        "webchat",
        session.session_id,
        {
            "type": "bot",
            "message": [{"type": "file", "attachment_id": attachment.attachment_id}],
        },
    )
    assert (
        await client.get(url, headers=headers, params={"revision": row["revision"]})
    ).content == b"existing history"
    remote_headers = {"Authorization": "Bearer " + admin["token"]}
    await target.put(
        "/api/v1/chat/remote-access/exposure",
        headers=remote_headers,
        json={"mode": "selected", "session_ids": [other.session_id]},
    )
    assert (
        await client.get(url, headers=headers, params={"revision": row["revision"]})
    ).status_code == 502
    assert (
        await client.get("/api/v1/chat/remote-connections", headers=headers)
    ).status_code == 200


@pytest.mark.asyncio
async def test_human_choice_exact_origin_duplicate_expiry_and_revocation(resources):
    local, remote, _, session, other, headers, row, path = resources
    client = local[0]
    target, server, _, db, admin, credential = remote
    request_id = str(uuid4())
    future = asyncio.get_running_loop().create_future()
    registry.add(
        request_id,
        build_webchat_unified_msg_origin(session),
        future,
        {"prompt": "Choose", "options": []},
        time.time(),
        time.time() + 60,
    )
    try:
        suffix = "/human-choices/" + request_id
        body = {"revision": row["revision"], "choice_id": "A", "free_text": ""}
        assert (
            await client.post(
                path.replace(session.session_id, other.session_id) + suffix,
                headers=headers,
                json=body,
            )
        ).status_code == 404
        assert not future.done()
        assert (
            await client.post(
                path + suffix, headers=headers, json={**body, "is_human": True}
            )
        ).status_code == 422
        assert (
            await client.post(path + suffix, headers=headers, json=body)
        ).status_code == 200
        assert future.result() == {"choice_id": "A", "free_text": ""}
        assert (
            await client.post(path + suffix, headers=headers, json=body)
        ).status_code == 409
        registry.remove(request_id)
        future = asyncio.get_running_loop().create_future()
        registry.add(
            request_id,
            build_webchat_unified_msg_origin(session),
            future,
            {},
            time.time() - 20,
            time.time() - 1,
        )
        direct = f"/api/v1/remote-chat/sessions/{session.session_id}" + suffix
        assert (
            await target.post(
                direct,
                headers={"Authorization": "RemoteSession " + credential["key"]},
                json={"choice_id": "A"},
            )
        ).status_code == 409
        assert not future.done()
        await target.delete(
            "/api/v1/chat/remote-access/credentials/" + credential["credential_id"],
            headers={"Authorization": "Bearer " + admin["token"]},
        )
        assert (
            await client.post(path + suffix, headers=headers, json=body)
        ).status_code == 502
        assert not future.done()
    finally:
        registry.remove(request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["upload", "download", "choice"])
async def test_revoke_during_final_resource_check(resources, monkeypatch, operation):
    local, remote, _, session, _, _, _, _ = resources
    target, server, _, db, admin, credential = remote
    service = server.asgi_app.state.services.remote_chat
    path = f"/api/v1/remote-chat/sessions/{session.session_id}"
    headers = {"Authorization": "RemoteSession " + credential["key"]}
    upload = (
        await target.post(
            path + "/attachments",
            headers=headers,
            files={"file": ("file.txt", b"test", "text/plain")},
        )
    ).json()["data"]
    future = asyncio.get_running_loop().create_future()
    request_id = str(uuid4())
    registry.add(
        request_id,
        build_webchat_unified_msg_origin(session),
        future,
        {},
        time.time(),
        time.time() + 60,
    )
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.require_session
    calls = 0

    async def paused(*args, **kwargs):
        nonlocal calls
        await original(*args, **kwargs)
        calls += 1
        if calls == 2:
            entered.set()
            await release.wait()

    monkeypatch.setattr(service, "require_session", paused)
    if operation == "upload":
        task = asyncio.create_task(
            target.post(
                path + "/attachments",
                headers=headers,
                files={"file": ("file.txt", b"late", "text/plain")},
            )
        )
    elif operation == "download":
        task = asyncio.create_task(
            target.get(
                path + "/attachments/" + upload["attachment_id"], headers=headers
            )
        )
    else:
        task = asyncio.create_task(
            target.post(
                path + "/human-choices/" + request_id,
                headers=headers,
                json={"choice_id": "A"},
            )
        )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await target.delete(
            "/api/v1/chat/remote-access/credentials/" + credential["credential_id"],
            headers={"Authorization": "Bearer " + admin["token"]},
        )
        release.set()
        assert (await asyncio.wait_for(task, 3)).status_code == 403
        assert not future.done()
    finally:
        release.set()
        registry.remove(request_id)


@pytest.mark.asyncio
async def test_local_validation_is_definitively_rejected_without_outbound_io(resources):
    local, _, observed, _, _, headers, row, path = resources
    count = len(observed)
    response = await local[0].post(
        path + "/messages",
        headers=headers,
        json={
            "text": "Draft",
            "attachment_ids": [str(uuid4()) for _ in range(21)],
            "request_id": str(uuid4()),
            "revision": row["revision"],
        },
    )
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "remote_rejected"
    assert len(observed) == count


@pytest.mark.asyncio
async def test_image_upload_and_pending_upload_isolation_between_bindings(resources):
    import io

    from PIL import Image

    local, remote, _, session, _, headers, row, path = resources
    client = local[0]
    target, _, _, db, admin, credential = remote
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(image, format="PNG")
    response = await client.post(
        path + "/attachments",
        headers=headers,
        params={"revision": row["revision"]},
        files={"file": ("picture.png", image.getvalue(), "image/png")},
    )
    assert response.status_code == 200
    upload = response.json()["data"]
    assert upload["type"] == "image"
    url = path + "/attachments/" + upload["attachment_id"]
    preview = await client.get(
        url, headers=headers, params={"revision": row["revision"]}
    )
    assert preview.content == image.getvalue()
    assert preview.headers["content-type"].startswith("image/png")
    assert preview.headers["cache-control"] == "no-store"
    second_key = (
        await target.post(
            "/api/v1/chat/remote-access/credentials",
            headers={"Authorization": "Bearer " + admin["token"]},
            json={"name": "Second binding"},
        )
    ).json()["data"]["key"]
    binding = (
        await client.post(
            "/api/v1/chat/remote-connections",
            headers=headers,
            json={
                "name": "Other binding",
                "base_url": "http://remote",
                "key": second_key,
            },
        )
    ).json()["data"]["connection_id"]
    rows = (
        await client.get("/api/v1/chat/remote-connections", headers=headers)
    ).json()["data"]["connections"]
    revision = next(
        item["revision"] for item in rows if item["connection_id"] == binding
    )
    other_url = url.replace(row["connection_id"], binding)
    assert (
        await client.get(other_url, headers=headers, params={"revision": revision})
    ).status_code == 502
    wrong_send = await client.post(
        path.replace(row["connection_id"], binding) + "/messages",
        headers=headers,
        json={
            "attachment_ids": [upload["attachment_id"]],
            "request_id": str(uuid4()),
            "revision": revision,
        },
    )
    assert wrong_send.json()["data"]["status"] == "rejected"
    assert webchat_queue_mgr.get_or_create_queue(session.session_id).empty()
    # Once published into the exposed session, its ordinary history is shared.
    await db.insert_platform_message_history(
        "webchat",
        session.session_id,
        {
            "type": "bot",
            "message": [{"type": "image", "attachment_id": upload["attachment_id"]}],
        },
    )
    assert (
        await client.get(other_url, headers=headers, params={"revision": revision})
    ).content == image.getvalue()


@pytest.mark.asyncio
async def test_remote_ordinary_account_upload_owner_and_cross_account_denial(resources):
    from tests.test_multi_user_chat import USER_PASSWORD, login

    _, remote, _, admin_session, _, _, _, _ = resources
    client, _, _, db, admin, admin_key = remote
    admin_headers = {"Authorization": "Bearer " + admin["token"]}
    created = await client.post(
        "/api/v1/accounts",
        headers=admin_headers,
        json={"username": "remote-user", "password": USER_PASSWORD},
    )
    assert created.status_code == 200
    user = await login(client, "remote-user", USER_PASSWORD)
    headers = {"Authorization": "Bearer " + user["token"]}
    session = await db.create_platform_session(creator=user["resource_owner"])
    await client.put(
        "/api/v1/chat/remote-access/exposure", headers=headers, json={"mode": "all"}
    )
    credential = (
        await client.post(
            "/api/v1/chat/remote-access/credentials",
            headers=headers,
            json={"name": "User binding"},
        )
    ).json()["data"]
    dedicated = {"Authorization": "RemoteSession " + credential["key"]}
    path = f"/api/v1/remote-chat/sessions/{session.session_id}"
    upload = await client.post(
        path + "/attachments",
        headers=dedicated,
        files={"file": ("own.txt", b"owned", "text/plain")},
    )
    assert upload.status_code == 200
    attachment_id = upload.json()["data"]["attachment_id"]
    attachment = await db.get_attachment_by_id(attachment_id)
    assert attachment.owner == user["resource_owner"]
    foreign = f"/api/v1/remote-chat/sessions/{admin_session.session_id}/attachments/{attachment_id}"
    assert (
        await client.get(
            foreign, headers={"Authorization": "RemoteSession " + admin_key["key"]}
        )
    ).status_code == 404
    assert (await client.get(foreign, headers=dedicated)).status_code == 404
    send = await client.post(
        path + "/messages",
        headers=dedicated,
        json={"attachment_ids": [attachment_id], "request_id": str(uuid4())},
    )
    assert send.json()["data"]["status"] == "accepted"
    payload = (await webchat_queue_mgr.get_or_create_queue(session.session_id).get())[2]
    assert payload["_dashboard_account"].account_id == user["account_id"]
    assert payload["_dashboard_account"].role == "user"
    run_id = send.json()["data"]["run_id"]
    await webchat_queue_mgr.get_or_create_back_queue(run_id, session.session_id).put(
        {"type": "end", "data": "", "message_id": run_id}
    )


@pytest.mark.asyncio
async def test_created_selected_session_keeps_resource_and_human_choice_scope(
    resources,
):
    """Keep creation, resource access and human answers under one live exposure.

    Args:
        resources: Two production applications with account-owned direct bindings.
    """
    local, remote, _, _, _, headers, binding, _ = resources
    client = local[0]
    target, _, core, db, admin, _ = remote
    remote_headers = {"Authorization": "Bearer " + admin["token"]}
    await target.put(
        "/api/v1/chat/remote-access/exposure",
        headers=remote_headers,
        json={"mode": "selected", "session_ids": []},
    )
    core.astrbot_config["chatui"]["remote_session_creation_enabled"] = True
    base = "/api/v1/chat/remote-connections/" + binding["connection_id"]
    created = await client.post(
        base + "/sessions",
        headers=headers,
        json={
            "title": "Integrated session",
            "project_id": None,
            "revision": binding["revision"],
        },
    )
    assert created.status_code == 200, created.text
    sid = created.json()["data"]["session_id"]
    path = base + "/sessions/" + sid
    core.astrbot_config["chatui"]["remote_session_creation_enabled"] = False
    upload = await client.post(
        path + "/attachments",
        headers=headers,
        params={"revision": binding["revision"]},
        files={"file": ("created.txt", b"created session bytes", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    url = path + "/attachments/" + upload.json()["data"]["attachment_id"]
    assert (
        await client.get(url, headers=headers, params={"revision": binding["revision"]})
    ).content == b"created session bytes"
    session = await db.get_platform_session_by_id(sid)
    choice_id = str(uuid4())
    future = asyncio.get_running_loop().create_future()
    registry.add(
        choice_id,
        build_webchat_unified_msg_origin(session),
        future,
        {"prompt": "Approve created session"},
        time.time(),
        time.time() + 60,
    )
    try:
        # Agent disablement leaves the authorized human resource/answer path available.
        for instance in (local, remote):
            instance[2].astrbot_config["session_messages"]["enable"] = False
        answer = await client.post(
            path + "/human-choices/" + choice_id,
            headers=headers,
            json={"revision": binding["revision"], "choice_id": "A"},
        )
        assert answer.status_code == 200, answer.text
        assert future.result()["choice_id"] == "A"
        await target.put(
            "/api/v1/chat/remote-access/exposure",
            headers=remote_headers,
            json={"mode": "selected", "session_ids": []},
        )
        assert (
            await client.get(
                url, headers=headers, params={"revision": binding["revision"]}
            )
        ).status_code == 502
        assert (
            await client.get("/api/v1/chat/remote-connections", headers=headers)
        ).status_code == 200
    finally:
        registry.remove(choice_id)
