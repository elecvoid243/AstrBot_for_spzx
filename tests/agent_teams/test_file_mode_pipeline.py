"""Public file capability contract with actual pipelines and external effects."""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.agent_teams.checkpoint_worker import run_worker


@pytest.fixture
def file_environment(tmp_path):
    """Provide model and sandbox services independent of execution workers.

    Args:
        tmp_path: Isolated state and filesystem effects.

    Yields:
        The external endpoint, requests and sandbox operations.
    """
    requests, operations = [], []
    target = tmp_path / "effect.txt"
    target.write_text("before\n", encoding="utf-8")

    class Recorder(BaseHTTPRequestHandler):
        """Serve scripted model responses and real isolated filesystem effects."""

        def log_message(self, *args):
            """Silence standard HTTP logs.

            Args:
                *args: Standard HTTP log arguments.
            """

        def do_POST(self):
            """Respond at an external model or sandbox filesystem boundary."""
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            case = json.loads((tmp_path / "case.json").read_text(encoding="utf-8"))
            if self.path in {"/python", "/fs"}:
                effect_before = {
                    "exists": target.exists(),
                    "bytes": target.read_bytes().hex() if target.exists() else None,
                    "mtime_ns": target.stat().st_mtime_ns if target.exists() else None,
                }
            if self.path == "/python":
                operations.append({"kind": "python", **payload})
                result = subprocess.run(
                    [sys.executable, "-B", "-c", payload["code"]],
                    cwd=tmp_path,
                    capture_output=True,
                    text=True,
                    timeout=payload["timeout"],
                )
                response = {
                    "success": result.returncode == 0,
                    "output": result.stdout,
                    "error": result.stderr,
                }
            elif self.path == "/fs":
                operations.append({"kind": "fs", **payload})
                assert Path(payload["path"]).resolve() == target.resolve()
                target.write_text(payload["content"], encoding=payload["encoding"])
                response = {"success": True}
            else:
                requests.append(payload)
                results = {
                    message["tool_call_id"]: message["content"]
                    for message in payload["contexts"]
                    if message.get("role") == "tool"
                }
                name, args, call_id = (
                    "submit_task_result",
                    {"text": "File effect complete."},
                    "submit",
                )
                if "team_manage" in payload["tools"]:
                    name = "team_manage"
                    if "create" not in results:
                        role = json.loads(
                            (tmp_path / "role.json").read_text(encoding="utf-8")
                        )
                        args, call_id = (
                            {
                                "action": "create_member",
                                "request_id": "create",
                                "role_id": role["role_id"],
                                "name": "Writer",
                            },
                            "create",
                        )
                    elif "dispatch" not in results:
                        member = json.JSONDecoder().raw_decode(results["create"])[0]
                        args, call_id = (
                            {
                                "action": "dispatch",
                                "request_id": "dispatch",
                                "member_id": member["member_id"],
                                "name": "Write",
                                "task": "Edit the isolated file.",
                            },
                            "dispatch",
                        )
                    elif "wait" not in results:
                        task = json.JSONDecoder().raw_decode(results["dispatch"])[0]
                        args, call_id = (
                            {
                                "action": "wait",
                                "request_id": "wait",
                                "task_ids": [task["task_id"]],
                            },
                            "wait",
                        )
                    else:
                        name = "submit_task_result"
                elif case.get("exercise"):
                    if case["settings"]["file_tool_mode"] == "legacy":
                        if "write" not in results:
                            name, args, call_id = (
                                "astrbot_file_write_tool",
                                {"path": str(target), "content": "written\n"},
                                "write",
                            )
                        elif "edit" not in results:
                            name, args, call_id = (
                                "astrbot_file_edit_tool",
                                {"path": str(target), "old": "written", "new": "after"},
                                "edit",
                            )
                    elif "patch" not in results:
                        name, args, call_id = (
                            "astrbot_apply_patch",
                            {
                                "patch": f"*** Begin Patch\n*** Update File: {target.as_posix()}\n-before\n+after\n*** End Patch"
                            },
                            "patch",
                        )
                        if case.get("delete_patch"):
                            args = {
                                "patch": f"*** Begin Patch\n*** Delete File: {target.as_posix()}\n*** End Patch",
                                "confirm": True,
                            }
                response = {
                    "role": "tool",
                    "id": "plan-" + call_id,
                    "tools_call_name": [name],
                    "tools_call_args": [args],
                    "tools_call_ids": [call_id],
                }
                if (
                    "submit" in results
                    or case["entry"] == "ordinary"
                    and call_id == "submit"
                ):
                    response = {
                        "role": "assistant",
                        "completion_text": "File effect complete.",
                    }
                if case.get("old_attempt") and len(requests) == 1:
                    response = {
                        "role": "assistant",
                        "completion_text": "No result has been submitted.",
                    }
            if self.path in {"/python", "/fs"}:
                operations[-1].update(
                    before=effect_before,
                    after={
                        "exists": target.exists(),
                        "bytes": target.read_bytes().hex() if target.exists() else None,
                        "mtime_ns": target.stat().st_mtime_ns
                        if target.exists()
                        else None,
                    },
                )
            (tmp_path / "external-ledger.json").write_text(
                json.dumps({"requests": requests, "operations": operations}),
                encoding="utf-8",
            )
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(
            endpoint=f"http://127.0.0.1:{server.server_port}",
            requests=requests,
            operations=operations,
            target=target,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("runtime", ["local", "sandbox"])
@pytest.mark.parametrize(
    "dependency,mode",
    [
        ("astrbot_apply_patch", "legacy"),
        ("astrbot_file_write_tool", "patch"),
        ("astrbot_file_edit_tool", "patch"),
    ],
)
@pytest.mark.parametrize("entry", ["workflow", "autonomous"])
def test_mode_excluded_dependency_cannot_be_ready(
    tmp_path, file_environment, runtime, dependency, mode, entry
):
    """Explicit incompatible dependencies cannot preview ready or start.

    Args:
        tmp_path: Isolated persisted state.
        file_environment: Independent external model and filesystem recorder.
        runtime: Supported computer environment.
        dependency: Explicit required file tool.
        mode: Profile behavior mode excluding that tool.
        entry: Actual graph or autonomous launch entry.
    """
    case = {
        "entry": entry,
        "tools": [dependency],
        "settings": {"computer_use_runtime": runtime, "file_tool_mode": mode},
    }
    (tmp_path / "case.json").write_text(json.dumps(case), encoding="utf-8")
    asyncio.run(
        run_worker(tmp_path, file_environment.endpoint, "start", "file_mode_dependency")
    )
    observed = json.loads((tmp_path / "observed.json").read_text(encoding="utf-8"))
    assert observed["preview"]["ready"] is False
    assert observed["readiness"]["ready"] is False
    issue = next(
        item
        for item in observed["preview"]["issues"]
        if item["path"] == "installation.bindings." + dependency
    )
    assert issue["code"] == "INCOMPATIBLE"
    assert "file_tool_mode=" + mode in issue["message"]
    assert observed["start_error"]["fields"]
    assert file_environment.requests == []
    assert file_environment.target.read_text(encoding="utf-8") == "before\n"


@pytest.mark.parametrize("runtime", ["local", "sandbox"])
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("legacy", ["astrbot_file_write_tool", "astrbot_file_edit_tool"]),
        ("patch", ["astrbot_apply_patch"]),
    ],
)
@pytest.mark.parametrize("entry", ["ordinary", "workflow", "autonomous"])
@pytest.mark.parametrize("inherit", [False, True])
def test_ready_file_capabilities_execute_in_the_main_pipeline(
    tmp_path, file_environment, runtime, mode, expected, entry, inherit
):
    """The ready capability group reaches actual schemas and file execution.

    Args:
        tmp_path: Isolated persisted state and file.
        file_environment: Independent model and sandbox filesystem.
        runtime: Local or sandbox computer environment.
        mode: Configured behavior mode.
        expected: Independently specified file capability group.
        entry: Ordinary chat, workflow or autonomous team.
        inherit: Inherit enabled tools or explicitly select the file group.
    """
    case = {
        "entry": entry,
        "tools": None if inherit else expected,
        "exercise": True,
        "settings": {
            "computer_use_runtime": runtime,
            "file_tool_mode": mode,
            "computer_use_require_admin": False,
        },
    }
    (tmp_path / "case.json").write_text(json.dumps(case), encoding="utf-8")
    asyncio.run(
        run_worker(tmp_path, file_environment.endpoint, "start", "file_mode_execute")
    )
    observed = json.loads((tmp_path / "observed.json").read_text(encoding="utf-8"))
    assert observed["preview"]["ready"] is True, observed["preview"]["issues"]
    if entry != "ordinary":
        assert observed["readiness"]["ready"] is True
        assert observed["run"]["status"] == "completed", observed["run"]
    writer_requests = [
        request
        for request in file_environment.requests
        if "team_manage" not in request["tools"]
    ]
    assert writer_requests
    file_names = {
        "astrbot_file_write_tool",
        "astrbot_file_edit_tool",
        "astrbot_apply_patch",
    }
    for request in writer_requests:
        assert file_names.intersection(request["tools"]) == set(expected)
        if entry != "ordinary" and not inherit:
            assert "external_evidence" not in request["tools"]
        if entry != "ordinary":
            assert "Original verified persona." in str(request["contexts"])
            assert "File role prompt." in str(request["contexts"])
    for resource in observed["resources"]["tools"]:
        if resource["id"] in file_names:
            assert ("default" in resource["enabled_profiles"]) == (
                resource["id"] in expected
            )
    assert file_environment.target.read_text(encoding="utf-8") == "after\n"
    if runtime == "sandbox":
        assert file_environment.operations


