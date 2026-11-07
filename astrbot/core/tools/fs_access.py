"""Per-conversation file access mode for LLM file tools.

Three modes (docs/superpowers/specs/2026-08-20-file-access-mode-design.md):
- full: status quo — admins are unrestricted.
- readonly: all writes rejected (the spcode "plan" mode merges here).
- workspace: writes restricted to the session workspace, temp dirs,
  configured extra roots, and plugin-registered dynamic roots.

The explicit mode is per-umo and persisted in SharedPreferences. Async
entry points (dashboard reads and side-thread inheritance) hydrate it
lazily; synchronous tool resolution falls back to the config default until
that hydration has happened. The mode is enforced at tool-call time (see
computer_tools/fs.py and shell.py) so it also covers subagent tool calls.

Workspace-mode roots are composed of: the session workspace root, AstrBot
temp dirs, config extra roots, plugin dynamic roots (loaded project dir /
active worktree), and per-umo custom roots edited from the dashboard
(persisted in SharedPreferences, so they survive restarts).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.core import sp
from astrbot.core.utils.astrbot_path import (
    get_astrbot_system_tmp_path,
    get_astrbot_temp_path,
)

if TYPE_CHECKING:
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.astr_agent_context import AstrAgentContext
    from astrbot.core.computer.process_sandbox.base import SandboxSpec

    ToolContext = ContextWrapper[AstrAgentContext]


class FileAccessMode(str, Enum):
    FULL = "full"
    READONLY = "readonly"
    WORKSPACE = "workspace"


_MODE_RESTRICTIVENESS = {
    FileAccessMode.FULL: 2,
    FileAccessMode.WORKSPACE: 1,
    FileAccessMode.READONLY: 0,
}


def is_tightening(previous: FileAccessMode | None, new: FileAccessMode) -> bool:
    """Return whether moving to a new mode removes file access authority.

    Used by callers that must invalidate already-running execution after a
    mode switch. An unknown previous mode counts as tightening for every
    restricted mode, because the effective default may have been wider.

    Args:
        previous: Previously effective mode, or None when unknown.
        new: Mode being applied.

    Returns:
        True when the new mode is strictly more restrictive.
    """
    if previous is None:
        return new is not FileAccessMode.FULL
    return _MODE_RESTRICTIVENESS[new] < _MODE_RESTRICTIVENESS[previous]


_mode_overrides: dict[str, FileAccessMode] = {}
_last_writable: dict[str, FileAccessMode] = {}
_dynamic_roots: dict[str, tuple[Path, ...]] = {}
_custom_roots_cache: dict[str, tuple[Path, ...]] = {}

_CUSTOM_ROOTS_KEY = "fs_access_custom_roots"
_MODE_KEY = "fs_access_mode"


def resolve_default(cfg: dict) -> FileAccessMode:
    """Map a config dict to the default file access mode.

    Args:
        cfg: A full config dict (or AstrBotConfig); unknown values fall
            back to FULL for backward compatibility.

    Returns:
        The configured default mode.
    """
    provider_settings = (cfg or {}).get("provider_settings", {})
    try:
        return FileAccessMode(
            str(provider_settings.get("file_access_default_mode", "full"))
        )
    except ValueError:
        return FileAccessMode.FULL


def get_mode_for_umo(
    umo: str, *, default: FileAccessMode = FileAccessMode.FULL
) -> FileAccessMode:
    """Return the effective mode: explicit override, else the given default."""
    override = _mode_overrides.get(umo)
    return override if override is not None else default


def set_mode_for_umo(umo: str, mode: FileAccessMode) -> None:
    """Set the explicit per-umo mode, remembering the last writable one."""
    if mode is not FileAccessMode.READONLY:
        _last_writable[umo] = mode
    _mode_overrides[umo] = mode


async def load_mode_for_umo(umo: str) -> FileAccessMode | None:
    """Hydrate one umo's persisted explicit mode into memory.

    Called by async entry points that have storage access. Synchronous tool
    paths keep using whatever override is already cached, falling back to the
    config default otherwise.

    Args:
        umo: Unified message origin.

    Returns:
        The loaded mode, or None when no valid override is stored.
    """
    cached = _mode_overrides.get(umo)
    if cached is not None:
        return cached
    try:
        raw = await sp.session_get(umo, _MODE_KEY, None)
    except Exception as exc:
        logger.warning("Failed to load file access mode for %s: %s", umo, exc)
        return None
    if not isinstance(raw, str):
        return None
    try:
        mode = FileAccessMode(raw)
    except ValueError:
        return None
    _mode_overrides[umo] = mode
    if mode is not FileAccessMode.READONLY:
        _last_writable[umo] = mode
    return mode


async def set_mode_for_umo_persisted(umo: str, mode: FileAccessMode) -> None:
    """Apply and persist the explicit per-umo mode.

    Args:
        umo: Unified message origin.
        mode: New explicit mode.
    """
    set_mode_for_umo(umo, mode)
    try:
        await sp.session_put(umo, _MODE_KEY, mode.value)
    except Exception as exc:
        logger.warning("Failed to persist file access mode for %s: %s", umo, exc)


async def clear_mode_persisted_for_umo(umo: str) -> None:
    """Drop the in-memory and persisted explicit mode for one umo.

    Args:
        umo: Unified message origin.
    """
    clear_mode_for_umo(umo)
    try:
        await sp.session_remove(umo, _MODE_KEY)
    except Exception as exc:
        logger.warning(
            "Failed to clear persisted file access mode for %s: %s", umo, exc
        )


def restore_writable_for_umo(umo: str, *, default: FileAccessMode) -> FileAccessMode:
    """Leave readonly mode, restoring the last writable mode (never readonly)."""
    mode = _last_writable.get(umo, default)
    if mode is FileAccessMode.READONLY:
        mode = (
            default if default is not FileAccessMode.READONLY else FileAccessMode.FULL
        )
    _mode_overrides[umo] = mode
    return mode


def clear_mode_for_umo(umo: str) -> None:
    """Drop the explicit override (falls back to the config default)."""
    _mode_overrides.pop(umo, None)
    _last_writable.pop(umo, None)


def count_readonly_overrides() -> int:
    """Count umos whose explicit override is readonly."""
    return sum(1 for m in _mode_overrides.values() if m is FileAccessMode.READONLY)


def reset() -> None:
    """Clear all state (test fixture / shutdown)."""
    _mode_overrides.clear()
    _last_writable.clear()
    _dynamic_roots.clear()
    _custom_roots_cache.clear()


def set_dynamic_roots(umo: str, roots: Sequence[Path | str] | None) -> None:
    """Register per-umo extra writable roots (e.g. the active git worktree).

    Args:
        umo: Unified message origin.
        roots: Absolute roots to allow, or None/empty to clear.
    """
    if not roots:
        _dynamic_roots.pop(umo, None)
        return
    _dynamic_roots[umo] = tuple(
        Path(r).expanduser().resolve(strict=False) for r in roots
    )


def get_dynamic_roots(umo: str) -> tuple[Path, ...]:
    return _dynamic_roots.get(umo, ())


def _normalize_roots(roots: Sequence[Path | str] | None) -> tuple[Path, ...]:
    """Trim, resolve, and case-insensitively dedupe a root list."""
    normalized: list[Path] = []
    seen: set[str] = set()
    for item in roots or []:
        text = str(item).strip()
        if not text:
            continue
        path = Path(text).expanduser().resolve(strict=False)
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(path)
    return tuple(normalized)


async def get_custom_roots(umo: str) -> tuple[Path, ...]:
    """Per-umo user-defined whitelist roots (persisted in SharedPreferences).

    Args:
        umo: Unified message origin.

    Returns:
        Normalized custom roots; empty when none were configured. The
        first read per umo hydrates the in-memory cache from storage.
    """
    cached = _custom_roots_cache.get(umo)
    if cached is None:
        try:
            raw = await sp.session_get(umo, _CUSTOM_ROOTS_KEY, [])
        except Exception:
            # Storage unavailable: fail closed with no custom roots rather
            # than breaking every workspace-mode tool call.
            raw = []
        cached = _normalize_roots(raw if isinstance(raw, list) else [])
        _custom_roots_cache[umo] = cached
    return cached


async def set_custom_roots(
    umo: str, roots: Sequence[Path | str] | None
) -> tuple[Path, ...]:
    """Replace the umo's custom roots and persist them.

    Args:
        umo: Unified message origin.
        roots: Raw root strings/paths; empty entries are dropped and the
            list is deduplicated case-insensitively.

    Returns:
        The normalized custom roots now in effect.
    """
    normalized = _normalize_roots(roots)
    _custom_roots_cache[umo] = normalized
    await sp.session_put(umo, _CUSTOM_ROOTS_KEY, [str(p) for p in normalized])
    return normalized


def configured_extra_roots(cfg: dict) -> tuple[Path, ...]:
    """Read provider_settings.file_access_extra_roots from a config dict."""
    raw = (cfg or {}).get("provider_settings", {}).get("file_access_extra_roots") or []
    roots: list[Path] = []
    for item in raw:
        text = str(item).strip()
        if text:
            roots.append(Path(text).expanduser().resolve(strict=False))
    return tuple(roots)


def _config_for(context: ToolContext) -> dict:
    cfg = context.context.context.get_config(
        umo=context.context.event.unified_msg_origin
    )
    return cfg if isinstance(cfg, dict) else {}


def get_mode(context: ToolContext) -> FileAccessMode:
    """Resolve the effective mode for a tool-call context."""
    override = _mode_overrides.get(context.context.event.unified_msg_origin)
    if override is not None:
        return override
    return resolve_default(_config_for(context))


async def extra_write_roots(context: ToolContext) -> tuple[Path, ...]:
    """Configured + plugin dynamic + per-umo custom roots for this context."""
    umo = context.context.event.unified_msg_origin
    return (
        configured_extra_roots(_config_for(context))
        + get_dynamic_roots(umo)
        + await get_custom_roots(umo)
    )


async def write_roots(
    context: ToolContext,
    *,
    current_workspace_root: Path | None = None,
) -> tuple[Path, ...]:
    """All roots writable in workspace mode for this context."""
    # Lazy import: computer_tools.__init__ pulls in fs.py, which imports this
    # module — a top-level import would be circular.
    from .computer_tools.util import workspace_root_for_context

    root = current_workspace_root or await workspace_root_for_context(context)
    return (
        Path(root).resolve(strict=False),
        Path(get_astrbot_system_tmp_path()).resolve(strict=False),
        Path(get_astrbot_temp_path()).resolve(strict=False),
        *await extra_write_roots(context),
    )


def _is_within(path: Path, roots: tuple[Path, ...]) -> bool:
    """Case-insensitive (Windows) containment check after normalization."""
    candidate = Path(os.path.normcase(str(path)))
    for root in roots:
        normalized = Path(os.path.normcase(str(root)))
        if candidate == normalized or candidate.is_relative_to(normalized):
            return True
    return False


async def sandbox_spec_for_context(
    context: ToolContext,
    *,
    current_workspace_root: Path | None = None,
) -> SandboxSpec | None:
    """Build the process-sandbox policy for a local shell or Python call.

    Windows-only for now: other platforms keep their existing host execution.
    FULL mode stays unsandboxed for backward compatibility, so this returns
    None for FULL and for non-Windows hosts. WORKSPACE grants write access to
    the session workspace plus the configured/plugin/custom extra roots;
    READONLY grants no writable root (the backend still allows a private temp
    directory owned by that single execution).

    Args:
        context: Tool-call context used to resolve the mode and roots.
        current_workspace_root: Pre-resolved workspace root, if known.

    Returns:
        The sandbox policy, or None when the call should keep host execution.
    """
    if sys.platform != "win32":
        return None
    mode = get_mode(context)
    if mode is FileAccessMode.FULL:
        return None

    from astrbot.core.computer.process_sandbox.base import SandboxSpec

    from .computer_tools.util import workspace_root_for_context

    workspace = Path(
        current_workspace_root or await workspace_root_for_context(context)
    ).resolve(strict=False)
    writable_roots: tuple[Path, ...] = ()
    if mode is FileAccessMode.WORKSPACE:
        writable_roots = tuple(
            Path(root).resolve(strict=False)
            for root in await extra_write_roots(context)
            if Path(root).resolve(strict=False) != workspace
        )
    return SandboxSpec(
        workspace=workspace,
        workspace_writable=mode is FileAccessMode.WORKSPACE,
        filesystem_scope="host",
        writable_roots=writable_roots,
        readable_roots=(),
        allow_network=False,
    )


async def assert_writable(
    path: str,
    context: ToolContext,
    *,
    current_workspace_root: Path | None = None,
) -> None:
    """Raise PermissionError when `path` may not be written under the mode.

    Args:
        path: Target path (absolute, or relative to the workspace root).
        context: Tool-call context used to resolve mode and roots.
        current_workspace_root: Pre-resolved workspace root, if known.

    Raises:
        PermissionError: In readonly mode, or in workspace mode when the
            path is outside all writable roots.
    """
    mode = get_mode(context)
    if mode is FileAccessMode.FULL:
        return
    resolved = Path(path).expanduser().resolve(strict=False)
    if mode is FileAccessMode.READONLY:
        raise PermissionError(
            "Write access is disabled: this conversation is in readonly "
            f"(plan) file access mode. Blocked path: {resolved}."
        )
    if not resolved.is_absolute() or not Path(path).is_absolute():
        from .computer_tools.util import workspace_root_for_context

        root = current_workspace_root or await workspace_root_for_context(context)
        resolved = (root / resolved).resolve(strict=False)
    roots = await write_roots(context, current_workspace_root=current_workspace_root)
    if not _is_within(resolved, roots):
        allowed = ", ".join(sorted(str(r) for r in roots))
        raise PermissionError(
            "Write access is restricted to the workspace whitelist. "
            f"Allowed directories: {allowed}. Blocked path: {resolved}."
        )
