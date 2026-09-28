"""Tests for the local shell resolver (Git Bash / pwsh / powershell / cmd)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrbot.core.computer.booters import local as local_booter  # noqa: E402

_ENV_KEYS = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")


def _isolate_env(monkeypatch):
    """Remove the well-known Git install roots so only PATH decides."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_find_git_bash_derives_from_git_on_path(tmp_path, monkeypatch):
    """Git on D: is found by deriving bash.exe from git.exe's location."""
    root = tmp_path / "Git"
    (root / "cmd").mkdir(parents=True)
    (root / "bin").mkdir()
    (root / "cmd" / "git.exe").touch()
    (root / "bin" / "bash.exe").touch()
    _isolate_env(monkeypatch)
    monkeypatch.setattr(
        local_booter.shutil,
        "which",
        lambda name: str(root / "cmd" / "git.exe") if name == "git" else None,
    )

    assert local_booter._find_git_bash() == str(root / "bin" / "bash.exe")


def test_find_git_bash_never_uses_path_bash(monkeypatch):
    """`which("bash")` resolves to the WSL launcher stub and must be ignored."""
    stub = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\bash.EXE"
    _isolate_env(monkeypatch)
    monkeypatch.setattr(
        local_booter.shutil,
        "which",
        lambda name: stub if name == "bash" else None,
    )

    assert local_booter._find_git_bash() is None


def test_find_git_bash_falls_back_to_program_files(tmp_path, monkeypatch):
    root = tmp_path / "pf" / "Git"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "bash.exe").touch()
    monkeypatch.setattr(local_booter.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    for key in ("ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(key, raising=False)

    assert local_booter._find_git_bash() == str(root / "bin" / "bash.exe")


def test_resolve_auto_prefers_git_bash(monkeypatch):
    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(local_booter, "_find_git_bash", lambda: r"D:\Git\bin\bash.exe")

    spec = local_booter.resolve_local_shell("auto")

    assert spec.family == "git_bash"
    assert spec.executable == r"D:\Git\bin\bash.exe"
    assert spec.prefix_args == ("-c",)


def test_resolve_auto_falls_back_to_pwsh(monkeypatch):
    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(local_booter, "_find_git_bash", lambda: None)
    monkeypatch.setattr(
        local_booter.shutil,
        "which",
        lambda name: "/opt/pwsh" if name == "pwsh" else None,
    )

    spec = local_booter.resolve_local_shell("auto")

    assert spec.family == "pwsh"
    assert spec.prefix_args == (
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    )


def test_resolve_auto_falls_back_to_windows_powershell(monkeypatch):
    """Neither Git Bash nor pwsh present, as on the reference machine."""
    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(local_booter, "_find_git_bash", lambda: None)
    monkeypatch.setattr(local_booter.shutil, "which", lambda name: None)

    spec = local_booter.resolve_local_shell("auto")

    assert spec.family == "powershell"
    assert spec.executable == "powershell.exe"


def test_resolve_explicit_family_is_honoured(monkeypatch):
    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(
        local_booter.shutil,
        "which",
        lambda name: "cmd.EXE" if name == "cmd" else None,
    )

    spec = local_booter.resolve_local_shell("cmd")

    assert spec.family == "cmd"
    assert spec.prefix_args == ("/d", "/s", "/c")


def test_resolve_unavailable_family_falls_back_to_auto(monkeypatch):
    """A config naming an uninstalled shell must not disable execution."""
    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(local_booter, "_find_git_bash", lambda: None)
    monkeypatch.setattr(local_booter.shutil, "which", lambda name: None)

    assert local_booter.resolve_local_shell("git_bash").family == "powershell"


def test_resolve_unknown_shell_type_falls_back_to_auto(monkeypatch):
    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(local_booter, "_find_git_bash", lambda: None)
    monkeypatch.setattr(local_booter.shutil, "which", lambda name: None)

    assert local_booter.resolve_local_shell("nonsense").family == "powershell"


def test_resolve_non_windows_returns_posix(monkeypatch):
    monkeypatch.setattr(local_booter.sys, "platform", "linux")

    spec = local_booter.resolve_local_shell("git_bash")

    assert spec.family == "posix"
    assert spec.prefix_args == ()
