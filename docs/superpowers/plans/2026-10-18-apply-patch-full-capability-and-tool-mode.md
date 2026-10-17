# apply_patch 全能力与文件工具组切换 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `astrbot_apply_patch` to cover `file_write` + `file_edit` (replace-all, encoding, strict-indent, history/rollback), then gate the two mutually exclusive file-tool groups behind `provider_settings.file_tool_mode`.

**Architecture:** Patch grammar gains a `*** Replace All:` section; `plan_patch` gains `encoding` / `strict_indent`; `ApplyPatchTool.call` gains three mutually exclusive modes. A new `file_tool_mode` helper resolves the config and names the active group; four assembly points select tools by mode.

**Tech Stack:** Python 3.10+, asyncio, dataclasses, pytest, ruff.

## Global Constraints

- Default `file_tool_mode = "legacy"`: upgrading must not change the model-visible toolset.
- Keep `FileEditTool` / `FileWriteTool` classes registered; mode only affects assembly.
- Do not change existing tool result formats for file_edit / file_write.
- Comments and logs in English; user-facing tool descriptions in English.
- All commits local; never push.
- Run `uv run ruff format` + `uv run ruff check` before each commit.
- Any parse / planning / encoding / mode-validation failure must write zero bytes.
- Backup store stays `EditHistoryManager`; old backups must remain readable.

---

### Task 1: Replace All Parser + Engine

**Files:**
- Modify: `astrbot/core/tools/computer_tools/patch_engine.py`
- Test: `tests/unit/test_patch_engine.py`

**Interfaces:**
- `FilePatch` gains `old_lines: list[str]`, `new_lines: list[str]`, kind `"replace_all"`.
- Produces: `apply_replace_all_to_text(text, old_lines, new_lines, *, strict_indent=True) -> tuple[str, int]`

- [ ] **Step 1: Write failing parser/engine tests**

```python
def test_parse_replace_all_section():
    files = parse_patch(
        "*** Replace All: a.py\n-old\n-old2\n+new\n+new2\n"
    )
    assert files[0].kind == "replace_all"
    assert files[0].old_lines == ["old", "old2"]
    assert files[0].new_lines == ["new", "new2"]


def test_parse_rejects_replace_all_with_anchor():
    with pytest.raises(PatchParseError, match="Replace All"):
        parse_patch("*** Replace All: a.py\n@@ head\n-old\n+new\n")


def test_parse_rejects_update_and_replace_all_same_path():
    with pytest.raises(PatchParseError, match="duplicate path"):
        parse_patch(
            "*** Update File: a.py\n-a\n+b\n"
            "*** Replace All: a.py\n-c\n+d\n"
        )


def test_replace_all_replaces_every_occurrence():
    out, count = apply_replace_all_to_text(
        "x = 1\nx = 1\nx = 1\n", ["x = 1"], ["x = 2"]
    )
    assert count == 3
    assert out == "x = 2\nx = 2\nx = 2\n"


def test_replace_all_zero_matches_raises():
    with pytest.raises(ValueError, match="Could not find"):
        apply_replace_all_to_text("a\n", ["zzz"], ["b"])


def test_replace_all_respects_strict_indent_false():
    out, _ = apply_replace_all_to_text(
        "    x = 1\n", ["    x = 1"], ["\ty = 2"], strict_indent=False
    )
    assert "\ty = 2" in out
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_patch_engine.py -k "replace_all" -q`
Expected: FAIL (`ImportError` / unknown kind).

- [ ] **Step 3: Implement parser support**

Add marker `_REPLACE_ALL = "*** Replace All:"`.

In `FilePatch`, add:

```python
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)
```

In the marker dispatch (before the generic `*** Update File` handling is fine, order matters only for prefix collisions — the markers are distinct):

```python
        if stripped.startswith(_REPLACE_ALL):
            finish_hunk()
            path = _marker_value(stripped, _REPLACE_ALL)
            if not path:
                fail("Replace All requires a path", index)
            if path in seen_paths:
                fail(f"duplicate path: {path}", index)
            seen_paths.add(path)
            current = FilePatch(kind="replace_all", path=path)
            files.append(current)
            continue
```

In the per-kind line handling, before the `add`/`delete` branches:

