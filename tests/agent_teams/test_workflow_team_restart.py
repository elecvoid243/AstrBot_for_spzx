"""Actual nested Agent pipelines survive force-killed SQLite boundaries."""

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
        "child_pipeline",
        "child_create_pre",
        "child_create_post",
        "child_complete_pre",
        "child_complete_post",
        "child_return_pre",
        "child_return_post",
        "child_tool_result",
        "child_unknown",
        "child_pause",
        "child_cancel",
        "child_human",
    ],
)
def test_workflow_child_scope_survives_real_process_loss(
    tmp_path, scenario, preview=False
):
    """Verify original attempts, unique scopes and external effects after a kill.

    Args:
        tmp_path: Persistent isolated database and communication directory.
        scenario: Normal execution or selected scope/checkpoint transaction.
        preview: Keep the real production dashboard available for visual acceptance.
    """
    requests, effects = [], []
    report = "完整证据\n" * 2000 + "COMPLETE_END"

    class Recorder(BaseHTTPRequestHandler):
        """External model and tool records live outside the terminated process."""

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            """Return deterministic provider-native plans from real tool results.

            Returns:
                None after persisting the independent request/effect ledger.
            """
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                effects.append(payload)
                response = {"text": "External verified evidence " + payload["value"]}
            else:
                requests.append(payload)
                results = {
                    message["tool_call_id"]: message["content"]
                    for message in payload["contexts"]
                    if message.get("role") == "tool"
                }
                context = json.dumps(payload["contexts"], ensure_ascii=False)

                def call(identity, name, args):
                    """Encode one complete provider tool response.

                    Args:
                        identity: Stable response and call identity.
                        name: Public runtime or external tool.
                        args: Exact planned parameters.

                    Returns:
                        Provider-native response for the existing tool loop.
                    """
                    return {
                        "role": "tool",
                        "id": identity,
                        "tools_call_name": [name],
                        "tools_call_args": [args],
                        "tools_call_ids": [identity],
                        "reasoning_content": "Verified nested plan",
                        "reasoning_signature": "child-signature",
                        "usage": {"input_other": 11, "output": 5},
                    }

                if "team_manage" in payload["tools"]:
                    if "child-create" not in results:
                        response = call(
                            "child-create",
                            "team_manage",
                            {
                                "action": "create_member",
                                "request_id": "child-member",
                                "preset_id": "writer",
                                "name": "Independent child writer",
                            },
                        )
                    elif "child-note" not in results:
                        member = json.JSONDecoder().raw_decode(results["child-create"])[
                            0
                        ]["member_id"]
                        response = call(
                            "child-note",
                            "team_communicate",
                            {
                                "action": "send",
                                "request_id": "child-note",
                                "recipient_member_id": member,
                                "text": "CHILD_SCOPED_NOTE",
                            },
                        )
                    elif "child-dispatch-first" not in results:
                        member = json.JSONDecoder().raw_decode(results["child-create"])[
                            0
                        ]["member_id"]
                        response = call(
                            "child-dispatch-first",
                            "team_manage",
                            {
                                "action": "dispatch",
                                "request_id": "child-first",
                                "member_id": member,
                                "name": "Child first",
                                "task": "Child first evidence",
                            },
                        )
                    elif "child-wait-first" not in results:
                        task = json.JSONDecoder().raw_decode(
                            results["child-dispatch-first"]
                        )[0]["task_id"]
                        response = call(
                            "child-wait-first",
                            "team_manage",
                            {
                                "action": "wait",
                                "request_id": "child-wait-first",
                                "task_ids": [task],
                            },
                        )
                    elif "child-read" not in results:
                        ref = json.JSONDecoder().raw_decode(
                            results["child-wait-first"]
                        )[0]["tasks"][0]["result_ref"]
                        response = call("child-read", "read_task_artifact", ref)
                    elif "child-dispatch-second" not in results:
                        member = json.JSONDecoder().raw_decode(results["child-create"])[
                            0
                        ]["member_id"]
                        first = json.JSONDecoder().raw_decode(
                            results["child-dispatch-first"]
                        )[0]["task_id"]
                        response = call(
                            "child-dispatch-second",
                            "team_manage",
                            {
                                "action": "dispatch",
                                "request_id": "child-second",
                                "member_id": member,
                                "name": "Child second",
                                "task": "Child second evidence",
                                "dependencies": [first],
                            },
                        )
                    elif "child-wait-second" not in results:
                        task = json.JSONDecoder().raw_decode(
                            results["child-dispatch-second"]
                        )[0]["task_id"]
                        response = call(
                            "child-wait-second",
                            "team_manage",
                            {
                                "action": "wait",
                                "request_id": "child-wait-second",
                                "task_ids": [task],
                            },
                        )
                    elif "child-submit" not in results:
                        response = call(
                            "child-submit", "submit_task_result", {"text": report}
                        )
                    else:
                        response = {
                            "role": "assistant",
                            "completion_text": "Submitted.",
                        }
                elif "Parent dependent" in context:
                    if "parent-read" not in results:
                        framing = next(
                            part["text"]
                            for message in payload["contexts"]
                            for part in message.get("content", [])
                            if isinstance(part, dict)
                            and "Pinned input references: " in part.get("text", "")
                        )
                        refs = json.JSONDecoder().raw_decode(
                            framing.split("Pinned input references: ", 1)[1]
                        )[0]
                        response = call("parent-read", "read_task_artifact", refs[0])
                    elif "parent-submit" not in results:
                        response = call(
                            "parent-submit",
                            "submit_task_result",
                            {"text": "Parent delivered"},
                        )
                    else:
                        response = {
                            "role": "assistant",
                            "completion_text": "Delivered.",
                        }
                elif "Parent independent" in context:
                    response = (
                        {"role": "assistant", "completion_text": "Independent saved."}
                        if "independent-submit" in results
                        else call(
                            "independent-submit",
                            "submit_task_result",
                            {"text": "PRIVATE_PARENT_RESULT"},
                        )
                    )
                else:
                    second = "Child second evidence" in context
                    effect_id = (
                        "child-effect-second" if second else "child-effect-first"
                    )
                    submit_id = (
                        "child-member-submit-second"
                        if second
                        else "child-member-submit-first"
                    )
                    if effect_id not in results:
                        response = call(
                            effect_id,
                            "external_evidence",
                            {"value": "second" if second else "first"},
                        )
                    elif submit_id not in results:
                        response = call(
                            submit_id,
                            "submit_task_result",
                            {
                                "text": "Child complete evidence "
                                + ("second" if second else "first")
                            },
                        )
                    else:
                        response = {
                            "role": "assistant",
                            "completion_text": "Evidence submitted.",
                        }
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
    processes = []
    try:
        with (tmp_path / "start.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [*command, "start", scenario],
                cwd=root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            processes.append(process)
            marker = tmp_path / (
                "completed.json" if scenario == "child_pipeline" else "kill-ready.json"
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
            )[-12000:]
            if scenario != "child_pipeline":
                process.kill()
            process.wait(timeout=10)
        if scenario != "child_pipeline":
            recovered = subprocess.run(
                [*command, "recover", scenario],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=75,
            )
            (tmp_path / "recover.log").write_text(
                recovered.stdout + recovered.stderr, encoding="utf-8"
            )
            assert recovered.returncode == 0, (
                recovered.stdout[-15000:] + recovered.stderr[-5000:]
            )
        evidence = json.loads(
            (
                tmp_path
                / (
                    "completed.json"
                    if scenario == "child_pipeline"
                    else "recovered.json"
                )
            ).read_text(encoding="utf-8")
        )
        current = evidence["after"]
        if scenario == "child_cancel":
            assert current["status"] == "cancelled"
            assert current["tasks"][0]["status"] == "cancelled"
            assert current["scopes"][0]["status"] == "cancelled"
            assert all(
                task["status"] == "cancelled" for task in current["scopes"][0]["tasks"]
            )
            assert effects == [{"value": "first"}]
            assert all(
                task["state"]["attempt_id"] == old["state"]["attempt_id"]
                for task, old in zip(
                    current["scopes"][0]["tasks"],
                    evidence["before"]["scopes"][0]["tasks"],
                )
            )
            return
        if scenario == "child_unknown":
            assert current["status"] == "blocked"
            assert evidence["unknown_rejections"] == [409]
            assert current["usage"]["model_calls"] == 7
            assert effects == [{"value": "first"}]
            assert (
                next(
                    task
                    for task in current["tasks"]
                    if task["node_id"] == "independent"
                )["status"]
                == "completed"
            )
            assert (
                next(
                    task for task in current["tasks"] if task["node_id"] == "dependent"
                )["status"]
                == "dependency_failed"
            )
            assert any(
                call["status"] == "unknown"
                for scope in current["scopes"]
                for task in scope["tasks"]
                for call in task["tool_calls"]
            )
            return
        assert current["status"] == "completed", {
            "status": current["status"],
            "tasks": [
                (task["node_id"], task["status"], task["state"].get("reason"))
                for scope in [current, *current["scopes"]]
                for task in scope["tasks"]
            ],
        }
        assert effects == [{"value": "first"}, {"value": "second"}]
        assert len(current["scopes"]) == 1
        scope = current["scopes"][0]
        assert len(scope["tasks"]) == (4 if scenario == "child_human" else 3)
        assert (
            len(
                [
                    event
                    for event in current["events"]
                    if event["kind"] == "scope.started"
                ]
            )
            == 1
        )
        assert (
            len(
                [
                    event
                    for event in current["events"]
                    if event["kind"] == "team.create_member"
                ]
            )
            == 1
        )
        assert (
            len(
                [
                    event
                    for event in current["events"]
                    if event["kind"] == "team.dispatch"
                ]
            )
            == 2
        )
        assert (
            len(
                [
                    event
                    for event in current["events"]
                    if event["kind"] == "child.returned"
                ]
            )
            == 1
        )
        assert current["tasks"][0]["result"] == {"text": report}
        assert scope["snapshot"]["root_run_id"] == current["run_id"]
        child_ids = {task["run_id"] for task in scope["tasks"]}
        child_requests = [
            request for request in requests if request["run_id"] in child_ids
        ]
        assert all(
            "PRIVATE_PARENT" not in json.dumps(request["contexts"])
            for request in child_requests
        )
        coordinator_requests = [
            request for request in child_requests if "team_manage" in request["tools"]
        ]
        for request in coordinator_requests:
            texts = [
                part["text"]
                for message in request["contexts"]
                for part in message.get("content", [])
                if isinstance(part, dict)
                and "Declared run inputs (JSON):\n" in part.get("text", "")
            ]
            decoded, _ = json.JSONDecoder().raw_decode(
                texts[0].split("Declared run inputs (JSON):\n", 1)[1]
            )
            assert decoded == {
                "goal": "研究 {notes}\nLiteral braces",
                "notes": '中文\n{"year": 2025}',
            }
        second = next(
            request
            for request in child_requests
            if "Child second evidence" in json.dumps(request["contexts"])
        )
        assert "Child complete evidence first" in json.dumps(second["contexts"])
        assert "CHILD_SCOPED_NOTE" in json.dumps(second["contexts"])
        assert all(
            "CHILD_SCOPED_NOTE" not in json.dumps(request["contexts"])
            for request in requests
            if request["run_id"] not in child_ids
        )
        assert all(
            "Original verified persona" in json.dumps(request["contexts"])
            and "profile=original" in json.dumps(request["contexts"])
            for request in requests
        )
        if scenario == "child_pipeline":
            assert current["usage"]["model_calls"] == 20
        read_request = next(
            request
            for request in requests
            if any(
                message.get("tool_call_id") == "parent-read"
                for message in request["contexts"]
            )
        )
        assert "COMPLETE_END" in json.dumps(read_request["contexts"])
        if "before" in evidence:
            old_tasks = [
                *evidence["before"]["tasks"],
                *[
                    task
                    for scope in evidence["before"].get("scopes", [])
                    for task in scope["tasks"]
                ],
            ]
            new_tasks = [*current["tasks"], *scope["tasks"]]
            for old in old_tasks:
                new = next(
                    task for task in new_tasks if task["run_id"] == old["run_id"]
                )
                assert new["state"]["attempt_id"] == old["state"]["attempt_id"]
                assert new["snapshot"] == old["snapshot"] or old["status"] == "pending"
        if preview:
            subprocess.run(
                [*command, "serve", scenario], cwd=root, env=environment, check=True
            )
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
