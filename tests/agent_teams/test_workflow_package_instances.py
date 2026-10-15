"""Real export/import across independent data roots and local resource identities."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def package_model_endpoint(tmp_path):
    """Provide an external native model boundary for real instance execution.

    Args:
        tmp_path: Isolated request gate and worker communication directory.

    Yields:
        Endpoint and the actual HTTP request ledger.
    """
    requests = []

    class ModelHandler(BaseHTTPRequestHandler):
        """External model boundary; all AstrBot conversion and execution stay real."""

        def do_POST(self):
            """Record each actual request and return a schema-correct tool response."""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if (tmp_path / "hold-inflight").exists() and len(requests) == 1:
                (tmp_path / "b/http-entered").write_text("entered", encoding="utf-8")
                for _ in range(1000):
                    if (tmp_path / "b/drift-ready").exists():
                        break
                    time.sleep(0.01)
                assert (tmp_path / "b/drift-ready").exists()
            submitted = any(
                message.get("role") == "tool" for message in body["messages"]
            )
            message = (
                {"role": "assistant", "content": "Submitted"}
                if submitted
                else {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"submit-{len(requests)}",
                            "type": "function",
                            "function": {
                                "name": "submit_task_result",
                                "arguments": json.dumps(
                                    {
                                        "result_json": json.dumps(
                                            {"text": "Portable evidence"}
                                        )
                                    }
                                ),
                            },
                        }
                    ],
                }
            )
            completion = {
                "id": f"response-{len(requests)}",
                "object": "chat.completion",
                "created": 1,
                "model": "portable-test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "stop" if submitted else "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
            if body.get("stream"):
                delta = {**message}
                if not submitted:
                    delta["tool_calls"] = [{**message["tool_calls"][0], "index": 0}]
                chunks = [
                    {
                        **completion,
                        "object": "chat.completion.chunk",
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": None}
                        ],
                    },
                    {
                        **completion,
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop" if submitted else "tool_calls",
                            }
                        ],
                    },
                ]
                response = (
                    "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                    + "data: [DONE]\n\n"
                ).encode()
            else:
                response = json.dumps(completion).encode()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/event-stream" if body.get("stream") else "application/json",
            )
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        yield endpoint, requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_different_instances_export_import_and_run_actual_provider_pipeline(
    tmp_path, package_model_endpoint
):
    """Use real HTTP provider requests and an explicit contracted graph handoff.

    Args:
        tmp_path: Parent of separate instance A and instance B durable roots.
        package_model_endpoint: External model endpoint and actual request ledger.
    """
    endpoint, requests = package_model_endpoint
    root = Path(__file__).resolve().parents[2]
    for instance, action in (("a", "export"), ("b", "import")):
        folder = tmp_path / instance
        folder.mkdir()
        if instance == "b":
            (folder / "input.json").write_bytes(
                (tmp_path / "a" / "export.json").read_bytes()
            )
        with (folder / "worker.log").open("w", encoding="utf-8") as output:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tests.agent_teams.package_worker",
                    str(folder),
                    endpoint,
                    instance,
                    action,
                ],
                cwd=root,
                env={**os.environ, "ASTRBOT_ROOT": str(folder), "PYTHONUTF8": "1"},
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=90,
            )
        assert completed.returncode == 0, (folder / "worker.log").read_text(
            encoding="utf-8"
        )
    package = json.loads((tmp_path / "a" / "export.json").read_text(encoding="utf-8"))
    result = json.loads((tmp_path / "b" / "result.json").read_text(encoding="utf-8"))
    (tmp_path / "http-requests.json").write_text(
        json.dumps(requests, indent=2), encoding="utf-8"
    )
    assert result["preview"]["ready"] is True
    assert result["run"]["status"] == "completed", result["run"]
    assert len(requests) == 4
    assert all(request["model"] == "portable-test-model" for request in requests)
    assert "Portable evidence" in json.dumps(requests[2]["messages"])
    assert "private-key-a" not in json.dumps(package)
    assert "model-a" not in json.dumps(package)
    assert "profile-a" not in json.dumps(package)
    assert result["installed"]["asset"]["definition"]["tasks"][1]["position"] == {
        "x": 480.0,
        "y": 60.0,
    }
    assert all(
        task["snapshot"]["effective"]["provider_id"] == "model-b"
        for task in result["run"]["tasks"]
    )


def test_inflight_package_recovers_original_streamed_response(
    tmp_path, package_model_endpoint
):
    """Restore the compatible resource and reuse its complete native response.

    Args:
        tmp_path: Separate durable source and target instance directories.
        package_model_endpoint: External HTTP boundary and actual request ledger.
    """
    endpoint, requests = package_model_endpoint
    (tmp_path / "hold-inflight").write_text("hold", encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    for instance, action in (("a", "export"), ("b", "inflight-recover")):
        folder = tmp_path / instance
        folder.mkdir()
        if instance == "b":
            (folder / "input.json").write_bytes(
                (tmp_path / "a/export.json").read_bytes()
            )
        with (folder / "worker.log").open("w", encoding="utf-8") as output:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tests.agent_teams.package_worker",
                    str(folder),
                    endpoint,
                    instance,
                    action,
                ],
                cwd=root,
                env={**os.environ, "ASTRBOT_ROOT": str(folder), "PYTHONUTF8": "1"},
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=90,
            )
        assert completed.returncode == 0, (folder / "worker.log").read_text(
            encoding="utf-8"
        )
    result = json.loads((tmp_path / "b/result.json").read_text(encoding="utf-8"))
    (tmp_path / "http-requests.json").write_text(
        json.dumps(requests, indent=2), encoding="utf-8"
    )
    assert result["readiness"]["ready"] is False
    assert result["blocked"]["status"] == "blocked"
    blocked = next(
        task
        for task in result["blocked"]["tasks"]
        if task["snapshot"].get("node_id") == "research"
    )
    assert blocked["state"]["reason"] == "Frozen resources are no longer available"
    assert any(
        issue["path"] == "installation.bindings.model"
        for issue in blocked["state"]["issues"]
    )
    assert blocked["pending_result"] is None and blocked["artifacts"] == []
    assert result["run"]["status"] == "completed", result["run"]
    assert result["run"]["snapshot"] == result["before"]["snapshot"]
    recovered = next(
        task for task in result["run"]["tasks"] if task["run_id"] == blocked["run_id"]
    )
    assert recovered["snapshot"] == blocked["snapshot"]
    assert recovered["state"]["attempt_id"] == blocked["state"]["attempt_id"]
    assert len(requests) == 4
    assert all(request["model"] == "portable-test-model" for request in requests)
    assert any(message.get("role") == "tool" for message in requests[1]["messages"])
    assert any(event["kind"] == "task.blocked" for event in recovered["events"])