```python
        if current.kind == "replace_all":
            if stripped.startswith("@@"):
                fail("Replace All does not accept @@ anchors", index)
            if line.startswith("+"):
                current.new_lines.append(line[1:])
            elif line.startswith("-"):
                current.old_lines.append(line[1:])
            else:
                fail("Replace All lines must start with '+' or '-'", index)
            continue
```

After the parse loop:

```python
        if entry.kind == "replace_all" and (
            not entry.old_lines or not entry.new_lines
        ):
            raise PatchParseError(
                f"Replace All {entry.path!r} needs both - lines and + lines"
            )
```

- [ ] **Step 4: Implement the engine**

Import `robust_replace` from `.edit_engine` and add:

```python
def apply_replace_all_to_text(
    text: str,
    old_lines: list[str],
    new_lines: list[str],
    *,
    strict_indent: bool = True,
) -> tuple[str, int]:
    """Replace every occurrence of the old block."""
    return robust_replace(
        text,
        "\n".join(old_lines),
        "\n".join(new_lines),
        replace_all=True,
        strict_indent=strict_indent,
    )
```

- [ ] **Step 5: Run tests, format, lint, commit**

```bash
uv run pytest tests/unit/test_patch_engine.py -q
uv run ruff format astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
uv run ruff check astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
git add astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
git commit -m "feat(patch-engine): parse and apply replace-all sections"
```

---

### Task 2: Replace All Planning, Stats, Diff

**Files:**
- Modify: `astrbot/core/tools/computer_tools/patch_engine.py`
- Test: `tests/unit/test_patch_engine.py`

**Interfaces:**
- `plan_patch` handles `kind == "replace_all"`.

- [ ] **Step 1: Write the failing test**

```python
def test_plan_replace_all_counts_and_diff():
    files = parse_patch("*** Replace All: a.txt\n-x\n+X\n")
    plan = plan_patch(files, lambda p: b"x\nx\n")
    item = plan.files[0]
    assert item.kind == "replace_all"
    assert item.new_bytes == b"X\nX\n"
    assert item.added == 2
    assert item.removed == 2
    assert "+X" in item.diff
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_patch_engine.py::test_plan_replace_all_counts_and_diff -q`
Expected: FAIL (plan treats it as update / missing `new_bytes`).

- [ ] **Step 3: Implement the planning branch**

In `plan_patch`, extend the `else` chain:

```python
        elif entry.kind == "replace_all":
            old_text, eol, has_bom = decode_text_bytes(original or b"")
            new_text, occurrences = apply_replace_all_to_text(
                old_text, entry.old_lines, entry.new_lines
            )
            new_bytes = encode_text_bytes(new_text, eol, has_bom)
            _validate_python(entry.path, old_text, new_text)
            added = occurrences * len(entry.new_lines)
            removed = occurrences * len(entry.old_lines)
            diff = build_unified_diff(entry.path, old_text, new_text)
        else:
            ... existing update branch ...
```

Guard: `replace_all` requires an existing file (`original is not None`), same as update:

```python
        if entry.kind in ("update", "delete", "replace_all") and original is None:
            raise PatchParseError(f"file does not exist: {entry.path}")
```

Ensure the generic `added/removed` recomputation at the bottom does not overwrite the replace-all numbers: compute `added/removed` only when not already set, or move the generic counting into each branch. Simplest: set `diff`/`added`/`removed` in each branch and leave the shared block only for `update`/`add`.

- [ ] **Step 4: Run tests, format, lint, commit**

```bash
uv run pytest tests/unit/test_patch_engine.py -q
uv run ruff format astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
uv run ruff check astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
git add astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
git commit -m "feat(patch-engine): plan replace-all edits with stats"
```

---

### Task 3: Encoding Support

**Files:**
- Modify: `astrbot/core/tools/computer_tools/patch_engine.py`
- Modify: `astrbot/core/tools/computer_tools/patch_tool.py`
- Test: `tests/unit/test_patch_engine.py`, `tests/test_apply_patch_tool.py`

**Interfaces:**
- `plan_patch(patches, read, *, encoding="utf-8")`
- `patch_tool.normalize_encoding(name: str) -> tuple[str, bool]` → `(codec, bom_for_new)`

- [ ] **Step 1: Write failing tests**

