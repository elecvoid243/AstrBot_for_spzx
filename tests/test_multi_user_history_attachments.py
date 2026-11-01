"""Combination regressions for owned history and attachment cascades."""

import asyncio
from pathlib import Path

import pytest

from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.utils.webchat_access import (
    DashboardPrincipal,
    dashboard_account_context,
)
from tests.fixtures.dashboard_accounts import account_dashboard as account_dashboard
from tests.fixtures.dashboard_accounts import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_attachments import attachment_chat as attachment_chat
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat
from tests.test_multi_user_history import private_history as private_history


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["delete", "edit", "retry"])
@pytest.mark.parametrize("actor", [0, 2], ids=["ordinary", "admin"])
async def test_corrupt_history_cannot_clean_another_accounts_files(
    private_history, attachment_chat, action, actor
):
    client, server, core, db, resources = private_history
    service = server.asgi_app.state.services.chat
    account, session_id, records = resources[actor]
    foreign_account = resources[1][0]
    headers = {"Authorization": f"Bearer {account['token']}"}
    attachments = []
    for current in (account, foreign_account):
        uploaded = await client.post(
            "/api/v1/files",
            headers={"Authorization": f"Bearer {current['token']}"},
            files={
                "file": (
                    "private.txt",
                    current["resource_owner"].encode(),
                    "text/plain",
                )
            },
        )
        assert uploaded.json()["status"] == "ok", uploaded.text
        attachments.append(
            await db.get_attachment_by_id(uploaded.json()["data"]["attachment_id"])
        )
    own, foreign = attachments
    legacy_path = Path(service.attachments_dir) / "legacy-history.txt"
    legacy_path.write_bytes(b"legacy administrator content")
    legacy = await db.insert_attachment(str(legacy_path), "file", "text/plain")
    source_content = {
        "type": "user",
        "message": [
            {"type": "plain", "text": "private question"},
            {
                "type": "file",
                "attachment_id": own.attachment_id,
                "filename": "private.txt",
            },
        ],
    }
    bot_content = {
        "type": "bot",
        "message": [
            {"type": "plain", "text": "private answer"},
            *[
                {
                    "type": "file",
                    "attachment_id": item.attachment_id,
                    "filename": "private.txt",
                }
                for item in (own, foreign, legacy)
            ],
        ],
    }
    # Deliberately restore a malformed persisted reference, bypassing send validation.
    await service.platform_history_mgr.update(
        message_id=records[0].id, content=source_content
    )
    await service.platform_history_mgr.update(
        message_id=records[1].id, content=bot_content
    )

    async def external_model():
        _, target, payload = await webchat_queue_mgr.get_or_create_queue(
            session_id
        ).get()
        assert payload["thinking_effort"] == "low"
        assert payload["selected_provider"] == "gpt-mini"
        assert payload["selected_model"] == "gpt-4o-mini"
        back = webchat_queue_mgr.get_or_create_back_queue(payload["message_id"], target)
        await back.put({"type": "plain", "data": "safe retry", "streaming": True})
        await back.put({"type": "end"})

    if action == "delete":
        response = await client.delete(
            f"/api/v1/chat/sessions/{session_id}", headers=headers
        )
        assert response.json()["status"] == "ok", response.text
        assert not Path(own.path).exists()
        assert await db.get_attachment_by_id(own.attachment_id) is None
        assert legacy_path.exists() == (account["role"] != "admin")
    elif action == "edit":
        forged = await client.patch(
            f"/api/v1/chat/sessions/{session_id}/messages/{records[0].id}",
            headers=headers,
            json={
                "content": {
                    "type": "user",
                    "message": [
                        {"type": "plain", "text": "forged edit"},
                        {"type": "file", "attachment_id": foreign.attachment_id},
                    ],
                }
            },
        )
        assert forged.json()["status"] == "error", forged.text
        assert (
            await db.get_platform_message_history_by_id(records[0].id)
        ).content == source_content
        assert (
            await db.get_platform_message_history_by_id(records[1].id)
        ).content == bot_content
        response = await client.patch(
            f"/api/v1/chat/sessions/{session_id}/messages/{records[0].id}",
            headers=headers,
            json={
                "content": {
                    **source_content,
                    "message": [
                        {"type": "plain", "text": "edited with owned attachment"},
                        source_content["message"][1],
                    ],
                }
            },
        )
        assert response.json()["status"] == "ok", response.text
        assert await db.get_platform_message_history_by_id(records[1].id) is None
        saved = response.json()["data"]["message"]
        worker = asyncio.create_task(external_model())
        continuation = await asyncio.wait_for(
            client.post(
                "/api/v1/chat",
                headers=headers,
                json={
                    "session_id": session_id,
                    "message": saved["content"]["message"],
                    "_skip_user_history": True,
                    "_llm_checkpoint_id": saved["llm_checkpoint_id"],
                    "config_id": "default",
                    "thinking_effort": "low",
                    "selected_provider": "gpt-mini",
                    "selected_model": "gpt-4o-mini",
                },
            ),
            10,
        )
        if "safe retry" not in continuation.text:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        assert "safe retry" in continuation.text, continuation.text
        await asyncio.wait_for(worker, 2)
        assert Path(own.path).exists()
    else:
        for invalid in [
            {"config_id": "missing-profile"},
            {"thinking_effort": "x" * 1000},
            *(
                [{"selected_provider": "foreign-model", "selected_model": "bad"}]
                if account["role"] == "user"
                else []
            ),
        ]:
            denied = await client.post(
                f"/api/v1/chat/sessions/{session_id}/messages/{records[1].id}/regenerate",
                headers=headers,
                json=invalid,
            )
            assert denied.json()["status"] == "error", denied.text
            assert (
                await db.get_platform_message_history_by_id(records[1].id)
            ).content == bot_content
            assert (
                await db.get_platform_message_history_by_id(records[0].id)
            ).llm_checkpoint_id == records[0].llm_checkpoint_id

        worker = asyncio.create_task(external_model())
        response = await asyncio.wait_for(
            client.post(
                f"/api/v1/chat/sessions/{session_id}/messages/{records[1].id}/regenerate",
                headers=headers,
                json={
                    "config_id": "default",
                    "thinking_effort": "low",
                    "selected_provider": "gpt-mini",
                    "selected_model": "gpt-4o-mini",
                },
            ),
            10,
        )
        if "safe retry" not in response.text:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        assert "safe retry" in response.text, response.text
        await asyncio.wait_for(worker, 2)
        assert Path(own.path).exists()
    # Neither file content nor the database row can be affected by the forged ID.
    assert Path(foreign.path).read_bytes() == foreign_account["resource_owner"].encode()
    assert (
        await db.get_attachment_by_id(foreign.attachment_id)
    ).owner == foreign_account["resource_owner"]
    foreign_download = await client.get(
        f"/api/v1/files/{foreign.attachment_id}/content",
        headers={"Authorization": f"Bearer {foreign_account['token']}"},
    )
    assert foreign_download.content == foreign_account["resource_owner"].encode()
    assert (
        await db.count_platform_message_history(
            platform_id="webchat", user_id=resources[1][1]
        )
        == 2
    )


