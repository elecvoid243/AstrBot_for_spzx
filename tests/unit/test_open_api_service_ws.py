import asyncio
import json
from types import SimpleNamespace

import pytest

from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.dashboard.services.open_api_service import (
    OpenApiService,
    OpenApiServiceError,
    OpenApiWebSocketChatBridge,
)


def _service() -> OpenApiService:
    core_lifecycle = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[]),
        platform_message_history_manager=None,
        astrbot_config_mgr=SimpleNamespace(
            confs={"default": {"admins_id": ["admin-user"]}}
        ),
    )
    return OpenApiService(SimpleNamespace(), core_lifecycle)


def _bridge() -> OpenApiWebSocketChatBridge:
    async def build_user_message_parts(_message):
        return []

    async def create_attachment_from_file(_filename, _attach_type):
        return None

    async def insert_user_message(_session_id, _effective_username, _message_parts):
        pass

    async def save_bot_message(_session_id, _message_parts, _agent_stats, _refs):
        return None

    return OpenApiWebSocketChatBridge(
        build_user_message_parts=build_user_message_parts,
        create_attachment_from_file=create_attachment_from_file,
        extract_web_search_refs=lambda _text, _parts: {},
        insert_user_message=insert_user_message,
        save_bot_message=save_bot_message,
    )


@pytest.mark.asyncio
async def test_run_chat_websocket_closes_when_api_key_is_invalid(monkeypatch):
    service = _service()
    sent: list[dict] = []
    closed: list[tuple[int, str]] = []

    async def authenticate_api_key(_raw_key):
        return None, "Invalid API key"

    monkeypatch.setattr(service, "authenticate_api_key", authenticate_api_key)

    async def receive_json():
        raise AssertionError("receive_json should not be called")

    async def send_json(payload: dict) -> None:
        sent.append(payload)

    async def close(code: int, reason: str) -> None:
        closed.append((code, reason))

    await service.run_chat_websocket(
        raw_api_key="bad",
        receive_json=receive_json,
        send_json=send_json,
        close=close,
        conf_list=[],
        chat_bridge=_bridge(),
    )

    assert sent == [
        {"type": "error", "code": "UNAUTHORIZED", "data": "Invalid API key"}
    ]
    assert closed == [(1008, "Invalid API key")]


@pytest.mark.asyncio
async def test_run_chat_websocket_handles_control_messages(monkeypatch):
    service = _service()
    messages = iter(
        [
            ["not", "an", "object"],
            {"t": "ping"},
            {"t": "unknown"},
            {"t": "send", "message": "hello"},
        ]
    )
    sent: list[dict] = []
    handled: list[dict] = []

    async def authenticate_api_key(_raw_key):
        return ["chat", "chat:admin"], None

    async def handle_chat_ws_send(**kwargs):
        handled.append(
            {
                "post_data": kwargs["post_data"],
                "allow_admin_username": kwargs["allow_admin_username"],
            }
        )

    monkeypatch.setattr(service, "authenticate_api_key", authenticate_api_key)
    monkeypatch.setattr(service, "handle_chat_ws_send", handle_chat_ws_send)

    async def receive_json():
        try:
            return next(messages)
        except StopIteration as exc:
            raise RuntimeError("disconnect") from exc

    async def send_json(payload: dict) -> None:
        sent.append(payload)

    async def close(_code: int, _reason: str) -> None:
        raise AssertionError("close should not be called")

    await service.run_chat_websocket(
        raw_api_key="good",
        receive_json=receive_json,
        send_json=send_json,
        close=close,
        conf_list=[],
        chat_bridge=_bridge(),
    )

    assert sent == [
        {
            "type": "error",
            "code": "INVALID_MESSAGE",
            "data": "message must be an object",
        },
        {"type": "pong"},
        {
            "type": "error",
            "code": "INVALID_MESSAGE",
            "data": "Unsupported message type: unknown",
        },
    ]
    assert handled == [
        {
            "post_data": {"t": "send", "message": "hello"},
            "allow_admin_username": True,
        }
    ]


@pytest.mark.asyncio
async def test_prepare_chat_send_rejects_configured_admin_username():
    """The shared HTTP/WS boundary must reject administrator impersonation."""
    service = _service()

    with pytest.raises(
        OpenApiServiceError,
        match="username is reserved for an AstrBot administrator",
    ):
        await service.prepare_chat_send(
            {"username": "admin-user", "message": "hello"},
            [],
        )