```python
def test_plan_updates_gbk_file():
    files = parse_patch("*** Update File: a.txt\n-old\n+new\n")
    plan = plan_patch(files, lambda p: "old\n".encode("gbk"), encoding="gbk")
    assert plan.files[0].new_bytes == "new\n".encode("gbk")


def test_plan_new_file_utf8_sig_has_bom():
    files = parse_patch("*** Add File: a.txt\n+x\n")
    plan = plan_patch(files, lambda p: None, encoding="utf-8-sig")
    assert plan.files[0].new_bytes == b"\xef\xbb\xbfx\n"
```

Tool test:

```python
@pytest.mark.asyncio
async def test_invalid_encoding_writes_nothing(workspace):
    (workspace / "a.txt").write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(),
        patch="*** Update File: a.txt\n-old\n+new\n",
        encoding="no-such-codec",
    )
    assert "encoding" in result.lower()
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "old\n"
```

- [ ] **Step 2: Implement the normalizer**

In `patch_tool.py`:

```python
def normalize_encoding(name: str) -> tuple[str, bool]:
    """Return (codec, bom_for_new_files)."""
    raw = (name or "utf-8").strip().lower().replace("_", "-")
    if raw in ("utf-8", "utf8"):
        return "utf-8", False
    if raw.replace(" ", "-") in ("utf-8-bom", "utf-8-sig", "utf8-bom", "utf8-sig"):
        return "utf-8", True
    if raw == "ansi":
        if sys.platform == "win32":
            import ctypes
            return f"cp{ctypes.windll.kernel32.GetACP()}", False
        return locale.getpreferredencoding(False) or "utf-8", False
    codecs.lookup(raw)  # raises LookupError for unknown names
    return raw, False
```

- [ ] **Step 3: Thread encoding through planning**

`plan_patch(..., *, encoding="utf-8", ...)`:
- update / replace_all: `decode_text_bytes(raw, encoding)`
- add: `text = "\n".join(add_lines) + "\n"; new_bytes = text.encode(encoding)` but honor `bom_for_new`. Since `plan_patch` receives a codec + bom flag, pass `encoding` and `bom_for_new` separately? Simplest: pass `encoding: str` already normalized to the codec, and `new_file_bom: bool`. Or let `plan_patch` accept `encoding` and compute BOM if encoding == "utf-8-sig" — but we normalized. Add parameter `bom_for_new: bool = False`.

Signature: `plan_patch(patches, read, *, encoding="utf-8", bom_for_new=False)`.
- Add branch:
  ```python
  text = "\n".join(entry.add_lines) + "\n"
  new_bytes = encode_text_bytes(text, "\n", bom_for_new, encoding)
  ```
  `encode_text_bytes` prefixes BOM when `has_bom`; codec is utf-8 for utf-8-sig, so no double BOM.

- [ ] **Step 4: Tool call wiring**

At `call` entry:

```python
        try:
            codec, bom_for_new = normalize_encoding(encoding)
        except LookupError:
            return f"Error: unknown encoding: {encoding}"
```

Pass `encoding=codec, bom_for_new=bom_for_new` to `plan_patch`.
History modes ignore it.

- [ ] **Step 5: Run tests, format, lint, commit**

```bash
uv run pytest tests/unit/test_patch_engine.py tests/test_apply_patch_tool.py -q
uv run ruff format astrbot/core/tools/computer_tools/patch_engine.py astrbot/core/tools/computer_tools/patch_tool.py tests/unit/test_patch_engine.py tests/test_apply_patch_tool.py
uv run ruff check ...
git commit -m "feat(patch-tool): support per-call text encoding"
```

---

### Task 4: `strict_indent` Pass-through

**Files:**
- Modify: `patch_engine.py`, `patch_tool.py`
- Test: `tests/unit/test_patch_engine.py`, `tests/test_apply_patch_tool.py`

- [ ] **Step 1: Write failing tests**

```python
def test_apply_strict_indent_false_allows_style_change():
    hunk = Hunk(old_lines=["    return 1"], new_lines=["\treturn 2"])
    out = apply_hunks_to_text(
        "def f():\n    return 1\n", [hunk], strict_indent=False
    )
    assert "\treturn 2" in out


def test_apply_strict_indent_true_still_rejects():
    hunk = Hunk(old_lines=["    return 1"], new_lines=["\treturn 2"])
    with pytest.raises(ValueError, match="indentation style"):
        apply_hunks_to_text("def f():\n    return 1\n", [hunk])
```

- [ ] **Step 2: Implement pass-through**

