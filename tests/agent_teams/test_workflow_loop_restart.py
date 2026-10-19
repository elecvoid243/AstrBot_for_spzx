"""Real terminated processes preserve bounded loop and external-effect history."""

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
        "loop_pre",
        "loop_post",
        "loop_pause",
        "loop_cancel",
        "loop_second_response",
        "loop_second_result",
    ],
)
def test_iteration_boundary_survives_process_termination(tmp_path, scenario):
    """Recover real checkpoints while retaining round history and external effects.

    Args:
        tmp_path: Isolated SQLite and independent external ledger directory.
        scenario: Iteration, control or saved model/tool boundary to terminate at.
    """
    requests, effects = [], []

    class ExternalService(BaseHTTPRequestHandler):
        """Keep model/tool observations independent of every execution worker."""

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            """Return real provider responses for drafting and independent review.

            Returns:
                None after recording the external request and responding.
            """
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                effects.append(payload)
                response = {"text": "Evidence " + payload["value"]}
            else:
                requests.append(payload)
                contexts = payload["contexts"]
                start = max(
                    i for i, message in enumerate(contexts) if message["role"] == "user"
                )
                prompt = json.dumps(contexts[start], ensure_ascii=False)
                current = json.dumps(contexts[start:], ensure_ascii=False)
                reviewing = "REVIEW-ROUND" in prompt
                iteration = (
                    2
                    if ("Report revision 2" if reviewing else "Report revision 1")
                    in prompt
                    else 1
                )
                identity = ("review" if reviewing else "write") + str(iteration)
                if "submit-" + identity in current:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif "effect-" + identity in current:
                    result = (
                        {
                            "approved": iteration == 2,
                            "text": f"Review {iteration}: add evidence",
                        }
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
                        "usage": {"input_other": 11, "output": 5},
                    }
                else:
                    response = {
                        "role": "tool",
                        "id": "plan-" + identity,
                        "tools_call_name": ["external_evidence"],
                        "tools_call_args": [{"value": identity}],
                        "tools_call_ids": ["effect-" + identity],
                        "usage": {"input_other": 17, "output": 9},
                    }
            (tmp_path / "external-ledger.json").write_text(
                json.dumps({"requests": requests, "effects": effects}), encoding="utf-8"
            )
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
                deadline = time.monotonic() + 60
                while not (tmp_path / "kill-ready.json").exists():
                    assert worker.poll() is None, (tmp_path / "worker.log").read_text(
                        encoding="utf-8"
                    )
                    assert time.monotonic() < deadline, (
                        tmp_path / "worker.log"
                    ).read_text(encoding="utf-8")
                    time.sleep(0.03)
                worker.kill()
                worker.wait(timeout=10)
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=10)
        assert [effect["value"] for effect in effects] == (
            ["write1", "review1", "write2"]
            if scenario == "loop_second_result"
            else ["write1", "review1"]
        )
        rebuilt = subprocess.run(
            [*command, "recover", scenario],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=environment,
        )
        assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
        evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        after = evidence["after"]
        assert after["snapshot"] == evidence["before"]["snapshot"]
        for original, retained in zip(
            evidence["before"]["loops"][0]["iterations"][0]["tasks"],
            after["loops"][0]["iterations"][0]["tasks"],
            strict=True,
        ):
            for field in ["run_id", "snapshot", "state", "result", "result_ref", "events"]:
                assert retained[field] == original[field]
        if scenario == "loop_cancel":
            assert after["control"]["status"] == "cancelled"
            assert after["loops"][0]["status"] == "cancelled"
            assert after["loops"][0]["iterations"][0]["status"] == "rejected"
            assert len(effects) == 2 and len(requests) == 6
            assert not after["todos"] and after["result"] is None
            return
        assert after["status"] == "completed", after
        rounds = after["loops"][0]["iterations"]
        assert [iteration["status"] for iteration in rounds] == ["rejected", "passed"]
        assert (
            len([item for item in after["events"] if item["kind"] == "loop.started"])
            == 1
        )
        assert [effect["value"] for effect in effects] == [
            "write1",
            "review1",
            "write2",
            "review2",
        ]
        assert len(requests) == 12
        assert rounds[0]["tasks"][1]["snapshot"]["input_artifacts"] == [
            rounds[0]["tasks"][0]["result_ref"]
        ]
        assert rounds[1]["tasks"][0]["snapshot"]["inputs"] == {
            "draft": "Report revision 1",
            "feedback": "Review 1: add evidence",
        }
        assert [
            task["recovery"]["model_calls"]
            for iteration in rounds
            for task in iteration["tasks"]
        ] == [3, 3, 3, 3]
        assert evidence["exported"]["content"] == {"text": "Report revision 2"}
        assert (
            evidence["exported"]["artifact_id"]
            == rounds[1]["tasks"][0]["result_ref"]["artifact_id"]
        )
        if scenario != "loop_pre":
            original = evidence["before"]["loops"][0]["iterations"][1]["tasks"][0]
            restored = rounds[1]["tasks"][0]
            assert original["run_id"] == restored["run_id"]
            assert original["state"]["attempt_id"] == restored["state"]["attempt_id"]
            assert original["snapshot"] == restored["snapshot"]
        if scenario == "loop_pause":
            assert evidence["paused"]["control"]["status"] == "paused"
            assert (
                evidence["paused"]["loops"][0]["iterations"][1]["tasks"][0]["status"]
                == "pending"
            )
    finally:
        server.shutdown()
        server.server_close()
