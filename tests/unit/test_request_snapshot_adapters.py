"""Unit tests for snapshot provider adapters, normalized views and truncation.

The view is derived from the stored sanitized raw body; structural truncation
enforces the serialized UTF-8 byte cap of one snapshot.

Author: elecvoid243
Date: 2026-11-15
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from astrbot.core.provider import request_snapshot
from astrbot.core.provider.request_snapshot import (
    ADAPTERS,
    MAX_BYTES_PER_SNAPSHOT,
    build_view,
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


def _snapshot_with(
    raw: object,
    adapter: str | None,
    provider: object | None = None,
) -> dict:
    """Capture ``raw`` and return the full snapshot as the API would see it."""

    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        turn_id="t1",
    ):
        snapshot_id = request_snapshots.capture(
            provider or FakeProvider(),
            raw,
            adapter=adapter,
        )
    snapshot = request_snapshots.get(snapshot_id)
    assert snapshot is not None
    return snapshot


def _serialized_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def test_adapter_registry_exposes_all_provider_families():
    assert set(ADAPTERS) == {
        "openai_chat",
        "openai_responses",
        "anthropic",
        "gemini",
    }


def test_openai_chat_view_extracts_system_messages_tools_and_params(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "model": "m1",
        "messages": [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "hi"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {"type": "object"},
                },
            }
        ],
        "temperature": 0.7,
        "extra_body": {"chat_template_kwargs": {"reasoning_effort": 100}},
    }
    view = build_view(_snapshot_with(raw, "openai_chat"))
    assert view["system"]["text"] == "SYS"
    assert view["system"]["source_field"] == "messages[0]"
    assert view["messages"][1]["role"] == "user"
    assert [row["role"] for row in view["messages"]] == ["system", "user"]
    assert view["tools"][0]["name"] == "read_file"
    assert view["tools"][0]["parameters"] == {"type": "object"}
    assert view["params"]["temperature"] == 0.7
    assert view["extra"]["chat_template_kwargs"]["reasoning_effort"] == 100
    assert view["extra"]["extra_body_keys"] == ["chat_template_kwargs"]


def test_openai_chat_tool_rows_carry_schema_metadata(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    }
    snapshot = _snapshot_with(raw, "openai_chat")
    tool = build_view(snapshot)["tools"][0]
    # Only a schema dict was available at capture time.
    assert tool["origin"] == "unknown"
    assert tool["active"] is True
    # No denied set was supplied, so denial is unknown rather than fake False.
    assert tool["denied"] is None
    assert snapshot["tool_meta"]["read_file"]["denied"] is None
    assert tool["background"] is False
    assert len(tool["schema_hash"]) == 40
    assert tool["schema_bytes"] > 0
    assert snapshot["tool_meta"]["read_file"]["schema_hash"] == tool["schema_hash"]
    assert tool["display_schema_hash"] == tool["schema_hash"]
    assert tool["schema_truncated"] is False


def test_truncated_tool_schema_keeps_capture_hash_and_flags_display(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "d" * 50000,
                            }
                        },
                    },
                },
            }
        ],
    }
    snapshot = _snapshot_with(raw, "openai_chat")
    tool = build_view(snapshot)["tools"][0]
    meta = snapshot["tool_meta"]["read_file"]
    # The view hash is the capture-time identity; the displayed schema differs
    # because its long description was trimmed to fit the tool budget.
    assert tool["schema_hash"] == meta["schema_hash"]
    assert tool["schema_bytes"] == meta["schema_bytes"]
    assert tool["display_schema_hash"] != tool["schema_hash"]
    assert tool["schema_truncated"] is True
    assert "truncated" in json.dumps(tool["parameters"])


def test_openai_responses_view_uses_instructions_and_input(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "model": "m1",
        "instructions": "SYS",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hi"}],
            },
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "read_file",
                "arguments": '{"path": "a"}',
            },
            {"type": "function_call_output", "call_id": "c1", "output": "ok"},
        ],
        "tools": [
            {
                "type": "function",
                "name": "read_file",
                "description": "read",
                "parameters": {"type": "object"},
            }
        ],
        "temperature": 0.2,
        "max_output_tokens": 128,
        "store": False,
    }
    view = build_view(_snapshot_with(raw, "openai_responses"))
    assert view["system"]["text"] == "SYS"
    assert view["system"]["source_field"] == "instructions"
    assert [row["role"] for row in view["messages"]] == [
        "user",
        "assistant",
        "tool",
    ]
    assert view["messages"][1]["tool_calls"][0]["name"] == "read_file"
    assert view["messages"][2]["tool_call_id"] == "c1"
    assert view["messages"][2]["content"] == "ok"
    assert view["tools"][0]["name"] == "read_file"
    assert view["params"]["max_output_tokens"] == 128
    assert view["extra"]["store"] is False


def test_openai_responses_view_reads_system_input_messages(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "model": "m1",
        "input": [
            {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": "SYS IN INPUT"}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hi"}],
            },
        ],
    }
    view = build_view(_snapshot_with(raw, "openai_responses"))
    assert view["system"]["text"] == "SYS IN INPUT"
    assert view["system"]["source_field"] == "input[role=system|developer]"


def test_openai_responses_joins_instructions_and_system_input(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "model": "m1",
        "instructions": "INSTRUCTIONS",
        "input": [
            {
                "type": "message",
                "role": "developer",
                "content": "DEVELOPER",
            },
            {
                "type": "message",
                "role": "user",
                "content": "hi",
            },
        ],
    }
    view = build_view(_snapshot_with(raw, "openai_responses"))
    assert view["system"]["text"] == "INSTRUCTIONS\n\nDEVELOPER"
    assert (
        view["system"]["source_field"] == "instructions + input[role=system|developer]"
    )


def test_anthropic_view_uses_system_and_input_schema(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "model": "m1",
        "system": [{"type": "text", "text": "SYS"}],
        "messages": [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"path": "a"},
                    }
                ],
            },
        ],
        "tools": [
            {
                "name": "read_file",
                "description": "read",
                "input_schema": {"type": "object"},
            }
        ],
        "temperature": 0.5,
        "max_tokens": 256,
        "thinking": {"type": "enabled", "budget_tokens": 1024},
    }
    view = build_view(_snapshot_with(raw, "anthropic"))
    assert view["system"]["text"] == "SYS"
    assert view["system"]["source_field"] == "system"
    assert view["messages"][1]["role"] == "assistant"
    assert view["messages"][1]["tool_calls"][0]["id"] == "toolu_1"
    assert view["messages"][1]["tool_calls"][0]["name"] == "read_file"
    assert view["tools"][0]["name"] == "read_file"
    assert view["tools"][0]["parameters"] == {"type": "object"}
    assert view["params"]["max_tokens"] == 256
    assert view["extra"]["thinking"]["budget_tokens"] == 1024


def test_gemini_view_uses_config_system_instruction_and_contents(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "contents": [
            {"role": "user", "parts": [{"text": "hi"}]},
            {
                "role": "model",
                "parts": [{"functionCall": {"name": "read_file", "args": {}}}],
            },
        ],
        "config": {
            "system_instruction": {"parts": [{"text": "SYS"}]},
            "tools": [
                {
                    "function_declarations": [
                        {
                            "name": "read_file",
                            "description": "read",
                            "parameters": {"type": "object"},
                        }
                    ]
                }
            ],
            "generation_config": {"temperature": 0.4, "max_output_tokens": 64},
        },
    }
    view = build_view(_snapshot_with(raw, "gemini"))
    assert view["system"]["text"] == "SYS"
    assert view["system"]["source_field"] == "config.system_instruction"
    assert view["messages"][1]["role"] == "assistant"
    assert view["messages"][1]["tool_calls"][0]["name"] == "read_file"
    assert view["tools"][0]["name"] == "read_file"
    assert view["params"]["temperature"] == 0.4
    assert view["params"]["max_output_tokens"] == 64


def test_capture_falls_back_to_provider_snapshot_adapter(monkeypatch):
    _enable(monkeypatch)

    class AnthropicProvider(FakeProvider):
        SNAPSHOT_ADAPTER = "anthropic"

    raw = {
        "system": "SYS",
        "messages": [{"role": "user", "content": "hi"}],
    }
    snapshot = _snapshot_with(raw, None, provider=AnthropicProvider())
    assert snapshot["adapter"] == "anthropic"
    assert build_view(snapshot)["system"]["text"] == "SYS"


def test_unknown_adapter_falls_back_to_raw_shape(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "config": {"system_instruction": "SYS"},
    }
    view = build_view(_snapshot_with(raw, None))
    assert view["system"]["text"] == "SYS"
    assert view["messages"][0]["role"] == "user"


def test_capture_uses_explicit_tool_set_and_denied_tools(monkeypatch):
    _enable(monkeypatch)
    from astrbot.core.agent.tool import FunctionTool, ToolSet

    plugin_tool = FunctionTool(
        name="read_file",
        description="read",
        parameters={"type": "object", "properties": {}},
        handler_module_path="data.plugins.demo.main",
    )
    denied_tool = FunctionTool(
        name="write_file",
        description="write",
        parameters={"type": "object", "properties": {}},
    )
    background_tool = FunctionTool(
        name="long_task",
        description="bg",
        parameters={"type": "object", "properties": {}},
        is_background_task=True,
    )
    tool_set = ToolSet([plugin_tool, denied_tool, background_tool])

    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
    ):
        snapshot_id = request_snapshots.capture(
            FakeProvider(),
            {"messages": []},
            adapter="openai_chat",
            tool_set=tool_set,
            denied_tools={"write_file"},
        )
    meta = request_snapshots.get(snapshot_id)["tool_meta"]
    assert meta["read_file"]["origin"] == "plugin"
    assert meta["read_file"]["active"] is True
    assert meta["read_file"]["denied"] is False
    assert meta["write_file"]["denied"] is True
    assert meta["long_task"]["background"] is True
    assert len(meta["read_file"]["schema_hash"]) == 40
    assert meta["read_file"]["schema_bytes"] > 0


def test_attach_tool_meta_fills_every_attempt_after_capture(monkeypatch):
    _enable(monkeypatch)
    from astrbot.core.agent.tool import FunctionTool, ToolSet

    read_tool = FunctionTool(
        name="read_file",
        description="read",
        parameters={"type": "object", "properties": {}},
        handler_module_path="data.plugins.demo.main",
    )
    write_tool = FunctionTool(
        name="write_file",
        description="write",
        parameters={"type": "object", "properties": {}},
    )
    raw = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    }
    with request_snapshot_scope(
        session_id="s1",
        umo="webchat:FriendMessage:s1",
        turn_id="t1",
        invocation_id="i1",
    ):
        request_snapshots.capture(FakeProvider(), raw, adapter="openai_chat")
        request_snapshots.capture(FakeProvider(), raw, adapter="openai_chat")

    before = request_snapshots.metadata_for_invocation("i1")
    assert len(before) == 2
    assert [item["tool_meta"]["read_file"]["origin"] for item in before] == [
        "unknown",
        "unknown",
    ]
    assert [item["tool_meta"]["read_file"]["denied"] for item in before] == [
        None,
        None,
    ]
    capture_hash = before[0]["tool_meta"]["read_file"]["schema_hash"]

    request_snapshots.attach_tool_meta(
        "i1",
        tool_set=ToolSet([read_tool, write_tool]),
        denied_tools={"write_file"},
    )

    after = request_snapshots.metadata_for_invocation("i1")
    assert len(after) == 2
    for item in after:
        assert item["tool_meta"]["read_file"]["origin"] == "plugin"
        assert item["tool_meta"]["read_file"]["denied"] is False
        assert item["tool_meta"]["read_file"]["schema_hash"] == capture_hash
        assert item["tool_meta"]["write_file"]["origin"] == "unknown"
        assert item["tool_meta"]["write_file"]["denied"] is True


def test_message_flags_reflect_markers_in_the_payload(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "messages": [
            {"role": "user", "content": "hi", "_internal": True},
            {"role": "user", "content": "plain"},
        ]
    }
    view = build_view(_snapshot_with(raw, "openai_chat"))
    assert view["messages"][0]["flags"] == ["internal"]
    assert view["messages"][1]["flags"] == []


def test_estimated_tokens_uses_the_token_counter_when_valid(monkeypatch):
    _enable(monkeypatch)
    raw = {"messages": [{"role": "user", "content": "你" * 100}]}
    view = build_view(_snapshot_with(raw, "openai_chat"))
    row = view["messages"][0]
    assert row["chars"] == 100
    # EstimateTokenCounter: 100 CJK characters x 0.6 = 60 tokens.
    assert row["estimated_tokens"] == 60


def test_estimated_tokens_falls_back_for_native_keyword_shapes(monkeypatch):
    _enable(monkeypatch)
    text = "x" * 300
    raw = {"contents": [{"role": "user", "parts": [{"text": text}]}]}
    view = build_view(_snapshot_with(raw, "gemini"))
    row = view["messages"][0]
    assert row["chars"] == 300
    assert row["estimated_tokens"] == max(1, 300 // 3)


def test_build_view_does_not_mutate_or_store_the_snapshot(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "system": "SYS",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "read_file", "parameters": {"type": "object"}},
            }
        ],
    }
    snapshot = _snapshot_with(raw, "openai_chat")
    before = copy.deepcopy(snapshot)
    build_view(snapshot)
    assert snapshot == before
    assert "view" not in snapshot


def test_truncation_drops_oldest_messages_first(monkeypatch):
    _enable(monkeypatch)
    messages = [{"role": "system", "content": "SYS"}]
    messages.extend(
        {"role": "user", "content": f"m{index}:" + "x" * 20000} for index in range(100)
    )
    messages[-1]["content"] = "newest"
    raw = {
        "model": "m1",
        "messages": messages,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {"type": "object"},
                },
            }
        ],
        "temperature": 0.7,
    }
    snapshot = _snapshot_with(raw, "openai_chat")
    view = build_view(snapshot)
    assert view["messages"][-1]["content"] == "newest"
    assert view["messages"][0]["role"] == "system"
    assert view["messages"][0]["content"] == "SYS"
    # The protected system head keeps index 0; every later row shifts by the
    # number of omitted messages, so the newest row keeps its original index.
    assert view["messages"][0]["index"] == 0
    assert view["messages"][1]["index"] == (snapshot["counts"]["omitted_messages"] + 1)
    assert view["messages"][-1]["index"] == len(messages) - 1
    assert snapshot["counts"]["omitted_messages"] > 0
    assert (
        snapshot["diagnostics"]["omitted_messages"]
        == (snapshot["counts"]["omitted_messages"])
    )
    assert snapshot["truncated"] is True
    assert snapshot["original_bytes"] > snapshot["bytes"]
    assert snapshot["bytes"] <= MAX_BYTES_PER_SNAPSHOT
    assert _serialized_bytes(snapshot["raw"]) <= MAX_BYTES_PER_SNAPSHOT
    assert view["system"]["text"] == "SYS"
    assert [tool["name"] for tool in view["tools"]] == ["read_file"]


def test_cjk_and_emoji_payload_stays_within_the_byte_cap(monkeypatch):
    _enable(monkeypatch)
    chunk = "测试内容🙂" * 2000
    messages = [{"role": "system", "content": "系统提示"}]
    messages.extend(
        {"role": "user", "content": f"{index}:" + chunk} for index in range(80)
    )
    raw = {"model": "m1", "messages": messages}
    snapshot = _snapshot_with(raw, "openai_chat")
    assert snapshot["truncated"] is True
    assert snapshot["bytes"] <= MAX_BYTES_PER_SNAPSHOT
    assert _serialized_bytes(snapshot["raw"]) <= MAX_BYTES_PER_SNAPSHOT
    view = build_view(snapshot)
    newest = view["messages"][-1]
    assert "测试内容🙂" in json.dumps(newest["content"], ensure_ascii=False)
    assert view["system"]["text"] == "系统提示"


def test_oversized_message_keeps_head_and_tail_with_marker(monkeypatch):
    _enable(monkeypatch)
    body = "H" * 100000 + "TAIL"
    raw = {
        "messages": [
            {"role": "user", "content": body},
            {"role": "assistant", "content": "done"},
        ]
    }
    view = build_view(_snapshot_with(raw, "openai_chat"))
    content = view["messages"][0]["content"]
    assert isinstance(content, str)
    assert "…[truncated " in content
    assert content.startswith("H" * 100)
    assert content.endswith("TAIL")
    assert len(content.encode("utf-8")) < 32 * 1024 + 128
    assert view["messages"][-1]["content"] == "done"


def test_system_alone_over_budget_is_marked_not_dropped(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "messages": [
            {"role": "system", "content": "S" * 700000},
            {"role": "user", "content": "hi"},
        ],
    }
    snapshot = _snapshot_with(raw, "openai_chat")
    view = build_view(snapshot)
    assert snapshot["bytes"] <= MAX_BYTES_PER_SNAPSHOT
    assert snapshot["truncated"] is True
    assert view["system"]["text"].startswith("S")
    assert "truncated" in view["system"]["text"]
    assert view["messages"][-1]["content"] == "hi"


def test_system_prompt_is_capped_even_when_total_is_under_budget(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "messages": [
            {"role": "system", "content": "S" * 200000},
            {"role": "user", "content": "hi"},
        ]
    }
    snapshot = _snapshot_with(raw, "openai_chat")
    view = build_view(snapshot)
    assert snapshot["bytes"] < MAX_BYTES_PER_SNAPSHOT
    assert snapshot["truncated"] is True
    assert len(view["system"]["text"].encode("utf-8")) <= 64 * 1024 + 64
    assert "truncated" in view["system"]["text"]
    assert view["messages"][-1]["content"] == "hi"


def test_tools_that_exceed_budget_are_truncated_not_dropped(monkeypatch):
    _enable(monkeypatch)
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{index}",
                "description": "demo",
                "parameters": {
                    "type": "object",
                    "properties": {"x": {"type": "string", "description": "z" * 60000}},
                },
            },
        }
        for index in range(40)
    ]
    raw = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": tools,
    }
    snapshot = _snapshot_with(raw, "openai_chat")
    view = build_view(snapshot)
    assert snapshot["bytes"] <= MAX_BYTES_PER_SNAPSHOT
    assert snapshot["truncated"] is True
    assert len(view["tools"]) == 40
    assert any("truncated" in json.dumps(tool["parameters"]) for tool in view["tools"])
    assert view["messages"][-1]["content"] == "hi"


def test_newest_message_is_never_silently_dropped(monkeypatch):
    _enable(monkeypatch)
    huge = "Z" * (MAX_BYTES_PER_SNAPSHOT * 2)
    raw = {
        "messages": [
            {"role": "user", "content": "old"},
            {"role": "user", "content": huge},
        ]
    }
    snapshot = _snapshot_with(raw, "openai_chat")
    view = build_view(snapshot)
    assert snapshot["bytes"] <= MAX_BYTES_PER_SNAPSHOT
    assert len(view["messages"]) == 1
    assert "truncated" in json.dumps(view["messages"][-1]["content"])
    assert view["messages"][-1]["index"] == 1


def test_unknown_huge_provider_field_does_not_drop_messages(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "messages": [{"role": "user", "content": "hi"}],
        "mystery_provider_field": "x" * (MAX_BYTES_PER_SNAPSHOT * 2),
    }
    snapshot = _snapshot_with(raw, "openai_chat")
    view = build_view(snapshot)
    assert snapshot["bytes"] <= MAX_BYTES_PER_SNAPSHOT
    assert snapshot["truncated"] is True
    assert view["messages"][-1]["content"] == "hi"


def test_structure_without_trimable_strings_becomes_a_flagged_object(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "messages": [
            {"role": "user", "content": list(range(40000))},
        ]
    }
    snapshot = _snapshot_with(raw, "openai_chat")
    stored = snapshot["raw"]["messages"][0]
    # The structure has no long string left to trim, so it is replaced by an
    # object marker instead of a bare JSON string. The adapter still keeps the
    # row visible.
    assert isinstance(stored, dict)
    assert isinstance(stored.get("truncated"), str)
    view = build_view(snapshot)
    assert len(view["messages"]) == 1
    assert view["messages"][0]["role"] == "user"


def test_flat_string_input_is_trimmed_to_the_cap(monkeypatch):
    _enable(monkeypatch)
    raw = {
        "input": "H" * 100000 + "TAIL",
        "instructions": "SYS",
    }
    snapshot = _snapshot_with(raw, "openai_responses")
    view = build_view(snapshot)
    assert snapshot["bytes"] <= MAX_BYTES_PER_SNAPSHOT
    assert snapshot["truncated"] is True
    content = view["messages"][0]["content"]
    assert "…[truncated " in content
    assert content.endswith("TAIL")
    assert view["system"]["text"] == "SYS"
