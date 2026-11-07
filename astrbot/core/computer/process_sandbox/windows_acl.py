"""Windows write/delete sandbox backend.

Runs the model-facing shell/Python commands through windows_acl_native.py, a
trusted runner that creates a WRITE_RESTRICTED, Low-integrity token and a
kill-on-close Job before spawning the wrapped argv inside a DACL allowlist.

Only file writes and deletes are confined. Reads and network are unrestricted
by design (the current product requirement); filesystem_scope, readable_roots
and allow_network are therefore ignored here.

Known boundaries inherited from the native runner: a hard link is the same
file object, so an external hard link can become writable through a granted
tree (the boundary is partial, never "full"); the DACL grants are standing
for the workspace/explicit roots and revoked only for the per-execution temp
directory.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from .base import (
    ProcessSandbox,
    SandboxProcess,
    SandboxRunResult,
    SandboxSpec,
    SandboxTimeoutError,
)

RUNNER_SIGNATURE = "windows-acl-run:"
_RUNNER_PATH = Path(__file__).with_name("windows_acl_native.py")

_ENV_WHITELIST = (
    "PATH",
    "PATHEXT",
    "COMSPEC",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "USERNAME",
    "USERDOMAIN",
    "LOGONSERVER",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "ProgramData",
    "CommonProgramFiles",
    "CommonProgramFiles(x86)",
    "CommonProgramW6432",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION",
    "OS",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONDONTWRITEBYTECODE",
)

_BLOCKED_ENV_KEYS = {
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "NODE_OPTIONS",
    "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES",
}


class SandboxUnavailableError(RuntimeError):
    """Raised when the Windows sandbox runner could not establish confinement."""


class WindowsSandboxProcess:
    """Adapt an asyncio runner process to the SandboxProcess protocol."""

    def __init__(self, process: asyncio.subprocess.Process, temp_dir: str) -> None:
        self._process = process
        self._temp_dir = temp_dir
        self._cleaned = False

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    @property
    def stdin(self):
        return self._process.stdin

    @property
    def stdout(self):
        return self._process.stdout

    async def wait(self) -> int:
        try:
            return await self._process.wait()
        finally:
            self._cleanup()

    def interrupt(self) -> None:
        self._kill_runner()

    def terminate(self) -> None:
        self._kill_runner()

    def kill(self) -> None:
        self._kill_runner()

    def send_signal(self, _signal) -> None:
        self._kill_runner()

    def _kill_runner(self) -> None:
        if self._process.returncode is None:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass

    def _cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def __del__(self) -> None:  # best-effort when nobody awaits wait()
        try:
            self._cleanup()
        except Exception:
            pass


class WindowsAclProcessSandbox(ProcessSandbox):
    """Write/delete-confining process sandbox for native Windows."""

    def __init__(self) -> None:
        self.python_exe = _console_python_exe()
        self.runner_path = str(_RUNNER_PATH)

    @staticmethod
    def _new_temp_dir() -> str:
        path = os.path.join(
            tempfile.gettempdir(), "astrbot-sandbox-" + uuid.uuid4().hex[:12]
        )
        os.mkdir(path)
        return path

    @staticmethod
    def _windows_startupinfo():
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        return startupinfo

    def _build_env(self, extra: dict[str, str] | None) -> dict[str, str]:
        env = {key: os.environ[key] for key in _ENV_WHITELIST if key in os.environ}
        for raw_key, raw_value in (extra or {}).items():
            key = str(raw_key)
            value = str(raw_value)
            if not key or "=" in key or "\x00" in key or "\x00" in value:
                raise ValueError(f"Invalid sandbox environment variable name: {key!r}.")
            if key.upper() in _BLOCKED_ENV_KEYS:
                continue
            env[key] = value
        return env

    def _build_command(
        self, argv: list[str], spec: SandboxSpec, temp_dir: str, env: dict[str, str]
    ) -> list[str]:
        del env  # the runner inherits the sanitized environment from Popen
        command = [
            self.python_exe,
            self.runner_path,
            "--workspace",
            str(Path(spec.workspace).resolve()),
            "--mode",
            "workspace" if spec.workspace_writable else "read-only",
            "--temp",
            temp_dir,
            "--max-processes",
            str(spec.limits.processes),
        ]
        if spec.workspace_writable:
            for root in spec.writable_roots:
                command.extend(("--writable", str(Path(root).resolve())))
        command.append("--")
        command.extend(argv)
        return command

    @staticmethod
    def _check_runner_failure(returncode: int, stderr: str) -> None:
        if returncode == 127 and RUNNER_SIGNATURE in stderr:
            detail = (
                stderr.strip().splitlines()[-1]
                if stderr.strip()
                else "unknown runner failure"
            )
            raise SandboxUnavailableError(f"Windows sandbox unavailable: {detail}")

    def run(
        self,
        argv: list[str],
        spec: SandboxSpec,
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        output_limit: int | None = None,
        discard_stdout: bool = False,
    ) -> SandboxRunResult:
        sandbox_argv, workspace, normalized_env = self._prepare_command(
            argv, spec, env=env
        )
        temp_dir = self._new_temp_dir()
        child_env = self._build_env(normalized_env)
        command = self._build_command(sandbox_argv, spec, temp_dir, child_env)
        try:
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(workspace),
                    env=child_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL if discard_stdout else subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                    startupinfo=self._windows_startupinfo(),
                )
            except subprocess.TimeoutExpired as exc:
                raise SandboxTimeoutError(
                    f"Sandbox command timed out after {timeout} seconds."
                ) from exc
            stdout = b"" if discard_stdout else completed.stdout or b""
            stderr = completed.stderr or b""
            self._check_runner_failure(
                completed.returncode, stderr.decode("utf-8", "replace")
            )
            stdout_limited = output_limit is not None and len(stdout) > output_limit
            stderr_limited = output_limit is not None and len(stderr) > output_limit
            if output_limit is not None:
                stdout = stdout[:output_limit]
                stderr = stderr[:output_limit]
            return SandboxRunResult(
                returncode=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                stdout_limited=stdout_limited,
                stderr_limited=stderr_limited,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def spawn_argv(
        self,
        argv: list[str],
        spec: SandboxSpec,
        *,
        env: dict[str, str] | None = None,
    ) -> SandboxProcess:
        sandbox_argv, workspace, normalized_env = self._prepare_command(
            argv, spec, env=env
        )
        temp_dir = self._new_temp_dir()
        child_env = self._build_env(normalized_env)
        command = self._build_command(sandbox_argv, spec, temp_dir, child_env)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(workspace),
                env=child_env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                startupinfo=self._windows_startupinfo(),
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        return WindowsSandboxProcess(process, temp_dir)

    async def spawn_shell(
        self,
        command: str,
        spec: SandboxSpec,
        *,
        env: dict[str, str] | None = None,
    ) -> SandboxProcess:
        comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
        return await self.spawn_argv([comspec, "/d", "/c", command], spec, env=env)


def _console_python_exe() -> str:
    executable = sys.executable
    if Path(executable).name.lower() != "pythonw.exe":
        return executable
    sibling = Path(executable).with_name("python.exe")
    return str(sibling) if sibling.exists() else executable