@pytest.mark.parametrize("runtime", ["local", "sandbox"])
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("legacy", ["astrbot_file_write_tool", "astrbot_file_edit_tool"]),
        ("patch", ["astrbot_apply_patch"]),
    ],
)
@pytest.mark.parametrize("entry", ["workflow", "autonomous"])
def test_live_mode_edits_preserve_the_launched_behavior(
    tmp_path, file_environment, runtime, mode, expected, entry
):
    """A saved role keeps its mode after current profile edits.

    Args:
        tmp_path: Isolated persistence and effects.
        file_environment: Independent model and sandbox filesystem.
        runtime: Supported computer environment.
        mode: Mode frozen by launch.
        expected: Independently specified file capability group.
        entry: Workflow or autonomous task source.
    """
    changed_mode = "patch" if mode == "legacy" else "legacy"
    case = {
        "entry": entry,
        "tools": expected,
        "exercise": True,
        "change_after_launch": changed_mode,
        "settings": {
            "computer_use_runtime": runtime,
            "file_tool_mode": mode,
            "computer_use_require_admin": False,
        },
    }
    (tmp_path / "case.json").write_text(json.dumps(case), encoding="utf-8")
    asyncio.run(
        run_worker(tmp_path, file_environment.endpoint, "start", "file_mode_change")
    )
    observed = json.loads((tmp_path / "observed.json").read_text(encoding="utf-8"))
    assert observed["run"]["status"] == "completed", observed["run"]
    for request in file_environment.requests:
        if "team_manage" not in request["tools"]:
            assert {
                "astrbot_apply_patch",
                "astrbot_file_write_tool",
                "astrbot_file_edit_tool",
            }.intersection(request["tools"]) == set(expected)
    assert file_environment.target.read_text(encoding="utf-8") == "after\n"


