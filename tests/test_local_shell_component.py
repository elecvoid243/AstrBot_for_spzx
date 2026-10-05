from __future__ import annotations

import asyncio
import os
import shlex
import signal
import subprocess
import sys

import psutil
import pytest

from astrbot.core.computer.booters import local as local_booter
from astrbot.core.computer.booters.local import LocalShellComponent


class _FakePopen:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.pid = 12345

    def communicate(self, timeout=None):
        return self._stdout, self._stderr

    def wait(self, timeout=None):
        pass


class _FakeTaskkillResult:
    def __init__(self, returncode: int):
        self.returncode = returncode


def _python_command(code: str) -> str:
    """Build a shell-safe Python command for the current operating system.

    The command string is handed to whichever shell the local runtime
    resolved, and PowerShell and Git Bash disagree on how a quoted
    interpreter path is invoked: PowerShell needs the call operator, which
    bash reads as a background separator. An unquoted forward-slash path
    works in both, so only the code argument is quoted.
    """
    if os.name != "nt":
        return shlex.join([sys.executable, "-u", "-c", code])
    if " " in sys.executable:
        pytest.skip(
            "Interpreter path contains a space; PowerShell and Git Bash need "
            "different quoting for it."
        )
    interpreter = sys.executable.replace("\\", "/")
    return f'{interpreter} -u -c "{code}"'


@pytest.fixture(autouse=True)
def _no_git_bash_by_default(monkeypatch):
    """Keep shell detection off the real filesystem unless a test opts in.

    Most tests here pin the PowerShell fallback chain or the decoding path, so
    letting `_find_git_bash()` probe the host would make them depend on whether
    Git for Windows happens to be installed. Tests that exercise Git Bash patch
    `_find_git_bash` themselves, which overrides this.
    """
    monkeypatch.setattr(local_booter, "_find_git_bash", lambda: None)


def test_local_shell_component_decodes_utf8_output(monkeypatch):
    def fake_run(*args, **kwargs):
        _ = args, kwargs
        return _FakePopen(stdout="技能内容".encode())

    monkeypatch.setattr(subprocess, "Popen", fake_run)

    result = asyncio.run(LocalShellComponent().exec("dummy"))

    assert result["stdout"] == "技能内容"
    assert result["stderr"] == ""
    assert result["exit_code"] == 0


