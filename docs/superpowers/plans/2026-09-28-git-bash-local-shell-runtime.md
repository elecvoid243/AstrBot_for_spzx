# Git Bash Local Shell Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Windows users run local shell commands in Git Bash, with Git Bash auto-selected when present and PowerShell 7 / Windows PowerShell 5.1 as fallbacks.

**Architecture:** Replace the bare-string `resolve_windows_shell()` with `resolve_local_shell()`, which returns a frozen `ShellSpec` (family tag + executable + fixed argv prefix). All spawn sites build their argv from the spec, so adding a shell family means adding one table row. The shell choice is a `provider_settings` config value resolved per `umo`, consistent with `computer_use_runtime`.

**Tech Stack:** Python 3.10+, `subprocess`, `asyncio.create_subprocess_exec`, pytest, `ruff`.

**Spec:** This plan carries its own spec — see *Probe Evidence* below. The measurements there were taken on Windows 10/11 with Git for Windows 2.38 and are the authority for every pinned value.

**Worktree:** Create one via the `superpowers:using-git-worktrees` skill before Task 1. The repository is currently on branch `all` with uncommitted changes (`.gitignore`, a dashboard CSS file, and one untracked changelog); do not disturb them.

## Global Constraints

- Python 3.10+; no new third-party dependencies.
- Cross-platform: Windows, macOS, Linux; Arm64 and x86.
- All comments, log messages, and docstrings in **English**.
- Docstrings use the Google format (`Args:` / `Returns:` / `Raises:`).
- Use `pathlib.Path` for path handling, never string concatenation.
- Windows-only behaviour must be guarded by `sys.platform == "win32"` or `os.name == "nt"`, matching the surrounding code.
- Run `ruff format .` and `ruff check .` before every commit.
- Conventional commit messages.
- Do not add helper functions unless the same logic appears in 3+ places; inline otherwise.
- `LocalShellComponent.exec()` has no production caller (local runtime always uses `exec_managed`); it is exercised only by tests. Keep it working, but do not build new features on it.

## Probe Evidence

Measured facts that every task's pinned values come from. Re-read this before changing any constant.

**Shell selection**

| Observation | Value |
|---|---|
| `shutil.which("bash")` on Windows | Resolves to the WSL launcher stub under `WindowsApps` — a different filesystem and shell. **Must never be used.** |
| `shutil.which("git")` → `parent.parent` | `D:\Program Files\Git`; `bin/bash.exe` and `usr/bin/bash.exe` both exist |
| Well-known paths alone | Missed this machine entirely (Git installed on `D:`) |
| `pwsh` on the reference machine | Not installed — a two-level fallback would hard-fail |

**Process lifecycle**

| Scenario | `taskkill /F /T` | `CTRL_BREAK_EVENT` |
|---|---|---|
| `bash -c "sleep 500"` | clean | clean |
| `bash -c "sleep 500 & sleep 500"` | **leaks both `sleep.exe`** | clean, 0 orphans |
| Foreground path with `CREATE_NO_WINDOW`, backgrounded job | **leaks** | clean |
| Delivery to `bash` | — | 0.00 s, rc `0xC000013A` |
| Delivery to PowerShell 5.1 | — | **no response after 15 s** |
| Delivery to `python -c "time.sleep(60)"` (control) | — | 0.00 s, rc `0xC000013A` |

`CTRL_BREAK_EVENT` cannot reach a process created with `CREATE_NO_WINDOW` — that flag drops the console. `CREATE_NEW_PROCESS_GROUP` plus a hidden `STARTUPINFO` keeps the console and does not flash a window.

`CTRL_BREAK_EVENT` is a **hard kill**: a bash `trap ... INT TERM` did not fire (output was `READY` only). Within MSYS2, `kill -INT/-TERM/-9` produce exit codes 130/143/137, and `kill` works **across** bash instances (a later invocation can signal a pid from an earlier one).

**Encoding and IO**

