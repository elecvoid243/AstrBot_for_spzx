"""Accepted results survive abrupt process loss without replaying Agent work."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "safe,file_result", [(True, False), (False, False), (True, True), (False, True)]
)
def test_saved_submission_rebuilds_without_dispatch_after_process_termination(
    tmp_path, safe, file_result, graph=False
):
    """Exercise the real pipeline, on-disk SQLite, termination and fresh services.

    Args:
        tmp_path: Data directory shared only by the two isolated processes.
        safe: Whether the real pipeline reached its saved completion boundary.
        file_result: Whether the actual task stages and submits a file artifact.
        graph: Whether the task belongs to a persisted workflow graph.
    """
    first_script = """
import asyncio
import sys
from pathlib import Path
from pytest import MonkeyPatch
from tests.agent_teams.test_agent_team_main_pipeline import (
    test_main_pipeline_applies_role_capabilities_and_hooks,
)
asyncio.run(test_main_pipeline_applies_role_capabilities_and_hooks(
    Path(sys.argv[1]), MonkeyPatch(), sys.argv[2],
))
"""
    root = Path(__file__).resolve().parents[2]
    with (tmp_path / "first-process.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                first_script,
                str(tmp_path),
                ("v2_graph_crash_role" if safe else "v2_graph_unsafe_role")
                if graph
                else ("v2_file_crash_role" if safe else "v2_file_unsafe_role")
                if file_result
                else ("v2_crash_role" if safe else "v2_unsafe_role"),
            ],
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        try:
            marker = tmp_path / "crash-ready.json"
            deadline = time.monotonic() + 30
            while not marker.exists():
                assert process.poll() is None, (
                    tmp_path / "first-process.log"
                ).read_text(encoding="utf-8")
                assert time.monotonic() < deadline, (
                    "Pipeline did not save the safe point"
                )
                time.sleep(0.05)
            identities = json.loads(marker.read_text(encoding="utf-8"))
            model_calls = (tmp_path / "model-calls.log").read_text(encoding="utf-8") if graph else None
            # TerminateProcess on Windows and SIGTERM on POSIX: no async cleanup.
            process.terminate()
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    second_script = """
import asyncio
import json
import sys
from pathlib import Path
from sqlalchemy import text
from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase

async def main():
    folder = Path(sys.argv[1])
    identities = json.loads((folder / "crash-ready.json").read_text(encoding="utf-8"))
    db = SQLiteDatabase(identities["db_path"])
    def forbid_dispatch(username):
        raise AssertionError("Recovery must not dispatch a model or tool")
    service = WorkflowService(db, RoleAssetService(db), ports_factory=forbid_dispatch)
    run_id = identities["run_id"]
    root = await service.get_run("alice", run_id)
    if root["snapshot"]["engine"] == "graph_v2":
        run_id = root["tasks"][0]["run_id"]
    before = await service.get_run("alice", run_id)
    if before["state"]["safe_to_finish"]:
        async with db.get_db() as session:
            await session.execute(text("DROP TRIGGER reject_completion"))
            await session.commit()
    await service.boot_sweep()
    finished = await service.get_run("alice", run_id)
    if root["snapshot"]["engine"] == "graph_v2":
        await service.advance_run("alice", root["run_id"])
        await service.advance_run("alice", root["run_id"])
        try:
            await service.submit_result("alice", run_id, before["state"]["attempt_id"], {"text": "Late conflicting output"})
        except RoleAssetError as exc:
            assert exc.status == 409
        else:
            raise AssertionError("An abandoned executor cannot replace the current output")
        assert await service.get_run("alice", run_id) == finished
    if before["state"]["safe_to_finish"]:
        repeated = await service.finish_submission("alice", run_id)
        result = await service.get_result("alice", run_id)
        ref = finished["result_ref"]
        artifact = await service.read_artifact("alice", **ref)
        files = []
        for file_ref in result.get("files", []):
            file = await service.get_file("alice", **ref, **file_ref)
            files.append({"filename": file["filename"], "content": file["content"].decode()})
    else:
        try:
            await service.finish_submission("alice", run_id)
        except RoleAssetError as exc:
            assert exc.status == 409
        else:
            raise AssertionError("Unsafe pending result must not complete")
        repeated = None
        result = None
        artifact = None
        files = []
    (folder / "restarted.json").write_text(json.dumps({
        "before": before,
        "finished": finished,
        "repeated": repeated,
        "result": result,
        "artifact": artifact,
        "files": files,
        "root": await service.get_run("alice", identities["run_id"]),
    }), encoding="utf-8")
    await db.engine.dispose()
asyncio.run(main())
"""
    second = subprocess.run(
        [sys.executable, "-c", second_script, str(tmp_path)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert second.returncode == 0, second.stdout + second.stderr
    evidence = json.loads((tmp_path / "restarted.json").read_text(encoding="utf-8"))
    before = evidence["before"]
    assert before["status"] == "running"
    assert before["state"]["safe_to_finish"] is safe
    assert before["pending_result"] is not None
    assert before["result"] is None
    assert evidence["finished"]["status"] == ("completed" if safe else "interrupted")
    assert evidence["finished"]["run_id"] == (evidence["root"]["tasks"][0]["run_id"] if graph else identities["run_id"])
    if graph:
        assert evidence["root"]["status"] == ("completed" if safe else "blocked")
        assert (tmp_path / "model-calls.log").read_text(encoding="utf-8") == model_calls
    assert evidence["finished"]["snapshot"] == before["snapshot"]
    assert evidence["finished"]["state"] == before["state"]
    if safe:
        assert evidence["repeated"] == evidence["finished"]
        assert evidence["result"] == before["pending_result"]
        assert evidence["artifact"]["content"] == evidence["result"]
        assert len(evidence["finished"]["artifacts"]) == 1
        assert (
            len(
                [
                    event
                    for event in evidence["finished"]["events"]
                    if event["kind"] == "task.completed"
                ]
            )
            == 1
        )
        if file_result:
            assert evidence["files"] == [
                {
                    "filename": "evidence.txt",
                    "content": "Complete restart evidence. " * 1000,
                }
            ]
            assert not (tmp_path / "evidence.txt").exists()
        else:
            assert json.loads(evidence["result"]["text"])["correction_context"] is True
    else:
        assert evidence["finished"]["result"] is None
        assert evidence["finished"]["pending_result"] == before["pending_result"]


@pytest.mark.parametrize("safe", [True, False])
def test_graph_ownership_and_submission_survive_terminated_pipeline_process(tmp_path, safe):
    test_saved_submission_rebuilds_without_dispatch_after_process_termination(tmp_path, safe, False, graph=True)
