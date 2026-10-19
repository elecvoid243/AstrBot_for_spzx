"""Separate member waiting from uncertain non-wait native tool responses."""

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import pytest


@pytest.mark.parametrize(
    "scenario", ["team_deadline_wait", "team_deadline_unknown", "team_deadline_normal"]
)
def test_team_tool_deadline_public_contracts(tmp_path, scenario):
    """Observe real member work, native timeout uncertainty and public resolution.

    Args:
        tmp_path: Durable worker database and independent evidence directory.
        scenario: Isolated wait, response-loss or normal completion contract.
    """
    requests, effects, gates, processes = [], [], [], []
    lock = threading.Lock()
    coordinator_release = threading.Event()
    effect_entered, effect_release = threading.Event(), threading.Event()
    return_entered, return_release = threading.Event(), threading.Event()

    class ExternalService(BaseHTTPRequestHandler):
        """Independent model, effect and native-tool response gate."""

        def log_message(self, format, *args):
            """Keep HTTP server diagnostics in the explicit evidence ledger.

            Args:
                format: Unused request log format.
                *args: Unused request log arguments.
            """

        def do_POST(self):
            """Serve controlled responses without replacing internal scheduling.

            Returns:
                None after saving the actual request and returning its response.
            """
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/return-gate":
                observed = {**payload, "entered_at": time.time()}
                gates.append(observed)
                (tmp_path / "return-gate-entered.json").write_text(
                    json.dumps(observed), encoding="utf-8"
                )
                return_entered.set()
                assert return_release.wait(60), "The test must release its return gate"
                response = {"released": True}
            elif self.path == "/tool":
                with lock:
                    effects.append({**payload, "received_at": time.time()})
                if scenario == "team_deadline_wait":
                    effect_entered.set()
                    assert effect_release.wait(45), "The test must release its member"
                response = {"text": "Independent deadline evidence"}
            else:
                with lock:
                    requests.append(payload)
                results = {
                    message["tool_call_id"]: message["content"]
                    for message in payload["contexts"]
                    if message.get("role") == "tool"
                }
                tools = []
                identity = "completed"
                if "team_manage" in payload["tools"]:
                    if scenario == "team_deadline_wait":
                        (tmp_path / "coordinator-held.json").write_text(
                            json.dumps({"received_at": time.time()}), encoding="utf-8"
                        )
                        assert coordinator_release.wait(45)
                    elif "create-one" not in results:
                        role_id = re.search(
                            r'Allowed roles: \{"([^"]+)"',
                            payload["tool_descriptions"]["team_manage"],
                        )[1]
                        identity = "create"
                        tools = [
                            (
                                "team_manage",
                                {
                                    "action": "create_member",
                                    "request_id": "create-one",
                                    "role_id": role_id,
                                    "name": "Writer",
                                },
                                "create-one",
                            )
                        ]
                    elif "dispatch-one" not in results:
                        member = json.JSONDecoder().raw_decode(results["create-one"])[0]
                        identity = "dispatch"
                        tools = [
                            (
                                "team_manage",
                                {
                                    "action": "dispatch",
                                    "request_id": "dispatch-one",
                                    "member_id": member["member_id"],
                                    "name": "One child",
                                    "task": "deadline-one",
                                },
                                "dispatch-one",
                            )
                        ]
                    elif "wait-one" not in results:
                        child = json.JSONDecoder().raw_decode(results["dispatch-one"])[
                            0
                        ]
                        identity = "wait"
                        tools = [
                            (
                                "team_manage",
                                {
                                    "action": "wait",
                                    "request_id": "wait-one",
                                    "task_ids": [child["task_id"]],
                                },
                                "wait-one",
                            )
                        ]
                    elif "submit-coordinator" not in results:
                        identity = "submit-root"
                        tools = [
                            (
                                "submit_task_result",
                                {"text": "Verified deadline root"},
                                "submit-coordinator",
                            )
                        ]
                elif "effect-one" not in results:
                    identity = "effect"
                    tools = [
                        ("external_evidence", {"value": "deadline-one"}, "effect-one")
                    ]
                elif "submit-member" not in results:
                    identity = "submit-child"
                    tools = [
                        (
                            "submit_task_result",
                            {"text": "Verified deadline child"},
                            "submit-member",
                        )
                    ]
                response = (
                    {
                        "role": "tool",
                        "id": identity,
                        "tools_call_name": [tool[0] for tool in tools],
                        "tools_call_args": [tool[1] for tool in tools],
                        "tools_call_ids": [tool[2] for tool in tools],
                    }
                    if tools
                    else {"role": "assistant", "completion_text": "Submitted"}
                )
            with lock:
                (tmp_path / "external-ledger.json").write_text(
                    json.dumps(
                        {"requests": requests, "effects": effects, "gates": gates}
                    ),
                    encoding="utf-8",
                )
            data = json.dumps(response).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # Cancellation is expected for the held coordinator request
                # and the timed-out native tool's external response gate.
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExternalService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    command = [
        sys.executable,
        "-B",
        "-m",
        "tests.agent_teams.checkpoint_worker",
        str(tmp_path),
        f"http://127.0.0.1:{server.server_port}",
    ]
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    worker = None
    try:
        with (tmp_path / "start.log").open("x", encoding="utf-8") as output:
            worker = subprocess.Popen(
                [*command, "start", scenario],
                cwd=root,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        processes.append(
            {
                "launcher_pid": worker.pid,
                "action": "start",
                "command": [*command, "start", scenario],
            }
        )
        if scenario == "team_deadline_wait":
            deadline = time.monotonic() + 40
            while not effect_entered.is_set():
                assert worker.poll() is None, (tmp_path / "start.log").read_text(
                    encoding="utf-8"
                )
                assert time.monotonic() < deadline, (tmp_path / "start.log").read_text(
                    encoding="utf-8"
                )
                time.sleep(0.02)
            held_at = time.monotonic()
            assert not effect_release.wait(1.5)
            assert not (tmp_path / "deadline-evidence.json").exists()
            effect_release.set()
            assert worker.wait(timeout=30) == 0, (tmp_path / "start.log").read_text(
                encoding="utf-8"
            )
            evidence = json.loads(
                (tmp_path / "deadline-evidence.json").read_text(encoding="utf-8")
            )
            assert time.monotonic() - held_at >= 1.5
            assert evidence["deadline"] == 0.5 and evidence["elapsed"] >= 1.5
            result = json.loads(evidence["result"][0]["content"][0]["text"])
            assert result["tasks"][0]["status"] == "completed"
            child = next(
                task
                for task in evidence["after"]["tasks"]
                if task["run_id"] == evidence["child"]["run_id"]
            )
            assert result["tasks"][0]["result_ref"] == child["result_ref"]
            assert child["result"]["text"] == "Verified deadline child"
        elif scenario == "team_deadline_unknown":
            deadline = time.monotonic() + 40
            while not return_entered.is_set():
                assert worker.poll() is None, (tmp_path / "start.log").read_text(
                    encoding="utf-8"
                )
                assert time.monotonic() < deadline, (tmp_path / "start.log").read_text(
                    encoding="utf-8"
                )
                time.sleep(0.02)
            deadline = time.monotonic() + 40
            while not (tmp_path / "kill-ready.json").exists():
                assert worker.poll() is None, (tmp_path / "start.log").read_text(
                    encoding="utf-8"
                )
                assert time.monotonic() < deadline, (tmp_path / "start.log").read_text(
                    encoding="utf-8"
                )
                time.sleep(0.02)
            before = json.loads(
                (tmp_path / "deadline-before.json").read_text(encoding="utf-8")
            )
            call = next(
                call
                for call in before["tasks"][0]["tool_calls"]
                if call["status"] == "unknown"
            )
            assert before["status"] == "blocked" and call["raw_results"] == []
            assert any(
                item["data"].get("id") == "dispatch-one"
                and "execution timeout after 5 seconds"
                in str(item["data"].get("result"))
                for item in before["tasks"][0]["process"]
            )
            observed = json.loads(
                (tmp_path / "dispatch-returned.json").read_text(encoding="utf-8")
            )
            assert (
                json.loads(observed["result"])["run_id"] == before["tasks"][1]["run_id"]
            )
            identity = json.loads(
                (tmp_path / "deadline-runtime-start.json").read_text(encoding="utf-8")
            )
            runtime = psutil.Process(identity["pid"])
            assert runtime.create_time() == identity["creation_time"]
            assert runtime.pid in {
                worker.pid,
                *[
                    child.pid
                    for child in psutil.Process(worker.pid).children(recursive=True)
                ],
            }
            assert str(tmp_path) in runtime.cmdline()
            runtime.kill()
            processes[-1].update(
                {
                    "runtime": identity,
                    "intentional_kill": True,
                    "actual_runtime_exit": runtime.wait(10),
                    "launcher_exit": worker.wait(10),
                    "original_runtime_absent_before_rebuild": not runtime.is_running(),
                }
            )
            assert processes[-1]["original_runtime_absent_before_rebuild"]
            return_release.set()
            with (tmp_path / "recover.log").open("x", encoding="utf-8") as output:
                worker = subprocess.Popen(
                    [*command, "recover", scenario],
                    cwd=root,
                    env=environment,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
            processes.append(
                {
                    "launcher_pid": worker.pid,
                    "action": "recover",
                    "command": [*command, "recover", scenario],
                }
            )
            assert worker.wait(timeout=70) == 0, (tmp_path / "recover.log").read_text(
                encoding="utf-8"
            )
            evidence = json.loads(
                (tmp_path / "deadline-evidence.json").read_text(encoding="utf-8")
            )
            after = evidence["after"]
            assert after["status"] == "completed" and len(after["members"]) == 2
            assert [task["run_id"] for task in after["tasks"]] == [
                task["run_id"] for task in before["tasks"]
            ]
            assert after["tasks"][1]["result_ref"] == before["tasks"][1]["result_ref"]
            old_receipts = {item["receipt_id"] for item in before["usage"]["ledger"]}
            assert old_receipts <= {
                item["receipt_id"] for item in after["usage"]["ledger"]
            }
            for key in ("model_calls", "tool_calls", "steps_used"):
                assert after["usage"][key] >= before["usage"][key] > 0
            assert (
                after["tasks"][1]["snapshot"]["member"]["conversation_id"]
                == before["tasks"][1]["snapshot"]["member"]["conversation_id"]
            )
            assert all(
                c["status"] == "applied" for c in after["tasks"][0]["tool_calls"]
            )
        else:
            assert worker.wait(timeout=70) == 0, (tmp_path / "start.log").read_text(
                encoding="utf-8"
            )
            evidence = json.loads(
                (tmp_path / "deadline-evidence.json").read_text(encoding="utf-8")
            )
            assert evidence["after"]["status"] == "completed"
        assert len(effects) == 1 and effects[0]["value"] == "deadline-one"
        processes[-1]["launcher_exit"] = worker.returncode
    finally:
        coordinator_release.set()
        effect_release.set()
        return_release.set()
        if worker is not None and worker.poll() is None:
            # Only this test's recorded independent runtime may be cleaned up.
            identity_path = (
                tmp_path / f"deadline-runtime-{processes[-1]['action']}.json"
            )
            if identity_path.exists():
                identity = json.loads(identity_path.read_text(encoding="utf-8"))
                try:
                    runtime = psutil.Process(identity["pid"])
                    if (
                        runtime.create_time() == identity["creation_time"]
                        and str(tmp_path) in runtime.cmdline()
                    ):
                        runtime.kill()
                        processes[-1]["cleanup_runtime_exit"] = runtime.wait(10)
                except psutil.NoSuchProcess:
                    pass
            worker.wait(timeout=10)
        (tmp_path / "processes.json").write_text(
            json.dumps(processes), encoding="utf-8"
        )
        (tmp_path / "external-ledger-final.json").write_text(
            json.dumps({"requests": requests, "effects": effects, "gates": gates}),
            encoding="utf-8",
        )
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
