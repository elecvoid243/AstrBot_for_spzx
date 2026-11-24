"""Bridge the tray administrator's reminder inbox to a modeless Windows panel."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import webbrowser
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

from sqlalchemy import func, update
from sqlmodel import col, select

from astrbot import logger
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import ChatUIProject, DashboardAccount, ProjectTodo


class DesktopReminders:
    def __init__(self, db: BaseDatabase, webui_url: Callable[[], str]) -> None:
        self.db = db
        self.webui_url = webui_url
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.displayed: list[dict] | None = None

    async def sync(self, notices: list[dict]) -> None:
        """Update the one desktop window, starting it lazily on the first reminder.

        Args:
            notices: Current unread occurrences belonging to the bound administrator.
        """
        if not notices:
            if self.process is not None:
                await self.close()
            return
        if self.process is None or self.process.returncode is not None:
            if self.reader:
                await self.reader
            source = (
                Path(__file__).with_name("desktop_reminders.ps1").read_text("utf-8")
            )
            command = base64.b64encode(source.encode("utf-16le")).decode("ascii")
            executable = (
                Path(os.environ["SystemRoot"])
                / "System32/WindowsPowerShell/v1.0/powershell.exe"
            )
            self.process = await asyncio.create_subprocess_exec(
                str(executable),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-EncodedCommand",
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.displayed = None
            self.reader = asyncio.create_task(
                self._actions(self.process), name="desktop-reminder-actions"
            )
        if self.displayed == notices:
            return
        # The script is fixed; all project and card text travels strictly as JSON data.
        data = json.dumps(notices, ensure_ascii=True).encode("ascii") + b"\n"
        try:
            self.process.stdin.write(data)
            await asyncio.wait_for(self.process.stdin.drain(), 5)
            self.displayed = notices
        except (OSError, TimeoutError):
            await self.close()
            raise

    async def _actions(self, child: asyncio.subprocess.Process) -> None:
        """Handle explicit desktop actions against the displayed occurrence only.

        Args:
            child: Windows panel process that generated the actions.
        """
        while line := await child.stdout.readline():
            try:
                action = json.loads(line)
                notice = next(
                    (
                        n
                        for n in self.displayed or ()
                        if n["id"] == action["id"] and n["unread_at"] == action["at"]
                    ),
                    None,
                )
                if not notice:
                    continue
                if action["action"] == "open":
                    query = urlencode(
                        {
                            "boardProject": notice["project_id"],
                            "boardCard": notice["id"],
                        }
                    )
                    await asyncio.to_thread(
                        webbrowser.open, f"{self.webui_url()}/#/chat?{query}"
                    )
                elif action["action"] == "close":
                    admin_projects = (
                        select(ChatUIProject.project_id)
                        .join(
                            DashboardAccount,
                            ChatUIProject.creator
                            == func.coalesce(
                                DashboardAccount.legacy_owner,
                                DashboardAccount.account_id,
                            ),
                        )
                        .where(
                            DashboardAccount.bootstrap_key == "dashboard",
                            DashboardAccount.role == "admin",
                            col(DashboardAccount.enabled).is_(True),
                        )
                    )
                    async with self.db.get_db() as session, session.begin():
                        await session.execute(
                            update(ProjectTodo)
                            .where(
                                ProjectTodo.id == notice["id"],
                                ProjectTodo.unread_at == notice["unread_at"],
                                col(ProjectTodo.project_id).in_(admin_projects),
                            )
                            .values(unread_at=None)
                        )
            except Exception:
                self.displayed = None
                logger.exception("Failed to apply a desktop reminder action")

    async def close(self) -> None:
        """Release the panel and its IPC reader when the tray exits."""
        child = self.process
        if child and child.returncode is None:
            child.stdin.close()
            try:
                await asyncio.wait_for(child.wait(), 2)
            except TimeoutError:
                child.kill()
                await child.wait()
        if self.reader:
            await self.reader
        self.process = None
        self.reader = None
        self.displayed = None
