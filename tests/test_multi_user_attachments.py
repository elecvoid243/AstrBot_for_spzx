"""Attachment consumption through real account authentication and SQLite."""

import asyncio
import sqlite3
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from PIL import Image as PILImage
from sqlalchemy import event
from sqlmodel import select

from astrbot.core import file_token_service
from astrbot.core.db import sqlite as sqlite_module
from astrbot.core.db.po import Attachment, DashboardAccount
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.message.components import File, Image, Record, Video
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.webchat import webchat_adapter, webchat_event
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.utils.webchat_access import webchat_resource_owner
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat


@pytest_asyncio.fixture
async def attachment_chat(ordinary_chat, tmp_path, monkeypatch):
    """Isolate real attachment storage and stop the chunk expiry worker."""
    client, server, core, db, admin, users = ordinary_chat
    service = server.asgi_app.state.services.chat
    from astrbot.dashboard.services.chunked_upload_service import ChunkedUploadService

    for name in ("attachments_dir", "webchat_img_dir"):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setattr(service, name, str(path))
    monkeypatch.setattr(
        service, "chunked_uploads", ChunkedUploadService(tmp_path / "chunks")
    )
    yield client, server, core, db, admin, users
    task = service.chunked_uploads._cleanup_task
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,mime",
    [
        ("image", "image/png"),
        ("record", "audio/wav"),
        ("video", "video/mp4"),
        ("file", "application/pdf"),
    ],
)
async def test_upload_send_preview_download_and_foreign_reference(
    attachment_chat, kind, mime
):
    client, server, core, db, admin, users = attachment_chat
    for account in [admin, *users]:
        headers = {"Authorization": f"Bearer {account['token']}"}
        created = await client.get("/api/v1/chat/sessions/new", headers=headers)
        session_id = created.json()["data"]["session_id"]
        response = await client.post(
            "/api/v1/files",
            headers=headers,
            files={"file": ("private.bin", b"private attachment", mime)},
        )
        assert response.status_code == 200, response.text
        attachment = response.json()["data"]
        assert attachment["type"] == kind
        row = await db.get_attachment_by_id(attachment["attachment_id"])
        assert row.owner == account["resource_owner"]
        for url in [
            f"/api/v1/files/{row.attachment_id}/content",
            f"/api/v1/files/content?filename={attachment['stored_filename']}",
            f"/api/chat/get_attachment?attachment_id={row.attachment_id}",
            f"/api/chat/get_file?filename={attachment['stored_filename']}",
        ]:
            own = await client.get(url, headers=headers)
            assert own.content == b"private attachment", own.text
            for other in [admin, *users]:
                if other is account:
                    continue
                denied = await client.get(
                    url, headers={"Authorization": f"Bearer {other['token']}"}
                )
                assert b"private attachment" not in denied.content
                assert denied.status_code >= 400 or denied.json()["status"] == "error"
        foreign = users[1] if account is users[0] else users[0]
        foreign_headers = {"Authorization": f"Bearer {foreign['token']}"}
        foreign_session = (
            await client.get("/api/v1/chat/sessions/new", headers=foreign_headers)
        ).json()["data"]["session_id"]
        denied = await client.post(
            "/api/v1/chat",
            headers=foreign_headers,
            json={
                "session_id": foreign_session,
                "message": [{"type": kind, "attachment_id": row.attachment_id}],
            },
        )
        assert denied.json()["status"] == "error", denied.text
        assert await db.count_platform_message_history("webchat", foreign_session) == 0
        assert Path(row.path).read_bytes() == b"private attachment"

        async def generate():
            _, _, payload = await webchat_queue_mgr.get_or_create_queue(
                session_id
            ).get()
            assert payload["message"][0]["attachment_id"] == row.attachment_id
            assert (
                Path(payload["message"][0]["path"]).read_bytes()
                == b"private attachment"
            )
            await webchat_queue_mgr.put_back_queue(
                payload["message_id"],
                {"type": "plain", "data": "received", "streaming": True},
            )
            output = (
                Path(server.asgi_app.state.services.chat.attachments_dir)
                / f"output-{session_id}.txt"
            )
            output.write_bytes(b"private generated attachment")
            await webchat_queue_mgr.put_back_queue(
                payload["message_id"],
                {
                    "type": "file",
                    "data": f"[FILE]{output.name}|reply.txt",
                    "streaming": True,
                },
            )
            await webchat_queue_mgr.put_back_queue(
                payload["message_id"], {"type": "end", "data": ""}
            )

        worker = asyncio.create_task(generate())
        sent = await asyncio.wait_for(
            client.post(
                "/api/v1/chat",
                headers=headers,
                json={
                    "session_id": session_id,
                    "message": [
                        {
                            "type": kind,
                            "attachment_id": row.attachment_id,
                            "filename": "private.bin",
                        }
                    ],
                },
            ),
            timeout=8,
        )
        await worker
        assert "received" in sent.text
        history = await client.get(
            f"/api/v1/chat/sessions/{session_id}", headers=headers
        )
        assert row.attachment_id in history.text
        async with db.get_db() as connection:
            output_row = (
                await connection.execute(
                    select(Attachment).where(
                        Attachment.path
                        == str(
                            Path(server.asgi_app.state.services.chat.attachments_dir)
                            / f"output-{session_id}.txt"
                        )
                    )
                )
            ).scalar_one()
        assert output_row.owner == account["resource_owner"]
        downloaded = await client.get(
            f"/api/v1/files/{output_row.attachment_id}/content", headers=headers
        )
        assert downloaded.content == b"private generated attachment"


