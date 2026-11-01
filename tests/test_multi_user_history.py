"""Owner-scoped session organization and history through production HTTP."""

import asyncio

import pytest
import pytest_asyncio

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from tests.fixtures.dashboard_accounts import account_dashboard as account_dashboard
from tests.fixtures.dashboard_accounts import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat


@pytest_asyncio.fixture
async def private_history(ordinary_chat):
    """Restore persisted sample turns for three real, HTTP-authenticated accounts.

    Args:
        ordinary_chat: Production Dashboard fixture with three authenticated accounts.

    Yields:
        HTTP client, production services, database and restored account resources.
    """
    client, server, core, db, admin, users = ordinary_chat
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    resources = []
    for index, account in enumerate([*users, admin]):
        headers = {"Authorization": f"Bearer {account['token']}"}
        created = await client.get("/api/v1/chat/sessions/new", headers=headers)
        session_id = created.json()["data"]["session_id"]
        checkpoint = f"checkpoint-{index}"
        origin = (
            f"webchat:FriendMessage:webchat!{account['resource_owner']}!{session_id}"
        )
        await core.conversation_manager.new_conversation(
            origin,
            content=[
                {"role": "user", "content": f"private question {index}"},
                {"role": "assistant", "content": f"private answer {index}"},
                {"role": "_checkpoint", "content": {"id": checkpoint}},
            ],
        )
        records = []
        for kind, text in [
            ("user", f"private question {index}"),
            ("bot", f"private answer {index}"),
        ]:
            records.append(
                await db.insert_platform_message_history(
                    platform_id="webchat",
                    user_id=session_id,
                    content={
                        "type": kind,
                        "message": [{"type": "plain", "text": text}],
                    },
                    sender_id=account["resource_owner"] if kind == "user" else "bot",
                    sender_name=kind,
                    llm_checkpoint_id=checkpoint,
                )
            )
        resources.append((account, session_id, records))
    yield client, server, core, db, resources
    for _, session_id, _ in resources:
        webchat_queue_mgr.remove_queues(session_id)


@pytest.mark.asyncio
async def test_organization_batches_search_and_database_reopen(private_history):
    client, server, core, db, resources = private_history
    account, session_id, records = resources[0]
    foreign_id = resources[1][1]
    headers = {"Authorization": f"Bearer {account['token']}"}
    response = await client.patch(
        f"/api/v1/chat/sessions/{session_id}",
        headers=headers,
        json={"display_name": "My renamed chat"},
    )
    assert response.json()["status"] == "ok", response.text
    response = await client.patch(
        f"/api/v1/chat/sessions/{session_id}", headers=headers, json={"starred": True}
    )
    assert response.json()["status"] == "ok", response.text
    listing = await client.get("/api/v1/chat/sessions", headers=headers)
    assert "My renamed chat" in listing.text and foreign_id not in listing.text
    assert listing.json()["data"][0]["starred"] is True
    for current, own_id, own_records in resources:
        own_headers = {"Authorization": f"Bearer {current['token']}"}
        result = await client.get(
            "/api/v1/messages/search",
            headers=own_headers,
            params={"q": "private", "page_size": 1},
        )
        assert result.status_code == 200, result.text
        assert [row["session_id"] for row in result.json()["data"]["results"]] == [
            own_id
        ]
        assert all(
            other_id not in result.text
            for _, other_id, _ in resources
            if other_id != own_id
        )
        page = await client.get(
            f"/api/v1/chat/sessions/{own_id}", headers=own_headers, params={"limit": 1}
        )
        assert page.json()["data"]["has_more"] is True
        older = await client.get(
            f"/api/v1/chat/sessions/{own_id}/history",
            headers=own_headers,
            params={"before_id": own_records[1].id, "limit": 1},
        )
        assert [row["id"] for row in older.json()["data"]["history"]] == [
            own_records[0].id
        ]
        markers = await client.get(
            f"/api/v1/chat/sessions/{own_id}/markers", headers=own_headers
        )
        assert [row["id"] for row in markers.json()["data"]["markers"]] == [
            own_records[0].id
        ]
    for action in ("batch-archive", "batch-unarchive"):
        result = await client.post(
            f"/api/v1/chat/sessions/{action}",
            headers=headers,
            json={"session_ids": [session_id, foreign_id]},
        )
        data = result.json()["data"]
        assert (
            data["failed_count"] == 1
            and data["failed_items"][0]["session_id"] == foreign_id
        )
        assert not (await db.get_platform_session_by_id(foreign_id)).archived
    await client.post(f"/api/v1/chat/sessions/{session_id}/archive", headers=headers)
    archived = await client.get(
        "/api/v1/chat/sessions/archived",
        headers=headers,
        params={"search": "renamed", "page_size": 1},
    )
    assert archived.json()["data"]["pagination"]["total"] == 1
    assert foreign_id not in archived.text
    await db.engine.dispose()
    reopened = SQLiteDatabase(db.db_path)
    await reopened.initialize()
    try:
        saved = await reopened.get_platform_session_by_id(session_id)
        assert (
            saved.display_name == "My renamed chat" and saved.starred and saved.archived
        )
        assert (
            await reopened.count_platform_message_history(
                platform_id="webchat", user_id=session_id
            )
            == 2
        )
    finally:
        await reopened.engine.dispose()
    deleted = await client.post(
        "/api/chat/batch_delete_sessions",
        headers=headers,
        json={"session_ids": [session_id, foreign_id]},
    )
    assert deleted.json()["data"]["deleted_count"] == 1
    assert await db.get_platform_session_by_id(session_id) is None
    assert (
        await db.count_platform_message_history(
            platform_id="webchat", user_id=foreign_id
        )
        == 2
    )


