from __future__ import annotations

import asyncio
import hashlib
import locale
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

if sys.version_info < (3, 14):
    from python_ripgrep import search

from astrbot.api import logger
from astrbot.core.computer.file_read_utils import (
    detect_text_encoding,
    read_local_text_range_sync,
)
from astrbot.core.utils.astrbot_path import (
    get_astrbot_root,
    get_astrbot_system_tmp_path,
)

from ..olayer import FileSystemComponent, PythonComponent, ShellComponent
from .base import ComputerBooter
from .shipyard_search_file_util import _truncate_long_lines

_BLOCKED_COMMAND_PATTERNS = [
    " rm -rf ",
    " rm -fr ",
    " rm -r ",
    " mkfs",
    " dd if=",
    " shutdown",
    " reboot",
    " poweroff",
    " halt",
    " sudo ",
    ":(){:|:&};:",
    " kill -9 ",
    " killall ",
]


# WHY ``_NO_WINDOW_KWARGS``:
#   pythonw.exe (GUI subsystem) 启动下,spawn 一个 CUI 子进程 (cmd.exe /
#   python.exe) 时 Windows 默认会为子进程新开一个控制台窗口 — 即用户报告的
#   "弹 cmd 黑框" 现象。``creationflags=CREATE_NO_WINDOW`` 是消除该窗口的
#   标准做法。
#
#   跨平台:
#     - win32: 返回 ``{"creationflags": subprocess.CREATE_NO_WINDOW}``
#     - 其他: 返回 ``{}`` (non-Windows 上 ``CREATE_NO_WINDOW`` 不存在)
#
#   Refs:
#     - subprocess.CREATE_NO_WINDOW 只在 win32 平台上有定义
#     - asyncio.create_subprocess_exec 同样支持 ``creationflags`` kwarg
_NO_WINDOW_KWARGS: dict[str, int] = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)


def _is_safe_command(command: str) -> bool:
    cmd = f" {command.strip().lower()} "
    return not any(pat in cmd for pat in _BLOCKED_COMMAND_PATTERNS)


def _self_pids() -> frozenset[int]:
    """Return the PIDs that must never be killed by the local shell tool.

    Includes the current AstrBot process, its parent, and the process group
    leader so that indirect kills (for example ``kill -9 -$PGID``) are also
    blocked. On Windows ``os.getpgrp`` is unavailable, so the set only
    contains the current process and its parent in that case.
    """
    pids: set[int] = {os.getpid(), os.getppid()}
    getpgrp = getattr(os, "getpgrp", None)
    if getpgrp is not None:
        try:
            pgid = getpgrp()
        except (OSError, AttributeError):
            pgid = 0
        if pgid and pgid > 0:
            pids.add(pgid)
    return frozenset(pids)


# Command names treated as kill invocations. Order matters: longer names must
# precede their prefixes (``killall5`` before ``killall`` before ``kill``).
# Everything except the bare ``kill`` selects its victim from the process list
# and is refused outright, because it cannot be checked against the protected
# PID set.
_KILL_COMMAND_NAMES = (
    "killall5",  # Unix (System V): kill every process
    "killall",  # Unix: kill by exact process name
    "taskkill",  # Windows: kill by PID / IM / window title
    "pkill",  # Unix: kill by pattern match
    "stop-process",  # PowerShell: kill by name or PID
    "spps",  # PowerShell alias of Stop-Process
    "pgrep",  # Unix: process lookup (often paired with pkill)
    "kill",  # Unix kill / PowerShell alias of Stop-Process
)

# A kill invocation, optionally reached through a path (``/bin/kill``) or a
# shell substitution (``$(kill 1234)``). ``args`` is the remainder of the
# command up to the next shell separator and is scanned for literal PIDs.
_KILL_INVOCATION_PATTERN = re.compile(
    r"(?:^|[\s;&|(){}<>$`\"'/=])"
    r"(?P<name>" + "|".join(_KILL_COMMAND_NAMES) + r")"
    r"(?:\.[a-z]+)?\b(?P<args>[^;&|\n]*)"
)

# Kill-style API calls such as ``os.kill(pid, 9)``, ``Process.kill("KILL", pid)``
# or PowerShell's ``$_.Kill()``. A shell command has no reason to spell a kill
# this way. The first argument is captured so a literal PID can be checked like
# any other target; anything else counts as unresolvable.
_KILL_API_CALL_PATTERN = re.compile(r"\bkill\s*\(\s*(?P<target>[^,)]*)")

# Refusal reasons returned by :func:`_would_kill_self`. There are two, because
# the two causes call for different corrections: a selector that resolves to the
# host has to be retargeted, while a selector that cannot be resolved has to be
# spelled in a form this guard understands. Collapsing them into one string
# would leave the model unable to tell which correction applies.
_HOST_KILL_REFUSAL = (
    "Blocked: this kill command would terminate AstrBot's own process. "
    "Retarget it at the unrelated process."
)

_UNRESOLVED_KILL_REFUSAL = (
    "Blocked: AstrBot could not resolve which processes this kill command "
    "would terminate. Name the target explicitly -- an image name, a process "
    "name, or a numeric PID."
)


