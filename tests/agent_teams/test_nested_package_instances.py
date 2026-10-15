"""Actual mixed nested execution and upgrades across process/data boundaries."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import psutil


@pytest.fixture
def nested_model_endpoint(tmp_path):
    """Provide controlled external responses while recording real HTTP and tools.

    Args:
        tmp_path: Independent external ledger directory.

    Yields:
        HTTP endpoint, request ledger and tool-plan ledger.
    """
    requests, plans = [], []
    coordinators = {1: 0, 2: 0}

    class ModelHandler(BaseHTTPRequestHandler):
        """OpenAI-compatible external model; AstrBot owns all execution."""

        def do_POST(self):
            """Answer the observed native tool surface and retain complete requests."""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            functions = {
                tool["function"]["name"]: tool["function"]
                for tool in body.get("tools", [])
            }
            text = json.dumps(body["messages"])
            version = 2 if "NESTED_V2" in text else 1
            report = (
                f"Verified nested evidence V{version}.\n" * 1200
            ) + f"COMPLETE_V{version}_END"
            calls = [
                call
                for message in body["messages"]
                for call in message.get("tool_calls", [])
            ]
            function, arguments = None, None
            if any(call["function"]["name"] == "submit_task_result" for call in calls):
                pass
            elif "team_manage" in functions:
                decisions = [
                    call for call in calls if call["function"]["name"] == "team_manage"
                ]
                if not decisions:
                    coordinators[version] += 1
                    allowed = json.loads(
                        functions["team_manage"]["description"].split(
                            "Allowed workflows: ", 1
                        )[1]
                    )
                    child = next(iter(allowed.values()))
                    function, arguments = (
                        "team_manage",
                        {
                            "action": "call_workflow",
                            "request_id": f"nested-{version}-{coordinators[version]}",
                            "asset_id": child["asset_id"],
                            "version": child["version"],
                            "name": "Read complete evidence",
                            "inputs": {
                                key: "portable evidence"
                                for key in child["required_inputs"]
                            },
                        },
                    )
                else:
                    decision = json.loads(decisions[-1]["function"]["arguments"])
                    response = next(
                        message
                        for message in reversed(body["messages"])
                        if message.get("tool_call_id") == decisions[-1]["id"]
                    )
                    result = json.loads(response["content"])
                    if decision["action"] == "call_workflow":
                        function, arguments = (
                            "team_manage",
                            {
                                "action": "wait",
                                "request_id": decision["request_id"] + "-wait",
                                "task_ids": [result["task_id"]],
                            },
                        )
                    else:
                        result_text = result["tasks"][0]["result"]["text"]
                        number = int(decision["request_id"].split("-")[2])
                        function, arguments = (
                            "submit_task_result",
                            {
                                "result_json": json.dumps(
                                    {"text": result_text, "approved": number % 2 == 0}
                                )
                            },
                        )
            else:
                function = "submit_task_result"
                parameters = functions[function]["parameters"].get("properties", {})
                arguments = (
                    {"result_json": json.dumps({"text": report})}
                    if "result_json" in parameters
                    else {"text": report}
                )
            message = {"role": "assistant", "content": "Submitted"}
            if function:
                plans.append(
                    {"function": function, "arguments": arguments, "version": version}
                )
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"nested-call-{len(requests)}",
                            "type": "function",
                            "function": {
                                "name": function,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
            completion = {
                "id": f"nested-response-{len(requests)}",
                "object": "chat.completion",
                "created": 1,
                "model": "portable-test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if function else "stop",
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
                if function:
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
                                "finish_reason": "tool_calls" if function else "stop",
                            }
                        ],
                    },
                ]
                data = (
                    "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                    + "data: [DONE]\n\n"
                ).encode()
            else:
                data = json.dumps(completion).encode()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/event-stream" if body.get("stream") else "application/json",
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            """Keep transport logs in the structured external ledger."""

    port_file = tmp_path / "model-port"
    port = int(port_file.read_text(encoding="utf-8")) if port_file.exists() else 0
    server = ThreadingHTTPServer(("127.0.0.1", port), ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, plans
    finally:
        calls = {
            call["id"]: call
            for request in requests
            for message in request["messages"]
            for call in message.get("tool_calls", [])
        }
        results = {
            message["tool_call_id"]: {
                "call": calls[message["tool_call_id"]],
                "result": message,
            }
            for request in requests
            for message in request["messages"]
            if message.get("role") == "tool" and message.get("tool_call_id") in calls
        }
        (tmp_path / "tool-results.json").write_text(
            json.dumps(list(results.values()), indent=2), encoding="utf-8"
        )
        (tmp_path / "http-requests.json").write_text(
            json.dumps(requests, indent=2), encoding="utf-8"
        )
        (tmp_path / "tool-plans.json").write_text(
            json.dumps(plans, indent=2), encoding="utf-8"
        )
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_mixed_package_runs_in_second_instance_and_survives_upgrade_after_process_loss(
    tmp_path, nested_model_endpoint
):
    """Upgrade all nested versions while the durable old approval survives a kill.

    Args:
        tmp_path: Separate instance A and instance B persistence roots.
        nested_model_endpoint: Controlled external native model HTTP responses.
    """
    endpoint, requests, plans = nested_model_endpoint
    root = Path(__file__).resolve().parents[2]
    processes = []
    for instance, action in (
        ("a", "nested-export"),
        ("b", "nested-prepare"),
        ("b", "nested-upgrade"),
    ):
        folder = tmp_path / instance
        folder.mkdir(exist_ok=True)
        if action == "nested-prepare":
            for version in (1, 2):
                (folder / f"input-v{version}.json").write_bytes(
                    (tmp_path / "a" / f"export-v{version}.json").read_bytes()
                )
        command = [
            sys.executable,
            "-m",
            "tests.agent_teams.package_worker",
            str(folder),
            endpoint,
            instance,
            action,
        ]
        with (folder / (action + ".log")).open("w", encoding="utf-8") as output:
            worker = subprocess.Popen(
                command,
                cwd=root,
                env={**os.environ, "ASTRBOT_ROOT": str(folder), "PYTHONUTF8": "1"},
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            process = {
                "pid": worker.pid,
                "command": command,
                "cwd": str(root),
                "action": action,
            }
            processes.append(process)
            try:
                if action == "nested-prepare":
                    deadline = time.monotonic() + 90
                    while (
                        time.monotonic() < deadline
                        and not (folder / "waiting-ready").exists()
                        and worker.poll() is None
                    ):
                        time.sleep(0.02)
                    assert (folder / "waiting-ready").exists(), (
                        folder / (action + ".log")
                    ).read_text(encoding="utf-8")
                    runtime = json.loads(
                        (folder / "instance-process.json").read_text(encoding="utf-8")
                    )
                    process["runtime"] = runtime
                    assert runtime["action"] == action
                    os.kill(runtime["pid"], signal.SIGTERM)
                    assert worker.wait(timeout=10) != 0
                    assert not psutil.pid_exists(runtime["pid"])
                    process["runtime_alive_after_termination"] = False
                else:
                    assert worker.wait(timeout=90) == 0, (
                        folder / (action + ".log")
                    ).read_text(encoding="utf-8")
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=10)
                process["exit_code"] = worker.returncode
                process["forced_termination"] = action == "nested-prepare"
                (tmp_path / "processes.json").write_text(
                    json.dumps(processes, indent=2), encoding="utf-8"
                )
    package = json.loads((tmp_path / "a/export-v1.json").read_text(encoding="utf-8"))
    before = json.loads((tmp_path / "b/before-crash.json").read_text(encoding="utf-8"))
    result = json.loads((tmp_path / "b/result.json").read_text(encoding="utf-8"))
    assert result["original"]["snapshot"] == before["run"]["snapshot"]
    assert result["after_upgrade"]["snapshot"] == before["run"]["snapshot"]
    assert result["after_upgrade"]["todos"] == result["original"]["todos"]
    assert (
        result["installed"]["asset"]["asset_id"]
        == before["installed"]["asset"]["asset_id"]
    )
    assert result["installed"]["asset"]["version"] == 2
    assert result["repeated"]["existing"] is True
    assert result["old_completed"]["status"] == result["new"]["status"] == "completed"
    assert result["old_artifact"] == result["exported_old"]
    assert (
        result["old_artifact"]["content"]["text"]
        == "Verified nested evidence V1.\n" * 1200 + "COMPLETE_V1_END"
    )
    assert (
        result["new_artifact"]["content"]["text"]
        == "Verified nested evidence V2.\n" * 1200 + "COMPLETE_V2_END"
    )
    assert result["preview"]["ready"] is True
    assert all(
        asset["action"] == "new_version" for asset in result["preview"]["assets"]
    )
    assert (
        len(
            [
                plan
                for plan in plans
                if plan["function"] == "team_manage"
                and plan["arguments"]["action"] == "call_workflow"
            ]
        )
        == 4
    )
    assert len(requests) == 24
    assert all(request["model"] == "portable-test-model" for request in requests)
    for private in (
        "private-key-a",
        "profile-a",
        "model-a",
        "conversation_id",
        "bindings",
    ):
        assert private not in json.dumps(package)
    assert (
        before["run"]["snapshot"]["child_assets"]["evidence"]["members"]["coordinator"][
            "effective"
        ]["provider_id"]
        == "model-b"
    )
