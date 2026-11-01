"""Account-owned session archives through production Dashboard HTTP and SQLite."""

import asyncio
import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
import pytest_asyncio

from astrbot.core.db.sqlite import SQLiteDatabase
from tests.fixtures.dashboard_accounts import account_dashboard as account_dashboard
from tests.fixtures.dashboard_accounts import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_attachments import attachment_chat as attachment_chat
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat
from tests.test_multi_user_chat_config import configured_chat as configured_chat
from tests.test_multi_user_projects import send_reply
from tests.test_session_transfer_import import build_package_bytes


@pytest_asyncio.fixture
async def transfer_chat(configured_chat, attachment_chat, tmp_path):
    """Use production archive services with isolated files and real accounts."""
    client, server, core, db, admin, users = configured_chat
    chat = server.asgi_app.state.services.chat
    transfer = server.asgi_app.state.services.session_transfer
    chat.conv_mgr = transfer.conv_mgr = core.conversation_manager
    transfer.temp_dir = tmp_path / "archives"
    transfer.attachments_dir = Path(chat.attachments_dir)
    yield client, server, core, db, admin, users


@pytest.mark.asyncio
async def test_owned_archive_roundtrip_quotes_threads_attachment_and_reopen(
    transfer_chat,
):
    client, server, core, db, admin, users = transfer_chat
    account = users[0]
    headers = {"Authorization": f"Bearer {account['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    upload = (
        await client.post(
            "/api/v1/files",
            headers=headers,
            files={
                "file": ("roundtrip.txt", b"private archive attachment", "text/plain")
            },
        )
    ).json()["data"]
    await send_reply(
        client,
        core,
        account,
        sid,
        config_id="research",
        message=[{"type": "file", "attachment_id": upload["attachment_id"]}],
    )
    history = (
        await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
    ).json()["data"]["history"]
    bot = next(row for row in history if row["content"]["type"] == "bot")
    await send_reply(
        client,
        core,
        account,
        sid,
        message=[
            {"type": "plain", "text": "quote my previous answer"},
            {"type": "reply", "message_id": bot["id"]},
        ],
    )
    for side_chat in (False, True):
        body = {"session_id": sid, "side_chat": side_chat}
        if not side_chat:
            body.update(parent_message_id=bot["id"], selected_text="answer")
        thread = (
            await client.post("/api/v1/chat/threads", headers=headers, json=body)
        ).json()["data"]["thread_id"]
        await send_reply(
            client,
            core,
            account,
            thread,
            thread=True,
            message=[{"type": "file", "attachment_id": upload["attachment_id"]}],
        )
    archive = await client.get(f"/api/v1/chat/sessions/{sid}/export", headers=headers)
    assert (
        archive.status_code == 200
        and archive.headers["content-type"] == "application/zip"
    )
    for other in (users[1], admin):
        denied = await client.get(
            f"/api/v1/chat/sessions/{sid}/export",
            headers={"Authorization": f"Bearer {other['token']}"},
        )
        assert b"private archive attachment" not in denied.content
        assert denied.json()["status"] == "error"
    staged = (
        await client.post(
            "/api/v1/chat/sessions/import",
            headers=headers,
            files={"file": ("roundtrip.zip", archive.content)},
        )
    ).json()["data"]
    assert staged["can_import"]
    import_id = staged["import_id"]
    for other in (users[1], admin):
        denied = await client.post(
            "/api/v1/chat/sessions/import/confirm",
            headers={"Authorization": f"Bearer {other['token']}"},
            json={"import_id": import_id},
        )
        assert denied.json()["status"] == "error"
        assert "roundtrip" not in denied.text and sid not in denied.text
    transfer = server.asgi_app.state.services.session_transfer
    assert transfer.pending_imports[import_id].zip_path.exists()
    deleted = await client.delete(f"/api/v1/chat/sessions/{sid}", headers=headers)
    assert deleted.json()["status"] == "ok", deleted.text
    assert await db.get_attachment_by_id(upload["attachment_id"]) is None
    confirmations = await asyncio.gather(
        *[
            client.post(
                "/api/v1/chat/sessions/import/confirm",
                headers=headers,
                json={"import_id": import_id},
            )
            for _ in range(2)
        ]
    )
    successes = [r.json()["data"] for r in confirmations if r.json()["status"] == "ok"]
    assert len(successes) == 1 and successes[0]["errors"] == []
    restored = successes[0]["created"][0]["new_session_id"]
    assert restored != sid and import_id not in transfer.pending_imports
    rows = (
        await client.get(f"/api/v1/chat/sessions/{restored}", headers=headers)
    ).json()["data"]["history"]
    assert all(row["content"]["_imported"] is True for row in rows)
    attachment_id = next(
        part["attachment_id"]
        for row in rows
        for part in row["content"].get("message", [])
        if "attachment_id" in part
    )
    assert attachment_id != upload["attachment_id"]
    downloaded = await client.get(
        f"/api/v1/files/{attachment_id}/content", headers=headers
    )
    assert downloaded.content == b"private archive attachment"
    quote = next(
        part
        for row in rows
        for part in row["content"].get("message", [])
        if part.get("type") == "reply"
    )
    imported_bot = next(row for row in rows if row["content"]["type"] == "bot")
    # SQLite may reuse deleted integer IDs; the target must be this new row.
    assert quote["message_id"] == imported_bot["id"]
    threads = await db.get_webchat_threads_by_parent_session(
        restored, account["resource_owner"]
    )
    assert len(threads) == 2 and {t.parent_message_id for t in threads} == {
        0,
        imported_bot["id"],
    }
    for thread in threads:
        thread_rows = (
            await client.get(
                f"/api/v1/chat/threads/{thread.thread_id}", headers=headers
            )
        ).json()["data"]["history"]
        assert attachment_id in json.dumps(thread_rows)
        assert all(row["content"]["_imported"] for row in thread_rows)
        await send_reply(
            client, core, account, thread.thread_id, thread=True, config_id="research"
        )
    for other in (users[1], admin):
        other_headers = {"Authorization": f"Bearer {other['token']}"}
        for path in (
            f"/api/v1/chat/sessions/{restored}",
            f"/api/chat/get_attachment?attachment_id={attachment_id}",
        ):
            denied = await client.get(path, headers=other_headers)
            assert denied.json()["status"] == "error", denied.text
    origin = f"webchat:FriendMessage:webchat!{account['resource_owner']}!{restored}"
    cid = await core.conversation_manager.get_curr_conversation_id(origin)
    assert cid
    before = await core.conversation_manager.get_conversation(origin, cid)
    assert "private-reply" in before.history
    await send_reply(
        client,
        core,
        account,
        restored,
        config_id="writing",
        message=[
            {"type": "plain", "text": "continue after restoring"},
            {"type": "reply", "message_id": imported_bot["id"]},
            {"type": "file", "attachment_id": attachment_id},
        ],
    )
    after = await core.conversation_manager.get_conversation(origin, cid)
    assert len(json.loads(after.history)) > len(json.loads(before.history))
    assert core.umop_config_router.get_conf_id_for_umop(origin) == "writing"
    await db.engine.dispose()
    reopened = SQLiteDatabase(db.db_path)
    await reopened.initialize()
    try:
        assert (await reopened.get_platform_session_by_id(restored)).creator == account[
            "resource_owner"
        ]
        attachment = await reopened.get_attachment_by_id(attachment_id)
        assert (
            attachment.owner == account["resource_owner"]
            and Path(attachment.path).read_bytes() == downloaded.content
        )
        assert (
            len(
                await reopened.get_webchat_threads_by_parent_session(
                    restored, account["resource_owner"]
                )
            )
            == 2
        )
    finally:
        await reopened.engine.dispose()


