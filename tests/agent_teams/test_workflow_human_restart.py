"""Human boundaries survive terminated pipelines with an independent ledger."""

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
        "human_wait_pre",
        "human_wait_post",
        "human_answer_pre",
        "human_answer_post",
        "human_consume_pre",
        "human_consume_post",
        "human_shutdown",
        "human_shutdown_wait",
        "human_approval_pre",
        "human_approval_post",
        "human_consume_owned_post",
        "human_consume_owned_shutdown",
    ],
)
def test_human_boundary_survives_real_process_rebuild(tmp_path, scenario):
    requests, effects = [], []

    class ExternalService(BaseHTTPRequestHandler):
        """Controlled external model/tool; records outlive all worker processes."""

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            """Return a tool plan, explicit result submission and final response.

            Returns:
                None after saving the independent external observation.
            """
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                effects.append(payload)
                response = {"text": "External evidence for Asia"}
            else:
                requests.append(payload)
                context = json.dumps(payload["contexts"])
                if "submit-original" in context:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif "evidence-original" in context:
                    response = {
                        "role": "tool",
                        "id": "submission-original",
                        "tools_call_name": ["submit_task_result"],
                        "tools_call_args": [{"text": "Verified report for Asia"}],
                        "tools_call_ids": ["submit-original"],
                        "usage": {"input_other": 11, "output": 5},
                    }
                else:
                    response = {
                        "role": "tool",
                        "id": "plan-original",
                        "tools_call_name": ["external_evidence"],
                        "tools_call_args": [{"value": "Asia"}],
                        "tools_call_ids": ["evidence-original"],
                        "usage": {"input_other": 17, "output": 9},
                    }
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExternalService)
    threading.Thread(target=server.serve_forever, daemon=True).start()
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
    root = Path(__file__).resolve().parents[2]
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
                if scenario.startswith("human_shutdown") or scenario.endswith(
                    "owned_shutdown"
                ):
                    assert worker.wait(timeout=10) == 0
                else:
                    worker.kill()
                    worker.wait(timeout=10)
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=10)
        assert (len(requests), len(effects)) == (
            (3, 1) if scenario.startswith("human_approval") else (0, 0)
        )
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
        assert after["status"] == "completed", after
        if scenario.startswith("human_approval"):
            assert after["todos"][0]["status"] == "approved"
            assert after["todos"][0]["artifact_ref"]["version"] == 1
            assert evidence["exported"]["version"] == 1
            assert evidence["exported"]["content"] == {
                "text": "Verified report for Asia"
            }
            assert before["todos"][0]["status"] == (
                "answered" if scenario.endswith("post") else "pending"
            )
            assert (
                len([e for e in after["events"] if e["kind"] == "human.consumed"]) == 1
            )
            assert len(effects) == 1 and len(requests) == 3
            return
        assert after["todos"][0]["response"]["answer"] == "Asia"
        assert after["todos"][0]["consumed_at"] is not None
        assert len([e for e in after["events"] if e["kind"] == "human.consumed"]) == 1
        assert (
            after["tasks"][1]["snapshot"]["task"]
            == "Read evidence for Asia and submit a report."
        )
        assert (
            before["tasks"][1]["state"]["attempt_id"]
            == after["tasks"][1]["state"]["attempt_id"]
        )
        assert after["tasks"][1]["result"] == {"text": "Verified report for Asia"}
        assert len(effects) == 1 and len(requests) == 3
        assert after["tasks"][1]["tool_calls"][0]["status"] == "applied"
        assert (
            before["todos"] == []
            if scenario == "human_wait_pre"
            else before["todos"][0]["status"]
            == (
                "answered"
                if scenario
                in {"human_answer_post", "human_consume_pre", "human_shutdown"}
                else "completed"
                if scenario
                in {
                    "human_consume_post",
                    "human_consume_owned_post",
                    "human_consume_owned_shutdown",
                }
                else "pending"
            )
        )
    finally:
        server.shutdown()
        server.server_close()
