"""Ownership, input diagnostics and pinned workflow versions over HTTP."""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowVersion
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.role_assets import router as roles_router
from astrbot.dashboard.api.workflow_assets import router
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest_asyncio.fixture
async def publication_client(workflow_env):
    """Expose the real asset and draft endpoints for the workflow owner.

    Args:
        workflow_env: Services backed by the test's isolated database.

    Yields:
        An HTTP client authenticated as the fixture's asset owner.
    """
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=workflow_env.roles, workflow_assets=workflow_env.service
    )
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_deletion_http_checks_ownership_and_hides_both_directories(workflow_env):
    env = workflow_env
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=env.roles, workflow_assets=env.service
    )
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "bob"
    role_path = f"/api/v1/agent_roles/{env.role['role_id']}"
    asset_path = f"/api/v1/workflow_assets/{env.asset['asset_id']}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.delete(role_path)).status_code == 404
        assert (await client.delete(asset_path)).status_code == 404
        app.dependency_overrides[require_dashboard_user] = lambda: "alice"
        assert (await client.delete(role_path)).status_code == 200
        assert (await client.delete(role_path)).status_code == 200
        assert (await client.delete(asset_path)).status_code == 200
        assert (await client.get("/api/v1/agent_roles")).json()["data"]["roles"] == []
        assert (await client.get("/api/v1/workflow_assets")).json()["data"][
            "assets"
        ] == []
        assert (await client.get(role_path)).json()["data"]["deleted"]
        assert (await client.get(asset_path)).json()["data"]["deleted"]
        assert (
            await client.post(asset_path + "/runs", json={"version": 1})
        ).status_code == 409