@pytest.mark.parametrize("runtime", ["local", "sandbox"])
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("legacy", ["astrbot_file_write_tool", "astrbot_file_edit_tool"]),
        ("patch", ["astrbot_apply_patch"]),
    ],
)
@pytest.mark.parametrize(
    "policy", ["mode_edit", "runtime_revoked", "tool_revoked", "unknown"]
)
def test_frozen_file_plan_survives_process_loss_with_current_authorization(
    tmp_path, file_environment, runtime, mode, expected, policy
):
    """Recovery keeps original behavior while current revocation and uncertainty block.

    Args:
        tmp_path: Real SQLite files shared across independent processes.
        file_environment: Parent-owned model and filesystem recorder.
        runtime: Original local or sandbox environment.
        mode: Frozen file mode.
        expected: Independently specified original tool group.
        policy: Behavior edit, live revocation or uncertain external effect.
    """
    settings = {
        "computer_use_runtime": runtime,
        "file_tool_mode": mode,
        "computer_use_require_admin": False,
    }
    case = {
        "entry": "workflow",
        "tools": expected,
        "exercise": True,
        "old_attempt": True,
        "crash_phase": "unknown" if policy == "unknown" else "response",
        "settings": settings,
        "recovery_settings": {
            **settings,
            "file_tool_mode": "patch" if mode == "legacy" else "legacy",
            **({"computer_use_runtime": "none"} if policy == "runtime_revoked" else {}),
        },
        **({"revoke_tool": expected[0]} if policy == "tool_revoked" else {}),
    }
    if policy == "admin_tightened":
        case["recovery_settings"]["computer_use_require_admin"] = True
    elif policy == "admin_removed":
        case["settings"]["computer_use_require_admin"] = True
        case["recovery_settings"]["computer_use_require_admin"] = True
        case.update(admins=["alice"], recovery_admins=[])
    elif policy == "readonly":
        case["recovery_settings"]["file_access_default_mode"] = "readonly"
    elif policy == "roots_revoked":
        case["settings"].update(
            file_access_default_mode="workspace",
            file_access_extra_roots=[str(tmp_path)],
        )
        case["recovery_settings"].update(
            file_access_default_mode="workspace", file_access_extra_roots=[]
        )
    elif policy == "blacklist_added":
        case["delete_patch"] = True
        case["recovery_settings"]["file_remove_blacklist"] = [
            str(file_environment.target)
        ]
    (tmp_path / "case.json").write_text(json.dumps(case), encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-B",
        "-m",
        "tests.agent_teams.checkpoint_worker",
        str(tmp_path),
        file_environment.endpoint,
    ]
    processes = []
    environment = {**os.environ, "PYTHONUTF8": "1"}
    with (tmp_path / "start.log").open("w", encoding="utf-8") as log:
        worker = subprocess.Popen(
            [*command, "start", "file_mode_recovery"],
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        processes.append(
            {
                "action": "start",
                "pid": worker.pid,
                "command": [*command, "start", "file_mode_recovery"],
            }
        )
        (tmp_path / "processes.json").write_text(
            json.dumps(processes), encoding="utf-8"
        )
        try:
            deadline = time.monotonic() + 40
            while not (tmp_path / "kill-ready.json").exists():
                assert worker.poll() is None, (tmp_path / "start.log").read_text(
                    encoding="utf-8"
                )
                assert time.monotonic() < deadline, (tmp_path / "start.log").read_text(
                    encoding="utf-8"
                )
                time.sleep(0.02)
            worker.kill()
            worker.wait(timeout=10)
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=10)
            processes[0]["exit"] = worker.returncode
            (tmp_path / "processes.json").write_text(
                json.dumps(processes), encoding="utf-8"
            )
    content_before = file_environment.target.read_bytes()
    modified_before = file_environment.target.stat().st_mtime_ns
    requests_before = list(file_environment.requests)
    operations_before = list(file_environment.operations)
    for action in ["inspect", "recover"]:
        with (tmp_path / (action + ".log")).open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [*command, action, "file_mode_recovery"],
                cwd=root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            process.wait(timeout=40)
            processes.append(
                {
                    "action": action,
                    "pid": process.pid,
                    "exit": process.returncode,
                    "command": [*command, action, "file_mode_recovery"],
                }
            )
            (tmp_path / "processes.json").write_text(
                json.dumps(processes), encoding="utf-8"
            )
            assert process.returncode == 0, (tmp_path / (action + ".log")).read_text(
                encoding="utf-8"
            )
    inspected = json.loads((tmp_path / "inspect.json").read_text(encoding="utf-8"))[
        "before"
    ]
    recovered = json.loads((tmp_path / "recover.json").read_text(encoding="utf-8"))
    before, after = recovered["before"], recovered["after"]
    assert before["snapshot"] == inspected["snapshot"] == after["snapshot"]
    assert before["snapshot"]["profile"]["provider_settings"]["file_tool_mode"] == mode
    assert before["state"]["attempt_id"] == after["state"]["attempt_id"]
    assert len(after["attempts"]) == len(before["attempts"]) == 1
    assert after["attempts"] == before["attempts"] == inspected["attempts"]
    old_attempt = json.loads(
        (tmp_path / "old-attempt.json").read_text(encoding="utf-8")
    )
    assert after["attempts"][0]["attempt_id"] == old_attempt["state"]["attempt_id"]
    assert after["state"]["attempt_id"] != old_attempt["state"]["attempt_id"]
    assert before["snapshot"]["initiator"] == after["snapshot"]["initiator"] == "alice"
    if policy == "mode_edit":
        assert after["status"] == "completed", recovered
        assert file_environment.target.read_text(encoding="utf-8") == "after\n"
        assert len(requests_before) == 2
        assert len(file_environment.requests) > 2
        assert (
            file_environment.requests[2]["contexts"]
            != file_environment.requests[1]["contexts"]
        )
        for request in file_environment.requests:
            assert set(expected).issubset(request["tools"])
            assert "Original verified persona." in str(request["contexts"])
            assert "sender=alice;role=member" in str(request["contexts"])
            assert "Edited persona." not in str(request["contexts"])
    elif policy in {
        "admin_tightened",
        "admin_removed",
        "readonly",
        "roots_revoked",
        "blacklist_added",
    }:
        assert after["status"] == "completed", recovered
        assert file_environment.target.read_bytes() == content_before
        assert file_environment.target.stat().st_mtime_ns == modified_before
        if runtime == "sandbox" and policy == "blacklist_added":
            # Patch validation may read the target before rejecting deletion.
            assert all(
                operation["before"] == operation["after"]
                for operation in file_environment.operations[len(operations_before) :]
            )
        else:
            assert file_environment.operations == operations_before
        error_context = str(file_environment.requests[2:]).lower()
        assert (
            "write access is restricted" in error_context
            or "write access is disabled" in error_context
            or "not allowed" in error_context
        )
        for request in file_environment.requests[2:]:
            assert set(expected).issubset(request["tools"])
            # Restored framing retains its original marker; it grants no authority.
            original_role = "admin" if policy == "admin_removed" else "member"
            assert f"sender=alice;role={original_role}" in str(request["contexts"])
    else:
        assert file_environment.requests == requests_before
        assert file_environment.operations == operations_before
        assert file_environment.target.read_bytes() == content_before
        assert file_environment.target.stat().st_mtime_ns == modified_before
        if policy == "unknown":
            assert after["status"] == "interrupted", recovered
            assert after["recovery"]["available"] is False
            assert (
                recovered["recovery_error"]["message"]
                == "Tool execution has started; its result requires confirmation."
            )
            assert content_before != b"before\n"
            assert after["tool_calls"][0]["status"] == "unknown"
            assert after["tool_calls"][0]["args"] == before["tool_calls"][0]["args"]
        else:
            assert after["status"] == "blocked", recovered
            assert (
                after["state"]["reason"] == "Frozen resources are no longer available"
            )
            assert any(
                issue["code"] == "UNAVAILABLE" and expected[0] in issue["message"]
                for issue in after["state"]["issues"]
            )


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("legacy", ["astrbot_file_write_tool", "astrbot_file_edit_tool"]),
        ("patch", ["astrbot_apply_patch"]),
    ],
)
@pytest.mark.parametrize(
    "policy", ["admin_tightened", "admin_removed", "readonly", "roots_revoked"]
)
def test_recovery_obeys_current_local_file_authorization(
    tmp_path, file_environment, mode, expected, policy
):
    """Current local permissions constrain the original saved file plan.

    Args:
        tmp_path: Durable data shared across real process termination.
        file_environment: Independent model and filesystem observations.
        mode: Original frozen file tool behavior.
        expected: Original selected file capability group.
        policy: Current authorization change that must constrain the tool.
    """
    test_frozen_file_plan_survives_process_loss_with_current_authorization(
        tmp_path, file_environment, "local", mode, expected, policy
    )


