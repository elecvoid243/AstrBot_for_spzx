"""Builtin apply_patch tool: atomic multi-file patch application."""

from __future__ import annotations

import asyncio
import codecs
import locale
import sys
from dataclasses import dataclass, field
from pathlib import Path

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.computer.computer_client import get_booter
from astrbot.core.tools import fs_access
from astrbot.core.tools.computer_tools.edit_engine import get_file_lock
from astrbot.core.tools.computer_tools.edit_history import get_history_manager
from astrbot.core.tools.computer_tools.fs import (
    _READONLY_WRITE_ERROR,
    _is_forbidden_path,
    _is_user_blacklisted,
    _normalize_rw_path,
    _read_file_bytes,
    _record_changed_file,
    _sandbox_read_bytes,
    _sandbox_write_bytes,
    _write_guard,
)
from astrbot.core.tools.computer_tools.patch_engine import (
    PatchParseError,
    parse_patch,
    plan_patch,
)
from astrbot.core.tools.computer_tools.util import (
    is_local_runtime,
    workspace_root_for_context,
)
from astrbot.core.tools.registry import builtin_tool


def normalize_encoding(name: str) -> tuple[str, bool]:
    """Return (codec, bom_for_new_files).

    Args:
        name: User-provided encoding alias.

    Returns:
        A codec name plus whether new files should start with a UTF-8 BOM.

    Raises:
        LookupError: If the name is not a known Python codec.
    """
    raw = (name or "utf-8").strip().lower().replace("_", "-")
    if raw in ("utf-8", "utf8"):
        return "utf-8", False
    compact = raw.replace(" ", "-")
    if compact in ("utf-8-bom", "utf-8-sig", "utf8-bom", "utf8-sig"):
        return "utf-8", True
    if raw == "ansi":
        if sys.platform == "win32":
            import ctypes

            return f"cp{ctypes.windll.kernel32.GetACP()}", False
        return locale.getpreferredencoding(False) or "utf-8", False
    codecs.lookup(raw)
    return raw, False


