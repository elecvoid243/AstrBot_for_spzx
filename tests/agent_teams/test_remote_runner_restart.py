"""Public remote recovery with independent processes and an external ledger."""

import json
import os
import socket
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
        ("dashscope", "accepted_disconnect"),
        ("dashscope", "accepted_redirect"),
        ("dashscope", "usage_missing"),
        ("dashscope", "usage_zero"),
        ("dify", "workflow_events_saved"),
        ("dify", "workflow_events_query"),
        ("dify", "workflow_events_process_failure"),
        ("dify", "workflow_events_checkpoint_failure"),
        ("dify", "chat_workflow_events"),
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
                "chat_thoughts",
                "chat_thought_process_failure",
                "chat_thought_checkpoint_failure",
            )
        ],
    ],
)
def test_remote_run_identity_survives_process_loss_without_recreating_remote_work(
    tmp_path,
    runner,
    scenario,
):
    """Recover a real remote invocation across independent worker processes.

    The parent owns the HTTP ledger while the production pipeline writes SQLite.
    Kill-ready files mark committed identities/results or an explicit unknown
    boundary; the parent terminates that worker and rebuilds the public service.

    Args:
        tmp_path: Isolated database, worker logs, kill signals and external ledger.
        runner: Actual Dify, Coze, DeerFlow or DashScope production adapter.
        scenario: Provider response, persistence fault or stream-loss window;
            saved results are reused, queryable identities are inspected, and
            ambiguous deliveries remain pending without another creation POST.
    """
    ledger = []
    release_unknown = threading.Event()
    release_query = threading.Event()

    class RemoteService(BaseHTTPRequestHandler):
        def do_POST(self):
            """Record creation before emitting the selected provider stream.

            Accepted disconnects and redirects happen after the body is recorded.
            Other scenarios expose an identity, complete response or process
            observation before the worker's durable kill/failure boundary.
            """
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ledger.append({"method": "POST", "path": self.path, "body": body})
            if runner == "dashscope" and scenario == "accepted_disconnect":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                self.close_connection = True
                return
            if runner == "dashscope" and scenario == "accepted_redirect":
                self.send_response(307)
                self.send_header("Location", "/another-creation")
                self.end_headers()
                return
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
            if scenario.startswith("chat_thought"):
                thought = {
                    "event": "agent_thought",
                    "id": "thought-original",
                    "task_id": "task-original",
                    "message_id": "message-original",
                    "conversation_id": "conversation-original",
                    "position": 1,
                    "thought": "Original provider reasoning.",
                    "tool": "evidence_lookup",
                    "tool_input": '{"query":"original evidence"}',
                    "observation": "",
                }
                for event in (
                    thought,
                    thought,
                    {**thought, "observation": "Original tool observation."},
                    {
                        **thought,
                        "id": "thought-second",
                        "position": 2,
                        "thought": "Second provider reasoning.",
                        "tool": "",
                        "tool_input": "",
                        "observation": "",
                    },
                ):
                    self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                self.wfile.flush()
            if scenario == "chat_workflow_events":
                delta = {
                    "event": "reasoning_chunk",
                    "task_id": "task-original",
                    "data": {
                        "message_id": "message-original",
                        "node_id": "llm-original",
                        "reasoning": "Original reasoning fragment.",
                        "is_final": False,
                    },
                }
                log = {
                    "event": "agent_log",
                    "task_id": "task-original",
                    "data": {
                        "id": "log-original",
                        "node_execution_id": "agent-execution-original",
                        "node_id": "agent-original",
                        "label": "Evidence tool",
                        "status": "success",
                        "data": {"output": "Original agent tool observation."},
                    },
                }
                for event in (delta, delta, log, log):
                    self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                self.wfile.write(
                    b'data: {"event":"node_finished","task_id":"task-original","workflow_run_id":"remote-original","data":{"id":"chatflow-node-execution","node_id":"llm-original","node_type":"llm","title":"Evidence reasoning","index":1,"inputs":{},"process_data":{},"outputs":{"reasoning_content":"Original chatflow node reasoning."},"status":"succeeded","error":null,"elapsed_time":1.0,"created_at":1700000000,"finished_at":1700000001}}\n\n'
                )
                self.wfile.write(
                    b'data: {"event":"message","answer":"Original verified evidence.","conversation_id":"conversation-original"}\n\ndata: {"event":"message_end","metadata":{"reasoning":{"llm-original":"Original final reasoning."},"usage":{"prompt_tokens":11,"completion_tokens":20,"total_tokens":31}}}\n\n'
                )
                self.wfile.flush()
                return
            if scenario == "chat_result_saved" or scenario.startswith("chat_thought"):
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
            if scenario.startswith("workflow_events"):
                node = {
                    "event": "node_finished",
                    "task_id": "task-original",
                    "workflow_run_id": "remote-original",
                    "data": {
                        "id": "llm-execution-original",
                        "node_id": "llm-original",
                        "node_type": "llm",
                        "title": "Evidence reasoning",
                        "index": 1,
                        "inputs": {"prompt": "Check original evidence"},
                        "process_data": {
                            "reasoning": "Original workflow process reasoning."
                        },
                        "outputs": {
                            "text": "Intermediate answer",
                            "reasoning_content": "Original workflow provider reasoning.",
                        },
                        "status": "succeeded",
                        "error": None,
                        "elapsed_time": 1.0,
                        "created_at": 1700000000,
                        "finished_at": 1700000001,
                    },
                }
                tool = {
                    **node,
                    "data": {
                        **node["data"],
                        "id": "tool-execution-original",
                        "node_id": "tool-original",
                        "node_type": "tool",
                        "inputs": {"query": "Original workflow tool input."},
                        "outputs": {"text": "Original workflow tool observation."},
                        "iteration_id": "iteration-original",
                    },
                }
                for event in (
                    node,
                    node,
                    tool,
                    {
                        **tool,
                        "data": {
                            **tool["data"],
                            "id": "tool-execution-next",
                            "iteration_id": "iteration-next",
                        },
                    },
                ):
                    self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                self.wfile.flush()
                if scenario == "workflow_events_query":
                    release_unknown.wait(20)
                    return
            if scenario in {"pre_create", "result_saved"} or scenario.startswith(
                "workflow_events"
            ):
                self.wfile.write(
                    b'data: {"event":"workflow_finished","workflow_run_id":"remote-original","data":{"id":"remote-original","status":"succeeded","outputs":{"astrbot_wf_output":{"text":"Original verified evidence."}},"error":null,"total_tokens":31}}\n\n'
                )
                self.wfile.flush()

        def do_GET(self):
            """Query the original run without creating replacement remote work.

            The interrupted-query scenario blocks at the external boundary until
            the parent kills the querying process; other variants return the
            original status, unavailable service, mismatched ID or absent output.
            """
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
        if scenario in {
            "workflow_eof",
            "chat_eof",
            "partial_eof",
            "accepted_disconnect",
            "accepted_redirect",
        }:
            assert evidence["before"]["status"] == "blocked"
            assert evidence["before"]["recovery"]["boundary"] != "remote_result"
        if (
            scenario
            in {"chat_eof", "partial_eof", "accepted_disconnect", "accepted_redirect"}
            or scenario.endswith("thought_process_failure")
            or scenario.endswith("thought_checkpoint_failure")
        ):
            assert evidence["error"]["status"] == 409
            assert evidence["after"]["result"] is None
            assert evidence["after"]["recovery"]["available"] is False
            assert [entry["method"] for entry in ledger] == ["POST"]
            assert evidence["after"]["usage"]["model_calls"] == 1
            assert evidence["after"]["usage"]["tokens"]["unknown_calls"] == 1
            if scenario.startswith("chat_thought"):
                assert evidence["before"]["status"] == "blocked"
                assert "save" in evidence["before"]["state"]["reason"].lower()
                records = evidence["after"]["process"]
                assert (
                    records[0]["data"]["event"]["thought"]
                    == "Original provider reasoning."
                )
                assert not any(record["kind"] == "model_response" for record in records)
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
        if (
            scenario
            in {
                "pre_create",
                "result_saved",
            }
            or scenario.startswith("workflow_events")
        ) and runner != "dashscope":
            assert evidence["after"]["status"] == "completed"
            assert evidence["after"]["result"] == {
                "text": "Original verified evidence."
            }
            assert [entry["method"] for entry in ledger] == (
                ["POST", "GET"]
                if scenario.startswith("workflow_events")
                and scenario != "workflow_events_saved"
                else ["POST"]
            )
            if scenario.startswith("workflow_events"):
                if scenario.endswith("failure"):
                    assert evidence["before"]["status"] == "blocked"
                    assert "save" in evidence["before"]["state"]["reason"].lower()
                    assert evidence["before"]["result"] is None
                records = [
                    record
                    for record in evidence["after"]["process"]
                    if record["kind"] == "remote_event"
                    and record["data"]["event"]["event"] == "node_finished"
                ]
                expected_ids = [
                    "llm-execution-original",
                    "tool-execution-original",
                ]
                if not scenario.endswith("failure"):
                    expected_ids.append("tool-execution-next")
                assert (
                    len(records)
                    == len({r["record_id"] for r in records})
                    == len(expected_ids)
                )
                assert [
                    r["data"]["event"]["data"]["id"] for r in records
                ] == expected_ids
                assert records[0]["data"]["event"]["data"]["outputs"] == {
                    "text": "Intermediate answer",
                    "reasoning_content": "Original workflow provider reasoning.",
                }
                assert records[0]["data"]["event"]["data"]["process_data"] == {
                    "reasoning": "Original workflow process reasoning."
                }
                assert records[1]["data"]["event"]["data"]["inputs"] == {
                    "query": "Original workflow tool input."
                }
                assert records[-1]["data"]["event"]["data"]["outputs"] == {
                    "text": "Original workflow tool observation."
                }
                assert {r["attempt_id"] for r in records} == {
                    evidence["before"]["state"]["attempt_id"]
                }
                assert {r["task_id"] for r in records} == {
                    evidence["after"]["state"]["task_id"]
                }
                assert len({r["member_id"] for r in records}) == 1
                assert {r["data"]["event"]["workflow_run_id"] for r in records} == {
                    "remote-original"
                }
                if not scenario.endswith("failure"):
                    assert (
                        records[1]["data"]["event"]["data"]["node_id"]
                        == (records[2]["data"]["event"]["data"]["node_id"])
                    )
                    assert records[1]["data"]["event"]["data"]["iteration_id"] == (
                        "iteration-original"
                    )
                    assert records[2]["data"]["event"]["data"]["iteration_id"] == (
                        "iteration-next"
                    )
                assert not evidence["after"]["tool_calls"]
            return
        hooks = [
            json.loads(line)
            for line in (tmp_path / "hooks.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert [entry["kind"] for entry in hooks] == ["request", "response", "done"]
        assert hooks[1]["text"].endswith("[Checked by completion hook]")
        if scenario in {"chat_result_saved", "chat_thoughts", "chat_workflow_events"}:
            assert evidence["after"]["status"] == "awaiting_result"
            assert evidence["after"]["usage"]["tokens"]["reported"] == 31
            assert evidence["after"]["usage"]["model_calls"] == 1
            assert evidence["stopped"]["recovery"]["capability"] == "external_boundary"
            assert [entry["method"] for entry in ledger] == ["POST"]
            if scenario == "chat_workflow_events":
                records = [
                    r
                    for r in evidence["after"]["process"]
                    if r["kind"] == "remote_event"
                ]
                assert [r["data"]["event"]["event"] for r in records] == [
                    "reasoning_chunk",
                    "reasoning_chunk",
                    "agent_log",
                    "node_finished",
                    "message_end",
                ]
                assert len({r["record_id"] for r in records}) == 5
                assert records[0]["data"]["event"] == records[1]["data"]["event"]
                assert records[0]["data"]["event"]["data"]["reasoning"] == (
                    "Original reasoning fragment."
                )
                assert records[2]["data"]["event"]["data"]["data"] == {
                    "output": "Original agent tool observation."
                }
                assert records[3]["data"]["event"]["data"]["outputs"] == {
                    "reasoning_content": "Original chatflow node reasoning."
                }
                assert records[4]["data"]["event"]["metadata"]["reasoning"] == {
                    "llm-original": "Original final reasoning."
                }
                assert not evidence["after"]["tool_calls"]
            if scenario == "chat_thoughts":
                records = [
                    record
                    for record in evidence["after"]["process"]
                    if record["kind"] == "remote_event"
                ]
                assert len(records) == 3
                assert len({record["record_id"] for record in records}) == 3
                assert {record["attempt_id"] for record in records} == {
                    evidence["before"]["state"]["attempt_id"]
                }
                assert [record["data"]["event"]["id"] for record in records] == [
                    "thought-original",
                    "thought-original",
                    "thought-second",
                ]
                assert (
                    records[1]["data"]["event"]["observation"]
                    == "Original tool observation."
                )
                assert records[0]["data"]["event"]["tool"] == "evidence_lookup"
                assert (
                    records[0]["data"]["event"]["tool_input"]
                    == '{"query":"original evidence"}'
                )
                assert (
                    records[2]["data"]["event"]["thought"]
                    == "Second provider reasoning."
                )
                assert not evidence["after"]["tool_calls"]
                assert (
                    len(
                        [
                            record
                            for record in evidence["after"]["process"]
                            if record["kind"] == "model_response"
                        ]
                    )
                    == 1
                )
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


def test_dashscope_retry_policy_is_local_to_the_team_session(tmp_path):
    """Use the real SDK to contrast one team delivery with ordinary chat retries.

    Args:
        tmp_path: Isolated directory storing the observed request ledger.
    """
    import requests
    from dashscope import Application

    from astrbot.core.agent.runners.dashscope.dashscope_agent_runner import (
        _SingleDeliverySession,
    )

    ledger = []

    class RemoteService(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ledger.append(body["input"]["prompt"])
            if ledger.count("ordinary chat") != 2:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"request_id":"ordinary-original","output":{"text":"Ordinary answer","finish_reason":"stop"},"usage":{}}'
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RemoteService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    parameters = {
        "app_id": "app-original",
        "api_key": "local-only-key",
        "base_address": f"http://127.0.0.1:{server.server_port}",
    }
    try:
        with _SingleDeliverySession() as session:
            with pytest.raises(
                requests.RequestException, match="requires confirmation"
            ):
                Application.call(prompt="team request", session=session, **parameters)
        response = Application.call(prompt="ordinary chat", **parameters)
        assert response.output.text == "Ordinary answer"
        assert ledger == ["team request", "ordinary chat", "ordinary chat"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (tmp_path / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
