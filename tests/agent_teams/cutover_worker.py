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
    import jwt
    from httpx import ASGITransport, AsyncClient

    from astrbot.core import LogBroker
    from astrbot.core.agent_teams.assets import RoleAssetError
    from astrbot.core.agent_teams.chat import ChatCaller
    from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
    from astrbot.core.platform.manager import PlatformManager
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
        AiocqhttpAdapter,
    )
    from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
    from astrbot.core.star.context import Context
    from astrbot.core.star.star_manager import PluginManager
    from astrbot.core.updater import AstrBotUpdater
    from astrbot.dashboard.api.app import create_dashboard_asgi_app
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
    original_core = roles.core_lifecycle
    core = AstrBotCoreLifecycle(LogBroker(), service.db)
    for name in (
        "conversation_manager",
        "provider_manager",
        "persona_mgr",
        "astrbot_config_mgr",
        "kb_manager",
    ):
        setattr(core, name, getattr(original_core, name))
    core.astrbot_config = core.astrbot_config_mgr.default_conf
    core.platform_message_history_manager = PlatformMessageHistoryManager(service.db)
    core.umop_config_router = core.astrbot_config_mgr.ucr
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
    core.star_context = Context(
        asyncio.Queue(),
        core.astrbot_config,
        service.db,
        core.provider_manager,
        core.platform_manager,
        core.conversation_manager,
        core.platform_message_history_manager,
        core.persona_mgr,
        core.astrbot_config_mgr,
        core.kb_manager,
        None,
    )
    core.plugin_manager = PluginManager(core.star_context, core.astrbot_config)
    core.astrbot_updater = AstrBotUpdater()
    core.workflow_service = service
    roles.core_lifecycle = core
    app = create_dashboard_asgi_app(
        core_lifecycle=core, db=service.db, jwt_secret="isolated-cutover-qa"
    )
    legacy = app.state.services.agent_team_runs
    assert isinstance(legacy, AgentTeamRunService)
    # Reuse the actual WebChat transport and pipeline, without a scripted runner.
    legacy.ports_factory = lambda username, emit: service.ports_factory(username)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://cutover",
        headers={
            "Authorization": "Bearer "
            + jwt.encode(
                {"username": "alice"}, "isolated-cutover-qa", algorithm="HS256"
            )
        },
    )
    observations = {}
    try:
        async with app.router.lifespan_context(app), client:
            observations["formal_dashboard_started"] = True
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
                observations["startup_history"] = {}
                await service.db.create_agent_team(
                    team_id="saved-history-team",
                    owner_username="alice",
                    name="Original history",
                    coordinator_member_id="lead",
                    config={},
                    members=[],
                )
                for status in ("running", "paused", "completed", "stopped", "error"):
                    run_id = f"preserved-{status}"
                    await service.db.create_agent_team_run(
                        run_id=run_id,
                        team_id="saved-history-team",
                        workflow_id=None,
                        mode="dag",
                        input=f"Original {status} input",
                        status=status,
                        graph_snapshot={"nodes": [], "edges": []},
                        node_states={"evidence": {"status": "done", "result": status}},
                        rounds=[{"notes": "Original evidence"}],
                    )
                    response = await client.get(
                        f"/api/v1/agent_history/legacy_run/{run_id}"
                    )
                    assert response.status_code == 200, response.text
                    observations["startup_history"][run_id] = response.json()["data"][
                        "run"
                    ]
                (folder / "startup-history-before.json").write_text(
                    json.dumps(observations["startup_history"], indent=2),
                    encoding="utf-8",
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
                response = await client.post(
                    f"/api/v1/agent_teams/runs/{original['run_id']}/resume"
                )
                assert response.status_code == 200, response.text
                for _ in range(1000):
                    continued = await legacy.get_history_run(
                        "alice", original["run_id"]
                    )
                    if (
                        continued["history_status"] == "paused"
                        and not continued["execution_active"]
                    ):
                        break
                    await asyncio.sleep(0.02)
                assert continued["history_status"] == "paused"
                assert continued["run_id"] == original["run_id"]
                observations["continued_original"] = continued
                await legacy.request_stop_run("alice", original["run_id"])
                observations["after"] = await legacy.get_history_run(
                    "alice", original["run_id"]
                )
                assert observations["after"]["history_status"] == "stopped"
            else:
                before_startup = json.loads(
                    (folder / "startup-history-before.json").read_text(encoding="utf-8")
                )
                observations["startup_history"] = {}
                for run_id, before in before_startup.items():
                    response = await client.get(
                        f"/api/v1/agent_history/legacy_run/{run_id}"
                    )
                    assert response.status_code == 200, response.text
                    current = response.json()["data"]["run"]
                    observations["startup_history"][run_id] = current
                    assert current == before, (before, current)
                    assert current["history_status"] == (
                        "interrupted"
                        if before["status"] in {"running", "paused"}
                        else before["status"]
                    )
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
        await client.aclose()
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
