"""Real model and tool call ledger for all draft trial scopes."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "mode, expected", [("full", ["a", "b"]), ("single", ["b"]), ("partial", ["b"])]
)
def test_trial_scope_real_pipeline_calls_only_selected_nodes(tmp_path, mode, expected):
    model_calls, effects = [], []

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
                node = "b" if "TRIAL-B" in prompt else "a"
                model_calls.append({"node": node, "prompt": prompt})
                if "submit-" + node in current:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif "effect-" + node in current:
                    response = {
                        "role": "tool",
                        "id": "result-" + node,
                        "tools_call_name": ["submit_task_result"],
                        "tools_call_args": [{"text": "result " + node}],
                        "tools_call_ids": ["submit-" + node],
                    }
                else:
                    response = {
                        "role": "tool",
                        "id": "plan-" + node,
                        "tools_call_name": ["external_evidence"],
                        "tools_call_args": [{"value": node}],
                        "tools_call_ids": ["effect-" + node],
                    }
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExternalService)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    command = [
        sys.executable,
        "-m",
        "tests.agent_teams.trial_worker",
        str(tmp_path),
        f"http://127.0.0.1:{server.server_port}",
        "start",
        "loop_trial_" + mode,
    ]
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "TESTING": "true",
        "ASTRBOT_TEST_MODE": "true",
    }
    try:
        worker = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            env=env,
        )
        assert worker.returncode == 0, worker.stdout + worker.stderr
        result = json.loads((tmp_path / "completed.json").read_text(encoding="utf-8"))
        assert result["status"] == "completed", result
        assert [task["node_id"] for task in result["tasks"]] == expected
        assert effects == expected
        assert {call["node"] for call in model_calls} == set(expected)
        prompt = next(call["prompt"] for call in model_calls if call["node"] == "b")
        assert ("result a" if mode == "full" else "fixed boundary") in prompt
    finally:
        server.shutdown()
        server.server_close()
