"""Unit tests for snapshot provenance, cache diagnostics and attachments.

These tests cover the ChatUI developer-mode features added on top of the
request recorder: per-session provenance attribution, per-session cache
fingerprints, response/diagnostics/injection attachment and the error path.

Author: elecvoid243
Date: 2026-11-15
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrbot.core.provider import request_snapshot
from astrbot.core.provider.entities import LLMResponse, TokenUsage
from astrbot.core.provider.request_snapshot import (
    MAX_PROVENANCE_ENTRIES,
    PROVENANCE_MAX_TEXT_BYTES,
    PROVENANCE_TTL_SECONDS,
    RequestSnapshotRecorder,
    record_provenance,
    request_snapshot_scope,
    request_snapshots,
)


class FakeProvider:
    """Minimal provider surface consumed by ``capture``."""

    provider_config = {"api_base": "https://api.example.com/v1"}

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


def _enable(monkeypatch) -> None:
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: True)


def _openai_system(system: str) -> dict:
    return {
        "model": "m1",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "hi"},
        ],
    }


def _anthropic(messages: list[dict], system: str = "SYS", tools: list | None = None):
    raw = {"system": system, "messages": messages}
    if tools is not None:
        raw["tools"] = tools
    return raw


def _capture(
    raw: object,
    *,
    session_id: str = "s1",
    invocation_id: str = "i1",
    adapter: str | None = None,
    kind: str = "chat",
) -> dict:
    """Capture ``raw`` and return the stored snapshot as the API would see it."""

    with request_snapshot_scope(
        session_id=session_id,
        umo=f"webchat:FriendMessage:{session_id}",
        turn_id="t1",
        invocation_id=invocation_id,
        kind=kind,
    ):
        snapshot_id = request_snapshots.capture(
            FakeProvider(),
            raw,
            adapter=adapter,
        )
    assert snapshot_id is not None
    snapshot = request_snapshots.get(snapshot_id)
    assert snapshot is not None
    return snapshot


def test_unique_provenance_gets_exact_span(monkeypatch):
    _enable(monkeypatch)
    record_provenance("s1", "persona", "PERSONA")
    snapshot = _capture(_openai_system("PERSONA\nENV"))
    part = snapshot["provenance"][0]
    assert part["label"] == "persona"
    assert part["span"] == [0, 7]
    assert part["approx"] is False
    assert part["chars"] == 7
    assert part["hash"]


def test_duplicate_or_missing_provenance_is_approximate(monkeypatch):
    _enable(monkeypatch)
    record_provenance("s1", "environment", "ENV")
    record_provenance("s1", "missing", "GONE")
    record_provenance("s1", "empty", "")
    snapshot = _capture(_openai_system("ENV ENV"))
    parts = snapshot["provenance"]
    assert [part["label"] for part in parts] == ["environment", "missing"]
    for part in parts:
        assert part["span"] is None and part["approx"] is True
    assert parts[0]["chars"] == 3


def test_provenance_is_session_scoped(monkeypatch):
    _enable(monkeypatch)
    record_provenance("s1", "persona", "PERSONA")
    other = _capture(_openai_system("PERSONA\nENV"), session_id="s2")
    assert other["provenance"] == []
    mine = _capture(_openai_system("PERSONA\nENV"), session_id="s1")
    assert mine["provenance"][0]["span"] == [0, 7]


def test_provenance_buffer_is_bounded_and_expires(monkeypatch):
    _enable(monkeypatch)
    clock = [1000.0]
    recorder = RequestSnapshotRecorder(clock=lambda: clock[0])
    labels = [f"L{index}" for index in range(MAX_PROVENANCE_ENTRIES + 8)]
    for index, label in enumerate(labels):
        recorder.record_provenance("s1", label, f"T{index} ")

    system = " ".join(f"T{index}" for index in range(MAX_PROVENANCE_ENTRIES + 8))
    raw = {"messages": [{"role": "system", "content": system}]}
    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        invocation_id="i1",
    ):
        snapshot_id = recorder.capture(FakeProvider(), raw)
    snapshot = recorder.get(snapshot_id)
    parts = snapshot["provenance"]
    assert len(parts) == MAX_PROVENANCE_ENTRIES
    assert parts[0]["label"] == labels[-MAX_PROVENANCE_ENTRIES]

    clock[0] += PROVENANCE_TTL_SECONDS + 1
    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        invocation_id="i2",
    ):
        expired_id = recorder.capture(FakeProvider(), raw)
    assert recorder.get(expired_id)["provenance"] == []


def test_cache_reports_first_message_divergence(monkeypatch):
    _enable(monkeypatch)
    previous = _anthropic(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "a"},
        ]
    )
    current = _anthropic(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "b"},
        ]
    )
    _capture(previous, invocation_id="i1")
    snapshot = _capture(current, invocation_id="i2")
    cache = snapshot["diagnostics"]["cache"]
    assert cache["divergence_index"] == 1
    assert cache["system_changed"] is False
    assert cache["tools_changed"] is False
    assert cache["hit_ratio"] is None
    assert cache["prefix_match_ratio"] == pytest.approx(0.5)
    assert cache["prefix_warn_ratio"] == pytest.approx(
        request_snapshot.CACHE_PREFIX_WARN_RATIO
    )
    assert cache["miss_warning"] is True


def test_cache_reports_appended_message_and_tool_change(monkeypatch):
    _enable(monkeypatch)
    tool_a = {"name": "read_file", "input_schema": {"type": "object"}}
    tool_b = {
        "name": "read_file",
        "input_schema": {"type": "object", "properties": {"path": {}}},
    }
    _capture(
        _anthropic([{"role": "user", "content": "hi"}], tools=[tool_a]),
        invocation_id="i1",
    )
    appended = _capture(
        _anthropic(
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "next"},
            ],
            tools=[tool_b],
        ),
        invocation_id="i2",
    )
    cache = appended["diagnostics"]["cache"]
    assert cache["divergence_index"] == 1
    assert cache["tools_changed"] is True
    assert cache["system_changed"] is False
    assert cache["prefix_match_ratio"] == pytest.approx(0.0)
    assert cache["miss_warning"] is True


def test_cache_is_null_without_a_previous_request(monkeypatch):
    _enable(monkeypatch)
    snapshot = _capture(_openai_system("SYS"))
    cache = snapshot["diagnostics"]["cache"]
    assert cache["system_changed"] is None
    assert cache["tools_changed"] is None
    assert cache["divergence_index"] is None
    assert cache["prefix_match_ratio"] is None
    assert cache["miss_warning"] is None


def test_cache_warning_defers_to_server_hit_ratio(monkeypatch):
    _enable(monkeypatch)
    previous = _anthropic(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "again"},
            {"role": "assistant", "content": "b"},
        ]
    )
    current = _anthropic(
        [
            {"role": "user", "content": "changed"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "again"},
            {"role": "assistant", "content": "b"},
        ]
    )
    _capture(previous, invocation_id="i1")
    snapshot = _capture(current, invocation_id="i2")
    cache = snapshot["diagnostics"]["cache"]
    assert cache["miss_warning"] is True

    # A server-reported high hit ratio disproves the local guess.
    request_snapshots.attach_response(
        "i2",
        LLMResponse(
            role="assistant",
            usage=TokenUsage(input_other=100, input_cached=900),
        ),
    )
    stored = request_snapshots.get(snapshot["snapshot_id"])
    assert stored["diagnostics"]["cache"]["hit_ratio"] == pytest.approx(0.9)
    assert stored["diagnostics"]["cache"]["miss_warning"] is False

    # A low server ratio keeps the warning; no response keeps the prefix guess.
    next_snapshot = _capture(
        _anthropic([{"role": "user", "content": "different"}]),
        invocation_id="i3",
    )
    assert next_snapshot["diagnostics"]["cache"]["miss_warning"] is True
    request_snapshots.attach_response(
        "i3",
        LLMResponse(
            role="assistant",
            usage=TokenUsage(input_other=900, input_cached=100),
        ),
    )
    stored = request_snapshots.get(next_snapshot["snapshot_id"])
    assert stored["diagnostics"]["cache"]["hit_ratio"] == pytest.approx(0.1)
    assert stored["diagnostics"]["cache"]["miss_warning"] is True


def test_attach_diagnostics_merges_compression_and_usage(monkeypatch):
    _enable(monkeypatch)
    snapshot = _capture(_openai_system("SYS"))
    response = LLMResponse(
        role="assistant",
        id="chatcmpl-9f2a",
        usage=TokenUsage(input_other=21000, input_cached=5300, output=642),
        raw_completion=SimpleNamespace(
            model="m-returned",
            choices=[SimpleNamespace(finish_reason="tool_calls")],
        ),
    )
    request_snapshots.attach_response("i1", response, chunks=214)
    request_snapshots.attach_diagnostics(
        "i1",
        {
            "compression": {"strategy": "llm_summary", "tokens_before": 61200},
            "budget": {"step_used": 2, "step_max": 24},
            "retries": {"count": 0, "fallback": False},
            "stop_reason": "completed",
        },
    )
    stored = request_snapshots.get(snapshot["snapshot_id"])
    assert stored["status"] == "ok"
    assert stored["diagnostics"]["compression"]["strategy"] == "llm_summary"
    assert stored["diagnostics"]["budget"]["step_max"] == 24
    assert stored["diagnostics"]["retries"]["count"] == 0
    assert stored["diagnostics"]["stop_reason"] == "completed"
    assert stored["response"]["response_id"] == "chatcmpl-9f2a"
    assert stored["response"]["finish_reason"] == "tool_calls"
    assert stored["response"]["returned_model"] == "m-returned"
    assert stored["response"]["usage"]["input_cached"] == 5300
    assert stored["response"]["usage"]["total"] == 26942
    assert stored["response"]["chunks"] == 214
    assert stored["response"]["usage_matches_request"] is True
    assert stored["provider"]["returned_model"] == "m-returned"
    assert stored["diagnostics"]["cache"]["hit_ratio"] == pytest.approx(5300 / 26300)


def test_attach_diagnostics_keeps_response_and_covers_every_attempt(monkeypatch):
    _enable(monkeypatch)
    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        invocation_id="i1",
    ):
        first = request_snapshots.capture(FakeProvider(), _openai_system("SYS"))
        second = request_snapshots.capture(FakeProvider(), _openai_system("SYS"))
    request_snapshots.attach_response("i1", LLMResponse(role="assistant"))
    request_snapshots.attach_diagnostics("i1", {"stop_reason": "completed"})
    for snapshot_id in (first, second):
        stored = request_snapshots.get(snapshot_id)
        assert stored["diagnostics"]["stop_reason"] == "completed"
        assert stored["response"] is not None
        assert stored["response"]["response_id"] is None


def test_attach_response_parses_anthropic_and_gemini(monkeypatch):
    _enable(monkeypatch)
    _capture(_openai_system("SYS"), invocation_id="anthropic")
    request_snapshots.attach_response(
        "anthropic",
        LLMResponse(
            role="assistant",
            raw_completion=SimpleNamespace(stop_reason="end_turn", model="claude"),
        ),
    )
    stored = request_snapshots.get(
        request_snapshots.metadata_for_invocation("anthropic")[0]["snapshot_id"]
    )
    assert stored["response"]["finish_reason"] == "end_turn"
    assert stored["response"]["returned_model"] == "claude"

    _capture(_openai_system("SYS"), invocation_id="gemini")
    enum_reason = SimpleNamespace(value="STOP")
    request_snapshots.attach_response(
        "gemini",
        LLMResponse(
            role="assistant",
            raw_completion=SimpleNamespace(
                candidates=[SimpleNamespace(finish_reason=enum_reason)],
            ),
        ),
    )
    stored = request_snapshots.get(
        request_snapshots.metadata_for_invocation("gemini")[0]["snapshot_id"]
    )
    assert stored["response"]["finish_reason"] == "STOP"
    assert stored["response"]["returned_model"] is None
    assert stored["response"]["usage"] is None
    assert stored["diagnostics"]["cache"]["hit_ratio"] is None


def test_attach_response_respects_usage_mismatch(monkeypatch):
    _enable(monkeypatch)
    snapshot = _capture(_openai_system("SYS"))
    response = LLMResponse(
        role="assistant",
        usage=TokenUsage(input_other=10, input_cached=0, output=5),
        usage_matches_request=False,
    )
    request_snapshots.attach_response("i1", response, chunks=-3)
    stored = request_snapshots.get(snapshot["snapshot_id"])
    assert stored["response"]["usage_matches_request"] is False
    assert stored["response"]["chunks"] == 0
    assert stored["response"]["finish_reason"] is None
    assert stored["response"]["returned_model"] is None
    assert stored["diagnostics"]["cache"]["hit_ratio"] == 0.0


def test_attach_injections_stores_capped_items_on_every_attempt(monkeypatch):
    _enable(monkeypatch)
    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        invocation_id="i1",
    ):
        first = request_snapshots.capture(FakeProvider(), _openai_system("SYS"))
        second = request_snapshots.capture(FakeProvider(), _openai_system("SYS"))
    items = [
        {
            "plugin": "tc_memory",
            "handler": "decorate_llm_req",
            "changes": [
                {
                    "field": "system_prompt",
                    "delta": 12,
                    "lossy": False,
                    "preview": "p" * 200,
                    "full": "f" * 9000,
                }
            ],
        },
        "not-a-dict",
    ]
    request_snapshots.attach_injections("i1", items)
    for snapshot_id in (first, second):
        stored = request_snapshots.get(snapshot_id)
        assert len(stored["injections"]) == 1
        item = stored["injections"][0]
        assert item["plugin"] == "tc_memory"
        change = item["changes"][0]
        assert change["field"] == "system_prompt"
        assert change["delta"] == 12
        assert len(change["preview"]) == 81
        assert change["preview"].endswith("…")
        assert len(change["full"]) == 8193
        assert change["full"].endswith("…")
    assert items[0]["changes"][0]["preview"] == "p" * 200


def test_list_is_lightweight_and_detail_is_full(monkeypatch):
    _enable(monkeypatch)
    snapshot = _capture(_openai_system("SYS"))
    items = [{"plugin": "p", "handler": "h", "changes": []}]
    request_snapshots.attach_injections("i1", items)
    listing = request_snapshots.list_for_session("s1")
    assert "injections" not in listing["items"][0]
    assert "provenance" not in listing["items"][0]
    detail = request_snapshots.get(snapshot["snapshot_id"])
    assert detail["injections"] == items


def test_mark_error_records_type_and_truncated_message(monkeypatch):
    _enable(monkeypatch)
    snapshot = _capture(_openai_system("SYS"))
    request_snapshots.attach_response(
        "i1",
        LLMResponse(role="assistant", id="chatcmpl-1"),
    )
    request_snapshots.mark_error("i1", ValueError("boom" * 600))
    stored = request_snapshots.get(snapshot["snapshot_id"])
    assert stored["status"] == "error"
    error = stored["response"]["error"]
    assert error["type"] == "ValueError"
    assert error["message"] == ("boom" * 600)[:1024]
    assert len(error["message"]) == 1024
    assert stored["response"]["response_id"] is None


def test_attachments_never_raise(monkeypatch):
    _enable(monkeypatch)

    class Exploding(Exception):
        def __str__(self) -> str:  # pragma: no cover - exercised on purpose
            raise RuntimeError("unprintable")

    request_snapshots.attach_diagnostics("unknown", {"compression": {}})
    request_snapshots.attach_injections("unknown", [{"plugin": "p"}])
    request_snapshots.attach_injections("unknown", "not-a-list")
    request_snapshots.attach_response("unknown", SimpleNamespace())
    request_snapshots.attach_response("unknown", None)
    request_snapshots.mark_error("unknown", Exploding())
    request_snapshots.mark_error(None, None)

    snapshot = _capture(_openai_system("SYS"))
    request_snapshots.attach_response("i1", SimpleNamespace(raw_completion="junk"))
    request_snapshots.mark_error("i1", Exploding())
    stored = request_snapshots.get(snapshot["snapshot_id"])
    assert stored["status"] == "error"
    assert stored["response"]["error"]["type"] == "Exploding"
    assert stored["response"]["error"]["message"] == ""


def test_record_provenance_is_noop_when_mode_is_off(monkeypatch):
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: False)
    recorder = RequestSnapshotRecorder()
    recorder.record_provenance("s1", "persona", "PERSONA")
    assert recorder._provenance == {}


def test_record_provenance_caps_text_at_utf8_budget(monkeypatch):
    _enable(monkeypatch)
    recorder = RequestSnapshotRecorder()
    recorder.record_provenance("s1", "persona", "汉" * 20000)
    stored = recorder._provenance["s1"][0][1]
    assert stored.endswith("…[truncated]…")
    assert len(stored.encode("utf-8")) <= PROVENANCE_MAX_TEXT_BYTES
    assert "汉" in stored


def test_auxiliary_capture_does_not_poison_cache_baseline(monkeypatch):
    _enable(monkeypatch)
    chat = _anthropic([{"role": "user", "content": "hi"}], system="SYS")
    title = _anthropic([{"role": "user", "content": "title me"}], system="TITLE")

    first = _capture(chat, invocation_id="i1")
    assert first["diagnostics"]["cache"]["system_changed"] is None

    auxiliary = _capture(title, invocation_id="i2", kind="title")
    assert auxiliary["diagnostics"]["cache"] is None
    request_snapshots.attach_response("i2", LLMResponse(role="assistant"))
    stored_auxiliary = request_snapshots.get(auxiliary["snapshot_id"])
    assert stored_auxiliary["diagnostics"]["cache"] is None

    third = _capture(chat, invocation_id="i3")
    cache = third["diagnostics"]["cache"]
    assert cache["system_changed"] is False
    assert cache["tools_changed"] is False
    assert cache["divergence_index"] is None


def test_attach_response_drops_unusable_usage(monkeypatch):
    _enable(monkeypatch)
    snapshot = _capture(_openai_system("SYS"))
    request_snapshots.attach_response(
        "i1",
        LLMResponse(
            role="assistant",
            usage=SimpleNamespace(input="not-a-counter"),
        ),
    )
    stored = request_snapshots.get(snapshot["snapshot_id"])
    assert stored["response"]["usage"] is None
    assert stored["diagnostics"]["cache"]["hit_ratio"] is None


def test_attach_response_prefers_raw_created_at(monkeypatch):
    _enable(monkeypatch)
    recorder = RequestSnapshotRecorder(clock=lambda: 1000.0)
    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        invocation_id="i1",
    ):
        snapshot_id = recorder.capture(FakeProvider(), _openai_system("SYS"))
    recorder.attach_response(
        "i1",
        LLMResponse(
            role="assistant",
            raw_completion=SimpleNamespace(created=1763200867),
        ),
    )
    assert recorder.get(snapshot_id)["response"]["created_at"] == 1763200867

    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        invocation_id="i2",
    ):
        fallback_id = recorder.capture(FakeProvider(), _openai_system("SYS"))
    recorder.attach_response("i2", LLMResponse(role="assistant"))
    assert recorder.get(fallback_id)["response"]["created_at"] == 1000.0