@pytest.mark.asyncio
async def test_draft_with_deleted_result_can_be_reopened_repaired_and_started(
    workflow_env,
    publication_client,
):
    env = workflow_env
    definition = {
        **env.asset["definition"],
        "result_task_id": "deleted",
        "required_inputs": ["topic"],
        "tasks": [
            {
                **env.asset["definition"]["tasks"][0],
                "task": "Write about {topic}",
                "inputs": {"topic": {"input": "topic", "field": "text"}},
            }
        ],
    }
    created = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    draft_path = f"/api/v1/agent_drafts/workflow/{asset_id}"
    asset_path = f"/api/v1/workflow_assets/{asset_id}"
    invalid = await publication_client.post(
        f"{draft_path}/publish", json={"revision": 1}
    )
    assert invalid.status_code == 400, invalid.text
    assert invalid.json()["data"]["fields"][0]["path"] == "definition.result_task_id"
    assert (await publication_client.get(asset_path)).status_code == 404
    reopened = (await publication_client.get(draft_path)).json()["data"]
    assert reopened["revision"] == 1
    assert reopened["body"]["definition"]["result_task_id"] == "deleted"

    trial_path = f"{asset_path}/trials"
    trial = {"kind": "workflow", "revision": 1}
    full = await publication_client.post(
        f"{trial_path}/preview", json={**trial, "mode": "full"}
    )
    assert full.status_code == 400, full.text
    assert full.json()["data"]["fields"][0]["path"] == "definition.result_task_id"
    trial.update(mode="partial", node_id="writer")
    preview = await publication_client.post(f"{trial_path}/preview", json=trial)
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["selected_nodes"] == ["writer"]
    assert preview.json()["data"]["required_inputs"] == ["topic"]
    tested = await publication_client.post(
        trial_path, json={**trial, "inputs": {"topic": "energy"}}
    )
    assert tested.status_code == 200, tested.text
    result = await completed_run(env.service, tested.json()["data"]["run_id"])
    assert result["status"] == "completed"
    assert result["snapshot"]["asset_version"] is None

    definition["result_task_id"] = "writer"
    corrected = await publication_client.put(
        draft_path,
        json={"body": {"definition": definition}, "expected_revision": 1},
    )
    assert corrected.status_code == 200, corrected.text
    published = await publication_client.post(
        f"{draft_path}/publish", json={"revision": 2}
    )
    assert published.status_code == 200, published.text
    assert published.json()["data"]["version"] == 1
    started = await publication_client.post(
        f"{asset_path}/runs", json={"version": 1, "inputs": {"topic": "energy"}}
    )
    assert started.status_code == 200, started.text
    finished = await completed_run(env.service, started.json()["data"]["run_id"])
    assert finished["status"] == "completed"
    assert finished["result_ref"] == finished["tasks"][0]["result_ref"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result_task_id", "graph_case", "expected_code"),
    [
        (None, None, "REFERENCE"),
        ("join", None, "REFERENCE"),
        ("writer", None, "UNREACHABLE"),
        ("output", None, "UNREACHABLE"),
        ("output", "approval", "UNREACHABLE"),
        ("output", "exit", "UNREACHABLE"),
        ("output", "missing-feedback", "UNREACHABLE"),
        ("output", "available-feedback", None),
    ],
)
async def test_asset_publication_requires_reachable_result(
    workflow_env, publication_client, result_task_id, graph_case, expected_code
):
    env = workflow_env
    writer = env.asset["definition"]["tasks"][0]
    definition = {
        **env.asset["definition"],
        "result_task_id": result_task_id,
        "tasks": [
            writer,
            {"id": "join", "name": "Join", "control": {"kind": "join"}},
            {**writer, "id": "active", "name": "Active"},
            {"id": "merge", "name": "Merge", "control": {"kind": "join"}},
            {
                **writer,
                "id": "output",
                "name": "Output",
                "task": "Write from {draft}",
                "inputs": {"draft": {"task_id": "writer", "field": "text"}},
            },
        ],
        "edges": [
            {"source": "join", "target": "writer"},
            {"source": "writer", "target": "merge"},
            {"source": "active", "target": "merge"},
            {"source": "merge", "target": "output"},
        ],
    }
    if graph_case == "approval":
        definition["tasks"][-1].update(task=writer["task"], inputs={})
        definition["tasks"].append(
            {
                "id": "approval",
                "name": "Approval",
                "human": {
                    "kind": "approval",
                    "question": "Approve this draft?",
                    "artifact_task_id": "writer",
                    "artifact_version": 1,
                },
            }
        )
        definition["edges"][-1] = {"source": "merge", "target": "approval"}
        definition["edges"].append(
            {
                "source": "approval",
                "target": "output",
                "condition": {"field": "approved", "value": True},
            }
        )
    elif graph_case:
        exit_task = {**writer, "id": "exit", "name": "Exit"}
        if graph_case == "exit":
            output = definition["tasks"][-1]
            exit_task.update(task=output["task"], inputs=output["inputs"])
            output.update(task=writer["task"], inputs={})
            definition["edges"][-1] = {"source": "merge", "target": "exit"}
            definition["edges"].append({"source": "active", "target": "output"})
            body = ["join", "writer", "active", "merge", "exit"]
            feedback = []
        else:
            definition["edges"].extend(
                [
                    {"source": "output", "target": "exit"},
                    {"source": "active", "target": "exit"},
                ]
            )
            body = ["join", "writer", "active", "merge", "output", "exit"]
            feedback = [
                {
                    "source": "active"
                    if graph_case == "available-feedback"
                    else "writer",
                    "target": "output",
                    "input": "draft",
                }
            ]
        definition["tasks"].append(exit_task)
        definition["loops"] = [
            {
                "id": "review",
                "name": "Review",
                "body": body,
                "exit_task_id": "exit",
                "exit_condition": {"field": "text", "value": "finished"},
                "max_iterations": 2,
                "feedback": feedback,
            }
        ]
    asset_path = f"/api/v1/workflow_assets/{env.asset['asset_id']}"
    published = await publication_client.put(
        asset_path, json={"definition": definition, "expected_version": 1}
    )
    if expected_code is None:
        assert published.status_code == 200, published.text
        assert published.json()["data"]["version"] == 2
        return
    assert published.status_code == 400, published.text
    fields = published.json()["data"]["fields"]
    assert {(field["path"], field["code"]) for field in fields} == {
        ("definition.result_task_id", expected_code)
    }
    original = (await publication_client.get(asset_path)).json()["data"]
    assert original["versions"] == [1]
    assert original["definition"]["edges"] == []


