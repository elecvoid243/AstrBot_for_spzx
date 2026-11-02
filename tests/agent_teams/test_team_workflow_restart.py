"""Fixed workflow reverse calls survive real process loss with external ledgers."""

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
        "reverse_pipeline",
        "reverse_call_pre",
        "reverse_call_post",
        "reverse_create_pre",
        "reverse_create_post",
        "reverse_complete_pre",
        "reverse_complete_post",
        "reverse_return_pre",
        "reverse_return_post",
        "reverse_tool_result",
        "reverse_coordinator_first",
        "reverse_unknown",
        "reverse_pause",
        "reverse_cancel",
        "reverse_human",
    ],
)
def test_reverse_workflow_survives_actual_process_loss(
    tmp_path, scenario, preview=False
):
    """Keep one child and return, fixed snapshots, context and exact file versions.

    Args:
        tmp_path: Isolated SQLite and worker communication directory.
        scenario: Normal completion or selected commit/external boundary.
        preview: Serve the actual built dashboard for visual acceptance.
    """
    requests, effects = [], []
    report = "完整证据\n" * 2000 + "COMPLETE_END"
    input_report = "Pinned input evidence\n" * 1000 + "PINNED_INPUT_END"

    class Recorder(BaseHTTPRequestHandler):
        """Keep external model and tool effects outside the killed process."""

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            """Answer provider-native requests using only external tool observations.

            Returns:
                None after sending and recording the deterministic response.
            """
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                effects.append(payload)
                response = {"text": "External verified workflow evidence"}
            else:
                requests.append(payload)
                results = {
                    message["tool_call_id"]: message["content"]
                    for message in payload["contexts"]
                    if message.get("role") == "tool"
                }
                context = json.dumps(payload["contexts"], ensure_ascii=False)
                prompt = (
                    (payload.get("system_prompt") or "")
                    + "\n"
                    + "\n".join(
                        text
                        for message in payload["contexts"]
                        for text in (
                            [message["content"]]
                            if isinstance(message.get("content"), str)
                            else [
                                part["text"]
                                for part in message.get("content", [])
                                if part.get("type") == "text"
                            ]
                        )
                    )
                )
                refs = (
                    json.JSONDecoder().raw_decode(
                        prompt.split("Pinned input references: ", 1)[1]
                    )[0]
                    if "Pinned input references: " in prompt
                    else []
                )

                def call(identity, name, args):
                    """Produce one original provider plan with traceable call identity.

                    Args:
                        identity: Stable provider response and tool call identity.
                        name: Public runtime or external tool name.
                        args: Explicit planned parameters.

                    Returns:
                        Complete provider response for the existing Agent loop.
                    """
                    return {
                        "role": "tool",
                        "id": identity,
                        "tools_call_name": [name],
                        "tools_call_args": [args],
                        "tools_call_ids": [identity],
                        "reasoning_content": "Verified reverse call plan",
                        "reasoning_signature": "reverse-signature",
                        "usage": {"input_other": 11, "output": 5},
                    }

                if (
                    "team_manage" in payload["tools"]
                    and scenario == "reverse_coordinator_first"
                    and (tmp_path / "retry-parent.json").exists()
                ):
                    # A new Attempt makes new observations; the old blocked wait
                    # remains immutable evidence in the failed Attempt.
                    if "reverse-retry-inspect" not in results:
                        response = call(
                            "reverse-retry-inspect",
                            "team_manage",
                            {"action": "inspect", "request_id": "retry-inspect"},
                        )
                    elif "reverse-retry-wait" not in results:
                        task = next(
                            task
                            for task in json.loads(results["reverse-retry-inspect"])[
                                "tasks"
                            ]
                            if task["name"] == "Research saved workflow"
                        )
                        response = call(
                            "reverse-retry-wait",
                            "team_manage",
                            {
                                "action": "wait",
                                "request_id": "retry-wait",
                                "task_ids": [task["task_id"]],
                            },
                        )
                    elif "reverse-retry-read" not in results:
                        task = json.loads(results["reverse-retry-wait"])["tasks"][0]
                        assert task["status"] == "completed", task
                        response = call(
                            "reverse-retry-read",
                            "read_task_artifact",
                            task["result_ref"],
                        )
                    elif "reverse-retry-submit" not in results:
                        response = call(
                            "reverse-retry-submit",
                            "submit_task_result",
                            {"text": "Parent delivered"},
                        )
                    else:
                        response = {
                            "role": "assistant",
                            "completion_text": "Delivered.",
                        }
                elif "team_manage" in payload["tools"]:
                    allowed = json.loads(
                        payload["tool_descriptions"]["team_manage"].split(
                            "Allowed workflows: ", 1
                        )[1]
                    )
                    workflow = next(iter(allowed.values()))
                    if "reverse-input-read" not in results:
                        response = call("reverse-input-read", "read_task_artifact", refs[0])
                    elif "reverse-input-file" not in results:
                        original = json.loads(results["reverse-input-read"])
                        assert original["content"]["text"] == input_report
                        response = call("reverse-input-file", "read_task_artifact", {**refs[0], "file_id": original["content"]["files"][0]["file_id"]})
                    elif "reverse-call" not in results:
                        original_file = json.loads(results["reverse-input-file"])
                        assert original_file["content"] == input_report and original_file["version"] == 1
                        response = call(
                            "reverse-call",
                            "team_manage",
                            {
                                "action": "call_workflow",
                                "request_id": "saved-workflow",
                                "name": "Research saved workflow",
                                "asset_id": workflow["asset_id"],
                                "version": workflow["version"],
                                "inputs": {"topic": "Literal {notes}", "evidence_text": original_file["content"]},
                                "input_artifacts": refs,
                            },
                        )
                    elif "reverse-wait" not in results:
                        task = json.loads(results["reverse-call"])["task_id"]
                        response = call(
                            "reverse-wait",
                            "team_manage",
                            {
                                "action": "wait",
                                "request_id": "wait-workflow",
                                "task_ids": [task],
                            },
                        )
                    elif (
                        json.loads(results["reverse-wait"])["tasks"][0]["status"]
                        != "completed"
                    ):
                        response = {
                            "role": "assistant",
                            "completion_text": "The workflow needs local attention.",
                        }
                    elif "reverse-read" not in results:
                        ref = json.loads(results["reverse-wait"])["tasks"][0][
                            "result_ref"
                        ]
                        response = call("reverse-read", "read_task_artifact", ref)
                    elif "reverse-submit" not in results:
                        response = call(
                            "reverse-submit",
                            "submit_task_result",
                            {"text": "Parent delivered"},
                        )
                    else:
                        response = {
                            "role": "assistant",
                            "completion_text": "Delivered.",
                        }
                elif "Reverse input source" in context:
                    if "input-file" not in results:
                        response = call(
                            "input-file",
                            "store_task_file",
                            {"filename": "input.txt", "content": input_report},
                        )
                    elif "input-submit" not in results:
                        response = call(
                            "input-submit",
                            "submit_task_result",
                            {
                                "result_json": json.dumps(
                                    {
                                        "text": input_report,
                                        "files": [
                                            {
                                                "file_id": json.loads(
                                                    results["input-file"]
                                                )["file_id"]
                                            }
                                        ],
                                    }
                                )
                            },
                        )
                    else:
                        response = {
                            "role": "assistant",
                            "completion_text": "Input submitted.",
                        }
                elif "Reverse effect" in context:
                    assert input_report in prompt
                    assert refs == []
                    if "reverse-effect" not in results:
                        response = call(
                            "reverse-effect", "external_evidence", {"value": "once"}
                        )
                    elif "reverse-file" not in results:
                        response = call(
                            "reverse-file",
                            "store_task_file",
                            {"filename": "evidence.txt", "content": report},
                        )
                    elif "reverse-private-file" not in results:
                        response = call(
                            "reverse-private-file",
                            "store_task_file",
                            {
                                "filename": "private.txt",
                                "content": "UNSUBMITTED_PRIVATE",
                            },
                        )
                    elif "reverse-evidence-submit" not in results:
                        file = json.loads(results["reverse-file"])
                        response = call(
                            "reverse-evidence-submit",
                            "submit_task_result",
                            {
                                "result_json": json.dumps(
                                    {
                                        "text": report,
                                        "files": [{"file_id": file["file_id"]}],
                                    }
                                )
                            },
                        )
                    else:
                        response = {
                            "role": "assistant",
                            "completion_text": "Evidence submitted.",
                        }
                else:
                    identity = (
                        "reverse-dependent-submit"
                        if "Reverse dependent" in context
                        else "reverse-independent-submit"
                    )
                    response = (
                        {"role": "assistant", "completion_text": "Complete."}
                        if identity in results
                        else call(
                            identity,
                            "submit_task_result",
                            {
                                "text": "Dependent delivered"
                                if "Reverse dependent" in context
                                else "Independent delivered"
                            },
                        )
                    )
            (tmp_path / "external-ledger.json").write_text(
                json.dumps({"requests": requests, "effects": effects}), encoding="utf-8"
            )
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
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
    environment = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "ASTRBOT_LOG_LEVEL": "WARNING",
    }
    process = None
    process_records = []
    try:
        with (tmp_path / "start.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [*command, "start", scenario],
                cwd=root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            marker = tmp_path / (
                "completed.json"
                if scenario == "reverse_pipeline"
                else "kill-ready.json"
            )
            deadline = time.monotonic() + 50
            while (
                not marker.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            assert marker.exists(), (tmp_path / "start.log").read_text(
                encoding="utf-8"
            )[-14000:]
            if scenario != "reverse_pipeline":
                process.kill()
            process.wait(timeout=10)
            process_records.append(
                {
                    "action": "start",
                    "pid": process.pid,
                    "killed": scenario != "reverse_pipeline",
                    "exit_code": process.returncode,
                }
            )
        if scenario != "reverse_pipeline":
            process = subprocess.Popen(
                [*command, "recover", scenario],
                cwd=root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            stdout, stderr = process.communicate(timeout=80)
            process_records.append(
                {
                    "action": "recover",
                    "pid": process.pid,
                    "exit_code": process.returncode,
                }
            )
            (tmp_path / "recover.log").write_text(stdout + stderr, encoding="utf-8")
            assert process.returncode == 0, stdout[-14000:] + stderr[-5000:]
        (tmp_path / "processes.json").write_text(
            json.dumps(process_records), encoding="utf-8"
        )
        evidence = json.loads(
            (
                tmp_path
                / (
                    "completed.json"
                    if scenario == "reverse_pipeline"
                    else "recovered.json"
                )
            ).read_text(encoding="utf-8")
        )
        current = evidence["after"]
        assert effects == [{"value": "once"}]
        assert len(current["scopes"]) == 1
        scope = current["scopes"][0]
        pinned_input = json.loads(
            (tmp_path / "reverse-input.json").read_text(encoding="utf-8")
        )
        input_ref = pinned_input["reference"]
        assert scope["snapshot"]["input_artifacts"] == [input_ref]
        coordinator = current["tasks"][0]
        input_consumer = scope["tasks"][0]
        assert input_ref not in input_consumer["read_artifacts"]
        for consumer in (coordinator,):
            assert input_ref in consumer["read_artifacts"]
            assert (
                sum(
                    event["kind"] == "artifact.read"
                    and event["data"]["reference"] == input_ref
                    for event in consumer["events"]
                )
                == 1
            )
        if scenario == "reverse_cancel":
            assert current["status"] == "cancelled"
            assert scope["status"] == "cancelled"
            assert all(task["status"] == "cancelled" for task in scope["tasks"])
            return
        if scenario == "reverse_unknown":
            assert current["status"] == "blocked", current
            assert evidence["unknown_rejections"] == [409]
            statuses = {task["node_id"]: task["status"] for task in scope["tasks"]}
            assert statuses["independent"] == "completed"
            assert statuses["dependent"] == "dependency_failed"
            assert any(
                call["status"] == "unknown"
                for task in scope["tasks"]
                for call in task["tool_calls"]
            )
            return
        assert current["status"] == "completed", [
            (task["node_id"], task["status"], task["state"].get("reason"))
            for scope in [current, *current["scopes"]]
            for task in scope["tasks"]
        ]
        assert (
            current["result"] == coordinator["result"] == {"text": "Parent delivered"}
        )
        assert scope["snapshot"]["asset_version"] == 1
        assert scope["snapshot"]["definition"]["name"].startswith("Fixed research")
        assert all(
            task["snapshot"].get("effective", {}).get("prompt") != "CHANGED ROLE"
            for task in scope["tasks"]
        )
        assert [event["kind"] for event in current["events"]].count(
            "team.call_workflow"
        ) == 1
        assert [event["kind"] for event in current["events"]].count(
            "scope.started"
        ) == 1
        assert [event["kind"] for event in current["events"]].count(
            "child.returned"
        ) == 1
        call_task = current["tasks"][1]
        source = scope["tasks"][0]
        assert call_task["result"] == source["result"]
        assert source["result"]["text"] == report
        assert call_task["state"]["child_result_ref"] == source["result_ref"]
        if scenario != "reverse_pipeline":
            assert evidence["file_verified"] is True
        child_ids = {task["run_id"] for task in scope["tasks"]}
        assert all(
            "PRIVATE_PARENT_GOAL" not in json.dumps(request["contexts"])
            for request in requests
            if request["run_id"] in child_ids
        )
        assert all(
            "Original verified persona" in json.dumps(request["contexts"])
            and "profile=original" in json.dumps(request["contexts"])
            for request in requests
        )
        # The original provider plan for creating the call is resumed, never replanned.
        assert (
            len(
                [
                    request
                    for request in requests
                    if "team_manage" in request["tools"]
                    and any(
                        message.get("tool_call_id") == "reverse-input-file"
                        for message in request["contexts"]
                    )
                    and not any(
                        message.get("tool_call_id") == "reverse-call"
                        for message in request["contexts"]
                    )
                ]
            )
            == 1
        )
        if evidence.get("before", {}).get("scopes"):
            assert scope["run_id"] == evidence["before"]["scopes"][0]["run_id"]
            assert {
                task["run_id"]: task["state"]["attempt_id"] for task in scope["tasks"]
            } == {
                task["run_id"]: task["state"]["attempt_id"]
                for task in evidence["before"]["scopes"][0]["tasks"]
            }
        if "before" in evidence:
            old = evidence["before"]
            assert current["snapshot"] == old["snapshot"]
            assert current["usage"]["model_calls"] >= old["usage"]["model_calls"]
            assert current["usage"]["steps_used"] >= old["usage"]["steps_used"]
            assert {item["receipt_id"] for item in old["usage"]["ledger"]} <= {
                item["receipt_id"] for item in current["usage"]["ledger"]
            }
            assert (
                coordinator["state"]["attempt_id"]
                == old["tasks"][0]["state"]["attempt_id"]
            ) is (scenario != "reverse_coordinator_first")
        for task in [*current["tasks"], *scope["tasks"]]:
            for records, identity in (
                (task["process"], "record_id"),
                (task["tool_calls"], "record_id"),
                (task["messages"], "message_id"),
            ):
                assert len({record[identity] for record in records}) == len(records)
        if scenario == "reverse_coordinator_first":
            unsubmitted = evidence["unsubmitted"]
            original = unsubmitted["tasks"][0]
            assert unsubmitted["status"] == "blocked"
            assert original["status"] == "awaiting_result"
            assert original["result"] is None and original["pending_result"] is None
            assert unsubmitted["result"] is None
            assert all(
                task["status"] == "completed"
                for task in unsubmitted["scopes"][0]["tasks"]
            )
            assert evidence["recover_rejections"] == [409]
            old_wait = next(
                call
                for call in original["tool_calls"]
                if call["call_id"] == "reverse-wait"
            )
            assert (
                next(
                    call
                    for call in coordinator["tool_calls"]
                    if call["record_id"] == old_wait["record_id"]
                )
                == old_wait
            )
            blocked = json.loads(old_wait["raw_results"][0]["content"][0]["text"])[
                "tasks"
            ][0]
            assert (
                blocked["status"] == "blocked"
                and blocked["result"] is None
                and blocked["result_ref"] is None
            )
            assert not any(
                call["name"] == "submit_task_result" for call in original["tool_calls"]
            )
            final_response = [
                record["data"]
                for record in original["process"]
                if record["kind"] == "model_response"
            ][-1]
            assert final_response["role"] == "assistant"
            assert final_response["text"] == "The workflow needs local attention."
            assert final_response["tool_calls"] == []
            assert (
                coordinator["attempts"][0]["attempt_id"]
                == original["state"]["attempt_id"]
            )
            new_wait = next(
                call
                for call in coordinator["tool_calls"]
                if call["call_id"] == "reverse-retry-wait"
            )
            assert new_wait["record_id"] != old_wait["record_id"]
            assert new_wait["attempt_id"] == coordinator["state"]["attempt_id"]
            returned = json.loads(new_wait["raw_results"][0]["content"][0]["text"])[
                "tasks"
            ][0]
            assert (
                returned["status"] == "completed"
                and returned["result"] == call_task["result"]
            )
            first_retry = next(
                index
                for index, request in enumerate(requests)
                if request["run_id"] == coordinator["run_id"]
                and request["attempt_id"] == coordinator["state"]["attempt_id"]
            )
            assert first_retry == 17
            before_child = json.loads(
                (tmp_path / "parent-before-child.json").read_text(encoding="utf-8")
            )
            assert before_child["tasks"][0]["status"] == "awaiting_result"
            assert before_child["scopes"][0]["tasks"][0]["status"] == "interrupted"
        if preview:
            subprocess.run(
                [*command, "serve", scenario], cwd=root, env=environment, check=True
            )
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