- `apply_hunks_to_text(text, hunks, *, strict_indent=True)`
- `_apply_one(..., strict_indent)` and every `adjusted_replacement(..., strict_indent=strict_indent)`
- `plan_patch(..., strict_indent=True)` → pass to `apply_hunks_to_text` and `apply_replace_all_to_text`
- Tool schema: `"strict_indent": {"type": "boolean", "default": True}`
- `call(..., strict_indent=True)` → pass to `plan_patch`
- History modes ignore it.

- [ ] **Step 3: Run tests, format, lint, commit**

```bash
uv run pytest tests/unit/test_patch_engine.py tests/test_apply_patch_tool.py -q
uv run ruff ... && git commit -m "feat(patch-tool): expose strict_indent"
```

---

### Task 5: History and Rollback Modes

**Files:**
- Modify: `astrbot/core/tools/computer_tools/patch_tool.py`
- Test: `tests/test_apply_patch_tool.py`

**Interfaces:**
- Schema: `patch` optional; add `path`, `list_history`, `backup_id`.
- `ApplyPatchTool._list_history(path, local_env, sb) -> str`
- `ApplyPatchTool._rollback(path, backup_id, context, local_env, sb) -> str`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_list_history_and_rollback(workspace):
    target = workspace / "a.txt"
    target.write_text("v1\n", encoding="utf-8")
    await ApplyPatchTool().call(
        _context(), patch="*** Update File: a.txt\n-v1\n+v2\n"
    )
    history = await ApplyPatchTool().call(
        _context(), path="a.txt", list_history=True
    )
    assert "Edit history for" in history
    backup_id = history.split("[")[1].split("]")[0]

    rolled = await ApplyPatchTool().call(
        _context(), path="a.txt", backup_id=backup_id
    )
    assert "Successfully rolled back" in rolled
    assert target.read_text(encoding="utf-8") == "v1\n"


@pytest.mark.asyncio
async def test_mode_validation(workspace):
    (workspace / "a.txt").write_text("x\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(),
        patch="*** Update File: a.txt\n-x\n+y\n",
        path="a.txt",
        list_history=True,
    )
    assert "exactly one" in result.lower() or "mode" in result.lower()
```

- [ ] **Step 2: Implement mode validation**

```python
patch = (patch or "").strip()
has_patch = bool(patch)
has_history = bool(list_history)
has_rollback = bool(backup_id)
if sum([has_patch, has_history, has_rollback]) != 1:
    return (
        "Error: provide exactly one mode: `patch`, "
        "`path` + `list_history=true`, or `path` + `backup_id`."
    )
if (has_history or has_rollback) and not path:
    return "Error: `path` is required for history and rollback modes."
if has_patch and (path or list_history or backup_id):
    return "Error: `patch` cannot be combined with history/rollback parameters."
```

- [ ] **Step 3: Implement list/rollback**

- Normalize `path` with `_normalize_rw_path(..., write=list_history is False)`.
- `history_mgr.list_backups` formatting copied verbatim from `FileEditTool._do_list_history`.
- Rollback:
  ```python
  entry, backup_bytes = await asyncio.to_thread(
      history_mgr.read_backup, real_path, backup_id
  )
  # read current bytes; save pre-rollback snapshot
  # write backup_bytes via local/sandbox writer
  _record_changed_file(context, real_path, "rollback", runtime, snapshot_id)
  return f"Successfully rolled back {real_path} to backup {entry.id} ({entry.timestamp}, {entry.size} bytes)."
  ```
- Catch `ValueError` / `FileNotFoundError` and return `Error: {exc}`.

- [ ] **Step 4: Run tests, format, lint, commit**

```bash
uv run pytest tests/test_apply_patch_tool.py -q
uv run ruff ... && git commit -m "feat(patch-tool): add history and rollback modes"
```

---

### Task 6: `file_tool_mode` Config Helper

**Files:**
- Create: `astrbot/core/tools/file_tool_mode.py`
- Modify: `astrbot/core/config/default.py`
- Test: `tests/test_file_tool_mode.py` (new)

- [ ] **Step 1: Write failing tests**

```python
from astrbot.core.tools.file_tool_mode import (
    file_tool_allowed,
    file_tool_names,
    resolve_file_tool_mode,
)


def test_default_is_legacy():
    assert resolve_file_tool_mode({}) == "legacy"
    assert resolve_file_tool_mode(None) == "legacy"