@pytest.mark.asyncio
async def test_incomplete_fixed_version_can_be_read_and_repaired(
    workflow_env, publication_client
):
    env = workflow_env
    asset_id = "incomplete-workflow"
    old = {**env.asset["definition"], "result_task_id": None}
    async with env.db.get_db() as session:
        session.add(
            AgentWorkflowVersion(
                asset_id=asset_id, version=1, owner_username="alice", definition=old
            )
        )
        await session.commit()

    asset_path = f"/api/v1/workflow_assets/{asset_id}"
    env.inventory["profiles"] = []
    old_version = await publication_client.get(asset_path, params={"version": 1})
    assert old_version.status_code == 200, old_version.text
    assert old_version.json()["data"]["definition"] == old
    blocked = await publication_client.get(
        f"{asset_path}/readiness", params={"version": 1}
    )
    assert blocked.status_code == 200, blocked.text
    diagnostic = blocked.json()["data"]
    assert not diagnostic["ready"]
    assert {(issue["path"], issue["code"]) for issue in diagnostic["issues"]} == {
        ("definition.result_task_id", "REFERENCE")
    }
    started = await publication_client.post(f"{asset_path}/runs", json={"version": 1})
    assert started.status_code == 400, started.text
    assert started.json()["data"]["fields"] == diagnostic["issues"]

    fixed = {**old, "result_task_id": "writer"}
    draft = await publication_client.post(
        "/api/v1/agent_drafts/workflow",
        json={"asset_id": asset_id, "body": {"definition": fixed}},
    )
    assert draft.status_code == 200, draft.text
    draft_path = f"/api/v1/agent_drafts/workflow/{asset_id}"
    published = await publication_client.post(
        f"{draft_path}/publish", json={"revision": 1}
    )
    assert published.status_code == 200, published.text
    assert published.json()["data"]["versions"] == [2, 1]
    assert (await publication_client.get(asset_path, params={"version": 1})).json()[
        "data"
    ]["definition"] == old
    readiness = await publication_client.get(
        f"{asset_path}/readiness", params={"version": 2}
    )
    assert readiness.status_code == 200, readiness.text
    diagnostic = readiness.json()["data"]
    assert not diagnostic["ready"]
    assert ("definition.tasks.0.installation.profile_id", "MISSING") in {
        (issue["path"], issue["code"]) for issue in diagnostic["issues"]
    }
    assert all(
        issue["path"] != "definition.result_task_id" for issue in diagnostic["issues"]
    )


@pytest.mark.asyncio
async def test_legacy_unreachable_result_keeps_run_semantics_but_cannot_be_pinned(
    workflow_env,
    publication_client,
):
    env = workflow_env
    asset_id = "unreachable-workflow"
    legacy = {
        **env.asset["definition"],
        "tasks": [
            *env.asset["definition"]["tasks"],
            {"id": "join", "name": "Join", "control": {"kind": "join"}},
        ],
        "edges": [{"source": "join", "target": "writer"}],
    }
    async with env.db.get_db() as session:
        session.add(
            AgentWorkflowVersion(
                asset_id=asset_id, version=1, owner_username="alice", definition=legacy
            )
        )
        await session.commit()

    run = await env.service.start_run("alice", asset_id, {"version": 1})
    finished = await completed_run(env.service, run["run_id"])
    assert finished["status"] == "blocked"
    assert finished["result_ref"] is None

    parent_path = f"/api/v1/workflow_assets/{env.asset['asset_id']}"
    definition = {
        "name": "Parent",
        "result_task_id": "call",
        "tasks": [
            {
                "id": "call",
                "name": "Call",
                "workflow": {"asset_id": asset_id, "version": 1},
            }
        ],
    }
    failed = await publication_client.put(
        parent_path, json={"definition": definition, "expected_version": 1}
    )
    assert failed.status_code == 400, failed.text
    assert {
        (field["path"], field["code"]) for field in failed.json()["data"]["fields"]
    } == {("definition.tasks.0.workflow", "UNREACHABLE")}
    assert (await publication_client.get(parent_path)).json()["data"]["versions"] == [1]


