"""Final default entry behavior through actual application services and HTTP."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import psutil
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


@pytest.mark.parametrize("scenario", ["loop_cutover_stop", "loop_cutover_loss"])
def test_original_owner_must_finish_before_the_shared_default_entry_can_run(
    tmp_path, scenario
):
    """Use actual SQLite, model transport and separate processes for cutover.

    Args:
        tmp_path: Isolated persistent instance and external request ledger.
        scenario: Explicit owner stop or independent process loss.
    """
    requests = []

    class Model(BaseHTTPRequestHandler):
        """Record and answer only the external provider boundary."""

        def log_message(self, format, *args):
            """Keep provider traffic in the explicit request ledger.

            Args:
                format: Default HTTP log format.
                *args: Default HTTP log values.
            """
            pass

        def do_POST(self):
            """Answer the external model request while retaining its full payload."""
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            if "team_finish" in payload["tools"]:
                response = {
                    "role": "assistant",
                    "completion_text": "No dispatch; waiting for the original owner.",
                }
            elif any(
                item.get("tool_call_id") == "new-result" for item in payload["contexts"]
            ):
                response = {"role": "assistant", "completion_text": "Submitted."}
            else:
                assert "LEGACY_PARK" not in json.dumps(payload["contexts"])
                response = {
                    "role": "tool",
                    "id": "new-submission",
                    "tools_call_name": ["submit_task_result"],
                    "tools_call_args": [{"text": "New asset result"}],
                    "tools_call_ids": ["new-result"],
                }
            response["usage"] = {"input_other": 11, "output": 5}
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    command = [
        sys.executable,
        "-B",
        "-m",
        "tests.agent_teams.cutover_worker",
        str(tmp_path),
        f"http://127.0.0.1:{server.server_port}",
    ]
    root = Path(__file__).resolve().parents[2]
    processes = []
    try:
        for action in ["start", "recover"] if scenario.endswith("loss") else ["start"]:
            argv = [*command, action, scenario]
            with (tmp_path / f"{action}.log").open("x", encoding="utf-8") as output:
                worker = subprocess.Popen(
                    argv,
                    cwd=root,
                    env={
                        **os.environ,
                        "ASTRBOT_ROOT": str(tmp_path),
                        "PYTHONUTF8": "1",
                    },
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
                runtime = None
                try:
                    if action == "start" and scenario.endswith("loss"):
                        deadline = time.monotonic() + 60
                        while not (tmp_path / "waiting-ready").exists():
                            assert worker.poll() is None, (
                                tmp_path / "start.log"
                            ).read_text(encoding="utf-8")
                            assert time.monotonic() < deadline
                            time.sleep(0.02)
                        runtime = json.loads(
                            (tmp_path / f"worker-identity-{action}.json").read_text(
                                encoding="utf-8"
                            )
                        )
                        owned_pids = {
                            worker.pid,
                            *(
                                child.pid
                                for child in psutil.Process(worker.pid).children(
                                    recursive=True
                                )
                            ),
                        }
                        assert runtime["pid"] in owned_pids
                        psutil.Process(runtime["pid"]).kill()
                        assert worker.wait(timeout=10) != 0
                        assert not psutil.pid_exists(runtime["pid"])
                        runtime["absent_before_rebuild"] = True
                    else:
                        assert worker.wait(timeout=60) == 0, (
                            tmp_path / f"{action}.log"
                        ).read_text(encoding="utf-8")
                        runtime = json.loads(
                            (tmp_path / f"worker-identity-{action}.json").read_text(
                                encoding="utf-8"
                            )
                        )
                        assert not psutil.pid_exists(runtime["pid"])
                        runtime["absent_after_exit"] = True
                finally:
                    if runtime is not None and psutil.pid_exists(runtime["pid"]):
                        psutil.Process(runtime["pid"]).kill()
                    if worker.poll() is None:
                        for child in psutil.Process(worker.pid).children(
                            recursive=True
                        ):
                            child.kill()
                        worker.kill()
                        worker.wait(timeout=10)
                    processes.append(
                        {
                            "pid": worker.pid,
                            "argv": argv,
                            "cwd": str(root),
                            "exit_code": worker.returncode,
                            "runtime": runtime,
                            "intentional_kill": action == "start"
                            and scenario.endswith("loss"),
                        }
                    )
        legacy_requests = [item for item in requests if "team_finish" in item["tools"]]
        # A normal no-dispatch pause saves no new round; resuming its owner
        # must still make another real provider call before explicitly stopping.
        assert len(legacy_requests) >= (2 if scenario.endswith("loss") else 3)
        if scenario.endswith("loss"):
            before = json.loads(
                (tmp_path / "before-loss.json").read_text(encoding="utf-8")
            )["before"]
            after = json.loads(
                (tmp_path / "recover-public.json").read_text(encoding="utf-8")
            )["after"]
            assert before["history_status"] == "paused"
            assert after["history_status"] == "interrupted"
            assert {
                key: value for key, value in before.items() if key != "history_status"
            } == {key: value for key, value in after.items() if key != "history_status"}
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        (tmp_path / "external-ledger.json").write_text(
            json.dumps(requests, indent=2), encoding="utf-8"
        )
        (tmp_path / "processes.json").write_text(
            json.dumps(processes, indent=2), encoding="utf-8"
        )


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
