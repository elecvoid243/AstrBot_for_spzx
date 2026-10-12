"""Recovery controls preserve attempts and reject unsupported replay."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import httpx
from fastapi import FastAPI
from types import SimpleNamespace
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_workflow_assets import completed_run, workflow_env


@pytest.mark.asyncio
async def test_recovery_is_owner_scoped_and_never_replays_a_finished_run(workflow_env):
    """A recovery action returns the same completed result without dispatching.

    Args:
        workflow_env: Real database and public workflow service.
    """
    env = workflow_env
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    finished = await completed_run(env.service, run["run_id"])
    with pytest.raises(RoleAssetError) as inaccessible:
        await env.service.recover_run("bob", run["run_id"])
    assert inaccessible.value.status == 404
    recovered = await env.service.recover_run("alice", run["run_id"])
    assert recovered == finished
    assert len(env.observations) == 1


@pytest.mark.asyncio
async def test_http_recovery_uses_the_authenticated_owner_and_original_run(workflow_env):
    """The HTTP action shares the persisted public recovery behavior.

    Args:
        workflow_env: Real services and durable database.
    """
    env = workflow_env
    started = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    finished = await completed_run(env.service, started["run_id"])
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post(f"/api/v1/workflow_runs/{started['run_id']}/recover")
        assert result.status_code == 200
        assert result.json()["data"] == finished
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (await client.post(f"/api/v1/workflow_runs/{started['run_id']}/recover")).status_code == 404
    assert len(env.observations) == 1


@pytest.mark.parametrize("scenario", ["response", "request", "no_thinking", "started", "submit_pre", "submit_post", "save_failure", "observation_failure", "missing_model", "changed_tool", "parameters", "repair", "parameters_checkpoint", "repair_checkpoint", "parameters_checkpoint_failure", "graph", "concurrent"])
def test_saved_model_plan_survives_forced_process_termination(tmp_path, scenario, preview=False):
    """Resume the real execution chain without repeating the saved model plan.

    Args:
        tmp_path: Shared on-disk data; the external ledger belongs to this process.
        scenario: Saved response, missing response, started tool or submission boundary.
        preview: Serve the actual built dashboard against the interrupted database.
    """
    requests = []
    effects = []
    parameter_mode = scenario.startswith(("parameters", "repair"))
    repair_mode = scenario.startswith("repair")

    class ExternalRecorder(BaseHTTPRequestHandler):
        """Model and tool boundary independent of the terminated Agent process."""

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            """Record model calls and tool effects independently of the worker.

            Returns:
                None after persisting the ledger and sending the scripted response.
            """
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                effects.append(payload)
                response = {"text": "Verified external evidence."}
            else:
                requests.append(payload)
                context = json.dumps(payload["contexts"])
                if "submit-original" in context:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif "evidence-original" in context:
                    response = {"role": "tool", "id": "submission-original", "tools_call_name": ["submit_task_result"], "tools_call_args": [{"text": "Original verified report."}], "tools_call_ids": ["submit-original"]}
                else:
                    response = {"role": "tool", "id": "plan-original", "tools_call_name": ["external_evidence"], "tools_call_args": [{"value": "original-plan"}], "tools_call_ids": ["evidence-original"], "reasoning_content": "Original provider thinking.", "reasoning_signature": "original-signature", "tools_call_extra_content": {"evidence-original": {"google": {"thought_signature": "original-tool-signature"}}}}
                    if payload.get("parameter_query"):
                        response["id"] = "parameters-original"
                        if repair_mode and not payload.get("repair_query"):
                            response = {"role": "assistant", "id": "empty-parameters", "completion_text": ""}
                    if scenario == "no_thinking":
                        response.pop("reasoning_content")
                        response.pop("reasoning_signature")
                if parameter_mode:
                    response["usage"] = {"input_other": 8, "input_cached": 2, "output": 2}
            (tmp_path / "external-ledger.json").write_text(json.dumps({"requests": requests, "effects": effects}), encoding="utf-8")
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExternalRecorder)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "tests.agent_teams.checkpoint_worker", str(tmp_path), endpoint]
    try:
        with (tmp_path / "first.log").open("w", encoding="utf-8") as log:
            worker = subprocess.Popen([*command, "start", scenario], cwd=root, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUTF8": "1"})
            try:
                deadline = time.monotonic() + 40
                while not (tmp_path / "kill-ready.json").exists():
                    assert worker.poll() is None, (tmp_path / "first.log").read_text(encoding="utf-8")
                    assert time.monotonic() < deadline, (tmp_path / "first.log").read_text(encoding="utf-8")
                    time.sleep(0.05)
                assert len(requests) == (3 if repair_mode else 2 if scenario.startswith("submit_") or parameter_mode else 1)
                assert effects == ([{"value": "original-plan"}] if scenario in {"started", "submit_pre", "submit_post"} else [])
                worker.kill()
                worker.wait(timeout=10)
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=10)
        if preview:
            subprocess.run([*command, "serve", scenario], cwd=root, check=True, env={**os.environ, "PYTHONUTF8": "1"})
            return
        resumed = subprocess.run([*command, "recover", scenario], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=40, env={**os.environ, "PYTHONUTF8": "1"})
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        if scenario == "parameters_checkpoint_failure":
            assert evidence["after"]["status"] == "failed"
            assert "Failed to save complete process record" in evidence["after"]["state"]["reason"]
            assert len(requests) == 2
            assert effects == []
            failed = evidence["after"]
            repaired = subprocess.run([*command, "recover_storage", scenario], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=40, env={**os.environ, "PYTHONUTF8": "1"})
            assert repaired.returncode == 0, repaired.stdout + repaired.stderr
            evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
            assert evidence["after"]["snapshot"] == failed["snapshot"]
            assert evidence["after"]["state"]["attempt_id"] == failed["state"]["attempt_id"]
        if scenario == "started":
            assert evidence["error"]["status"] == 409
            assert "confirmation" in evidence["error"]["message"]
            assert evidence["after"]["recovery"]["available"] is False
            assert len(requests) == 1
            assert effects == [{"value": "original-plan"}]
            return
        if scenario == "missing_model":
            assert evidence["after"]["status"] == "blocked"
            assert "Frozen resources" in evidence["after"]["state"]["reason"]
            assert len(requests) == 1
            assert effects == []
            return
        if scenario == "changed_tool":
            assert evidence["after"]["status"] == "blocked"
            assert "Saved tool capabilities have changed" in evidence["after"]["state"]["reason"]
            assert evidence["after"]["recovery"]["available"] is True
            assert len(requests) == 1
            assert effects == []
            blocked = evidence["after"]
            repaired = subprocess.run([*command, "recover", "response"], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=40, env={**os.environ, "PYTHONUTF8": "1"})
            assert repaired.returncode == 0, repaired.stdout + repaired.stderr
            evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
            assert evidence["after"]["snapshot"] == blocked["snapshot"]
            assert evidence["after"]["state"]["attempt_id"] == blocked["state"]["attempt_id"]
        assert evidence["after"]["status"] == "completed"
        assert evidence["after"]["snapshot"] == evidence["before"]["snapshot"]
        assert evidence["after"]["state"]["attempt_id"] == evidence["before"]["state"]["attempt_id"]
        assert evidence["result"] == {"text": "Original verified report."}
        assert evidence["late_errors"] == [409, 409, 409, 409]
        assert evidence["repeated"] == evidence["after"]
        assert len(evidence["after"]["artifacts"]) == 1
        assert sum(event["kind"] == "task.completed" for event in evidence["after"]["events"]) == 1
        assert evidence["after"]["recovery"]["steps_used"] == (2 if scenario == "submit_post" else 3)
        assert evidence["after"]["recovery"]["model_calls"] == (6 if repair_mode else 5 if parameter_mode else 2 if scenario == "submit_post" else 3)
        assert len(requests) == (6 if repair_mode else 5 if parameter_mode else 4 if scenario in {"request", "save_failure"} else 2 if scenario == "submit_post" else 3)
        if parameter_mode:
            assert evidence["stats"]["token_usage"] == {"input_other": 48 if repair_mode else 40, "input_cached": 12 if repair_mode else 10, "output": 12 if repair_mode else 10}
            records = [record for record in evidence["after"]["process"] if record["kind"] == "model_response" and record["data"]["id"] == "parameters-original"]
            assert len(records) == 1
            assert records[0]["data"]["reasoning_content"] == "Original provider thinking."
            assert records[0]["data"]["reasoning_signature"] == "original-signature"
            assert records[0]["data"]["tool_calls"] == [{"id": "evidence-original", "name": "external_evidence", "args": {"value": "original-plan"}}]
            assert records[0]["data"]["tools_call_extra_content"] == {"evidence-original": {"google": {"thought_signature": "original-tool-signature"}}}
            previous = [record for record in evidence["before"]["process"] if record["kind"] == "model_response" and record["data"]["id"] == "parameters-original"]
            if scenario in {"parameters", "repair"}:
                assert len(previous) == 1
                assert records == previous
            else:
                assert previous == []
        if scenario == "graph":
            assert evidence["parent"]["status"] == "completed"
        if scenario == "concurrent":
            assert evidence["recovery_errors"] == [409]
        assert effects == [{"value": "original-plan"}]
        restored_context = json.dumps(requests[-2]["contexts"] if parameter_mode else requests[2 if scenario in {"request", "save_failure"} else 1]["contexts"])
        for value in ("Temporary original framing.", "data:image/png;base64,", "original-tool-signature", "Original verified persona.", "profile=original", *(() if scenario == "no_thinking" else ("original-signature", "Original provider thinking."))):
            assert value in restored_context
        assert "Edited persona." not in restored_context
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
