"""A durable human choice resumes its original tool batch across process loss."""

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
    "boundary",
    [
        "request_pre",
        "request_post",
        "answer_pre",
        "answer_post",
        "consume_pre",
        "consume_post",
        "pause_continue",
        "cancel_answered",
        "cancel_pending",
        "consume_pause",
        "request_failure",
        "independent",
        "deadline_post",
        "chat",
    ],
)
def test_dynamic_choice_original_call_survives_process_loss(tmp_path, boundary):
    """Keep completed external steps and both choices exactly once.

    Args:
        tmp_path: Durable worker storage.
        boundary: Transaction boundary at which the parent kills the worker.
    """
    requests, effects = [], []

    class Recorder(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            """Serve an independently recorded model/tool boundary."""
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                effects.append(payload)
                response = {"text": "Verified evidence."}
            else:
                requests.append(payload)
                context = json.dumps(payload["contexts"])
                if "INDEPENDENT" in context:
                    response = (
                        {"role": "assistant", "completion_text": "Submitted."}
                        if "independent-result" in context
                        else {
                            "role": "tool",
                            "id": "independent-submission",
                            "tools_call_name": ["submit_task_result"],
                            "tools_call_ids": ["independent-result"],
                            "tools_call_args": [{"text": "Independent result."}],
                        }
                    )
                elif "submit-original" in context:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif "choice-two" in context:
                    response = {
                        "role": "tool",
                        "id": "submission",
                        "tools_call_name": ["submit_task_result"],
                        "tools_call_ids": ["submit-original"],
                        "tools_call_args": [{"text": "Agreed report."}],
                    }
                elif "evidence-original" in context:
                    question = {
                        "prompt": "Which report?",
                        "options": [
                            {"id": "A", "label": "Brief"},
                            {
                                "id": "B",
                                "label": "Detailed",
                                "description": "Include evidence",
                            },
                        ],
                        "extra_content": "## Evidence\nUse the verified source.",
                        "recommended_choice_id": "B",
                        "recommendation_reason": "The evidence is available.",
                    }
                    response = {
                        "role": "tool",
                        "id": "questions-original",
                        "tools_call_name": ["ask_user_choice", "ask_user_choice"],
                        "tools_call_ids": ["choice-one", "choice-two"],
                        "tools_call_args": [
                            question,
                            {**question, "prompt": "Any extra requirements?"},
                        ],
                    }
                else:
                    response = {
                        "role": "tool",
                        "id": "evidence-plan",
                        "tools_call_name": ["external_evidence"],
                        "tools_call_ids": ["evidence-original"],
                        "tools_call_args": [{"value": "original-evidence"}],
                    }
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    command = [
        sys.executable,
        "-m",
        "tests.agent_teams.checkpoint_worker",
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
        if boundary.endswith(("_pre", "_post")):
            with (tmp_path / "worker.log").open("w", encoding="utf-8") as log:
                worker = subprocess.Popen(
                    [*command, "start", "choice_" + boundary],
                    cwd=root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                )
                try:
                    deadline = time.monotonic() + 50
                    while not (tmp_path / "kill-ready.json").exists():
                        assert worker.poll() is None and time.monotonic() < deadline, (
                            tmp_path / "worker.log"
                        ).read_text(encoding="utf-8")
                        time.sleep(0.03)
                finally:
                    if worker.poll() is None:
                        worker.kill()
                    worker.wait(timeout=10)
            with (tmp_path / "recovery.log").open("w", encoding="utf-8") as log:
                rebuilt = subprocess.run(
                    [*command, "recover", "choice_" + boundary],
                    cwd=root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=60,
                    env=env,
                )
            assert rebuilt.returncode == 0, (tmp_path / "recovery.log").read_text(
                encoding="utf-8"
            )
        else:
            with (tmp_path / "worker.log").open("w", encoding="utf-8") as log:
                worker = subprocess.run(
                    [*command, "start", "choice_" + boundary],
                    cwd=root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=70,
                    env=env,
                )
            assert worker.returncode == 0, (tmp_path / "worker.log").read_text(
                encoding="utf-8"
            )
        after = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))[
            "after"
        ]
        if boundary in {"cancel_pending", "cancel_answered", "request_failure"}:
            assert after["result"] is None
            assert len(effects) == 1 and len(requests) == 2
            task = after["tasks"][0]
            if boundary == "request_failure":
                assert task["status"] == "failed" and task["todos"] == []
                assert "human choice" in task["state"]["reason"]
            else:
                assert task["status"] == "cancelled"
                assert task["todos"][0]["status"] == "cancelled"
                assert task["todos"][0]["consumed_at"] is None
            return
        assert after["result"] == {"text": "Agreed report."}
        todos = after["tasks"][0]["todos"]
        assert len(todos) == 2 and all(todo["status"] == "consumed" for todo in todos)
        assert len({todo["tool_record_id"] for todo in todos}) == 2
        assert len({todo["attempt_id"] for todo in todos}) == 1
        assert len(effects) == 1 and len(requests) == (
            6 if boundary == "independent" else 4
        )
        final_context = requests[-2]["contexts"]
        choices = [
            item
            for item in final_context
            if item.get("role") == "tool"
            and item.get("tool_call_id", "").startswith("choice-")
        ]
        assert [item["tool_call_id"] for item in choices] == [
            "choice-one",
            "choice-two",
        ]
        if boundary == "deadline_post":
            assert all(
                todo["decision_source"]["type"] == "recommendation" for todo in todos
            )
            assert all(
                item["content"] == "User selected: Detailed (id=B)" for item in choices
            )
            return
        assert (
            choices[0]["content"]
            == "User selected: Detailed (id=B)\nAdditional note: Reviewed evidence"
        )
        assert (
            choices[1]["content"]
            == "User selected: __free_text__ (id=__free_text__)\nAdditional note: Use the detailed report"
        )
    finally:
        server.shutdown()
        server.server_close()
