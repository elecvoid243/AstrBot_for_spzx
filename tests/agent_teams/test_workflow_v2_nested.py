"""Fixed V2 child calls and bounded rework through the workflow service."""

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets_api import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_fixed_v2_call_preserves_native_inputs_and_pins_version(workflow_env):
    """Publish and launch a fixed child with required, defaulted and nullable ports.

    Args:
        workflow_env: Persistent workflow services backed by isolated SQLite.
    """
    service = workflow_env.service
    inputs = {
        "type": "object",
        "properties": {
            "query": {"type": "object"},
            "labels": {"type": "array", "default": ["original"]},
            "note": {"type": ["string", "null"], "default": "fallback"},
        },
        "required": ["query"],
    }
    output = {"type": "object", "properties": inputs["properties"]}
    child = {
        "name": "Native child",
        "port_contract_version": 2,
        "input_contract": inputs,
        "entry_task_ids": ["copy"],
        "result_task_id": "copy",
        "tasks": [
            {
                "id": "copy",
                "name": "Copy fields",
                "transform": "map_fields",
                "input_contract": inputs,
                "output_contract": output,
                "inputs": {key: {"input": key} for key in inputs["properties"]},
            }
        ],
    }
    saved = await service.save_asset("alice", {"definition": child})
    parent = {
        "name": "Pinned native caller",
        "port_contract_version": 2,
        "input_contract": {
            "type": "object",
            "properties": {
                "query": {"type": "object"},
                "note": {"type": ["string", "null"]},
                "private": {"type": "string"},
            },
            "required": ["query"],
        },
        "entry_task_ids": ["call"],
        "result_task_id": "call",
        "tasks": [
            {
                "id": "call",
                "name": "Call child",
                "workflow": {"asset_id": saved["asset_id"], "version": 1},
                "input_contract": inputs,
                "output_contract": output,
                "inputs": {"query": {"input": "query"}, "note": {"input": "note"}},
            }
        ],
    }
    published = await service.save_asset("alice", {"definition": parent})
    unprojectable = {
        **output,
        "minProperties": 4,
        "patternProperties": {"^internal_": {"type": "string"}},
    }
    child["tasks"][0]["output_contract"] = unprojectable
    await service.save_asset(
        "alice",
        {
            "expected_version": 1,
            "definition": {**child, "name": "New child version"},
        },
        saved["asset_id"],
    )

    run = await service.start_run(
        "alice",
        published["asset_id"],
        {
            "version": 1,
            "inputs": {"query": {"limit": 0}, "note": None, "private": "root only"},
        },
    )
    finished = await completed_run(service, run["run_id"])
    assert finished["result"] == {
        "query": {"limit": 0},
        "labels": ["original"],
        "note": None,
    }
    scope = finished["scopes"][0]["snapshot"]
    assert scope["asset_version"] == 1
    assert scope["inputs"] == finished["result"]

    parent["tasks"][0]["workflow"]["version"] = 2
    parent["tasks"][0]["output_contract"] = unprojectable
    with pytest.raises(RoleAssetError) as incompatible:
        await service.save_asset("alice", {"definition": parent})
    assert incompatible.value.fields[0]["path"] == "definition.tasks.0.output_contract"

    parent["tasks"][0]["workflow"]["version"] = 99
    with pytest.raises(RoleAssetError) as error:
        await service.save_asset("alice", {"definition": parent})
    assert error.value.fields[0]["path"] == "definition.tasks.0.workflow"


