"""Authenticated ChatUI names reach prompts without changing resource ownership."""

import asyncio

import pytest

from astrbot.core.astr_main_agent import _append_system_reminders
from astrbot.core.platform import AstrBotMessage, MessageMember, MessageType
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.utils.webchat_access import DashboardPrincipal
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_authenticated_account_names_reach_identifier_prompts(
    ordinary_chat, enabled
):
    """Exercise login, real send routes, the queue, adapter and prompt decoration.

    Args:
        ordinary_chat: Production Dashboard with an administrator and two users.
        enabled: Profile's existing user-identification setting.
    """
    client, _, core, _, admin, users = ordinary_chat
    core.astrbot_config["provider_settings"]["identifier"] = enabled
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    for index, account in enumerate([admin, *users]):
        headers = {"Authorization": f"Bearer {account['token']}"}
        response = await client.get("/api/v1/chat/sessions/new", headers=headers)
        sid = response.json()["data"]["session_id"]
        prompts = []

        async def consume():
            incoming = await webchat_queue_mgr.get_or_create_queue(sid).get()
            event = adapter.create_event(await adapter.convert_message(incoming))
            assert event.get_sender_id() == account["resource_owner"]
            assert event.get_sender_name() == account["username"]
            assert event.unified_msg_origin == (
                f"webchat:FriendMessage:webchat!{account['resource_owner']}!{sid}"
            )
            req = ProviderRequest(prompt=event.message_str)
            _append_system_reminders(event, req, {"identifier": enabled}, timezone=None)
            prompts.extend(part.text for part in req.extra_user_content_parts)
            message_id = incoming[2]["message_id"]
            await webchat_queue_mgr.put_back_queue(
                message_id, {"type": "plain", "data": "reply", "streaming": True}
            )
            await webchat_queue_mgr.put_back_queue(message_id, {"type": "end"})

        worker = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    worker,
                    client.post(
                        "/api/chat/send" if index == 1 else "/api/v1/chat",
                        headers=headers,
                        json={"session_id": sid, "message": "What is my account name?"},
                    ),
                ),
                timeout=15,
            )
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        if enabled:
            text = "\n".join(prompts)
            assert f'"username": "{account["username"]}"' in text
            assert f'"user_id": "{account["resource_owner"]}"' in text
            assert "Current authenticated ChatUI user" in text
            assert "another user's memories" in text
            for other in [admin, *users]:
                if other is not account:
                    assert other["username"] not in text
        else:
            assert prompts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "principal, expected_name",
    [
        (DashboardPrincipal("account_1", "account_1", "user", "johnmac"), "johnmac"),
        (
            DashboardPrincipal("admin_1", "account_1", "admin", "renamed-admin"),
            "renamed-admin",
        ),
        ({"resource_owner": "account_1", "login_name": "forged"}, "account_1"),
        (
            DashboardPrincipal("account_2", "account_2", "user", "foreign-user"),
            "account_1",
        ),
        (None, "account_1"),
    ],
)
async def test_adapter_uses_only_matching_trusted_identity(principal, expected_name):
    """Ignore client-shaped identity fields and another account's principal.

    Args:
        principal: Trusted principal or an invalid attempted substitution.
        expected_name: Expected nickname after adapter admission.
    """
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    payload = {
        "message_id": "request-1",
        "message": [{"type": "plain", "text": "hello"}],
        "username": "forged",
        "nickname": "forged",
        "_dashboard_account": principal,
    }
    event = adapter.create_event(
        await adapter.convert_message(("account_1", "session-1", payload))
    )
    assert event.get_sender_id() == "account_1"
    assert event.get_sender_name() == expected_name
    req = ProviderRequest(prompt="hello")
    _append_system_reminders(event, req, {"identifier": True}, None)
    text = "\n".join(part.text for part in req.extra_user_content_parts)
    assert "forged" not in text and "foreign-user" not in text
    assert ("Current authenticated ChatUI user" in text) == (
        expected_name != "account_1"
    )


def test_identifier_preserves_other_platform_names():
    """Keep the original platform ID/nickname prompt for non-ChatUI events."""
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.sender = MessageMember("123456", "QQ nickname")
    event = AstrMessageEvent(
        "hello",
        message,
        PlatformMetadata(name="aiocqhttp", id="qq", description="QQ"),
        "123456",
    )
    req = ProviderRequest(prompt="hello")
    _append_system_reminders(event, req, {"identifier": True}, None)
    assert req.extra_user_content_parts[0].text == (
        "<system_reminder>User ID: 123456, Nickname: QQ nickname</system_reminder>"
    )