| Observation | Value |
|---|---|
| `ls` / `find` / `grep` / `cat` / `wc` / `stat` / argv / cwd with CJK | Native UTF-8 throughout |
| PowerShell 5.1 CJK output | GBK bytes |
| `目录` encoded as GBK | `C4 BF C2 BC` — **valid UTF-8**, silently mis-decodes to `Ŀ¼` under the current decoder |
| `locale` inside Git Bash | `LC_CTYPE="C.UTF-8"` — no environment injection needed |
| stdin via pipe (`read x`, `cat`) | Works |
| `cwd` translation | `G:\github\X` → `/g/github/X` |
| Startup noise with `-c` (no `-l`) | None; `echo READY` emits exactly `READY\n` |
| Path forms `/c/…`, `C:/…`, `C:\…` | All accepted |

**Buffering** (bash streams line-by-line, same as PowerShell; the hazard is block-buffering *children*)

| Command | Chunks within 3 s |
|---|---|
| bash `echo` loop, `/usr/bin/echo`, `\| cat`, `tail -f` | 3–5 / 5 |
| `\| sed ''` | **0 / 1** — everything at t=5.35 s |
| `\| sed -u`, `\| stdbuf -oL sed`, `\| grep --line-buffered`, `awk fflush()` | 3 / 5 |

**Startup cost** (median, n=12): `bash -c 'echo hi'` 77.8 ms vs `powershell … 'echo hi'` 199.1 ms — bash is 2.56× faster.

## Review Focus

Failure modes the evidence implies but no task's own tests would otherwise catch. Each line names the owning task and has a test there.

1. **Git Bash installed outside `%ProgramFiles%`** (D: drive, Scoop, Chocolatey) — a hard-coded path list would miss it and silently fall back to PowerShell. Owning task: Task 1.
2. **`which("bash")` returning the WSL launcher stub** — selecting it would run commands in a different filesystem. Owning task: Task 1.
3. **A config value naming a shell that is not installed** — must fall back to auto-detect, not raise and disable shell execution. Owning task: Task 1.
4. **A command that backgrounds work (`&`) and then hits the timeout** — must not leak orphan processes. Owning task: Task 3.
5. **One output buffer mixing UTF-8 (bash tools) and GBK (native Windows tools)** — part of it will mojibake; the decoder must not raise. Owning task: Task 2.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `astrbot/core/computer/booters/local.py` | Shell resolution, spawning, lifecycle | Add `ShellSpec`, `_find_git_bash`, `resolve_local_shell`; remove `resolve_windows_shell`; rework `exec`, `exec_managed`, `_terminate_process`, `_LocalShellSession` |
| `astrbot/core/astr_main_agent.py` | Per-turn system prompt | `_build_local_mode_prompt` takes the configured shell; `_apply_local_env_tools` reads it |
| `astrbot/core/tools/computer_tools/shell.py` | `astrbot_execute_shell` tool | Read the configured shell, pass the spec to `exec_managed` |
| `astrbot/core/config/default.py` | Config defaults and dashboard schema | Add `computer_use_local_shell` |
| `tests/test_local_shell_resolver.py` | Resolver unit tests | Create |
| `tests/test_local_shell_component.py` | Spawn/lifecycle tests | Extend |
| `tests/unit/test_astr_main_agent.py` | Prompt tests | Update existing, add git_bash |

---

### Task 1: Shell resolver

**Files:**
- Modify: `astrbot/core/computer/booters/local.py:172-174` (replace `resolve_windows_shell`)
- Test: `tests/test_local_shell_resolver.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ShellSpec` — frozen dataclass with fields `family: str`, `executable: str`, `prefix_args: tuple[str, ...]`
  - `_find_git_bash() -> str | None`
  - `resolve_local_shell(shell_type: str = "auto") -> ShellSpec`

**Pinned values**

`family` is one of `"git_bash"`, `"pwsh"`, `"powershell"`, `"cmd"`, `"posix"`.