@pytest.mark.asyncio
async def test_foreign_ids_formal_and_legacy_are_denied_without_mutation(
    private_history,
):
    client, server, core, db, resources = private_history
    _, target, records = resources[0]
    for caller, own_id, own_records in resources[1:]:
        headers = {"Authorization": f"Bearer {caller['token']}"}
        for method, path, payload in [
            (
                "PATCH",
                f"/api/v1/chat/sessions/{own_id}/messages/{own_records[0].id}",
                {
                    "content": {
                        "type": "user",
                        "message": [
                            {"type": "reply", "message_id": records[0].id},
                            {"type": "plain", "text": "forged quote"},
                        ],
                    }
                },
            ),
            ("PATCH", f"/api/v1/chat/sessions/{target}", {"display_name": "stolen"}),
            ("PATCH", f"/api/v1/chat/sessions/{target}", {"starred": True}),
            ("POST", f"/api/v1/chat/sessions/{target}/archive", {}),
            ("POST", f"/api/v1/chat/sessions/{target}/unarchive", {}),
            ("DELETE", f"/api/v1/chat/sessions/{target}", None),
            ("GET", f"/api/v1/chat/sessions/{target}/markers", None),
            (
                "GET",
                f"/api/v1/chat/sessions/{target}/history?before_id={records[1].id}",
                None,
            ),
            ("GET", f"/api/chat/delete_session?session_id={target}", None),
            (
                "POST",
                "/api/chat/update_session_display_name",
                {"session_id": target, "display_name": "stolen"},
            ),
            (
                "PATCH",
                f"/api/v1/chat/sessions/{own_id}/messages/{records[0].id}",
                {
                    "content": {
                        "type": "user",
                        "message": [{"type": "plain", "text": "stolen"}],
                    }
                },
            ),
            (
                "POST",
                f"/api/v1/chat/sessions/{own_id}/messages/{records[1].id}/regenerate",
                {},
            ),
            (
                "POST",
                "/api/chat/message/edit",
                {
                    "session_id": own_id,
                    "message_id": records[0].id,
                    "content": {"type": "user", "message": []},
                },
            ),
            (
                "POST",
                "/api/chat/message/regenerate",
                {"session_id": own_id, "message_id": records[1].id},
            ),
            (
                "POST",
                "/api/v1/chat",
                {
                    "session_id": own_id,
                    "message": [
                        {"type": "reply", "message_id": records[0].id},
                        {"type": "plain", "text": "quote theft"},
                    ],
                },
            ),
        ]:
            response = await client.request(method, path, headers=headers, json=payload)
            assert (
                response.status_code >= 400 or response.json()["status"] == "error"
            ), (method, path, response.text)
            assert (
                "private question 0" not in response.text
                and "private answer 0" not in response.text
            )
        assert (await db.get_platform_session_by_id(target)).display_name != "stolen"
        assert not (await db.get_platform_session_by_id(target)).starred
        assert (await db.get_platform_message_history_by_id(records[0].id)).content[
            "message"
        ][0]["text"] == "private question 0"
        assert not server.asgi_app.state.services.chat.chat_runs
    # Middleware must resolve actual route parameters before FastAPI routing.
    platform_session = await db.create_platform_session(
        creator=resources[0][0]["resource_owner"], platform_id="qq"
    )
    unsupported = await client.patch(
        f"/api/v1/chat/sessions/{platform_session.session_id}",
        headers={"Authorization": f"Bearer {resources[0][0]['token']}"},
        json={"display_name": "wrong platform"},
    )
    assert unsupported.status_code == 403
    assert (
        await db.get_platform_session_by_id(platform_session.session_id)
    ).display_name != "wrong platform"

    # Project, branch, side-thread and ZIP endpoints stay closed for ordinary accounts.
    headers = {"Authorization": f"Bearer {resources[0][0]['token']}"}
    for path in [
        f"/api/v1/chat/sessions/{target}/messages/{records[1].id}/branch",
        "/api/v1/chat/threads",
        "/api/v1/chat/import/stage",
    ]:
        assert (await client.post(path, headers=headers, json={})).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_edit_continue_retry_and_owned_thread_cascades(private_history, legacy):
    client, server, core, db, resources = private_history
    account, session_id, records = resources[0]
    headers = {"Authorization": f"Bearer {account['token']}"}
    own_thread = await db.create_webchat_thread(
        creator=account["resource_owner"],
        parent_session_id=session_id,
        parent_message_id=records[1].id,
        selected_text="own",
        base_checkpoint_id="checkpoint-0",
    )
    foreign_thread = await db.create_webchat_thread(
        creator=resources[1][0]["resource_owner"],
        parent_session_id=session_id,
        parent_message_id=records[1].id,
        selected_text="foreign",
        base_checkpoint_id="checkpoint-0",
    )
    response = await client.request(
        "POST" if legacy else "PATCH",
        "/api/chat/message/edit"
        if legacy
        else f"/api/v1/chat/sessions/{session_id}/messages/{records[0].id}",
        headers=headers,
        json={
            **(
                {"session_id": session_id, "message_id": records[0].id}
                if legacy
                else {}
            ),
            "content": {
                "type": "user",
                "message": [{"type": "plain", "text": "edited question"}],
            },
        },
    )
    assert response.json()["status"] == "ok", response.text
    assert await db.get_platform_message_history_by_id(records[1].id) is None
    assert await db.get_webchat_thread_by_id(own_thread.thread_id) is None
    assert await db.get_webchat_thread_by_id(foreign_thread.thread_id) is not None
    saved = response.json()["data"]["message"]
    continuation = {
        "session_id": session_id,
        "message": "edited question",
        "_skip_user_history": True,
        "_llm_checkpoint_id": saved["llm_checkpoint_id"],
    }
    for body in [
        dict(continuation, message="not the saved text"),
        dict(continuation, _llm_checkpoint_id="foreign-checkpoint"),
        dict(continuation, _platform_history_id="webchat_thread"),
    ]:
        denied = await client.post("/api/v1/chat", headers=headers, json=body)
        assert denied.status_code >= 400 or denied.json()["status"] == "error", (
            denied.text
        )

    async def external_model():
        sender, target, payload = await webchat_queue_mgr.get_or_create_queue(
            session_id
        ).get()
        checkpoint = payload["llm_checkpoint_id"]
        origin = f"webchat:FriendMessage:webchat!{sender}!{target}"
        cid = await core.conversation_manager.get_curr_conversation_id(origin)
        await core.conversation_manager.update_conversation(
            origin,
            cid,
            history=[
                {"role": "user", "content": "edited question"},
                {"role": "assistant", "content": "regenerated reply"},
                {"role": "_checkpoint", "content": {"id": checkpoint}},
            ],
        )
        back = webchat_queue_mgr.get_or_create_back_queue(payload["message_id"], target)
        await back.put(
            {"type": "plain", "data": "regenerated reply", "streaming": True}
        )
        await back.put({"type": "end"})

    worker = asyncio.create_task(external_model())
    reply = await asyncio.wait_for(
        client.post("/api/v1/chat", headers=headers, json=continuation), 10
    )
    await worker
    assert "regenerated reply" in reply.text, reply.text
    history = (
        await client.get(f"/api/v1/chat/sessions/{session_id}", headers=headers)
    ).json()["data"]["history"]
    assert len([row for row in history if row["content"]["type"] == "user"]) == 1
    bot = next(row for row in history if row["content"]["type"] == "bot")
    worker = asyncio.create_task(external_model())
    reply = await asyncio.wait_for(
        client.post(
            "/api/chat/message/regenerate"
            if legacy
            else f"/api/v1/chat/sessions/{session_id}/messages/{bot['id']}/regenerate",
            headers=headers,
            json={"session_id": session_id, "message_id": bot["id"]} if legacy else {},
        ),
        10,
    )
    await worker
    assert "regenerated reply" in reply.text, reply.text
    worker = asyncio.create_task(external_model())
    quote = await asyncio.wait_for(
        client.post(
            "/api/chat/send" if legacy else "/api/v1/chat",
            headers=headers,
            json={
                "session_id": session_id,
                "message": [
                    {"type": "reply", "message_id": records[0].id},
                    {"type": "plain", "text": "owned quote"},
                ],
            },
        ),
        10,
    )
    await worker
    assert "regenerated reply" in quote.text, quote.text
    quote_history = (
        await client.get(f"/api/v1/chat/sessions/{session_id}", headers=headers)
    ).json()["data"]["history"]
    assert any(
        part.get("type") == "reply" and part["message_id"] == records[0].id
        for row in quote_history
        for part in row["content"]["message"]
    )
    # A quote to an earlier retained message must still permit a normal retry.
    quoted_bot = next(
        row for row in reversed(quote_history) if row["content"]["type"] == "bot"
    )
    worker = asyncio.create_task(external_model())
    retried_quote = await asyncio.wait_for(
        client.post(
            "/api/chat/message/regenerate"
            if legacy
            else f"/api/v1/chat/sessions/{session_id}/messages/{quoted_bot['id']}/regenerate",
            headers=headers,
            json={"session_id": session_id, "message_id": quoted_bot["id"]}
            if legacy
            else {},
        ),
        10,
    )
    if "regenerated reply" not in retried_quote.text:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    assert "regenerated reply" in retried_quote.text, retried_quote.text
    await asyncio.wait_for(worker, 2)
    assert await db.get_platform_message_history_by_id(records[0].id) is not None
    quoted_source = next(
        row for row in reversed(quote_history) if row["content"]["type"] == "user"
    )
    edited_quote = await client.request(
        "POST" if legacy else "PATCH",
        "/api/chat/message/edit"
        if legacy
        else f"/api/v1/chat/sessions/{session_id}/messages/{quoted_source['id']}",
        headers=headers,
        json={
            **(
                {"session_id": session_id, "message_id": quoted_source["id"]}
                if legacy
                else {}
            ),
            "content": {
                "type": "user",
                "message": [
                    {"type": "reply", "message_id": records[0].id},
                    {"type": "plain", "text": "edited owned quote"},
                ],
            },
        },
    )
    assert edited_quote.json()["status"] == "ok", edited_quote.text
    saved_quote = edited_quote.json()["data"]["message"]
    worker = asyncio.create_task(external_model())
    continued_quote = await asyncio.wait_for(
        client.post(
            "/api/chat/send" if legacy else "/api/v1/chat",
            headers=headers,
            json={
                "session_id": session_id,
                "message": saved_quote["content"]["message"],
                "_skip_user_history": True,
                "_llm_checkpoint_id": saved_quote["llm_checkpoint_id"],
            },
        ),
        10,
    )
    if "regenerated reply" not in continued_quote.text:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    assert "regenerated reply" in continued_quote.text, continued_quote.text
    await asyncio.wait_for(worker, 2)
    assert await db.get_platform_message_history_by_id(records[0].id) is not None
    # A mismatched child owner must survive parent deletion too.
    deleted = await client.delete(
        f"/api/v1/chat/sessions/{session_id}", headers=headers
    )
    assert deleted.json()["status"] == "ok", deleted.text
    assert await db.get_webchat_thread_by_id(foreign_thread.thread_id) is not None
    assert (
        await db.count_platform_message_history(
            platform_id="webchat", user_id=resources[1][1]
        )
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True], ids=["formal", "legacy"])
@pytest.mark.parametrize(
    "restored_input",
    [
        "foreign_reply",
        "empty",
        "reply_only",
        "deleted_bot_reply",
        "missing_reply",
        "invalid_reply",
    ],
)
@pytest.mark.parametrize("operation", ["retry", "edit"])
async def test_history_preflight_rejects_invalid_inputs_without_mutation(
    private_history, legacy, restored_input, operation
):
    client, server, core, db, resources = private_history
    account, session_id, records = resources[0]
    service = server.asgi_app.state.services.chat
    if restored_input == "empty":
        message = []
    elif restored_input == "reply_only":
        message = [{"type": "reply", "message_id": records[0].id}]
    else:
        reply_id = {
            "foreign_reply": resources[1][2][0].id,
            "deleted_bot_reply": records[1].id,
            "missing_reply": 999999999,
            "invalid_reply": "not-a-message-id",
        }[restored_input]
        message = [
            {"type": "reply", "message_id": reply_id},
            {"type": "plain", "text": "restored user text"},
        ]
    if operation == "retry":
        # Restored state is intentionally malformed; public send would reject it.
        await service.platform_history_mgr.update(
            message_id=records[0].id, content={"type": "user", "message": message}
        )
    thread = await db.create_webchat_thread(
        creator=account["resource_owner"],
        parent_session_id=session_id,
        parent_message_id=records[1].id,
        selected_text="retained child",
        base_checkpoint_id=records[0].llm_checkpoint_id,
    )
    child_history = await db.insert_platform_message_history(
        platform_id="webchat_thread",
        user_id=thread.thread_id,
        content={
            "type": "user",
            "message": [{"type": "plain", "text": "child history"}],
        },
        sender_id=account["resource_owner"],
        sender_name=account["username"],
    )
    thread_before = (await db.get_webchat_thread_by_id(thread.thread_id)).model_dump()
    child_before = (
        await db.get_platform_message_history_by_id(child_history.id)
    ).model_dump()
    session_before = (await db.get_platform_session_by_id(session_id)).model_dump()
    source_before = (
        await db.get_platform_message_history_by_id(records[0].id)
    ).model_dump()
    bot_before = (
        await db.get_platform_message_history_by_id(records[1].id)
    ).model_dump()
    session = await db.get_platform_session_by_id(session_id)
    conversation_before = await service.load_current_conversation_history(session)
    if operation == "retry":
        method = "POST"
        endpoint = (
            "/api/chat/message/regenerate"
            if legacy
            else f"/api/v1/chat/sessions/{session_id}/messages/{records[1].id}/regenerate"
        )
        payload = (
            {"session_id": session_id, "message_id": records[1].id} if legacy else {}
        )
    else:
        method = "POST" if legacy else "PATCH"
        endpoint = (
            "/api/chat/message/edit"
            if legacy
            else f"/api/v1/chat/sessions/{session_id}/messages/{records[0].id}"
        )
        payload = {
            **(
                {"session_id": session_id, "message_id": records[0].id}
                if legacy
                else {}
            ),
            "content": {"type": "user", "message": message},
        }
    response = await client.request(
        method,
        endpoint,
        headers={"Authorization": f"Bearer {account['token']}"},
        json=payload,
    )
    assert response.json()["status"] == "error", response.text
    assert "private question 1" not in response.text
    assert (
        await service.load_current_conversation_history(session) == conversation_before
    )
    assert (
        await db.get_platform_message_history_by_id(records[0].id)
    ).model_dump() == source_before
    assert (
        await db.get_platform_message_history_by_id(records[1].id)
    ).model_dump() == bot_before
    assert (
        await db.get_platform_session_by_id(session_id)
    ).model_dump() == session_before
    assert (
        await db.get_webchat_thread_by_id(thread.thread_id)
    ).model_dump() == thread_before
    assert (
        await db.get_platform_message_history_by_id(child_history.id)
    ).model_dump() == child_before
    assert not service.chat_runs
