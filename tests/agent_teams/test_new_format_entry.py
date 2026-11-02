"""New-format public history and removal of retired execution endpoints."""

import copy
from types import SimpleNamespace

import httpx
import pytest
from sqlmodel import select

from astrbot.core.agent_teams.history import RunHistoryService
from astrbot.core.db.po import AgentTeam, AgentWorkflowRun
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.router import build_api_router
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_history_pages_owned_root_runs_without_reading_or_removing_old_data(
    workflow_env,
):
    """Keep pagination, ownership and original results on the actual run path.

    Args:
        workflow_env: Real database and public workflow service with execution ports.
    """
    from fastapi import FastAPI

    env = workflow_env
    runs = []
    for _ in range(2):
        started = await env.service.start_run(
            "alice", env.asset["asset_id"], {"version": 1}
        )
        runs.append(await completed_run(env.service, started["run_id"]))
    async with env.db.get_db() as session:
        original = await session.get(AgentWorkflowRun, runs[0]["run_id"])
        assert original is not None
        for identity, owner, parent in (
            ("hidden-child", "alice", original.run_id),
            ("other-owner", "bob", None),
        ):
            snapshot = copy.deepcopy(original.snapshot)
            if parent:
                snapshot["parent_run_id"] = parent
            session.add(
                AgentWorkflowRun(
                    run_id=identity,
                    owner_username=owner,
                    snapshot=snapshot,
                    state=copy.deepcopy(original.state),
                    status="completed",
                )
            )
        session.add(
            AgentTeam(
                team_id="retired-team",
                owner_username="alice",
                name="Preserved original data",
                coordinator_member_id="old-lead",
                config={},
                members=[],
            )
        )
        await session.commit()
    app = FastAPI()
    app.state.services = SimpleNamespace(
        workflow_assets=env.service,
        run_history=RunHistoryService(env.db, env.service),
    )
    app.include_router(build_api_router())
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get("/api/v1/agent_history", params={"limit": 1})
        assert first.status_code == 200
        page = first.json()["data"]
        assert page["next_offset"] == 1
        second = (
            await client.get(
                "/api/v1/agent_history", params={"limit": 1, "offset": 1}
            )
        ).json()["data"]
        assert second["next_offset"] is None
        records = page["records"] + second["records"]
        assert {item["source"]["id"] for item in records} == {
            run["run_id"] for run in runs
        }
        assert all(item["status"] == "completed" for item in records)
        assert not any("legacy" in str(item) for item in records)
        for run in runs:
            result = await client.get(
                f"/api/v1/workflow_runs/{run['run_id']}/result"
            )
            assert result.status_code == 200
            assert result.json()["data"] == {"text": "Complete report. " * 1000}
        for path in (
            "/api/v1/agent_teams",
            "/api/agent_teams",
            "/api/v1/agent_teams/retired-team/migration/preview",
            "/api/v1/agent_history/legacy_run/retired-run",
            "/api/v1/agent_history/legacy_run/retired-run/members/old-lead",
        ):
            assert (await client.get(path)).status_code == 404
        for path in (
            "/api/v1/agent_teams",
            "/api/agent_teams/retired-team/runs",
            "/api/v1/agent_teams/retired-team/runs",
            "/api/v1/agent_teams/retired-team/migration",
            "/api/v1/agent_teams/runs/retired-run/resume",
        ):
            assert (await client.post(path, json={})).status_code == 404
    async with env.db.get_db() as session:
        preserved = (
            await session.execute(
                select(AgentTeam).where(AgentTeam.team_id == "retired-team")
            )
        ).scalar_one_or_none()
        assert preserved is not None
        assert preserved.name == "Preserved original data"