@pytest.mark.parametrize(
    "runtime,policy",
    [
        ("sandbox", "readonly"),
        ("local", "blacklist_added"),
        ("sandbox", "blacklist_added"),
    ],
)
def test_patch_recovery_obeys_current_write_and_delete_authorization(
    tmp_path, file_environment, runtime, policy
):
    """Sandbox write policy and patch deletion restrictions remain current.

    Args:
        tmp_path: Durable data shared across real process termination.
        file_environment: Independent model and sandbox filesystem.
        runtime: Actual local or sandbox dispatch environment.
        policy: Current write or delete authorization restriction.
    """
    test_frozen_file_plan_survives_process_loss_with_current_authorization(
        tmp_path, file_environment, runtime, "patch", ["astrbot_apply_patch"], policy
    )


@pytest.mark.parametrize("runtime", ["local", "sandbox"])
@pytest.mark.parametrize("mode", ["legacy", "patch"])
def test_empty_role_allowlist_blocks_model_requested_file_effects(
    tmp_path, file_environment, runtime, mode
):
    """A valid mode cannot grant tools excluded by an explicit role allowlist.

    Args:
        tmp_path: Isolated persisted state.
        file_environment: Parent-owned model requests and external effects.
        runtime: Supported computer environment.
        mode: Profile file behavior mode.
    """
    case = {
        "entry": "workflow",
        "tools": [],
        "exercise": True,
        "settings": {
            "computer_use_runtime": runtime,
            "file_tool_mode": mode,
            "computer_use_require_admin": False,
        },
    }
    (tmp_path / "case.json").write_text(json.dumps(case), encoding="utf-8")
    asyncio.run(
        run_worker(tmp_path, file_environment.endpoint, "start", "file_mode_allowlist")
    )
    observed = json.loads((tmp_path / "observed.json").read_text(encoding="utf-8"))
    assert observed["preview"]["ready"] is True
    assert observed["preview"]["effective"]["tools"] == []
    assert observed["run"]["status"] == "completed"
    for request in file_environment.requests:
        assert not {
            "astrbot_file_write_tool",
            "astrbot_file_edit_tool",
            "astrbot_apply_patch",
            "external_evidence",
        }.intersection(request["tools"])
    assert "not found" in str(file_environment.requests).lower()
    assert file_environment.target.read_text(encoding="utf-8") == "before\n"
    assert file_environment.operations == []


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("legacy", ["astrbot_file_write_tool", "astrbot_file_edit_tool"]),
        ("patch", ["astrbot_apply_patch"]),
    ],
)
def test_current_admin_revocation_constrains_an_inflight_member(
    tmp_path, file_environment, mode, expected
):
    """A live profile edit constrains an already assigned admin event.

    Args:
        tmp_path: Isolated persisted state and file.
        file_environment: Independent model and file effect observations.
        mode: Original file behavior mode.
        expected: Original selected capability group.
    """
    case = {
        "entry": "workflow",
        "tools": expected,
        "exercise": True,
        "admins": ["alice"],
        "revoke_admin_during_model": True,
        "settings": {
            "computer_use_runtime": "local",
            "file_tool_mode": mode,
            "computer_use_require_admin": True,
        },
    }
    original = file_environment.target.read_bytes()
    original_mtime = file_environment.target.stat().st_mtime_ns
    (tmp_path / "case.json").write_text(json.dumps(case), encoding="utf-8")
    asyncio.run(
        run_worker(tmp_path, file_environment.endpoint, "start", "file_mode_live_admin")
    )
    observed = json.loads((tmp_path / "observed.json").read_text(encoding="utf-8"))
    assert observed["run"]["status"] == "completed", observed["run"]
    assert file_environment.target.read_bytes() == original
    assert file_environment.target.stat().st_mtime_ns == original_mtime
    assert "write access is restricted" in str(file_environment.requests).lower()
    assert "sender=alice;role=admin" in str(file_environment.requests)
    assert observed["run"]["snapshot"]["members"]["writer"]["profile"]["admins_id"] == [
        "alice"
    ]