def _would_kill_self(command: str) -> str | None:
    """Best-effort detection of commands that target the host AstrBot process.

    Complements the substring blacklist in :data:`_BLOCKED_COMMAND_PATTERNS`
    by inspecting the command for kill invocations that name a protected PID,
    or that select their victim by process name and would reach one. A
    name-based selector that resolves to unrelated processes is allowed;
    anything that cannot be resolved is refused.

    This is a speed bump, not a security boundary: the local shell runs with
    the same privileges as AstrBot, so indirections not modelled here (extra
    quoting, encodings, another interpreter, a script on disk) still get
    through. Only OS-level isolation gives a real guarantee. The sandbox
    runtime is unaffected because its commands run in an isolated container.

    Args:
        command: Shell command text about to run, or text about to be written
            to a managed shell session.

    Returns:
        The refusal reason, or None when the command is allowed. The reason is
        a non-empty string, so callers may also treat the result as a boolean.
    """
    lowered = command.lower()
    protected = _self_pids()

    for api_call in _KILL_API_CALL_PATTERN.finditer(lowered):
        target = api_call.group("target").strip()
        if not target.isdigit():
            return _UNRESOLVED_KILL_REFUSAL
        if int(target) in protected:
            return _HOST_KILL_REFUSAL

    # Dropping quotes defeats trivial concatenation such as ``k''ill 1234``
    # without changing the token boundaries the pattern relies on.
    flattened = lowered.replace("'", "").replace('"', "")

    for match in _KILL_INVOCATION_PATTERN.finditer(flattened):
        name = match.group("name")
        args = match.group("args")
        # A literal protected PID settles it, whichever flag introduced it.
        if any(int(pid) in protected for pid in re.findall(r"\d+", args)):
            return _HOST_KILL_REFUSAL
        if name == "kill":
            # A ``kill`` without a literal PID either selects by name
            # (``kill -Name python``) or leans on a pipeline
            # (``gps python | kill``); neither can be verified as safe.
            # ``kill -l`` only lists signal names, so it stays allowed.
            if not re.search(r"\d", args) and args.strip() not in {"-l", "--list"}:
                return _UNRESOLVED_KILL_REFUSAL
            continue
        # Name-based forms are refused only when the victim cannot be
        # established. Resolving the selector lets a kill aimed at something
        # unrelated through, while one that would reach the host still refuses.
        victims = _kill_victims(name, args)
        if victims is None:
            return _UNRESOLVED_KILL_REFUSAL
        if victims & protected:
            return _HOST_KILL_REFUSAL

    return None


def _kill_victims(program: str, args: str) -> set[int] | None:
    """Resolve the PIDs a name-selecting kill invocation would terminate.

    Name-based forms are not refused for being name-based -- they are refused
    when their victim cannot be established. Resolving the selector against the
    process table turns the ordinary case, stopping a process that has nothing
    to do with AstrBot, into an allow, while a selector that would reach the
    host still refuses.

    Selectors are matched as regular expressions: that is what ``pkill`` does,
    and it over-approximates for ``taskkill`` and ``Stop-Process``, which is the
    safe direction -- an over-wide match refuses more, never less.

    Args:
        program: Lowercased command name from the kill invocation pattern.
        args: Text after the command name, up to the next shell separator.

    Returns:
        The PIDs the invocation would terminate, or None when the victim cannot
        be established and the caller must refuse.
    """
    tokens = args.split()
    pids: set[int] = set()
    selectors: list[str] = []
    full_command_line = False

    if program == "taskkill":
        for index, token in enumerate(tokens[:-1]):
            flag = token.lstrip("/-").lower()
            value = tokens[index + 1].strip("\"'")
            if flag == "im":
                selectors.append(value)
            elif flag == "pid":
                if not value.isdigit():
                    return None
                pids.add(int(value))
    elif program in {"stop-process", "spps"}:
        for index, token in enumerate(tokens[:-1]):
            flag = token.lstrip("-").lower()
            value = tokens[index + 1].strip("\"'")
            if flag == "name":
                selectors.append(value)
            elif flag == "id":
                if not value.isdigit():
                    return None
                pids.add(int(value))
    elif program == "pkill":
        full_command_line = any(token in {"-f", "--full"} for token in tokens)
        operands = [token for token in tokens if not token.startswith("-")]
        if operands:
            selectors.append(operands[0].strip("\"'"))
    else:
        # killall5 kills every process, killall is already refused by the
        # substring blacklist, and pgrep selects no victim at all.
        return None

    if not selectors and not pids:
        return None

    victims = set(pids)
    if not selectors:
        return victims

    try:
        patterns = [re.compile(selector, re.IGNORECASE) for selector in selectors]
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            info = proc.info
            haystack = (
                " ".join(info.get("cmdline") or [])
                if full_command_line
                else info.get("name") or ""
            )
            if any(pattern.search(haystack) for pattern in patterns):
                victims.add(info["pid"])
    except Exception:
        return None
    return victims


_POWERSHELL_PREFIX_ARGS = (
    "-NoLogo",
    "-NoProfile",
    "-NonInteractive",
    "-Command",
)


@dataclass(frozen=True)
class ShellSpec:
    """Resolved local shell: family tag, executable, and fixed argv prefix.

    Attributes:
        family: One of "git_bash", "pwsh", "powershell", "cmd", "posix".
        executable: Absolute or PATH-resolved executable path. Empty for the
            "posix" family, which runs through the platform shell instead of
            an explicit argv.
        prefix_args: Arguments placed before the command text.
    """

    family: str
    executable: str
    prefix_args: tuple[str, ...]


def _find_git_bash() -> str | None:
    """Locate Git for Windows' bash.exe without trusting a PATH ``bash``.

    ``shutil.which("bash")`` is deliberately avoided: on Windows it usually
    resolves to the WSL launcher stub under WindowsApps, which is a different
    filesystem and shell entirely. The Git installation is located through the
    ``git`` on PATH instead, with the usual install roots as a fallback.

    Returns:
        Absolute path to bash.exe, or None when Git for Windows is absent.
    """
    git = shutil.which("git")
    if git:
        # `git.exe` sits in <root>\cmd, <root>\bin or <root>\mingw64\bin
        # depending on PATH order, so search the ancestors rather than
        # assuming a fixed depth. The drive root is skipped so an unrelated
        # C:\bin\bash.exe cannot be picked up.
        for ancestor in Path(git).resolve().parents:
            if ancestor.parent == ancestor:
                break
            for relative in ("bin/bash.exe", "usr/bin/bash.exe"):
                candidate = ancestor / relative
                if candidate.is_file():
                    return str(candidate)

    for env_key, subdir in (
        ("ProgramFiles", "Git"),
        ("ProgramFiles(x86)", "Git"),
        ("LOCALAPPDATA", "Programs/Git"),
    ):
        base = os.environ.get(env_key)
        if not base:
            continue
        candidate = Path(base) / subdir / "bin/bash.exe"
        if candidate.is_file():
            return str(candidate)

    return None


