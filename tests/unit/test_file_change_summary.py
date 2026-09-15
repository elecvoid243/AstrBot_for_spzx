from __future__ import annotations

import asyncio
import shutil
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


def test_summary_created_then_gone_drops_row(tmp_path: Path):
    """A created file that is gone by summary time was a scratch artifact the
    turn cleaned up, so it must not leave a statless row behind."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    target = scratch / "dump.cjs"
    target.write_text("x = 1\n", encoding="utf-8")
    target.unlink()
    scratch.rmdir()

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
    assert summary == []


def test_summary_write_then_gone_keeps_row(tmp_path: Path):
    """Only `created` rows are dropped. A `write` row means the path existed
    before the turn, so its disappearance is a real deletion worth reporting."""
    target = tmp_path / "preexisting.txt"
    target.write_text("v2\n", encoding="utf-8")
    target.unlink()

    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "write",
                    "runtime": "local",
                    "backup_id": "b1",
                    "ts": 1.0,
                }
            ]
        )
    )
    assert len(summary) == 1
    assert summary[0]["kind"] == "write"
    assert summary[0]["adds"] is None


def test_summary_created_then_removed_keeps_removal_row(tmp_path: Path):
    """An explicit removal later in the turn upgrades the row to `remove`, so
    the drop rule must not swallow it."""
    target = tmp_path / "tmp.cjs"
    target.write_text("x = 1\n", encoding="utf-8")
    target.unlink()

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
                {
                    "path": str(target),
                    "kind": "remove",
                    "runtime": "local",
                    "backup_id": "",
                    "ts": 2.0,
                },
            ]
        )
    )
    assert len(summary) == 1
    assert summary[0]["kind"] == "remove"


def test_summary_created_but_unreadable_keeps_row(tmp_path: Path):
    """Present-but-unreadable is not "gone": the row stays. A directory is the
    portable way to make read_bytes() fail without deleting the path."""
    target = tmp_path / "not-a-file"
    target.mkdir()

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
    assert len(summary) == 1
    assert summary[0]["adds"] is None


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


def test_summary_remove(tmp_path: Path):
    """Removed files: no content reads, no stats, card renders a removal row."""
    target = tmp_path / "gone.txt"
    target.write_text("x\n", encoding="utf-8")
    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "remove",
                    "runtime": "local",
                    "backup_id": "",
                    "ts": 1.0,
                },
            ]
        )
    )
    assert len(summary) == 1
    assert summary[0]["kind"] == "remove"
    assert summary[0]["adds"] is None
    assert summary[0]["dels"] is None
    assert summary[0]["sha256"] == ""
    assert summary[0]["diff_available"] is False


def test_summary_edit_then_remove_upgrades_kind(
    tmp_path: Path, history: EditHistoryManager
):
    """An edit followed by a removal must surface as a removal row."""
    target = tmp_path / "e.txt"
    target.write_text("v1\n", encoding="utf-8")
    entry = history.save_backup(str(target), target.read_bytes())
    target.write_text("v1\nv2\n", encoding="utf-8")

    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "edit",
                    "runtime": "local",
                    "backup_id": entry.id,
                    "ts": 1.0,
                },
                {
                    "path": str(target),
                    "kind": "remove",
                    "runtime": "local",
                    "backup_id": "",
                    "ts": 2.0,
                },
            ],
            history=history,
        )
    )
    assert len(summary) == 1
    assert summary[0]["kind"] == "remove"
    assert summary[0]["backup_id"] == ""
    assert summary[0]["diff_available"] is False


def test_summary_edit_under_removed_directory_becomes_removal(
    tmp_path: Path, history: EditHistoryManager
):
    """file_remove records the directory it deleted, not each file inside it,
    so an edited file that vanished together with that directory must surface
    as a removal row instead of a statless edit row."""
    proj = tmp_path / "proj"
    proj.mkdir()
    target = proj / "existing.py"
    target.write_text("v1\n", encoding="utf-8")
    entry = history.save_backup(str(target), target.read_bytes())
    target.write_text("v1\nv2\n", encoding="utf-8")
    shutil.rmtree(proj)

    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "edit",
                    "runtime": "local",
                    "backup_id": entry.id,
                    "ts": 1.0,
                },
                {
                    "path": str(proj),
                    "kind": "remove",
                    "runtime": "local",
                    "backup_id": "",
                    "ts": 2.0,
                },
            ],
            history=history,
        )
    )
    assert [row["path"] for row in summary] == [str(target), str(proj)]
    row = summary[0]
    assert row["kind"] == "remove"
    assert row["backup_id"] == ""
    assert row["adds"] is None
    assert row["dels"] is None
    assert row["diff_available"] is False
    assert summary[1]["kind"] == "remove"


def test_summary_gone_sibling_directory_keeps_kind(
    tmp_path: Path, history: EditHistoryManager
):
    """Only paths *inside* a removed directory are upgraded. A sibling whose
    name merely shares the prefix ("proj" vs "proj-extra") stays an edit row."""
    proj = tmp_path / "proj"
    proj.mkdir()
    sibling = tmp_path / "proj-extra"
    sibling.mkdir()
    target = sibling / "other.py"
    target.write_text("v1\n", encoding="utf-8")
    entry = history.save_backup(str(target), target.read_bytes())
    target.write_text("v1\nv2\n", encoding="utf-8")
    shutil.rmtree(sibling)
    shutil.rmtree(proj)

    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "edit",
                    "runtime": "local",
                    "backup_id": entry.id,
                    "ts": 1.0,
                },
                {
                    "path": str(proj),
                    "kind": "remove",
                    "runtime": "local",
                    "backup_id": "",
                    "ts": 2.0,
                },
            ],
            history=history,
        )
    )
    row = next(item for item in summary if item["path"] == str(target))
    assert row["kind"] == "edit"
    assert row["adds"] is None
    assert row["diff_available"] is False


def test_summary_removal_before_turn_window_ignored(
    tmp_path: Path, history: EditHistoryManager
):
    """A removal recorded before the turn window must not relabel a file that
    vanished for some other reason in this turn."""
    proj = tmp_path / "proj"
    proj.mkdir()
    target = proj / "existing.py"
    target.write_text("v1\n", encoding="utf-8")
    entry = history.save_backup(str(target), target.read_bytes())
    target.write_text("v1\nv2\n", encoding="utf-8")
    shutil.rmtree(proj)

    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "edit",
                    "runtime": "local",
                    "backup_id": entry.id,
                    "ts": 6.0,
                },
                {
                    "path": str(proj),
                    "kind": "remove",
                    "runtime": "local",
                    "backup_id": "",
                    "ts": 1.0,
                },
            ],
            since_ts=5.0,
            history=history,
        )
    )
    assert len(summary) == 1
    assert summary[0]["kind"] == "edit"
    assert summary[0]["adds"] is None
