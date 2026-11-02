"""Actual accounting commits and admission failures across terminated processes."""

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
        "usage_reserved",
        "usage_report_pre",
        "usage_reported",
        "usage_reservation_failure",
        "usage_report_failure",
        "usage_limit",
        "usage_completed_limit",
        "usage_parallel",
        "usage_compression",
        "usage_native_retry",
        "usage_native_limit",
        "usage_native_anthropic",
        "usage_native_gemini",
        "usage_native_gemini_usage",
        "usage_native_missing",
        "usage_shared_wait",
        "usage_native_gemini_empty",
        "usage_native_gemini_components",
    ],
)
def test_real_usage_boundaries_retain_consumption_and_stop_uncommitted_work(
    tmp_path, scenario
):
    """Accounting follows committed calls and cannot reset on a process restart.

    Args:
        tmp_path: Durable SQLite and independently observed model/tool effects.
        scenario: Reservation/report boundary, storage failure, or step exhaustion.
    """
    requests, effects = [], []
    active_models = 0
    peak_models = 0
    recorder_lock = threading.Lock()
    first_model_started = threading.Event()
    release_first_model = threading.Event()

    class Recorder(BaseHTTPRequestHandler):
        """External effects remain observable after the worker is terminated."""

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            """Record real requests and return a complete scripted provider response.

            Returns:
                None after writing the independent ledger and response.
            """
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if scenario.startswith("usage_native_"):
                requests.append(payload)
                data = json.dumps(
                    {
                        "error": {
                            "message": "Transient failure",
                            "type": "server_error",
                            "code": 500,
                            "status": "INTERNAL",
                        }
                    }
                    if len(requests) == 1
                    else {
                        "id": "native-response",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "model-original",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Observed.",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 2,
                            "total_tokens": 12,
                        },
                    }
                ).encode()
                if len(requests) > 1 and payload.get("stream"):
                    chunk = {
                        "id": "native-response",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "model-original",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": "Observed."},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                    data = (
                        "data: "
                        + json.dumps(chunk)
                        + "\n\ndata: "
                        + json.dumps(
                            {
                                **chunk,
                                "choices": [],
                                "usage": {
                                    "prompt_tokens": 10,
                                    "completion_tokens": 2,
                                    "total_tokens": 12,
                                },
                            }
                        )
                        + "\n\ndata: [DONE]\n\n"
                    ).encode()
                    if scenario == "usage_native_missing":
                        data = (
                            "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
                        ).encode()
                if scenario in {
                    "usage_native_gemini_usage",
                    "usage_native_gemini_empty",
                    "usage_native_gemini_components",
                }:
                    metadata = {
                        "promptTokenCount": 10,
                        "candidatesTokenCount": 2,
                        "thoughtsTokenCount": 80,
                        "toolUsePromptTokenCount": 3,
                        "totalTokenCount": 95,
                    }
                    if scenario == "usage_native_gemini_empty":
                        metadata = {}
                    elif scenario == "usage_native_gemini_components":
                        metadata.pop("totalTokenCount")
                    data = (
                        "data: "
                        + json.dumps(
                            {
                                "candidates": [
                                    {
                                        "content": {
                                            "parts": [{"text": "Observed."}],
                                            "role": "model",
                                        },
                                        "finishReason": "STOP",
                                    }
                                ],
                                "usageMetadata": metadata,
                            }
                        )
                        + "\n\n"
                    ).encode()
                self.send_response(
                    500
                    if len(requests) == 1
                    and scenario
                    not in {
                        "usage_native_gemini_usage",
                        "usage_native_gemini_empty",
                        "usage_native_gemini_components",
                    }
                    else 200
                )
                self.send_header(
                    "Content-Type",
                    "text/event-stream"
                    if len(requests) > 1
                    and payload.get("stream")
                    or scenario
                    in {
                        "usage_native_gemini_usage",
                        "usage_native_gemini_empty",
                        "usage_native_gemini_components",
                    }
                    else "application/json",
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if self.path == "/tool":
                effects.append(payload)
                response = {"text": "Verified evidence."}
            else:
                nonlocal active_models, peak_models
                with recorder_lock:
                    active_models += 1
                    peak_models = max(peak_models, active_models)
                    requests.append(payload)
                    first_model = len(requests) == 1
                if scenario in {"usage_parallel", "usage_shared_wait"} and first_model:
                    first_model_started.set()
                    assert release_first_model.wait(45), (
                        "Quota contention was not observed"
                    )
                latest_user = max(
                    i
                    for i, message in enumerate(payload["contexts"])
                    if message["role"] == "user"
                )
                context = json.dumps(
                    payload["contexts"][latest_user:]
                    if scenario in {"usage_compression", "usage_shared_wait"}
                    else payload["contexts"]
                )
                if "submit-original" in context:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif "evidence-original" in context:
                    response = {
                        "role": "tool",
                        "id": "submission-original",
                        "tools_call_name": ["submit_task_result"],
                        "tools_call_args": [{"text": "Verified report."}],
                        "tools_call_ids": ["submit-original"],
                    }
                else:
                    response = {
                        "role": "tool",
                        "id": "plan-original",
                        "tools_call_name": ["external_evidence"],
                        "tools_call_args": [{"value": "once"}],
                        "tools_call_ids": ["evidence-original"],
                    }
                if scenario == "usage_compression":
                    if (
                        "Generate a summary of our previous conversation history"
                        in context
                    ):
                        original = json.dumps(payload["contexts"])
                        response = {
                            "role": "assistant",
                            "completion_text": "Saved history: "
                            + (
                                "evidence-original completed. "
                                if "evidence-original" in original
                                else ""
                            )
                            + (
                                "submit-original accepted. "
                                if "submit-original" in original
                                else ""
                            ),
                        }
                    elif response.get("role") == "assistant":
                        response["completion_text"] = "Submitted evidence. " * 1000
                else:
                    response["usage"] = {
                        "input_other": 8,
                        "input_cached": 2,
                        "output": 2,
                    }
                with recorder_lock:
                    active_models -= 1
            (tmp_path / "external-ledger.json").write_text(
                json.dumps({"requests": requests, "effects": effects}), encoding="utf-8"
            )
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "PYTHONUTF8": "1"}
    limits = {
        "max_steps": 1
        if scenario
        in {
            "usage_limit",
            "usage_native_limit",
            "usage_native_anthropic",
            "usage_native_gemini",
            "usage_native_gemini_usage",
            "usage_native_gemini_empty",
            "usage_native_gemini_components",
        }
        else 2
        if scenario in {"usage_native_retry", "usage_native_missing"}
        else 4
        if scenario == "usage_completed_limit"
        else 20,
        "max_output_tokens": 7,
    }
    if scenario in {"usage_parallel", "usage_shared_wait"}:
        limits["token_budget"] = 20000
        limits["max_output_tokens"] = 15000
    command = [
        sys.executable,
        "-m",
        "tests.agent_teams.checkpoint_worker",
        str(tmp_path),
        f"http://127.0.0.1:{server.server_port}",
    ]
    try:
        with (tmp_path / "start.log").open("w", encoding="utf-8") as log:
            worker = subprocess.Popen(
                [
                    *command,
                    "start",
                    scenario,
                    json.dumps(
                        limits
                    ),
                ],
                cwd=root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            processes = {"start": {"pid": worker.pid, "command": worker.args}}
            try:
                if scenario in {"usage_parallel", "usage_shared_wait"}:
                    assert first_model_started.wait(40), "The first model never started"
                    deadline = time.monotonic() + 15
                    waiting_path = tmp_path / "usage-waiting.json"
                    while not waiting_path.exists():
                        assert worker.poll() is None
                        assert time.monotonic() < deadline, (
                            "No public quota wait snapshot"
                        )
                        time.sleep(0.03)
                    waiting = json.loads(waiting_path.read_text(encoding="utf-8"))
                    assert any(
                        event["kind"] == "usage.waiting" for event in waiting["events"]
                    )
                    parked = [
                        task
                        for task in waiting["tasks"]
                        if task["state"].get("usage_wait")
                    ]
                    assert parked and all(
                        task["state"]["capacity_released"] for task in parked
                    )
                    # The first delivery is still in flight; the competitor has no receipt.
                    assert len(requests) == active_models == peak_models == 1
                    assert not effects
                    assert waiting["usage"]["model_calls"] == 1
                    assert waiting["usage"]["tokens"]["reported"] is None
                    assert 15000 < waiting["usage"]["tokens"]["reserved"] <= 20000
                    assert waiting["usage"]["tokens"]["unknown_calls"] == 1
                    release_first_model.set()
                deadline = time.monotonic() + 50
                while not (tmp_path / "kill-ready.json").exists():
                    assert worker.poll() is None, (tmp_path / "start.log").read_text(
                        encoding="utf-8"
                    )[-16000:]
                    assert time.monotonic() < deadline, (
                        tmp_path / "start.log"
                    ).read_text(encoding="utf-8")[-16000:]
                    time.sleep(0.03)
                assert effects == (
                    [{"value": "once"}] * 3
                    if scenario == "usage_shared_wait"
                    else [{"value": "once"}] * 2
                    if scenario in {"usage_parallel", "usage_compression"}
                    else [{"value": "once"}]
                    if scenario == "usage_completed_limit"
                    else []
                )
                assert (
                    len(requests) > 6
                    if scenario == "usage_compression"
                    else len(requests)
                    == (
                        9
                        if scenario == "usage_shared_wait"
                        else 6
                        if scenario == "usage_parallel"
                        else 0
                        if scenario in {"usage_reserved", "usage_reservation_failure"}
                        else 2
                        if scenario
                        in {
                            "usage_completed_limit",
                            "usage_native_retry",
                            "usage_native_missing",
                        }
                        else 1
                    )
                )
                worker.kill()
                worker.wait(timeout=10)
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=10)
                processes["start"]["exit_code"] = worker.returncode
                (tmp_path / "processes.json").write_text(
                    json.dumps(processes, indent=2), encoding="utf-8"
                )
        resumed = subprocess.Popen(
            [*command, "recover", scenario],
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        try:
            recovery_log, _ = resumed.communicate(timeout=60)
        finally:
            if resumed.poll() is None:
                resumed.kill()
                resumed.communicate(timeout=10)
            processes["recover"] = {
                "pid": resumed.pid,
                "command": resumed.args,
                "exit_code": resumed.returncode,
            }
            (tmp_path / "processes.json").write_text(
                json.dumps(processes, indent=2), encoding="utf-8"
            )
        (tmp_path / "recover.log").write_text(recovery_log, encoding="utf-8")
        assert resumed.returncode == 0, recovery_log[-20000:]
        evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        before, after = evidence["before"], evidence["after"]
        assert after["state"].get("attempt_id") == before["state"].get("attempt_id")
        assert {r["receipt_id"] for r in before["usage"]["ledger"]} <= {
            r["receipt_id"] for r in after["usage"]["ledger"]
        }
        ledger = after["usage"]["ledger"]
        assert len({r["receipt_id"] for r in ledger}) == len(ledger)
        if scenario.startswith("usage_native_"):
            assert len(requests) == len(ledger) == limits["max_steps"]
            assert all(
                (
                    request["generationConfig"]["maxOutputTokens"]
                    if scenario.startswith("usage_native_gemini")
                    else request["max_tokens"]
                )
                == 7
                for request in requests
            )
            assert len({r["invocation_id"] for r in ledger}) == 1
            assert ledger[0]["quality"] == (
                "reported"
                if scenario
                in {"usage_native_gemini_usage", "usage_native_gemini_components"}
                else "estimated"
                if scenario == "usage_native_gemini_empty"
                else "unknown"
            )
            assert after["usage"]["model_calls"] == len(requests)
            assert after["usage"]["tokens"]["reported"] == (
                12
                if scenario == "usage_native_retry"
                else 95
                if scenario
                in {"usage_native_gemini_usage", "usage_native_gemini_components"}
                else None
            )
            assert after["status"] == (
                "awaiting_result"
                if limits["max_steps"] == 2
                or scenario.startswith("usage_native_gemini")
                else "blocked"
            )
            if scenario == "usage_native_missing":
                assert ledger[1]["quality"] == "estimated"
            if scenario in {
                "usage_native_gemini_usage",
                "usage_native_gemini_components",
            }:
                assert ledger[0]["report"]["input_tokens"] == 13
                assert ledger[0]["report"]["output_tokens"] == 82
            if scenario == "usage_native_anthropic":
                assert json.loads(
                    (tmp_path / "native-config.json").read_text(encoding="utf-8")
                )["custom_extra_body"] == {"max_tokens": 500}
            assert not effects
            return
        assert all(
            request["llm_params"]["max_tokens"] == limits["max_output_tokens"]
            for request in requests
        )
        if scenario == "usage_limit":
            assert after["status"] == "blocked"
            assert after["state"]["limit_stop"]["limit"] == "max_steps"
            assert len(requests) == 1 and not effects
            assert after["usage"]["tokens"]["reported"] == 12
        else:
            assert after["status"] == "completed", after["state"]
            assert effects == [{"value": "once"}] * (
                3
                if scenario == "usage_shared_wait"
                else 2
                if scenario in {"usage_parallel", "usage_compression"}
                else 1
            )
            assert after["usage"]["model_calls"] == len(requests) + (
                1 if scenario == "usage_reserved" else 0
            )
            assert (
                after["usage"]["tokens"]["reported"] is None
                if scenario == "usage_compression"
                else after["usage"]["tokens"]["reported"]
                >= (24 if scenario == "usage_completed_limit" else 36)
            )
            if scenario in {
                "usage_reserved",
                "usage_report_pre",
                "usage_report_failure",
            }:
                assert any(
                    r["quality"] == "unknown" and r["kind"] == "model" for r in ledger
                )
            if scenario == "usage_reported":
                assert after["usage"]["tokens"]["reported"] == 48
            if scenario in {"usage_parallel", "usage_shared_wait"}:
                assert after["usage"]["tokens"]["reported"] == (
                    108 if scenario == "usage_shared_wait" else 72
                )
                assert after["usage"] == before["usage"]
                assert after["usage"]["tokens"]["reserved"] == 0
                # Tools have no token report; completed models do not erase that unknown usage.
                assert after["usage"]["tokens"]["unknown_calls"] == (
                    6 if scenario == "usage_shared_wait" else 4
                )
                assert all(
                    receipt["quality"] == "reported"
                    and receipt["reported_tokens"] == 12
                    for receipt in ledger
                    if receipt["kind"] == "model"
                )
                for task in after["tasks"]:
                    assert task["usage"]["model_calls"] == 3
                    assert task["usage"]["tokens"]["reported"] == 36
                    original = next(
                        item
                        for item in waiting["tasks"]
                        if item["run_id"] == task["run_id"]
                    )
                    assert (
                        task["state"]["attempt_id"] == original["state"]["attempt_id"]
                    )
                assert peak_models == 1
                assert any(
                    event["kind"] == "usage.waiting" for event in after["events"]
                )
                assert all(
                    not task["state"].get("capacity_released")
                    for task in after["tasks"]
                )
            if scenario == "usage_compression":
                assert any(
                    "Generate a summary of our previous conversation history"
                    in json.dumps(request["contexts"])
                    for request in requests
                )
                assert after["usage"]["tokens"]["estimated"] > 0
    finally:
        release_first_model.set()
        server.shutdown()
        server.server_close()