def rework_definitions() -> tuple[dict, dict]:
    """Build a V2 fixed call with a child approval and a parent rework cycle.

    Returns:
        Child and parent definitions. The parent requires the saved child identity.
    """
    payload = {"type": "object"}
    text = {"type": "string"}
    public_output = {
        "type": "object",
        "properties": {"title": text, "payload": payload},
        "required": ["title", "payload"],
    }
    child_inputs = {
        "type": "object",
        "properties": {
            "payload": payload,
            "raw": {
                "type": "string",
                "default": '{"title":"First","payload":{"round":7},"private":"hidden"}',
            },
        },
        "required": ["payload"],
    }
    received = {
        "type": "object",
        "properties": {"payload": payload},
        "required": ["payload"],
    }
    child = {
        "name": "Fixed research draft",
        "port_contract_version": 2,
        "input_contract": child_inputs,
        "entry_task_ids": ["receive"],
        "result_task_id": "parse",
        "tasks": [
            {
                "id": "receive",
                "name": "Receive payload",
                "transform": "map_fields",
                "input_contract": received,
                "output_contract": received,
                "inputs": {"payload": {"input": "payload"}},
            },
            {
                "id": "inner",
                "name": "Review child input",
                "human": {
                    "kind": "approval",
                    "question": "Approve the child input?",
                    "artifact_task_id": "receive",
                },
            },
            {
                "id": "parse",
                "name": "Prepare public result",
                "transform": "parse_json",
                "output_contract": public_output,
                "inputs": {"text": {"input": "raw"}},
            },
        ],
        "edges": [
            {"source": "receive", "target": "inner"},
            {
                "source": "inner",
                "target": "parse",
                "condition": {"field": "approved", "value": True},
            },
        ],
    }
    parent = {
        "name": "Bounded approval",
        "port_contract_version": 2,
        "input_contract": received,
        "entry_task_ids": ["call", "observe"],
        "result_task_id": "observe",
        "tasks": [
            {
                "id": "call",
                "name": "Call fixed draft",
                "input_contract": child_inputs,
                "output_contract": public_output,
                "inputs": {"payload": {"input": "payload"}},
            },
            {
                "id": "review",
                "name": "Approve public result",
                "human": {
                    "kind": "approval",
                    "question": "Approve the published result?",
                    "artifact_task_id": "call",
                },
            },
            {
                "id": "observe",
                "name": "Read only the passed round",
                "transform": "map_fields",
                "input_contract": public_output,
                "output_contract": public_output,
                "inputs": {
                    key: {"task_id": "call", "field": key}
                    for key in ("title", "payload")
                },
            },
        ],
        "edges": [{"source": "call", "target": "review"}],
        "loops": [
            {
                "id": "rework",
                "name": "Review and revise",
                "body": ["call", "review"],
                "exit_task_id": "review",
                "exit_condition": {"field": "approved", "value": True},
                "max_iterations": 2,
                "feedback": [
                    {
                        "source": "review",
                        "field": "text",
                        "target": "call",
                        "input": "raw",
                    },
                    {
                        "source": "call",
                        "field": "payload",
                        "target": "call",
                        "input": "payload",
                    },
                ],
            }
        ],
    }
    return child, parent


async def pending_review(service, run_id: str) -> tuple[dict, dict]:
    """Read the next pending approval and its enclosing run.

    Args:
        service: Public workflow service.
        run_id: Existing root run identity.

    Returns:
        Root detail and the pending approval.
    """
    import asyncio

    for _ in range(300):
        run = await service.get_run("alice", run_id)
        if todo := next(
            (todo for todo in run["todos"] if todo["status"] == "pending"), None
        ):
            return run, todo
        assert run["status"] == "running", run["state"]["reason"]
        await asyncio.sleep(0.01)
    raise AssertionError("No approval reached the root run")


async def answer_review(service, run_id, todo, feedback=None):
    """Answer the request with the exact public artifact version.

    Args:
        service: Public workflow service.
        run_id: Root run holding the approval.
        todo: Pending approval read through the root.
        feedback: Optional reason for returning the approval.
    """
    await service.answer_todo(
        "alice",
        run_id,
        todo["todo_id"],
        {
            "action": "reject" if feedback else "approve",
            "request_id": "decision-" + todo["todo_id"],
            "artifact_ref": todo["artifact_ref"],
            **({"feedback": feedback} if feedback else {}),
        },
    )


