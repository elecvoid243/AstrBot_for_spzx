"""Regression coverage for UTC history cutoffs on non-UTC hosts."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import astrbot.core.db.sqlite as sqlite_module
from astrbot.core.db.po import PlatformMessageHistory
from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_delete_platform_message_offset_compares_in_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The history cutoff must match the UTC wall time stored in created_at.

    Args:
        tmp_path: Temporary directory for the database file.
        monkeypatch: Fixture used to simulate a host in a non-UTC timezone.
    """
    now_utc = datetime.now(timezone.utc)
    inside_window = (now_utc - timedelta(hours=20)).replace(tzinfo=None)
    outside_window = (now_utc - timedelta(hours=30)).replace(tzinfo=None)

    db = SQLiteDatabase(str(tmp_path / "history.db"))
    await db.initialize()
    try:
        async with db.get_db() as session:
            async with session.begin():
                for created_at in (inside_window, outside_window):
                    session.add(
                        PlatformMessageHistory(
                            platform_id="p1",
                            user_id="u1",
                            content={},
                            created_at=created_at,
                        )
                    )

        class LocalTime(datetime):
            """Simulate a host whose local timezone is UTC+8."""

            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return now_utc.replace(tzinfo=None) + timedelta(hours=8)
                return now_utc.astimezone(tz)

        monkeypatch.setattr(sqlite_module, "datetime", LocalTime)
        await db.delete_platform_message_offset("p1", "u1", offset_sec=86400)

        rows = await db.get_platform_message_history("p1", "u1")
        assert [row.created_at for row in rows] == [outside_window]
    finally:
        await db.engine.dispose()
