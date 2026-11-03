"""Verify the todo index upgrade preserves existing requests."""

import pytest
from sqlalchemy import text

from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_todo_index_upgrade_preserves_rows_and_allows_multiple_requests(tmp_path):
    path = tmp_path / "todos.db"
    db = SQLiteDatabase(str(path))
    await db.initialize()
    async with db.engine.begin() as conn:
        await conn.execute(text("DROP INDEX ix_agent_workflow_todos_task_run_id"))
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX ix_agent_workflow_todos_task_run_id ON agent_workflow_todos (task_run_id)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO agent_workflow_todos (todo_id, root_run_id, task_run_id, data, created_at, updated_at) VALUES ('one', 'root', 'task', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    await db.engine.dispose()
    reopened = SQLiteDatabase(str(path))
    await reopened.initialize()
    await reopened.engine.dispose()
    again = SQLiteDatabase(str(path))
    await again.initialize()
    async with again.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_workflow_todos (todo_id, root_run_id, task_run_id, data, created_at, updated_at) VALUES ('two', 'root', 'task', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        rows = (
            (
                await conn.execute(
                    text("SELECT todo_id FROM agent_workflow_todos ORDER BY todo_id")
                )
            )
            .scalars()
            .all()
        )
        assert rows == ["one", "two"]
    await again.engine.dispose()


@pytest.mark.asyncio
async def test_todo_index_upgrade_rejects_unexpected_unique_index(tmp_path):
    path = tmp_path / "unexpected.db"
    db = SQLiteDatabase(str(path))
    await db.initialize()
    async with db.engine.begin() as conn:
        await conn.execute(text("DROP INDEX ix_agent_workflow_todos_task_run_id"))
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX custom_unique_task ON agent_workflow_todos (task_run_id)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO agent_workflow_todos (todo_id, root_run_id, task_run_id, data, created_at, updated_at) VALUES ('one', 'root', 'task', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    await db.engine.dispose()
    reopened = SQLiteDatabase(str(path))
    with pytest.raises(
        RuntimeError, match="Unexpected unique todo task index: custom_unique_task"
    ):
        await reopened.initialize()
    async with reopened.engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT todo_id FROM agent_workflow_todos"))
        ).scalar_one() == "one"
        indexes = (
            await conn.execute(text("PRAGMA index_list(agent_workflow_todos)"))
        ).all()
        assert any(row[1] == "custom_unique_task" and row[2] for row in indexes)
    await reopened.engine.dispose()
