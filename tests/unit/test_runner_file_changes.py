from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner


def _runner_with_entries(entries: list[dict]) -> ToolLoopAgentRunner:
    runner = ToolLoopAgentRunner.__new__(ToolLoopAgentRunner)
    runner.run_context = SimpleNamespace(
        context=SimpleNamespace(extra={"changed_files": entries})
    )
    runner.stats = SimpleNamespace(start_time=100.0)
    return runner


@pytest.mark.asyncio
async def test_builds_response_from_changed_files(monkeypatch):
    files = [
        {
            "path": "a.txt",
            "kind": "edit",
            "adds": 1,
            "dels": 0,
            "backup_id": "b1",
            "sha256": "x",
            "runtime": "local",
            "diff_available": True,
        }
    ]

    async def fake_summary(entries, *, since_ts=0.0, history=None):
        assert since_ts == 100.0
        return files

    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.edit_history.build_turn_change_summary",
        fake_summary,
    )
    runner = _runner_with_entries([{"path": "a.txt", "ts": 101.0}])
    resp = await runner._build_file_changes_response()
    assert resp is not None
    assert resp.type == "file_changes"
    comp = resp.data["chain"].chain[0]
    assert comp.data == {"files": files}


@pytest.mark.asyncio
async def test_no_entries_returns_none():
    runner = _runner_with_entries([])
    assert await runner._build_file_changes_response() is None


@pytest.mark.asyncio
async def test_summary_failure_returns_none(monkeypatch):
    async def boom(entries, *, since_ts=0.0, history=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.edit_history.build_turn_change_summary",
        boom,
    )
    runner = _runner_with_entries([{"path": "a.txt", "ts": 101.0}])
    assert await runner._build_file_changes_response() is None
