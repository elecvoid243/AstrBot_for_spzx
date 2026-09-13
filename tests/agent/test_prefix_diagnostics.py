"""Tests for the per-session request fingerprint diagnostics."""

import pytest

from astrbot.core.agent.message import Message
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.utils import prefix_diagnostics as pd
from astrbot.core.utils.prefix_diagnostics import (
    fingerprint_messages,
    fingerprint_tools,
    get_session_request,
    record_session_request,
)


@pytest.fixture(autouse=True)
def clear_records():
    pd._RECORDS.clear()
    yield
    pd._RECORDS.clear()


def _toolset(name: str = "tool_a") -> ToolSet:
    ts = ToolSet()
    ts.add_tool(
        FunctionTool(
            name=name,
            description="d",
            parameters={"type": "object", "properties": {}},
        )
    )
    return ts


def test_fingerprint_messages_stable_and_sensitive():
    msgs = [Message(role="user", content="hello")]
    assert fingerprint_messages(msgs) == fingerprint_messages(
        [Message(role="user", content="hello")]
    )
    assert fingerprint_messages(msgs) != fingerprint_messages(
        [Message(role="user", content="hello!")]
    )


def test_fingerprint_messages_accepts_dicts():
    hashes = fingerprint_messages([{"role": "user", "content": "hi"}])
    assert len(hashes) == 1 and len(hashes[0]) == 16


def test_fingerprint_tools_overall_and_per_tool():
    fp = fingerprint_tools(_toolset())
    assert fp["hash"] and set(fp["tools"]) == {"tool_a"}
    other = fingerprint_tools(_toolset("tool_b"))
    assert other["hash"] != fp["hash"]
    assert fingerprint_tools(None) == {"hash": None, "tools": {}}


def test_record_and_get_round_trip():
    record_session_request(
        "umo-1",
        model="m1",
        func_tool=_toolset(),
        messages=[Message(role="user", content="hi")],
    )
    record = get_session_request("umo-1")
    assert record is not None
    assert record["model"] == "m1"
    assert record["tools"]["tools"] == {"tool_a": record["tools"]["tools"]["tool_a"]}
    assert len(record["msg_hashes"]) == 1


def test_record_skips_subagent_and_empty_session():
    record_session_request(
        "umo-1",
        model="m1",
        func_tool=None,
        messages=[],
        is_subagent=True,
    )
    record_session_request(
        "",
        model="m1",
        func_tool=None,
        messages=[],
    )
    assert get_session_request("umo-1") is None


def test_record_extracts_system_text():
    record_session_request(
        "umo-1",
        model="m",
        func_tool=None,
        messages=[
            Message(role="system", content="SYSTEM PROMPT TEXT"),
            Message(role="user", content="hi"),
        ],
    )
    assert get_session_request("umo-1")["system_text"] == "SYSTEM PROMPT TEXT"

    record_session_request(
        "umo-2",
        model="m",
        func_tool=None,
        messages=[Message(role="user", content="hi")],
    )
    assert get_session_request("umo-2")["system_text"] is None


def test_record_overwrites_and_bounds_sessions():
    for i in range(pd._MAX_SESSIONS + 4):
        record_session_request(
            f"umo-{i}",
            model="m",
            func_tool=None,
            messages=[Message(role="user", content=str(i))],
        )
    assert get_session_request("umo-0") is None
    assert get_session_request(f"umo-{pd._MAX_SESSIONS + 3}") is not None

    record_session_request(
        "umo-x",
        model="old",
        func_tool=None,
        messages=[Message(role="user", content="a")],
    )
    record_session_request(
        "umo-x",
        model="new",
        func_tool=None,
        messages=[Message(role="user", content="b")],
    )
    assert get_session_request("umo-x")["model"] == "new"
