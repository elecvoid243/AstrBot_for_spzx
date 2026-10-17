"""Builtin apply_patch tool: atomic multi-file patch application."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.tools import fs_access
from astrbot.core.tools.computer_tools.fs import (
    _READONLY_WRITE_ERROR,
    _is_forbidden_path,
    _is_user_blacklisted,
    _normalize_rw_path,
    _read_file_bytes,
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
    ) -> ToolExecResult:
        if fs_access.get_mode(context) is fs_access.FileAccessMode.READONLY:
            return _READONLY_WRITE_ERROR

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

        def read(path: str) -> bytes | None:
            real = resolved.get(path, path)
            try:
                return _read_file_bytes(real)
            except FileNotFoundError:
                return None

        try:
            plan = plan_patch(entries, read)
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
        return await self._commit(plan, resolved, context=context, local_env=local_env)

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

    async def _commit(self, plan, resolved, *, context, local_env) -> str:
        return "Error: commit path not implemented"
