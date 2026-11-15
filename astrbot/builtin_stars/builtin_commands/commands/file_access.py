"""File access mode command (``/mode``)."""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.core.computer.computer_client import shutdown_local_execution_for_owner
from astrbot.core.tools import fs_access

_MODES: dict[str, fs_access.FileAccessMode] = {
    "full": fs_access.FileAccessMode.FULL,
    "readonly": fs_access.FileAccessMode.READONLY,
    "workspace": fs_access.FileAccessMode.WORKSPACE,
}

_LABELS: dict[fs_access.FileAccessMode, str] = {
    fs_access.FileAccessMode.FULL: "完全权限",
    fs_access.FileAccessMode.READONLY: "只读模式",
    fs_access.FileAccessMode.WORKSPACE: "工作区写入",
}

_USAGE = "可用：/mode full | readonly | workspace"


class FileAccessCommands:
    def __init__(self, context) -> None:
        self.context = context

    async def set_mode(self, event: AstrMessageEvent, mode: str | None = None) -> None:
        """Show or change the file access mode for the current conversation.

        Args:
            event: Command event identifying the session.
            mode: One of ``full`` / ``readonly`` / ``workspace``. Omitted to
                report the current mode.
        """
        umo = event.unified_msg_origin
        cfg = self.context.get_config(umo=umo)
        default = fs_access.resolve_default(cfg)
        await fs_access.load_mode_for_umo(umo)
        current = fs_access.get_mode_for_umo(umo, default=default)

        raw = (mode or "").strip().lower()
        if not raw:
            event.set_result(
                MessageEventResult().message(
                    f"当前文件访问模式：{_LABELS[current]}（{current.value}）\n{_USAGE}"
                )
            )
            return

        target = _MODES.get(raw)
        if target is None:
            event.set_result(
                MessageEventResult().message(f"未知的文件访问模式：{raw}\n{_USAGE}")
            )
            return
        if target is current:
            event.set_result(
                MessageEventResult().message(
                    f"当前已是{_LABELS[target]}（{target.value}），未做改动。"
                )
            )
            return

        await fs_access.set_mode_for_umo_persisted(umo, target)
        if fs_access.is_tightening(current, target):
            # A stricter mode invalidates already-running local execution:
            # otherwise a shell or Python process started under the old mode
            # could keep writing after the switch.
            try:
                await shutdown_local_execution_for_owner(umo)
            except Exception:
                logger.warning(
                    "[fs_access] failed to stop local execution for %s",
                    umo,
                    exc_info=True,
                )
        event.set_result(
            MessageEventResult().message(
                f"✅ 文件访问模式已切换为：{_LABELS[target]}（{target.value}）"
            )
        )
