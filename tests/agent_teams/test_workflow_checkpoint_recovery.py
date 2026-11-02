"""Recovery controls preserve attempts and reject unsupported replay."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


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


@pytest.mark.parametrize("scenario", ["response", "request", "no_thinking", "started", "submit_pre", "submit_post", "save_failure", "observation_failure", "missing_model", "changed_tool", "parameters", "repair", "parameters_checkpoint", "repair_checkpoint", "parameters_checkpoint_failure", "tool_progress_gap", "graph", "concurrent", "tool_result", "tool_checkpoint", "tool_applied", "batch_unknown", "submit_mixed_pre", "submit_mixed_post", "submit_custom_pre", "submit_custom_post", "submit_file_pre", "submit_file_post", "parameters_tool_result", "tool_image_protocol", "tool_save_failure", "tool_observation_failure", "legacy_response", "legacy_upgrade_failure", "partial_generator", "generator_error", "completed_generator", "submit_conflict_result", "submit_conflict_applied", "submit_conflict_pre", "overflow_result", "overflow_applied", "overflow_legacy_result", "overflow_legacy_applied", "overflow_request", "overflow_generator", "overflow_legacy_unaligned", "overflow_legacy_request", "overflow_restore_failure"])
def test_saved_model_plan_survives_forced_process_termination(tmp_path, scenario, preview=False):
    """Resume the real execution chain without repeating the saved model plan.

    Args:
        tmp_path: Shared on-disk data; the external ledger belongs to this process.
        scenario: Saved response, missing response, started tool or submission boundary.
        preview: Serve the actual built dashboard against the interrupted database.
    """
    requests = []
    effects = []
    file_reads = []
    large_result = "Verified external evidence. " * 15000 + "COMPLETE_END"
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
            if self.path == "/query" and scenario == "tool_progress_gap":
                response = {"status": "succeeded", "evidence": "External receipt", "results": [{"content": [{"type": "text", "text": "Verified external evidence."}]}]}
            elif self.path == "/tool":
                effects.append(payload)
                response = {"text": "Verified external evidence."}
                if scenario.startswith("overflow"):
                    response["text"] = large_result
                if payload.get("part"):
                    response = {"text": "Partial verified evidence." if payload["part"] == "initial" else "Complete verified evidence."}
                    if scenario == "overflow_generator" and payload["part"] == "initial":
                        response["text"] = large_result
                if scenario == "tool_image_protocol":
                    response = {"raw_result": {"content": [{"type": "text", "text": "Verified external evidence."}, {"type": "image", "mimeType": "image/png", "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg=="}]}}
            else:
                requests.append(payload)
                context = json.dumps(payload["contexts"])
                if scenario.startswith("overflow") and len(requests) == 2:
                    message = next(message for message in payload["contexts"] if message.get("tool_call_id") == "evidence-original")
                    path = Path(message["content"].split("written to `", 1)[1].split("`", 1)[0])
                    assert path.resolve().parent == (tmp_path / "overflow").resolve()
                    file_reads.append({"path": str(path), "content": path.read_text(encoding="utf-8") if path.is_file() else None})
                if "submit-original" in context:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                    if scenario.startswith("submit_conflict") and "submit-conflict" not in context:
                        response = {"role": "tool", "id": "submission-conflict", "tools_call_name": ["submit_task_result"], "tools_call_args": [{"text": "Conflicting report."}], "tools_call_ids": ["submit-conflict"]}
                elif "evidence-original" in context:
                    response = {"role": "tool", "id": "submission-original", "tools_call_name": ["submit_task_result"], "tools_call_args": [{"text": "Original verified report."}], "tools_call_ids": ["submit-original"]}
                    if scenario.startswith(("submit_custom", "submit_file")):
                        result = {"text": "Original verified report."}
                        if scenario.startswith("submit_file"):
                            if "file-original" not in context:
                                response = {"role": "tool", "id": "file-plan", "tools_call_name": ["store_task_file"], "tools_call_args": [{"filename": "report.txt", "content": "Complete original file report."}], "tools_call_ids": ["file-original"]}
                            else:
                                stored = next(message for message in payload["contexts"] if message.get("tool_call_id") == "file-original")
                                result["files"] = [{"file_id": json.loads(stored["content"])["file_id"]}]
                        if response["id"] == "submission-original":
                            response["tools_call_args"] = [{"result_json": json.dumps(result)}]
                    if scenario.startswith("submit_mixed"):
                        response["tools_call_name"].append("external_evidence")
                        response["tools_call_args"].append({"value": "after-submission"})
                        response["tools_call_ids"].append("after-submit-original")
                else:
                    response = {"role": "tool", "id": "plan-original", "tools_call_name": ["external_evidence"], "tools_call_args": [{"value": "original-plan"}], "tools_call_ids": ["evidence-original"], "reasoning_content": "Original provider thinking.", "reasoning_signature": "original-signature", "tools_call_extra_content": {"evidence-original": {"google": {"thought_signature": "original-tool-signature"}}}}
                    if scenario == "batch_unknown":
                        response.update({"tools_call_name": ["external_evidence"] * 3,
                            "tools_call_args": [{"value": value} for value in ("original-plan", "unknown-effect", "unstarted")],
                            "tools_call_ids": ["evidence-original", "evidence-unknown", "evidence-unstarted"]})
                    if payload.get("parameter_query"):
                        response["id"] = "parameters-original"
                        if repair_mode and not payload.get("repair_query"):
                            response = {"role": "assistant", "id": "empty-parameters", "completion_text": ""}
                    if scenario == "no_thinking":
                        response.pop("reasoning_content")
                        response.pop("reasoning_signature")
                if parameter_mode:
                    response["usage"] = {"input_other": 8, "input_cached": 2, "output": 2}
            (tmp_path / "external-ledger.json").write_text(json.dumps({"requests": requests, "effects": effects, "file_reads": file_reads}), encoding="utf-8")
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
                assert len(requests) == (3 if repair_mode or scenario.startswith(("submit_file", "submit_conflict")) else 2 if scenario.startswith("submit_") or parameter_mode else 1)
                assert effects == ([{"value": "original-plan", "part": "initial"}, {"value": "original-plan", "part": "final"}] if scenario in {"completed_generator", "overflow_generator"} else [{"value": "original-plan", "part": "initial"}] if scenario in {"partial_generator", "generator_error"} else [{"value": "original-plan"}, {"value": "unknown-effect"}] if scenario == "batch_unknown" else [{"value": "original-plan"}] if scenario == "started" or scenario.startswith(("submit_", "tool_", "overflow")) or scenario == "parameters_tool_result" else [])
                worker.kill()
                worker.wait(timeout=10)
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=10)
        if preview:
            subprocess.run([*command, "serve", scenario], cwd=root, check=True, env={**os.environ, "PYTHONUTF8": "1"})
            return
        if scenario.startswith("overflow"):
            overflow_dir = (tmp_path / "overflow").resolve()
            spilled = list(overflow_dir.glob("*.txt"))
            assert len(spilled) == 1
            for path in spilled:
                assert path.resolve().parent == overflow_dir
                assert path.read_text(encoding="utf-8") == large_result
                path.unlink()
                assert not path.exists()
                if scenario == "overflow_restore_failure":
                    path.mkdir()
        resumed = subprocess.run([*command, "recover", scenario], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=40, env={**os.environ, "PYTHONUTF8": "1"})
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        if scenario == "overflow_restore_failure":
            assert evidence["after"]["status"] == "blocked"
            assert "Failed to restore saved tool output" in evidence["after"]["state"]["reason"]
            assert evidence["after"]["state"]["attempt_id"] == evidence["before"]["state"]["attempt_id"]
            assert evidence["after"]["tool_calls"] == evidence["before"]["tool_calls"]
            assert len(requests) == 1
            assert file_reads == []
            for path in spilled:
                assert path.resolve().parent == overflow_dir
                path.rmdir()
            repaired = subprocess.run([*command, "recover", scenario], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=40, env={**os.environ, "PYTHONUTF8": "1"})
            assert repaired.returncode == 0, repaired.stdout + repaired.stderr
            evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        if scenario == "tool_progress_gap":
            before_process = evidence["before"]["process"]
            assert not any(record["kind"] == "tool_progress" for record in before_process)
            calls = evidence["after"]["tool_calls"]
            call = next(item for item in calls if item["name"] == "external_evidence")
            records = [record["data"] for record in evidence["after"]["process"] if record["kind"] == "tool_result" and record["data"].get("tool_record_id") == call["record_id"] and record["data"].get("index") == 0]
            assert len(records) == 1 and records[0]["raw_result"]["content"][0]["text"] == "Verified external evidence.", (call["record_id"], [r["data"] for r in evidence["after"]["process"] if r["kind"] == "tool_result"])
        if scenario in {"response", "parameters", "repair", "parameters_checkpoint", "repair_checkpoint"}:
            expected_id = "plan-original" if scenario == "response" else "parameters-original"
            previous = [record for record in evidence["before"]["process"] if record["kind"] == "model_response" and record["data"]["id"] == expected_id]
            current_records = [record for record in evidence["after"]["process"] if record["kind"] == "model_response" and record["data"]["id"] == expected_id]
            assert len(current_records) == 1
            saved = current_records[0]["data"]
            assert saved["receipt_id"]
            matching = [entry for entry in evidence["after"]["usage"]["ledger"] if entry["receipt_id"] == saved["receipt_id"]]
            assert len(matching) == 1 and matching[0]["invocation_id"] == saved["invocation_id"]
            if previous:
                assert len(previous) == 1 and previous[0]["data"] == saved
            recorded_calls = {call["record_id"] for call in evidence["after"]["tool_calls"]}
            results = [record for record in evidence["after"]["process"] if record["kind"] == "tool_result"]
            assert results and all(record["data"].get("tool_record_id") in recorded_calls for record in results)
            if scenario in {"parameters", "repair"}:
                parent = [record for record in evidence["after"]["process"] if record["kind"] == "model_response" and record["data"]["id"] == "plan-original"]
                assert len(parent) == 1 and parent[0]["data"]["receipt_id"] != saved["receipt_id"]
        if scenario.startswith("overflow"):
            assert len(file_reads) == 1
            assert file_reads[0]["content"] is not None
            assert file_reads[0]["content"] == large_result
            assert file_reads[0]["content"].endswith("COMPLETE_END")
            protocol_results = [message for message in requests[1]["contexts"] if message.get("tool_call_id") == "evidence-original"]
            assert len(protocol_results) == 1
            raw_results = evidence["before"]["tool_calls"][0]["raw_results"]
            if scenario == "overflow_generator":
                assert len(raw_results) == 2
                assert "Complete verified evidence." in protocol_results[0]["content"]
            if scenario == "overflow_legacy_unaligned":
                assert [raw["content"][0]["type"] for raw in raw_results] == ["resource_link", "text"]
            assert raw_results[-1 if scenario == "overflow_legacy_unaligned" else 0]["content"][0]["text"] == large_result
            assert evidence["after"]["tool_calls"][0]["raw_results"] == evidence["before"]["tool_calls"][0]["raw_results"]
        if scenario.startswith("submit_conflict"):
            before = next(call for call in evidence["before"]["tool_calls"] if call["call_id"] == "submit-conflict")
            after = next(call for call in evidence["after"]["tool_calls"] if call["call_id"] == "submit-conflict")
            assert "A different result was already submitted" in after["raw_results"][0]["content"][0]["text"]
            if scenario.endswith("pre"):
                assert before["raw_results"] == []
                assert after["status"] == "applied"
                assert after["context_position"] is not None
            else:
                assert before["status"] == ("applied" if scenario.endswith("applied") else "result_saved")
                assert before["context_position"] == (7 if scenario.endswith("applied") else None)
                assert after == before
            recorded = [record["data"]["raw_result"] for record in evidence["after"]["process"] if record["kind"] == "tool_result" and record["data"]["id"] == "submit-conflict"]
            assert recorded == after["raw_results"]
            assert evidence["after"]["status"] == "completed"
            assert evidence["result"] == {"text": "Original verified report."}
            assert len(evidence["after"]["artifacts"]) == 1
            assert sum(event["kind"] == "task.completed" for event in evidence["after"]["events"]) == 1
            assert len(requests) == (4 if scenario.endswith("pre") else 3)
            assert effects == [{"value": "original-plan"}]
            return
        if scenario in {"legacy_response", "legacy_upgrade_failure"}:
            assert evidence["before"]["tool_calls"] == []
        if scenario in {"partial_generator", "generator_error"}:
            assert evidence["error"]["status"] == 409
            assert evidence["after"]["status"] in {"interrupted", "blocked"}
            assert evidence["after"]["recovery"]["available"] is False
            call = evidence["after"]["tool_calls"][0]
            assert call["status"] == "unknown"
            assert call["raw_results"][0]["content"][0]["text"] == "Partial verified evidence."
            assert call["context_position"] is None
            assert effects == [{"value": "original-plan", "part": "initial"}]
            assert len(requests) == 1
            return
        if scenario == "tool_save_failure":
            assert evidence["error"]["status"] == 409
            assert evidence["after"]["status"] == "failed"
            assert "Failed to save execution checkpoint" in evidence["after"]["state"]["reason"]
            assert evidence["after"]["recovery"]["available"] is False
            assert effects == [{"value": "original-plan"}]
            assert len(requests) == 1
            return
        if scenario == "legacy_upgrade_failure":
            failed = evidence["after"]
            assert failed["status"] == "failed"
            assert "Failed to save execution checkpoint" in failed["state"]["reason"]
            assert len(requests) == 1
            assert effects == []
            repaired = subprocess.run([*command, "recover_storage", scenario], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=40, env={**os.environ, "PYTHONUTF8": "1"})
            assert repaired.returncode == 0, repaired.stdout + repaired.stderr
            evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
            assert evidence["after"]["state"]["attempt_id"] == failed["state"]["attempt_id"]
            assert evidence["after"]["snapshot"] == failed["snapshot"]
        if scenario == "batch_unknown":
            assert evidence["after"]["status"] == "blocked"
            assert "confirmation" in evidence["after"]["state"]["reason"]
            assert evidence["after"]["state"]["attempt_id"] == evidence["before"]["state"]["attempt_id"]
            assert effects == [{"value": "original-plan"}, {"value": "unknown-effect"}, {"value": "unstarted"}]
            assert len(requests) == 1
            assert [call["status"] for call in evidence["before"]["tool_calls"]] == ["result_saved", "started", "intent"]
            assert [call["status"] for call in evidence["after"]["tool_calls"]] == ["result_saved", "unknown", "result_saved"]
            assert evidence["after"]["recovery"]["available"] is False
            return
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
        if scenario in {"legacy_response", "legacy_upgrade_failure"}:
            assert [call["call_id"] for call in evidence["after"]["tool_calls"]] == ["evidence-original", "submit-original"]
            assert all(call["status"] == "applied" for call in evidence["after"]["tool_calls"])
        assert evidence["after"]["snapshot"] == evidence["before"]["snapshot"]
        assert evidence["after"]["state"]["attempt_id"] == evidence["before"]["state"]["attempt_id"]
        if scenario.startswith("submit_file"):
            assert evidence["result"]["text"] == "Original verified report."
            assert len(evidence["result"]["files"]) == 1
            assert evidence["file"]["filename"] == "report.txt"
            assert evidence["file"]["content"] == "Complete original file report."
        else:
            assert evidence["result"] == {"text": "Original verified report."}
        assert evidence["late_errors"] == [409, 409, 409, 409]
        assert evidence["repeated"] == evidence["after"]
        assert len(evidence["after"]["artifacts"]) == 1
        assert sum(event["kind"] == "task.completed" for event in evidence["after"]["events"]) == 1
        early_finish = scenario in {"submit_post", "submit_custom_post", "submit_file_post"}
        if early_finish:
            submitted = next(call for call in evidence["after"]["tool_calls"] if call["name"] == "submit_task_result")
            assert submitted["status"] == "result_saved"
            assert submitted["raw_results"][0]["content"][0]["text"] == "Result accepted."
        steps = (2 if early_finish else 3) + int(scenario.startswith("submit_file"))
        assert evidence["after"]["recovery"]["steps_used"] == steps
        assert evidence["after"]["recovery"]["model_calls"] == (6 if repair_mode else 5 if parameter_mode else steps)
        assert len(requests) == (6 if repair_mode else 5 if parameter_mode else 4 if scenario in {"request", "save_failure"} else steps)
        if parameter_mode:
            assert evidence["stats"]["token_usage"] == {"input_other": 48 if repair_mode else 40, "input_cached": 12 if repair_mode else 10, "output": 12 if repair_mode else 10}
            records = [record for record in evidence["after"]["process"] if record["kind"] == "model_response" and record["data"]["id"] == "parameters-original"]
            assert len(records) == 1
            assert records[0]["data"]["reasoning_content"] == "Original provider thinking."
            assert records[0]["data"]["reasoning_signature"] == "original-signature"
            assert records[0]["data"]["tool_calls"] == [{"id": "evidence-original", "name": "external_evidence", "args": {"value": "original-plan"}}]
            assert records[0]["data"]["tools_call_extra_content"] == {"evidence-original": {"google": {"thought_signature": "original-tool-signature"}}}
            previous = [record for record in evidence["before"]["process"] if record["kind"] == "model_response" and record["data"]["id"] == "parameters-original"]
            if scenario in {"parameters", "repair", "parameters_tool_result"}:
                assert len(previous) == 1
                assert records == previous
            else:
                assert previous == []
        if scenario == "graph":
            assert evidence["parent"]["status"] == "completed"
        if scenario == "concurrent":
            assert evidence["recovery_errors"] == [409]
        assert effects == ([{"value": "original-plan", "part": "initial"}, {"value": "original-plan", "part": "final"}] if scenario in {"completed_generator", "overflow_generator"} else [{"value": "original-plan"}, {"value": "after-submission"}] if scenario.startswith("submit_mixed") else [{"value": "original-plan"}])
        if scenario == "completed_generator":
            call = evidence["before"]["tool_calls"][0]
            assert call["status"] == "result_saved"
            assert [raw["content"][0]["text"] for raw in call["raw_results"]] == ["Partial verified evidence.", "Complete verified evidence."]
            saved_context = json.dumps(requests[1]["contexts"])
            assert saved_context.count("Partial verified evidence.") == 1
            assert saved_context.count("Complete verified evidence.") == 1
        if scenario in {"tool_result", "tool_checkpoint", "tool_applied"}:
            calls = evidence["before"]["tool_calls"]
            assert len(calls) == 1
            assert calls[0]["status"] == ("applied" if scenario == "tool_applied" else "result_saved")
            assert calls[0]["raw_results"][0]["content"][0]["text"] == "Verified external evidence."
            if scenario != "tool_applied":
                assert calls[0]["context_position"] is None
            restored = evidence["after"]["tool_calls"][0]
            assert restored["status"] == "applied"
            assert restored["context_position"] is not None
            if scenario != "tool_applied":
                assert restored["execution_id"] != evidence["before"]["state"]["execution_id"]
            assert len([message for message in requests[1]["contexts"] if message.get("tool_call_id") == "evidence-original"]) == 1
        restored_context = json.dumps(requests[-2]["contexts"] if parameter_mode else requests[2 if scenario in {"request", "save_failure"} else 1]["contexts"])
        for value in ("Temporary original framing.", "data:image/png;base64,", "original-tool-signature", "Original verified persona.", "profile=original", *(() if scenario == "no_thinking" else ("original-signature", "Original provider thinking."))):
            assert value in restored_context
        assert "Edited persona." not in restored_context
        if scenario == "tool_image_protocol":
            assert "Image from tool 'external_evidence'" in restored_context
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
