"""Plugin recovery through current authorization and durable public services."""

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
    "capability",
    [
        "query",
        "query_denied",
        "idempotent",
        "safe",
        "idempotent_denied",
        "safe_denied",
        "unknown",
        "legacy",
        "query_changed",
        "query_missing",
    ],
)
def test_permission_wrapped_recovery_survives_process_termination(tmp_path, capability):
    """Recover the original effect through the real permission-wrapped pipeline.

    Args:
        tmp_path: Persistent database and external ledger outside the killed worker.
        capability: Explicit plugin recovery contract or conservative control.
    """
    deliveries, effects, queries, requests = [], [], [], []
    receipt = "Complete original receipt. " * 1000 + "RECEIPT_END"

    class ExternalService(BaseHTTPRequestHandler):
        """Keep external effects and authoritative receipts outside the worker."""

        def log_message(self, *args):
            """Silence HTTP logs.

            Args:
                *args: Standard HTTP logging arguments.
            """

        def do_POST(self):
            """Serve the controlled model and external effect boundaries."""
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                deliveries.append(payload)
                if not payload.get("idempotency_key") or not any(
                    effect.get("idempotency_key") == payload["idempotency_key"]
                    for effect in effects
                ):
                    effects.append(payload)
                response = {"text": receipt}
            elif self.path == "/query":
                queries.append(payload)
                response = {
                    "status": "succeeded",
                    "evidence": "The independent ledger retains the original receipt.",
                    "results": [{"content": [{"type": "text", "text": receipt}]}],
                }
            else:
                requests.append(payload)
                context = json.dumps(payload["contexts"])
                if "Permission denied" in context:
                    response = {
                        "role": "assistant",
                        "completion_text": "The current caller cannot use this tool.",
                    }
                elif "submit-original" in context:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif "evidence-original" in context:
                    response = {
                        "role": "tool",
                        "tools_call_name": ["submit_task_result"],
                        "tools_call_args": [{"text": "Original verified report."}],
                        "tools_call_ids": ["submit-original"],
                    }
                else:
                    response = {
                        "role": "tool",
                        "tools_call_name": ["external_evidence"],
                        "tools_call_args": [{"value": "original-plan"}],
                        "tools_call_ids": ["evidence-original"],
                    }
            (tmp_path / "external-ledger.json").write_text(
                json.dumps(
                    {
                        "deliveries": deliveries,
                        "effects": effects,
                        "queries": queries,
                        "requests": requests,
                    }
                ),
                encoding="utf-8",
            )
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExternalService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    command = [
        sys.executable,
        "-m",
        "tests.agent_teams.checkpoint_worker",
        str(tmp_path),
        f"http://127.0.0.1:{server.server_port}",
    ]
    environment = {**os.environ, "PYTHONUTF8": "1"}
    root = Path(__file__).resolve().parents[2]
    try:
        with (tmp_path / "start.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [*command, "start", f"guarded_{capability}"],
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
            )
            try:
                deadline = time.monotonic() + 40
                while not (tmp_path / "kill-ready.json").exists():
                    assert process.poll() is None, (tmp_path / "start.log").read_text(
                        encoding="utf-8"
                    )
                    assert time.monotonic() < deadline, (
                        tmp_path / "start.log"
                    ).read_text(encoding="utf-8")
                    time.sleep(0.02)
                assert len(deliveries) == len(effects) == len(requests) == 1
                assert queries == []
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
        recovered = subprocess.run(
            [*command, "recover", f"guarded_{capability}"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
            env=environment,
        )
        (tmp_path / "recover.log").write_text(
            recovered.stdout + recovered.stderr, encoding="utf-8"
        )
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        before, after = evidence["before"], evidence["after"]
        original = before["tool_calls"][0]
        restored = next(
            call
            for call in after["tool_calls"]
            if call["record_id"] == original["record_id"]
        )
        assert after["run_id"] == before["run_id"]
        assert after["state"]["attempt_id"] == before["state"]["attempt_id"]
        assert after["snapshot"] == before["snapshot"]
        assert restored["call_id"] == original["call_id"] == "evidence-original"
        assert restored["invocation_id"] == original["invocation_id"]
        assert original["raw_results"] == []
        if capability in {"unknown", "legacy"}:
            assert evidence["error"]["status"] == 409
            assert after["status"] == "interrupted"
            assert original["recovery"]["declaration"] is None
            assert restored["recovery"]["declaration"] is None
            assert restored["status"] == "unknown"
            assert restored["raw_results"] == []
            assert queries == []
            assert len(deliveries) == len(effects) == len(requests) == 1
            return
        kind = capability.split("_", 1)[0]
        assert original["recovery"]["declaration"] == {
            "version": "permission-v1",
            "query": kind == "query",
            "safe_retry": kind == "safe",
            "idempotency_key_arg": "idempotency_key" if kind == "idempotent" else None,
        }
        assert (
            restored["recovery"]["declaration"] == original["recovery"]["declaration"]
        )
        if capability.endswith(("_changed", "_missing", "_denied")):
            assert after["status"] == "blocked"
            assert queries == []
            assert len(deliveries) == len(effects) == 1
            if capability == "query_denied":
                assert restored["status"] == "unknown"
                assert restored["raw_results"] == []
                assert (
                    "Permission denied"
                    in restored["recovery"]["queries"][0]["evidence"]
                )
                assert len(requests) == 1
            elif capability.endswith("_denied"):
                assert restored["status"] == "unknown"
                assert restored["raw_results"] == []
                assert (
                    "Permission denied" in restored["recovery"]["retries"][0]["error"]
                )
                assert len(requests) == 1
            else:
                assert restored["status"] == "unknown"
                assert restored["raw_results"] == []
                assert len(requests) == 1
            return
        assert after["status"] == "completed", evidence
        assert restored["status"] == "applied"
        assert restored["raw_results"][0]["content"][0]["text"] == receipt
        if kind == "query":
            assert queries == [
                {
                    "identity": original["record_id"],
                    "arguments": {"value": "original-plan"},
                }
            ]
            assert len(deliveries) == len(effects) == 1
        else:
            assert queries == []
            assert len(deliveries) == 2
            assert len(effects) == (1 if kind == "idempotent" else 2)
            assert all(
                delivery["identity"] == original["record_id"] for delivery in deliveries
            )
            if kind == "idempotent":
                assert all(
                    delivery["idempotency_key"] == original["record_id"]
                    for delivery in deliveries
                )
        assert effects[0]["identity"] == original["record_id"]
        applied = [
            message["content"]
            for message in requests[1]["contexts"]
            if message.get("tool_call_id") == "evidence-original"
        ]
        assert applied == [receipt]
        assert evidence["result"] == {"text": "Original verified report."}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
