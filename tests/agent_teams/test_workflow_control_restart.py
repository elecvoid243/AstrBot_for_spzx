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
    "scenario", ["control_pause", "control_continue", "control_cancel", "control_retry", "control_unknown"]
)
def test_control_decision_survives_process_termination(tmp_path, scenario, preview=False):
    """Kill and rebuild a pipeline after a public whole-run or retry decision.

    Args:
        tmp_path: Isolated SQLite and worker communication directory.
        scenario: Pause during a tool, cancellation, or committed retry before dispatch.
        preview: Keep the external service and actual dashboard available for UI QA.
    """
    effects = []
    requests = []
    retry_allowed = False

    class ExternalService(BaseHTTPRequestHandler):
        """External model and tool boundary whose records survive worker loss."""

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                effects.append(payload)
                response = {"text": "External effect receipt"}
            else:
                requests.append(payload)
                context = json.dumps(payload["contexts"])
                if preview and (tmp_path / "fail-model").exists():
                    response = {"error": "Verified provider failure for UI retry acceptance"}
                elif (
                    scenario == "control_retry"
                    and not retry_allowed
                    and len(effects) == 1
                    and "evidence-original" in context
                ):
                    response = {
                        "error": "Verified provider rejection after saved evidence"
                    }
                elif "submit-original" in context:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif "evidence-original" in context:
                    response = {
                        "role": "tool",
                        "id": "submission-original",
                        "tools_call_name": ["submit_task_result"],
                        "tools_call_args": [{"text": "Verified report"}],
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
            if preview:
                (tmp_path / "external-ledger.json").write_text(json.dumps({"effects": effects, "requests": requests}), encoding="utf-8")
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
                worker.kill()
                worker.wait(timeout=10)
        effects_before = len(effects)
        retry_allowed = True
        assert effects_before == 1
        if preview:
            with (tmp_path / "preview.log").open("w", encoding="utf-8") as log:
                worker = subprocess.Popen([*command, "serve", scenario], cwd=root, stdout=log, stderr=subprocess.STDOUT, env=environment)
                try:
                    while worker.poll() is None:
                        time.sleep(1)
                    assert worker.returncode == 0, (tmp_path / "preview.log").read_text(encoding="utf-8")
                finally:
                    if worker.poll() is None:
                        worker.kill()
                        worker.wait(timeout=10)
            return
        if scenario == "control_continue":
            with (tmp_path / "second-worker.log").open("w", encoding="utf-8") as log:
                worker = subprocess.Popen([*command, "recover_decision", scenario], cwd=root, stdout=log, stderr=subprocess.STDOUT, env=environment)
                try:
                    deadline = time.monotonic() + 45
                    while not (tmp_path / "second-ready.json").exists():
                        assert worker.poll() is None, (tmp_path / "second-worker.log").read_text(encoding="utf-8")
                        assert time.monotonic() < deadline
                        time.sleep(0.03)
                finally:
                    worker.kill()
                    worker.wait(timeout=10)
            assert len(effects) == 1 and len(requests) == 1
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
        if scenario == "control_cancel":
            assert after["status"] == "cancelled"
            assert len(effects) == effects_before
        elif scenario == "control_unknown":
            assert after["status"] == "interrupted"
            assert after["tool_calls"][0]["status"] == "unknown"
            assert after["state"]["attempt_id"] == before["state"]["attempt_id"]
            assert after["attempts"] == []
            assert len(effects) == len(requests) == 1
        else:
            assert after["status"] == "completed", evidence
            assert after["state"]["attempt_id"] == before["state"]["attempt_id"]
            if scenario in {"control_pause", "control_continue"}:
                assert len(effects) == 1
                assert len(requests) == 3
                assert before["control"]["status"] == ("paused" if scenario == "control_pause" else "active")
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
                assert (
                    after["attempts"][0]["attempt_id"] != after["state"]["attempt_id"]
                )
                assert after["attempts"][0]["snapshot"] == after["snapshot"]
                assert (
                    after["attempts"][0]["checkpoint"]["stats"]["token_usage"][
                        "input_other"
                    ]
                    == 17
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
        server.shutdown()
        server.server_close()