@pytest.mark.asyncio
async def test_inline_graph_http_start_and_read_original_result(workflow_env):
    env = workflow_env
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    definition = {
        "name": "Inline HTTP",
        "result_task_id": "agent",
        "default_provider_id": "model-a",
        "required_inputs": ["topic"],
        "tasks": [
            {
                "id": "agent",
                "name": "Writer",
                "member": "writer",
                "role": {"name": "Writer", "prompt": "Write"},
                "installation": {"profile_id": "default"},
                "task": "Write {topic}",
                "inputs": {"topic": {"input": "topic"}},
            }
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        saved = (
            await client.post(
                "/api/v1/workflow_assets", json={"definition": definition}
            )
        ).json()["data"]
        path = f"/api/v1/workflow_assets/{saved['asset_id']}"
        assert (await client.get(path, params={"version": 1})).json()["data"][
            "definition"
        ]["tasks"][0]["role"]["prompt"] == "Write"
        start = await client.post(
            path + "/runs", json={"version": 1, "inputs": {"topic": "energy"}}
        )
        assert start.status_code == 200
        run_id = start.json()["data"]["run_id"]
        finished = await completed_run(env.service, run_id)
        assert finished["status"] == "completed"
        assert (await client.get(f"/api/v1/workflow_runs/{run_id}/result")).json()[
            "data"
        ] == {"text": "Complete report. " * 1000}
        assert finished["result_ref"] == finished["tasks"][0]["result_ref"]


def test_workflow_http_validation_and_ownership(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "api.db"))
    roles = RoleAssetService(db)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=roles, workflow_assets=WorkflowService(db, roles)
    )
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        definition = {
            "name": "Report",
            "result_task_id": "writer",
            "default_provider_id": "model-a",
            "required_inputs": ["topic"],
            "tasks": [
                {
                    "id": "writer",
                    "name": "Writer",
                    "member": "writer",
                    "role": {"name": "Writer", "prompt": "Write."},
                    "installation": {"profile_id": "default"},
                    "task": "Read {topic}",
                    "inputs": {"topic": {"input": "topic"}},
                }
            ],
        }
        response = client.post(
            "/api/v1/workflow_assets", json={"definition": definition}
        )
        assert response.status_code == 200
        asset = response.json()["data"]
        assert (
            client.get(f"/api/v1/workflow_assets/{asset['asset_id']}?version=1").json()[
                "data"
            ]
            == asset
        )
        invalid = client.post(
            f"/api/v1/workflow_assets/{asset['asset_id']}/runs", json={"version": 1}
        )
        assert invalid.status_code == 400
        assert invalid.json()["data"]["fields"][0]["path"] == "inputs.topic"
        assert client.get("/api/v1/workflow_runs").json()["data"] == {"runs": []}
        assert (
            client.put(
                f"/api/v1/workflow_assets/{asset['asset_id']}",
                json={"definition": definition, "expected_version": 9},
            ).status_code
            == 409
        )
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (
            client.get(f"/api/v1/workflow_assets/{asset['asset_id']}").status_code
            == 404
        )
        assert client.get("/api/v1/workflow_assets").json()["data"] == {"assets": []}


def test_team_allowed_workflows_http_pin_and_owner_validation(tmp_path):
    """Save explicit workflow permissions and expose field-specific owner errors.

    Args:
        tmp_path: Isolated SQLite used by the real HTTP application service.
    """
    db = SQLiteDatabase(str(tmp_path / "reverse-api.db"))
    roles = RoleAssetService(db)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=roles, workflow_assets=WorkflowService(db, roles)
    )
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        role = client.post(
            "/api/v1/agent_roles",
            json={"definition": {"name": "Writer", "prompt": "Write"}},
        ).json()["data"]
        workflow = client.post(
            "/api/v1/workflow_assets",
            json={
                "definition": {
                    "name": "Saved workflow",
                    "result_task_id": "result",
                    "required_inputs": ["topic"],
                    "tasks": [
                        {
                            "id": "result",
                            "name": "Research",
                            "member": "writer",
                            "role_id": role["role_id"],
                            "role_version": 1,
                            "task": "Research {topic}",
                            "inputs": {"topic": {"input": "topic"}},
                        }
                    ],
                }
            },
        ).json()["data"]
        team = {
            "kind": "autonomous",
            "name": "Coordinator",
            "coordinator": {
                "id": "coordinator",
                "role_id": role["role_id"],
                "role_version": 1,
            },
            "allowed_roles": [
                {"id": "writer", "role_id": role["role_id"], "role_version": 1}
            ],
            "allowed_workflows": [
                {
                    "asset_id": workflow["asset_id"],
                    "version": 1,
                }
            ],
        }
        saved = client.post("/api/v1/workflow_assets", json={"definition": team})
        assert saved.status_code == 200
        assert (
            saved.json()["data"]["definition"]["allowed_workflows"]
            == team["allowed_workflows"]
        )
        team["allowed_workflows"][0]["version"] = 2
        missing = client.post("/api/v1/workflow_assets", json={"definition": team})
        assert missing.status_code == 404
        assert (
            missing.json()["data"]["fields"][0]["path"]
            == "definition.allowed_workflows.0"
        )
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        bob_role = client.post(
            "/api/v1/agent_roles",
            json={"definition": {"name": "Bob", "prompt": "Write"}},
        ).json()["data"]
        team["coordinator"]["role_id"] = team["allowed_roles"][0]["role_id"] = bob_role[
            "role_id"
        ]
        team["allowed_workflows"][0]["version"] = 1
        denied = client.post("/api/v1/workflow_assets", json={"definition": team})
        assert denied.status_code == 404
        assert denied.json()["data"]["fields"][0]["code"] == "REFERENCE"
        assert client.get("/api/v1/workflow_assets").json()["data"]["assets"] == []