def _write_file_bytes(path: str, data: bytes) -> None:
    """Write bytes, creating missing parent directories."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _remove_file(path: str) -> None:
    """Remove a file."""
    Path(path).unlink()


async def _sandbox_remove_file(sb, path: str) -> None:
    """Remove a file inside the sandbox via sb.python.exec.

    Args:
        sb: Sandbox booter.
        path: Remote file path.

    Raises:
        OSError: On removal failure.
    """
    code = (
        "import os, sys\n"
        f"path = {path!r}\n"
        "try:\n"
        "    os.remove(path)\n"
        "except Exception as e:\n"
        "    print('ERROR:' + str(e), end='', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
    result = await sb.python.exec(code, timeout=30)
    if not result.get("success", False):
        raise OSError(f"Failed to remove sandbox file: {path}")


@builtin_tool(config={"provider_settings.computer_use_runtime": ("local", "sandbox")})
@dataclass
class ApplyPatchTool(FunctionTool):
    """Apply a Codex-style patch atomically."""

    name: str = "astrbot_apply_patch"
    description: str = (
        "Apply a multi-file patch. Use for structural edits (add, delete, move) "
        "or changes spanning several files; for a single small edit prefer "
        "`astrbot_file_edit_tool`.\n\n"
        "Format:\n"
        "*** Begin Patch\n"
        "*** Update File: path\n"
        "-old line\n"
        "+new line\n"
        "*** Add File: path\n"
        "+content\n"
        "*** Delete File: path\n"
        "*** End Patch\n\n"
        "Context lines must be copied from the file. Destructive changes "
        "(delete, overwrite) are not applied unless `confirm=true`."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "patch": {"type": "string", "description": "Patch text."},
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview only; never write.",
                    "default": False,
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Required for destructive changes.",
                    "default": False,
                },
                "encoding": {
                    "type": "string",
                    "description": "Text encoding for patched files.",
                    "default": "utf-8",
                },
                "strict_indent": {
                    "type": "boolean",
                    "description": "Keep the file's indentation on fuzzy matches.",
                    "default": True,
                },
            },
            "required": ["patch"],
        }
    )

    async def call(
        self,
        context: ContextWrapper,
        patch: str,
        dry_run: bool = False,
        confirm: bool = False,
        encoding: str = "utf-8",
        strict_indent: bool = True,
    ) -> ToolExecResult:
        if fs_access.get_mode(context) is fs_access.FileAccessMode.READONLY:
            return _READONLY_WRITE_ERROR

        try:
            codec, bom_for_new = normalize_encoding(encoding)
        except LookupError:
            return f"Error: unknown encoding: {encoding}"

        local_env = is_local_runtime(context)
        restricted, extra_roots = await _write_guard(context)
        umo = str(context.context.event.unified_msg_origin)
        workspace_root = (
            await workspace_root_for_context(context) if local_env else None
        )

        try:
            entries = parse_patch(patch)
        except PatchParseError as exc:
            return f"Error: invalid patch: {exc}"

        resolved: dict[str, str] = {}
        try:
            for entry in entries:
                resolved[entry.path] = _normalize_rw_path(
                    entry.path,
                    restricted=restricted,
                    local_env=local_env,
                    umo=umo,
                    write=True,
                    current_workspace_root=workspace_root,
                    extra_write_roots=extra_roots,
                )
                if entry.move_to is not None:
                    resolved[entry.move_to] = _normalize_rw_path(
                        entry.move_to,
                        restricted=restricted,
                        local_env=local_env,
                        umo=umo,
                        write=True,
                        current_workspace_root=workspace_root,
                        extra_write_roots=extra_roots,
                    )
        except (ValueError, PermissionError) as exc:
            return f"Error: {exc}"

        cache: dict[str, bytes | None] = {}
        sb = None
        if local_env:
            for real in set(resolved.values()):
                try:
                    cache[real] = await asyncio.to_thread(_read_file_bytes, real)
                except FileNotFoundError:
                    cache[real] = None
        else:
            sb = await get_booter(context.context.context, umo)
            for real in set(resolved.values()):
                try:
                    cache[real] = await _sandbox_read_bytes(sb, real)
                except OSError:
                    cache[real] = None

        def read(path: str) -> bytes | None:
            return cache.get(resolved.get(path, path))

        try:
            plan = plan_patch(
                entries,
                read,
                encoding=codec,
                bom_for_new=bom_for_new,
                strict_indent=strict_indent,
            )
        except (PatchParseError, ValueError) as exc:
            return f"Error: patch cannot be applied: {exc}"

        blacklist = (
            context.context.context.get_config(umo=umo)
            .get("provider_settings", {})
            .get("file_remove_blacklist")
            or []
        )
        for entry in entries:
            if entry.kind != "delete" and entry.move_to is None:
                continue
            source = Path(resolved[entry.path])
            if _is_forbidden_path(source) or _is_user_blacklisted(source, blacklist):
                return f"Error: deleting or moving {entry.path} is not allowed."

        if plan.destructive and not dry_run and not confirm:
            return self._confirmation(plan)
        if dry_run:
            return self._preview(plan)
        return await self._commit(
            plan,
            resolved,
            context=context,
            local_env=local_env,
            sb=sb,
        )

    @staticmethod
    def _confirmation(plan) -> str:
        lines = ["This patch contains destructive changes and was NOT applied."]
        for item in plan.destructive:
            lines.append(f"- {item.destructive_reason}: {item.path}")
        lines.append("Retry with confirm=true after the user approves.")
        return "\n".join(lines)

    @staticmethod
    def _preview(plan) -> str:
        lines = [
            f"Dry run: no files were written. {len(plan.files)} files, "
            f"+{plan.added} -{plan.removed}"
        ]
        for item in plan.files:
            suffix = f" -> {item.move_to}" if item.move_to else ""
            lines.append(f"{item.kind.upper()} {item.path}{suffix}")
        if plan.destructive:
            lines.extend(["", "Destructive changes (need confirm=true when applying):"])
            for item in plan.destructive:
                lines.append(f"- {item.destructive_reason}: {item.path}")
        body = "\n\n".join(item.diff for item in plan.files if item.diff)
        if body:
            lines.extend(["", body[:40000]])
        return "\n".join(lines)

    async def _commit(self, plan, resolved, *, context, local_env, sb) -> str:
        if local_env:

            async def read_at(path: str) -> bytes | None:
                return await asyncio.to_thread(self._read_optional, path)

            async def write_at(path: str, data: bytes) -> None:
                await asyncio.to_thread(_write_file_bytes, path, data)

            async def remove_at(path: str) -> None:
                await asyncio.to_thread(_remove_file, path)

            runtime = "local"
        else:

            async def read_at(path: str) -> bytes | None:
                try:
                    return await _sandbox_read_bytes(sb, path)
                except OSError:
                    return None

            async def write_at(path: str, data: bytes) -> None:
                await _sandbox_write_bytes(sb, path, data, create_parents=True)

            async def remove_at(path: str) -> None:
                await _sandbox_remove_file(sb, path)

            runtime = "sandbox"

        writes = []
        moves = []
        deletes = []
        for item in plan.files:
            real = resolved[item.path]
            if item.kind == "delete":
                deletes.append((item, real))
            elif item.move_to is not None:
                moves.append((item, real, resolved[item.move_to]))
            elif item.new_bytes is not None:
                writes.append((item, real))

        paths = sorted(
            {real for _, real in writes}
            | {src for _, src, _ in moves}
            | {dst for _, _, dst in moves}
            | {real for _, real in deletes}
        )
        locks = []
        committed: list[tuple[str, bytes | None]] = []
        try:
            for path in paths:
                lock = get_file_lock(path)
                await lock.acquire()
                locks.append(lock)

            for item, real in writes:
                if await read_at(real) != item.original:
                    raise RuntimeError(f"file changed since plan: {item.path}")
            for item, src, dst in moves:
                if await read_at(src) != item.original:
                    raise RuntimeError(f"file changed since plan: {item.path}")
                if await read_at(dst) != item.target_original:
                    raise RuntimeError(f"file changed since plan: {item.move_to}")
            for item, real in deletes:
                if await read_at(real) != item.original:
                    raise RuntimeError(f"file changed since plan: {item.path}")

            history = get_history_manager()
            for item, real in writes:
                backup_id = ""
                if item.original is not None:
                    entry = await asyncio.to_thread(
                        history.save_backup, real, item.original, runtime=runtime
                    )
                    backup_id = entry.id
                await write_at(real, item.new_bytes or b"")
                committed.append((real, item.original))
                _record_changed_file(
                    context,
                    real,
                    "created" if item.original is None else "edit",
                    runtime,
                    backup_id,
                )
            for item, src, dst in moves:
                backup_id = ""
                if item.target_original is not None:
                    entry = await asyncio.to_thread(
                        history.save_backup,
                        dst,
                        item.target_original,
                        runtime=runtime,
                    )
                    backup_id = entry.id
                await write_at(dst, item.new_bytes or b"")
                committed.append((dst, item.target_original))
                _record_changed_file(context, dst, "write", runtime, backup_id)
            for item, real in deletes:
                entry = await asyncio.to_thread(
                    history.save_backup,
                    real,
                    item.original or b"",
                    runtime=runtime,
                )
                await remove_at(real)
                committed.append((real, item.original))
                _record_changed_file(context, real, "remove", runtime, entry.id)
            for item, src, _ in moves:
                entry = await asyncio.to_thread(
                    history.save_backup,
                    src,
                    item.original or b"",
                    runtime=runtime,
                )
                await remove_at(src)
                committed.append((src, item.original))
                _record_changed_file(context, src, "remove", runtime, entry.id)

            return self._success(plan)
        except Exception as exc:
            failures = await self._rollback(committed, write_at, remove_at)
            if failures:
                return (
                    f"Error: {exc}\nRollback failed for: {', '.join(failures)}\n"
                    "Manual cleanup required."
                )
            return f"Error: {exc}\nNo files were modified."
        finally:
            for lock in reversed(locks):
                lock.release()

    @staticmethod
    def _read_optional(path: str) -> bytes | None:
        try:
            return _read_file_bytes(path)
        except FileNotFoundError:
            return None

    @staticmethod
    async def _rollback(committed, write_at, remove_at) -> list[str]:
        failures: list[str] = []
        for path, original in reversed(committed):
            try:
                if original is None:
                    await remove_at(path)
                else:
                    await write_at(path, original)
            except Exception:
                failures.append(path)
        return failures

    @staticmethod
    def _success(plan) -> str:
        lines = [
            f"Applied patch: {len(plan.files)} files changed "
            f"(+{plan.added} -{plan.removed})"
        ]
        for item in plan.files:
            suffix = f" -> {item.move_to}" if item.move_to else ""
            lines.append(f"{item.kind.upper()} {item.path}{suffix}")
        body = "\n\n".join(item.diff for item in plan.files if item.diff)
        if body:
            lines.extend(["", body[:40000]])
        return "\n".join(lines)