def resolve_local_shell(shell_type: str = "auto") -> ShellSpec:
    """Resolve which local shell commands should run in.

    On Windows, "auto" prefers Git Bash, then PowerShell 7, then Windows
    PowerShell. An explicit family that is not installed falls back to "auto",
    so a stale config value cannot disable shell execution.

    Args:
        shell_type: "auto" or one of "git_bash", "pwsh", "powershell", "cmd".
            Ignored outside Windows.

    Returns:
        The resolved shell specification.
    """
    if sys.platform != "win32":
        return ShellSpec(family="posix", executable="", prefix_args=())

    if shell_type == "git_bash" and (git_bash := _find_git_bash()):
        return ShellSpec("git_bash", git_bash, ("-c",))
    if shell_type == "pwsh" and shutil.which("pwsh"):
        return ShellSpec("pwsh", "pwsh.exe", _POWERSHELL_PREFIX_ARGS)
    if shell_type == "powershell":
        return ShellSpec("powershell", "powershell.exe", _POWERSHELL_PREFIX_ARGS)
    if shell_type == "cmd" and shutil.which("cmd"):
        return ShellSpec("cmd", "cmd.exe", ("/d", "/s", "/c"))

    if shell_type not in ("auto", "git_bash", "pwsh", "powershell", "cmd"):
        logger.warning(
            "Unknown local shell %r; falling back to auto-detection.", shell_type
        )
    elif shell_type != "auto":
        logger.warning(
            "Configured local shell %r is unavailable; falling back to auto-detection.",
            shell_type,
        )

    if git_bash := _find_git_bash():
        return ShellSpec("git_bash", git_bash, ("-c",))
    if shutil.which("pwsh"):
        return ShellSpec("pwsh", "pwsh.exe", _POWERSHELL_PREFIX_ARGS)
    return ShellSpec("powershell", "powershell.exe", _POWERSHELL_PREFIX_ARGS)


def _decode_bytes_with_fallback(
    output: bytes | None,
    *,
    preferred_encoding: str | None = None,
) -> str:
    if output is None:
        return ""

    preferred = locale.getpreferredencoding(False) or "utf-8"
    attempted_encodings: list[str] = []

    def _try_decode(encoding: str) -> str | None:
        normalized = encoding.lower()
        if normalized in attempted_encodings:
            return None
        attempted_encodings.append(normalized)
        try:
            return output.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            return None

    for encoding in filter(None, [preferred_encoding, "utf-8", "utf-8-sig"]):
        if decoded := _try_decode(encoding):
            return decoded

    if os.name == "nt":
        for encoding in ("mbcs", "cp936", "gbk", "gb18030", preferred):
            if decoded := _try_decode(encoding):
                return decoded
    elif decoded := _try_decode(preferred):
        return decoded

    return output.decode("utf-8", errors="replace")


def _decode_shell_output(output: bytes | None) -> str:
    return _decode_bytes_with_fallback(output, preferred_encoding="utf-8")


async def _bounded_await(
    task: asyncio.Task,
    timeout: float,
    *,
    cancel_on_timeout: bool = False,
) -> bool:
    """Await a task with a hard timeout, never blocking indefinitely.

    Args:
        task: The asyncio task to await.
        timeout: Maximum seconds to wait for completion.
        cancel_on_timeout: Whether to cancel the task when the timeout
            expires. Use True when the task is disposable (e.g. an output
            reader that can be abandoned).

    Returns:
        True if the task completed within the timeout, False otherwise.
    """
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        if cancel_on_timeout:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        return False


@dataclass
class _LocalShellSession:
    """Runtime state for one managed local shell process."""

    session_id: str
    owner_id: str
    creator_id: str
    creator_is_admin: bool
    sandboxed: bool
    process: asyncio.subprocess.Process
    output_path: Path
    started_at: float
    output_event: asyncio.Event
    reader_task: asyncio.Task[None]
    wait_task: asyncio.Task[int]
    shell_family: str
    timeout_task: asyncio.Task[None] | None = None
    cursor: int = 0
    timed_out: bool = False
    terminated: bool = False


