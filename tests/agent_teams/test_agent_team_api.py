"""API smoke tests over a minimal FastAPI app with dependency overrides."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

import astrbot.dashboard.api.agent_teams as mod
from astrbot.dashboard.services.agent_team_run_service import RunEventBus
from astrbot.dashboard.services.agent_team_service import AgentTeamsServiceError

# Event history replayed to every new SSE subscriber (Task 7 bus, shared).
_BUS = RunEventBus()
_BUS.emit({"type": "run_started", "run_id": "r1"})
_BUS.emit({"type": "dag_progress", "done": 0, "total": 2})


class FakeTeamSvc:
    # Class-level call recorder shared by the per-test service instances.
    calls: list[tuple] = []

    async def create_team(self, username, payload):
        assert username == "alice"
        return {"team_id": "t1", "name": payload.get("name")}

    async def list_teams(self, username):
        return {"teams": []}

    async def create_workflow(self, username, team_id, payload):
        raise AgentTeamsServiceError(
            "工作流校验失败（1 项）",
            field_errors=[
                {
                    "path": "nodes.n1.task",
                    "code": "REQUIRED",
                    "message": "节点 'n1' 缺少任务模板",
                }
            ],
        )

    async def update_workflow(self, username, team_id, workflow_id, payload):
        FakeTeamSvc.calls.append(("update", username, team_id, workflow_id, payload))
        return {"workflow_id": workflow_id, "team_id": team_id}

    async def delete_workflow(self, username, team_id, workflow_id):
        FakeTeamSvc.calls.append(("delete", username, team_id, workflow_id))
        return {"message": "工作流已删除"}


class FakeRunSvc:
    # Class-level call recorder for the run-service routes under test.
    calls: list[tuple] = []

    async def start_run(self, username, team_id, payload):
        if payload.get("input") == "conflict":
            raise AgentTeamsServiceError("该团队已有 active run，无法重复启动")
        return {"run_id": "r1", "status": "running"}

    async def pause_run(self, username, run_id):
        assert username == "alice" and run_id == "r1"
        return {"message": "已暂停"}

    async def interrupt_node(self, username, run_id, member_id):
        assert username == "alice"
        if run_id == "rmissing":
            raise AgentTeamsServiceError("运行 'rmissing' 不存在")
        FakeRunSvc.calls.append(("interrupt", run_id, member_id))
        return {"message": "已中断"}

    async def get_transcript(
        self, username, run_id, member_id, before_id=None, limit=50
    ):
        assert username == "alice"
        if run_id == "rmissing":
            raise AgentTeamsServiceError("运行 'rmissing' 不存在")
        FakeRunSvc.calls.append(("transcript", run_id, member_id, before_id, limit))
        if before_id is not None:
            return {"messages": [], "next_before_id": None}
        return {
            "messages": [
                {"id": 2, "direction": "reply", "text": "回复"},
                {"id": 1, "direction": "sent", "text": "任务"},
            ],
            "next_before_id": 1,
        }

    async def get_event_bus(self, username, run_id):
        assert username == "alice" and run_id == "r1"
        return _BUS


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/v1")
    app.include_router(mod.legacy_router)

    async def _fake_auth():
        return "alice"

    app.dependency_overrides[mod.get_team_service] = lambda: FakeTeamSvc()
    app.dependency_overrides[mod.get_run_service] = lambda: FakeRunSvc()
    app.dependency_overrides[mod._auth_dep] = _fake_auth
    return TestClient(app, raise_server_exceptions=False)


def test_create_team_route(client):
    resp = client.post(
        "/api/v1/agent_teams", json={"name": "t", "members": [], "coordinator": ""}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["team_id"] == "t1"


def test_start_run_maps_active_conflict_to_409(client):
    resp = client.post(
        "/api/v1/agent_teams/t1/runs",
        json={"mode": "dag", "input": "conflict", "workflow_id": "w1"},
    )
    assert resp.status_code == 409
    # A plain service error carries no field data.
    assert "data" not in resp.json()


def test_workflow_error_envelope_carries_field_errors(client):
    """Field-level validation problems ride the error envelope as data.fields."""
    resp = client.post(
        "/api/v1/agent_teams/t1/workflows",
        json={"name": "w", "graph": {"nodes": [], "edges": []}},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert "工作流校验失败" in body["message"]
    assert body["data"]["fields"] == [
        {
            "path": "nodes.n1.task",
            "code": "REQUIRED",
            "message": "节点 'n1' 缺少任务模板",
        }
    ]


def test_pause_run_forwards_username_first_on_legacy_route(client):
    resp = client.post("/api/agent_teams/runs/r1/pause")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"message": "已暂停"}


def test_interrupt_member_route_on_both_routers(client):
    FakeRunSvc.calls.clear()
    resp = client.post("/api/v1/agent_teams/runs/r1/members/m1/interrupt")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"message": "已中断"}

    resp = client.post("/api/agent_teams/runs/r1/members/m1/interrupt")
    assert resp.status_code == 200

    assert FakeRunSvc.calls == [("interrupt", "r1", "m1"), ("interrupt", "r1", "m1")]


def test_interrupt_member_unknown_run_maps_to_400(client):
    resp = client.post("/api/v1/agent_teams/runs/rmissing/members/m1/interrupt")
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


def test_member_transcript_route_shape_and_paging_on_both_routers(client):
    FakeRunSvc.calls.clear()
    resp = client.get("/api/v1/agent_teams/runs/r1/members/m1/transcript")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert [m["id"] for m in body["messages"]] == [2, 1]
    assert body["next_before_id"] == 1

    resp = client.get(
        "/api/v1/agent_teams/runs/r1/members/m1/transcript",
        params={"before_id": 1, "limit": 10},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"messages": [], "next_before_id": None}
    assert FakeRunSvc.calls[-1] == ("transcript", "r1", "m1", 1, 10)

    resp = client.get("/api/agent_teams/runs/r1/members/m1/transcript")
    assert resp.status_code == 200
    assert resp.json()["data"]["messages"][0]["id"] == 2


def test_member_transcript_unknown_run_maps_to_400(client):
    resp = client.get("/api/v1/agent_teams/runs/rmissing/members/m1/transcript")
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


def test_workflow_update_delete_routes_nest_team_id_in_path(client):
    """PUT/DELETE workflow routes must take team_id from the PATH on both
    routers (the v1 contract freezes when the API client is generated)."""
    FakeTeamSvc.calls.clear()

    resp = client.put(
        "/api/v1/agent_teams/t1/workflows/w1", json={"team_id": "ignored", "name": "v2"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["workflow_id"] == "w1"

    resp = client.delete("/api/v1/agent_teams/t1/workflows/w1")
    assert resp.status_code == 200

    resp = client.put("/api/agent_teams/t1/workflows/w1", json={"name": "v2"})
    assert resp.status_code == 200

    resp = client.delete("/api/agent_teams/t1/workflows/w1")
    assert resp.status_code == 200

    assert FakeTeamSvc.calls[0] == (
        "update",
        "alice",
        "t1",
        "w1",
        {"team_id": "ignored", "name": "v2"},
    )
    assert ("delete", "alice", "t1", "w1") in FakeTeamSvc.calls
    # team_id always comes from the path, never from the request body
    assert all(call[2] == "t1" for call in FakeTeamSvc.calls)


@pytest.mark.asyncio
async def test_stream_replays_history_then_heartbeats(monkeypatch):
    # TestClient buffers the whole response body, so an endless SSE stream
    # must be driven through the handler + body iterator directly.
    monkeypatch.setattr(mod, "_SSE_HEARTBEAT_SECONDS", 0.01)

    async def _never_receive():
        # Keeps is_disconnected() pending until its cancel scope fires.
        await asyncio.sleep(3600)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/agent_teams/runs/r1/stream",
            "headers": [],
            "query_string": b"",
        },
        receive=_never_receive,
    )
    resp = await mod.stream_run(
        run_id="r1", request=request, username="alice", service=FakeRunSvc()
    )
    assert isinstance(resp, StreamingResponse)

    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk)
        if chunk == ": heartbeat\n\n":
            break
    events = [
        json.loads(chunk.removeprefix("data: "))
        for chunk in chunks
        if chunk.startswith("data: ")
    ]
    assert [e["type"] for e in events] == ["run_started", "dag_progress"]
    assert chunks[-1] == ": heartbeat\n\n"

    # Closing the stream unsubscribes from the shared bus.
    await resp.body_iterator.aclose()
    assert _BUS._subscribers == []
