"""Regressions for administrator Chat and independent API key compatibility."""

import asyncio

import jwt
import pytest

from astrbot.core import sp
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat


@pytest.mark.asyncio
async def test_explicit_api_key_precedes_cookies_without_identity_fallback(
    ordinary_chat,
):
    """All existing key transports keep their own scopes despite ambient cookies."""
    client, server, core, db, admin, users = ordinary_chat
    headers = {"Authorization": f"Bearer {admin['token']}"}
    created = await client.post(
        "/api/v1/api-keys",
        headers=headers,
        json={"name": "precedence-check", "scopes": ["data"]},
    )
    key = created.json()["data"]["api_key"]
    expired = jwt.encode(
        {
            **jwt.decode(users[0]["token"], options={"verify_signature": False}),
            "exp": 1,
        },
        server.asgi_app.state.jwt_secret,
        algorithm="HS256",
    )
    for cookie in (None, users[0]["token"], expired):
        client.cookies.clear()
        if cookie:
            client.cookies.set("astrbot_dashboard_jwt", cookie)
        for transport in (
            {"headers": {"Authorization": f"ApiKey {key}"}},
            {"headers": {"X-API-Key": key}},
            {"params": {"api_key": key}},
            {"params": {"key": key}},
            {"headers": {"Authorization": f"Bearer {key}"}},
        ):
            result = await client.get("/api/v1/conversations", **transport)
            assert result.status_code == 200 and result.json()["status"] == "ok", (
                result.text
            )
            assert (
                await client.get("/api/v1/providers", **transport)
            ).status_code == 403
    # Explicit invalid/empty keys and Dashboard JWTs presented as API keys must
    # never acquire the ambient administrator's full permissions.
    client.cookies.set("astrbot_dashboard_jwt", admin["token"])
    for invalid in ("invalid-key", "", admin["token"], expired):
        for transport in (
            {"headers": {"Authorization": f"ApiKey {invalid}"}},
            {"headers": {"X-API-Key": invalid}},
            {"params": {"api_key": invalid}},
        ):
            assert (
                await client.get("/api/v1/conversations", **transport)
            ).status_code == 401
    assert (
        await client.get("/api/v1/accounts", headers={"Authorization": f"ApiKey {key}"})
    ).status_code == 401