async def rework_process(folder, phase):
    """Persist the second-round child approval and recover it in a fresh process.

    Args:
        folder: Workspace for the isolated database and handoff.
        phase: Start and park the run or recover the checkpoint.
    """
    import asyncio
    import json

    from astrbot.core.agent_teams.assets import RoleAssetService
    from astrbot.core.agent_teams.workflows import WorkflowService
    from astrbot.core.db.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(folder / "runs.db"))
    service = WorkflowService(db, RoleAssetService(db))
    marker = folder / "handoff.json"
    try:
        if phase == "start":
            child, parent = rework_definitions()
            saved = await service.save_asset("alice", {"definition": child})
            parent["tasks"][0]["workflow"] = {
                "asset_id": saved["asset_id"],
                "version": 1,
            }
            approval = parent["tasks"][1]["human"]
            approval["artifact_version"] = 2
            with pytest.raises(RoleAssetError) as incompatible:
                await service.save_asset("alice", {"definition": parent})
            assert (
                incompatible.value.fields[0]["path"]
                == "definition.tasks.1.human.artifact_version"
            )
            approval["artifact_version"] = 1
            binding = parent["loops"][0]["feedback"][0]
            binding["source"] = "receive"
            with pytest.raises(RoleAssetError) as cross_scope:
                await service.save_asset("alice", {"definition": parent})
            assert (
                cross_scope.value.fields[0]["path"]
                == "definition.loops.0.feedback.0.source"
            )
            binding["source"] = "review"
            published = await service.save_asset("alice", {"definition": parent})
            run = await service.start_run(
                "alice",
                published["asset_id"],
                {"version": 1, "inputs": {"payload": {"seed": 0}}},
            )
            run_id = run["run_id"]
            _, first_inner = await pending_review(service, run_id)
            assert first_inner["scope_run_id"] != run_id
            await service.save_asset(
                "alice",
                {"definition": {**child, "name": "New version"}, "expected_version": 1},
                saved["asset_id"],
            )
            await answer_review(service, run_id, first_inner)
            run, first = await pending_review(service, run_id)
            assert first["scope_run_id"] == run_id
            assert (
                next(task for task in run["tasks"] if task["node_id"] == "observe")[
                    "status"
                ]
                == "pending"
            )
            ref = first["artifact_ref"]
            assert (await service.read_artifact("alice", **ref))["content"] == {
                "title": "First",
                "payload": {"round": 7},
            }
            call = next(task for task in run["tasks"] if task["node_id"] == "call")
            original = call["state"]["child_source_ref"]
            assert ref != original
            assert (await service.read_artifact("alice", **original))["content"][
                "private"
            ] == "hidden"
            await answer_review(
                service,
                run_id,
                first,
                '{"title":"Second","payload":{"round":8},"private":"hidden again"}',
            )
            run, second_inner = await pending_review(service, run_id)
            assert second_inner["scope_run_id"] not in {
                first_inner["scope_run_id"],
                run_id,
            }
            assert (
                await service.read_artifact("alice", **second_inner["artifact_ref"])
            )["content"] == {"payload": {"round": 7}}
            marker.write_text(
                json.dumps(
                    {"run_id": run_id, "waiting": second_inner, "rejected": ref}
                ),
                encoding="utf-8",
            )
            await asyncio.Event().wait()
        else:
            handoff = json.loads(marker.read_text(encoding="utf-8"))
            await service.boot_sweep()
            run_id = handoff["run_id"]
            run, waiting = await pending_review(service, run_id)
            for field in ("todo_id", "scope_run_id", "artifact_ref"):
                assert waiting[field] == handoff["waiting"][field]
            assert (
                next(
                    scope
                    for scope in run["scopes"]
                    if scope["run_id"] == waiting["scope_run_id"]
                )["snapshot"]["asset_version"]
                == 1
            )
            assert run["loops"][0]["iterations"][0]["status"] == "rejected"
            await answer_review(service, run_id, waiting)
            _, final = await pending_review(service, run_id)
            assert final["artifact_ref"] != handoff["rejected"]
            await answer_review(service, run_id, final)
            finished = await completed_run(service, run_id)
            assert finished["result"] == {"title": "Second", "payload": {"round": 8}}
            assert finished["loops"][0]["iterations"][1]["status"] == "passed"
            assert (await service.export_artifact("alice", **final["artifact_ref"]))[
                "content"
            ] == finished["result"]
            with pytest.raises(RoleAssetError):
                await service.export_artifact("alice", **handoff["rejected"])
    finally:
        await service.shutdown()
        await db.engine.dispose()


