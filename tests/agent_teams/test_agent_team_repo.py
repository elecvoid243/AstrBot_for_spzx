"""Repo-method tests against a real on-disk SQLite database."""

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_team_crud_roundtrip(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()

    await db.create_agent_team(
        team_id="t1",
        owner_username="alice",
        name="team",
        coordinator_member_id="m1",
        members=[{"member_id": "m1", "name": "lead", "session_id": "c1"}],
        config={},
    )
    team = await db.get_agent_team("t1")
    assert team is not None and team.owner_username == "alice"
    assert len(await db.get_agent_teams_by_owner("alice")) == 1
    assert await db.get_agent_teams_by_owner("bob") == []

    await db.update_agent_team(
        "t1", name="renamed", config={"failure_policy": "auto_skip"}
    )
    team = await db.get_agent_team("t1")
    assert team.name == "renamed"
    assert team.config == {"failure_policy": "auto_skip"}
    # None values must not overwrite
    await db.update_agent_team("t1", name=None)
    assert (await db.get_agent_team("t1")).name == "renamed"

    await db.delete_agent_team("t1")
    assert await db.get_agent_team("t1") is None


@pytest.mark.asyncio
async def test_run_lifecycle_queries(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    for run_id, status in (("r1", "completed"), ("r2", "running")):
        await db.create_agent_team_run(
            run_id=run_id,
            team_id="t1",
            workflow_id=None,
            mode="dag",
            input="x",
            status=status,
            graph_snapshot={},
            node_states={},
            rounds=[],
        )
    active = await db.get_active_agent_team_run("t1")
    assert active is not None and active.run_id == "r2"

    await db.update_agent_team_run(
        "r2", status="paused", node_states={"n1": {"status": "running"}}
    )
    active = await db.get_active_agent_team_run("t1")
    assert active is not None and active.status == "paused"
    assert (
        len(
            await db.get_agent_team_runs_by_status(["running", "paused", "interrupted"])
        )
        == 1  # r2 only; r1 is completed (terminal, excluded)
    )
    assert len(await db.get_agent_team_runs_by_status(["completed"])) == 1
    runs = await db.get_agent_team_runs_by_team("t1")
    assert {r.run_id for r in runs} == {"r1", "r2"}
