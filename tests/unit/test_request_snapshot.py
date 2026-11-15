"""Unit tests for the in-memory LLM request snapshot recorder.

The recorder is a ChatUI developer-mode feature: it is default-off, only
keeps ``webchat:`` sessions, and stores sanitized payloads in memory.

Author: elecvoid243
Date: 2026-11-15
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from astrbot.core.pipeline.llm_request_trace import bounded_context_value
from astrbot.core.provider import request_snapshot
from astrbot.core.provider.request_snapshot import (
    MAX_MESSAGE_BINDINGS,
    MAX_SNAPSHOTS_PER_SESSION,
    TTL_SECONDS,
    RequestSnapshotRecorder,
    request_snapshot_scope,
    request_snapshots,
)


class FakeProvider:
    """Minimal provider surface consumed by ``capture``."""

    provider_config = {
        "api_base": "https://api.example.com/v1",
        "proxy": "",
        "timeout": 120,
    }

    def meta(self) -> SimpleNamespace:
        return SimpleNamespace(id="p1", type="openai_chat", model="m1")

    def get_model(self) -> str:
        return "m1"

    def get_current_key(self) -> str:
        return "sk-secret-1234"

    def get_keys(self) -> list[str]:
        return ["sk-secret-1234"]


@pytest.fixture(autouse=True)
def reset_recorder():
    """Keep singleton state from leaking between tests."""

    request_snapshots.reset()
    yield
    request_snapshots.reset()


def _enable(monkeypatch, enabled: bool = True) -> None:
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: enabled)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        (1, False),
        (0, False),
        ("true", False),
        ("false", False),
        (None, False),
    ],
)
def test_developer_mode_enabled_requires_a_strict_boolean(monkeypatch, raw, expected):
    monkeypatch.setattr(
        request_snapshot,
        "astrbot_config",
        {"chatui": {"developer_mode": raw}},
    )

    assert request_snapshot.developer_mode_enabled() is expected


def test_disabled_capture_is_a_noop(monkeypatch):
    _enable(monkeypatch, False)
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        assert request_snapshots.capture(FakeProvider(), {"messages": []}) is None
    assert request_snapshots.list_for_session("s1")["items"] == []


def test_disabled_capture_does_not_build_or_serialize(monkeypatch):
    _enable(monkeypatch, False)
    calls: list[object] = []
    monkeypatch.setattr(
        request_snapshot,
        "bounded_context_value",
        lambda *args, **kwargs: calls.append(args) or {},
    )
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        assert request_snapshots.capture(FakeProvider(), {"messages": []}) is None
    assert calls == []


def test_non_webchat_umo_is_not_captured(monkeypatch):
    _enable(monkeypatch)
    with request_snapshot_scope(session_id="s1", umo="qq:FriendMessage:123"):
        assert request_snapshots.capture(FakeProvider(), {"messages": []}) is None
    assert request_snapshots.list_for_session("s1")["items"] == []


def test_capture_without_scope_or_session_is_skipped(monkeypatch):
    _enable(monkeypatch)
    assert request_snapshots.capture(FakeProvider(), {"messages": []}) is None
    with request_snapshot_scope(umo="webchat:FriendMessage:s1"):
        assert request_snapshots.capture(FakeProvider(), {"messages": []}) is None
    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        is_subagent=True,
    ):
        assert request_snapshots.capture(FakeProvider(), {"messages": []}) is None


def test_capture_records_scope_and_attempts(monkeypatch):
    _enable(monkeypatch)
    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        turn_id="t1",
        invocation_id="i1",
        kind="chat",
        step=2,
    ):
        first = request_snapshots.capture(FakeProvider(), {"messages": []})
        second = request_snapshots.capture(FakeProvider(), {"messages": []})
    items = request_snapshots.list_for_session("s1")["items"]
    assert [item["snapshot_id"] for item in items] == [second, first]
    assert [item["request"]["attempt"] for item in items] == [2, 1]
    assert request_snapshots.metadata_for_invocation("i1")[0]["request"]["attempt"] == 1
    assert request_snapshots.metadata_for_invocation("i1")[1]["request"]["attempt"] == 2


def test_scope_nests_and_restores(monkeypatch):
    _enable(monkeypatch)
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        with request_snapshot_scope(kind="title", turn_id=None):
            snapshot = request_snapshots.capture(FakeProvider(), {"messages": []})
            item = request_snapshots.get(snapshot)
            assert item["kind"] == "title"
            assert item["session_id"] == "s1"
        inner = request_snapshots.capture(FakeProvider(), {"messages": []})
        assert request_snapshots.get(inner)["kind"] == "chat"


def test_session_cap_evicts_oldest(monkeypatch):
    _enable(monkeypatch)
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        ids = [
            request_snapshots.capture(FakeProvider(), {"messages": []})
            for _ in range(MAX_SNAPSHOTS_PER_SESSION + 2)
        ]
    listing = request_snapshots.list_for_session("s1", limit=MAX_SNAPSHOTS_PER_SESSION)
    assert len(listing["items"]) == MAX_SNAPSHOTS_PER_SESSION
    assert listing["dropped"] == 2
    assert request_snapshots.get(ids[0]) is None
    assert request_snapshots.get(ids[-1]) is not None


def test_session_byte_cap_evicts_oldest(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(request_snapshot, "MAX_BYTES_PER_SESSION", 2000)
    raw = {"messages": [{"role": "user", "content": "x" * 500}]}
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        ids = [request_snapshots.capture(FakeProvider(), raw) for _ in range(10)]
    listing = request_snapshots.list_for_session("s1")
    assert 0 < len(listing["items"]) < 10
    assert listing["dropped"] > 0
    assert sum(item["bytes"] for item in listing["items"]) <= 2000
    assert request_snapshots.get(ids[0]) is None
    assert request_snapshots.get(ids[-1]) is not None


def test_total_byte_cap_evicts_across_sessions(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(request_snapshot, "MAX_TOTAL_BYTES", 1200)
    raw = {"messages": [{"role": "user", "content": "x" * 500}]}
    for index in range(5):
        session_id = f"s{index % 2}"
        with request_snapshot_scope(
            session_id=session_id,
            umo=f"webchat:FriendMessage:{session_id}",
        ):
            request_snapshots.capture(FakeProvider(), raw)
    kept = [
        item
        for session_id in ("s0", "s1")
        for item in request_snapshots.list_for_session(session_id)["items"]
    ]
    assert len(kept) < 5
    assert sum(item["bytes"] for item in kept) <= 1200


def test_ttl_expires_entries(monkeypatch):
    _enable(monkeypatch)
    now = [1000.0]
    rec = RequestSnapshotRecorder(clock=lambda: now[0])
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        snapshot_id = rec.capture(FakeProvider(), {"messages": []})
    assert rec.get(snapshot_id) is not None
    now[0] += TTL_SECONDS + 1
    assert rec.list_for_session("s1")["items"] == []
    assert rec.get(snapshot_id) is None


def test_empty_session_is_dropped_from_bookkeeping(monkeypatch):
    _enable(monkeypatch)
    now = [1000.0]
    rec = RequestSnapshotRecorder(clock=lambda: now[0])
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        rec.capture(FakeProvider(), {"messages": []})
    assert "s1" in rec._sessions
    now[0] += TTL_SECONDS + 1
    rec.list_for_session("s1")
    assert "s1" not in rec._sessions
    assert "s1" not in rec._session_bytes


def test_oversized_payload_is_truncated_and_redacted(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,PRIVATE-IMAGE-BYTES"
                        },
                    },
                    {"type": "text", "text": "x" * 700000},
                ],
            }
        ],
    }
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        snapshot_id = request_snapshots.capture(FakeProvider(), raw)
    item = request_snapshots.get(snapshot_id)
    assert item["truncated"] is True
    assert item["redactions"] >= 1
    assert "PRIVATE-IMAGE-BYTES" not in str(item)
    assert item["bytes"] > 0


def test_provider_metadata_never_stores_the_key(monkeypatch):
    _enable(monkeypatch)
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        snapshot_id = request_snapshots.capture(FakeProvider(), {"messages": []})
    item = request_snapshots.get(snapshot_id)
    rendered = str(item)
    assert "sk-secret-1234" not in rendered
    provider = item["provider"]
    assert provider["id"] == "p1"
    assert provider["type"] == "openai_chat"
    assert provider["class"] == "FakeProvider"
    assert provider["model"] == "m1"
    assert provider["host"] == "https://api.example.com/v1"
    assert provider["proxy"] is False
    assert provider["key_fp"].endswith("1234")
    assert provider["key_index"] == 0
    assert provider["key_count"] == 1


def test_list_is_lightweight_and_get_is_full(monkeypatch):
    _enable(monkeypatch)
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        snapshot_id = request_snapshots.capture(FakeProvider(), {"messages": []})
    listing = request_snapshots.list_for_session("s1")
    assert listing["enabled"] is True
    assert listing["expired"] is False
    assert "raw" not in listing["items"][0]
    assert "injections" not in listing["items"][0]
    assert "provenance" not in listing["items"][0]
    full = request_snapshots.get(snapshot_id)
    assert "raw" in full
    assert full["injections"] == []
    assert full["provenance"] == []
    assert full["counts"]["messages"] == 0


def test_capture_never_raises(monkeypatch):
    _enable(monkeypatch)

    class ExplodingProvider(FakeProvider):
        def meta(self):
            raise RuntimeError("boom")

        def get_model(self):
            raise RuntimeError("boom")

    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        assert (
            request_snapshots.capture(ExplodingProvider(), {"messages": []}) is not None
        )


def test_message_binding_resolves_turn_and_reports_expiry(monkeypatch):
    _enable(monkeypatch)
    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        turn_id="t1",
    ):
        request_snapshots.capture(FakeProvider(), {"messages": []})
    request_snapshots.bind_message(7, "t1", "s1")
    bound = request_snapshots.list_for_session("s1", message_id=7)
    assert bound["expired"] is False
    assert [item["turn_id"] for item in bound["items"]] == ["t1"]
    missing = request_snapshots.list_for_session("s1", message_id=999)
    assert missing["expired"] is True
    assert missing["items"] == []
    assert request_snapshots.list_for_session("s1")["expired"] is False


def test_message_binding_expires_with_ttl(monkeypatch):
    _enable(monkeypatch)
    now = [1000.0]
    rec = RequestSnapshotRecorder(clock=lambda: now[0])
    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        turn_id="t1",
    ):
        rec.capture(FakeProvider(), {"messages": []})
    rec.bind_message(7, "t1", "s1")
    assert rec.list_for_session("s1", message_id=7)["items"]
    now[0] += TTL_SECONDS + 1
    assert rec.list_for_session("s1", message_id=7)["expired"] is True


def test_message_binding_cap_evicts_oldest(monkeypatch):
    _enable(monkeypatch)
    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        turn_id="t1",
    ):
        request_snapshots.capture(FakeProvider(), {"messages": []})
    for message_id in range(MAX_MESSAGE_BINDINGS + 2):
        request_snapshots.bind_message(message_id, "t1", "s1")
    assert request_snapshots.list_for_session("s1", message_id=0)["expired"] is True
    latest = request_snapshots.list_for_session(
        "s1", message_id=MAX_MESSAGE_BINDINGS + 1
    )
    assert latest["expired"] is False
    assert [item["turn_id"] for item in latest["items"]] == ["t1"]


def test_turn_filter_and_limit_clamp(monkeypatch):
    _enable(monkeypatch)
    for turn_id in ("t1", "t2"):
        with request_snapshot_scope(
            session_id="s1",
            umo="webchat:FriendMessage:s1",
            turn_id=turn_id,
        ):
            request_snapshots.capture(FakeProvider(), {"messages": []})
    assert [
        item["turn_id"]
        for item in request_snapshots.list_for_session("s1", turn_id="t1")["items"]
    ] == ["t1"]
    assert len(request_snapshots.list_for_session("s1", limit=0)["items"]) == 1
    assert len(request_snapshots.list_for_session("s1", limit=999)["items"]) == 2


def test_limits_expose_retention_constants():
    limits = request_snapshots.limits()
    assert limits["max_bytes_per_snapshot"] == 524288
    assert limits["max_snapshots_per_session"] == 128
    assert limits["max_bytes_per_session"] == 16777216
    assert limits["max_total_bytes"] == 67108864
    assert limits["max_message_bindings"] == 1024
    assert limits["ttl_seconds"] == 7200


def test_bounded_context_value_supports_limits_and_redaction_counts():
    small = bounded_context_value({"text": "x" * 100}, limit=10)
    assert small["limit"] == 10
    assert small["truncated"] is True
    assert small["redactions"] == 0
    redacted = bounded_context_value({"api_key": "PRIVATE", "ok": "value"})
    assert "PRIVATE" not in str(redacted)
    assert redacted["redactions"] >= 1
    assert redacted["limit"] == 8192


def _image_part(url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": url}}


def test_bounded_context_value_does_not_count_allowed_media_reference():
    sanitized = bounded_context_value(_image_part("https://example.com/a.png"))
    assert sanitized["redactions"] == 0
    assert sanitized["value"]["url"] == "https://example.com/a.png"


def test_bounded_context_value_counts_credential_url_once():
    sanitized = bounded_context_value(
        _image_part("https://user:pass@example.com/a.png?token=secret")
    )
    assert sanitized["redactions"] == 1
    assert sanitized["value"]["reference_redacted"] is True
    assert "pass" not in sanitized["value"]["url"]
    assert "token" not in sanitized["value"]["url"]


def test_bounded_context_value_counts_data_uri_once():
    sanitized = bounded_context_value(
        _image_part("data:image/png;base64,PRIVATE-IMAGE-BYTES")
    )
    assert sanitized["redactions"] == 1
    assert "PRIVATE-IMAGE-BYTES" not in str(sanitized)


def test_capture_keeps_sampling_fields_when_first_message_is_huge(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "temperature": 0.3,
        "top_p": 0.9,
        "max_tokens": 512,
        # Anthropic-native shape: the raw body carries a top-level ``system``,
        # which selects that adapter, and its tool list is flat.
        "tools": [{"name": "t1", "input_schema": {"type": "object"}}],
        "system": "s" * 128,
        "messages": [
            {
                "role": "user",
                "content": "x" * (request_snapshot.MAX_BYTES_PER_SNAPSHOT + 1),
            },
            {"role": "assistant", "content": "done"},
        ],
    }
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        snapshot_id = request_snapshots.capture(FakeProvider(), raw)
    item = request_snapshots.get(snapshot_id)
    assert item["truncated"] is True
    assert item["request"]["temperature"] == 0.3
    assert item["request"]["top_p"] == 0.9
    assert item["request"]["max_tokens"] == 512
    assert item["counts"]["messages"] == 2
    assert item["counts"]["tools"] == 1
    assert item["counts"]["system_chars"] == 128


def test_capture_does_not_mutate_caller_body(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "temperature": 0.1,
        "messages": [
            {"role": "user", "content": "hello"},
            _image_part("data:image/png;base64,PRIVATE-IMAGE-BYTES"),
        ],
        "api_key": "PRIVATE-KEY",
    }
    before = copy.deepcopy(raw)
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        request_snapshots.capture(FakeProvider(), raw)
    assert raw == before


def test_listing_and_get_return_detached_copies(monkeypatch):
    _enable(monkeypatch)
    raw = {"messages": [{"role": "user", "content": "hello"}]}
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        snapshot_id = request_snapshots.capture(FakeProvider(), raw)

    listing = request_snapshots.list_for_session("s1")
    listing["items"][0]["request"]["temperature"] = 999
    listing["items"][0]["counts"]["messages"] = 42
    stored_list = request_snapshots.list_for_session("s1")["items"][0]
    assert stored_list["request"]["temperature"] is None
    assert stored_list["counts"]["messages"] == 1

    full = request_snapshots.get(snapshot_id)
    full["request"]["temperature"] = 999
    full["raw"]["messages"][0]["content"] = "mutated"
    again = request_snapshots.get(snapshot_id)
    assert again["request"]["temperature"] is None
    assert again["raw"]["messages"][0]["content"] == "hello"


def test_short_api_key_uses_hash_only_fingerprint(monkeypatch):
    _enable(monkeypatch)

    class ShortKeyProvider(FakeProvider):
        def get_current_key(self) -> str:
            return "sk-1"

        def get_keys(self) -> list[str]:
            return ["sk-1"]

    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        snapshot_id = request_snapshots.capture(ShortKeyProvider(), {"messages": []})
    fingerprint = request_snapshots.get(snapshot_id)["provider"]["key_fp"]
    assert fingerprint is not None
    assert "sk-1" not in fingerprint
    assert "…" not in fingerprint