@dataclass
class LocalShellComponent(ShellComponent):
    _sessions: dict[str, _LocalShellSession] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _sessions_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = 300,
        shell: bool = True,
        background: bool = False,
        shell_spec: ShellSpec | None = None,
    ) -> dict[str, Any]:
        if not _is_safe_command(command):
            raise PermissionError("Blocked unsafe shell command.")
        if refusal := _would_kill_self(command):
            raise PermissionError(refusal)

        def _run() -> dict[str, Any]:
            run_env = os.environ.copy()
            if env:
                run_env.update({str(k): str(v) for k, v in env.items()})
            working_dir = os.path.abspath(cwd) if cwd else get_astrbot_root()
            popen_command: str | list[str] = command
            popen_shell = shell
            spawn_kwargs: dict[str, Any] = dict(_NO_WINDOW_KWARGS)
            spec: ShellSpec | None = None
            if sys.platform == "win32" and shell:
                spec = shell_spec or resolve_local_shell()
                popen_command = [spec.executable, *spec.prefix_args, command]
                popen_shell = False
                if spec.family == "git_bash":
                    # CREATE_NO_WINDOW drops the console entirely, and a
                    # process without one never receives CTRL_BREAK_EVENT —
                    # the only reliable sweep for MSYS2 background jobs. See
                    # `_terminate_process`.
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 0  # SW_HIDE
                    spawn_kwargs = {
                        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP,
                        "startupinfo": startupinfo,
                    }
            if background:
                # Safety relies on `_is_safe_command()`.
                proc = subprocess.Popen(  # noqa: S602  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
                    popen_command,
                    shell=popen_shell,
                    cwd=working_dir,
                    env=run_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **spawn_kwargs,
                )
                return {"pid": proc.pid, "stdout": "", "stderr": "", "exit_code": None}
            # Safety relies on `_is_safe_command()`.
            proc = subprocess.Popen(  # noqa: S602  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
                popen_command,
                shell=popen_shell,
                cwd=working_dir,
                env=run_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **spawn_kwargs,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout or 300)
            except subprocess.TimeoutExpired:
                should_kill_parent = sys.platform != "win32"
                if spec is not None and spec.family == "git_bash":
                    # Same MSYS2 leak as `_terminate_process`: background jobs
                    # detach from the Windows parent-PID chain, so signal the
                    # process group before falling back to the taskkill sweep.
                    try:
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                if sys.platform == "win32":
                    try:
                        taskkill_result = subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                            **_NO_WINDOW_KWARGS,
                        )
                        should_kill_parent = taskkill_result.returncode != 0
                    except Exception:
                        should_kill_parent = True
                if should_kill_parent:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                raise
            return {
                "stdout": _decode_shell_output(stdout),
                "stderr": _decode_shell_output(stderr),
                "exit_code": proc.returncode,
            }

        return await asyncio.to_thread(_run)

    async def exec_managed(
        self,
        command: str,
        *,
        owner_id: str,
        creator_id: str,
        creator_is_admin: bool,
        sandboxed: bool,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        yield_time_ms: int = 10_000,
        max_output_chars: int = 10_000,
        shell_spec: ShellSpec | None = None,
    ) -> dict[str, Any]:
        """Start a locally managed shell process and briefly wait for it.

        Args:
            command: Shell command to execute.
            owner_id: Unified message origin containing the process.
            creator_id: Sender ID that created the session.
            creator_is_admin: Whether the creator was an administrator.
            sandboxed: Whether the process is isolated from the host.
            cwd: Working directory for the process.
            env: Additional environment variables.
            timeout: Hard process lifetime in seconds. None disables it.
            yield_time_ms: Maximum time to wait before returning a session ID.
            max_output_chars: Maximum output bytes returned in this call.

        Returns:
            Process result with output, status, and session metadata.

        Raises:
            PermissionError: If the command matches a blocked pattern or is a
                kill command that cannot be verified as safe.
            ValueError: If a timing or output limit is invalid.
        """
        if not _is_safe_command(command):
            raise PermissionError("Blocked unsafe shell command.")
        if refusal := _would_kill_self(command):
            raise PermissionError(refusal)
        if yield_time_ms < 0 or yield_time_ms > 120_000:
            raise ValueError("`yield_time_ms` must be between 0 and 120000.")
        if timeout is not None and timeout <= 0:
            raise ValueError("`timeout` must be greater than 0 when provided.")
        if max_output_chars < 1:
            raise ValueError("`max_output_chars` must be greater than 0.")

        run_env = os.environ.copy()
        if env:
            run_env.update({str(k): str(v) for k, v in env.items()})
        working_dir = Path(cwd).resolve() if cwd else Path(get_astrbot_root()).resolve()
        session_id = f"sh_{uuid.uuid4().hex[:16]}"
        owner_digest = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:16]
        output_dir = Path(get_astrbot_system_tmp_path()) / "shell" / owner_digest
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{session_id}.log"
        output_path.touch()

        process_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            # CREATE_NEW_PROCESS_GROUP keeps CTRL_BREAK_EVENT interrupt
            # (see interrupt_session) working by giving the child its own
            # process group attached to a console. CREATE_NO_WINDOW cannot be
            # used here because it drops the console entirely and breaks that
            # interrupt path. Instead, hide the console window via STARTUPINFO
            # so spawning the shell under pythonw.exe (GUI subsystem) does not
            # flash a visible console window.
            process_kwargs["creationflags"] = getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            process_kwargs["startupinfo"] = startupinfo
        else:
            process_kwargs["start_new_session"] = True

        spec = shell_spec or resolve_local_shell()

        try:
            if sys.platform == "win32":
                process_factory = asyncio.create_subprocess_exec
                process_args = (spec.executable, *spec.prefix_args, command)
            else:
                process_factory = asyncio.create_subprocess_shell
                process_args = (command,)
            process = await process_factory(
                *process_args,
                cwd=working_dir,
                env=run_env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **process_kwargs,
            )
        except Exception:
            output_path.unlink(missing_ok=True)
            raise

        output_event = asyncio.Event()

        async def _capture_output() -> None:
            if process.stdout is None:
                return
            with output_path.open("ab") as output_file:
                while chunk := await process.stdout.read(8192):
                    output_file.write(chunk)
                    output_file.flush()
                    output_event.set()

        reader_task = asyncio.create_task(
            _capture_output(),
            name=f"local_shell_output_{session_id}",
        )
        wait_task = asyncio.create_task(
            process.wait(),
            name=f"local_shell_wait_{session_id}",
        )
        wait_task.add_done_callback(lambda _: output_event.set())
        session = _LocalShellSession(
            session_id=session_id,
            owner_id=owner_id,
            creator_id=creator_id,
            creator_is_admin=creator_is_admin,
            sandboxed=sandboxed,
            process=process,
            output_path=output_path,
            started_at=time.time(),
            output_event=output_event,
            reader_task=reader_task,
            wait_task=wait_task,
            shell_family=spec.family,
        )

        if timeout is not None:

            async def _enforce_timeout() -> None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(wait_task),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    session.timed_out = True
                    logger.warning(
                        "Managed local shell session timed out: session_id=%s pid=%s",
                        session_id,
                        process.pid,
                    )
                    await self._terminate_process(session)

            session.timeout_task = asyncio.create_task(
                _enforce_timeout(),
                name=f"local_shell_timeout_{session_id}",
            )

        async with self._sessions_lock:
            self._sessions[session_id] = session

        if yield_time_ms > 0:
            try:
                await asyncio.wait_for(
                    asyncio.shield(wait_task),
                    timeout=yield_time_ms / 1000,
                )
            except asyncio.TimeoutError:
                pass

        return await self.poll_session(
            owner_id=owner_id,
            requester_id=creator_id,
            requester_is_admin=creator_is_admin,
            session_id=session_id,
            cursor=0,
            yield_time_ms=0,
            max_output_chars=max_output_chars,
        )

    async def list_sessions(
        self,
        *,
        owner_id: str,
        requester_id: str,
        requester_is_admin: bool,
    ) -> dict[str, Any]:
        """List managed shell sessions visible to one requester.

        Args:
            owner_id: Unified message origin containing the sessions.
            requester_id: Sender ID requesting the session list.
            requester_is_admin: Whether the requester is an administrator.

        Returns:
            Session summaries scoped to the conversation and requester.
        """
        async with self._sessions_lock:
            sessions = [
                session
                for session in self._sessions.values()
                if session.owner_id == owner_id
                and (
                    requester_is_admin
                    or (
                        not session.creator_is_admin
                        and session.creator_id == requester_id
                    )
                )
            ]

        items = []
        for session in sessions:
            exit_code = session.process.returncode
            status = (
                "running"
                if exit_code is None
                else (
                    "timed_out"
                    if session.timed_out
                    else (
                        "terminated"
                        if session.terminated
                        else ("completed" if exit_code == 0 else "failed")
                    )
                )
            )
            try:
                output_size = session.output_path.stat().st_size
            except OSError:
                output_size = session.cursor
            items.append(
                {
                    "session_id": session.session_id,
                    "pid": session.process.pid,
                    "status": status,
                    "exit_code": exit_code,
                    "started_at": session.started_at,
                    "sandboxed": session.sandboxed,
                    "unread_output_bytes": max(output_size - session.cursor, 0),
                }
            )
        return {"sessions": items}

    async def poll_session(
        self,
        *,
        owner_id: str,
        requester_id: str,
        requester_is_admin: bool,
        session_id: str,
        cursor: int | None = None,
        yield_time_ms: int = 0,
        max_output_chars: int = 10_000,
    ) -> dict[str, Any]:
        """Read new output and status from a managed shell session.

        Args:
            owner_id: Unified message origin containing the session.
            requester_id: Sender ID requesting the output.
            requester_is_admin: Whether the requester is an administrator.
            session_id: Managed shell session identifier.
            cursor: Byte offset to read from. Defaults to the last returned offset.
            yield_time_ms: Maximum wait for new output or process completion.
            max_output_chars: Maximum output bytes returned in this call.

        Returns:
            Incremental output, next cursor, process status, and exit code.

        Raises:
            ValueError: If the session is unavailable or an argument is invalid.
        """
        if yield_time_ms < 0 or yield_time_ms > 120_000:
            raise ValueError("`yield_time_ms` must be between 0 and 120000.")
        if max_output_chars < 1:
            raise ValueError("`max_output_chars` must be greater than 0.")

        session = await self._get_owned_session(
            owner_id,
            requester_id,
            requester_is_admin,
            session_id,
        )
        read_cursor = session.cursor if cursor is None else cursor
        if read_cursor < 0:
            raise ValueError("`cursor` must be greater than or equal to 0.")

        def _read_output() -> tuple[bytes, int, int]:
            try:
                output_size = session.output_path.stat().st_size
            except FileNotFoundError:
                return b"", read_cursor, read_cursor
            normalized_cursor = min(read_cursor, output_size)
            with session.output_path.open("rb") as output_file:
                output_file.seek(normalized_cursor)
                raw_output = output_file.read(max_output_chars)
            return (
                raw_output,
                normalized_cursor + len(raw_output),
                output_size,
            )

        reader_stuck = False
        if session.wait_task.done():
            # The reader only ends at pipe EOF, which a surviving detached
            # child (e.g. an MSYS2 grandchild) can postpone indefinitely by
            # inheriting the stdout handle. Bound the wait and proceed with
            # the last-known output; once the reader proves stuck, later
            # waits in this call would gain nothing.
            reader_stuck = not await _bounded_await(session.reader_task, timeout=5)
        raw_output, next_cursor, output_size = await asyncio.to_thread(_read_output)

        if not raw_output and session.process.returncode is None and yield_time_ms > 0:
            session.output_event.clear()
            raw_output, next_cursor, output_size = await asyncio.to_thread(_read_output)
            if not raw_output and session.process.returncode is None:
                output_waiter = asyncio.create_task(session.output_event.wait())
                done, _ = await asyncio.wait(
                    {output_waiter, session.wait_task},
                    timeout=yield_time_ms / 1000,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if output_waiter not in done:
                    output_waiter.cancel()
                    try:
                        await output_waiter
                    except asyncio.CancelledError:
                        pass
                if session.wait_task.done() and not reader_stuck:
                    reader_stuck = not await _bounded_await(
                        session.reader_task, timeout=5
                    )
                raw_output, next_cursor, output_size = await asyncio.to_thread(
                    _read_output
                )

        exit_code = session.process.returncode
        if exit_code is not None and not reader_stuck:
            reader_stuck = not await _bounded_await(session.reader_task, timeout=5)
            raw_output, next_cursor, output_size = await asyncio.to_thread(_read_output)

        exit_code = session.process.returncode
        if (
            exit_code is not None
            and not session.reader_task.done()
            and not reader_stuck
        ):
            await _bounded_await(session.reader_task, timeout=5)
            raw_output, next_cursor, output_size = await asyncio.to_thread(_read_output)

        session.cursor = next_cursor
        status = (
            "running"
            if exit_code is None
            else (
                "timed_out"
                if session.timed_out
                else (
                    "terminated"
                    if session.terminated
                    else ("completed" if exit_code == 0 else "failed")
                )
            )
        )
        has_more = next_cursor < output_size
        session_closed = exit_code is not None and not has_more
        result = {
            "session_id": session.session_id,
            "pid": session.process.pid,
            "status": status,
            "stdout": _decode_shell_output(raw_output),
            "stderr": "",
            "exit_code": exit_code,
            "cursor": next_cursor,
            "has_more": has_more,
            "session_closed": session_closed,
        }
        if session_closed:
            await self._remove_session(session)
        return result

    async def write_session(
        self,
        *,
        owner_id: str,
        requester_id: str,
        requester_is_admin: bool,
        session_id: str,
        chars: str,
    ) -> dict[str, Any]:
        """Write text to the stdin pipe of a managed shell session.

        Args:
            owner_id: Unified message origin containing the session.
            requester_id: Sender ID writing to the process.
            requester_is_admin: Whether the requester is an administrator.
            session_id: Managed shell session identifier.
            chars: Text to write verbatim.

        Returns:
            Current process status after the write.

        Raises:
            PermissionError: If the text is a kill command that cannot be
                verified as safe.
            ValueError: If the session is unavailable or no longer accepts input.
        """
        # Per-write check only: a command split across several writes is not
        # reassembled, so this raises the bar rather than closing the path.
        if refusal := _would_kill_self(chars):
            raise PermissionError(refusal)
        session = await self._get_owned_session(
            owner_id,
            requester_id,
            requester_is_admin,
            session_id,
        )
        if session.process.returncode is not None or session.process.stdin is None:
            raise ValueError(f"Shell session {session_id} is not accepting input.")
        session.process.stdin.write(chars.encode("utf-8"))
        await session.process.stdin.drain()
        return {
            "session_id": session_id,
            "pid": session.process.pid,
            "status": "running",
            "written_chars": len(chars),
        }

    async def interrupt_session(
        self,
        *,
        owner_id: str,
        requester_id: str,
        requester_is_admin: bool,
        session_id: str,
        yield_time_ms: int = 1_000,
        max_output_chars: int = 10_000,
    ) -> dict[str, Any]:
        """Send an interrupt signal to a managed shell process group.

        Args:
            owner_id: Unified message origin containing the session.
            requester_id: Sender ID requesting the interrupt.
            requester_is_admin: Whether the requester is an administrator.
            session_id: Managed shell session identifier.
            yield_time_ms: Maximum wait for output or exit after the signal.
            max_output_chars: Maximum output bytes returned after the signal.

        Returns:
            Incremental output and status after sending the interrupt.
        """
        session = await self._get_owned_session(
            owner_id,
            requester_id,
            requester_is_admin,
            session_id,
        )
        if session.process.returncode is None:
            if os.name == "nt":
                try:
                    session.process.send_signal(
                        getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)
                    )
                except Exception:
                    # Console control events are undeliverable when the child
                    # has no console (e.g. AstrBot runs under pythonw.exe),
                    # and CPython can misreport the failure as SystemError.
                    # Fall back to a graceful taskkill sweep of the tree.
                    logger.debug(
                        "CTRL_BREAK undeliverable for shell session %s; "
                        "falling back to taskkill",
                        session_id,
                    )
                    try:
                        await asyncio.to_thread(
                            subprocess.run,
                            ["taskkill", "/T", "/PID", str(session.process.pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                        )
                    except Exception:
                        pass
            else:
                try:
                    os.killpg(session.process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
        return await self.poll_session(
            owner_id=owner_id,
            requester_id=requester_id,
            requester_is_admin=requester_is_admin,
            session_id=session_id,
            yield_time_ms=yield_time_ms,
            max_output_chars=max_output_chars,
        )

    async def terminate_session(
        self,
        *,
        owner_id: str,
        requester_id: str,
        requester_is_admin: bool,
        session_id: str,
        max_output_chars: int = 10_000,
    ) -> dict[str, Any]:
        """Terminate a managed shell process group.

        Args:
            owner_id: Unified message origin containing the session.
            requester_id: Sender ID requesting termination.
            requester_is_admin: Whether the requester is an administrator.
            session_id: Managed shell session identifier.
            max_output_chars: Maximum remaining output bytes to return.

        Returns:
            Remaining output and final process status.
        """
        session = await self._get_owned_session(
            owner_id,
            requester_id,
            requester_is_admin,
            session_id,
        )
        session.terminated = True
        await self._terminate_process(session)
        return await self.poll_session(
            owner_id=owner_id,
            requester_id=requester_id,
            requester_is_admin=requester_is_admin,
            session_id=session_id,
            yield_time_ms=0,
            max_output_chars=max_output_chars,
        )

    async def shutdown_sessions(self, *, keep_family: str | None = None) -> None:
        """Terminate and remove managed local shell sessions.

        Args:
            keep_family: When set, sessions started with this shell family are
                left running and only the remaining ones are terminated. The
                dashboard uses it to invalidate sessions that a shell-type
                change made stale.
        """
        async with self._sessions_lock:
            sessions = [
                session
                for session in self._sessions.values()
                if keep_family is None or session.shell_family != keep_family
            ]
        for session in sessions:
            session.terminated = True
        termination_results = await asyncio.gather(
            *(self._terminate_process(session) for session in sessions),
            return_exceptions=True,
        )
        for session, result in zip(sessions, termination_results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "Failed to terminate managed local shell session %s: %s",
                    session.session_id,
                    result,
                )
        for session in sessions:
            # The reader only ends at pipe EOF, which a detached grandchild
            # can postpone indefinitely; cancel it rather than blocking the
            # whole shutdown on a stuck pipe.
            await _bounded_await(session.reader_task, timeout=5, cancel_on_timeout=True)
        for session in sessions:
            await self._remove_session(session)

    async def _get_owned_session(
        self,
        owner_id: str,
        requester_id: str,
        requester_is_admin: bool,
        session_id: str,
    ) -> _LocalShellSession:
        """Resolve a shell session while enforcing requester ownership.

        Args:
            owner_id: Unified message origin that must contain the session.
            requester_id: Sender ID requesting access.
            requester_is_admin: Whether the requester is an administrator.
            session_id: Managed shell session identifier.

        Returns:
            Matching managed shell session.

        Raises:
            ValueError: If the session does not exist for this owner.
        """
        async with self._sessions_lock:
            session = self._sessions.get(session_id)
        if (
            session is None
            or session.owner_id != owner_id
            or (
                not requester_is_admin
                and (session.creator_is_admin or session.creator_id != requester_id)
            )
        ):
            raise ValueError(f"Shell session {session_id} was not found.")
        return session

    async def _terminate_process(self, session: _LocalShellSession) -> None:
        """Gracefully terminate a process group, then force it if needed.

        Args:
            session: Managed shell session to terminate.
        """
        if session.process.returncode is not None:
            return
        if os.name == "nt" and session.shell_family == "git_bash":
            # MSYS2 background jobs (`cmd &`) detach from the Windows
            # parent-PID chain that `taskkill /T` walks, so the sweep alone
            # leaks them. CTRL_BREAK_EVENT reaches the whole MSYS2 process
            # group; the taskkill below stays as the fallback for whatever
            # the signal did not reach.
            try:
                session.process.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception:
                # Same console-less failure as interrupt_session: the event
                # cannot be delivered without a console and CPython may
                # misreport it as SystemError. The taskkill sweep below is
                # the fallback for whatever the signal did not reach.
                pass
            else:
                try:
                    await asyncio.wait_for(asyncio.shield(session.wait_task), timeout=5)
                except asyncio.TimeoutError:
                    pass
        if os.name == "nt":
            try:
                taskkill_result = await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill", "/F", "/T", "/PID", str(session.process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except Exception:
                should_terminate = True
            else:
                should_terminate = taskkill_result.returncode != 0
            # The signal above may already have ended the process, in which
            # case the sweep reports failure and terminating again raises
            # ProcessLookupError.
            if should_terminate and session.process.returncode is None:
                session.process.terminate()
        else:
            try:
                os.killpg(session.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        try:
            await asyncio.wait_for(
                asyncio.shield(session.wait_task),
                timeout=5,
            )
        except asyncio.TimeoutError:
            if os.name == "nt":
                session.process.kill()
            else:
                try:
                    os.killpg(session.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if not await _bounded_await(session.wait_task, timeout=5):
                logger.warning(
                    "Managed local shell process did not exit after kill: pid=%s",
                    session.process.pid,
                )

    async def _remove_session(self, session: _LocalShellSession) -> None:
        """Remove a completed session and its temporary output file.

        Args:
            session: Managed shell session to remove.
        """
        async with self._sessions_lock:
            if self._sessions.get(session.session_id) is session:
                self._sessions.pop(session.session_id, None)
        timeout_task = session.timeout_task
        if (
            timeout_task is not None
            and timeout_task is not asyncio.current_task()
            and not timeout_task.done()
        ):
            timeout_task.cancel()
            try:
                await timeout_task
            except asyncio.CancelledError:
                pass
        session.output_path.unlink(missing_ok=True)
        try:
            session.output_path.parent.rmdir()
        except OSError:
            pass


@dataclass
class LocalPythonComponent(PythonComponent):
    async def exec(
        self,
        code: str,
        kernel_id: str | None = None,
        timeout: int = 30,
        silent: bool = False,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        # The child interpreter must be the console-subsystem ``python.exe``:
        # together with ``CREATE_NO_WINDOW`` below it gets a windowless
        # console that every nested CUI child spawned by user code
        # (subprocess / os.system / multiprocessing) inherits, so no console
        # window ever flashes even when AstrBot itself runs under
        # ``pythonw.exe`` (GUI subsystem, no console to inherit).
        python_exe = os.environ.get("PYTHON", sys.executable)
        if sys.platform == "win32" and Path(python_exe).name.lower() == "pythonw.exe":
            sibling_console_exe = Path(python_exe).with_name("python.exe")
            if sibling_console_exe.exists():
                python_exe = str(sibling_console_exe)

        def _run() -> dict[str, Any]:
            try:
                working_dir = os.path.abspath(cwd) if cwd else get_astrbot_root()
                result = subprocess.run(
                    [python_exe, "-c", code],
                    timeout=timeout,
                    capture_output=True,
                    cwd=working_dir,
                    # Windowless console for the direct child; nested CUI
                    # children inherit it. Non-Windows passes no extra flags.
                    **_NO_WINDOW_KWARGS,
                )
                stdout = "" if silent else _decode_shell_output(result.stdout)
                stderr = (
                    _decode_shell_output(result.stderr)
                    if result.returncode != 0
                    else ""
                )
                return {
                    "data": {
                        "output": {"text": stdout, "images": []},
                        "error": stderr,
                    }
                }
            except subprocess.TimeoutExpired:
                return {
                    "data": {
                        "output": {"text": "", "images": []},
                        "error": "Execution timed out.",
                    }
                }

        return await asyncio.to_thread(_run)


@dataclass
class LocalFileSystemComponent(FileSystemComponent):
    async def create_file(
        self, path: str, content: str = "", mode: int = 0o644
    ) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            abs_path = os.path.abspath(path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(abs_path, mode)
            return {"success": True, "path": abs_path}

        return await asyncio.to_thread(_run)

    async def read_file(
        self,
        path: str,
        encoding: str = "utf-8",
        offset: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            abs_path = os.path.abspath(path)
            detected_encoding = encoding
            if encoding == "utf-8":
                with open(abs_path, "rb") as f:
                    raw_sample = f.read(8192)
                detected_encoding = detect_text_encoding(raw_sample) or encoding
            return {
                "success": True,
                "content": read_local_text_range_sync(
                    abs_path,
                    encoding=detected_encoding,
                    offset=offset,
                    limit=limit,
                ),
            }

        return await asyncio.to_thread(_run)

    async def search_files(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        after_context: int | None = None,
        before_context: int | None = None,
    ) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            if sys.version_info < (3, 14):
                results = search(
                    patterns=[pattern],
                    paths=[path] if path else None,
                    globs=[glob] if glob else None,
                    after_context=after_context,
                    before_context=before_context,
                    line_number=True,
                )
                return {
                    "success": True,
                    "content": _truncate_long_lines("".join(results)),
                }

            rg_path = shutil.which("rg")
            if not rg_path:
                return {
                    "success": False,
                    "content": "",
                    "error": (
                        "The ripgrep (rg) executable is required for file search on "
                        "Python 3.14 or later because python-ripgrep 0.0.8 is "
                        "incompatible."
                    ),
                }

            command = [rg_path, "--color=never", "-n", "-e", pattern]
            if glob:
                command.extend(["-g", glob])
            if after_context is not None:
                command.extend(["-A", str(after_context)])
            if before_context is not None:
                command.extend(["-B", str(before_context)])
            command.extend(["--", path or "."])

            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "content": "",
                    "error": "File search timed out after 30 seconds.",
                }
            except OSError as exc:
                return {
                    "success": False,
                    "content": "",
                    "error": f"Unable to start ripgrep: {exc}",
                }

            stdout = _decode_bytes_with_fallback(
                result.stdout, preferred_encoding="utf-8"
            )
            if result.returncode == 0:
                return {
                    "success": True,
                    "content": _truncate_long_lines(stdout),
                }
            if result.returncode == 1:
                return {"success": True, "content": ""}

            stderr = _decode_bytes_with_fallback(
                result.stderr, preferred_encoding="utf-8"
            ).strip()
            return {
                "success": False,
                "content": "",
                "error": stderr or f"ripgrep exited with code {result.returncode}",
                "exit_code": result.returncode,
            }

        return await asyncio.to_thread(_run)

    async def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            abs_path = os.path.abspath(path)
            with open(abs_path, encoding=encoding) as f:
                content = f.read()
            occurrences = content.count(old_string)
            if occurrences == 0:
                return {
                    "success": False,
                    "error": "old string not found in file",
                    "replacements": 0,
                }
            if replace_all:
                updated = content.replace(old_string, new_string)
                replacements = occurrences
            else:
                updated = content.replace(old_string, new_string, 1)
                replacements = 1
            with open(abs_path, "w", encoding=encoding) as f:
                f.write(updated)
            return {
                "success": True,
                "path": abs_path,
                "replacements": replacements,
            }

        return await asyncio.to_thread(_run)

    async def write_file(
        self, path: str, content: str, mode: str = "w", encoding: str = "utf-8"
    ) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            abs_path = os.path.abspath(path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, mode, encoding=encoding) as f:
                f.write(content)
            return {"success": True, "path": abs_path}

        return await asyncio.to_thread(_run)

    async def delete_file(self, path: str) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            abs_path = os.path.abspath(path)
            if os.path.isdir(abs_path):
                shutil.rmtree(abs_path)
            else:
                os.remove(abs_path)
            return {"success": True, "path": abs_path}

        return await asyncio.to_thread(_run)

    async def list_dir(
        self, path: str = ".", show_hidden: bool = False
    ) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            abs_path = os.path.abspath(path)
            entries = os.listdir(abs_path)
            if not show_hidden:
                entries = [e for e in entries if not e.startswith(".")]
            return {"success": True, "entries": entries}

        return await asyncio.to_thread(_run)


class LocalBooter(ComputerBooter):
    def __init__(self) -> None:
        self._fs = LocalFileSystemComponent()
        self._python = LocalPythonComponent()
        self._shell = LocalShellComponent()

    async def boot(self, session_id: str) -> None:
        logger.info(f"Local computer booter initialized for session: {session_id}")

    async def shutdown(self) -> None:
        await self._shell.shutdown_sessions()
        logger.info("Local computer booter shutdown complete.")

    @property
    def fs(self) -> FileSystemComponent:
        return self._fs

    @property
    def python(self) -> PythonComponent:
        return self._python

    @property
    def shell(self) -> ShellComponent:
        return self._shell

    async def upload_file(self, path: str, file_name: str) -> dict:
        raise NotImplementedError(
            "LocalBooter does not support upload_file operation. Use shell instead."
        )

    async def download_file(self, remote_path: str, local_path: str) -> None:
        raise NotImplementedError(
            "LocalBooter does not support download_file operation. Use shell instead."
        )

    async def available(self) -> bool:
        return True