| family | `executable` | `prefix_args` |
|---|---|---|
| `git_bash` | detected absolute path | `("-c",)` |
| `pwsh` | `"pwsh.exe"` | `("-NoLogo", "-NoProfile", "-NonInteractive", "-Command")` |
| `powershell` | `"powershell.exe"` | same as `pwsh` |
| `cmd` | `"cmd.exe"` | `("/d", "/s", "/c")` |
| `posix` | `""` | `()` |

`"auto"` priority on Windows: `git_bash` → `pwsh` → `powershell`. Outside Windows: always `posix`, `shell_type` ignored.

`_find_git_bash()` detection order:
1. `shutil.which("git")` → `Path(git).resolve().parent.parent` → `bin/bash.exe`, then `usr/bin/bash.exe`
2. `os.environ["ProgramFiles"] / "Git" / "bin/bash.exe"`
3. `os.environ["ProgramFiles(x86)"] / "Git" / "bin/bash.exe"`
4. `os.environ["LOCALAPPDATA"] / "Programs/Git" / "bin/bash.exe"`

Return the first path where `Path.is_file()` is true, else `None`. Do **not** cache — it is a handful of `is_file()` calls per tool invocation, and a cache would need invalidation in tests. Never call `shutil.which("bash")`.

An explicit `shell_type` whose shell is not installed falls back to `"auto"` and logs a warning via the module `logger`. An unrecognised `shell_type` does the same.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_local_shell_resolver.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_local_shell_resolver.py -v`
Expected: collection error — `AttributeError: module 'astrbot.core.computer.booters.local' has no attribute 'ShellSpec'` (or `_find_git_bash`).

- [ ] **Step 3: Implement the resolver**

In `astrbot/core/computer/booters/local.py`, replace `resolve_windows_shell` (currently at lines 172-174) with `ShellSpec`, `_find_git_bash`, and `resolve_local_shell`, using the pinned values above. `ShellSpec` is a `@dataclass(frozen=True)`; give it a Google-style docstring describing the three fields, including that `executable` is empty for the `posix` family. Both new functions get Google-style docstrings; `_find_git_bash` must state in its docstring why `shutil.which("bash")` is deliberately avoided. Use the module's existing `logger` for the fallback warning.

Keep `pathlib.Path` for the candidate construction and `sys.platform` for the platform check.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_local_shell_resolver.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/computer/booters/local.py tests/test_local_shell_resolver.py
git commit -m "feat: add local shell resolver with git bash detection"
```

---

### Task 2: Spawn commands through the resolved shell

**Files:**
- Modify: `astrbot/core/computer/booters/local.py` (`_LocalShellSession` near line 216, `exec` at line 250, `exec_managed` at line 344)
- Test: `tests/test_local_shell_component.py`

**Interfaces:**
- Consumes: `ShellSpec`, `resolve_local_shell` from Task 1.
- Produces:
  - `_LocalShellSession.shell_family: str` — new required field, inserted immediately after `wait_task` and before the defaulted fields (`timeout_task`, `cursor`, `timed_out`, `terminated`), set from the spec at session creation
  - `LocalShellComponent.exec(command, cwd=None, env=None, timeout=300, shell=True, background=False, shell_spec=None)`
  - `LocalShellComponent.exec_managed(command, *, owner_id, creator_id, creator_is_admin, sandboxed, cwd=None, env=None, timeout=None, yield_time_ms=10_000, max_output_chars=10_000, shell_spec=None)`

`shell_spec: ShellSpec | None = None`; `None` means `resolve_local_shell()`.

**Pinned behaviour**

Both spawn sites build argv as `[spec.executable, *spec.prefix_args, command]` instead of the hard-coded `-NoLogo -NoProfile -NonInteractive -Command` list.

