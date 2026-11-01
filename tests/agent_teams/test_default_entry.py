"""Final default entry behavior through actual application services and HTTP."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.api.agent_teams import legacy_router, router
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.services.agent_team_run_service import AgentTeamRunService


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["/api/v1/agent_teams", "/api/agent_teams"])
async def test_retired_legacy_start_preserves_history_without_creating_a_run(
    tmp_path, prefix
):
    """Keep historical definitions readable while directing new work to assets.

    Args:
        tmp_path: Isolated persistent database directory.
        prefix: Versioned entry or its previous alias.
    """
    db = SQLiteDatabase(str(tmp_path / "entry.db"))
    await db.create_agent_team(
        team_id="original-team",
        owner_username="alice",
        name="Original team",
        coordinator_member_id="lead",
        config={},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "session_id": "original-lead",
                "umo": "webchat:FriendMessage:original-lead",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "writer",
                "name": "Writer",
                "session_id": "original-writer",
                "umo": "webchat:FriendMessage:original-writer",
                "system_prompt": "Write.",
            },
        ],
    )
    legacy = AgentTeamRunService(db, None)
    app = FastAPI()
    app.state.services = SimpleNamespace(agent_team_runs=legacy)
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    app.include_router(router, prefix="/api/v1")
    app.include_router(legacy_router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"{prefix}/original-team/runs",
                json={"mode": "auto", "input": "New request"},
            )
            assert response.status_code == 410
            assert "asset" in response.json()["message"].lower()
            history = await client.get(f"{prefix}/original-team/runs")
            assert history.status_code == 200
            assert history.json()["data"]["runs"] == []
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["dag", "auto"])
@pytest.mark.parametrize("status", ["running", "paused", "interrupted"])
async def test_legacy_history_without_its_original_owner_cannot_resume(
    tmp_path, mode, status
):
    """Reject legacy replay without changing saved history or its original result.

    Args:
        tmp_path: Isolated persistent database directory.
        mode: Original legacy orchestration mode.
        status: Unfinished status left by the previous owner.
    """
    db = SQLiteDatabase(str(tmp_path / "history.db"))
    await db.create_agent_team(
        team_id="original-team",
        owner_username="alice",
        name="Original team",
        coordinator_member_id="lead",
        config={},
        members=[],
    )
    await db.create_agent_team_run(
        run_id="original-run",
        team_id="original-team",
        workflow_id=None,
        mode=mode,
        input="Original input",
        status=status,
        graph_snapshot={"nodes": [], "edges": []},
        node_states={"research": {"status": "done", "result": "Original evidence"}},
        rounds=[],
    )
    legacy = AgentTeamRunService(db, None)
    before = await legacy.get_history_run("alice", "original-run")
    app = FastAPI()
    app.state.services = SimpleNamespace(agent_team_runs=legacy)
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    app.include_router(router, prefix="/api/v1")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/agent_teams/runs/original-run/resume")
            assert response.status_code == 410
        assert await legacy.get_history_run("alice", "original-run") == before
        assert before["history_status"] == "interrupted"
    finally:
        await db.engine.dispose()
