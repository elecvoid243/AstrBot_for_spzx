"""Exercise an inline workflow through the real main agent pipeline."""

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


def test_same_member_graph_pipeline_does_not_share_history(tmp_path):
    requests = []

    class ModelRecorder(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                response = {"text": "secret-tool-output"}
            else:
                requests.append(payload)
                index = len(requests)
                if index == 1:
                    name, args = "external_evidence", {"value": "secret-tool-argument"}
                elif index == 2:
                    name, args = (
                        "submit_task_result",
                        {
                            "result_json": json.dumps(
                                {"public": "selected-value", "secret": "secret-result"}
                            )
                        },
                    )
                elif index == 4:
                    name, args = "submit_task_result", {"text": "Final"}
                else:
                    name = None
                response = (
                    {
                        "role": "tool",
                        "id": f"call-{index}",
                        "tools_call_name": [name],
                        "tools_call_args": [args],
                        "tools_call_ids": [f"call-{index}"],
                    }
                    if name
                    else {"role": "assistant", "completion_text": "Done"}
                )
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
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.agent_teams.checkpoint_worker",
                str(tmp_path),
                f"http://127.0.0.1:{server.server_port}",
                "start",
                "inline_isolation",
            ],
            cwd=root,
            env={**os.environ, "PYTHONUTF8": "1", "ASTRBOT_DISABLE_METRICS": "1"},
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        completed = json.loads(
            (tmp_path / "completed.json").read_text(encoding="utf-8")
        )["run"]
        assert completed["status"] == "completed", completed
        assert completed["result"] == {"text": "Final"}
        assert len(requests) >= 4
        downstream = json.dumps(requests[3:], ensure_ascii=False)
        assert "selected-value" in downstream
        for secret in (
            "secret-history",
            "secret-tool-output",
            "secret-tool-argument",
            "secret-result",
        ):
            assert secret not in downstream
        first, second = completed["tasks"]
        assert (
            first["snapshot"]["member"]["member_id"]
            == second["snapshot"]["member"]["member_id"]
        )
        assert (
            first["snapshot"]["member"]["conversation_id"]
            != second["snapshot"]["member"]["conversation_id"]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_graph_task_recovery_reuses_persisted_conversation(tmp_path):
    requests = []

    class ModelRecorder(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            response = (
                {
                    "role": "tool",
                    "id": "plan-original",
                    "tools_call_name": ["submit_task_result"],
                    "tools_call_args": [{"text": "Recovered result"}],
                    "tools_call_ids": ["submit-original"],
                }
                if len(requests) == 1
                else {"role": "assistant", "completion_text": "Done"}
            )
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelRecorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-m",
        "tests.agent_teams.checkpoint_worker",
        str(tmp_path),
        f"http://127.0.0.1:{server.server_port}",
    ]
    env = {**os.environ, "PYTHONUTF8": "1", "ASTRBOT_DISABLE_METRICS": "1"}
    try:
        with (tmp_path / "worker.log").open("w", encoding="utf-8") as log:
            worker = subprocess.Popen(
                [*command, "start", "inline_recovery"],
                cwd=root,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 40
                while not (tmp_path / "kill-ready.json").exists():
                    assert worker.poll() is None, (tmp_path / "worker.log").read_text(
                        encoding="utf-8"
                    )
                    assert time.monotonic() < deadline, (
                        tmp_path / "worker.log"
                    ).read_text(encoding="utf-8")
                    time.sleep(0.05)
                worker.kill()
                worker.wait(timeout=10)
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=10)
        resumed = subprocess.run(
            [*command, "recover", "inline_recovery"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        assert evidence["after"]["status"] == "completed", evidence
        assert evidence["parent"]["status"] == "completed"
        assert evidence["result"] == {"text": "Recovered result"}
        before, after = evidence["before"], evidence["after"]
        assert before["state"]["attempt_id"] == after["state"]["attempt_id"]
        assert (
            before["snapshot"]["member"]["conversation_id"]
            == after["snapshot"]["member"]["conversation_id"]
        )
        assert (
            before["snapshot"]["member"]["session_id"]
            == after["snapshot"]["member"]["session_id"]
        )
        assert len(requests) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