In `exec()`, when the resolved family is `git_bash`, replace `**_NO_WINDOW_KWARGS` with `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` plus a hidden `STARTUPINFO` (`STARTF_USESHOWWINDOW`, `wShowWindow = 0`). `CREATE_NO_WINDOW` drops the console, and without a console `CTRL_BREAK_EVENT` cannot reach the process — which is the only reliable way to sweep MSYS2 background jobs. Add a comment saying exactly that, pointing at `_terminate_process`.

`exec_managed` already uses `CREATE_NEW_PROCESS_GROUP` plus a hidden `STARTUPINFO`; only its argv changes.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_local_shell_component.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_local_shell_component.py -v`
Expected: the two new argv tests fail — the recorded argv is still `["powershell.exe", "-NoLogo", …]`. The mixed-encoding test passes already (it pins existing behaviour; keep it).

- [ ] **Step 3: Implement argv and spawn-shape changes**

Apply the pinned behaviour above. Do not change `_NO_WINDOW_KWARGS` itself — it is still correct for `powershell`/`pwsh`/`cmd` in `exec()`. Build the git_bash kwargs inline next to the existing `_NO_WINDOW_KWARGS` use, with the comment explaining the console requirement.

- [ ] **Step 4: Update the existing tests that assumed PowerShell was always resolved**

Four tests in `tests/test_local_shell_component.py` monkeypatch `shutil.which` to return `None` and assert PowerShell argv. On a machine with Git in `%ProgramFiles%`, `_find_git_bash` now finds it and they would break. Add one line to each:

```python
monkeypatch.setattr(local_booter, "_find_git_bash", lambda: None)
```

The affected tests are `test_local_shell_component_uses_windows_powershell`, `test_exec_falls_back_to_powershell_when_pwsh_missing`, `test_managed_shell_uses_windows_powershell`, and any sibling that asserts `powershell.exe` or `pwsh.exe` argv. Leave their assertions unchanged — they pin the fallback chain.

- [ ] **Step 5: Run the full shell test files**

Run: `uv run pytest tests/test_local_shell_component.py tests/test_local_shell_resolver.py tests/unit/test_computer.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add astrbot/core/computer/booters/local.py tests/test_local_shell_component.py
git commit -m "feat: spawn local shell commands through the resolved shell spec"
```

---

### Task 3: Sweep MSYS2 process trees on termination

**Files:**
- Modify: `astrbot/core/computer/booters/local.py` (`_terminate_process` at line 914, `exec` timeout handler at lines 312-335)
- Test: `tests/test_local_shell_component.py`

**Interfaces:**
- Consumes: `_LocalShellSession.shell_family` from Task 2.
- Produces: nothing new — behaviour change only.

**Pinned behaviour**

`_terminate_process`: when `os.name == "nt"` **and** `session.shell_family == "git_bash"`, send `CTRL_BREAK_EVENT` to the process group and wait up to 5 s for `session.wait_task` **before** running the existing `taskkill /F /T`. If the process is already gone, skip straight to the existing path. `taskkill` stays as the fallback for whatever the signal did not reach. Add a comment recording that `taskkill /T` alone leaks MSYS2 background jobs because they detach from the Windows parent-PID chain.

`exec()`'s `subprocess.TimeoutExpired` handler: same treatment — when the resolved family is `git_bash`, send `CTRL_BREAK_EVENT` to `proc` before the existing `taskkill` block. `exec()` already resolves the spec for its argv; reuse that variable rather than resolving twice.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_local_shell_component.py`:

Add `import signal` to the test module's imports if it is not already there.

```python
@pytest.mark.asyncio
async def test_terminate_process_signals_git_bash_before_taskkill(monkeypatch, tmp_path):
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
        taskkills.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(local_booter.subprocess, "run", fake_taskkill)

    await LocalShellComponent()._terminate_process(session)

    assert signal.CTRL_BREAK_EVENT in signals
    assert taskkills, "taskkill must still run as the fallback sweep"
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
```

Both tests run on Windows only, like the rest of this module's tests — `signal.CTRL_BREAK_EVENT` does not exist elsewhere.

