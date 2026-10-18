"""Real-pipeline public cutover scenarios for independent legacy owner processes."""

import asyncio
import json
import os
import sys
from pathlib import Path


async def run_cutover_scenario(folder, action, scenario, service, roles):
    """Keep an owned pause blocking, then stop or lose that original owner.

    Args:
        folder: Isolated persistent instance and public observation directory.
        action: Start the original owner or rebuild after its termination.
        scenario: Explicit stopping or independent process loss.
        service: Real workflow service connected to the main Agent pipeline.
        roles: Actual local role and resource service.
    """
    from astrbot.core.agent_teams.assets import RoleAssetError
    from astrbot.core.agent_teams.chat import ChatCaller
    from astrbot.core.platform.manager import PlatformManager
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
        AiocqhttpAdapter,
    )
    from astrbot.dashboard.services.agent_team_run_service import AgentTeamRunService
    from astrbot.dashboard.services.agent_team_service import AgentTeamsServiceError

    (folder / f"worker-identity-{action}.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "parent_pid": os.getppid(),
                "executable": sys.executable,
                "argv": sys.argv,
                "cwd": str(Path.cwd()),
                "action": action,
                "scenario": scenario,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    legacy = AgentTeamRunService(service.db, None)
    # Reuse the actual WebChat transport and pipeline, without a scripted runner.
    legacy.ports_factory = lambda username, emit: service.ports_factory(username)
    service.legacy_runs = legacy
    core = roles.core_lifecycle
    core.platform_manager = PlatformManager(
        core.astrbot_config_mgr.default_conf, asyncio.Queue()
    )
    core.platform_manager.platform_insts.append(
        AiocqhttpAdapter(
            {
                "id": "qa-source",
                "type": "aiocqhttp",
                "enable": True,
                "ws_reverse_host": "127.0.0.1",
                "ws_reverse_port": 0,
            },
            {},
            asyncio.Queue(),
        )
    )
    observations = {}
    try:
        if action == "start":
            role = await roles.save_role(
                "alice",
                {
                    "definition": {
                        "name": "New writer",
                        "prompt": "Submit the explicit result.",
                        "tools": [],
                    },
                    "installation": {"bindings": {"model": "model-original"}},
                },
            )
            asset = await service.save_asset(
                "alice",
                {
                    "definition": {
                        "name": "New asset",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "NEW_ASSET_REQUEST",
                    }
                },
            )
            await service.db.create_agent_team(
                team_id="original-team",
                owner_username="alice",
                name="Original team",
                coordinator_member_id="lead",
                config={"max_rounds": 20, "reply_timeout": 10},
                members=[
                    {
                        "member_id": identity,
                        "name": identity,
                        "session_id": f"original-{identity}",
                        "umo": f"webchat:FriendMessage:original-{identity}",
                        "system_prompt": "LEGACY_PARK: wait for the original owner.",
                        "provider_id": "model-original",
                    }
                    for identity in ("lead", "writer")
                ],
            )
            original = await legacy.start_run(
                "alice", "original-team", {"mode": "auto", "input": "LEGACY_PARK"}
            )
            for _ in range(1000):
                parked = await legacy.get_history_run("alice", original["run_id"])
                if parked["status"] == "paused" and not parked["execution_active"]:
                    break
                await asyncio.sleep(0.02)
            assert parked["history_status"] == "paused"
            observations["before"] = parked
            observations["active"] = await legacy.list_active_runs("alice")
            assert [run["run_id"] for run in observations["active"]["runs"]] == [
                original["run_id"]
            ]
            caller = ChatCaller(
                platform_id="qa-source",
                sender_id="12345",
                source="qa-source:FriendMessage:12345",
                profile_id="default",
            )
            await service.chat.save_access(
                "alice",
                asset["asset_id"],
                {**caller.model_dump(), "version": 1},
            )
            observations["launch_rejections"] = {}
            for entry in ("dashboard", "chat"):
                try:
                    if entry == "chat":
                        await service.chat.invoke(caller, asset["asset_id"], {})
                    else:
                        await service.start_run(
                            "alice", asset["asset_id"], {"version": 1}
                        )
                except RoleAssetError as exc:
                    observations["launch_rejections"][entry] = {
                        "status": exc.status,
                        "fields": exc.fields,
                        "message": str(exc),
                    }
                    assert exc.status == 409
                    assert exc.fields[0]["code"] == "LEGACY_RUN_ACTIVE"
                else:
                    raise AssertionError(
                        "An owned normal pause must block default entry cutover"
                    )
            (folder / "identities.json").write_text(
                json.dumps(
                    {"run_id": original["run_id"], "asset_id": asset["asset_id"]}
                ),
                encoding="utf-8",
            )
            if scenario == "loop_cutover_loss":
                (folder / "before-loss.json").write_text(
                    json.dumps(observations, default=str), encoding="utf-8"
                )
                (folder / "waiting-ready").write_text(
                    "Original owner is normally paused", encoding="utf-8"
                )
                await asyncio.Future()
            await legacy.request_stop_run("alice", original["run_id"])
            observations["after"] = await legacy.get_history_run(
                "alice", original["run_id"]
            )
            assert observations["after"]["history_status"] == "stopped"
        else:
            identities = json.loads(
                (folder / "identities.json").read_text(encoding="utf-8")
            )
            original, asset = (
                {"run_id": identities["run_id"]},
                {"asset_id": identities["asset_id"]},
            )
            observations["after"] = await legacy.get_history_run(
                "alice", original["run_id"]
            )
            assert observations["after"]["history_status"] == "interrupted"
            try:
                await legacy.resume_run("alice", original["run_id"])
            except AgentTeamsServiceError as exc:
                observations["retired_resume"] = {
                    "status": exc.status,
                    "message": str(exc),
                }
                assert exc.status == 410
            else:
                raise AssertionError("A lost legacy owner cannot be reconstructed")
            assert (
                await legacy.get_history_run("alice", original["run_id"])
                == observations["after"]
            )
        assert not (await legacy.list_active_runs("alice"))["runs"]
        new = await service.start_run("alice", asset["asset_id"], {"version": 1})
        for _ in range(1000):
            current = await service.get_run("alice", new["run_id"])
            if current["status"] != "running":
                break
            await asyncio.sleep(0.02)
        observations["new"] = current
        assert current["status"] == "completed"
        assert current["result"] == {"text": "New asset result"}
        assert current["run_id"] != original["run_id"]
    finally:
        (folder / f"{action}-public.json").write_text(
            json.dumps(observations, indent=2, default=str), encoding="utf-8"
        )


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    os.environ["ASTRBOT_ROOT"] = str(folder)
    from tests.agent_teams import checkpoint_worker, loop_worker

    loop_worker.run_loop_scenario = run_cutover_scenario
    asyncio.run(
        checkpoint_worker.run_worker(folder, sys.argv[2], sys.argv[3], sys.argv[4])
    )
