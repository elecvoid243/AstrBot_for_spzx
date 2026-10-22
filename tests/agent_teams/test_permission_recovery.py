"""Plugin recovery through current authorization and durable public services."""

import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import pytest


@contextmanager
def checkpoint_process(command, folder, action, capability):
    """Own the actual Python worker and retain its OS exit before returning.

    Args:
        command: Interpreter, worker module and external endpoint arguments.
        folder: Isolated identity, logs and durable state directory.
        action: Start an interrupted attempt or recover it in a new process.
        capability: Public recovery scenario to exercise.

    Yields:
        Launcher and mutable exit evidence, finalized before the context returns.
    """
    record = {"action": action, "started_at_ns": time.perf_counter_ns(), "cleanup": []}
    runtime = None
    handle = None
    children = []
    with (folder / f"{action}.log").open("w", encoding="utf-8") as log:
        launcher = subprocess.Popen(
            [*command, action, f"guarded_{capability}"],
            cwd=Path(__file__).resolve().parents[2],
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        record["launcher_pid"] = launcher.pid
        owner = None
        try:
            owner = psutil.Process(launcher.pid)
            deadline = time.monotonic() + 40
            identity_path = folder / f"worker-{action}.json"
            while not identity_path.exists():
                for child in owner.children(recursive=True):
                    if child not in children:
                        children.append(child)
                assert launcher.poll() is None, (folder / f"{action}.log").read_text(
                    encoding="utf-8"
                )
                assert time.monotonic() < deadline, "Worker identity was not published"
                time.sleep(0.02)
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            runtime = psutil.Process(identity["pid"])
            assert runtime.create_time() == identity["creation_time"]
            assert runtime.pid == launcher.pid or runtime.ppid() == launcher.pid
            assert str(folder) in runtime.cmdline()
            if runtime.pid != launcher.pid and runtime not in children:
                children.append(runtime)
            record["worker"] = identity
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes

                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.argtypes = [
                    wintypes.DWORD,
                    wintypes.BOOL,
                    wintypes.DWORD,
                ]
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel.WaitForSingleObject.restype = wintypes.DWORD
                kernel.GetExitCodeProcess.argtypes = [
                    wintypes.HANDLE,
                    ctypes.POINTER(wintypes.DWORD),
                ]
                kernel.GetExitCodeProcess.restype = wintypes.BOOL
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel.CloseHandle.restype = wintypes.BOOL
                # Hold the actual worker object before killing either process.
                handle = kernel.OpenProcess(0x00100000 | 0x1000, False, runtime.pid)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                record["wait_method"] = "WaitForSingleObject/GetExitCodeProcess"
            else:
                assert runtime.pid == launcher.pid
                record["wait_method"] = "Popen.wait (actual worker)"
            yield launcher, record
        finally:
            try:
                if runtime is not None:
                    active = (
                        kernel.WaitForSingleObject(handle, 0) == 258
                        if handle
                        else launcher.poll() is None
                    )
                    if active:
                        record["worker_kill_requested"] = True
                        try:
                            runtime.kill()
                        except psutil.NoSuchProcess:
                            pass
                    if handle:
                        record["worker_wait_result"] = kernel.WaitForSingleObject(
                            handle, 10000
                        )
                        assert record["worker_wait_result"] == 0, record
                        exit_code = wintypes.DWORD()
                        if not kernel.GetExitCodeProcess(
                            handle, ctypes.byref(exit_code)
                        ):
                            raise ctypes.WinError(ctypes.get_last_error())
                        record["worker_exit"] = exit_code.value
                        assert exit_code.value != 259, record
                    elif runtime.pid == launcher.pid:
                        record["worker_exit"] = launcher.wait(timeout=10)
                    else:
                        record["worker_wait_error"] = "Actual worker handle unavailable"
                    record["worker_wait_finished_at_ns"] = time.perf_counter_ns()
            finally:
                if handle:
                    kernel.CloseHandle(handle)
                # On startup failures the worker may not have published identity yet.
                if runtime is None and owner is not None:
                    try:
                        for child in owner.children(recursive=True):
                            if child not in children:
                                children.append(child)
                    except psutil.NoSuchProcess:
                        pass
                for child in children:
                    if runtime == child and "worker_exit" in record:
                        continue
                    try:
                        if child.is_running():
                            child.kill()
                        record["cleanup"].append(
                            {"pid": child.pid, "exit": child.wait(timeout=10)}
                        )
                    except psutil.NoSuchProcess:
                        record["cleanup"].append(
                            {"pid": child.pid, "already_exited": True}
                        )
                    except psutil.Error as error:
                        record["cleanup"].append(
                            {"pid": child.pid, "error": str(error)}
                        )
                try:
                    record["launcher_exit"] = launcher.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    launcher.kill()
                    record["launcher_cleanup_exit"] = launcher.wait(timeout=10)
                    raise
                finally:
                    record["launcher_wait_finished_at_ns"] = time.perf_counter_ns()
                    (folder / f"process-{action}.json").write_text(
                        json.dumps(record), encoding="utf-8"
                    )
                assert not any("error" in item for item in record["cleanup"]), record


@pytest.mark.parametrize("action", ["start", "recover"])
def test_checkpoint_process_reaps_actual_worker_when_parent_fails(tmp_path, action):
    """A failed assertion or recovery wait cannot leave its Python worker alive.

    Args:
        tmp_path: Isolated process identities and partial runtime state.
        action: Initial or recovery process to interrupt during startup.
    """
    command = [
        sys.executable,
        "-m",
        "tests.agent_teams.checkpoint_worker",
        str(tmp_path),
        "http://127.0.0.1:1",
    ]
    with pytest.raises(RuntimeError, match="Injected parent failure"):
        with checkpoint_process(command, tmp_path, action, "query"):
            raise RuntimeError("Injected parent failure")
    stopped = json.loads(
        (tmp_path / f"process-{action}.json").read_text(encoding="utf-8")
    )
    assert stopped["worker_exit"] != 0
    assert stopped["launcher_exit"] != 0
    if os.name == "nt":
        assert stopped["worker_wait_result"] == 0


@pytest.mark.parametrize(
    "capability",
    [
        "query",
        "query_denied",
        "idempotent",
        "safe",
        "idempotent_denied",
        "safe_denied",
        "unknown",
        "legacy",
        "query_changed",
        "query_missing",
    ],
)
def test_permission_wrapped_recovery_survives_process_termination(tmp_path, capability):
    """Recover the original effect through the real permission-wrapped pipeline.

    Args:
        tmp_path: Persistent database and external ledger outside the killed worker.
        capability: Explicit plugin recovery contract or conservative control.
    """
    deliveries, effects, queries, requests = [], [], [], []
    receipt = "Complete original receipt. " * 1000 + "RECEIPT_END"

    class ExternalService(BaseHTTPRequestHandler):
        """Keep external effects and authoritative receipts outside the worker."""

        def log_message(self, *args):
            """Silence HTTP logs.

            Args:
                *args: Standard HTTP logging arguments.
            """

        def do_POST(self):
            """Serve the controlled model and external effect boundaries."""
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                deliveries.append(payload)
                if not payload.get("idempotency_key") or not any(
                    effect.get("idempotency_key") == payload["idempotency_key"]
                    for effect in effects
                ):
                    effects.append(payload)
                response = {"text": receipt}
            elif self.path == "/query":
                queries.append(payload)
                response = {
                    "status": "succeeded",
                    "evidence": "The independent ledger retains the original receipt.",
                    "results": [{"content": [{"type": "text", "text": receipt}]}],
                }
            else:
                requests.append(payload)
                context = json.dumps(payload["contexts"])
                if "Permission denied" in context:
                    response = {
                        "role": "assistant",
                        "completion_text": "The current caller cannot use this tool.",
                    }
                elif "submit-original" in context:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif "evidence-original" in context:
                    response = {
                        "role": "tool",
                        "tools_call_name": ["submit_task_result"],
                        "tools_call_args": [{"text": "Original verified report."}],
                        "tools_call_ids": ["submit-original"],
                    }
                else:
                    response = {
                        "role": "tool",
                        "tools_call_name": ["external_evidence"],
                        "tools_call_args": [{"value": "original-plan"}],
                        "tools_call_ids": ["evidence-original"],
                    }
            (tmp_path / "external-ledger.json").write_text(
                json.dumps(
                    {
                        "deliveries": deliveries,
                        "effects": effects,
                        "queries": queries,
                        "requests": requests,
                    }
                ),
                encoding="utf-8",
            )
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExternalService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    command = [
        sys.executable,
        "-m",
        "tests.agent_teams.checkpoint_worker",
        str(tmp_path),
        f"http://127.0.0.1:{server.server_port}",
    ]
    try:
        with checkpoint_process(command, tmp_path, "start", capability) as (
            process,
            stopped,
        ):
            deadline = time.monotonic() + 40
            while not (tmp_path / "kill-ready.json").exists():
                assert process.poll() is None, (tmp_path / "start.log").read_text(
                    encoding="utf-8"
                )
                assert time.monotonic() < deadline, (tmp_path / "start.log").read_text(
                    encoding="utf-8"
                )
                time.sleep(0.02)
            assert len(deliveries) == len(effects) == len(requests) == 1
            assert queries == []
        assert stopped["worker_kill_requested"] is True
        assert stopped["worker_exit"] != 0
        assert stopped["launcher_exit"] != 0
        with checkpoint_process(command, tmp_path, "recover", capability) as (
            recovery,
            resumed,
        ):
            assert resumed["started_at_ns"] > stopped["worker_wait_finished_at_ns"]
            assert resumed["started_at_ns"] > stopped["launcher_wait_finished_at_ns"]
            assert recovery.wait(timeout=45) == 0, (tmp_path / "recover.log").read_text(
                encoding="utf-8"
            )
        assert resumed["worker_exit"] == resumed["launcher_exit"] == 0
        evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        before, after = evidence["before"], evidence["after"]
        original = before["tool_calls"][0]
        restored = next(
            call
            for call in after["tool_calls"]
            if call["record_id"] == original["record_id"]
        )
        assert after["run_id"] == before["run_id"]
        assert after["state"]["attempt_id"] == before["state"]["attempt_id"]
        assert after["snapshot"] == before["snapshot"]
        assert restored["call_id"] == original["call_id"] == "evidence-original"
        assert restored["invocation_id"] == original["invocation_id"]
        assert original["raw_results"] == []
        if capability in {"unknown", "legacy"}:
            assert evidence["error"]["status"] == 409
            assert after["status"] == "interrupted"
            assert original["recovery"]["declaration"] is None
            assert restored["recovery"]["declaration"] is None
            assert restored["status"] == "unknown"
            assert restored["raw_results"] == []
            assert queries == []
            assert len(deliveries) == len(effects) == len(requests) == 1
            return
        kind = capability.split("_", 1)[0]
        assert original["recovery"]["declaration"] == {
            "version": "permission-v1",
            "query": kind == "query",
            "safe_retry": kind == "safe",
            "idempotency_key_arg": "idempotency_key" if kind == "idempotent" else None,
        }
        assert (
            restored["recovery"]["declaration"] == original["recovery"]["declaration"]
        )
        if capability.endswith(("_changed", "_missing", "_denied")):
            assert after["status"] == "blocked"
            assert queries == []
            assert len(deliveries) == len(effects) == 1
            if capability == "query_denied":
                assert restored["status"] == "unknown"
                assert restored["raw_results"] == []
                assert (
                    "Permission denied"
                    in restored["recovery"]["queries"][0]["evidence"]
                )
                assert len(requests) == 1
            elif capability.endswith("_denied"):
                assert restored["status"] == "unknown"
                assert restored["raw_results"] == []
                assert (
                    "Permission denied" in restored["recovery"]["retries"][0]["error"]
                )
                assert len(requests) == 1
            else:
                assert restored["status"] == "unknown"
                assert restored["raw_results"] == []
                assert len(requests) == 1
            return
        assert after["status"] == "completed", evidence
        assert restored["status"] == "applied"
        assert restored["raw_results"][0]["content"][0]["text"] == receipt
        if kind == "query":
            assert queries == [
                {
                    "identity": original["record_id"],
                    "arguments": {"value": "original-plan"},
                }
            ]
            assert len(deliveries) == len(effects) == 1
        else:
            assert queries == []
            assert len(deliveries) == 2
            assert len(effects) == (1 if kind == "idempotent" else 2)
            assert all(
                delivery["identity"] == original["record_id"] for delivery in deliveries
            )
            if kind == "idempotent":
                assert all(
                    delivery["idempotency_key"] == original["record_id"]
                    for delivery in deliveries
                )
        assert effects[0]["identity"] == original["record_id"]
        applied = [
            message["content"]
            for message in requests[1]["contexts"]
            if message.get("tool_call_id") == "evidence-original"
        ]
        assert applied == [receipt]
        assert evidence["result"] == {"text": "Original verified report."}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
