"""Real-manager nested-package scenarios shared by subprocess and UI acceptance."""

import asyncio
import json
from pathlib import Path


async def exercise_nested(
    service,
    roles,
    folder: Path,
    instance: str,
    action: str,
    profile_id: str,
    model_id: str,
):
    """Create, transfer, upgrade and resume a bounded mixed nested asset.

    Args:
        service: Actual public workflow application service.
        roles: Actual role service connected to native managers.
        folder: Independent instance's persistent data directory.
        instance: Source A or receiving instance B.
        action: Source export, prepare-before-crash, upgrade-after-restart or UI.
        profile_id: This instance's local profile identity.
        model_id: This instance's distinct native provider identity.
    """
    if instance == "a":
        identities = {}
        for version in (1, 2):
            role = await roles.save_role(
                "alice",
                {
                    "definition": {
                        "name": "Nested evidence role",
                        "prompt": f"NESTED_V{version}: preserve the complete evidence report.",
                        "tools": [],
                        "skills": [],
                        "knowledge_bases": [],
                        "max_steps": 8,
                        "max_iterations": 2,
                        "max_depth": 6,
                        "model_capabilities": ["tool_use"],
                        "runner_capabilities": [
                            "internal_checkpoint",
                            "task_result",
                            "task_tools",
                        ],
                    },
                    "installation": {
                        "profile_id": profile_id,
                        "bindings": {"model": model_id},
                    },
                    **({"expected_version": version - 1} if version > 1 else {}),
                },
                identities.get("role"),
            )
            identities["role"] = role["role_id"]
            leaf = await service.save_asset(
                "alice",
                {
                    "definition": {
                        "name": "Pinned evidence workflow",
                        "role_id": role["role_id"],
                        "role_version": version,
                        "task": "Research {topic}",
                        "required_inputs": ["topic"],
                    },
                    **({"expected_version": version - 1} if version > 1 else {}),
                },
                identities.get("leaf"),
            )
            identities["leaf"] = leaf["asset_id"]
            team = await service.save_asset(
                "alice",
                {
                    "definition": {
                        "kind": "autonomous",
                        "name": "Pinned research team",
                        "max_parallel": 1,
                        "max_depth": 6,
                        "coordinator": {
                            "role_id": role["role_id"],
                            "role_version": version,
                        },
                        "allowed_roles": [
                            {"role_id": role["role_id"], "role_version": version}
                        ],
                        "allowed_workflows": [
                            {
                                "asset_id": leaf["asset_id"],
                                "version": version,
                                "result_task_id": "result",
                            }
                        ],
                        "output_contract": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "approved": {"type": "boolean"},
                            },
                            "required": ["text", "approved"],
                            "additionalProperties": False,
                        },
                    },
                    **({"expected_version": version - 1} if version > 1 else {}),
                },
                identities.get("team"),
            )
            identities["team"] = team["asset_id"]
            graph = await service.save_asset(
                "alice",
                {
                    "definition": {
                        "name": "Portable mixed nested report",
                        "required_inputs": ["goal"],
                        "max_parallel": 1,
                        "max_depth": 6,
                        "tasks": [
                            {
                                "id": "evidence",
                                "name": "Research and revise evidence",
                                "team": {
                                    "asset_id": team["asset_id"],
                                    "version": version,
                                },
                                "inputs": {"goal": {"input": "goal"}},
                                "output_contract": team["definition"][
                                    "output_contract"
                                ],
                                "position": {"x": 80.0, "y": 60.0},
                            },
                            {
                                "id": "approve",
                                "name": "Approve exact report",
                                "human": {
                                    "kind": "approval",
                                    "question": "Approve this exact nested report?",
                                    "artifact_task_id": "evidence",
                                    "artifact_version": 1,
                                },
                                "position": {"x": 480.0, "y": 60.0},
                            },
                        ],
                        "edges": [{"source": "evidence", "target": "approve"}],
                        "loops": [
                            {
                                "id": "revision",
                                "name": "Bounded nested revision",
                                "body": ["evidence"],
                                "exit_task_id": "evidence",
                                "exit_condition": {
                                    "field": "approved",
                                    "operator": "eq",
                                    "value": True,
                                },
                                "max_iterations": 2,
                            }
                        ],
                    },
                    **({"expected_version": version - 1} if version > 1 else {}),
                },
                identities.get("graph"),
            )
            identities["graph"] = graph["asset_id"]
            package = await service.export_asset("alice", graph["asset_id"], version)
            (folder / f"export-v{version}.json").write_text(
                json.dumps(package, indent=2), encoding="utf-8"
            )
        return
    if action == "nested-prepare":
        package = json.loads((folder / "input-v1.json").read_text(encoding="utf-8"))
        preview = await service.preview_import("alice", {"package": package})
        installed = await service.import_asset("alice", {"package": package})
        run = await service.start_run(
            "alice",
            installed["asset"]["asset_id"],
            {
                "version": installed["asset"]["version"],
                "inputs": {"goal": "Produce the complete nested report"},
            },
        )
        for _ in range(2000):
            current = await service.get_run("alice", run["run_id"])
            if current["todos"] or current["status"] in {"blocked", "failed"}:
                break
            await asyncio.sleep(0.02)
        (folder / "before-crash.json").write_text(
            json.dumps(
                {
                    "preview": preview,
                    "installed": installed,
                    "run": current,
                    "resources": await roles.get_resources(),
                }
            ),
            encoding="utf-8",
        )
        assert current["todos"], current
        (folder / "waiting-ready").write_text("ready", encoding="utf-8")
        # The parent test kills this process. No graceful service shutdown occurs.
        await asyncio.Event().wait()
    elif action == "nested-upgrade":
        before = json.loads((folder / "before-crash.json").read_text(encoding="utf-8"))
        original = await service.get_run("alice", before["run"]["run_id"])
        package = json.loads((folder / "input-v2.json").read_text(encoding="utf-8"))
        preview = await service.preview_import("alice", {"package": package})
        installed = await service.import_asset("alice", {"package": package})
        repeated = await service.import_asset("alice", {"package": package})
        after = await service.get_run("alice", original["run_id"])
        todo = original["todos"][0]
        old_artifact = await service.read_artifact("alice", **todo["artifact_ref"])
        old_completed = await service.answer_todo(
            "alice",
            original["run_id"],
            todo["todo_id"],
            {
                "request_id": "approve-old",
                "action": "approve",
                "artifact_ref": todo["artifact_ref"],
            },
        )
        exported_old = await service.export_artifact("alice", **todo["artifact_ref"])
        new = await service.start_run(
            "alice",
            installed["asset"]["asset_id"],
            {
                "version": installed["asset"]["version"],
                "inputs": {"goal": "Produce the upgraded nested report"},
            },
        )
        for _ in range(2000):
            new = await service.get_run("alice", new["run_id"])
            if new["todos"] or new["status"] in {"blocked", "failed"}:
                break
            await asyncio.sleep(0.02)
        assert new["todos"], new
        new_todo = new["todos"][0]
        await service.answer_todo(
            "alice",
            new["run_id"],
            new_todo["todo_id"],
            {
                "request_id": "approve-new",
                "action": "approve",
                "artifact_ref": new_todo["artifact_ref"],
            },
        )
        new = await service.get_run("alice", new["run_id"])
        new_artifact = await service.read_artifact("alice", **new_todo["artifact_ref"])
        (folder / "result.json").write_text(
            json.dumps(
                {
                    "original": original,
                    "after_upgrade": after,
                    "old_completed": old_completed,
                    "preview": preview,
                    "installed": installed,
                    "repeated": repeated,
                    "new": new,
                    "old_artifact": old_artifact,
                    "exported_old": exported_old,
                    "new_artifact": new_artifact,
                    "resources": await roles.get_resources(),
                }
            ),
            encoding="utf-8",
        )