@pytest.mark.asyncio
async def test_chunk_sessions_staging_cleanup_and_legacy_upload(attachment_chat):
    client, server, core, db, admin, users = attachment_chat
    first = {"Authorization": f"Bearer {users[0]['token']}"}
    init = await client.post(
        "/api/v1/files/upload/init",
        headers=first,
        json={"filename": "chunk.txt", "total_size": 6},
    )
    upload_id = init.json()["data"]["upload_id"]
    for other in [admin, users[1]]:
        headers = {"Authorization": f"Bearer {other['token']}"}
        chunk = await client.post(
            "/api/v1/files/upload/chunk",
            headers=headers,
            data={"upload_id": upload_id, "chunk_index": "0"},
            files={"chunk": ("c", b"attack")},
        )
        assert chunk.json()["status"] == "error"
        for operation in ("status", "complete", "abort"):
            denied = await client.post(
                f"/api/v1/files/upload/{operation}",
                headers=headers,
                json={"upload_id": upload_id},
            )
            assert denied.json()["status"] == "error"
    await client.post(
        "/api/v1/files/upload/chunk",
        headers=first,
        data={"upload_id": upload_id, "chunk_index": "0"},
        files={"chunk": ("c", b"secret")},
    )
    status = await client.post(
        "/api/v1/files/upload/status", headers=first, json={"upload_id": upload_id}
    )
    assert status.json()["data"]["received_chunks"] == [0]
    complete = await client.post(
        "/api/v1/files/upload/complete", headers=first, json={"upload_id": upload_id}
    )
    row = await db.get_attachment_by_id(complete.json()["data"]["attachment_id"])
    assert row.owner == users[0]["resource_owner"]
    assert Path(row.path).read_bytes() == b"secret"
    assert not server.asgi_app.state.services.chat.chunked_uploads.sessions
    legacy = await client.post(
        "/api/chat/post_file",
        headers=first,
        files={"file": ("old.txt", b"legacy upload")},
    )
    assert legacy.json()["status"] == "ok", legacy.text


@pytest.mark.asyncio
async def test_legacy_files_tokens_and_explicit_cascade_owner(attachment_chat):
    client, server, core, db, admin, users = attachment_chat
    service = server.asgi_app.state.services.chat
    legacy_path = Path(service.attachments_dir) / "old.txt"
    legacy_path.write_bytes(b"old administrator data")
    legacy = await db.insert_attachment(str(legacy_path), "file", "text/plain")
    uploaded = []
    for account in users:
        response = await client.post(
            "/api/v1/files",
            headers={"Authorization": f"Bearer {account['token']}"},
            files={"file": ("owned.txt", account["resource_owner"].encode())},
        )
        uploaded.append(
            await db.get_attachment_by_id(response.json()["data"]["attachment_id"])
        )
    for account in [admin, *users]:
        response = await client.get(
            f"/api/v1/files/{legacy.attachment_id}/content",
            headers={"Authorization": f"Bearer {account['token']}"},
        )
        assert (response.content == b"old administrator data") == (account is admin)
    context = webchat_resource_owner.set(users[0]["resource_owner"])
    try:
        token = await file_token_service.register_file(uploaded[0].path)
    finally:
        webchat_resource_owner.reset(context)
    for account in [admin, users[1]]:
        denied = await client.get(
            f"/api/v1/files/tokens/{token}",
            headers={"Authorization": f"Bearer {account['token']}"},
        )
        assert denied.status_code == 403
    downloaded = await client.get(
        f"/api/file/{token}", headers={"Authorization": f"Bearer {users[0]['token']}"}
    )
    assert downloaded.content == users[0]["resource_owner"].encode()
    await service.delete_attachments(
        [legacy.attachment_id, *(item.attachment_id for item in uploaded)],
        owner=users[0]["resource_owner"],
    )
    assert legacy_path.exists()
    assert not Path(uploaded[0].path).exists()
    assert Path(uploaded[1].path).exists()
    assert await db.get_attachment_by_id(uploaded[1].attachment_id) is not None
    for value in ("../old.txt", str(legacy_path), "..\\old.txt"):
        response = await client.get(
            "/api/v1/files/content",
            params={"filename": value},
            headers={"Authorization": f"Bearer {users[1]['token']}"},
        )
        assert b"old administrator data" not in response.content