def test_patch_mode():
    assert resolve_file_tool_mode({"file_tool_mode": "patch"}) == "patch"
    assert resolve_file_tool_mode({"file_tool_mode": "bogus"}) == "legacy"


def test_file_tool_names_and_allowed():
    assert file_tool_names("legacy") == (
        "astrbot_file_write_tool",
        "astrbot_file_edit_tool",
    )
    assert file_tool_names("patch") == ("astrbot_apply_patch",)
    assert file_tool_allowed("astrbot_apply_patch", "legacy") is False
    assert file_tool_allowed("astrbot_file_edit_tool", "patch") is False
    assert file_tool_allowed("astrbot_file_read_tool", "patch") is True
```

- [ ] **Step 2: Implement**

```python
"""File write tool-group selection."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

FILE_TOOL_MODE_KEY = "file_tool_mode"
FILE_TOOL_MODE_LEGACY = "legacy"
FILE_TOOL_MODE_PATCH = "patch"
DEFAULT_FILE_TOOL_MODE = FILE_TOOL_MODE_LEGACY

_LEGACY_TOOLS = ("astrbot_file_write_tool", "astrbot_file_edit_tool")
_PATCH_TOOLS = ("astrbot_apply_patch",)
_warned: set[str] = set()


def resolve_file_tool_mode(provider_settings: dict | None) -> str:
    raw = (provider_settings or {}).get(FILE_TOOL_MODE_KEY, DEFAULT_FILE_TOOL_MODE)
    mode = str(raw or DEFAULT_FILE_TOOL_MODE).strip().lower()
    if mode in (FILE_TOOL_MODE_LEGACY, FILE_TOOL_MODE_PATCH):
        return mode
    if mode not in _warned:
        _warned.add(mode)
        logger.warning(
            "Unknown %s=%r; falling back to %s",
            FILE_TOOL_MODE_KEY, raw, DEFAULT_FILE_TOOL_MODE,
        )
    return DEFAULT_FILE_TOOL_MODE


def file_tool_names(mode: str) -> tuple[str, ...]:
    return _PATCH_TOOLS if mode == FILE_TOOL_MODE_PATCH else _LEGACY_TOOLS


def file_tool_allowed(name: str, mode: str) -> bool:
    if name in _LEGACY_TOOLS:
        return mode == FILE_TOOL_MODE_LEGACY
    if name in _PATCH_TOOLS:
        return mode == FILE_TOOL_MODE_PATCH
    return True
```

- [ ] **Step 3: Config default + metadata**

- `DEFAULT_CONFIG["provider_settings"]["file_tool_mode"] = "legacy"`.
- Add the `provider_settings.file_tool_mode` item to `agent_computer_use.items` exactly as in the spec.

- [ ] **Step 4: Run tests, format, lint, commit**

```bash
uv run pytest tests/test_file_tool_mode.py -q
uv run ruff ... && git commit -m "feat(config): add file_tool_mode switch"
```

---

### Task 7: Gate the Four Assembly Points

**Files:**
- Modify: `astrbot/core/astr_main_agent.py`
- Modify: `astrbot/core/astr_agent_tool_exec.py`
- Test: `tests/test_file_tool_mode.py`

- [ ] **Step 1: Write failing assembly tests**

Use a fake tool manager:

```python
from types import SimpleNamespace


class FakeMgr:
    def get_builtin_tool(self, tool):
        name = tool if isinstance(tool, str) else tool.name
        return SimpleNamespace(name=name)


def test_runtime_tools_legacy_vs_patch():
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

    legacy = FunctionToolExecutor._get_runtime_computer_tools(
        "local", FakeMgr(), mode="legacy"
    )
    assert "astrbot_file_edit_tool" in legacy
    assert "astrbot_apply_patch" not in legacy

    patch = FunctionToolExecutor._get_runtime_computer_tools(
        "local", FakeMgr(), mode="patch"
    )
    assert "astrbot_apply_patch" in patch
    assert "astrbot_file_edit_tool" not in patch
```

`_get_runtime_computer_tools` keeps class lookups for non-file tools and uses
`file_tool_names(mode)` + `tool_mgr.get_builtin_tool(name)` for the file tools, so the
fake only needs to return an object with a `name` attribute.

- [ ] **Step 2: Wire each point**

- `_apply_local_env_tools`: read `cfg = plugin_context.get_config(umo=umo)` before adding
  tools; replace the two explicit adds with:
  ```python
  mode = resolve_file_tool_mode(cfg.get("provider_settings", {}))
  for name in file_tool_names(mode):
      req.func_tool.add_tool(tool_mgr.get_builtin_tool(name))
  ```
- `_apply_sandbox_tools`: `mode = resolve_file_tool_mode(config.provider_settings)`; remove the
  explicit FileWrite/FileEdit adds and use the same loop (this also introduces apply_patch in
  patch mode, fixing the current sandbox gap).
- `_get_runtime_computer_tools(..., mode="legacy")`: replace the write/edit entries with
  `file_tools = {name: tool_mgr.get_builtin_tool(name) for name in file_tool_names(mode)}`
  and `tools.update(file_tools)`. Pass `mode` from `_build_handoff_toolset`, resolved from
  `provider_settings`.
- `_build_handoff_toolset`: in the name loop:
  ```python
  if isinstance(tool_name_or_obj, str) and not file_tool_allowed(tool_name_or_obj, mode):
      continue
  ```

- [ ] **Step 3: Run tests (assembly + existing tool tests)**

```bash
uv run pytest tests/test_file_tool_mode.py tests/test_computer_fs_tools.py -q
```

- [ ] **Step 4: Format, lint, commit**

```bash
uv run ruff ... && git commit -m "feat(agent): select file tool group by config"
```

---

### Task 8: Tool Description

**Files:**
- Modify: `astrbot/core/tools/computer_tools/patch_tool.py`
- Test: `tests/test_apply_patch_tool.py`

- [ ] **Step 1: Write the failing test**

```python
def test_description_documents_all_modes():
    desc = ApplyPatchTool().description
    for needle in ("Replace All", "list_history", "backup_id", "encoding"):
        assert needle in desc
```

- [ ] **Step 2: Rewrite the description**

Four sections: Patch / Replace All / History / Options, each 2–4 lines. Keep the existing minimal patch example.

- [ ] **Step 3: Run tests, format, lint, commit**

```bash
uv run pytest tests/test_apply_patch_tool.py -q
uv run ruff ... && git commit -m "docs(patch-tool): document aggregate capabilities"
```

---

### Task 9: Full Verification

- [ ] **Step 1: Run related suites**

```bash
uv run pytest tests/unit/test_edit_engine.py tests/unit/test_patch_engine.py tests/test_apply_patch_tool.py tests/test_apply_patch_registration.py tests/test_file_tool_mode.py tests/test_computer_fs_tools.py -q
```

- [ ] **Step 2: Run the unit suite**

```bash
uv run pytest tests/unit -q
```
Expected: no new failures (the known Windows CRLF test may still fail).

- [ ] **Step 3: Run the stress harness**

```bash
uv run python scripts/apply_patch_stress.py
```
Expected: 38/38 (existing cases unchanged).

- [ ] **Step 4: Format/lint the touched surface**

```bash
uv run ruff format astrbot/core/tools/file_tool_mode.py astrbot/core/tools/computer_tools astrbot/core/astr_main_agent.py astrbot/core/astr_agent_tool_exec.py astrbot/core/config/default.py tests/unit/test_patch_engine.py tests/test_apply_patch_tool.py tests/test_file_tool_mode.py
uv run ruff check <same paths>
```

- [ ] **Step 5: Commit final fixes**

```bash
git add <touched paths>
git commit -m "test(agent): verify apply_patch capability merge and tool mode"
```

---

## Self-Review

**Spec coverage:**
- 配置项 + 默认 + UI 元数据 → Task 6
- 四个装配点 → Task 7
- Replace All 语法 / 0-N / 统计 → Tasks 1–2
- encoding → Task 3
- strict_indent → Task 4
- 历史/回滚 + 模式校验 → Task 5
- 工具描述 → Task 8
- 测试矩阵 → Tasks 1–9

**Placeholder scan:** no TBD/TODO; every code step contains concrete code or an exact edit instruction.

**Type consistency:** `FilePatch.kind` includes `"replace_all"`; `plan_patch(patches, read, *, encoding, bom_for_new, strict_indent)`; `apply_hunks_to_text(text, hunks, *, strict_indent)`; `apply_replace_all_to_text(text, old_lines, new_lines, *, strict_indent)`; `normalize_encoding(name) -> tuple[str, bool]`; `resolve_file_tool_mode(provider_settings)`; `file_tool_names(mode)`, `file_tool_allowed(name, mode)`.