def test_local_shell_component_uses_windows_powershell(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakePopen(stdout=b"")

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(local_booter.shutil, "which", lambda _cmd: None)

    result = asyncio.run(LocalShellComponent().exec("Get-ChildItem"))

    assert result["exit_code"] == 0
    assert calls[0][0][0] == [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-ChildItem",
    ]
    assert calls[0][1]["shell"] is False


def test_local_shell_component_prefers_pwsh_when_available(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakePopen(stdout=b"")

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(
        local_booter.shutil,
        "which",
        lambda cmd: "/opt/pwsh" if cmd == "pwsh" else None,
    )

    result = asyncio.run(LocalShellComponent().exec("Get-ChildItem"))

    assert result["exit_code"] == 0
    assert calls[0][0][0] == [
        "pwsh.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-ChildItem",
    ]
    assert calls[0][1]["shell"] is False


def test_exec_falls_back_to_powershell_when_pwsh_missing(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakePopen(stdout=b"")

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(local_booter.shutil, "which", lambda _cmd: None)

    result = asyncio.run(LocalShellComponent().exec("Get-ChildItem"))

    assert result["exit_code"] == 0
    assert calls[0][0][0] == [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-ChildItem",
    ]
    assert calls[0][1]["shell"] is False


def test_local_shell_component_keeps_platform_shell_outside_windows(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakePopen(stdout=b"")

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    monkeypatch.setattr(local_booter.sys, "platform", "linux")

    result = asyncio.run(LocalShellComponent().exec("pwd"))

    assert result["exit_code"] == 0
    assert calls[0][0][0] == "pwd"
    assert calls[0][1]["shell"] is True


@pytest.mark.asyncio
async def test_managed_shell_uses_windows_powershell(monkeypatch, tmp_path):
    calls = []

    class FakeStdout:
        def __init__(self):
            self.chunks = [b"done\n", b""]

        async def read(self, _limit):
            return self.chunks.pop(0)

    class FakeProcess:
        def __init__(self):
            self.pid = 12345
            self.returncode = None
            self.stdout = FakeStdout()
            self.stdin = None

        async def wait(self):
            self.returncode = 0
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    async def fail_create_subprocess_shell(*_args, **_kwargs):
        raise AssertionError("Windows managed commands must not use cmd.exe.")

    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(local_booter.shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(
        local_booter.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        local_booter.asyncio,
        "create_subprocess_shell",
        fail_create_subprocess_shell,
    )

    result = await LocalShellComponent().exec_managed(
        "Get-ChildItem",
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=False,
        sandboxed=False,
        cwd=str(tmp_path),
        yield_time_ms=5_000,
    )

    assert result["status"] == "completed"
    assert result["stdout"] == "done\n"
    assert calls[0][0] == (
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-ChildItem",
    )
    assert "creationflags" in calls[0][1]
    # The managed shell hides its console window via STARTUPINFO so spawning
    # powershell.exe under pythonw.exe does not flash a window, while keeping
    # CREATE_NEW_PROCESS_GROUP (and a real console) so CTRL_BREAK_EVENT
    # interrupt keeps working.
    creationflags = calls[0][1]["creationflags"]
    assert creationflags & subprocess.CREATE_NEW_PROCESS_GROUP
    assert not (creationflags & subprocess.CREATE_NO_WINDOW)
    startupinfo = calls[0][1]["startupinfo"]
    assert startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert startupinfo.wShowWindow == 0  # SW_HIDE


@pytest.mark.asyncio
async def test_managed_shell_prefers_pwsh_when_available(monkeypatch, tmp_path):
    calls = []

    class FakeStdout:
        def __init__(self):
            self.chunks = [b"done\n", b""]

        async def read(self, _limit):
            return self.chunks.pop(0)

    class FakeProcess:
        def __init__(self):
            self.pid = 12345
            self.returncode = None
            self.stdout = FakeStdout()
            self.stdin = None

        async def wait(self):
            self.returncode = 0
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    async def fail_create_subprocess_shell(*_args, **_kwargs):
        raise AssertionError("Windows managed commands must not use cmd.exe.")

    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(
        local_booter.shutil,
        "which",
        lambda cmd: "/opt/pwsh" if cmd == "pwsh" else None,
    )
    monkeypatch.setattr(
        local_booter.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        local_booter.asyncio,
        "create_subprocess_shell",
        fail_create_subprocess_shell,
    )

    result = await LocalShellComponent().exec_managed(
        "Get-ChildItem",
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=False,
        sandboxed=False,
        cwd=str(tmp_path),
        yield_time_ms=5_000,
    )

    assert result["status"] == "completed"
    assert result["stdout"] == "done\n"
    assert calls[0][0] == (
        "pwsh.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-ChildItem",
    )
    assert "creationflags" in calls[0][1]


def test_local_shell_component_prefers_utf8_before_windows_locale(
    monkeypatch,
):
    def fake_run(*args, **kwargs):
        _ = args, kwargs
        return _FakePopen(stdout="技能内容".encode())

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    monkeypatch.setattr(local_booter.os, "name", "nt", raising=False)
    monkeypatch.setattr(
        local_booter.locale,
        "getpreferredencoding",
        lambda _do_setlocale=False: "cp936",
    )

    result = asyncio.run(LocalShellComponent().exec("dummy"))

    assert result["stdout"] == "技能内容"
    assert result["stderr"] == ""
    assert result["exit_code"] == 0


def test_local_shell_component_falls_back_to_gbk_on_windows(monkeypatch):
    def fake_run(*args, **kwargs):
        _ = args, kwargs
        return _FakePopen(stdout="微博热搜".encode("gbk"))

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    monkeypatch.setattr(local_booter.os, "name", "nt", raising=False)
    monkeypatch.setattr(
        local_booter.locale,
        "getpreferredencoding",
        lambda _do_setlocale=False: "cp1252",
    )

    result = asyncio.run(LocalShellComponent().exec("dummy"))

    assert result["stdout"] == "微博热搜"
    assert result["stderr"] == ""
    assert result["exit_code"] == 0


def test_local_shell_component_falls_back_to_utf8_replace(monkeypatch):
    def fake_run(*args, **kwargs):
        _ = args, kwargs
        return _FakePopen(stdout=b"\xffabc")

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    monkeypatch.setattr(local_booter.os, "name", "posix", raising=False)
    monkeypatch.setattr(
        local_booter.locale,
        "getpreferredencoding",
        lambda _do_setlocale=False: "utf-8",
    )

    result = asyncio.run(LocalShellComponent().exec("dummy"))

    assert result["stdout"] == "\ufffdabc"


def test_local_shell_component_falls_back_when_windows_taskkill_fails(monkeypatch):
    class TimeoutPopen:
        pid = 12345

        def __init__(self):
            self.killed = False
            self.wait_timeout = None

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="dummy", timeout=timeout)

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.wait_timeout = timeout

    proc = TimeoutPopen()

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: proc)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: _FakeTaskkillResult(returncode=1),
    )
    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(local_booter.shutil, "which", lambda _cmd: None)

    with pytest.raises(subprocess.TimeoutExpired):
        asyncio.run(LocalShellComponent().exec("dummy", timeout=1))

    assert proc.killed
    assert proc.wait_timeout == 5


@pytest.mark.asyncio
async def test_managed_shell_returns_completed_output_without_open_session():
    shell = LocalShellComponent()

    result = await shell.exec_managed(
        _python_command("print('hello')"),
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=False,
        sandboxed=False,
        yield_time_ms=5_000,
    )

    assert result["status"] == "completed"
    assert result["stdout"].splitlines() == ["hello"]
    assert result["exit_code"] == 0
    assert result["session_closed"] is True
    assert await shell.list_sessions(
        owner_id="owner-a",
        requester_id="user-a",
        requester_is_admin=False,
    ) == {"sessions": []}


@pytest.mark.asyncio
async def test_managed_shell_accepts_two_minute_yield_cap(tmp_path):
    """The 120s yield cap is accepted by both call sites and 120001 is rejected."""
    shell = LocalShellComponent()
    access = {
        "owner_id": "owner-a",
        "creator_id": "user-a",
        "creator_is_admin": False,
        "sandboxed": False,
        "cwd": str(tmp_path),
    }

    result = await shell.exec_managed(
        _python_command("print('done')"),
        yield_time_ms=120_000,
        **access,
    )

    assert result["status"] == "completed"
    assert result["stdout"].strip() == "done"
    with pytest.raises(ValueError, match="between 0 and 120000"):
        await shell.exec_managed(
            _python_command("print('done')"),
            yield_time_ms=120_001,
            **access,
        )
    with pytest.raises(ValueError, match="between 0 and 120000"):
        await shell.poll_session(
            owner_id="owner-a",
            requester_id="user-a",
            requester_is_admin=False,
            session_id="sh_missing",
            yield_time_ms=120_001,
        )


@pytest.mark.asyncio
async def test_managed_shell_allows_creator_and_conversation_admin():
    shell = LocalShellComponent()
    result = await shell.exec_managed(
        _python_command("import time; print('ready', flush=True); time.sleep(30)"),
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=False,
        sandboxed=True,
        yield_time_ms=200,
    )

    try:
        assert result["status"] == "running"
        assert result["stdout"].splitlines() == ["ready"]
        session_id = result["session_id"]
        assert (
            await shell.list_sessions(
                owner_id="owner-b",
                requester_id="user-a",
                requester_is_admin=False,
            )
        )["sessions"] == []
        sessions = (
            await shell.list_sessions(
                owner_id="owner-a",
                requester_id="user-a",
                requester_is_admin=False,
            )
        )["sessions"]
        assert [item["session_id"] for item in sessions] == [session_id]
        assert sessions[0]["sandboxed"] is True

        admin_sessions = (
            await shell.list_sessions(
                owner_id="owner-a",
                requester_id="admin-user",
                requester_is_admin=True,
            )
        )["sessions"]
        assert [item["session_id"] for item in admin_sessions] == [session_id]
        stopped = await shell.terminate_session(
            owner_id="owner-a",
            requester_id="admin-user",
            requester_is_admin=True,
            session_id=session_id,
        )

        assert stopped["status"] == "terminated"
        assert stopped["exit_code"] is not None
        assert stopped["session_closed"] is True
        assert await shell.list_sessions(
            owner_id="owner-a",
            requester_id="user-a",
            requester_is_admin=False,
        ) == {"sessions": []}
    finally:
        await shell.shutdown_sessions()


@pytest.mark.asyncio
async def test_managed_shell_rejects_cross_user_session_access():
    shell = LocalShellComponent()
    result = await shell.exec_managed(
        _python_command("import time; input(); time.sleep(30)"),
        owner_id="group-umo",
        creator_id="admin-user",
        creator_is_admin=True,
        sandboxed=False,
        yield_time_ms=100,
    )

    try:
        session_id = result["session_id"]
        member_access = {
            "owner_id": "group-umo",
            "requester_id": "member-user",
            "requester_is_admin": False,
        }
        demoted_creator_access = {
            "owner_id": "group-umo",
            "requester_id": "admin-user",
            "requester_is_admin": False,
        }
        other_conversation_admin_access = {
            "owner_id": "other-group-umo",
            "requester_id": "other-admin",
            "requester_is_admin": True,
        }

        assert await shell.list_sessions(**member_access) == {"sessions": []}
        assert await shell.list_sessions(**demoted_creator_access) == {"sessions": []}
        assert await shell.list_sessions(**other_conversation_admin_access) == {
            "sessions": []
        }
        with pytest.raises(ValueError, match="was not found"):
            await shell.poll_session(
                **member_access,
                session_id=session_id,
                cursor=0,
            )
        with pytest.raises(ValueError, match="was not found"):
            await shell.write_session(
                **member_access,
                session_id=session_id,
                chars="attacker-input\n",
            )
        with pytest.raises(ValueError, match="was not found"):
            await shell.interrupt_session(
                **member_access,
                session_id=session_id,
            )
        with pytest.raises(ValueError, match="was not found"):
            await shell.poll_session(
                **demoted_creator_access,
                session_id=session_id,
                cursor=0,
            )
        with pytest.raises(ValueError, match="was not found"):
            await shell.terminate_session(
                **other_conversation_admin_access,
                session_id=session_id,
            )
        with pytest.raises(ValueError, match="was not found"):
            await shell.terminate_session(
                **member_access,
                session_id=session_id,
            )

        assert shell._sessions[session_id].process.returncode is None
        admin_sessions = await shell.list_sessions(
            owner_id="group-umo",
            requester_id="admin-user",
            requester_is_admin=True,
        )
        assert [item["session_id"] for item in admin_sessions["sessions"]] == [
            session_id
        ]
        stopped = await shell.terminate_session(
            owner_id="group-umo",
            requester_id="admin-user",
            requester_is_admin=True,
            session_id=session_id,
        )
        assert stopped["status"] == "terminated"
    finally:
        await shell.shutdown_sessions()


@pytest.mark.asyncio
async def test_managed_shell_accepts_stdin_and_polls_incremental_output():
    shell = LocalShellComponent()
    result = await shell.exec_managed(
        _python_command("value = input(); print(f'got:{value}', flush=True)"),
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=False,
        sandboxed=True,
        yield_time_ms=100,
    )

    try:
        assert result["status"] == "running"
        await shell.write_session(
            owner_id="owner-a",
            requester_id="user-a",
            requester_is_admin=False,
            session_id=result["session_id"],
            chars="hello\n",
        )
        completed = await shell.poll_session(
            owner_id="owner-a",
            requester_id="user-a",
            requester_is_admin=False,
            session_id=result["session_id"],
            yield_time_ms=5_000,
        )
        output = completed["stdout"]
        if completed["status"] == "running":
            completed = await shell.poll_session(
                owner_id="owner-a",
                requester_id="user-a",
                requester_is_admin=False,
                session_id=result["session_id"],
                yield_time_ms=5_000,
            )
            output += completed["stdout"]

        assert completed["status"] == "completed"
        assert output.splitlines() == ["got:hello"]
        assert completed["session_closed"] is True
    finally:
        await shell.shutdown_sessions()


@pytest.mark.asyncio
async def test_managed_shell_hard_timeout_terminates_session():
    shell = LocalShellComponent()
    result = await shell.exec_managed(
        _python_command("import time; time.sleep(30)"),
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=False,
        sandboxed=False,
        timeout=1,
        yield_time_ms=0,
    )

    try:
        timed_out = await shell.poll_session(
            owner_id="owner-a",
            requester_id="user-a",
            requester_is_admin=False,
            session_id=result["session_id"],
            yield_time_ms=3_000,
        )

        assert timed_out["status"] == "timed_out"
        assert timed_out["exit_code"] is not None
        assert timed_out["session_closed"] is True
    finally:
        await shell.shutdown_sessions()


@pytest.mark.asyncio
async def test_managed_shell_keeps_completed_session_until_output_is_drained():
    shell = LocalShellComponent()
    result = await shell.exec_managed(
        _python_command("print('x' * 25000)"),
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=False,
        sandboxed=False,
        yield_time_ms=5_000,
        max_output_chars=10_000,
    )

    try:
        assert result["status"] == "completed"
        assert result["has_more"] is True
        output = result["stdout"]
        while result["has_more"]:
            result = await shell.poll_session(
                owner_id="owner-a",
                requester_id="user-a",
                requester_is_admin=False,
                session_id=result["session_id"],
                max_output_chars=10_000,
            )
            output += result["stdout"]

        assert output.splitlines() == ["x" * 25000]
        assert result["session_closed"] is True
        assert await shell.list_sessions(
            owner_id="owner-a",
            requester_id="user-a",
            requester_is_admin=False,
        ) == {"sessions": []}
    finally:
        await shell.shutdown_sessions()


_HOST_PID = 4321


class _FakeProcess:
    """Minimal psutil process stand-in exposing the ``info`` mapping."""

    def __init__(self, pid: int, name: str, cmdline: tuple[str, ...] = ()) -> None:
        self.info = {"pid": pid, "name": name, "cmdline": list(cmdline)}


def _patch_process_table(monkeypatch, processes: list[_FakeProcess]) -> None:
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: list(processes))


_HOST_PROCESS = _FakeProcess(_HOST_PID, "python.exe", ("python.exe", "main.py"))
_UNRELATED_PROCESS = _FakeProcess(9001, "node.exe", ("node.exe", "server.js"))


@pytest.mark.parametrize(
    "command",
    [
        "kill {pid}",
        "kill -TERM {pid}",
        "kill -s SIGKILL {pid}",
        "kill -s TERM {pid}",
        "kill -n 9 {pid}",
        "kill -- {pid}",
        "k''ill {pid}",
        "/bin/kill -s SIGKILL {pid}",
        "bash -c 'kill -s SIGKILL {pid}'",
        "echo $(kill {pid})",
        "python -c 'import os,signal;os.kill({pid},signal.SIGKILL)'",
        "perl -e 'kill 9, {pid}'",
        "ruby -e 'Process.kill(\"KILL\", {pid})'",
        "kill -Name python",
        "gps python | kill",
        "Get-Process python | ForEach-Object {{ $_.Kill() }}",
    ],
)
def test_would_kill_self_blocks_host_terminating_commands(monkeypatch, command):
    monkeypatch.setattr(local_booter, "_self_pids", lambda: frozenset({_HOST_PID}))

    assert local_booter._would_kill_self(command.format(pid=_HOST_PID))


@pytest.mark.parametrize(
    "command",
    [
        "kill -l",
        "kill {pid}",
        "echo hello",
        "python -c 'print(1)'",
        "git log --grep=ready",
    ],
)
def test_would_kill_self_allows_unrelated_commands(monkeypatch, command):
    monkeypatch.setattr(local_booter, "_self_pids", lambda: frozenset({_HOST_PID}))

    assert not local_booter._would_kill_self(command.format(pid=_HOST_PID + 1))


@pytest.mark.parametrize(
    "command",
    [
        "taskkill /F /PID 9002",
        "taskkill /F /IM node.exe",
        "pkill -f node",
        "Stop-Process -Name node",
        "python -c 'import os;os.kill(9002, 9)'",
    ],
)
def test_would_kill_self_allows_kill_of_unrelated_process(monkeypatch, command):
    """Resolving the selector must let a kill aimed elsewhere through.

    These are the shapes an agent reaches for when it wants to stop a process
    that has nothing to do with AstrBot. Refusing them outright was the bug.
    """
    monkeypatch.setattr(local_booter, "_self_pids", lambda: frozenset({_HOST_PID}))
    _patch_process_table(monkeypatch, [_HOST_PROCESS, _UNRELATED_PROCESS])

    assert not local_booter._would_kill_self(command)


@pytest.mark.parametrize(
    "command",
    [
        "taskkill /F /IM python.exe",
        "pkill python",
        "Stop-Process -Name python",
        f"python -c 'import os;os.kill({_HOST_PID}, 9)'",
    ],
)
def test_would_kill_self_blocks_kill_that_would_reach_the_host(monkeypatch, command):
    """Loosening the name-based branch must not open the host-kill path."""
    monkeypatch.setattr(local_booter, "_self_pids", lambda: frozenset({_HOST_PID}))
    _patch_process_table(monkeypatch, [_HOST_PROCESS, _UNRELATED_PROCESS])

    assert local_booter._would_kill_self(command)


@pytest.mark.parametrize(
    "command",
    [
        "taskkill /F /T",
        "taskkill /F /IM",
        "pkill *",
    ],
)
def test_would_kill_self_refuses_name_based_kill_it_cannot_resolve(
    monkeypatch, command
):
    """No selector, a flag without a value, or a bad pattern: fail closed."""
    monkeypatch.setattr(local_booter, "_self_pids", lambda: frozenset({_HOST_PID}))
    _patch_process_table(monkeypatch, [_HOST_PROCESS, _UNRELATED_PROCESS])

    assert local_booter._would_kill_self(command)


def test_would_kill_self_refuses_when_process_table_is_unavailable(monkeypatch):
    monkeypatch.setattr(local_booter, "_self_pids", lambda: frozenset({_HOST_PID}))

    def _explode(*args, **kwargs):
        raise RuntimeError("process table unavailable")

    monkeypatch.setattr(psutil, "process_iter", _explode)

    assert local_booter._would_kill_self("taskkill /F /IM node.exe")


@pytest.mark.asyncio
async def test_exec_managed_blocks_host_terminating_command():
    shell = LocalShellComponent()

    with pytest.raises(PermissionError, match="own process"):
        await shell.exec_managed(
            f"kill -s SIGKILL {os.getpid()}",
            owner_id="owner-a",
            creator_id="user-a",
            creator_is_admin=False,
            sandboxed=False,
            yield_time_ms=0,
        )

    assert await shell.list_sessions(
        owner_id="owner-a",
        requester_id="user-a",
        requester_is_admin=False,
    ) == {"sessions": []}


@pytest.mark.asyncio
async def test_exec_managed_unresolved_kill_says_it_could_not_resolve():
    """A selector the guard cannot resolve must say so.

    It must not claim the caller aimed at AstrBot -- that mislabel is what made
    an ordinary kill look like an attack on the host.
    """
    shell = LocalShellComponent()

    with pytest.raises(PermissionError, match="could not resolve"):
        await shell.exec_managed(
            "taskkill /F /T",
            owner_id="owner-a",
            creator_id="user-a",
            creator_is_admin=False,
            sandboxed=False,
            yield_time_ms=0,
        )


@pytest.mark.asyncio
async def test_write_session_blocks_host_terminating_input():
    shell = LocalShellComponent()
    result = await shell.exec_managed(
        _python_command("import time; time.sleep(30)"),
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=False,
        sandboxed=False,
        yield_time_ms=0,
    )

    try:
        with pytest.raises(PermissionError, match="own process"):
            await shell.write_session(
                owner_id="owner-a",
                requester_id="user-a",
                requester_is_admin=False,
                session_id=result["session_id"],
                chars=f"os.kill({os.getpid()}, 9)\n",
            )
        assert shell._sessions[result["session_id"]].process.returncode is None
    finally:
        await shell.shutdown_sessions()


@pytest.mark.asyncio
async def test_managed_shell_uses_git_bash_when_available(monkeypatch, tmp_path):
    calls = []

    class FakeStdout:
        def __init__(self):
            self.chunks = [b"done\n", b""]

        async def read(self, _size):
            return self.chunks.pop(0)

    class FakeProcess:
        pid = 4242
        returncode = 0
        stdin = None

        def __init__(self):
            self.stdout = FakeStdout()

        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(local_booter, "_find_git_bash", lambda: r"D:\Git\bin\bash.exe")
    monkeypatch.setattr(
        local_booter.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    result = await LocalShellComponent().exec_managed(
        "ls -la",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        cwd=str(tmp_path),
        yield_time_ms=5_000,
    )

    assert result["status"] == "completed"
    assert calls[0][0] == (r"D:\Git\bin\bash.exe", "-c", "ls -la")


def test_exec_uses_git_bash_argv_when_available(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakePopen(stdout=b"")

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    monkeypatch.setattr(local_booter.sys, "platform", "win32")
    monkeypatch.setattr(local_booter, "_find_git_bash", lambda: r"D:\Git\bin\bash.exe")

    result = asyncio.run(LocalShellComponent().exec("ls -la"))

    assert result["exit_code"] == 0
    assert calls[0][0][0] == [r"D:\Git\bin\bash.exe", "-c", "ls -la"]
    assert calls[0][1]["shell"] is False
    # CREATE_NO_WINDOW drops the console, which would make CTRL_BREAK_EVENT
    # undeliverable and leave MSYS2 background jobs unsweepable.
    assert calls[0][1]["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    assert not (calls[0][1]["creationflags"] & subprocess.CREATE_NO_WINDOW)


def test_decode_shell_output_survives_mixed_encoding():
    """UTF-8 and GBK bytes in one buffer must not raise."""
    mixed = "caf\u00e9".encode() + "\u6d4b\u8bd5".encode("gbk")

    assert isinstance(local_booter._decode_shell_output(mixed), str)


@pytest.mark.asyncio
async def test_terminate_process_signals_git_bash_before_taskkill(
    monkeypatch, tmp_path
):
    signals = []
    taskkills = []

    class FakeProcess:
        pid = 9001
        returncode = None

        def send_signal(self, sig):
            signals.append(sig)

        def terminate(self):
            pass

    (tmp_path / "out.log").touch()
    session = local_booter._LocalShellSession(
        session_id="sh_test",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=FakeProcess(),
        output_path=tmp_path / "out.log",
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
        shell_family="git_bash",
    )

    def fake_taskkill(*args, **kwargs):
        taskkills.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(local_booter.subprocess, "run", fake_taskkill)

    await LocalShellComponent()._terminate_process(session)

    assert signal.CTRL_BREAK_EVENT in signals
    assert taskkills, "taskkill must still run as the fallback sweep"
    if os.name == "nt":
        # The sweep is a CUI child of a console-less parent under pythonw.exe;
        # without this flag Windows flashes a visible console for it.
        assert taskkills[0][1]["creationflags"] == subprocess.CREATE_NO_WINDOW
    session.wait_task.cancel()


@pytest.mark.asyncio
async def test_terminate_process_skips_signal_for_powershell(monkeypatch, tmp_path):
    signals = []

    class FakeProcess:
        pid = 9002
        returncode = None

        def send_signal(self, sig):
            signals.append(sig)

        def terminate(self):
            pass

    (tmp_path / "out2.log").touch()
    session = local_booter._LocalShellSession(
        session_id="sh_test2",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=FakeProcess(),
        output_path=tmp_path / "out2.log",
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
        shell_family="powershell",
    )
    monkeypatch.setattr(
        local_booter.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=0),
    )

    await LocalShellComponent()._terminate_process(session)

    assert signal.CTRL_BREAK_EVENT not in signals
    session.wait_task.cancel()


@pytest.mark.asyncio
async def test_terminate_process_tolerates_already_exited_git_bash(
    monkeypatch, tmp_path
):
    """The signal already ended the process, so the sweep must not re-terminate it.

    Reproduces the real failure: CTRL_BREAK_EVENT ends the process, the
    taskkill sweep then reports failure because the PID is gone, and
    terminating an exited process raises ProcessLookupError.
    """

    class ExitingProcess:
        pid = 9003

        def __init__(self):
            self.returncode = None

        def send_signal(self, sig):
            _ = sig
            self.returncode = 0  # the signal ends the process

        def terminate(self):
            raise ProcessLookupError

        def kill(self):
            raise ProcessLookupError

    (tmp_path / "out3.log").touch()
    session = local_booter._LocalShellSession(
        session_id="sh_test3",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=ExitingProcess(),
        output_path=tmp_path / "out3.log",
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
        shell_family="git_bash",
    )
    monkeypatch.setattr(
        local_booter.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=1),
    )

    await LocalShellComponent()._terminate_process(session)

    session.wait_task.cancel()


@pytest.mark.asyncio
async def test_shutdown_sessions_keeps_sessions_of_the_configured_family(
    monkeypatch, tmp_path
):
    """Switching the local shell must not kill sessions already using it.

    After a config save the dashboard invalidates stale sessions: those started
    with the previously configured shell are removed, while sessions that
    already run the newly configured one keep going.
    """
    shell = LocalShellComponent()
    terminated = []

    async def fake_terminate(session):
        terminated.append(session.session_id)

    monkeypatch.setattr(shell, "_terminate_process", fake_terminate)

    out_dir = tmp_path / "shell"
    out_dir.mkdir()
    for session_id, family in (("sh_bash", "git_bash"), ("sh_ps", "powershell")):
        output_path = out_dir / f"{session_id}.log"
        output_path.touch()
        shell._sessions[session_id] = local_booter._LocalShellSession(
            session_id=session_id,
            owner_id="umo",
            creator_id="user",
            creator_is_admin=True,
            sandboxed=False,
            process=object(),
            output_path=output_path,
            started_at=0.0,
            output_event=asyncio.Event(),
            reader_task=asyncio.create_task(asyncio.sleep(0)),
            wait_task=asyncio.create_task(asyncio.sleep(0)),
            shell_family=family,
        )

    await shell.shutdown_sessions(keep_family="powershell")

    assert terminated == ["sh_bash"]
    assert set(shell._sessions) == {"sh_ps"}


@pytest.mark.asyncio
async def test_bounded_await_returns_true_when_task_completes():
    """A task that finishes within the timeout is awaited normally."""
    task = asyncio.create_task(asyncio.sleep(0))
    done = await local_booter._bounded_await(task, timeout=1.0)
    assert done is True
    assert task.done()


@pytest.mark.asyncio
async def test_bounded_await_returns_false_on_timeout_and_cancels():
    """A stuck task times out and is cancelled when cancel_on_timeout=True."""

    async def never():
        await asyncio.Event().wait()

    task = asyncio.create_task(never())
    done = await local_booter._bounded_await(task, timeout=0.05, cancel_on_timeout=True)
    assert done is False
    assert task.done()


@pytest.mark.asyncio
async def test_bounded_await_timeout_without_cancel_keeps_task_running():
    """With cancel_on_timeout=False the task keeps running after the timeout."""

    async def never():
        await asyncio.Event().wait()

    task = asyncio.create_task(never())
    done = await local_booter._bounded_await(task, timeout=0.05)
    assert done is False
    assert not task.done()
    task.cancel()


@pytest.mark.asyncio
async def test_poll_session_returns_when_reader_task_stuck(monkeypatch, tmp_path):
    """A pipe held open by a detached child must not hang poll forever.

    Reproduces the git bash hang: the shell process exited (returncode set,
    wait_task done) but a surviving MSYS2 grandchild keeps the stdout pipe
    open, so the reader task never finishes. Poll must return with the
    last-known output instead of awaiting the reader indefinitely.
    """
    import time

    class ExitedProcess:
        pid = 9010
        returncode = 1

    async def never():
        await asyncio.Event().wait()

    (tmp_path / "out_stuck.log").write_bytes(b"partial output\n")
    session = local_booter._LocalShellSession(
        session_id="sh_stuck_reader",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=ExitedProcess(),
        output_path=tmp_path / "out_stuck.log",
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(never()),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
        shell_family="git_bash",
    )
    shell = LocalShellComponent()
    shell._sessions[session.session_id] = session

    start = time.monotonic()
    result = await asyncio.wait_for(
        shell.poll_session(
            owner_id="umo",
            requester_id="user",
            requester_is_admin=True,
            session_id=session.session_id,
            yield_time_ms=0,
        ),
        timeout=15,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 10
    assert result["stdout"] == "partial output\n"
    assert result["exit_code"] == 1


@pytest.mark.asyncio
async def test_terminate_process_returns_when_wait_task_stuck(monkeypatch, tmp_path):
    """A process that survives kill() must not hang terminate forever.

    Reproduces the tail of _terminate_process: taskkill and terminate() both
    fail to end the process, kill() runs, and the final wait never settles.
    Terminate must give up with a warning instead of awaiting indefinitely.
    """
    import time

    class StubbornProcess:
        pid = 9011
        returncode = None

        def terminate(self):
            pass

        def kill(self):
            pass

    async def never():
        await asyncio.Event().wait()

    (tmp_path / "out4.log").touch()
    session = local_booter._LocalShellSession(
        session_id="sh_test4",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=StubbornProcess(),
        output_path=tmp_path / "out4.log",
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=asyncio.create_task(never()),
        shell_family="powershell",
    )
    monkeypatch.setattr(
        local_booter.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=1),
    )

    start = time.monotonic()
    await asyncio.wait_for(
        LocalShellComponent()._terminate_process(session),
        timeout=20,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 15
    session.wait_task.cancel()


@pytest.mark.asyncio
async def test_interrupt_tolerates_undeliverable_ctrl_break(monkeypatch, tmp_path):
    """CTRL_BREAK under pythonw (no console) must not surface as an error.

    Reproduces the live failure: send_signal(CTRL_BREAK_EVENT) makes CPython's
    os.kill raise SystemError ("returned a result with an exception set")
    because the child has no console. Interrupt must swallow the signal
    failure, attempt the graceful taskkill fallback, and still return a
    normal poll result.
    """

    class NoConsoleProcess:
        pid = 9012
        returncode = None

        def send_signal(self, sig):
            _ = sig
            raise SystemError(
                "<built-in function kill> returned a result with an exception set"
            )

    (tmp_path / "out5.log").touch()
    session = local_booter._LocalShellSession(
        session_id="sh_test5",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=NoConsoleProcess(),
        output_path=tmp_path / "out5.log",
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
        shell_family="powershell",
    )
    taskkills = []

    def fake_taskkill(*args, **kwargs):
        taskkills.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(local_booter.subprocess, "run", fake_taskkill)
    shell = LocalShellComponent()
    shell._sessions[session.session_id] = session

    result = await shell.interrupt_session(
        owner_id="umo",
        requester_id="user",
        requester_is_admin=True,
        session_id=session.session_id,
        yield_time_ms=0,
    )

    assert result["status"] == "running"
    assert taskkills, "the graceful taskkill fallback must still run"
    if os.name == "nt":
        # Same windowless requirement as _terminate_process: the fallback
        # sweep must not flash a console window under pythonw.exe.
        assert taskkills[0][1]["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.asyncio
async def test_terminate_git_bash_tolerates_signal_system_error(monkeypatch, tmp_path):
    """The git bash CTRL_BREAK branch must fall through to taskkill on error.

    Same console-less failure as interrupt: the signal raises SystemError,
    which is not OSError, so the narrow catch lets it escape. The sweep must
    still run and the terminate call must still return.
    """

    class NoConsoleGitBashProcess:
        pid = 9013

        def __init__(self):
            self.returncode = None

        def send_signal(self, sig):
            _ = sig
            raise SystemError(
                "<built-in method get_loop of _asyncio.Task object at 0x0> "
                "returned a result with an exception set"
            )

        def terminate(self):
            self.returncode = 1

        def kill(self):
            self.returncode = 1

    (tmp_path / "out6.log").touch()
    session = local_booter._LocalShellSession(
        session_id="sh_test6",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=NoConsoleGitBashProcess(),
        output_path=tmp_path / "out6.log",
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
        shell_family="git_bash",
    )
    monkeypatch.setattr(
        local_booter.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=1),
    )

    await asyncio.wait_for(
        LocalShellComponent()._terminate_process(session),
        timeout=20,
    )


@pytest.mark.asyncio
async def test_shutdown_sessions_returns_when_reader_stuck(monkeypatch, tmp_path):
    """Shutdown must not block on a reader whose pipe never reaches EOF."""
    import time

    class ExitedProcess:
        pid = 9014
        returncode = 1

    async def never():
        await asyncio.Event().wait()

    (tmp_path / "out7.log").touch()
    session = local_booter._LocalShellSession(
        session_id="sh_test7",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=ExitedProcess(),
        output_path=tmp_path / "out7.log",
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(never()),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
        shell_family="powershell",
    )
    shell = LocalShellComponent()
    shell._sessions[session.session_id] = session

    start = time.monotonic()
    await asyncio.wait_for(shell.shutdown_sessions(), timeout=20)
    elapsed = time.monotonic() - start

    assert elapsed < 15
    assert session.session_id not in shell._sessions


@pytest.mark.asyncio
async def test_bounded_await_swallows_task_exception():
    """A task that failed is finished; its exception must not escape.

    A reader task can die with transport-level errors (broken pipe after the
    process is killed). Callers use _bounded_await to wait for completion,
    not to consume the task's result, so the failure must not propagate.
    """

    async def boom():
        raise RuntimeError("pipe broke")

    task = asyncio.create_task(boom())
    await asyncio.sleep(0)  # let the task fail
    done = await local_booter._bounded_await(task, timeout=1.0)
    assert done is True
    assert task.done()


@pytest.mark.asyncio
async def test_remove_session_tolerates_locked_output_file(tmp_path):
    """Windows refuses to unlink a file a stuck reader still holds open.

    Reproduces the live failure: a detached MSYS2 grandchild keeps the pipe
    open, the reader task never finishes, and _remove_session raised
    PermissionError [WinError 32] out of terminate/poll.
    """

    class ExitedProcess:
        pid = 9015
        returncode = 1

    output_path = tmp_path / "out_locked.log"
    output_path.touch()
    session = local_booter._LocalShellSession(
        session_id="sh_locked",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=ExitedProcess(),
        output_path=output_path,
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
        shell_family="git_bash",
    )
    shell = LocalShellComponent()
    shell._sessions[session.session_id] = session

    with output_path.open("ab"):  # hold the file open like a stuck reader
        await shell._remove_session(session)

    assert session.session_id not in shell._sessions


@pytest.mark.asyncio
async def test_remove_session_cancels_stuck_reader(tmp_path):
    """A never-ending reader must be cancelled so its file handle is freed."""

    class ExitedProcess:
        pid = 9016
        returncode = 1

    async def never():
        await asyncio.Event().wait()

    output_path = tmp_path / "out_stuck2.log"
    output_path.touch()
    session = local_booter._LocalShellSession(
        session_id="sh_stuck2",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=ExitedProcess(),
        output_path=output_path,
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(never()),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
        shell_family="git_bash",
    )
    shell = LocalShellComponent()
    shell._sessions[session.session_id] = session

    await asyncio.wait_for(shell._remove_session(session), timeout=10)

    assert session.reader_task.done()
    assert not output_path.exists()


@pytest.mark.asyncio
async def test_terminate_tolerates_failed_wait_task(monkeypatch, tmp_path):
    """A wait_task that died with a transport error must not break terminate.

    Under pythonw.exe, killing the process can complete wait_task with
    OSError (invalid handle). Awaiting or shielding it then re-raises that
    error (surfaced live as a CPython SystemError) out of terminate.
    """

    class KillableProcess:
        pid = 9017

        def __init__(self):
            self.returncode = None

        def send_signal(self, sig):
            _ = sig
            raise SystemError("returned a result with an exception set")

        def terminate(self):
            self.returncode = 1

        def kill(self):
            self.returncode = 1

    async def broken_wait():
        raise OSError(6, "The handle is invalid")

    wait_task = asyncio.create_task(broken_wait())
    await asyncio.sleep(0)  # let the wait task fail before terminate runs

    (tmp_path / "out8.log").touch()
    session = local_booter._LocalShellSession(
        session_id="sh_test8",
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=KillableProcess(),
        output_path=tmp_path / "out8.log",
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=wait_task,
        shell_family="git_bash",
    )
    monkeypatch.setattr(
        local_booter.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=0),
    )

    await asyncio.wait_for(
        LocalShellComponent()._terminate_process(session),
        timeout=20,
    )


class _EmptyStdout:
    async def read(self, _limit: int) -> bytes:
        return b""


class _ControllableProcess:
    """Fake managed process whose exit the test triggers explicitly."""

    def __init__(self) -> None:
        self.pid = 12345
        self.returncode: int | None = None
        self.stdout = _EmptyStdout()
        self.stdin = None
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        return self.returncode or 0

    def exit(self, code: int = 0) -> None:
        self.returncode = code
        self._exited.set()


def _patch_managed_spawn(monkeypatch, holder: dict) -> None:
    """Route managed subprocess creation to a controllable fake process."""

    async def fake_spawn(*args, **kwargs):
        _ = args, kwargs
        proc = _ControllableProcess()
        holder["proc"] = proc
        return proc

    monkeypatch.setattr(local_booter.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(local_booter.asyncio, "create_subprocess_shell", fake_spawn)


async def _drain_notifications(notifications: list, count: int) -> None:
    """Let the event loop run until enough change notifications arrived."""
    for _ in range(100):
        await asyncio.sleep(0.01)
        if len(notifications) >= count:
            return


@pytest.mark.asyncio
async def test_change_listener_fires_on_session_create(monkeypatch, tmp_path):
    holder: dict = {}
    _patch_managed_spawn(monkeypatch, holder)
    shell = LocalShellComponent()
    notifications: list[str] = []
    shell.add_change_listener(notifications.append)

    result = await shell.exec_managed(
        "dummy",
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=True,
        sandboxed=False,
        cwd=str(tmp_path),
        yield_time_ms=50,
    )

    assert result["status"] == "running"
    assert notifications == ["owner-a"]


@pytest.mark.asyncio
async def test_change_listener_fires_on_process_exit(monkeypatch, tmp_path):
    holder: dict = {}
    _patch_managed_spawn(monkeypatch, holder)
    shell = LocalShellComponent()
    notifications: list[str] = []
    shell.add_change_listener(notifications.append)

    await shell.exec_managed(
        "dummy",
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=True,
        sandboxed=False,
        cwd=str(tmp_path),
        yield_time_ms=50,
    )
    assert notifications == ["owner-a"]

    holder["proc"].exit(0)
    await _drain_notifications(notifications, 2)

    # wait_task's done callback must schedule the notification without
    # blocking or hanging the loop.
    assert notifications == ["owner-a", "owner-a"]


@pytest.mark.asyncio
async def test_change_listener_fires_on_session_removal(monkeypatch, tmp_path):
    holder: dict = {}
    _patch_managed_spawn(monkeypatch, holder)
    shell = LocalShellComponent()
    notifications: list[str] = []
    shell.add_change_listener(notifications.append)

    started = await shell.exec_managed(
        "dummy",
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=True,
        sandboxed=False,
        cwd=str(tmp_path),
        yield_time_ms=50,
    )
    holder["proc"].exit(0)
    await _drain_notifications(notifications, 2)

    result = await shell.poll_session(
        owner_id="owner-a",
        requester_id="user-a",
        requester_is_admin=True,
        session_id=started["session_id"],
        yield_time_ms=0,
    )

    assert result["session_closed"] is True
    # create + exit + removal
    assert notifications == ["owner-a", "owner-a", "owner-a"]


@pytest.mark.asyncio
async def test_change_listener_exception_does_not_break_shell_ops(
    monkeypatch, tmp_path
):
    holder: dict = {}
    _patch_managed_spawn(monkeypatch, holder)
    shell = LocalShellComponent()
    notifications: list[str] = []

    def boom(_owner_id: str) -> None:
        raise RuntimeError("listener exploded")

    shell.add_change_listener(boom)
    shell.add_change_listener(notifications.append)

    started = await shell.exec_managed(
        "dummy",
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=True,
        sandboxed=False,
        cwd=str(tmp_path),
        yield_time_ms=50,
    )
    assert started["status"] == "running"

    holder["proc"].exit(0)
    result = await shell.poll_session(
        owner_id="owner-a",
        requester_id="user-a",
        requester_is_admin=True,
        session_id=started["session_id"],
        yield_time_ms=0,
    )

    # A raising listener must neither break shell operations nor prevent
    # later listeners from running.
    assert result["session_closed"] is True
    assert notifications


class _QueueStdout:
    """Fake stdout whose chunks the test feeds on demand (blocks until fed)."""

    def __init__(self) -> None:
        self.chunks: asyncio.Queue[bytes] = asyncio.Queue()

    async def read(self, _limit: int) -> bytes:
        return await self.chunks.get()

    def feed(self, data: bytes) -> None:
        self.chunks.put_nowait(data)

    def close(self) -> None:
        self.chunks.put_nowait(b"")


class _OutputProcess:
    """Controllable fake process with feedable stdout and explicit exit."""

    def __init__(self) -> None:
        self.pid = 12345
        self.returncode: int | None = None
        self.stdout = _QueueStdout()
        self.stdin = None
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        return self.returncode or 0

    def exit(self, code: int = 0) -> None:
        self.returncode = code
        self._exited.set()


def _patch_output_spawn(monkeypatch, holder: dict) -> None:
    """Route managed subprocess creation to an output-feedable fake."""

    async def fake_spawn(*args, **kwargs):
        _ = args, kwargs
        proc = _OutputProcess()
        holder["proc"] = proc
        return proc

    monkeypatch.setattr(local_booter.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(local_booter.asyncio, "create_subprocess_shell", fake_spawn)


@pytest.mark.asyncio
async def test_peek_does_not_advance_session_cursor(monkeypatch, tmp_path):
    holder: dict = {}
    _patch_output_spawn(monkeypatch, holder)
    shell = LocalShellComponent()
    await shell.exec_managed(
        "dummy",
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=True,
        sandboxed=False,
        cwd=str(tmp_path),
        yield_time_ms=50,
    )
    session = next(iter(shell._sessions.values()))
    assert session.cursor == 0

    holder["proc"].stdout.feed(b"hello\n")
    await asyncio.sleep(0.05)  # let the reader flush the chunk to disk

    peeked = await shell.peek_session_output(
        owner_id="owner-a",
        requester_id="user-a",
        requester_is_admin=True,
        session_id=session.session_id,
        cursor=0,
    )
    assert peeked["stdout"] == "hello\n"
    # The agent's incremental cursor must be untouched by user viewing.
    assert session.cursor == 0

    polled = await shell.poll_session(
        owner_id="owner-a",
        requester_id="user-a",
        requester_is_admin=True,
        session_id=session.session_id,
        yield_time_ms=0,
    )
    assert polled["stdout"] == "hello\n"


@pytest.mark.asyncio
async def test_peek_reports_closed_but_keeps_session(monkeypatch, tmp_path):
    holder: dict = {}
    _patch_output_spawn(monkeypatch, holder)
    shell = LocalShellComponent()
    started = await shell.exec_managed(
        "dummy",
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=True,
        sandboxed=False,
        cwd=str(tmp_path),
        yield_time_ms=50,
    )
    holder["proc"].stdout.feed(b"done\n")
    await asyncio.sleep(0.05)
    holder["proc"].exit(0)
    holder["proc"].stdout.close()
    await asyncio.sleep(0.05)

    peeked = await shell.peek_session_output(
        owner_id="owner-a",
        requester_id="user-a",
        requester_is_admin=True,
        session_id=started["session_id"],
        cursor=0,
    )
    assert peeked["session_closed"] is True
    assert peeked["exit_code"] == 0
    # peek never reaps: removal stays poll's job.
    assert started["session_id"] in shell._sessions


@pytest.mark.asyncio
async def test_peek_enforces_ownership(monkeypatch, tmp_path):
    holder: dict = {}
    _patch_output_spawn(monkeypatch, holder)
    shell = LocalShellComponent()
    started = await shell.exec_managed(
        "dummy",
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=False,
        sandboxed=False,
        cwd=str(tmp_path),
        yield_time_ms=50,
    )
    with pytest.raises(ValueError):
        await shell.peek_session_output(
            owner_id="owner-b",
            requester_id="user-b",
            requester_is_admin=False,
            session_id=started["session_id"],
        )


@pytest.mark.asyncio
async def test_peek_yield_waits_for_new_output(monkeypatch, tmp_path):
    holder: dict = {}
    _patch_output_spawn(monkeypatch, holder)
    shell = LocalShellComponent()
    started = await shell.exec_managed(
        "dummy",
        owner_id="owner-a",
        creator_id="user-a",
        creator_is_admin=True,
        sandboxed=False,
        cwd=str(tmp_path),
        yield_time_ms=50,
    )

    async def feed_later():
        await asyncio.sleep(0.1)
        holder["proc"].stdout.feed(b"late\n")

    feeder = asyncio.create_task(feed_later())
    peeked = await shell.peek_session_output(
        owner_id="owner-a",
        requester_id="user-a",
        requester_is_admin=True,
        session_id=started["session_id"],
        cursor=0,
        yield_time_ms=2000,
    )
    await feeder
    assert peeked["stdout"] == "late\n"
    assert peeked["status"] == "running"
