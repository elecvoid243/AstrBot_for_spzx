"""Exercise an inline workflow through the real main agent pipeline."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "scenario",
    [
        "inline_persona",
        "inline_override",
        "inline_kb",
        "inline_kb_renamed",
        "inline_kb_empty",
    ],
)
def test_inline_pipeline(tmp_path, scenario):
    requests = []

    class ModelRecorder(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            if len(requests) == 1:
                response = {
                    "role": "tool",
                    "id": "submission",
                    "tools_call_name": ["submit_task_result"],
                    "tools_call_args": [{"text": "Result from pipeline"}],
                    "tools_call_ids": ["submit-1"],
                }
            else:
                response = {"role": "assistant", "completion_text": "Done"}
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelRecorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.agent_teams.checkpoint_worker",
                str(tmp_path),
                f"http://127.0.0.1:{server.server_port}",
                "start",
                scenario,
            ],
            cwd=root,
            env={**os.environ, "PYTHONUTF8": "1", "ASTRBOT_DISABLE_METRICS": "1"},
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        completed = json.loads(
            (tmp_path / "completed.json").read_text(encoding="utf-8")
        )
        assert completed["run"]["status"] == "completed", completed
        assert completed["run"]["result"] == {"text": "Result from pipeline"}
        profile = completed["run"]["tasks"][0]["snapshot"]["profile"]["agent_runner"][
            "config"
        ]
        assert profile["model"]["provider_id"] == "model-alternate"
        assert profile["persona"]["persona_id"] == "writer"
        first = requests[0]
        assert first["provider_id"] == (
            "model-alternate" if scenario == "inline_override" else "model-original"
        )
        actual = json.dumps(first, ensure_ascii=False)
        assert actual.count("Unique responsibility.") == 1
        assert actual.count("Unique task energy") == 1
        assert actual.count("Original verified persona.") == (
            1 if scenario == "inline_persona" else 0
        )
        assert "external_evidence" in first["tools"]
        if scenario.startswith("inline_kb"):
            assert completed["kb_requests"] == (
                [] if scenario == "inline_kb_empty" else [["selected-id"]]
            )
            assert ("Selected knowledge" in actual) is (scenario != "inline_kb_empty")
            assert "Wrong knowledge" not in actual
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