@pytest.mark.asyncio
async def test_malicious_archive_cannot_link_foreign_resources_or_launder_authority(
    transfer_chat,
):
    client, server, core, db, admin, users = transfer_chat
    attacker, victim = users
    headers = {"Authorization": f"Bearer {attacker['token']}"}
    victim_headers = {"Authorization": f"Bearer {victim['token']}"}
    victim_sid = (
        await client.get("/api/v1/chat/sessions/new", headers=victim_headers)
    ).json()["data"]["session_id"]
    victim_upload = (
        await client.post(
            "/api/v1/files",
            headers=victim_headers,
            files={"file": ("victim.txt", b"SECRET-FOREIGN", "text/plain")},
        )
    ).json()["data"]
    await send_reply(client, core, victim, victim_sid)
    victim_rows = (
        await client.get(f"/api/v1/chat/sessions/{victim_sid}", headers=victim_headers)
    ).json()["data"]["history"]
    victim_bot = next(r for r in victim_rows if r["content"]["type"] == "bot")
    entry = {
        "session": {
            "session_id": victim_sid,
            "creator": victim["resource_owner"],
            "owner": victim["resource_owner"],
            "project_id": "foreign-project",
            "display_name": "Imported display",
        },
        "history": [
            {
                "id": 101,
                "content": {
                    "type": "bot",
                    "message": [{"type": "plain", "text": "ordinary visible answer"}],
                    "_imported": False,
                    "file_changes": {
                        "files": [
                            {
                                "path": "visible.txt",
                                "_canonical_path": "G:/secret",
                                "_recycle_entry": "foreign-delete",
                                "_removed_sha256": "foreign-hash",
                            }
                        ]
                    },
                    "terminal": {"output": "display only"},
                },
                "llm_checkpoint_id": "safe-checkpoint",
            },
            {
                "id": 102,
                "content": {
                    "type": "user",
                    "message": [
                        {"type": "plain", "text": "preserve my text"},
                        {
                            "type": "file",
                            "attachment_id": victim_upload["attachment_id"],
                        },
                        {
                            "type": "file",
                            "attachment_id": "../../escape",
                            "stored_filename": "foreign.txt",
                        },
                        {"type": "reply", "message_id": 101},
                        {"type": "reply", "message_id": 201},
                        {"type": "reply", "message_id": victim_bot["id"]},
                    ],
                },
            },
            {
                "id": 103,
                "content": {
                    "type": "branch_info",
                    "source_session_id": victim_sid,
                    "source_message_id": victim_bot["id"],
                },
            },
        ],
        "conversations": [
            {
                "conversation_id": "old-conversation",
                "content": [
                    {"role": "assistant", "content": "preserved context"},
                    {"role": "_checkpoint", "content": {"id": "safe-checkpoint"}},
                ],
            }
        ],
        "attachments": [
            {
                "attachment_id": "../../escape",
                "ext": ".txt",
                "zip_path": "files/attachments/packaged.txt",
                "owner": victim["resource_owner"],
            }
        ],
        "threads": [
            {
                "thread": {
                    "thread_id": "old-thread",
                    "parent_message_id": 101,
                    "base_checkpoint_id": "safe-checkpoint",
                },
                "history": [
                    {
                        "id": 201,
                        "content": {
                            "type": "user",
                            "message": [
                                {
                                    "type": "plain",
                                    "text": "keep this side conversation",
                                },
                                {"type": "reply", "message_id": 101},
                            ],
                        },
                    }
                ],
                "conversations": [
                    {"conversation_id": "old-thread-conversation", "content": []}
                ],
            }
        ],
        "preferences": [
            {
                "scope": "umo",
                "scope_id": f"webchat:FriendMessage:webchat!{victim['resource_owner']}!old-thread",
                "key": "sel_conv_id",
                "value": {"val": "old-conversation"},
            },
            {
                "scope": "global",
                "scope_id": "global",
                "key": "umop_config_routing",
                "value": {"val": {"*:*:*": "forged"}},
            },
            {
                "scope": "umo",
                "scope_id": "",
                "key": "fs_access_custom_roots",
                "value": {"val": ["G:/"]},
            },
            *[
                {
                    "scope": "umo",
                    "scope_id": "",
                    "key": key,
                    "value": {"val": "foreign-remote-identity"},
                }
                for key in (
                    "dify_conversation_id",
                    "coze_conversation_id",
                    "dashscope_conversation_id",
                    "deerflow_thread_id",
                )
            ],
            {
                "scope": "umo",
                "scope_id": "",
                "key": "sel_conv_id",
                "value": {"val": "old-conversation"},
            },
            {
                "scope": "umo",
                "scope_id": "",
                "key": "session_variables",
                "value": {"val": {"language": "en"}},
            },
        ],
    }
    routes_before = dict(core.umop_config_router.umop_to_conf_id)
    blob = build_package_bytes(
        sessions=[entry],
        extra_entries=[("files/attachments/packaged.txt", b"MY PACKAGED BYTES")],
    ).getvalue()
    staged = (
        await client.post(
            "/api/v1/chat/sessions/import",
            headers=headers,
            files={"file": ("hostile.zip", blob)},
        )
    ).json()["data"]
    result = (
        await client.post(
            "/api/v1/chat/sessions/import/confirm",
            headers=headers,
            json={"import_id": staged["import_id"]},
        )
    ).json()["data"]
    assert result["errors"] == [] and len(result["warnings"]) >= 8
    sid = result["created"][0]["new_session_id"]
    assert sid != victim_sid
    assert (await db.get_platform_session_by_id(sid)).creator == attacker[
        "resource_owner"
    ]
    assert await db.get_project_by_session(sid, attacker["resource_owner"]) is None
    assert core.umop_config_router.umop_to_conf_id == routes_before
    rows = (await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)).json()[
        "data"
    ]["history"]
    serialized = json.dumps(rows)
    assert "ordinary visible answer" in serialized and "display only" in serialized
    assert all(r["content"]["_imported"] is True for r in rows)
    assert (
        "_canonical_path" not in serialized
        and "_recycle_entry" not in serialized
        and "_removed_sha256" not in serialized
    )
    assert (
        victim_sid not in serialized
        and victim_upload["attachment_id"] not in serialized
    )
    parts = rows[1]["content"]["message"]
    assert [p["message_id"] for p in parts if p.get("type") == "reply"] == [
        rows[0]["id"]
    ]
    aid = next(p["attachment_id"] for p in parts if p.get("type") == "file")
    assert ".." not in aid and aid != victim_upload["attachment_id"]
    assert (
        await client.get(f"/api/v1/files/{aid}", headers=headers)
    ).content == b"MY PACKAGED BYTES"
    assert (
        await client.get(
            f"/api/v1/files/{victim_upload['attachment_id']}", headers=victim_headers
        )
    ).content == b"SECRET-FOREIGN"
    assert (
        await client.get(f"/api/v1/chat/sessions/{victim_sid}", headers=victim_headers)
    ).json()["data"]["history"] == victim_rows
    thread = (
        await db.get_webchat_threads_by_parent_session(sid, attacker["resource_owner"])
    )[0]
    thread_history = (
        await client.get(f"/api/v1/chat/threads/{thread.thread_id}", headers=headers)
    ).json()["data"]["history"]
    assert "keep this side conversation" in json.dumps(thread_history)
    assert all(
        part.get("type") != "reply"
        for row in thread_history
        for part in row["content"].get("message", [])
    )
    thread_umo = (
        f"webchat:FriendMessage:webchat!{attacker['resource_owner']}!{thread.thread_id}"
    )
    assert not [
        p
        for p in await db.get_preferences(scope="umo", scope_id=thread_umo)
        if p.key == "sel_conv_id"
    ]
    for _ in range(2):
        exported = await client.get(
            f"/api/v1/chat/sessions/{sid}/export", headers=headers
        )
        preview = (
            await client.post(
                "/api/v1/chat/sessions/import",
                headers=headers,
                files={"file": ("again.zip", exported.content)},
            )
        ).json()["data"]
        sid = (
            await client.post(
                "/api/v1/chat/sessions/import/confirm",
                headers=headers,
                json={"import_id": preview["import_id"]},
            )
        ).json()["data"]["created"][0]["new_session_id"]
        rows = (
            await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
        ).json()["data"]["history"]
        assert all(row["content"]["_imported"] for row in rows)
    branch = await client.post(
        f"/api/v1/chat/sessions/{sid}/messages/{rows[0]['id']}/branch", headers=headers
    )
    assert branch.json()["status"] == "ok", branch.text
    bid = branch.json()["data"]["session_id"]
    copied = (await client.get(f"/api/v1/chat/sessions/{bid}", headers=headers)).json()[
        "data"
    ]["history"]
    copied_bot = next(row for row in copied if row["content"]["type"] == "bot")
    assert copied_bot["content"]["_imported"] is True
    assert "_canonical_path" not in json.dumps(copied_bot)