@pytest.mark.asyncio
async def test_detached_dashboard_cleanup_uses_principal_owner(
    private_history, attachment_chat
):
    client, server, core, db, resources = private_history
    account, session_id, records = resources[0]
    uploads = []
    for current, _, _ in resources[:2]:
        uploaded = await client.post(
            "/api/v1/files",
            headers={"Authorization": f"Bearer {current['token']}"},
            files={
                "file": (
                    "private.txt",
                    current["resource_owner"].encode(),
                    "text/plain",
                )
            },
        )
        uploads.append(
            await db.get_attachment_by_id(uploaded.json()["data"]["attachment_id"])
        )
    service = server.asgi_app.state.services.chat
    await service.platform_history_mgr.update(
        message_id=records[1].id,
        content={
            "type": "bot",
            "message": [
                {"type": "file", "attachment_id": item.attachment_id}
                for item in uploads
            ],
        },
    )
    # Principal survives propagation even when the resource ContextVar is absent.
    context = dashboard_account_context.set(
        DashboardPrincipal(
            account_id=account["account_id"],
            resource_owner=account["resource_owner"],
            role=account["role"],
            login_name=account["username"],
        )
    )
    try:
        await service.delete_webchat_session(account["resource_owner"], session_id)
    finally:
        dashboard_account_context.reset(context)
    assert not Path(uploads[0].path).exists()
    assert Path(uploads[1].path).exists()
    assert await db.get_attachment_by_id(uploads[1].attachment_id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("authentication", ["api_key", "kernel"])
async def test_non_dashboard_cleanup_retains_unowned_attachment_compatibility(
    private_history, attachment_chat, authentication
):
    client, server, core, db, resources = private_history
    service = server.asgi_app.state.services.chat
    if authentication == "api_key":
        key = await client.post(
            "/api/apikey/create",
            headers={"Authorization": f"Bearer {resources[2][0]['token']}"},
            json={"name": "history-cleanup", "scopes": ["chat"]},
        )
        assert key.json()["status"] == "ok", key.text
        headers = {"Authorization": f"ApiKey {key.json()['data']['api_key']}"}
        created = await client.get("/api/v1/chat/sessions/new", headers=headers)
        session_id = created.json()["data"]["session_id"]
    else:
        session = await db.create_platform_session(
            creator="kernel-owner", platform_id="webchat"
        )
        session_id = session.session_id
    path = Path(service.attachments_dir) / "non-dashboard.txt"
    path.write_bytes(b"unowned core attachment")
    attachment = await db.insert_attachment(str(path), "file", "text/plain")
    assert attachment.owner is None
    await db.insert_platform_message_history(
        platform_id="webchat",
        user_id=session_id,
        sender_id="bot",
        sender_name="bot",
        content={
            "type": "bot",
            "message": [{"type": "file", "attachment_id": attachment.attachment_id}],
        },
    )
    if authentication == "api_key":
        deleted = await client.delete(
            f"/api/v1/chat/sessions/{session_id}", headers=headers
        )
        assert deleted.json()["status"] == "ok", deleted.text
    else:
        await service.delete_webchat_session("kernel-owner", session_id)
    assert not path.exists()
    assert await db.get_attachment_by_id(attachment.attachment_id) is None


@pytest.mark.asyncio
async def test_retry_rejects_a_newer_pending_user_turn_before_mutation(private_history):
    client, server, core, db, resources = private_history
    account, session_id, records = resources[0]
    pending = await db.insert_platform_message_history(
        platform_id="webchat",
        user_id=session_id,
        sender_id=account["resource_owner"],
        sender_name=account["username"],
        content={
            "type": "user",
            "message": [{"type": "plain", "text": "pending user turn"}],
        },
    )
    denied = await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages/{records[1].id}/regenerate",
        headers={"Authorization": f"Bearer {account['token']}"},
        json={},
    )
    assert denied.json()["status"] == "error", denied.text
    assert (
        await db.get_platform_message_history_by_id(records[1].id)
    ).content == records[1].content
    assert (
        await db.get_platform_message_history_by_id(records[0].id)
    ).llm_checkpoint_id == records[0].llm_checkpoint_id
    assert (
        await db.get_platform_message_history_by_id(pending.id)
    ).content == pending.content
    assert not server.asgi_app.state.services.chat.chat_runs