Use a `wait_task` that completes immediately, for example `asyncio.create_task(asyncio.sleep(0))`. `_terminate_process` ends with a bare `await session.wait_task` inside its timeout branch, so a long-running task would hang the test rather than fail it. The git_bash signal path still runs, and the assertion that `taskkill` was called still holds because the sweep is unconditional.

Note: `test_terminate_process_skips_signal_for_powershell` pins that PowerShell behaviour is unchanged — probe evidence showed `CTRL_BREAK_EVENT` is a no-op for PowerShell 5.1, so sending it there adds latency for nothing.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_local_shell_component.py -k terminate_process -v`
Expected: `test_terminate_process_signals_git_bash_before_taskkill` fails — `signals` is empty. `shell_family` must already exist from Task 2 or construction raises `TypeError`.

- [ ] **Step 3: Implement the termination changes**

Apply the pinned behaviour. The 5 s wait reuses the existing `asyncio.wait_for(asyncio.shield(session.wait_task), timeout=5)` shape already present further down in `_terminate_process`, so the timeout branch falls through to the existing `taskkill` path rather than duplicating it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_local_shell_component.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/computer/booters/local.py tests/test_local_shell_component.py
git commit -m "fix: sweep MSYS2 process trees with ctrl-break before taskkill"
```

---

### Task 4: Teach the system prompt about Git Bash

**Files:**
- Modify: `astrbot/core/astr_main_agent.py` (`_build_local_mode_prompt` at line 488; import of `resolve_windows_shell` near the top)
- Test: `tests/unit/test_astr_main_agent.py` (existing tests at lines 387-439)

**Interfaces:**
- Consumes: `resolve_local_shell`, `ShellSpec` from Task 1.
- Produces: `_build_local_mode_prompt(shell_type: str = "auto") -> str` — new optional parameter; Task 5 passes the configured value.

**Pinned behaviour**

The import becomes `from astrbot.core.computer.booters.local import resolve_local_shell`. The function branches on `resolve_local_shell(shell_type).family`:

| family | guidance |
|---|---|
| `posix` | unchanged: "The runtime shell is Unix-like. Use POSIX-compatible shell commands." |
| `git_bash` | new branch, see below |
| `pwsh` | unchanged |
| `powershell` | unchanged |
| `cmd` | new branch: state that the runtime shell is `cmd.exe` and that Unix utilities and PowerShell cmdlets are unavailable |

The `git_bash` branch must say all five of these, each in one sentence:

1. The runtime shell is Git Bash (MSYS2), not PowerShell — Unix tools such as `ls`, `grep`, `sed`, `find` are available.
2. Paths may be written as `/c/...`; Windows-style paths are also accepted by most tools.
3. `python` in this shell resolves to a Windows Store stub that exits successfully with no output — use `astrbot_execute_python` instead.
4. When piping long-running output, disable buffering with `-u` / `--line-buffered` / `fflush()`, otherwise output arrives only when the command finishes.
5. `git status` escapes non-ASCII filenames by default; pass `-c core.quotepath=false` to read them.

The existing `system_name` and `platform.system()` handling stays as-is.

- [ ] **Step 1: Update the existing prompt tests to the new interface**

In `tests/unit/test_astr_main_agent.py`, the three tests that patch `astrbot.core.astr_main_agent.resolve_windows_shell` with a string now patch the new name with a spec:

```python
from astrbot.core.computer.booters.local import ShellSpec

patch(
    "astrbot.core.astr_main_agent.resolve_local_shell",
    return_value=ShellSpec(family="powershell", executable="powershell.exe", prefix_args=("-Command",)),
),
```

Apply the equivalent change to `test_local_mode_prompt_hints_pwsh_when_resolved` (`family="pwsh"`, `executable="pwsh.exe"`) and `test_local_mode_prompt_ignores_pwsh_on_non_windows` (`family="pwsh"`). `test_local_mode_prompt_keeps_posix_shell_guidance` needs no patch change, but must still pass. Keep every existing assertion unchanged.