@pytest.mark.asyncio
async def test_short_webchat_origins_require_real_owned_sessions(ordinary_chat):
    """Workflow-style origins remain usable only for their real session owner."""
    client, server, core, db, admin, users = ordinary_chat
    headers = {"Authorization": f"Bearer {admin['token']}"}
    own = await db.create_platform_session(
        creator=admin["resource_owner"], platform_id="webchat"
    )
    foreign = await db.create_platform_session(
        creator=users[0]["resource_owner"], platform_id="webchat"
    )
    origins = [f"webchat:FriendMessage:{s.session_id}" for s in (own, foreign)]
    origins += [
        "webchat:FriendMessage:missing",
        f"webchat:FriendMessage:webchat!{admin['resource_owner']}!{foreign.session_id}",
    ]
    records = [
        await db.create_conversation(
            user_id=origin,
            platform_id="webchat",
            content=[{"role": "user", "content": f"history-{i}"}],
        )
        for i, origin in enumerate(origins)
    ]
    for i, (origin, record) in enumerate(zip(origins, records)):
        result = await client.get(
            f"/api/v1/conversations/{record.conversation_id}",
            params={"user_id": origin},
            headers=headers,
        )
        assert (result.json()["status"] == "ok") == (i == 0), result.text
        updated = await client.post(
            "/api/session/update-rule",
            headers=headers,
            json={
                "umo": origin,
                "rule_key": "session_service_config",
                "rule_value": {"custom_name": "owned workflow"},
            },
        )
        assert (updated.json()["status"] == "ok") == (i == 0), updated.text
    listed = await client.get("/api/v1/conversations", headers=headers)
    assert "history-0" in listed.text and "history-1" not in listed.text
    active = (await client.get("/api/v1/sessions/active-umos", headers=headers)).json()[
        "data"
    ]["umos"]
    assert origins[0] in active and all(origin not in active for origin in origins[1:])
    await sp.put_async(
        "unknown",
        "unknown",
        "session_groups",
        {
            str(i): {"name": str(i), "umos": [origin]}
            for i, origin in enumerate(origins)
        },
    )
    groups = await client.get("/api/v1/session-groups", headers=headers)
    assert [group["id"] for group in groups.json()["data"]["groups"]] == ["0"]
    # Kernel/pipeline use has no Dashboard context and still sees both owners.
    assert await db.get_conversation_by_id(records[1].conversation_id) is not None
    await core.conversation_manager.update_conversation(
        origins[0],
        records[0].conversation_id,
        history=[{"role": "user", "content": "workflow continues"}],
    )
    assert (await db.get_conversation_by_id(records[0].conversation_id)).content == [
        {"role": "user", "content": "workflow continues"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("with_attachment", [False, True])
async def test_admin_edit_continuation_and_regeneration_validate_saved_turn(
    ordinary_chat,
    tmp_path,
    with_attachment,
):
    """Editing, continuing and retrying preserve one linked user record over HTTP."""
    client, server, core, db, admin, users = ordinary_chat
    headers = {"Authorization": f"Bearer {admin['token']}"}
    session = await db.create_platform_session(
        creator=admin["resource_owner"], platform_id="webchat"
    )
    server.asgi_app.state.services.chat.conv_mgr = core.conversation_manager
    sid = session.session_id
    edited_parts = [{"type": "plain", "text": "edited"}]
    stored_parts = list(edited_parts)
    if with_attachment:
        attachment_path = tmp_path / "note.txt"
        attachment_path.write_text("Administrator attachment", encoding="utf-8")
        attachment = await db.insert_attachment(
            str(attachment_path), "file", "text/plain"
        )
        part = {
            "type": "file",
            "attachment_id": attachment.attachment_id,
            "filename": "note.txt",
        }
        edited_parts.append(part)
        stored_parts.append({**part, "url": "/display-only-url"})
    umo = f"webchat:FriendMessage:webchat!{admin['resource_owner']}!{sid}"
    cid = await core.conversation_manager.new_conversation(
        umo,
        content=[
            {"role": "user", "content": "original"},
            {"role": "assistant", "content": "old reply"},
            {"role": "_checkpoint", "content": {"id": "original-checkpoint"}},
        ],
    )
    user = await core.platform_message_history_manager.insert(
        platform_id="webchat",
        user_id=sid,
        content={"type": "user", "message": [{"type": "plain", "text": "original"}]},
        sender_id=admin["resource_owner"],
        sender_name=admin["username"],
        llm_checkpoint_id="original-checkpoint",
    )
    await core.platform_message_history_manager.insert(
        platform_id="webchat",
        user_id=sid,
        content={"type": "bot", "message": [{"type": "plain", "text": "old reply"}]},
        sender_id="bot",
        sender_name="bot",
        llm_checkpoint_id="original-checkpoint",
    )
    edit = await client.post(
        "/api/chat/message/edit",
        headers=headers,
        json={
            "session_id": sid,
            "message_id": user.id,
            "content": {
                "type": "user",
                "message": stored_parts,
            },
        },
    )
    assert edit.json()["status"] == "ok", edit.text
    checkpoint = edit.json()["data"]["message"]["llm_checkpoint_id"]
    assert checkpoint and checkpoint != "original-checkpoint"
    assert (
        await db.count_platform_message_history(platform_id="webchat", user_id=sid) == 1
    )
    payload = {
        "session_id": sid,
        "message": edited_parts,
        "_skip_user_history": True,
        "_llm_checkpoint_id": checkpoint,
    }
    for override in (
        {"_llm_checkpoint_id": "foreign-checkpoint"},
        {"message": "unrecorded injection"},
        {"_llm_checkpoint_id": None},
        {"_skip_user_history": False},
        {"_platform_history_id": "webchat_thread"},
        {"_api_key_allow_admin_role": True},
    ):
        denied = await client.post(
            "/api/chat/send", headers=headers, json={**payload, **override}
        )
        assert denied.status_code >= 400 or denied.json()["status"] == "error", (
            denied.text
        )
    assert (
        await db.count_platform_message_history(platform_id="webchat", user_id=sid) == 1
    )

    async def reply_to_continuations():
        for _ in range(2):
            owner, target, queued = await webchat_queue_mgr.get_or_create_queue(
                sid
            ).get()
            assert owner == admin["resource_owner"] and target == sid
            await db.update_conversation(
                cid,
                content=[
                    {"role": "user", "content": "edited"},
                    {"role": "assistant", "content": "continued reply"},
                    {
                        "role": "_checkpoint",
                        "content": {"id": queued["llm_checkpoint_id"]},
                    },
                ],
            )
            back = webchat_queue_mgr.get_or_create_back_queue(queued["message_id"], sid)
            await back.put(
                {
                    "type": "plain",
                    "data": "continued reply",
                    "streaming": True,
                    "message_id": queued["message_id"],
                }
            )
            await back.put({"type": "end", "message_id": queued["message_id"]})

    worker = asyncio.create_task(reply_to_continuations())
    try:
        continued = await asyncio.wait_for(
            client.post("/api/chat/send", headers=headers, json=payload), timeout=10
        )
        assert continued.status_code == 200 and "continued reply" in continued.text, (
            continued.text
        )
        history = await core.platform_message_history_manager.get(
            platform_id="webchat", user_id=sid, page=1, page_size=100
        )
        assert (
            len(history) == 2
            and sum(row.content["type"] == "user" for row in history) == 1
        )
        bot = next(row for row in history if row.content["type"] == "bot")
        assert bot.llm_checkpoint_id == checkpoint
        retried = await asyncio.wait_for(
            client.post(
                "/api/chat/message/regenerate",
                headers=headers,
                json={"session_id": sid, "message_id": bot.id},
            ),
            timeout=10,
        )
        assert retried.status_code == 200 and "continued reply" in retried.text, (
            retried.text
        )
        await asyncio.wait_for(worker, timeout=10)
        history = await core.platform_message_history_manager.get(
            platform_id="webchat", user_id=sid, page=1, page_size=100
        )
        assert (
            len(history) == 2
            and sum(row.content["type"] == "user" for row in history) == 1
        )
        assert len({row.llm_checkpoint_id for row in history}) == 1
        assert (
            await client.post(
                "/api/chat/message/edit",
                headers={"Authorization": f"Bearer {users[0]['token']}"},
                json={"session_id": sid, "message_id": user.id},
            )
        ).status_code == 403
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        webchat_queue_mgr.remove_queues(sid)