@pytest.mark.asyncio
async def test_live_input_and_output_reuse_owned_storage(attachment_chat, tmp_path):
    client, server, core, db, admin, users = attachment_chat
    live = server.asgi_app.state.services.live_chat
    service = server.asgi_app.state.services.chat
    live.attachments_dir = service.attachments_dir
    live.webchat_img_dir = service.webchat_img_dir
    voice = tmp_path / "input.wav"
    voice.write_bytes(b"test voice")
    core.plugin_manager.context.provider_manager = SimpleNamespace(
        stt_provider_insts=[
            SimpleNamespace(
                get_text=AsyncMock(return_value="voice input"),
                meta=lambda: SimpleNamespace(type="controlled-stt"),
            )
        ]
    )
    session = live.create_session(users[0]["resource_owner"])
    replies = []

    async def generate():
        _, _, payload = await webchat_queue_mgr.get_or_create_queue(
            session.conversation_id
        ).get()
        streamed = Path(service.attachments_dir) / "streamed.wav"
        streamed.write_bytes(b"generated live voice")
        await webchat_queue_mgr.put_back_queue(
            payload["message_id"],
            {
                "type": "record",
                "message_id": payload["message_id"],
                "data": f"[RECORD]{streamed.name}",
            },
        )
        await webchat_queue_mgr.put_back_queue(
            payload["message_id"],
            {"type": "end", "message_id": payload["message_id"], "data": ""},
        )

    worker = asyncio.create_task(generate())
    await asyncio.wait_for(
        live.process_audio(
            session,
            str(voice),
            0,
            AsyncMock(side_effect=lambda message: replies.append(message)),
        ),
        timeout=8,
    )
    await worker
    async with db.get_db() as connection:
        records = (await connection.execute(select(Attachment))).scalars().all()
    assert len(records) == 2
    assert all(row.owner == users[0]["resource_owner"] for row in records)
    assert any(reply["t"] == "attachment" for reply in replies)
    output = Path(service.attachments_dir) / "output.wav"
    output.write_bytes(b"generated voice")
    part = await live.create_attachment_from_file(
        output.name, "record", owner=users[0]["resource_owner"]
    )
    assert (await db.get_attachment_by_id(part["attachment_id"])).owner == users[0][
        "resource_owner"
    ]
    with pytest.raises(Exception, match="Attachment is unavailable"):
        await live.build_chat_message_parts([part], owner=users[1]["resource_owner"])


