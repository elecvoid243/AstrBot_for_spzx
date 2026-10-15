"""Independent AstrBot instance for portable-package and production WebUI acceptance."""

import asyncio
import contextlib
import copy
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


async def run_instance(
    folder: Path, endpoint: str, instance: str, action: str, port: int = 0
) -> None:
    """Build real managers, provider conversion, pipeline and durable public services.

    Args:
        folder: This instance's isolated root, configuration and SQLite directory.
        endpoint: External OpenAI-compatible recorder owned by the caller.
        instance: Distinct machine identity used for local profile and model IDs.
        action: Export, import-and-run, or serve the actual built dashboard.
        port: Dedicated HTTP port when serving the formal dashboard.
    """
    os.environ["ASTRBOT_ROOT"] = str(folder)
    from pytest import MonkeyPatch

    from astrbot.core.agent_teams.assets import RoleAssetService
    from astrbot.core.agent_teams.workflows import WorkflowService
    from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
    from astrbot.core.config import AstrBotConfig
    from astrbot.core.config.default import DEFAULT_CONFIG
    from astrbot.core.conversation_mgr import ConversationManager
    from astrbot.core.db.sqlite import SQLiteDatabase
    from astrbot.core.event_bus import EventBus
    from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager
    from astrbot.core.persona_mgr import PersonaManager
    from astrbot.core.pipeline.context import PipelineContext
    from astrbot.core.pipeline.scheduler import PipelineScheduler
    from astrbot.core.platform.sources.webchat import webchat_adapter, webchat_event
    from astrbot.core.platform.sources.webchat.webchat_queue_mgr import WebChatQueueMgr
    from astrbot.core.provider.manager import ProviderManager
    from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
    from astrbot.core.star.context import Context
    from astrbot.core.utils.shared_preferences import SharedPreferences
    from astrbot.core.umop_config_router import UmopConfigRouter
    from astrbot.dashboard.services.agent_team_ports import build_ports_for_test

    folder.mkdir(parents=True, exist_ok=True)
    patch = MonkeyPatch()
    db = SQLiteDatabase(str(folder / "instance.db"))
    await db.initialize()
    preferences = SharedPreferences(db)
    config = AstrBotConfig(str(folder / "config.json"), copy.deepcopy(DEFAULT_CONFIG))
    config["provider_settings"].update(
        {
            "streaming_response": False,
            "llm_safety_mode": False,
            "computer_use_runtime": "none",
        }
    )
    config["provider_settings"]["file_extract"]["enable"] = False
    config["agent_runner"]["config"]["misc"]["add_cron_tools"] = False
    profile_id, model_id = f"profile-{instance}", f"model-{instance}"
    config["agent_runner"]["config"]["model"]["provider_id"] = model_id
    acm = AstrBotConfigManager(config, UmopConfigRouter(preferences), preferences)
    await acm.initialize()
    acm.confs[profile_id] = AstrBotConfig(
        str(folder / "profile.json"), copy.deepcopy(dict(config))
    )
    config["provider_settings"]["provider_pool"] = []
    personas = PersonaManager(db, acm)
    providers = ProviderManager(acm, db, personas)
    provider_config = {
        "id": model_id,
        "type": "openai_chat_completion",
        "model": "portable-test-model",
        "api_base": endpoint + "/v1",
        "key": [f"private-key-{instance}"],
        "modalities": ["text", "tool_use"],
        "timeout": 10,
    }
    provider = ProviderOpenAIOfficial(provider_config, {})
    provider.set_model("portable-test-model")
    providers.inst_map[model_id] = provider
    providers.provider_insts.append(provider)
    if action == "serve-ambiguous":
        duplicate = ProviderOpenAIOfficial(
            {
                **provider_config,
                "id": model_id
                + "-duplicate-with-a-very-long-local-resource-name-for-narrow-screen-review",
            },
            {},
        )
        duplicate.set_model("portable-test-model")
        providers.inst_map[duplicate.meta().id] = duplicate
        providers.provider_insts.append(duplicate)
    conversations = ConversationManager(db)
    knowledge = KnowledgeBaseManager(providers)
    await knowledge.initialize()
    context = Context(
        asyncio.Queue(),
        config,
        db,
        providers,
        None,
        conversations,
        None,
        personas,
        acm,
        knowledge,
        None,
    )
    scheduler = PipelineScheduler(
        PipelineContext(config, SimpleNamespace(context=context), "default", db)
    )
    await scheduler.initialize()
    from astrbot import core

    original_preferences = core.sp
    for name, module in list(sys.modules.items()):
        if (
            name.startswith("astrbot")
            and getattr(module, "sp", None) is original_preferences
        ):
            patch.setattr(module, "sp", preferences)
    patch.setattr(webchat_adapter, "db_helper", db)
    patch.setattr(webchat_event, "db_helper", db)
    queue = WebChatQueueMgr()
    patch.setattr(webchat_event, "webchat_queue_mgr", queue)
    adapter = webchat_adapter.WebChatAdapter(
        {"id": "webchat", "type": "webchat", "enable": True}, {}, asyncio.Queue()
    )
    bus = EventBus(asyncio.Queue(), {"default": scheduler, profile_id: scheduler}, acm)

    async def listener(data):
        """Route actual WebChat delivery through the normal event bus.

        Args:
            data: Public delivery envelope containing the execution token.
        """
        queue.get_or_create_back_queue(data[2]["message_id"], data[1])
        await bus.event_queue.put(
            adapter.create_event(await adapter.convert_message(data))
        )

    queue.set_listener(listener)
    lifecycle = SimpleNamespace(
        conversation_manager=conversations,
        provider_manager=providers,
        persona_mgr=personas,
        astrbot_config_mgr=acm,
        kb_manager=knowledge,
    )
    roles = RoleAssetService(db, lifecycle)
    service = WorkflowService(
        db,
        roles,
        ports_factory=lambda user: build_ports_for_test(
            queue, user, lambda event: None
        ),
    )
    dispatch = asyncio.create_task(bus.dispatch())
    try:
        if instance == "a":
            assets = (await service.list_assets("alice"))["assets"]
            if assets:
                asset = assets[0]
            else:
                role = await roles.save_role(
                    "alice",
                    {
                        "definition": {
                            "name": "Portable evidence writer",
                            "prompt": "Preserve the exact verified evidence and submit the contracted result.",
                            "tools": [],
                            "skills": [],
                            "knowledge_bases": [],
                            "max_instances": 2,
                            "max_parallel": 2,
                            "model_capabilities": ["tool_use"],
                            "runner_capabilities": [
                                "internal_checkpoint",
                                "task_result",
                            ],
                        },
                        "installation": {
                            "profile_id": profile_id,
                            "bindings": {"model": model_id},
                        },
                    },
                )
                contract = {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                }
                task = {
                    "id": "research",
                    "name": "Research portable evidence",
                    "member": "researcher",
                    "role_id": role["role_id"],
                    "role_version": 1,
                    "task": "Research {topic}",
                    "inputs": {"topic": {"input": "topic"}},
                    "position": {"x": 80.0, "y": 60.0},
                    "output_contract": contract,
                }
                asset = await service.save_asset(
                    "alice",
                    {
                        "definition": {
                            "name": "Cross-instance evidence workflow",
                            "required_inputs": ["topic"],
                            "tasks": [
                                task,
                                {
                                    **task,
                                    "id": "write",
                                    "name": "Write reviewed evidence",
                                    "member": "writer",
                                    "task": "Write using {evidence}",
                                    "inputs": {
                                        "evidence": {
                                            "task_id": "research",
                                            "field": "text",
                                        }
                                    },
                                    "position": {"x": 480.0, "y": 60.0},
                                },
                            ],
                            "edges": [{"source": "research", "target": "write"}],
                        }
                    },
                )
            package = await service.export_asset("alice", asset["asset_id"], 1)
            (folder / "export.json").write_text(
                json.dumps(package, indent=2), encoding="utf-8"
            )
        elif action == "import":
            package = json.loads((folder / "input.json").read_text(encoding="utf-8"))
            preview = await service.preview_import("alice", {"package": package})
            installed = await service.import_asset("alice", {"package": package})
            run = await service.start_run(
                "alice",
                installed["asset"]["asset_id"],
                {"version": 1, "inputs": {"topic": "portable evidence"}},
            )
            for _ in range(500):
                current = await service.get_run("alice", run["run_id"])
                if current["status"] != "running":
                    break
                await asyncio.sleep(0.02)
            (folder / "result.json").write_text(
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
        if action.startswith("serve"):
            from fastapi import FastAPI
            from fastapi.responses import HTMLResponse
            from fastapi.staticfiles import StaticFiles
            from hypercorn.asyncio import serve
            from hypercorn.config import Config
            from astrbot.dashboard.api.auth import require_dashboard_user
            from astrbot.dashboard.api.workflow_assets import router
            from astrbot.dashboard.api.role_assets import router as role_router

            app = FastAPI()
            app.state.services = SimpleNamespace(
                workflow_assets=service, role_assets=roles
            )
            app.dependency_overrides[require_dashboard_user] = lambda: "alice"
            app.include_router(router, prefix="/api/v1")
            app.include_router(role_router, prefix="/api/v1")
            if action == "serve-runtime":

                @app.post("/qa/model")
                async def select_model(payload: dict):
                    """Change the real fixture provider during an isolated UI run.

                    Args:
                        payload: Model name selected by the acceptance harness.

                    Returns:
                        Current model metadata from the actual provider instance.
                    """
                    provider.set_model(payload["model"])
                    return {"model": provider.meta().model}

            dist = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

            @app.get("/")
            async def index():
                """Serve the production dashboard with isolated local test authentication.

                Returns:
                    Actual dashboard HTML; no substitute import controls are injected.
                """
                return HTMLResponse(
                    (dist / "index.html")
                    .read_text(encoding="utf-8")
                    .replace(
                        "<head>",
                        "<head><script>localStorage.setItem('token','isolated-qa');localStorage.setItem('user','alice');</script>",
                    )
                )

            server_config = Config()
            server_config.bind = [f"127.0.0.1:{port}"]
            (folder / "ready.json").write_text(
                json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8"
            )
            await serve(app, server_config)
    finally:
        await service.shutdown()
        dispatch.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dispatch
        await provider.client.close()
        await preferences.close()
        await knowledge.terminate()
        await db.engine.dispose()
        patch.undo()


if __name__ == "__main__":
    asyncio.run(
        run_instance(
            Path(sys.argv[1]),
            sys.argv[2],
            sys.argv[3],
            sys.argv[4],
            int(sys.argv[5]) if len(sys.argv) > 5 else 0,
        )
    )