def test_canvas_graph_http_save_and_field_diagnostics(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "graph-api.db"))
    roles = RoleAssetService(db)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=roles, workflow_assets=WorkflowService(db, roles)
    )
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        role = client.post(
            "/api/v1/agent_roles",
            json={"definition": {"name": "Writer", "prompt": "Write."}},
        ).json()["data"]
        task = {
            "id": "first",
            "name": "First",
            "member": "writer",
            "role_id": role["role_id"],
            "role_version": 1,
            "task": "Write",
        }
        definition = {
            "name": "Canvas",
            "result_task_id": "next",
            "tasks": [task, {**task, "id": "next"}],
            "edges": [{"source": "first", "target": "next"}],
        }
        response = client.post(
            "/api/v1/workflow_assets", json={"definition": definition}
        )
        assert response.status_code == 200
        asset = response.json()["data"]
        assert (
            client.get(f"/api/v1/workflow_assets/{asset['asset_id']}").json()["data"][
                "definition"
            ]["tasks"][0]["id"]
            == "first"
        )
        definition["edges"].append({"source": "next", "target": "first"})
        response = client.put(
            f"/api/v1/workflow_assets/{asset['asset_id']}",
            json={"definition": definition, "expected_version": 1},
        )
        assert response.status_code == 400
        assert any(
            field["code"] == "CYCLE" for field in response.json()["data"]["fields"]
        )
        assert (
            client.get(f"/api/v1/workflow_assets/{asset['asset_id']}").json()["data"][
                "version"
            ]
            == 1
        )


def test_human_http_uuid_answers_reconnect_and_owner_permissions(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "human-api.db"))
    service = WorkflowService(db, RoleAssetService(db))
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        asset = client.post(
            "/api/v1/workflow_assets",
            json={
                "definition": {
                    "name": "Question",
                    "result_task_id": "q",
                    "tasks": [
                        {
                            "id": "q",
                            "name": "Question",
                            "human": {
                                "kind": "question",
                                "question": "Which region?",
                                "choices": ["Asia", "Europe"],
                            },
                        }
                    ],
                }
            },
        ).json()["data"]
        run = client.post(
            f"/api/v1/workflow_assets/{asset['asset_id']}/runs",
            json={"version": 1, "source": "chat:alice"},
        ).json()["data"]
        path = f"/api/v1/workflow_runs/{run['run_id']}"
        todo = run["todos"][0]
        payload = {
            "request_id": "2a6fa610-a005-4c3b-8a38-df62d534a3fe",
            "action": "answer",
            "answer": "Asia",
        }
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert client.get(path).status_code == 404
        assert (
            client.post(
                path + f"/todos/{todo['todo_id']}/answer", json=payload
            ).status_code
            == 404
        )
        app.dependency_overrides[require_dashboard_user] = lambda: "alice"
        assert (
            client.post(
                path + f"/todos/{todo['todo_id']}/answer", json=payload
            ).status_code
            == 200
        )
        assert (
            client.post(
                path + f"/todos/{todo['todo_id']}/answer", json=payload
            ).status_code
            == 200
        )
        after = client.get(path).json()["data"]
        assert after["todos"][0]["response"]["answer"] == "Asia"
        events = client.get(path + "/events", params={"after": run["cursor"]}).json()[
            "data"
        ]
        assert events["snapshot"] is None
        assert events["reset"] is False
        assert events["cursor"] >= after["cursor"]
        assert (
            len(
                [
                    e
                    for e in events["events"]
                    if e["kind"] == "human.consumed"
                ]
            )
            == 1
        )


@pytest.mark.asyncio
async def test_human_inbox_http_owner_and_answer(workflow_env):
    env = workflow_env
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Question",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Where?"},
                    }
                ],
                "edges": [],
            }
        },
    )
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        inbox = (await client.get("/api/v1/workflow_human_requests")).json()["data"][
            "requests"
        ]
        assert len(inbox) == 1 and inbox[0]["todo_id"] == run["todos"][0]["todo_id"]
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (await client.get("/api/v1/workflow_human_requests")).json()["data"][
            "requests"
        ] == []
        answer_path = (
            f"/api/v1/workflow_runs/{run['run_id']}/todos/{inbox[0]['todo_id']}/answer"
        )
        payload = {"request_id": "answer", "action": "answer", "answer": "Asia"}
        assert (await client.post(answer_path, json=payload)).status_code == 404
        app.dependency_overrides[require_dashboard_user] = lambda: "alice"
        assert (await client.post(answer_path, json=payload)).status_code == 200
        assert (await client.get("/api/v1/workflow_human_requests")).json()["data"][
            "requests"
        ] == []