@pytest.mark.asyncio
async def test_proactive_orphan_and_database_reopen_keep_actual_session_owner(
    attachment_chat, tmp_path, monkeypatch
):
    client, server, core, db, admin, users = attachment_chat
    service = server.asgi_app.state.services.chat
    session = await db.create_platform_session(
        creator=users[1]["resource_owner"],
        platform_id="webchat",
        session_id="owned-output",
        is_group=0,
    )
    source = tmp_path / "model.txt"
    source.write_bytes(b"private proactive result")
    chain = MessageChain(chain=[File(file=str(source), name="model.txt")])
    monkeypatch.setattr(webchat_adapter, "db_helper", db)
    monkeypatch.setattr(webchat_event, "db_helper", db)
    monkeypatch.setattr(webchat_event, "attachments_dir", service.attachments_dir)
    adapter = webchat_adapter.WebChatAdapter({}, {}, asyncio.Queue())
    adapter.attachments_dir = Path(service.attachments_dir)
    # A background sender can be running under another account's context.
    token = webchat_resource_owner.set(users[0]["resource_owner"])
    try:
        await adapter._save_proactive_message(session.session_id, chain)
        await webchat_event._persist_bot_reply_if_orphan(
            session_id=f"webchat!{session.creator}!{session.session_id}",
            request_id="orphan-output",
            message_chain=chain,
        )
    finally:
        webchat_resource_owner.reset(token)
    # A passive/proactive stream uses the raw generated filename immediately,
    # before the history fallback makes its separate stored copy.
    back = webchat_queue_mgr.get_or_create_back_queue(
        "proactive-media", session.session_id
    )
    await webchat_event.WebChatMessageEvent._send(
        "proactive-media", chain, f"webchat!{session.creator}!{session.session_id}"
    )
    media = await back.get()
    filename = media["data"].removeprefix("[FILE]").split("|", 1)[0]
    visible = await client.get(
        "/api/v1/files/content",
        params={"filename": filename},
        headers={"Authorization": f"Bearer {users[1]['token']}"},
    )
    assert visible.content == b"private proactive result"
    webchat_queue_mgr.remove_back_queue("proactive-media")
    reopened = SQLiteDatabase(db.db_path)
    await reopened.initialize()
    reopened.inited = True
    try:
        async with reopened.get_db() as connection:
            rows = (await connection.execute(select(Attachment))).scalars().all()
        assert len(rows) == 3
        assert all(row.owner == session.creator for row in rows)
        for row in rows:
            denied = await client.get(
                f"/api/v1/files/{row.attachment_id}/content",
                headers={"Authorization": f"Bearer {users[0]['token']}"},
            )
            assert b"private proactive result" not in denied.content
            own = await client.get(
                f"/api/v1/files/{row.attachment_id}/content",
                headers={"Authorization": f"Bearer {users[1]['token']}"},
            )
            assert own.content == b"private proactive result"
    finally:
        await reopened.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", ["disable", "reset"])
async def test_revoked_login_cannot_consume_download_token(attachment_chat, revoke):
    client, server, core, db, admin, users = attachment_chat
    headers = {"Authorization": f"Bearer {users[0]['token']}"}
    uploaded = (
        await client.post(
            "/api/v1/files",
            headers=headers,
            files={"file": ("private.txt", b"revoked content")},
        )
    ).json()["data"]
    row = await db.get_attachment_by_id(uploaded["attachment_id"])
    owner_token = webchat_resource_owner.set(users[0]["resource_owner"])
    try:
        download_token = await file_token_service.register_file(row.path)
    finally:
        webchat_resource_owner.reset(owner_token)
    async with db.get_db() as connection:
        account = await connection.get(DashboardAccount, users[0]["account_id"])
        if revoke == "disable":
            account.enabled = False
        else:
            account.token_version += 1
        connection.add(account)
        await connection.commit()
    for path in (
        f"/api/v1/files/{row.attachment_id}/content",
        f"/api/v1/files/tokens/{download_token}",
        f"/api/file/{download_token}",
    ):
        denied = await client.get(path, headers=headers)
        assert denied.status_code == 401
        assert b"revoked content" not in denied.content
    assert not await file_token_service.check_token_expired(download_token)
    assert Path(row.path).read_bytes() == b"revoked content"


