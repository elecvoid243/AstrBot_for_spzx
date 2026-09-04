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