@pytest.mark.asyncio
async def test_export_filters_poisoned_foreign_attachment_reference(transfer_chat):
    client, server, core, db, admin, users = transfer_chat
    headers = {"Authorization": f"Bearer {users[0]['token']}"}
    other_headers = {"Authorization": f"Bearer {users[1]['token']}"}
    sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
        "data"
    ]["session_id"]
    upload = (
        await client.post(
            "/api/v1/files",
            headers=other_headers,
            files={"file": ("secret.txt", b"FOREIGN FILE BYTES", "text/plain")},
        )
    ).json()["data"]
    await core.platform_message_history_manager.insert(
        platform_id="webchat",
        user_id=sid,
        content={
            "type": "bot",
            "message": [{"type": "file", "attachment_id": upload["attachment_id"]}],
        },
    )
    response = await client.get(f"/api/v1/chat/sessions/{sid}/export", headers=headers)
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        payload = json.loads(archive.read("export.json"))
        assert payload["sessions"][0]["attachments"] == []
        assert not any(name.startswith("files/") for name in archive.namelist())
        assert b"FOREIGN FILE BYTES" not in b"".join(
            archive.read(name) for name in archive.namelist()
        )


@pytest.mark.asyncio
async def test_administrator_and_scoped_api_key_keep_owned_archive_access(
    transfer_chat,
):
    client, server, core, db, admin, users = transfer_chat
    admin_headers = {"Authorization": f"Bearer {admin['token']}"}
    key = (
        await client.post(
            "/api/v1/api-keys",
            headers=admin_headers,
            json={"name": "archive consumer", "scopes": ["chat", "file"]},
        )
    ).json()["data"]
    key_headers = {"Authorization": f"ApiKey {key['api_key']}"}
    for headers in (admin_headers, key_headers):
        sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
            "data"
        ]["session_id"]
        upload = (
            await client.post(
                "/api/v1/files",
                headers=headers,
                files={
                    "file": (
                        "consumer.txt",
                        b"owned archive consumer bytes",
                        "text/plain",
                    )
                },
            )
        ).json()["data"]
        await core.platform_message_history_manager.insert(
            platform_id="webchat",
            user_id=sid,
            content={
                "type": "user",
                "message": [{"type": "file", "attachment_id": upload["attachment_id"]}],
            },
        )
        exported = await client.get(
            f"/api/v1/chat/sessions/{sid}/export", headers=headers
        )
        assert exported.status_code == 200
        assert exported.headers["content-disposition"].startswith("inline; filename=")
        assert exported.headers["content-type"] == "application/zip"
        assert exported.content[:4] == b"PK\x03\x04"
        with zipfile.ZipFile(BytesIO(exported.content)) as archive:
            attachment_paths = [
                name
                for name in archive.namelist()
                if name.startswith("files/attachments/")
            ]
            assert len(attachment_paths) == 1
            assert archive.read(attachment_paths[0]) == b"owned archive consumer bytes"
        preview = (
            await client.post(
                "/api/v1/chat/sessions/import",
                headers=headers,
                files={"file": ("own.zip", exported.content)},
            )
        ).json()["data"]
        restored = (
            await client.post(
                "/api/v1/chat/sessions/import/confirm",
                headers=headers,
                json={"import_id": preview["import_id"]},
            )
        ).json()["data"]
        assert restored["errors"] == [] and len(restored["created"]) == 1
        restored_sid = restored["created"][0]["new_session_id"]
        records = await db.get_platform_message_history("webchat", restored_sid)
        restored_id = records[0].content["message"][0]["attachment_id"]
        attachment = await db.get_attachment_by_id(restored_id)
        expected_owner = admin["resource_owner"] if headers is admin_headers else None
        assert attachment.owner == expected_owner
        readers = [headers]
        if headers is key_headers:
            readers.append(admin_headers)
            # Legacy file consumption does not confer ownership of API-key chats.
            denied = await client.get(
                f"/api/v1/chat/sessions/{restored_sid}", headers=admin_headers
            )
            assert denied.json()["status"] == "error"
        paths = (
            f"/api/v1/files/{restored_id}/content",
            f"/api/chat/get_attachment?attachment_id={restored_id}",
        )
        for reader_headers in readers:
            # API keys use the scoped API; legacy consumers keep Dashboard auth.
            reader_paths = paths if reader_headers is admin_headers else paths[:1]
            for path in reader_paths:
                downloaded = await client.get(path, headers=reader_headers)
                assert downloaded.status_code == 200
                assert downloaded.content == b"owned archive consumer bytes"
        other = {"Authorization": f"Bearer {users[1]['token']}"}
        for path in paths:
            denied = await client.get(path, headers=other)
            assert denied.json()["status"] == "error"
        denied = await client.get(f"/api/v1/chat/sessions/{sid}/export", headers=other)
        assert denied.json()["status"] == "error"
    assert (
        list(server.asgi_app.state.services.session_transfer.temp_dir.glob("*.zip"))
        == []
    )
