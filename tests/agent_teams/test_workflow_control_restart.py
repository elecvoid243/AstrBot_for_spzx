"""Run controls survive killed real pipelines with an external effect ledger."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "scenario",
    [
        "control_pause",
        "control_continue",
        "control_cancel",
        "control_retry",
        "control_saved_receipt",
        "control_saved_receipt_terminal",
        "control_unknown",
        "control_request",
        "control_capacity",
        "control_mixed",
        "control_shutdown",
        "dispatch_commit",
    ],
)
def test_control_decision_survives_process_termination(
    tmp_path, scenario, preview=False
):
    """Kill and rebuild a pipeline after a committed control or dispatch decision.

    Args:
        tmp_path: Isolated SQLite and worker communication directory.
        scenario: Control or scheduling boundary where the worker stops.
        preview: Keep the external service and actual dashboard available for UI QA.
    """
    effects = []
    requests = []
    retry_allowed = False
    release_model = threading.Event()

    class ExternalService(BaseHTTPRequestHandler):
        """External model and tool boundary whose records survive worker loss."""

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            """Record requests and effects outside the execution process.

            Returns:
                None after returning a controlled model or tool response.
            """
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                effects.append(payload)
                response = {"text": "External effect receipt"}
            else:
                requests.append(payload)
                if scenario == "control_capacity":
                    release_model.wait(timeout=25)
                context = json.dumps(payload["contexts"])
                if preview and (tmp_path / "fail-model").exists():
                    response = {
                        "error": "Verified provider failure for UI retry acceptance"
                    }
                elif not retry_allowed and (
                    scenario == "control_retry"
                    or scenario == "control_saved_receipt"
                    and "evidence-original" in context
                ):
                    response = {
                        "error": "Verified provider rejection after saved evidence"
                    }
                elif (
                    scenario == "control_saved_receipt_terminal"
                    and "evidence-original" in context
                ):
                    response = {
                        "role": "assistant",
                        "completion_text": "Missing task submission.",
                    }
                elif "submit-original" in context:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif (
                    "evidence-original" in context
                    or scenario == "control_saved_receipt"
                    and "Deliver without a connector." in context
                ):
                    result = (
                        "Verified final report"
                        if scenario == "dispatch_commit"
                        and "Finish the report for Frozen field notes." in context
                        else "Verified delivery"
                        if "Deliver without a connector." in context
                        else "Verified report"
                    )
                    response = {
                        "role": "tool",
                        "id": "submission-original",
                        "tools_call_name": ["submit_task_result"],
                        "tools_call_args": [{"text": result}],
                        "tools_call_ids": ["submit-original"],
                        "usage": {"input_other": 11, "output": 5},
                    }
                else:
                    response = {
                        "role": "tool",
                        "id": "plan-original",
                        "tools_call_name": ["external_evidence"],
                        "tools_call_args": [{"value": "original-plan"}],
                        "tools_call_ids": ["evidence-original"],
                        "usage": {"input_other": 17, "output": 9},
                    }
                    if scenario == "control_mixed":
                        response["tools_call_args"] = [{"value": "b" if '"text": "b"' in context or '"content": "b"' in context else "a"}]
            if preview:
                (tmp_path / "external-ledger.json").write_text(
                    json.dumps({"effects": effects, "requests": requests}),
                    encoding="utf-8",
                )
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExternalService)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-m",
        "tests.agent_teams.checkpoint_worker",
        str(tmp_path),
        f"http://127.0.0.1:{server.server_port}",
    ]
    environment = {
        **os.environ,
        "PYTHONUTF8": "1",
        "ASTRBOT_ROOT": str(tmp_path / "runtime"),
        "TESTING": "true",
        "ASTRBOT_TEST_MODE": "true",
    }
    try:
        with (tmp_path / "worker.log").open("w", encoding="utf-8") as log:
            worker = subprocess.Popen(
                [*command, "start", scenario],
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
            )
            try:
                deadline = time.monotonic() + 45
                while not (tmp_path / "kill-ready.json").exists():
                    assert worker.poll() is None, (tmp_path / "worker.log").read_text(
                        encoding="utf-8"
                    )
                    assert time.monotonic() < deadline, (
                        tmp_path / "worker.log"
                    ).read_text(encoding="utf-8")
                    time.sleep(0.03)
            finally:
                if scenario == "control_shutdown":
                    assert worker.wait(timeout=10) == 0, (
                        tmp_path / "worker.log"
                    ).read_text(encoding="utf-8")
                else:
                    worker.kill()
                    worker.wait(timeout=10)
        effects_before = len(effects)
        if scenario == "dispatch_commit":
            assert len(requests) == 3
        retry_allowed = True
        assert effects_before == (
            0
            if scenario in {"control_request", "control_capacity", "control_retry"}
            else 2
            if scenario == "control_mixed"
            else 1
        )
        if preview:
            with (tmp_path / "preview.log").open("w", encoding="utf-8") as log:
                worker = subprocess.Popen(
                    [*command, "serve", scenario],
                    cwd=root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=environment,
                )
                try:
                    while worker.poll() is None:
                        time.sleep(1)
                    assert worker.returncode == 0, (tmp_path / "preview.log").read_text(
                        encoding="utf-8"
                    )
                finally:
                    if worker.poll() is None:
                        worker.kill()
                        worker.wait(timeout=10)
            return
        if scenario in {"control_continue", "control_capacity"}:
            with (tmp_path / "second-worker.log").open("w", encoding="utf-8") as log:
                worker = subprocess.Popen(
                    [*command, "recover_decision", scenario],
                    cwd=root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=environment,
                )
                try:
                    deadline = time.monotonic() + 45
                    while not (tmp_path / "second-ready.json").exists():
                        assert worker.poll() is None, (
                            tmp_path / "second-worker.log"
                        ).read_text(encoding="utf-8")
                        assert time.monotonic() < deadline
                        time.sleep(0.03)
                finally:
                    worker.kill()
                    worker.wait(timeout=10)
            assert len(effects) == (0 if scenario == "control_capacity" else 1)
            assert len(requests) == (0 if scenario == "control_capacity" else 1)
        rebuilt = subprocess.run(
            [*command, "recover", scenario],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
            env=environment,
        )
        assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
        evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        before, after = evidence["before"], evidence["after"]
        assert after["snapshot"] == before["snapshot"]
        if scenario == "dispatch_commit":
            assert (
                before["run_id"]
                == after["run_id"]
                == evidence["reopened"]["source"]["id"]
            )
            assert before["status"] == "running"
            assert after["status"] == evidence["reopened"]["status"] == "completed"
            predecessor, claimed = before["tasks"]
            saved, delivered = after["tasks"]
            assert predecessor == saved
            assert saved["status"] == "completed"
            assert claimed["status"] == "running"
            assert claimed["state"].get("execution_id") is None
            assert delivered["status"] == "completed"
            assert delivered["run_id"] == claimed["run_id"]
            assert delivered["state"]["attempt_id"] == claimed["state"]["attempt_id"]
            assert delivered["snapshot"] == claimed["snapshot"]
            assert delivered["snapshot"]["inputs"] == {"topic": "Frozen field notes"}
            assert evidence["predecessor_result"] == {"text": "Verified report"}
            assert evidence["result"] == {"text": "Verified final report"}
            assert after["result_ref"] == delivered["result_ref"]
            assert [
                event["kind"]
                for event in delivered["events"]
                if event["kind"] in {"task.running", "result.accepted", "task.completed"}
            ] == ["task.running", "result.accepted", "task.completed"]
            assert len(effects) == 2
            assert len(requests) == 6
            assert all(
                "Finish the report for Frozen field notes." in json.dumps(request)
                for request in requests[3:]
            )
        elif scenario == "control_saved_receipt_terminal":
            assert before == after
            assert evidence["retry_error"]["status"] == 409
            assert evidence["recovery_error"]["status"] == 409
            assert "original checkpoint" in evidence["recovery_error"]["message"]
            assert effects == [{"value": "original-plan"}]
            assert len(requests) == 2
        elif scenario == "control_saved_receipt":
            original, pending = before["tasks"]
            resumed, delivered = after["tasks"]
            assert original["status"] == "awaiting_result"
            assert pending["status"] != "completed"
            assert evidence["retry_error"]["status"] == 409
            assert "saved tool results" in evidence["retry_error"]["body"]["message"]
            assert after["status"] == "completed"
            assert resumed["run_id"] == original["run_id"]
            assert resumed["state"]["attempt_id"] == original["state"]["attempt_id"]
            assert delivered["run_id"] == pending["run_id"]
            assert delivered["state"]["attempt_id"] == pending["state"]["attempt_id"]
            assert resumed["attempts"] == []
            [saved] = original["tool_calls"]
            reused = next(
                call
                for call in resumed["tool_calls"]
                if call["record_id"] == saved["record_id"]
            )
            assert saved["status"] == reused["status"] == "applied"
            assert saved["raw_results"] == reused["raw_results"]
            assert (
                saved["raw_results"][0]["content"][0]["text"]
                == "External effect receipt"
            )
            assert (
                sum(
                    record["kind"] == "tool_result"
                    and record["data"].get("tool_record_id") == saved["record_id"]
                    for record in resumed["process"]
                )
                == 1
            )
            assert effects == [{"value": "original-plan"}]
            assert "External effect receipt" in json.dumps(requests[2]["contexts"])
            assert evidence["result"] == {"text": "Verified delivery"}
            assert after["result_ref"] == delivered["result_ref"]
            for task in (resumed, delivered):
                accepted = [
                    event["data"]
                    for event in task["events"]
                    if event["kind"] == "result.accepted"
                ]
                assert [event["attempt_id"] for event in accepted] == [
                    task["state"]["attempt_id"]
                ]
                assert (
                    sum(event["kind"] == "task.completed" for event in task["events"])
                    == 1
                )
        elif scenario == "control_mixed":
            assert after["control"]["status"] == "active"
            assert after["tasks"][0]["status"] == "interrupted"
            assert after["tasks"][0]["tool_calls"][0]["status"] == "unknown"
            assert after["tasks"][1]["status"] == "completed"
            assert len(effects) == 2
            assert len(requests) == 4
        elif scenario == "control_capacity":
            assert after["progress"]["running"] == 1
            assert after["tasks"][0]["status"] == "running"
            assert after["tasks"][1]["status"] == "pending"
            assert len(requests) == 1 and not effects
            assert after["tasks"][0]["state"]["attempt_id"] == before["tasks"][0]["state"]["attempt_id"]
        elif scenario == "control_request":
            assert after["control"]["status"] == "paused"
            assert after["state"]["attempt_id"] == before["state"]["attempt_id"]
            assert len(effects) == 0
            assert len(requests) == 1
        elif scenario == "control_cancel":
            assert after["status"] == "cancelled"
            original, pending = before["tasks"]
            cancelled, successor = after["tasks"]
            assert successor["run_id"] == pending["run_id"]
            assert successor["state"]["attempt_id"] == pending["state"]["attempt_id"]
            assert successor["result_ref"] is None
            assert not any(event["kind"] == "task.running" for event in successor["events"])
            assert cancelled["run_id"] == original["run_id"]
            assert cancelled["state"]["attempt_id"] == original["state"]["attempt_id"]
            [call] = cancelled["tool_calls"]
            assert call["status"] in {"started", "unknown"}
            assert call["raw_results"] == call["available_actions"] == []
            assert cancelled["result_ref"] is None
            assert not any(
                event["kind"] == "result.accepted" for event in cancelled["events"]
            )
            assert sum(event["kind"] == "run.cancel" for event in after["events"]) == 1
            assert effects == [{"value": "original-plan"}]
        elif scenario == "control_unknown":
            assert after["status"] == "interrupted"
            assert after["tool_calls"][0]["status"] == "unknown"
            assert after["state"]["attempt_id"] == before["state"]["attempt_id"]
            assert after["attempts"] == []
            assert len(effects) == len(requests) == 1
        else:
            assert after["status"] == "completed", evidence
            assert after["tasks"][0]["state"]["attempt_id"] == before["tasks"][0]["state"]["attempt_id"]
            if scenario in {"control_pause", "control_continue", "control_shutdown"}:
                assert len(effects) == 1
                assert len(requests) == 3
                assert before["control"]["status"] == (
                    "active" if scenario == "control_continue" else "paused"
                )
                if scenario == "control_shutdown":
                    shutdown = json.loads(
                        (tmp_path / "shutdown.json").read_text(encoding="utf-8")
                    )
                    assert shutdown["before"]["status"] == "running"
                    assert shutdown["after"]["tasks"][0]["status"] == "interrupted"
                    assert shutdown["after"]["control"]["status"] == "paused"
                    assert (
                        shutdown["after"]["tasks"][0]["state"]["attempt_id"]
                        == shutdown["before"]["tasks"][0]["state"]["attempt_id"]
                    )
                assert (
                    len(
                        [
                            event
                            for event in after["events"]
                            if event["kind"] == "run.pause"
                        ]
                    )
                    == 1
                )
            else:
                after = after["tasks"][0]
                assert (
                    after["attempts"][0]["attempt_id"] != after["state"]["attempt_id"]
                )
                assert after["attempts"][0]["snapshot"] == after["snapshot"]
                assert (
                    after["attempts"][0]["checkpoint"]["stats"]["token_usage"][
                        "input_other"
                    ]
                    == 0
                )
                assert any(
                    record["attempt_id"] == after["attempts"][0]["attempt_id"]
                    for record in after["process"]
                )
                assert (
                    len(
                        [
                            event
                            for event in after["events"]
                            if event["kind"] == "attempt.retried"
                        ]
                    )
                    == 1
                )
    finally:
        release_model.set()
        server.shutdown()
        server.server_close()
