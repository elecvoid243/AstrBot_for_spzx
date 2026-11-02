"""Real process termination and external call ledger for draft trials."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize("decision", ["pause", "cancel"])
def test_trial_survives_process_termination(tmp_path, decision):
    effects = []

    class ExternalService(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                effects.append(payload["value"])
                response = {"text": "Evidence " + payload["value"]}
            else:
                contexts = payload["contexts"]
                start = max(
                    i for i, message in enumerate(contexts) if message["role"] == "user"
                )
                prompt = json.dumps(contexts[start], ensure_ascii=False)
                current = json.dumps(contexts[start:], ensure_ascii=False)
                reviewing = "REVIEW-ROUND" in prompt
                publishing = "Deliver" in prompt
                iteration = (
                    2
                    if ("Report revision 2" if reviewing else "Report revision 1")
                    in prompt
                    else 1
                )
                identity = (
                    "publish"
                    if publishing
                    else ("review" if reviewing else "write") + str(iteration)
                )
                if "submit-" + identity in current:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif "effect-" + identity in current:
                    result = (
                        {"approved": iteration == 2, "text": f"Review {iteration}"}
                        if reviewing
                        else {"text": f"Report revision {iteration}"}
                    )
                    response = {
                        "role": "tool",
                        "id": "result-" + identity,
                        "tools_call_name": ["submit_task_result"],
                        "tools_call_args": [{"result_json": json.dumps(result)}]
                        if reviewing
                        else [result],
                        "tools_call_ids": ["submit-" + identity],
                    }
                else:
                    response = {
                        "role": "tool",
                        "id": "plan-" + identity,
                        "tools_call_name": ["external_evidence"],
                        "tools_call_args": [{"value": identity}],
                        "tools_call_ids": ["effect-" + identity],
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
        "tests.agent_teams.trial_worker",
        str(tmp_path),
        f"http://127.0.0.1:{server.server_port}",
    ]
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "TESTING": "true",
        "ASTRBOT_TEST_MODE": "true",
    }
    root = Path(__file__).resolve().parents[2]
    try:
        with (tmp_path / "worker.log").open("w", encoding="utf-8") as log:
            worker = subprocess.Popen(
                [*command, "start", "loop_trial_" + decision],
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )
            try:
                deadline = time.monotonic() + 60
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
        assert effects == ["write1", "review1"]
        recovered = subprocess.run(
            [*command, "recover", "loop_trial_" + decision],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=env,
        )
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        assert evidence["before"]["snapshot"] == evidence["after"]["snapshot"]
        assert evidence["after"]["snapshot"]["trial"]["mode"] == "partial"
        assert evidence["after"]["snapshot"]["trial"]["sources"]["seed"]["content"] == {
            "text": "seed"
        }
        assert "seed" not in evidence["after"]["snapshot"]["trial"]["selected_nodes"]
        if decision == "pause":
            assert evidence["controlled"]["control"]["status"] == "paused"
            assert evidence["after"]["status"] == "completed"
            assert effects == ["write1", "review1", "write2", "review2", "publish"]
        else:
            assert evidence["after"]["status"] == "cancelled"
            assert effects == ["write1", "review1"]
    finally:
        server.shutdown()
        server.server_close()