@pytest.mark.asyncio
async def test_prepare_chat_send_allows_admin_username_with_subscope(monkeypatch):
    """The explicit chat-admin subscope should preserve legitimate admin calls."""
    service = _service()

    async def ensure_chat_session(_username, _session_id):
        return None

    monkeypatch.setattr(service, "ensure_chat_session", ensure_chat_session)

    username, session_id, config_id = await service.prepare_chat_send(
        {
            "username": "admin-user",
            "session_id": "admin-session",
            "message": "hello",
        },
        [],
        allow_admin_username=True,
    )

    assert username == "admin-user"
    assert session_id == "admin-session"
    assert config_id is None


@pytest.mark.asyncio
async def test_ws_stream_skips_snapshot_telemetry_but_keeps_plain_and_tools(
    monkeypatch,
):
    service = _service()
    sent: list[dict] = []
    saved: list[list[dict]] = []
    snapshot_payload = json.dumps(
        {"items": [{"snapshot_id": "snap-secret", "raw": {"api_key": "PRIVATE"}}]}
    )

    async def prepare_chat_send(_post_data, _conf_list, **_kwargs):
        return "alice", "api-session", None

    async def update_session_config_route(**_kwargs):
        return None

    async def build_user_message_parts(_message):
        return [{"type": "plain", "text": "hello"}]

    async def create_attachment_from_file(_filename, _attach_type):
        return None

    async def insert_user_message(_session_id, _username, _parts):
        return None

    async def save_bot_message(_session_id, parts, _stats, _refs, **_kwargs):
        saved.append(parts)
        return None

    bridge = OpenApiWebSocketChatBridge(
        build_user_message_parts=build_user_message_parts,
        create_attachment_from_file=create_attachment_from_file,
        extract_web_search_refs=lambda _text, _parts: {},
        insert_user_message=insert_user_message,
        save_bot_message=save_bot_message,
    )
    monkeypatch.setattr(service, "prepare_chat_send", prepare_chat_send)
    monkeypatch.setattr(
        service,
        "update_session_config_route",
        update_session_config_route,
    )

    async def send_json(payload: dict) -> None:
        sent.append(payload)

    async def send_error(message: str, code: str) -> None:
        raise AssertionError(f"unexpected error: {code}: {message}")

    back_queue_ready = asyncio.Event()
    original_back_queue = webchat_queue_mgr.get_or_create_back_queue

    def tracking_back_queue(request_id, conversation_id=None):
        queue = original_back_queue(request_id, conversation_id)
        back_queue_ready.set()
        return queue

    monkeypatch.setattr(
        webchat_queue_mgr,
        "get_or_create_back_queue",
        tracking_back_queue,
    )

    async def generate() -> None:
        queue = webchat_queue_mgr.get_or_create_queue("api-session")
        _username, _session_id, payload = await queue.get()
        message_id = payload["message_id"]
        await back_queue_ready.wait()
        for event in (
            {
                "type": "plain",
                "chain_type": "llm_request_snapshots",
                "data": snapshot_payload,
                "streaming": False,
            },
            {
                "type": "tool_call",
                "chain_type": "tool_call",
                "data": "tool noise",
                "streaming": False,
            },
            {"type": "plain", "data": "hello", "streaming": True},
            {"type": "end", "data": "", "streaming": False},
        ):
            await webchat_queue_mgr.put_back_queue(
                message_id,
                {**event, "message_id": message_id},
            )

    worker = asyncio.create_task(generate())
    try:
        await asyncio.wait_for(
            service.handle_chat_ws_send(
                post_data={"message": "hello", "message_id": "api-run"},
                conf_list=[],
                chat_bridge=bridge,
                send_json=send_json,
                send_error=send_error,
            ),
            timeout=5,
        )
        await worker
    finally:
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        webchat_queue_mgr.remove_queues("api-session")

    assert "snap-secret" not in json.dumps(sent)
    assert not any(
        payload.get("chain_type") == "llm_request_snapshots" for payload in sent
    )
    assert any(payload.get("type") == "tool_call" for payload in sent)
    assert any(
        payload.get("type") == "plain" and payload.get("data") == "hello"
        for payload in sent
    )
    assert any(payload.get("type") == "end" for payload in sent)
    assert saved == [[{"type": "plain", "text": "hello"}]]