def test_nested_v2_rework_resumes_the_exact_second_round(tmp_path):
    """Terminate the process while the next child approval is pending.

    Args:
        tmp_path: Isolated SQLite database and process handoff directory.
    """
    import os
    import subprocess
    import sys
    import time

    environment = {**os.environ, "PYTHONUTF8": "1", "ASTRBOT_DISABLE_METRICS": "1"}
    command = [
        sys.executable,
        "-m",
        "tests.agent_teams.test_workflow_v2_nested",
        str(tmp_path),
    ]
    log_path = tmp_path / "start.log"
    with log_path.open("w", encoding="utf-8") as log:
        worker = subprocess.Popen(
            [*command, "start"], stdout=log, stderr=subprocess.STDOUT, env=environment
        )
        try:
            for _ in range(400):
                if (tmp_path / "handoff.json").exists() or worker.poll() is not None:
                    break
                time.sleep(0.05)
            assert (tmp_path / "handoff.json").exists(), log_path.read_text(
                encoding="utf-8"
            )
        finally:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)
    completed = subprocess.run(
        [*command, "recover"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.asyncio
async def test_missing_optional_feedback_matches_single_trial_and_exhaustion(
    workflow_env,
):
    """Omit optional feedback in a single trial and in a full rejected round.

    Args:
        workflow_env: Persistent workflow services backed by isolated SQLite.
    """
    env = workflow_env
    service = env.service
    contract = {
        "type": "object",
        "properties": {"text": {"type": "string", "default": "Default revision"}},
        "required": ["text"],
    }
    graph = {
        "name": "Optional feedback",
        "port_contract_version": 2,
        "input_contract": contract,
        "entry_task_ids": ["write"],
        "result_task_id": "deliver",
        "tasks": [
            {
                "id": "write",
                "name": "Write",
                "transform": "map_fields",
                "input_contract": contract,
                "output_contract": contract,
                "inputs": {"text": {"input": "text"}},
            },
            {
                "id": "missing",
                "name": "Omit feedback",
                "transform": "parse_json",
                "output_contract": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
                "inputs": {"text": {"input": "raw"}},
            },
            {
                "id": "review",
                "name": "Approve",
                "human": {
                    "kind": "approval",
                    "question": "Approve?",
                    "artifact_task_id": "write",
                },
            },
            {
                "id": "deliver",
                "name": "Deliver",
                "transform": "map_fields",
                "input_contract": contract,
                "output_contract": contract,
                "inputs": {"text": {"task_id": "write", "field": "text"}},
            },
        ],
        "edges": [
            {"source": "write", "target": "missing"},
            {"source": "missing", "target": "review"},
            {
                "source": "review",
                "target": "deliver",
                "condition": {"field": "approved", "value": True},
            },
        ],
        "loops": [
            {
                "id": "rework",
                "name": "Review again",
                "body": ["write", "missing", "review"],
                "max_iterations": 2,
                "exit_task_id": "review",
                "exit_condition": {"field": "approved", "value": True},
                "feedback": [
                    {
                        "source": "missing",
                        "field": "text",
                        "target": "write",
                        "input": "text",
                    }
                ],
            }
        ],
    }
    graph["input_contract"] = {
        **contract,
        "properties": {
            **contract["properties"],
            "raw": {"type": "string", "default": "{}"},
        },
    }
    saved = await service.save_asset("alice", {"definition": graph})
    draft = await env.roles.create_draft("alice", "workflow", {"definition": graph})
    payload = {
        "kind": "workflow",
        "mode": "single",
        "revision": draft["revision"],
        "node_id": "write",
    }
    preview = await service.preview_trial("alice", draft["asset_id"], payload)
    assert preview["requirements"]["missing"]["optional"]
    trial = await service.start_trial("alice", draft["asset_id"], payload)
    result = await completed_run(service, trial["run_id"])
    assert result["result"] == {"text": "Default revision"}

    run = await service.start_run(
        "alice", saved["asset_id"], {"version": 1, "inputs": {"text": "Initial"}}
    )
    run_id = run["run_id"]
    _, first = await pending_review(service, run_id)
    assert (await service.read_artifact("alice", **first["artifact_ref"]))[
        "content"
    ] == {"text": "Initial"}
    await answer_review(service, run_id, first, "Revise")
    _, second = await pending_review(service, run_id)
    assert (await service.read_artifact("alice", **second["artifact_ref"]))[
        "content"
    ] == result["result"]
    await answer_review(service, run_id, second, "Still needs revision")
    exhausted = await service.get_run("alice", run_id)
    assert exhausted["status"] == "blocked"
    assert exhausted["loops"][0]["status"] == "exhausted"
    assert exhausted["result"] is None
    assert (
        next(task for task in exhausted["tasks"] if task["node_id"] == "deliver")[
            "status"
        ]
        == "dependency_failed"
    )

    graph["loops"][0]["feedback"] = []
    with pytest.raises(RoleAssetError) as missing:
        await service.save_asset("alice", {"definition": graph})
    assert missing.value.fields[0]["path"] == "definition.loops.0.feedback"


@pytest.mark.asyncio
async def test_cross_loop_data_waits_cannot_cycle(workflow_env):
    """Order external consumers after the loop's exit during publication.

    Args:
        workflow_env: Persistent workflow services backed by isolated SQLite.
    """
    service = workflow_env.service
    child, graph = rework_definitions()
    saved = await service.save_asset("alice", {"definition": child})
    graph["tasks"][0]["workflow"] = {"asset_id": saved["asset_id"], "version": 1}
    optional = {"type": "object", "properties": {"title": {"type": "string"}}}
    middle = {
        "id": "middle",
        "name": "Wait inside the loop",
        "transform": "map_fields",
        "input_contract": optional,
        "output_contract": optional,
        "inputs": {"title": {"task_id": "observe", "field": "title"}},
    }
    graph["tasks"].append(middle)
    graph["loops"][0]["body"].append("middle")
    graph["edges"] = [
        {"source": "call", "target": "middle"},
        {"source": "middle", "target": "review"},
    ]
    with pytest.raises(RoleAssetError) as cycle:
        await service.save_asset("alice", {"definition": graph})
    assert any(
        issue["code"] == "CYCLE"
        and issue["path"]
        in {"definition.tasks.2.inputs.title", "definition.tasks.2.inputs.payload"}
        for issue in cycle.value.fields
    ), cycle.value.fields
    middle["inputs"] = {}
    assert (await service.save_asset("alice", {"definition": graph}))["version"] == 1


if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path

    asyncio.run(rework_process(Path(sys.argv[1]), sys.argv[2]))