@pytest.mark.asyncio
async def test_owner_column_upgrade_is_repeatable_and_preserves_legacy_attachment(
    attachment_chat, tmp_path, monkeypatch
):
    client, server, core, db, admin, users = attachment_chat
    service = server.asgi_app.state.services.chat
    data_root = tmp_path / "legacy-data"
    attachments = data_root / "attachments"
    attachments.mkdir(parents=True)
    monkeypatch.setattr(service, "attachments_dir", str(attachments))
    monkeypatch.setattr(
        sqlite_module, "get_astrbot_data_path", lambda: str(data_root), raising=False
    )
    path = attachments / "legacy-before-upgrade.txt"
    path.write_bytes(b"legacy content")
    unregistered = attachments / "unregistered-legacy.txt"
    unregistered.write_bytes(b"unregistered legacy content")
    old = await db.insert_attachment(str(path), "file", "text/plain")
    await db.engine.dispose()
    with sqlite3.connect(db.db_path) as connection:
        connection.execute("DROP INDEX ix_attachments_owner")
        connection.execute("ALTER TABLE attachments DROP COLUMN owner")
    await db.initialize()
    await db.initialize()
    for account in [admin, *users]:
        response = await client.get(
            f"/api/v1/files/{old.attachment_id}/content",
            headers={"Authorization": f"Bearer {account['token']}"},
        )
        assert (response.content == b"legacy content") == (account is admin)
        response = await client.get(
            "/api/v1/files/content",
            params={"filename": unregistered.name},
            headers={"Authorization": f"Bearer {account['token']}"},
        )
        assert (response.content == unregistered.read_bytes()) == (account is admin)
    assert (await db.get_attachment_by_id(old.attachment_id)).owner is None
    late_file = attachments / "new-file-without-record.txt"
    late_file.write_bytes(b"new unregistered content")
    await db.initialize()
    denied = await client.get(
        "/api/v1/files/content",
        params={"filename": late_file.name},
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert denied.json()["status"] == "error"


@pytest.mark.asyncio
async def test_legacy_inventory_failure_rolls_back_schema_and_retries_after_reopen(
    attachment_chat, tmp_path, monkeypatch
):
    client, server, core, db, admin, users = attachment_chat
    data_root = tmp_path / "legacy-retry"
    attachments = data_root / "attachments"
    attachments.mkdir(parents=True)
    images = data_root / "webchat" / "imgs"
    images.mkdir(parents=True)
    legacy = attachments / "before-first-upgrade.txt"
    legacy.write_bytes(b"old attachment before migration")
    legacy_image = images / "before-first-upgrade.png"
    PILImage.new("RGB", (2, 2), "blue").save(legacy_image)
    service = server.asgi_app.state.services.chat
    monkeypatch.setattr(service, "attachments_dir", str(attachments))
    monkeypatch.setattr(service, "webchat_img_dir", str(images))
    monkeypatch.setattr(sqlite_module, "get_astrbot_data_path", lambda: str(data_root))
    await db.engine.dispose()
    with sqlite3.connect(db.db_path) as connection:
        connection.execute("DROP INDEX ix_attachments_owner")
        connection.execute("ALTER TABLE attachments DROP COLUMN owner")
    inserted = 0

    def fail_second_insert(conn, cursor, statement, parameters, context, executemany):
        nonlocal inserted
        if statement.startswith("INSERT INTO attachments"):
            inserted += 1
            if inserted == 2:
                raise RuntimeError("Controlled legacy inventory failure")

    event.listen(db.engine.sync_engine, "before_cursor_execute", fail_second_insert)
    try:
        with pytest.raises(RuntimeError, match="Controlled legacy inventory failure"):
            await db.initialize()
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", fail_second_insert)
    await db.engine.dispose()
    with sqlite3.connect(db.db_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(attachments)")
        }
        assert "owner" not in columns
        assert connection.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0
    await db.initialize()
    await db.initialize()
    for path in (legacy, legacy_image):
        for account in [admin, users[0], users[1]]:
            response = await client.get(
                "/api/v1/files/content",
                params={"filename": path.name},
                headers={"Authorization": f"Bearer {account['token']}"},
            )
            assert (response.content == path.read_bytes()) == (account is admin)
    async with db.get_db() as connection:
        rows = (await connection.execute(select(Attachment))).scalars().all()
    assert len(rows) == 2
    assert all(row.owner is None for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "record", "video", "file"])
async def test_generated_component_is_owned_before_stream_publication(
    attachment_chat, tmp_path, monkeypatch, kind
):
    client, server, core, db, admin, users = attachment_chat
    service = server.asgi_app.state.services.chat
    session = await db.create_platform_session(
        creator=users[0]["resource_owner"],
        platform_id="webchat",
        session_id="generated-media",
        is_group=0,
    )
    source = tmp_path / "source.png"
    PILImage.new("RGB", (2, 2), "blue").save(source)
    if kind == "record":
        source = tmp_path / "source.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 160)
    component = (
        File(file=str(source), name="source.png")
        if kind == "file"
        else {"image": Image, "record": Record, "video": Video}[kind].fromFileSystem(
            str(source)
        )
    )
    monkeypatch.setattr(webchat_event, "db_helper", db)
    monkeypatch.setattr(webchat_event, "attachments_dir", service.attachments_dir)
    back = webchat_queue_mgr.get_or_create_back_queue(
        "generated-component", session.session_id
    )
    await webchat_event.WebChatMessageEvent._send(
        "generated-component",
        MessageChain(chain=[component]),
        f"webchat!{session.creator}!{session.session_id}",
    )
    payload = await back.get()
    assert payload["type"] == kind
    filename = payload["data"].split("]", 1)[1].split("|", 1)[0]
    assert await db.count_platform_message_history("webchat", session.session_id) == 0
    own = await client.get(
        "/api/v1/files/content",
        params={"filename": filename},
        headers={"Authorization": f"Bearer {users[0]['token']}"},
    )
    assert own.content == source.read_bytes()
    denied = await client.get(
        "/api/v1/files/content",
        params={"filename": filename},
        headers={"Authorization": f"Bearer {users[1]['token']}"},
    )
    assert denied.json()["status"] == "error"
    webchat_queue_mgr.remove_back_queue("generated-component")