- [ ] **Step 2: Add the Git Bash prompt test**

```python
def test_local_mode_prompt_describes_git_bash():
    with (
        patch("astrbot.core.astr_main_agent.platform.system", return_value="Windows"),
        patch(
            "astrbot.core.astr_main_agent.resolve_local_shell",
            return_value=ShellSpec(
                family="git_bash",
                executable=r"D:\Git\bin\bash.exe",
                prefix_args=("-c",),
            ),
        ),
    ):
        prompt = ama._build_local_mode_prompt()

    assert "Git Bash" in prompt
    assert "astrbot_execute_python" in prompt
    assert "line-buffered" in prompt
    assert "core.quotepath" in prompt
    assert "PowerShell 7-only syntax" not in prompt
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_astr_main_agent.py -k local_mode_prompt -v`
Expected: `test_local_mode_prompt_describes_git_bash` fails with `AssertionError: assert 'Git Bash' in prompt`. The three updated tests may already pass once `shell_type` defaults to `"auto"`, since they patch the resolver directly.

- [ ] **Step 4: Implement the prompt branches**

Apply the pinned behaviour. Keep the function's existing structure — a single `if`/`elif` chain assigning `shell_hint`, then one return of the assembled prompt — and do not extract helpers.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_astr_main_agent.py -k local_mode_prompt -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add astrbot/core/astr_main_agent.py tests/unit/test_astr_main_agent.py
git commit -m "feat: describe the git bash runtime shell in the local mode prompt"
```

---

### Task 5: Make the shell selectable from config

**Files:**
- Modify: `astrbot/core/config/default.py` (default near line 233; schema inside `agent_computer_use` near line 3929)
- Modify: `astrbot/core/tools/computer_tools/shell.py` (`ExecuteShellTool.call`, the `local_runtime` branch at lines 119-155)
- Modify: `astrbot/core/astr_main_agent.py` (`_apply_local_env_tools` at line 471 and its call site at line 1861)
- Test: `tests/test_local_shell_config.py` (create)

**Interfaces:**
- Consumes: `resolve_local_shell` from Task 1, `_build_local_mode_prompt(shell_type)` from Task 4.
- Produces:
  - Config key `provider_settings.computer_use_local_shell`, default `"auto"`, allowed values `["auto", "git_bash", "pwsh", "powershell", "cmd"]`
  - `_apply_local_env_tools(req, plugin_context, umo: str) -> None` — new required third parameter

**Pinned behaviour**

`default.py` gains `"computer_use_local_shell": "auto",` immediately after `"computer_use_runtime": "none",`.

The schema entry goes into the `agent_computer_use` block, directly after `provider_settings.computer_use_runtime`:

```json
"provider_settings.computer_use_local_shell": {
    "description": "本地 Shell 类型",
    "type": "string",
    "options": ["auto", "git_bash", "pwsh", "powershell", "cmd"],
    "labels": ["自动（Git Bash 优先）", "Git Bash", "PowerShell 7", "Windows PowerShell 5.1", "cmd.exe"],
    "hint": "仅 Windows 生效。auto 依次探测 Git Bash、pwsh、powershell.exe；显式选择但未安装时回退到 auto。",
    "condition": {
        "provider_settings.computer_use_runtime": "local",
    },
},
```

`ExecuteShellTool.call` reads the value in its `local_runtime` branch and passes it down:

```python
cfg = context.context.context.get_config(
    umo=context.context.event.unified_msg_origin
)
shell_type = str(
    cfg.get("provider_settings", {}).get("computer_use_local_shell", "auto")
)
result = await sb.shell.exec_managed(
    command,
    # ...existing keyword arguments unchanged...
    shell_spec=resolve_local_shell(shell_type),
)
```

`_apply_local_env_tools` does the same read via `plugin_context.get_config(umo=umo)` and passes the string to `_build_local_mode_prompt(shell_type)`. Its call site in `build_main_agent` becomes `_apply_local_env_tools(req, plugin_context, event.unified_msg_origin)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_local_shell_config.py`:

```python
"""Tests for the computer_use_local_shell configuration surface."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrbot.core.config.default import DEFAULT_CONFIG  # noqa: E402


def test_local_shell_default_is_auto():
    provider_settings = DEFAULT_CONFIG["provider_settings"]

    assert provider_settings["computer_use_local_shell"] == "auto"


def test_local_shell_schema_lists_every_family():
    from astrbot.core.config.default import CONFIG_METADATA_3

    schema = CONFIG_METADATA_3["ai_group"]["metadata"]["agent_computer_use"]["items"]
    entry = schema["provider_settings.computer_use_local_shell"]

    assert entry["options"] == ["auto", "git_bash", "pwsh", "powershell", "cmd"]
    assert len(entry["labels"]) == len(entry["options"])
    assert entry["condition"] == {
        "provider_settings.computer_use_runtime": "local",
    }
```

The dashboard schema lives in `CONFIG_METADATA_3` (line 3429), not in `DEFAULT_CONFIG` — `agent_computer_use` is at line 3924 nested under `ai_group` → `metadata`. `DEFAULT_CONFIG` holds only the runtime default value.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_local_shell_config.py -v`
Expected: `KeyError: 'computer_use_local_shell'`.

- [ ] **Step 3: Add the config default and schema entry**

Apply the pinned values. Match the surrounding indentation and key ordering exactly; `default.py` is large and machine-formatted.

- [ ] **Step 4: Wire the value through the tool and the prompt**

Apply the pinned `ExecuteShellTool.call` and `_apply_local_env_tools` changes, including the call-site update in `build_main_agent`. Import `resolve_local_shell` in `shell.py` alongside the existing `LocalShellComponent` import.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_local_shell_config.py tests/unit/test_astr_main_agent.py -v`
Expected: all pass.

- [ ] **Step 6: Run the whole affected suite and the linters**

Run: `uv run pytest tests/test_local_shell_config.py tests/test_local_shell_resolver.py tests/test_local_shell_component.py tests/unit/test_computer.py tests/unit/test_astr_main_agent.py tests/test_computer_config.py -v`
Expected: all pass.

Run: `ruff format . && ruff check .`
Expected: no changes needed, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add astrbot/core/config/default.py astrbot/core/tools/computer_tools/shell.py astrbot/core/astr_main_agent.py tests/test_local_shell_config.py
git commit -m "feat: make the local shell runtime configurable"
```

---

## Verification

After all five tasks, confirm on a Windows machine with Git installed:

1. `uv run pytest tests/ -k "shell or computer or main_agent" -v` — all pass.
2. Start AstrBot with `computer_use_runtime` set to `local` and run `uname -a` through `astrbot_execute_shell`. Expected: MSYS2 output, confirming Git Bash is selected.
3. Set `computer_use_local_shell` to `powershell` and rerun. Expected: the command now fails, confirming the config takes effect and the prompt guidance follows.
4. Start a long-running command (`sleep 300 & sleep 300`), then terminate its session via `astrbot_shell_session`. Expected: no orphaned `sleep.exe` in `tasklist`.

## Out of Scope

- PTY support via `winpty` (present at `/usr/bin/winpty`). It would make every child line-buffered and enable TTY-requiring programs, but needs a spawn-architecture change and its own interrupt-semantics probing.
- Injecting `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_0` / `GIT_CONFIG_VALUE_0` to disable `core.quotepath` without touching the user's git config. Task 4 covers this in the prompt instead.
- Fixing PowerShell 5.1's non-response to `CTRL_BREAK_EVENT`, which makes `astrbot_shell_session`'s `interrupt` a no-op on that family today. Task 3 explicitly leaves that behaviour unchanged.
