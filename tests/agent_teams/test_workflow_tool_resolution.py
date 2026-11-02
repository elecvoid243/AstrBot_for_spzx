"""Unknown external effects are resolved through durable public run actions."""

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
        "manual_retry",
        "manual_retry_late",
        "manual_retry_key",
        "manual_retry_partial",
        "manual_batch",
        "manual_retry_commit",
        "manual_graph",
        "manual_save_failure",
        "manual_result",
        "manual_paused_result",
        "manual_paused_failure",
        "manual_retry_paused",
        "manual_paused_race",
        "manual_paused_commit",
        "reconcile_paused",
        "manual_failure",
        "manual_http",
        "reconcile_succeeded",
        "reconcile_again",
        "reconcile_failed",
        "reconcile_partial",
        "reconcile_partial_failed",
        "reconcile_partial_conflict",
        "reconcile_unknown",
        "reconcile_error",
        "reconcile_save_failure",
        "idempotent_retry",
        "safe_retry",
        "idempotent_partial_twokill",
        "safe_partial_twokill",
        "idempotent_partial_raw",
        "idempotent_partial_conflict",
        "safe_partial_conflict",
        "idempotent_partial_incomplete",
        "safe_partial_incomplete",
        "reconcile_partial_not_executed_twokill",
        "reconcile_partial_not_executed_conflict",
        "safe_partial_short",
    ],
)
def test_unknown_tool_effect_resolution_survives_process_termination(
    tmp_path, scenario, preview=False
):
    """Observe effects outside a killed real-pipeline worker and resume publicly.

    Args:
        tmp_path: Isolated persistent database and worker communication files.
        scenario: Declared capability or explicit human decision to exercise.
        preview: Keep the actual dashboard and external service available for UI QA.
    """
    live_partial = "partial" in scenario and scenario.startswith(("idempotent_", "safe_", "reconcile_partial_not_executed"))
    effects = []
    requests = []
    queries = []
    deliveries = []
    release = threading.Event()

    class ExternalService(BaseHTTPRequestHandler):
        """External boundary whose effects outlive the Agent process."""

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            """Record external effects and serve the original saved model plan.

            Returns:
                None after writing the external response.
            """
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                deliveries.append(payload)
                # A not-executed query observes real absence of final effects.
                progress_only = scenario.startswith("reconcile_partial_not_executed") and (len(deliveries) == 1 or scenario.endswith("conflict") and len(deliveries) == 2)
                if not progress_only and (not payload.get("idempotency_key") or not any(item.get("idempotency_key") == payload["idempotency_key"] for item in effects)):
                    effects.append(payload)
                response = {"text": "Verified external evidence."}
                if live_partial:
                    response["sequence"] = ["Partial verified evidence.", "Complete verified evidence."]
                    if scenario.endswith("short"):
                        response["sequence"].insert(1, "Second partial evidence.")
                    if progress_only and len(deliveries) == 1:
                        response["sequence"] = response["sequence"][:1]
                    if len(deliveries) == 2 and scenario.endswith("conflict"):
                        response["sequence"][0] = "Conflicting partial evidence."
                    elif len(deliveries) == 2 and scenario.endswith("incomplete"):
                        response["sequence"] = []
                    elif len(deliveries) == 2 and scenario.endswith("short"):
                        response["sequence"] = response["sequence"][:1]
                if payload.get("part"):
                    response["text"] = "Partial verified evidence." if payload["part"] == "initial" else "Complete verified evidence."
                if scenario == "manual_retry_late" and len(deliveries) == 2:
                    assert release.wait(timeout=15)
            elif self.path == "/release":
                release.set()
                response = {}
            elif self.path == "/query":
                queries.append(payload)
                response = {"status": "succeeded", "evidence": "External ledger confirms the original operation.", "results": [{"content": [{"type": "text", "text": "Verified external evidence."}]}]}
                if scenario in {"reconcile_unknown", "reconcile_error"} or scenario == "reconcile_again" and len(queries) == 1:
                    response = {"status": "unknown", "evidence": "Remote operation is still unresolved.", "results": []}
                elif scenario == "reconcile_failed":
                    response = {"status": "failed", "evidence": "Remote ledger proves rejection.", "results": [{"isError": True, "content": [{"type": "text", "text": "External operation rejected: verified receipt."}]}]}
                elif scenario.startswith("reconcile_partial_not_executed"):
                    assert not any(item.get("identity") == payload["identity"] for item in effects)
                    response = {"status": "not_executed", "evidence": "External ledger confirms no final effect for this identity.", "results": []}
                elif scenario.startswith("reconcile_partial"):
                    failed = scenario == "reconcile_partial_failed"
                    response = {"status": "failed" if failed else "succeeded", "evidence": "Remote ledger contains the complete original output sequence.", "results": [
                        {"content": [{"type": "text", "text": "Conflicting partial evidence." if scenario.endswith("conflict") else "Partial verified evidence."}]},
                        {"isError": failed, "content": [{"type": "text", "text": "Verified terminal failure." if failed else "Complete verified evidence."}]},
                    ]}
            else:
                requests.append(payload)
                context = json.dumps(payload["contexts"])
                special = "independent" if "Independent task:" in context else "dependent" if "Dependent task:" in context else None
                if special:
                    response = {"role": "assistant", "completion_text": "Submitted."} if special + "-submit" in context else {"role": "tool", "id": special + "-submission", "tools_call_name": ["submit_task_result"], "tools_call_args": [{"text": special + " report"}], "tools_call_ids": [special + "-submit"]}
                elif "submit-original" in context:
                    response = {"role": "assistant", "completion_text": "Submitted."}
                elif "evidence-original" in context:
                    response = {"role": "tool", "id": "submission-original", "tools_call_name": ["submit_task_result"], "tools_call_args": [{"text": "Original verified report."}], "tools_call_ids": ["submit-original"]}
                else:
                    response = {"role": "tool", "id": "plan-original", "tools_call_name": ["external_evidence"], "tools_call_args": [{"value": "original-plan"}], "tools_call_ids": ["evidence-original"], "reasoning_content": "Original provider thinking.", "reasoning_signature": "original-signature"}
                    if scenario == "manual_batch":
                        response["tools_call_name"] += ["external_evidence", "external_evidence"]
                        response["tools_call_args"] += [{"value": "unknown-effect"}, {"value": "unstarted"}]
                        response["tools_call_ids"] += ["unknown-original", "unstarted-original"]
            encoded = json.dumps(response).encode()
            self.send_response(503 if self.path == "/query" and scenario == "reconcile_error" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExternalService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "tests.agent_teams.checkpoint_worker", str(tmp_path), f"http://127.0.0.1:{server.server_port}"]
    environment = {**os.environ, "PYTHONUTF8": "1", "ASTRBOT_ROOT": str(tmp_path / "runtime")}
    log = (tmp_path / "worker.log").open("w", encoding="utf-8")
    process = subprocess.Popen([*command, "start", scenario], cwd=root, stdout=log, stderr=subprocess.STDOUT, env=environment)
    try:
        deadline = time.monotonic() + 40
        while not (tmp_path / "kill-ready.json").exists() and time.monotonic() < deadline:
            assert process.poll() is None, (tmp_path / "worker.log").read_text(encoding="utf-8")
            time.sleep(0.02)
        assert (tmp_path / "kill-ready.json").exists()
        process.kill()
        process.wait(timeout=10)
        assert len(effects) == (0 if scenario.startswith("reconcile_partial_not_executed") else 2 if scenario == "manual_batch" else 1)
        if preview:
            process = subprocess.Popen([*command, "serve", scenario], cwd=root, stdout=log, stderr=subprocess.STDOUT, env=environment)
            print(f"PREVIEW_FOLDER={tmp_path}", flush=True)
            while True:
                assert process.poll() is None, (tmp_path / "worker.log").read_text(encoding="utf-8")
                time.sleep(1)
        if scenario in {"manual_retry_commit", "manual_paused_commit"}:
            decision_process = subprocess.Popen([*command, "recover_decision", scenario], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
            try:
                deadline = time.monotonic() + 40
                while not (tmp_path / "decision-ready.json").exists() and time.monotonic() < deadline:
                    assert decision_process.poll() is None
                    time.sleep(0.02)
                assert (tmp_path / "decision-ready.json").exists()
            finally:
                decision_process.kill()
                decision_process.wait(timeout=10)
            assert len(effects) == 1
        if scenario.endswith("twokill"):
            interim = subprocess.Popen([*command, "recover_first", scenario], cwd=root, stdout=log, stderr=subprocess.STDOUT, env=environment)
            try:
                deadline = time.monotonic() + 45
                while not (tmp_path / "second-ready.json").exists() and time.monotonic() < deadline:
                    assert interim.poll() is None, (tmp_path / "worker.log").read_text(encoding="utf-8")
                    time.sleep(0.02)
                assert (tmp_path / "second-ready.json").exists()
            finally:
                interim.kill()
                interim.wait(timeout=10)
        if scenario == "manual_paused_race":
            resolver = subprocess.Popen(
                [*command, "recover", scenario],
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
            )
            try:
                deadline = time.monotonic() + 45
                while not (tmp_path / "decision-ready.json").exists():
                    assert resolver.poll() is None, (tmp_path / "worker.log").read_text(
                        encoding="utf-8"
                    )
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
                paused = subprocess.run(
                    [*command, "pause_decision", scenario],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                    env=environment,
                )
                assert paused.returncode == 0, paused.stdout + paused.stderr
                assert len(effects) == len(requests) == 1
                (tmp_path / "release-decision").write_text("", encoding="utf-8")
                assert resolver.wait(timeout=45) == 0, (
                    tmp_path / "worker.log"
                ).read_text(encoding="utf-8")
            finally:
                if resolver.poll() is None:
                    resolver.kill()
                    resolver.wait(timeout=10)
        else:
            recovered = subprocess.run(
                [*command, "recover", scenario],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=45,
                env=environment,
            )
            assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        if live_partial and scenario.endswith(("conflict", "incomplete", "short")):
            assert evidence["after"]["status"] == "blocked"
            blocked = evidence["after"]["tool_calls"][0]
            assert blocked["status"] == "unknown"
            assert blocked["raw_results"] == evidence["before"]["tool_calls"][0]["raw_results"]
            conflict = blocked["recovery"]["retries"][0]["conflict"]
            assert conflict["index"] == (1 if scenario.endswith("short") else 0)
            if scenario.endswith("conflict"):
                assert conflict["result"]["content"][0]["text"] == "Conflicting partial evidence."
            elif scenario.endswith("incomplete"):
                assert "saved partial output" in conflict["reason"]
                assert conflict["result"] == "None"
            else:
                assert "ended before" in conflict["reason"]
            assert len(requests) == 1 and len(deliveries) == 2
            retried = subprocess.run([*command, "recover_verified", scenario], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=45, env=environment)
            assert retried.returncode == 0, retried.stdout + retried.stderr
            evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
            assert evidence["before"]["tool_calls"][0]["recovery"]["retries"][0]["conflict"] == conflict
        if scenario in {"manual_save_failure", "reconcile_save_failure"}:
            assert len(effects) == len(deliveries) == len(requests) == 1
            assert evidence["after"]["status"] in {"interrupted", "failed"}
            assert len(evidence["after"]["tool_calls"]) == 1
            assert evidence["after"]["tool_calls"][0]["status"] == "unknown"
            assert not evidence["after"]["tool_calls"][0]["recovery"].get("decisions")
            assert not evidence["after"]["tool_calls"][0]["recovery"].get("queries")
            repaired = subprocess.run([*command, "recover_storage", scenario], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=45, env=environment)
            assert repaired.returncode == 0, repaired.stdout + repaired.stderr
            evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
        if scenario in {"reconcile_unknown", "reconcile_error", "reconcile_partial_conflict"}:
            assert evidence["after"]["status"] == "blocked"
            call = evidence["after"]["tool_calls"][0]
            assert call["status"] == "unknown"
            assert len(effects) == len(deliveries) == len(queries) == len(requests) == 1
            assert call["recovery"]["queries"][0]["status"] == ("succeeded" if scenario == "reconcile_partial_conflict" else "unknown")
            assert "reconcile" in call["available_actions"]
            if scenario == "reconcile_partial_conflict":
                assert call["raw_results"] == evidence["before"]["tool_calls"][0]["raw_results"]
                assert call["recovery"]["queries"][0]["accepted"] is False
            return
        assert evidence["after"]["status"] == "completed"
        assert evidence["after"]["snapshot"] == evidence["before"]["snapshot"]
        assert evidence["after"]["state"]["attempt_id"] == evidence["before"]["state"]["attempt_id"]
        if "paused" in scenario:
            waiting = evidence["resolution_wait"]
            assert waiting["control"]["status"] == "paused"
            assert waiting["status"] == "interrupted"
            assert waiting["tool_calls"][0]["status"] == (
                "unknown"
                if scenario in {"manual_retry_paused", "reconcile_paused"}
                else "result_saved"
            )
            assert len(waiting["tool_calls"][0]["recovery"]["decisions"]) == 1
            assert (
                len(
                    [
                        event
                        for event in waiting["events"]
                        if event["kind"] == "tool.resolved"
                    ]
                )
                == 1
            )
        calls = [call for call in evidence["after"]["tool_calls"] if call["name"] == "external_evidence"]
        if live_partial:
            call = calls[0]
            expected = ["Partial verified evidence.", "Complete verified evidence."]
            if scenario.endswith("short"):
                expected.insert(1, "Second partial evidence.")
            assert [raw["content"][0]["text"] for raw in call["raw_results"]] == expected
            assert [block["content"] for block in call["recovery"]["retries"][0]["protocol_before"]] == ([] if scenario.endswith("raw") else expected[:-1])
            # A third authorized retry retains normal repeated-call guidance.
            assert [message["content"].split("\n\n[SYSTEM NOTICE]", 1)[0] for message in requests[1]["contexts"] if message.get("tool_call_id") == "evidence-original"] == ["\n\n".join(expected)]
            records = [record["data"]["raw_result"]["content"][0]["text"] for record in evidence["after"]["process"] if record["kind"] == "tool_result" and record["data"]["name"] == "external_evidence" and record["data"].get("raw_result")]
            assert records == expected
            assert len(calls) == 1 and call["status"] == "applied"
            assert call["raw_results"][0] == evidence["before"]["tool_calls"][0]["raw_results"][0]
            delivery_count = 3 if scenario.endswith(("conflict", "incomplete", "short")) else 2
            assert len(deliveries) == delivery_count
            assert len(effects) == (1 if scenario.startswith(("idempotent_", "reconcile_partial_not_executed")) else delivery_count)
            assert all(item["identity"] == call["record_id"] for item in deliveries)
            if scenario.startswith("idempotent_"):
                assert all(item["idempotency_key"] == call["record_id"] for item in deliveries)
            if scenario.startswith("reconcile_"):
                assert len(queries) == delivery_count - 1
                assert all(item["identity"] == call["record_id"] for item in queries)
        elif scenario.startswith("manual_retry") or scenario == "manual_save_failure":
            assert len(calls) == 2
            assert calls[0]["status"] == "unknown"
            assert calls[0]["raw_results"] == ([] if scenario != "manual_retry_partial" else evidence["before"]["tool_calls"][0]["raw_results"])
            assert calls[1]["status"] == "applied"
            assert calls[1]["recovery"]["replaces_record_id"] == calls[0]["record_id"]
            assert len(calls[0]["recovery"]["decisions"]) == 1
            if scenario == "manual_retry_partial":
                assert calls[0]["raw_results"][0]["content"][0]["text"] == "Partial verified evidence."
                assert effects == [{"value": "original-plan", "part": "initial"}, {"value": "original-plan", "part": "initial"}, {"value": "original-plan", "part": "final"}]
            elif scenario == "manual_retry_key":
                assert len(effects) == 2
                assert deliveries[0]["idempotency_key"] == calls[0]["record_id"]
                assert deliveries[1]["idempotency_key"] == calls[1]["record_id"]
                assert deliveries[0]["idempotency_key"] != deliveries[1]["idempotency_key"]
            else:
                assert effects == [{"value": "original-plan"}, {"value": "original-plan"}]
        elif scenario.startswith("reconcile_partial"):
            assert len(calls) == 1
            assert calls[0]["status"] == "applied"
            raw_text = [raw["content"][0]["text"] for raw in calls[0]["raw_results"]]
            expected = ["Partial verified evidence.", "Verified terminal failure." if scenario.endswith("failed") else "Complete verified evidence."]
            assert raw_text == expected
            applied = [message["content"] for message in requests[1]["contexts"] if message.get("tool_call_id") == "evidence-original"]
            assert applied == ["\n\n".join(expected) + ("\n\n[Tool result status: error]" if scenario.endswith("failed") else "")]
            assert calls[0]["raw_results"][0] == evidence["before"]["tool_calls"][0]["raw_results"][0]
            assert len(effects) == len(deliveries) == len(queries) == 1
        elif scenario.startswith("reconcile_"):
            assert len(calls) == 1
            assert calls[0]["status"] == "applied"
            assert calls[0]["raw_results"][0]["content"][0]["text"] == ("External operation rejected: verified receipt." if scenario == "reconcile_failed" else "Verified external evidence.")
            assert calls[0]["raw_results"][0]["isError"] == (scenario == "reconcile_failed")
            assert queries == [{"identity": calls[0]["record_id"], "arguments": {"value": "original-plan"}}] * (2 if scenario in {"reconcile_save_failure", "reconcile_again"} else 1)
            assert effects == [{"value": "original-plan", "identity": calls[0]["record_id"]}]
            if scenario == "reconcile_again":
                assert [item["status"] for item in calls[0]["recovery"]["queries"]] == ["unknown", "succeeded"]
                assert len(calls[0]["recovery"]["decisions"]) == 1
        elif scenario == "manual_batch":
            assert len(calls) == 3
            assert all(call["status"] == "applied" for call in calls)
            assert effects == [{"value": "original-plan"}, {"value": "unknown-effect"}, {"value": "unstarted"}]
        else:
            assert len(calls) == 1
            assert calls[0]["status"] == "applied"
            if scenario.startswith("manual_"):
                assert len(effects) == len(deliveries) == 1
                assert len(calls[0]["recovery"]["decisions"]) == 1
                assert calls[0]["raw_results"][0]["isError"] == scenario.endswith(
                    "_failure"
                )
            else:
                assert len(deliveries) == 2
                assert len(effects) == (1 if scenario == "idempotent_retry" else 2)
                assert deliveries[0]["identity"] == deliveries[1]["identity"] == calls[0]["record_id"]
                if scenario == "idempotent_retry":
                    assert deliveries[0]["idempotency_key"] == deliveries[1]["idempotency_key"] == calls[0]["recovery"]["idempotency_key"]
        if scenario == "manual_graph":
            independent = next(task for task in evidence["independent_before_resolution"]["tasks"] if task["node_id"] == "independent")
            assert independent["status"] == "completed"
            unresolved = next(task for task in evidence["independent_before_resolution"]["tasks"] if task["node_id"] == "write")
            assert unresolved["status"] == "interrupted"
            assert evidence["parent"]["status"] == "completed"
            assert len(requests) == 7
        else:
            assert len(requests) == 3
        assert evidence["late_errors"] == [409, 409, 409, 409]
        if scenario == "manual_retry_late":
            assert evidence["during_resolution_errors"] == [409, 409, 409, 409]
        if scenario == "manual_retry_commit":
            repeated = subprocess.run([*command, "recover", scenario], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=45, env=environment)
            assert repeated.returncode == 0, repeated.stdout + repeated.stderr
            assert len(effects) == 2
            assert len(requests) == 3
    finally:
        release.set()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        log.close()
