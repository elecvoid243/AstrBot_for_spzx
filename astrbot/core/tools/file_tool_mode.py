"""Selection of the model-visible file write tool group.

Two mutually exclusive groups are supported:

- ``legacy``: astrbot_file_write_tool + astrbot_file_edit_tool
- ``patch``: astrbot_apply_patch

The mode selects model-visible and role-ready tools; all classes stay registered so that
historical configs, dashboard entries, and internal callers keep working.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

FILE_TOOL_MODE_KEY = "file_tool_mode"
FILE_TOOL_MODE_LEGACY = "legacy"
FILE_TOOL_MODE_PATCH = "patch"
DEFAULT_FILE_TOOL_MODE = FILE_TOOL_MODE_PATCH

_LEGACY_TOOLS = ("astrbot_file_write_tool", "astrbot_file_edit_tool")
_PATCH_TOOLS = ("astrbot_apply_patch",)
_warned_modes: set[str] = set()


def resolve_file_tool_mode(provider_settings: dict | None) -> str:
    """Return the configured mode, falling back to ``patch``.

    Args:
        provider_settings: The ``provider_settings`` config section.

    Returns:
        ``"legacy"`` or ``"patch"``.
    """
    raw = (provider_settings or {}).get(FILE_TOOL_MODE_KEY, DEFAULT_FILE_TOOL_MODE)
    mode = str(raw or DEFAULT_FILE_TOOL_MODE).strip().lower()
    if mode in (FILE_TOOL_MODE_LEGACY, FILE_TOOL_MODE_PATCH):
        return mode
    if mode not in _warned_modes:
        _warned_modes.add(mode)
        logger.warning(
            "Unknown %s=%r; falling back to %s",
            FILE_TOOL_MODE_KEY,
            raw,
            DEFAULT_FILE_TOOL_MODE,
        )
    return DEFAULT_FILE_TOOL_MODE


def file_tool_names(mode: str) -> tuple[str, ...]:
    """Return the file write tool names visible in a resolved mode.

    Args:
        mode: Resolved file tool mode.

    Returns:
        The selected mutually exclusive file write tool group.
    """
    return _PATCH_TOOLS if mode == FILE_TOOL_MODE_PATCH else _LEGACY_TOOLS


def file_tool_classes() -> dict[str, type]:
    """Return name -> tool class for the file write tools.

    Imported lazily to avoid a module-load cycle with ``computer_tools``.

    Returns:
        Registered tool classes indexed by their stable tool names.
    """
    from astrbot.core.tools.computer_tools import (
        ApplyPatchTool,
        FileEditTool,
        FileWriteTool,
    )

    return {
        "astrbot_file_write_tool": FileWriteTool,
        "astrbot_file_edit_tool": FileEditTool,
        "astrbot_apply_patch": ApplyPatchTool,
    }


def file_tool_allowed(name: str, mode: str) -> bool:
    """Whether a tool name is visible in ``mode``.

    Non-file tools are always allowed.

    Args:
        name: Stable registered tool name.
        mode: Resolved file tool mode.

    Returns:
        Whether the mode includes the tool.
    """
    if name in _LEGACY_TOOLS:
        return mode == FILE_TOOL_MODE_LEGACY
    if name in _PATCH_TOOLS:
        return mode == FILE_TOOL_MODE_PATCH
    return True
