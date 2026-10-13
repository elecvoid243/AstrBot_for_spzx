"""Real main-pipeline team execution and independent forced-process recovery."""

import json
import os
import re
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
        "team_pipeline",
        "team_create",
        "team_dispatch",
        "team_wait",
        "team_tool_result",
        "team_finish",
        "team_save_failure",
        "team_slow",
        "team_unknown",
        "team_steps",
        "team_inputs",
        "team_message_pipeline",
        "team_message_live",
        "team_message_delivery_pre",
        "team_message_delivery_post",
        "team_message_tool_pre",
        "team_message_tool_post",
        "team_message_consume_pre",
        "team_message_consume_post",
        "team_message_delivery_failure",
        "team_message_consume_failure",
        "team_message_parameters",
        "team_message_parameters_checkpoint",
    ],
)
def test_autonomous_team_uses_main_pipeline_and_recovers_without_duplicate_decisions(
    tmp_path, scenario, preview=False
):
    """Recover durable team decisions without duplicating contexts, tasks or effects.

    Args:
        tmp_path: Persistent worker database and external effect ledger.
        scenario: Normal execution or a selected real storage failure boundary.
        preview: Keep the real dashboard and external model available for UI checks.
    """
    requests, effects, parameter_plans = [], [], {}

    class Recorder(BaseHTTPRequestHandler):
        """Independent external model and tool boundary."""

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            """Return deterministic model plans based on actual public tool results.

            Returns:
                None after saving independent request and effect evidence.
            """
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                if scenario == "team_slow":
                    time.sleep(1.5)
                effects.append(payload)
                response = {
                    "text": "External verified evidence for " + payload["value"]
                }
            else:
                requests.append(payload)
                messages = payload["contexts"]
                results = {
                    message["tool_call_id"]: message["content"]
                    for message in messages
                    if message.get("role") == "tool"
                }

                def call(identity, tools):
                    """Encode the external provider's complete planned tool response.

                    Args:
                        identity: Stable provider response identity.
                        tools: Tool names, arguments and original call identities.

                    Returns:
                        Provider-native response consumed by the actual Agent pipeline.
                    """
                    return {
                        "role": "tool",
                        "id": identity,
                        "tools_call_name": [tool[0] for tool in tools],
                        "tools_call_args": [tool[1] for tool in tools],
                        "tools_call_ids": [tool[2] for tool in tools],
                        "reasoning_content": "Verified team planning",
                        "reasoning_signature": "team-signature",
                    }

                if scenario.startswith("team_message_parameters") and payload["parameter_query"]:
                    response = {**parameter_plans[payload["member_id"]],
                                "id": parameter_plans[payload["member_id"]]["id"] + "-parameters"}
                elif "team_manage" in payload["tools"]:
                    role_id = re.search(
                        r'Allowed roles: \{"([^"]+)"',
                        payload["tool_descriptions"]["team_manage"],
                    )[1]
                    if scenario == "team_steps" and "dispatch-a" in results:
                        response = {
                            "role": "assistant",
                            "completion_text": "Step limit reached",
                        }
                    elif "create-a" not in results:
                        response = call(
                            "team-create",
                            [
                                (
                                    "team_manage",
                                    {
                                        "action": "create_member",
                                        "request_id": "member-a",
                                        "role_id": role_id,
                                        "name": "Writer A",
                                    },
                                    "create-a",
                                ),
                                (
                                    "team_manage",
                                    {
                                        "action": "create_member",
                                        "request_id": "member-b",
                                        "role_id": role_id,
                                        "name": "Writer B",
                                    },
                                    "create-b",
                                ),
                            ],
                        )
                    elif scenario.startswith("team_message_") and "message-initial" not in results:
                        response = call("team-message-initial", [
                            ("team_communicate", {"action": "send", "request_id": "initial-note",
                             "recipient_member_id": json.JSONDecoder().raw_decode(results["create-a"])[0]["member_id"],
                             "text": "INITIAL_MESSAGE 中文 {literal}"}, "message-initial"),
                            ("team_communicate", {"action": "send", "request_id": "initial-note",
                             "recipient_member_id": json.JSONDecoder().raw_decode(results["create-a"])[0]["member_id"],
                             "text": "INITIAL_MESSAGE 中文 {literal}"}, "message-duplicate"),
                        ])
                    elif "dispatch-a" not in results:
                        response = call(
                            "team-dispatch",
                            [
                                (
                                    "team_manage",
                                    {
                                        "action": "dispatch",
                                        "request_id": "task-a",
                                        "member_id": json.JSONDecoder().raw_decode(
                                            results["create-a"]
                                        )[0]["member_id"],
                                        "name": "First evidence A",
                                        "task": "writer-one-first",
                                    },
                                    "dispatch-a",
                                ),
                                (
                                    "team_manage",
                                    {
                                        "action": "dispatch",
                                        "request_id": "task-b",
                                        "member_id": json.JSONDecoder().raw_decode(
                                            results["create-b"]
                                        )[0]["member_id"],
                                        "name": "First evidence B",
                                        "task": "writer-two-first",
                                    },
                                    "dispatch-b",
                                ),
                                (
                                    "submit_task_result",
                                    {"text": "Premature completion"},
                                    "premature",
                                ),
                            ],
                        )
                    elif "wait-first" not in results:
                        response = call(
                            "team-wait",
                            [
                                (
                                    "team_manage",
                                    {
                                        "action": "wait",
                                        "request_id": "wait-first",
                                        "task_ids": [
                                            json.JSONDecoder().raw_decode(results[key])[
                                                0
                                            ]["task_id"]
                                            for key in ["dispatch-a", "dispatch-b"]
                                        ],
                                    },
                                    "wait-first",
                                )
                            ],
                        )
                    elif scenario.startswith("team_message_") and "message-artifact" not in results:
                        response = call("team-message-artifact", [
                            ("team_communicate", {"action": "send", "request_id": "evidence-note",
                             "recipient_member_id": json.JSONDecoder().raw_decode(results["create-a"])[0]["member_id"],
                             "text": "READ_SHARED_VERSION",
                             "artifact_refs": [{"artifact_id": json.JSONDecoder().raw_decode(results["dispatch-b"])[0]["run_id"], "version": 1}]}, "message-artifact"),
                        ])
                    elif "dispatch-followup" not in results:
                        response = call(
                            "team-followup",
                            [
                                (
                                    "team_manage",
                                    {
                                        "action": "dispatch",
                                        "request_id": "follow-up",
                                        "member_id": json.JSONDecoder().raw_decode(
                                            results["create-a"]
                                        )[0]["member_id"],
                                        "name": "Revision by Writer A",
                                        "task": "writer-followup",
                                        "dependencies": [
                                            json.JSONDecoder().raw_decode(
                                                results["dispatch-a"]
                                            )[0]["task_id"]
                                        ],
                                    },
                                    "dispatch-followup",
                                )
                            ],
                        )
                    elif "wait-followup" not in results:
                        response = call(
                            "team-wait-followup",
                            [
                                (
                                    "team_manage",
                                    {
                                        "action": "wait",
                                        "request_id": "wait-followup",
                                        "task_ids": [
                                            json.JSONDecoder().raw_decode(
                                                results["dispatch-followup"]
                                            )[0]["task_id"]
                                        ],
                                    },
                                    "wait-followup",
                                )
                            ],
                        )
                    elif "read-full" not in results:
                        response = call(
                            "team-read",
                            [
                                (
                                    "read_task_artifact",
                                    {
                                        "artifact_id": json.JSONDecoder().raw_decode(
                                            results["dispatch-a"]
                                        )[0]["run_id"],
                                        "version": 1,
                                    },
                                    "read-full",
                                )
                            ],
                        )
                    elif "team-submission" not in results:
                        response = call(
                            "team-submit",
                            [
                                (
                                    "submit_task_result",
                                    {"text": "Final verified team report"},
                                    "team-submission",
                                )
                            ],
                        )
                    else:
                        response = {"role": "assistant", "completion_text": "Submitted"}
                else:
                    user = next(
                        message
                        for message in reversed(messages)
                        if message["role"] == "user" and any(name in json.dumps(message) for name in ["writer-followup", "writer-two-first", "writer-one-first"])
                    )
                    goal = next(
                        name
                        for name in [
                            "writer-followup",
                            "writer-two-first",
                            "writer-one-first",
                        ]
                        if name in json.dumps(user)
                    )
                    if scenario == "team_message_live" and goal == "writer-one-first" and "effect-" + goal not in results and not (tmp_path / "model-held").exists():
                        (tmp_path / "model-held").write_text("ready", encoding="utf-8")
                        deadline = time.monotonic() + 15
                        while not (tmp_path / "mid-model-sent").exists():
                            assert time.monotonic() < deadline
                            time.sleep(0.02)
                    if "effect-" + goal not in results:
                        response = call(
                            "effect-plan-" + goal,
                            [("external_evidence", {"value": goal}, "effect-" + goal)],
                        )
                    elif scenario.startswith("team_message_") and goal == "writer-two-first" and "inspect-peers" not in results:
                        response = call("member-inspect", [("team_communicate", {"action": "inspect", "request_id": "inspect-peers"}, "inspect-peers")])
                    elif scenario.startswith("team_message_") and goal == "writer-two-first" and "member-feedback" not in results:
                        coordinator = next(member for member in json.JSONDecoder().raw_decode(results["inspect-peers"])[0]["members"] if member["name"] == "Coordinator")
                        response = call("member-feedback", [("team_communicate", {"action": "send", "request_id": "member-feedback", "recipient_member_id": coordinator["member_id"], "text": "MEMBER_FEEDBACK"}, "member-feedback")])
                    elif scenario.startswith("team_message_") and goal == "writer-followup" and "read-shared" not in results:
                        delivery = next(json.loads(part["text"].split("Team member message (JSON):\n", 1)[1])
                                        for message in messages for part in message.get("content", [])
                                        if isinstance(part, dict) and "READ_SHARED_VERSION" in part.get("text", ""))
                        response = call("member-read-shared", [("read_task_artifact", delivery["artifact_refs"][0], "read-shared")])
                    elif "submit-" + goal not in results:
                        response = call(
                            "member-submit-" + goal,
                            [
                                (
                                    "submit_task_result",
                                    {
                                        "text": "Complete evidence "
                                        + goal
                                        + ": "
                                        + "Verified evidence. " * 200
                                        + "COMPLETE_END"
                                    },
                                    "submit-" + goal,
                                )
                            ],
                        )
                    else:
                        response = {"role": "assistant", "completion_text": "Submitted"}
                if scenario.startswith("team_message_parameters") and not payload["parameter_query"] and response["role"] == "tool":
                    parameter_plans[payload["member_id"]] = response
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
    environment = {**os.environ, "PYTHONUTF8": "1"}
    try:
        if scenario in {"team_pipeline", "team_slow", "team_steps", "team_message_pipeline", "team_message_live", "team_message_parameters"}:
            first = subprocess.run(
                [*command, "start", scenario],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            assert first.returncode == 0, first.stdout + first.stderr
        else:
            with (tmp_path / "first.log").open("w", encoding="utf-8") as log:
                worker = subprocess.Popen(
                    [*command, "start", scenario],
                    cwd=root,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 45
                    while not (tmp_path / "kill-ready.json").exists():
                        assert worker.poll() is None, (
                            tmp_path / "first.log"
                        ).read_text(encoding="utf-8")
                        assert time.monotonic() < deadline, (
                            tmp_path / "first.log"
                        ).read_text(encoding="utf-8")
                        time.sleep(0.05)
                    if scenario == "team_save_failure":
                        assert len(requests) == 1
                        assert effects == []
                    if scenario == "team_message_consume_failure":
                        assert not any(effect["value"] == "writer-one-first" for effect in effects)
                        assert not any("team_manage" not in request["tools"] and "writer-one-first" in json.dumps(request["contexts"]) for request in requests)
                    worker.kill()
                    worker.wait(timeout=10)
                finally:
                    if worker.poll() is None:
                        worker.kill()
                        worker.wait(timeout=10)
            resumed = subprocess.run(
                [*command, "recover", scenario],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        evidence = json.loads(
            (tmp_path / "team-evidence.json").read_text(encoding="utf-8")
        )
        current = evidence["after"]
        if scenario == "team_inputs":
            coordinator_requests = [
                request for request in requests if "team_manage" in request["tools"]
            ]
            assert len(coordinator_requests) > 1
            for request in coordinator_requests:
                texts = []
                for message in request["contexts"]:
                    content = message.get("content")
                    if isinstance(content, str):
                        texts.append(content)
                    elif isinstance(content, list):
                        texts.extend(part["text"] for part in content if "text" in part)
                framing = next(
                    text for text in texts if "Declared run inputs (JSON):\n" in text
                )
                decoded, _ = json.JSONDecoder().raw_decode(
                    framing.split("Declared run inputs (JSON):\n", 1)[1]
                )
                assert decoded == {
                    "goal": "Research energy with two independent writers, then revise with the first writer.",
                    "constraints": 'ONLY_USE_2025_DATA\nKeep {"year": 2025} and {goal} literally.',
                }
            assert current["snapshot"]["asset_version"] == 1
            assert current["snapshot"]["team_definition"]["required_inputs"] == [
                "goal",
                "constraints",
            ]
        if scenario == "team_unknown":
            assert current["status"] == "blocked"
            assert [effect["value"] for effect in effects] == [
                "writer-one-first",
                "writer-two-first",
            ]
            assert any(
                call["status"] in {"started", "unknown"}
                for task in current["tasks"]
                for call in task["tool_calls"]
            )
            return
        if scenario == "team_steps":
            assert current["status"] == "blocked"
            assert current["tasks"][0]["status"] == "awaiting_result"
            assert current["tasks"][0]["snapshot"]["effective"]["max_steps"] == 1
            assert len(requests) <= 3
            assert not any(
                event["kind"] == "team.dispatch" for event in current["events"]
            )
            assert effects == []
            return
        assert current["status"] == "completed", current
        if scenario.startswith("team_message_"):
            assert len(current["messages"]) == (4 if scenario == "team_message_live" else 3)
            assert all(message["consumption"] is not None for message in current["messages"])
            if "before" in evidence:
                assert evidence["late_rejections"] == [409, 409]
            message_ids = [message["message_id"] for message in current["messages"]]
            for request in requests:
                injected = [json.loads(part["text"].split("Team member message (JSON):\n", 1)[1])["message_id"]
                            for message in request["contexts"] for part in message.get("content", [])
                            if isinstance(part, dict) and part.get("text", "").startswith("Team member message (JSON):\n")]
                assert all(injected.count(identity) <= 1 for identity in message_ids)
                assert all(next(message for message in current["messages"] if message["message_id"] == identity)["recipient_member_id"] == request["member_id"] for identity in injected)
            if scenario == "team_message_live":
                writer_requests = [request for request in requests if request["member_id"] == next(message for message in current["messages"] if message["text"] == "MID_MODEL_MESSAGE")["recipient_member_id"]]
                assert "MID_MODEL_MESSAGE" not in json.dumps(writer_requests[0]["contexts"])
                assert "MID_MODEL_MESSAGE" in json.dumps(writer_requests[1]["contexts"])
            shared = next(request for request in requests if any(message.get("tool_call_id") == "read-shared" for message in request["contexts"]))
            assert "COMPLETE_END" in json.dumps(shared["contexts"])
            assert "Complete evidence writer-two-first" in json.dumps(shared["contexts"])
            assert all("Error:" not in message.get("content", "") for message in shared["contexts"] if message.get("tool_call_id") == "read-shared")
        assert len(current["members"]) == 3
        assert len(current["tasks"]) == 4
        if scenario.startswith("team_message_"):
            assert sorted(effect["value"] for effect in effects) == ["writer-followup", "writer-one-first", "writer-two-first"]
        else:
            assert [effect["value"] for effect in effects] == [
                "writer-one-first", "writer-two-first", "writer-followup",
            ]
        assert (
            len(
                [
                    event
                    for event in current["events"]
                    if event["kind"] == "team.create_member"
                ]
            )
            == 2
        )
        assert (
            len(
                [
                    event
                    for event in current["events"]
                    if event["kind"] == "team.dispatch"
                ]
            )
            == 3
        )
        assert all(task["result_ref"]["version"] == 1 for task in current["tasks"])
        if "before" in evidence:
            for task in evidence["before"]["tasks"]:
                recovered = next(
                    item
                    for item in current["tasks"]
                    if item["run_id"] == task["run_id"]
                )
                if scenario == "team_message_consume_failure" and task["recovery"]["boundary"] is None:
                    assert task["state"]["attempt_id"] != recovered["state"]["attempt_id"]
                    assert any(attempt["attempt_id"] == task["state"]["attempt_id"] for attempt in recovered["attempts"])
                else:
                    assert task["state"]["attempt_id"] == recovered["state"]["attempt_id"]
        followup = next(
            request
            for request in requests
            if "writer-followup" in json.dumps(request["contexts"])
            and "team_manage" not in request["tools"]
        )
        assert "Complete evidence writer-one-first" in json.dumps(followup["contexts"])
        second = next(
            request
            for request in requests
            if "writer-two-first" in json.dumps(request["contexts"])
            and "team_manage" not in request["tools"]
        )
        assert "writer-one-first" not in json.dumps(second["contexts"])
        assert all(
            "Original verified persona" in json.dumps(request["contexts"])
            and "profile=original" in json.dumps(request["contexts"])
            for request in requests
        )
        member_requests = [
            request
            for request in requests
            if any(
                "Original role prompt" in json.dumps(message)
                for message in request["contexts"]
                if message["role"] == "user"
            )
        ]
        assert len(member_requests) >= 3
        assert all("team_manage" not in request["tools"] for request in member_requests)
        assert current["tasks"][0]["read_artifacts"] == [
            {"artifact_id": current["tasks"][1]["run_id"], "version": 1}
        ]
        if preview:
            subprocess.run(
                [*command, "serve", scenario], cwd=root, env=environment, check=True
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
