"""Schema tests for the agent-team PO tables."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from astrbot.core.db.po import AgentTeam, AgentTeamRun


@pytest.mark.asyncio
async def test_tables_created_and_roundtrip(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    assert {"agent_teams", "agent_team_workflows", "agent_team_runs"} <= set(
        SQLModel.metadata.tables.keys()
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(
            AgentTeam(
                team_id="t1",
                owner_username="alice",
                name="team",
                coordinator_member_id="m1",
                members=[{"member_id": "m1", "name": "lead", "session_id": "c1"}],
                config={"failure_policy": "pause"},
            )
        )
        session.add(
            AgentTeamRun(
                run_id="r1",
                team_id="t1",
                workflow_id=None,
                mode="dag",
                input="do it",
                status="running",
                graph_snapshot={"nodes": [], "edges": []},
                node_states={},
                rounds=[],
            )
        )
        await session.commit()
    async with maker() as session:
        loaded = (
            (
                await session.execute(
                    select(AgentTeamRun).where(AgentTeamRun.run_id == "r1")
                )
            )
            .scalars()
            .one()
        )
        assert loaded.mode == "dag"
        assert loaded.created_at is not None


@pytest.mark.asyncio
async def test_message_scope_upgrade_preserves_rows_and_rolls_back(tmp_path):
    import sqlite3

    from sqlalchemy import event
    from sqlalchemy.schema import CreateTable

    from astrbot.core.db.po import AgentWorkflowMessage
    from astrbot.core.db.sqlite import SQLiteDatabase

    path = tmp_path / "messages.db"
    db = SQLiteDatabase(str(path))
    ddl = str(
        CreateTable(AgentWorkflowMessage.__table__).compile(db.engine.sync_engine)
    )
    ddl = ddl.replace(
        "root_run_id, scope_id, sender_member_id, request_id",
        "root_run_id, sender_member_id, request_id",
    )
    with sqlite3.connect(path) as raw:
        raw.execute(ddl)
        raw.execute(
            "CREATE INDEX ix_agent_workflow_messages_root_run_id ON agent_workflow_messages (root_run_id)"
        )
        raw.execute(
            "CREATE INDEX ix_agent_workflow_messages_recipient_member_id ON agent_workflow_messages (recipient_member_id)"
        )
        raw.execute(
            "INSERT INTO agent_workflow_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-01-01",
                "2026-01-02",
                "saved",
                "root",
                "scope-1",
                "sender",
                "receiver",
                "task",
                "attempt",
                "request",
                '{"text":"saved"}',
                None,
            ),
        )

    def fail_after_drop(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("ALTER TABLE agent_workflow_messages_rebuild"):
            raise RuntimeError("injected upgrade failure")

    event.listen(db.engine.sync_engine, "before_cursor_execute", fail_after_drop)
    with pytest.raises(RuntimeError, match="injected"):
        await db.initialize()
    event.remove(db.engine.sync_engine, "before_cursor_execute", fail_after_drop)
    with sqlite3.connect(path) as raw:
        assert raw.execute(
            "SELECT message_id, payload FROM agent_workflow_messages"
        ).fetchall() == [("saved", '{"text":"saved"}')]
        assert not raw.execute(
            "SELECT name FROM sqlite_master WHERE name='agent_workflow_messages_rebuild'"
        ).fetchall()
    await db.initialize()
    await db.initialize()
    with sqlite3.connect(path) as raw:
        row = raw.execute("SELECT * FROM agent_workflow_messages").fetchone()
        assert row[2:] == (
            "saved",
            "root",
            "scope-1",
            "sender",
            "receiver",
            "task",
            "attempt",
            "request",
            '{"text":"saved"}',
            None,
        )
        raw.execute(
            "INSERT INTO agent_workflow_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-01-01",
                "2026-01-02",
                "second",
                "root",
                "scope-2",
                "sender",
                "receiver",
                "task",
                "attempt",
                "request",
                "{}",
                None,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO agent_workflow_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "2026-01-01",
                    "2026-01-02",
                    "duplicate",
                    "root",
                    "scope-2",
                    "sender",
                    "receiver",
                    "task",
                    "attempt",
                    "request",
                    "{}",
                    None,
                ),
            )
    await db.engine.dispose()


@pytest.mark.asyncio
async def test_message_scope_upgrade_rejects_unknown_structure(tmp_path):
    import sqlite3

    from sqlalchemy.schema import CreateTable

    from astrbot.core.db.po import AgentWorkflowMessage
    from astrbot.core.db.sqlite import SQLiteDatabase

    path = tmp_path / "unknown-messages.db"
    db = SQLiteDatabase(str(path))
    ddl = str(
        CreateTable(AgentWorkflowMessage.__table__).compile(db.engine.sync_engine)
    ).replace(
        "root_run_id, scope_id, sender_member_id, request_id",
        "root_run_id, sender_member_id, request_id",
    )
    with sqlite3.connect(path) as raw:
        raw.execute(ddl)
        raw.execute("ALTER TABLE agent_workflow_messages ADD COLUMN extra TEXT")
    with pytest.raises(RuntimeError, match="Unknown agent_workflow_messages"):
        await db.initialize()
    with sqlite3.connect(path) as raw:
        assert "extra" in [
            column[1]
            for column in raw.execute("PRAGMA table_info(agent_workflow_messages)")
        ]
        assert not raw.execute(
            "SELECT name FROM sqlite_master WHERE name='agent_workflow_messages_rebuild'"
        ).fetchall()
    await db.engine.dispose()
