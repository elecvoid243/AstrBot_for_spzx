"""Dynamic choice recovery through the actual model, tool and HTTP pipeline."""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.orm import Session

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowEvent
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router


async def run_choice_scenario(
    folder: Path,
    action: str,
    scenario: str,
    service: WorkflowService,
    roles: RoleAssetService,
) -> None:
    """Terminate at real transaction boundaries and answer through the public API.

    Args:
        folder: Durable state shared by independently launched workers.
        action: Start or recover the original run.
        scenario: Selected request, answer or consumption crash boundary.
        service: Actual workflow service with the ordinary main pipeline.
        roles: Actual resource catalog and role service.
    """
    boundary = scenario.removeprefix("choice_").rsplit("_", 1)[0]
    crash = scenario.endswith(("_pre", "_post"))
    controls_checked = False
    consumption_paused = False
    original_save = service.save_checkpoint

    async def pause_at_consumption(username, run_id, attempt_id, execution_id, data):
        """Place a real pause between tool return and durable answer consumption.

        Args:
            username: Actual run owner.
            run_id: Original task identity.
            attempt_id: Original attempt.
            execution_id: Current fence.
            data: Actual runner checkpoint.
        """
        nonlocal consumption_paused
        if not consumption_paused and any(
            call["name"] == "ask_user_choice" and call["status"] == "applied"
            for call in data.get("tool_calls", [])
        ):
            consumption_paused = True
            task = await service.get_run(username, run_id)
            root_id = task["snapshot"]["parent_run_id"]
            await service.control_run(
                username,
                root_id,
                {"request_id": "pause-consumption", "action": "pause"},
            )
            try:
                await original_save(username, run_id, attempt_id, execution_id, data)
            except RoleAssetError as exc:
                assert any(field["code"] == "PAUSED" for field in exc.fields)
            else:
                raise AssertionError("A paused run consumed a human answer")
            saved = await service.get_run(username, run_id)
            assert saved["todos"][0]["status"] == "answered"
            assert saved["todos"][0]["consumed_at"] is None
            await service.control_run(
                username,
                root_id,
                {"request_id": "continue-consumption", "action": "continue"},
            )
        await original_save(username, run_id, attempt_id, execution_id, data)

    if scenario == "choice_consume_pause":
        service.save_checkpoint = pause_at_consumption

    def before_flush(session, _context, _instances):
        """Hold before committing the selected persistent event.

        Args:
            session: Actual SQLAlchemy session.
            _context: Flush state.
            _instances: Optional explicit flush targets.
        """
        for row in session.new:
            if (
                scenario == "choice_request_failure"
                and isinstance(row, AgentWorkflowEvent)
                and row.kind == "human.requested"
            ):
                raise OSError("Injected human request persistence failure")
            if isinstance(row, AgentWorkflowEvent) and row.kind == {
                "request": "human.requested",
                "answer": "human.answered",
                "consume": "human.consumed",
            }.get(boundary):
                if scenario.endswith("_pre"):
                    (folder / "kill-ready.json").write_text("ready", encoding="utf-8")
                    while True:
                        time.sleep(1)
                session.info["choice_boundary"] = True

    def after_commit(session):
        """Hold after the selected real transaction has committed.

        Args:
            session: Committed SQLAlchemy session.
        """
        if session.info.pop("choice_boundary", False):
            (folder / "kill-ready.json").write_text("ready", encoding="utf-8")
            while True:
                time.sleep(1)

    if action == "start":
        if crash or scenario == "choice_request_failure":
            event.listen(Session, "before_flush", before_flush)
            event.listen(Session, "after_commit", after_commit)
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Durable original choices",
                    "result_task_id": "writer",
                    "default_provider_id": "model-original",
                    "max_parallel": 1,
                    "tasks": [
                        {
                            "id": "writer",
                            "name": "Writer",
                            "member": "writer",
                            "task": "Read evidence, ask two questions and submit the agreed report.",
                            "role": {
                                "name": "Writer",
                                "prompt": "Write from verified evidence and human decisions.",
                                "tools": ["ask_user_choice", "external_evidence"],
                                "skills": [],
                                "knowledge_bases": [],
                                "max_steps": 8,
                            },
                            "installation": {"profile_id": "default"},
                        },
                        *(
                            [
                                {
                                    "id": "independent",
                                    "name": "Independent",
                                    "member": "independent",
                                    "task": "INDEPENDENT: submit the independent result without asking questions.",
                                    "role": {
                                        "name": "Independent",
                                        "prompt": "Complete the independent task.",
                                        "tools": [],
                                        "skills": [],
                                        "knowledge_bases": [],
                                    },
                                    "installation": {"profile_id": "default"},
                                }
                            ]
                            if scenario == "choice_independent"
                            else []
                        ),
                    ],
                }
            },
        )
        readiness = await service.check_readiness("alice", asset["asset_id"], 1)
        assert readiness["ready"], readiness
        run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        (folder / "run.json").write_text(
            json.dumps({"run_id": run["run_id"]}), encoding="utf-8"
        )
    else:
        run_id = json.loads((folder / "run.json").read_text(encoding="utf-8"))["run_id"]
        run = await service.get_run("alice", run_id)
        await service.boot_sweep()
    before = run
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(1000):
            current = (
                await client.get(f"/api/v1/workflow_runs/{run['run_id']}")
            ).json()["data"]
            if current["status"] in {"completed", "cancelled"} or (
                scenario == "choice_request_failure" and current["status"] == "blocked"
            ):
                break
            for child in current["tasks"]:
                if child["status"] == "interrupted":
                    recovered = await client.post(
                        f"/api/v1/workflow_runs/{child['run_id']}/recover"
                    )
                    assert recovered.status_code == 200, recovered.text
            requests = (await client.get("/api/v1/workflow_human_requests")).json()[
                "data"
            ]["requests"]
            for todo in requests:
                assert todo["source_kind"] == "tool_call" and todo["deadline"] is None
                assert todo["scope_run_id"] == run["run_id"]
                if (
                    scenario == "choice_independent"
                    and current["tasks"][1]["status"] != "completed"
                ):
                    continue
                response = {
                    "request_id": "answer:" + todo["todo_id"],
                    "action": "answer",
                    "choice_id": "B"
                    if todo["tool_call_id"] == "choice-one"
                    else "__free_text__",
                    "free_text": "Reviewed evidence"
                    if todo["tool_call_id"] == "choice-one"
                    else "Use the detailed report",
                }
                url = f"/api/v1/workflow_runs/{todo['root_run_id']}/todos/{todo['todo_id']}/answer"
                if not controls_checked and scenario in {
                    "choice_pause_continue",
                    "choice_cancel_answered",
                    "choice_cancel_pending",
                }:
                    controls_checked = True
                    for invalid in [
                        {**response, "choice_id": "missing"},
                        {**response, "choice_id": "__free_text__", "free_text": None},
                    ]:
                        assert (await client.post(url, json=invalid)).status_code == 400
                    app.dependency_overrides[require_dashboard_user] = lambda: "bob"
                    assert (await client.get("/api/v1/workflow_human_requests")).json()[
                        "data"
                    ]["requests"] == []
                    assert (await client.post(url, json=response)).status_code == 404
                    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
                    control_url = f"/api/v1/workflow_runs/{run['run_id']}/control"
                    await client.post(
                        control_url, json={"request_id": "pause", "action": "pause"}
                    )
                    if scenario != "choice_cancel_pending":
                        duplicates = await asyncio.gather(
                            client.post(url, json=response),
                            client.post(
                                url, json={**response, "request_id": "same-decision"}
                            ),
                        )
                        assert all(item.status_code == 200 for item in duplicates), [
                            item.text for item in duplicates
                        ]
                        saved_todo = duplicates[-1].json()["data"]["tasks"][0]["todos"][
                            0
                        ]
                        assert (
                            saved_todo["status"] == "answered"
                            and saved_todo["consumed_at"] is None
                        )
                        assert (
                            await client.post(url, json={**response, "choice_id": "A"})
                        ).status_code == 409
                    next_action = (
                        "continue" if scenario == "choice_pause_continue" else "cancel"
                    )
                    changed = await client.post(
                        control_url,
                        json={"request_id": next_action, "action": next_action},
                    )
                    assert changed.status_code == 200, changed.text
                    if next_action == "cancel":
                        assert (
                            await client.post(url, json={**response, "choice_id": "A"})
                        ).status_code == 409
                        current = changed.json()["data"]
                        break
                saved = await client.post(url, json=response)
                assert saved.status_code == 200, saved.text
            await asyncio.sleep(0.03)
        expected = (
            "cancelled"
            if scenario in {"choice_cancel_answered", "choice_cancel_pending"}
            else "blocked"
            if scenario == "choice_request_failure"
            else "completed"
        )
        assert current["status"] == expected, current
        if scenario == "choice_consume_pause":
            assert consumption_paused
    (folder / "recovered.json").write_text(
        json.dumps({"before": before, "after": current}), encoding="utf-8"
    )
