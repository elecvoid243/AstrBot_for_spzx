from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from astrbot.core.tools.computer_tools.edit_history import (
    EditHistoryManager,
    build_turn_change_summary,
    count_unified_diff_changes,
    render_unified_diff,
)


@pytest.fixture()
def history(tmp_path: Path) -> EditHistoryManager:
    return EditHistoryManager(base_dir=tmp_path / "history")


def test_count_unified_diff_changes():
    adds, dels = count_unified_diff_changes("a\nb\nc\n", "a\nB\nc\nd\n")
    assert (adds, dels) == (2, 1)


def test_render_unified_diff_truncates():
    old = "".join(f"line{i}\n" for i in range(10))
    new = "".join(f"line{i}\n" for i in range(20000))
    result = render_unified_diff(old.encode(), new.encode(), "x.txt", max_chars=500)
    assert result["truncated"] is True
    assert len(result["diff"]) <= 500
    assert result["adds"] > 0


def test_summary_edit_net_diff(tmp_path: Path, history: EditHistoryManager):
    target = tmp_path / "a.txt"
    target.write_text("v1\n", encoding="utf-8")
    entry = history.save_backup(str(target), target.read_bytes())
    target.write_text("v1\nv2\nv3\n", encoding="utf-8")

    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "edit",
                    "runtime": "local",
                    "backup_id": entry.id,
                    "ts": 1.0,
                }
            ],
            history=history,
        )
    )
    assert len(summary) == 1
    assert summary[0]["kind"] == "edit"
    assert summary[0]["adds"] == 2
    assert summary[0]["dels"] == 0
    assert summary[0]["diff_available"] is True
    assert summary[0]["backup_id"] == entry.id
    assert len(summary[0]["sha256"]) == 64


def test_summary_created_file(tmp_path: Path):
    target = tmp_path / "new.txt"
    target.write_text("hello\nworld\n", encoding="utf-8")
    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "created",
                    "runtime": "local",
                    "backup_id": "",
                    "ts": 1.0,
                }
            ]
        )
    )
    assert summary[0]["adds"] == 2
    assert summary[0]["dels"] == 0
    assert summary[0]["diff_available"] is True


def test_summary_multiple_edits_dedup_first_baseline(
    tmp_path: Path, history: EditHistoryManager
):
    target = tmp_path / "b.txt"
    target.write_text("one\n", encoding="utf-8")
    first = history.save_backup(str(target), target.read_bytes())
    target.write_text("one\ntwo\n", encoding="utf-8")
    history.save_backup(str(target), target.read_bytes())
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")

    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "edit",
                    "runtime": "local",
                    "backup_id": first.id,
                    "ts": 1.0,
                },
                {
                    "path": str(target),
                    "kind": "edit",
                    "runtime": "local",
                    "backup_id": "later",
                    "ts": 2.0,
                },
            ],
            history=history,
        )
    )
    # Net diff vs the FIRST backup of the turn: +2 lines.
    assert summary[0]["adds"] == 2
    assert summary[0]["backup_id"] == first.id


def test_summary_since_ts_filters_old_entries(tmp_path: Path):
    target = tmp_path / "c.txt"
    target.write_text("x\n", encoding="utf-8")
    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "created",
                    "runtime": "local",
                    "backup_id": "",
                    "ts": 1.0,
                },
            ],
            since_ts=5.0,
        )
    )
    assert summary == []


def test_summary_sandbox_not_computable(tmp_path: Path, history: EditHistoryManager):
    target = tmp_path / "s.txt"
    target.write_text("v1\n", encoding="utf-8")
    entry = history.save_backup(str(target), target.read_bytes())
    target.write_text("v2\n", encoding="utf-8")
    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "edit",
                    "runtime": "sandbox",
                    "backup_id": entry.id,
                    "ts": 1.0,
                },
            ]
        )
    )
    assert summary[0]["diff_available"] is False
    assert summary[0]["adds"] is None


def test_summary_missing_baseline_degrades(tmp_path: Path):
    target = tmp_path / "d.txt"
    target.write_text("v2\n", encoding="utf-8")
    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "edit",
                    "runtime": "local",
                    "backup_id": "nonexistent",
                    "ts": 1.0,
                },
            ]
        )
    )
    assert summary[0]["diff_available"] is False
    assert summary[0]["adds"] is None
    assert len(summary[0]["sha256"]) == 64
