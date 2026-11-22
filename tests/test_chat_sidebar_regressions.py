"""Exercise sidebar ordering and branch metadata across database lifecycles."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from astrbot.core.backup.exporter import AstrBotExporter
from astrbot.core.backup.importer import AstrBotImporter
from astrbot.core.db.po import PlatformSession
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.chat_service import ChatService, ChatServiceError

DIRECT_EDGE = {"source_session_id": "parent", "source_message_id": 22}


@pytest_asyncio.fixture
async def sidebar_db(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "sidebar.db"))
    try:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        async with db.get_db() as session, session.begin():
            session.add_all(
                PlatformSession(
                    session_id=sid,
                    display_name=sid,
                    creator=owner,
                    platform_id="webchat",
                    updated_at=base + timedelta(days=4 - index),
                )
                for index, (sid, owner) in enumerate(
                    [
                        ("parent", "alice"),
                        ("child", "alice"),
                        ("root", "alice"),
                        ("foreign", "bob"),
                    ]
                )
            )
        for kind, source, message_id in (
            ("branch_info", "root", 11),
            ("branch_info", "parent", 22),
            ("bot", "root", 33),
        ):
            await db.insert_platform_message_history(
                platform_id="webchat",
                user_id="child",
                sender_id="bot",
                sender_name="bot",
                content={
                    "type": kind,
                    "source_session_id": source,
                    "source_message_id": message_id,
                    "marker": "branch_info",
                },
            )
        yield db
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_v1_backfill_repairs_nested_branches(sidebar_db):
    await sidebar_db.upsert_session_branch_relation("child", "root", 11)
    async with sidebar_db.engine.begin() as conn:
        await conn.execute(text("PRAGMA user_version = 1"))
    await sidebar_db.initialize()

    assert await sidebar_db.get_branch_relations() == {"child": DIRECT_EDGE}
    async with sidebar_db.engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA user_version"))).scalar_one() == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_backup", [False, True])
async def test_full_backup_preserves_branch_relations(
    sidebar_db, tmp_path, legacy_backup
):
    await sidebar_db.upsert_session_branch_relation("child", "parent", 22)
    exported = await AstrBotExporter(sidebar_db)._export_main_database()
    assert len(exported["session_branch_relations"]) == 1
    if legacy_backup:
        del exported["session_branch_relations"]

    target = SQLiteDatabase(str(tmp_path / "restored.db"))
    try:
        await target.upsert_session_branch_relation("stale", "parent", 11)
        importer = AstrBotImporter(target)
        async with target.get_db() as session, session.begin():
            await importer._clear_main_db(session)
            await importer._import_main_database(exported, session)
        await target.initialize()

        assert await target.get_branch_relations() == {"child": DIRECT_EDGE}
        async with target.engine.connect() as conn:
            assert (
                await conn.execute(
                    text("SELECT count(*) FROM session_branch_relations")
                )
            ).scalar_one() == 1
    finally:
        await target.engine.dispose()


@pytest.mark.asyncio
async def test_sidebar_snapshot_keeps_edges_and_sessions_that_move_between_pages(
    sidebar_db,
):
    await sidebar_db.upsert_session_branch_relation("child", "parent", 22)
    await sidebar_db.upsert_session_branch_relation("foreign", "parent", 33)
    service = ChatService(
        sidebar_db,
        SimpleNamespace(
            conversation_manager=None,
            platform_message_history_manager=None,
            umop_config_router=None,
        ),
    )
    first = await service.get_sidebar_session_page("alice", "webchat", 1, "start")
    assert first["items"][0]["session_id"] == "parent"
    assert first["items"][0]["branches"] == [
        {"session_id": "child", "display_name": "child"}
    ]
    cursor = first["next_cursor"]
    assert (await service.get_sidebar_session_page("bob", "webchat", 2, cursor))[
        "expired"
    ]

    await sidebar_db.update_platform_session("root", display_name="Updated elsewhere")
    rest = await service.get_sidebar_session_page("alice", "webchat", 2, cursor)
    assert [row["session_id"] for row in rest["items"]] == ["child", "root"]
    assert rest["items"][0]["branch_source"] == {
        "session_id": "parent",
        "message_id": 22,
    }
    assert rest["next_cursor"] is None
    with pytest.raises(ChatServiceError, match="Invalid page"):
        await service.get_sessions("alice", "webchat", page_size=201)
    with pytest.raises(ChatServiceError, match="Invalid page_size"):
        await service.get_sidebar_session_page("alice", "webchat", 201, "start")
