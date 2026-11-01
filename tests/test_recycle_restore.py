"""recycle_restore 回收站恢复测试。

自 spcode 插件 ``tests/test_file_remove_restore.py`` 平移;全部用例通过
``recycle_root`` 注入合成回收站目录,不触碰真实回收站。Windows/Linux 分支
用 monkeypatch 覆盖 ``sys.platform``,因此可在任意平台运行。
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from astrbot.core.tools.computer_tools import recycle_restore as frr

# ── 合成回收站工具 ────────────────────────────────────


def _make_i_bytes_v2(original: str, size: int = 5) -> bytes:
    """版本 2 元数据:null 结尾 UTF-16 名称(经典布局)。"""
    data = (2).to_bytes(8, "little")
    data += size.to_bytes(8, "little")
    data += (0).to_bytes(8, "little")
    data += original.encode("utf-16-le") + b"\x00\x00"
    return data


def _make_i_bytes_v4(original: str, size: int = 5) -> bytes:
    """版本 4 元数据:4 字节名称长度前缀(码元数,含结尾 null)。"""
    name = original.encode("utf-16-le") + b"\x00\x00"
    data = (4).to_bytes(8, "little")
    data += size.to_bytes(8, "little")
    data += (0).to_bytes(8, "little")
    data += (len(name) // 2).to_bytes(4, "little")
    data += name
    return data


def _make_i_bytes_v2_lengthprefixed(original: str, size: int = 5) -> bytes:
    """真实机器观察到的布局:版本字段为 2 但带 4 字节名称长度前缀
    (码元数,含结尾 null)——Win10/11 与 send2trash 的实际产物。"""
    name = original.encode("utf-16-le") + b"\x00\x00"
    data = (2).to_bytes(8, "little")
    data += size.to_bytes(8, "little")
    data += (0).to_bytes(8, "little")
    data += (len(name) // 2).to_bytes(4, "little")
    data += name
    return data


def _seed_windows_bin(
    recycle_root: Path, original: Path, content: bytes, i_bytes: bytes
) -> Path:
    sid = recycle_root / "$Recycle.Bin" / "S-1-5-18"
    sid.mkdir(parents=True)
    meta = sid / "$I1234567.txt"
    meta.write_bytes(i_bytes)
    (sid / "$R1234567.txt").write_bytes(content)
    return meta


# ── Windows 分支 ──────────────────────────────────────


@pytest.mark.parametrize(
    "make_i",
    [_make_i_bytes_v4, _make_i_bytes_v2_lengthprefixed],
)
def test_restore_windows_length_prefixed_i_file(
    tmp_path: Path, monkeypatch, make_i
) -> None:
    """版本 4 / 带长度前缀的版本 2 布局 → 搬回原位并清理元数据。"""
    monkeypatch.setattr(sys, "platform", "win32")
    original = tmp_path / "workspace" / "报告.md"
    recycle_root = tmp_path / "recycle"
    meta = _seed_windows_bin(
        recycle_root, original, "数据内容".encode(), make_i(str(original), 12)
    )
    assert not original.exists()

    result = frr.restore_from_recycle_bin(str(original), recycle_root=str(recycle_root))

    assert result["ok"] is True
    assert Path(result["restored_path"]) == original
    assert original.read_bytes() == "数据内容".encode()
    # 元数据与数据文件均被清理。
    assert not meta.exists()
    assert not meta.with_name("$R1234567.txt").exists()


def test_restore_windows_legacy_null_terminated_i_file(
    tmp_path: Path, monkeypatch
) -> None:
    """版本 2 经典 null 结尾布局 → 搬回原位并清理元数据。"""
    monkeypatch.setattr(sys, "platform", "win32")
    original = tmp_path / "workspace" / "notes.txt"
    recycle_root = tmp_path / "recycle"
    meta = _seed_windows_bin(
        recycle_root, original, b"legacy", _make_i_bytes_v2(str(original), 6)
    )

    result = frr.restore_from_recycle_bin(str(original), recycle_root=str(recycle_root))

    assert result["ok"] is True
    assert Path(result["restored_path"]) == original
    assert original.read_bytes() == b"legacy"
    assert not meta.exists()


def test_restore_target_exists_returns_error(tmp_path: Path, monkeypatch) -> None:
    """目标位置已被占用时拒绝恢复,不覆盖现有文件。"""
    monkeypatch.setattr(sys, "platform", "win32")
    original = tmp_path / "workspace" / "a.txt"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"new content")
    recycle_root = tmp_path / "recycle"
    _seed_windows_bin(recycle_root, original, b"old", _make_i_bytes_v2(str(original)))

    result = frr.restore_from_recycle_bin(str(original), recycle_root=str(recycle_root))

    assert result["ok"] is False
    assert "已存在" in result["error"]
    assert original.read_bytes() == b"new content"


def test_restore_not_found_returns_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    recycle_root = tmp_path / "recycle"
    recycle_root.mkdir()

    result = frr.restore_from_recycle_bin(
        str(tmp_path / "gone.txt"), recycle_root=str(recycle_root)
    )

    assert result["ok"] is False
    assert "未找到" in result["error"]


def test_restore_windows_picks_newest(tmp_path: Path, monkeypatch) -> None:
    """同一路径多次删除时取最新一条记录。"""
    monkeypatch.setattr(sys, "platform", "win32")
    original = tmp_path / "workspace" / "a.txt"
    recycle_root = tmp_path / "recycle"
    sid = recycle_root / "$Recycle.Bin" / "S-1-5-18"
    sid.mkdir(parents=True)
    for i, content in enumerate((b"old", b"newest")):
        meta = sid / f"$I000000{i}.txt"
        meta.write_bytes(_make_i_bytes_v2(str(original)))
        (sid / f"$R000000{i}.txt").write_bytes(content)
        os.utime(meta, (1000 + i, 1000 + i))

    result = frr.restore_from_recycle_bin(str(original), recycle_root=str(recycle_root))

    assert result["ok"] is True
    assert original.read_bytes() == b"newest"


# ── Linux 分支(XDG Trash) ────────────────────────────


def test_restore_linux_xdg_trashinfo(tmp_path: Path, monkeypatch) -> None:
    """``Path=`` 百分号解码后匹配 → 搬回原位并清理 .trashinfo。"""
    monkeypatch.setattr(sys, "platform", "linux")
    original = tmp_path / "workspace" / "b.txt"
    recycle_root = tmp_path / "recycle"
    info_dir = recycle_root / "info"
    files_dir = recycle_root / "files"
    info_dir.mkdir(parents=True)
    files_dir.mkdir()
    info = info_dir / "b.txt.trashinfo"
    info.write_text(
        f"[Trash Info]\nPath={quote(str(original))}\nDeletionDate=20260914T10:00:00\n",
        encoding="utf-8",
    )
    (files_dir / "b.txt").write_bytes(b"trashed")

    result = frr.restore_from_recycle_bin(str(original), recycle_root=str(recycle_root))

    assert result["ok"] is True
    assert Path(result["restored_path"]) == original
    assert original.read_bytes() == b"trashed"
    assert not info.exists()


def test_restore_linux_picks_newest(tmp_path: Path, monkeypatch) -> None:
    """同一路径多条 trashed 记录时取最新一条。"""
    monkeypatch.setattr(sys, "platform", "linux")
    original = tmp_path / "workspace" / "b.txt"
    recycle_root = tmp_path / "recycle"
    info_dir = recycle_root / "info"
    files_dir = recycle_root / "files"
    info_dir.mkdir(parents=True)
    files_dir.mkdir()
    for i, content in enumerate((b"old", b"newest")):
        info = info_dir / f"b{i}.txt.trashinfo"
        info.write_text(
            f"[Trash Info]\nPath={quote(str(original))}\n", encoding="utf-8"
        )
        (files_dir / f"b{i}.txt").write_bytes(content)
        os.utime(info, (1000 + i, 1000 + i))

    result = frr.restore_from_recycle_bin(str(original), recycle_root=str(recycle_root))

    assert result["ok"] is True
    assert original.read_bytes() == b"newest"


def test_restore_linux_relative_path_skipped(tmp_path: Path, monkeypatch) -> None:
    """挂载点回收站的相对 Path= 无法可靠还原,应视为未找到。"""
    monkeypatch.setattr(sys, "platform", "linux")
    recycle_root = tmp_path / "recycle"
    info_dir = recycle_root / "info"
    info_dir.mkdir(parents=True)
    (info_dir / "c.txt.trashinfo").write_text(
        "[Trash Info]\nPath=rel/c.txt\n", encoding="utf-8"
    )

    result = frr.restore_from_recycle_bin(
        str(tmp_path / "rel" / "c.txt"), recycle_root=str(recycle_root)
    )

    assert result["ok"] is False


# ── 入口防御 ──────────────────────────────────────────


def test_restore_empty_path_returns_error() -> None:
    assert frr.restore_from_recycle_bin("") == {"ok": False, "error": "路径为空"}
    assert frr.restore_from_recycle_bin("   ") == {"ok": False, "error": "路径为空"}


def test_restore_unsupported_platform(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    result = frr.restore_from_recycle_bin("/tmp/somefile")
    assert result["ok"] is False
    assert "不支持" in result["error"]


@pytest.mark.parametrize("directory", [False, True])
def test_concurrent_deletions_never_guess_an_ambiguous_new_entry(
    tmp_path, monkeypatch, directory
):
    """Hold A after its trash move while B recreates and deletes the same path."""
    from astrbot.core.tools.computer_tools import fs

    monkeypatch.setattr(sys, "platform", "win32")
    recycle_root = tmp_path / "recycle"
    folder = recycle_root / "$Recycle.Bin" / "synthetic-sid"
    folder.mkdir(parents=True)
    monkeypatch.setattr(frr, "_windows_drive_roots", lambda: [recycle_root])
    original = tmp_path / "same-path"
    if directory:
        original.mkdir()
        (original / "empty").mkdir()
        (original / "content.txt").write_text("A original")
    else:
        original.write_text("A original")
    a_moved, b_captured = Event(), Event()

    def trash(path):
        number = 2 if a_moved.is_set() else 1
        (folder / f"$I{number}.bin").write_bytes(_make_i_bytes_v4(str(original)))
        Path(path).replace(folder / f"$R{number}.bin")
        if number == 1:
            a_moved.set()
            assert b_captured.wait(10)

    monkeypatch.setattr(
        fs, "_load_send2trash", lambda: SimpleNamespace(send2trash=trash)
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(fs._remove_path, str(original), confirm=True, max_items=50)
        assert a_moved.wait(10)
        if directory:
            original.mkdir()
            (original / "content.txt").write_text("B newer")
        else:
            original.write_text("B newer")
        b = executor.submit(fs._remove_path, str(original), confirm=True, max_items=50)
        b_result = b.result(timeout=10)
        b_captured.set()
        a_result = a.result(timeout=10)
    assert a_result["ok"] and a_result["_recycle_entry"] is None
    assert b_result["ok"] and b_result["_recycle_entry"]
    restored = frr.restore_from_recycle_bin(
        str(original),
        expected_entry=b_result["_recycle_entry"],
        expected_sha256=b_result["_removed_sha256"],
    )
    assert restored["ok"]
    assert (folder / "$R1.bin").exists()
    assert (
        (original / "content.txt").read_text() == "B newer"
        if directory
        else original.read_text() == "B newer"
    )


@pytest.mark.parametrize("directory", [False, True])
def test_exact_recycle_evidence_rejects_modified_data_occupied_destination_and_links(
    tmp_path, monkeypatch, directory
):
    """A stale card never selects another item or overwrites an occupied path."""
    from astrbot.core.tools.computer_tools import fs

    monkeypatch.setattr(sys, "platform", "win32")
    recycle_root = tmp_path / "recycle"
    folder = recycle_root / "$Recycle.Bin" / "synthetic-sid"
    folder.mkdir(parents=True)
    monkeypatch.setattr(frr, "_windows_drive_roots", lambda: [recycle_root])
    original = tmp_path / "original"
    if directory:
        original.mkdir()
        (original / "nested.txt").write_text("original")
    else:
        original.write_text("original")

    def trash(path):
        (folder / "$I1.bin").write_bytes(_make_i_bytes_v4(str(original)))
        Path(path).replace(folder / "$R1.bin")

    monkeypatch.setattr(
        fs, "_load_send2trash", lambda: SimpleNamespace(send2trash=trash)
    )
    deleted = fs._remove_path(str(original), confirm=True, max_items=50)
    assert deleted["ok"] and deleted["_recycle_entry"]
    original.write_text("occupied")
    refused = frr.restore_from_recycle_bin(
        str(original),
        expected_entry=deleted["_recycle_entry"],
        expected_sha256=deleted["_removed_sha256"],
    )
    assert not refused["ok"]
    assert original.read_text() == "occupied"
    original.unlink()
    data = folder / "$R1.bin"
    changed = data / "nested.txt" if directory else data
    changed.write_text("other account changed content")
    refused = frr.restore_from_recycle_bin(
        str(original),
        expected_entry=deleted["_recycle_entry"],
        expected_sha256=deleted["_removed_sha256"],
    )
    assert not refused["ok"]
    assert not original.exists()
    assert changed.read_text() == "other account changed content"
    # Metadata reuse cannot reuse the proof, even if the raw original path matches.
    (folder / "$I1.bin").write_bytes(_make_i_bytes_v4(str(original), size=999))
    assert not frr.restore_from_recycle_bin(
        str(original),
        expected_entry=deleted["_recycle_entry"],
        expected_sha256=deleted["_removed_sha256"],
    )["ok"]


def test_directory_evidence_refuses_links_without_following_the_target(tmp_path):
    root = tmp_path / "directory"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "private.txt").write_text("private")
    try:
        (root / "link").symlink_to(external, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("Creating a test symlink is unavailable")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(root / "link"), str(external)],
            check=True,
            capture_output=True,
            creationflags=0x08000000,
        )
    with pytest.raises(OSError, match="identity|Linked"):
        frr.recycle_content_evidence(root)
    assert (external / "private.txt").read_text() == "private"