@pytest.mark.asyncio
@pytest.mark.parametrize("producer", ["upload", "image", "record", "video", "file"])
@pytest.mark.parametrize("outcome", ["complete", "fail", "cancel"])
async def test_unregistered_files_are_not_grandfathered(
    attachment_chat, tmp_path, monkeypatch, producer, outcome
):
    """Freeze actual registration after disk writes, including failed writes."""
    client, server, core, db, admin, users = attachment_chat
    service = server.asgi_app.state.services.chat
    data_root = tmp_path / "pending-data"
    attachments = data_root / "attachments"
    attachments.mkdir(parents=True)
    monkeypatch.setattr(service, "attachments_dir", str(attachments))
    monkeypatch.setattr(sqlite_module, "get_astrbot_data_path", lambda: str(data_root))
    pending_path = asyncio.get_running_loop().create_future()
    release = asyncio.Event()
    insert_attachment = db.insert_attachment

    async def paused_insert(*args, **kwargs):
        path = Path(kwargs.get("path") or args[0])
        assert path.is_file()
        pending_path.set_result(path)
        await release.wait()
        if outcome == "fail":
            raise RuntimeError("Controlled attachment registration failure")
        return await insert_attachment(*args, **kwargs)

    monkeypatch.setattr(db, "insert_attachment", paused_insert)
    owner_headers = {"Authorization": f"Bearer {users[0]['token']}"}
    if producer == "upload":
        secret = b"new private upload before registration"
        pending = asyncio.create_task(
            client.post(
                "/api/v1/files",
                headers=owner_headers,
                files={"file": ("new-upload.txt", secret)},
            )
        )
    else:
        session = await db.create_platform_session(
            creator=users[0]["resource_owner"],
            platform_id="webchat",
            session_id="pending-output",
            is_group=0,
        )
        source = tmp_path / "output.png"
        PILImage.new("RGB", (3, 3), "green").save(source)
        if producer == "record":
            source = tmp_path / "output.wav"
            with wave.open(str(source), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x00\x00" * 160)
        secret = source.read_bytes()
        component = (
            File(file=str(source), name=source.name)
            if producer == "file"
            else {"image": Image, "record": Record, "video": Video}[
                producer
            ].fromFileSystem(str(source))
        )
        monkeypatch.setattr(webchat_event, "db_helper", db)
        monkeypatch.setattr(webchat_event, "attachments_dir", service.attachments_dir)
        webchat_queue_mgr.get_or_create_back_queue("pending-media", session.session_id)
        pending = asyncio.create_task(
            webchat_event.WebChatMessageEvent._send(
                "pending-media",
                MessageChain(chain=[component]),
                f"webchat!{session.creator}!{session.session_id}",
            )
        )
    try:
        path = await asyncio.wait_for(pending_path, timeout=8)
        for account in [admin, users[1]]:
            for endpoint in ("/api/v1/files/content", "/api/chat/get_file"):
                denied = await client.get(
                    endpoint,
                    params={"filename": path.name},
                    headers={"Authorization": f"Bearer {account['token']}"},
                )
                assert denied.content != secret
                assert denied.json()["status"] == "error"
        if outcome == "cancel":
            pending.cancel()
        else:
            release.set()
        results = await asyncio.wait_for(
            asyncio.gather(pending, return_exceptions=True), timeout=8
        )
        if outcome == "complete":
            assert not isinstance(results[0], BaseException)
            own = await client.get(
                "/api/v1/files/content",
                params={"filename": path.name},
                headers=owner_headers,
            )
            assert own.content == secret
        else:
            assert isinstance(results[0], BaseException)
        # Restarting initialization must not turn a failed/cancelled file into
        # legacy administrator data; only the schema upgrade can inventory it.
        await db.initialize()
        for account in [admin, users[1]] + (
            [] if outcome == "complete" else [users[0]]
        ):
            denied = await client.get(
                "/api/v1/files/content",
                params={"filename": path.name},
                headers={"Authorization": f"Bearer {account['token']}"},
            )
            assert denied.content != secret
            assert denied.json()["status"] == "error"
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        webchat_queue_mgr.remove_back_queue("pending-media")
