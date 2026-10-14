"""Public remote recovery with independent processes and an external ledger."""

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
    "runner,scenario",
    [
        ("dify", "identity_saved"),
        ("coze", "identity_saved"),
        ("deerflow", "identity_saved"),
        ("deerflow", "end_interrupted"),
        ("deerflow", "end_success"),
        ("dashscope", "result_saved"),
        ("dashscope", "identity_missing"),
        ("dashscope", "partial_eof"),
        ("dashscope", "usage_missing"),
        ("dashscope", "usage_zero"),
        *[
            ("dify", scenario)
            for scenario in (
                "pre_create",
                "identity_save_failure",
                "identity_missing",
                "result_saved",
                "query_running",
                "query_unreachable",
                "query_mismatched",
                "query_missing_output",
                "query_interrupted",
                "chat_unknown",
                "workflow_eof",
                "chat_eof",
                "chat_result_saved",
            )
        ],
    ],
)
def test_remote_run_identity_survives_process_loss_without_recreating_remote_work(
    tmp_path,
    runner,
    scenario,
):
    ledger = []
    release_unknown = threading.Event()
    release_query = threading.Event()

    class RemoteService(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ledger.append({"method": "POST", "path": self.path, "body": body})
            if runner == "coze" and any(
                message["role"] not in {"user", "assistant"}
                for message in body["additional_messages"]
            ):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"code":400,"msg":"Unsupported message role"}')
                return
            if runner == "deerflow" and self.path == "/api/langgraph/threads":
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"thread_id":"thread-original"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if runner == "deerflow":
                self.send_header(
                    "Content-Location", "/api/threads/thread-original/runs/run-original"
                )
            self.end_headers()
            if runner == "dashscope" and scenario in {"usage_missing", "usage_zero"}:
                model = {"model_id": "model-original"}
                if scenario == "usage_zero":
                    model.update(input_tokens=0, output_tokens=0)
                self.wfile.write(
                    b"data: "
                    + json.dumps(
                        {
                            "request_id": "request-original",
                            "output": {
                                "text": "Original verified evidence.",
                                "session_id": "session-original",
                                "finish_reason": "stop",
                            },
                            "usage": {"models": [model]},
                        }
                    ).encode()
                    + b"\n\n"
                )
                self.wfile.flush()
                return
            if runner == "dashscope" and scenario == "result_saved":
                self.wfile.write(
                    b'data: {"request_id":"request-original","output":{"text":"Original verified ","session_id":"session-original","finish_reason":"null"},"usage":{"models":[{"input_tokens":7,"output_tokens":3,"model_id":"model-original"}]}}\n\n'
                    b'data: {"request_id":"request-original","output":{"text":"evidence.","session_id":"session-original","finish_reason":"null"},"usage":{"models":[{"input_tokens":7,"output_tokens":9,"model_id":"model-original"}]}}\n\n'
                    b'data: {"request_id":"request-original","output":{"text":"","session_id":"session-original","finish_reason":"stop"},"usage":{}}\n\n'
                )
                self.wfile.flush()
                return
            if runner == "dashscope" and scenario == "partial_eof":
                self.wfile.write(
                    b'data: {"request_id":"request-original","output":{"text":"Unconfirmed partial answer","session_id":"session-original","finish_reason":"null"},"usage":{}}\n\n'
                )
                self.wfile.flush()
                return
            if scenario == "chat_eof":
                self.wfile.write(
                    b'data: {"event":"message","answer":"Unconfirmed partial answer","conversation_id":"conversation-original"}\n\n'
                )
                self.wfile.flush()
                return
            if scenario == "chat_result_saved":
                self.wfile.write(
                    b'data: {"event":"message","answer":"Original verified evidence.","conversation_id":"conversation-original"}\n\ndata: {"event":"message_end","metadata":{"usage":{"prompt_tokens":11,"completion_tokens":20,"total_tokens":31}}}\n\n'
                )
                self.wfile.flush()
                return
            if scenario in {"identity_missing", "chat_unknown"}:
                self.wfile.flush()
                deadline = time.monotonic() + 20
                while (
                    not (tmp_path / "started.json").exists()
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
                (tmp_path / "kill-ready.json").write_text(
                    (tmp_path / "started.json").read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                release_unknown.wait(20)
                return
            self.wfile.write(
                b'data: {"event":"workflow_started","workflow_run_id":"remote-original","data":{"id":"remote-original"}}\n\n'
                if runner == "dify"
                else b'event: conversation.chat.created\ndata: {"id":"chat-original","conversation_id":"conversation-original","bot_id":"bot-original","status":"in_progress"}\n\n'
                if runner == "coze"
                else b'event: metadata\ndata: {"thread_id":"thread-original","run_id":"run-original"}\n\n'
            )
            self.wfile.flush()
            if runner == "deerflow" and scenario.startswith("end_"):
                self.wfile.write(
                    b'event: values\ndata: {"messages":[{"id":"partial-message","type":"ai","content":"Partial before interruption."}]}\n\nevent: end\ndata: {}\n\n'
                )
                self.wfile.flush()
                return
            if scenario == "workflow_eof":
                return
            if scenario in {"pre_create", "result_saved"}:
                self.wfile.write(
                    b'data: {"event":"workflow_finished","workflow_run_id":"remote-original","data":{"id":"remote-original","status":"succeeded","outputs":{"astrbot_wf_output":{"text":"Original verified evidence."}},"error":null,"total_tokens":31}}\n\n'
                )
                self.wfile.flush()

        def do_GET(self):
            ledger.append({"method": "GET", "path": self.path})
            if (
                scenario == "query_interrupted"
                and not (tmp_path / "query-ready.json").exists()
            ):
                (tmp_path / "query-ready.json").write_text("ready", encoding="utf-8")
                release_query.wait(30)
                return
            self.send_response(503 if scenario == "query_unreachable" else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {
                "id": "remote-original",
                "status": "succeeded",
                "outputs": {
                    "astrbot_wf_output": {"text": "Original verified evidence."},
                },
                "error": None,
                "total_tokens": 31,
            }
            if runner == "coze":
                response = (
                    {
                        "code": 0,
                        "msg": "",
                        "data": {
                            "id": "chat-original",
                            "conversation_id": "conversation-original",
                            "bot_id": "bot-original",
                            "status": "completed",
                        },
                    }
                    if self.path.startswith("/v3/chat/retrieve")
                    else {
                        "code": 0,
                        "msg": "",
                        "data": [
                            {
                                "role": "assistant",
                                "type": "answer",
                                "content": "Original verified evidence.",
                                "content_type": "text",
                                "chat_id": "chat-original",
                                "conversation_id": "conversation-original",
                            },
                        ],
                    }
                )
            elif runner == "deerflow":
                response = (
                    {
                        "thread_id": "thread-original",
                        "run_id": "run-original",
                        "status": "success",
                    }
                    if "/messages" not in self.path
                    else {
                        "data": [
                            {
                                "thread_id": "thread-original",
                                "run_id": "run-original",
                                "seq": 5,
                                "category": "message",
                                "event_type": "llm.ai.response",
                                "metadata": {"caller": "lead_agent"},
                                "content": {
                                    "type": "ai",
                                    "content": "Original verified evidence.",
                                },
                            },
                        ],
                        "has_more": False,
                    }
                )
            if scenario == "query_running":
                response["status"] = "running"
            elif scenario == "end_interrupted":
                response["status"] = "interrupted"
            elif scenario == "query_mismatched":
                response["id"] = "another-run"
            elif scenario == "query_missing_output":
                response["outputs"] = {}
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RemoteService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[2]
    args = [
        sys.executable,
        "-m",
        "tests.agent_teams.remote_worker",
        str(tmp_path),
        f"http://127.0.0.1:{server.server_port}",
    ]
    process = None
    try:
        with (tmp_path / "first.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [*args, "start", runner + ":" + scenario],
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUTF8": "1"},
            )
            deadline = time.monotonic() + 40
            while not (tmp_path / "kill-ready.json").exists():
                assert process.poll() is None, (tmp_path / "first.log").read_text(
                    encoding="utf-8"
                )
                assert time.monotonic() < deadline, (tmp_path / "first.log").read_text(
                    encoding="utf-8"
                )
                time.sleep(0.05)
            process.terminate()
            process.wait(timeout=10)
            release_unknown.set()
        if scenario == "query_interrupted":
            with (tmp_path / "query-process.log").open(
                "w", encoding="utf-8"
            ) as query_log:
                querying = subprocess.Popen(
                    [*args, "recover", runner + ":" + scenario],
                    cwd=root,
                    stdout=query_log,
                    stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONUTF8": "1"},
                )
                try:
                    deadline = time.monotonic() + 40
                    while not (tmp_path / "query-ready.json").exists():
                        assert querying.poll() is None, (
                            tmp_path / "query-process.log"
                        ).read_text(encoding="utf-8")
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                    querying.terminate()
                    querying.wait(timeout=10)
                finally:
                    if querying.poll() is None:
                        querying.kill()
                        querying.wait(timeout=10)
                    release_query.set()
        restarted = subprocess.run(
            [*args, "recover", runner + ":" + scenario],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        (tmp_path / "second.log").write_text(
            restarted.stdout + restarted.stderr, encoding="utf-8"
        )
        assert restarted.returncode == 0, restarted.stdout + restarted.stderr
        evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        if runner == "coze":
            messages = ledger[0]["body"]["additional_messages"]
            assert all(message["role"] in {"user", "assistant"} for message in messages)
            assert "Check evidence." in messages[-1]["content"]
            assert "Find the original evidence." in messages[-1]["content"]
        elif runner == "dify":
            task_input = (
                ledger[0]["body"]["query"]
                if scenario.startswith("chat_")
                else ledger[0]["body"]["inputs"]["astrbot_text_query"]
            )
            assert "Check evidence." in task_input
            assert "Find the original evidence." in task_input
        assert evidence["after"]["snapshot"] == evidence["before"]["snapshot"]
        assert "local-only-key" not in json.dumps(evidence["after"]["snapshot"])
        assert (
            evidence["after"]["state"]["attempt_id"]
            == evidence["before"]["state"]["attempt_id"]
        )
        if scenario in {"workflow_eof", "chat_eof", "partial_eof"}:
            assert evidence["before"]["status"] == "blocked"
            assert evidence["before"]["recovery"]["boundary"] != "remote_result"
        if scenario in {"chat_eof", "partial_eof"}:
            assert evidence["error"]["status"] == 409
            assert evidence["after"]["result"] is None
            assert evidence["after"]["recovery"]["available"] is False
            assert [entry["method"] for entry in ledger] == ["POST"]
            return
        if scenario == "end_interrupted":
            assert evidence["before"]["status"] == "blocked"
            assert evidence["before"]["recovery"]["boundary"] == "remote_running"
            assert evidence["after"]["status"] == "blocked"
            assert evidence["after"]["result"] is None
            assert [entry["method"] for entry in ledger] == [
                "POST",
                "POST",
                "GET",
                "GET",
            ]
            return
        if scenario in {"identity_save_failure", "identity_missing", "chat_unknown"}:
            assert evidence["error"]["status"] == 409
            assert "requires confirmation" in evidence["error"]["message"]
            assert evidence["after"]["result"] is None
            assert evidence["after"]["recovery"]["available"] is False
            assert len(ledger) == 1 and ledger[0]["method"] == "POST"
            if scenario == "chat_unknown":
                assert (
                    evidence["after"]["recovery"]["capability"] == "external_boundary"
                )
            return
        if scenario.startswith("query_") and scenario != "query_interrupted":
            assert evidence["after"]["status"] == "blocked"
            assert evidence["after"]["result"] is None
            assert (
                "confirmation" in evidence["after"]["state"]["reason"]
                or "confirmed" in evidence["after"]["state"]["reason"]
            )
            assert [entry["method"] for entry in ledger] == ["POST", "GET"]
            return
        if scenario in {"pre_create", "result_saved"} and runner != "dashscope":
            assert evidence["after"]["status"] == "completed"
            assert evidence["after"]["result"] == {
                "text": "Original verified evidence."
            }
            assert [entry["method"] for entry in ledger] == ["POST"]
            return
        hooks = [
            json.loads(line)
            for line in (tmp_path / "hooks.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert [entry["kind"] for entry in hooks] == ["request", "response", "done"]
        assert hooks[1]["text"].endswith("[Checked by completion hook]")
        if scenario == "chat_result_saved":
            assert evidence["after"]["status"] == "awaiting_result"
            assert evidence["after"]["usage"]["tokens"]["reported"] == 31
            assert evidence["after"]["usage"]["model_calls"] == 1
            assert evidence["stopped"]["recovery"]["capability"] == "external_boundary"
            assert [entry["method"] for entry in ledger] == ["POST"]
            return
        if runner == "dashscope":
            assert "Check evidence." in ledger[0]["body"]["input"]["prompt"]
            assert "Find the original evidence." in ledger[0]["body"]["input"]["prompt"]
            assert evidence["after"]["status"] == "awaiting_result"
            assert evidence["after"]["usage"]["tokens"]["reported"] == (
                None
                if scenario == "usage_missing"
                else 0
                if scenario == "usage_zero"
                else 16
            )
            assert evidence["after"]["usage"]["tokens"]["unknown_calls"] == (
                1 if scenario == "usage_missing" else 0
            )
            assert evidence["after"]["usage"]["model_calls"] == 1
            assert [entry["method"] for entry in ledger] == ["POST"]
            assert ledger[0]["path"] == "/api/v1/apps/app-original/completion"
            return
        assert evidence["stopped"]["recovery"]["capability"] == "remote_query"
        assert evidence["stopped"]["recovery"]["remote_identity"] == (
            {"workflow_run_id": "remote-original"}
            if runner == "dify"
            else {
                "chat_id": "chat-original",
                "conversation_id": "conversation-original",
                "bot_id": "bot-original",
            }
            if runner == "coze"
            else {"thread_id": "thread-original", "run_id": "run-original"}
        )
        assert evidence["after"]["status"] == (
            "completed" if runner == "dify" else "awaiting_result"
        )
        if runner == "dify":
            assert evidence["after"]["usage"]["tokens"]["reported"] == 31
            assert evidence["after"]["usage"]["model_calls"] == 1
            assert evidence["after"]["result"] == {
                "text": "Original verified evidence."
            }
        else:
            assert evidence["after"]["result"] is None
            assert (
                evidence["after"]["state"]["response"]["text"]
                == "Original verified evidence.[Checked by completion hook]"
            )
        assert (
            evidence["after"]["state"]["attempt_id"]
            == evidence["before"]["state"]["attempt_id"]
        )
        assert evidence["after"]["snapshot"] == evidence["before"]["snapshot"]
        assert evidence["repeated"] == (
            evidence["after"] if runner == "dify" else {"error": 409}
        )
        if scenario == "query_interrupted":
            assert [entry["method"] for entry in ledger] == ["POST", "GET", "GET"]
            return
        assert [(call["method"], call["path"]) for call in ledger] == (
            [
                ("POST", "/workflows/run"),
                ("GET", "/workflows/run/remote-original"),
            ]
            if runner == "dify"
            else [
                ("POST", "/v3/chat"),
                (
                    "GET",
                    "/v3/chat/retrieve?conversation_id=conversation-original&chat_id=chat-original",
                ),
                (
                    "GET",
                    "/v3/chat/message/list?conversation_id=conversation-original&chat_id=chat-original",
                ),
            ]
            if runner == "coze"
            else [
                ("POST", "/api/langgraph/threads"),
                ("POST", "/api/langgraph/threads/thread-original/runs/stream"),
                ("GET", "/api/langgraph/threads/thread-original/runs/run-original"),
                (
                    "GET",
                    "/api/langgraph/threads/thread-original/runs/run-original/messages?limit=200",
                ),
            ]
        )
    finally:
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        release_unknown.set()
        release_query.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (tmp_path / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
