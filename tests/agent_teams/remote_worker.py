"""Real main-pipeline worker for remote execution crash acceptance."""

import asyncio
import contextlib
import copy
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from pytest import MonkeyPatch
from sqlalchemy import event as sql_event
from sqlalchemy.orm import Session

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.config import AstrBotConfig
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db.po import AgentWorkflowCheckpoint, AgentWorkflowProcess
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.event_bus import EventBus
from astrbot.core.knowledge_base import kb_mgr
from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.sources.webchat import webchat_adapter, webchat_event
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import WebChatQueueMgr
from astrbot.core.provider.manager import ProviderManager
from astrbot.core.star.context import Context
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)
from astrbot.core.umop_config_router import UmopConfigRouter
from astrbot.core.utils.shared_preferences import SharedPreferences
from astrbot.dashboard.services.agent_team_ports import build_ports_for_test


async def run_worker(folder: Path, endpoint: str, action: str, scenario: str) -> None:
    """Execute a public asset using actual remote Runner and durable services.

    Args:
        folder: Isolated data shared only by terminated and rebuilt processes.
        endpoint: Remote service owned by the external ledger process.
        action: Start or recover the original execution.
        scenario: Remote provider and selected persistence fault window.
    """
    patch = MonkeyPatch()
    runner_name, window = scenario.split(":", 1)
    db = SQLiteDatabase(str(folder / "remote.db"))
    await db.initialize()
    preferences = SharedPreferences(db)
    config = AstrBotConfig(str(folder / "config.json"), copy.deepcopy(DEFAULT_CONFIG))
    config["provider_settings"].update(
        {"streaming_response": False, "llm_safety_mode": False}
    )
    config["provider_settings"]["file_extract"]["enable"] = False
    config["agent_runner"] = {
        "runner_type": "dify",
        "config": {
            "dify_api_type": "workflow",
            "dify_api_base": endpoint,
            "dify_api_key": "local-only-key",
            "timeout": 10,
        },
    }
    if window.startswith("chat_"):
        config["agent_runner"]["config"]["dify_api_type"] = "chat"
    if window.startswith("chat_thought"):
        config["agent_runner"]["config"]["dify_api_type"] = "agent"
    if window == "chat_workflow_events":
        config["agent_runner"]["config"]["dify_api_type"] = "chatflow"
    if scenario.startswith("coze:"):
        config["agent_runner"] = {
            "runner_type": "coze",
            "config": {
                "coze_api_base": endpoint,
                "coze_api_key": "local-only-key",
                "bot_id": "bot-original",
                "auto_save_history": True,
                "timeout": 10,
            },
        }
    elif scenario.startswith("deerflow:"):
        config["agent_runner"] = {
            "runner_type": "deerflow",
            "config": {
                "deerflow_api_base": endpoint,
                "timeout": 10,
            },
        }
    elif scenario.startswith("dashscope:"):
        import dashscope

        patch.setattr(dashscope, "base_http_api_url", endpoint + "/api/v1")
        config["agent_runner"] = {
            "runner_type": "dashscope",
            "config": {
                "dashscope_api_key": "local-only-key",
                "dashscope_app_id": "app-original",
                "dashscope_app_type": "agent",
            },
        }
    acm = AstrBotConfigManager(config, UmopConfigRouter(preferences), preferences)
    await acm.initialize()
    personas = PersonaManager(db, acm)
    await personas.initialize()
    providers = ProviderManager(acm, db, personas)
    conversations = ConversationManager(db)
    patch.setattr(kb_mgr, "DB_PATH", folder / "knowledge" / "kb.db")
    patch.setattr(kb_mgr, "FILES_PATH", str(folder / "knowledge"))
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
    patch.setattr(star_handlers_registry, "_handlers", [])
    patch.setattr(star_handlers_registry, "star_handlers_map", {})
    patch.setitem(
        star_map,
        "t18_completion_hook",
        SimpleNamespace(name="t18_completion_hook", activated=True, reserved=False),
    )

    def record_hook(kind, response=None):
        """Persist the observable effects of the real plugin callbacks.

        Args:
            kind: Hook event delivered by the production dispatcher.
            response: Effective provider response, when supplied.
        """
        with (folder / "hooks.jsonl").open("a", encoding="utf-8") as log:
            log.write(
                json.dumps(
                    {
                        "kind": kind,
                        "text": response.result_chain.get_plain_text()
                        if response and response.result_chain
                        else None,
                    }
                )
                + "\n"
            )

    async def request_hook(event, request):
        """Observe the effective request hook through the production dispatcher.

        Args:
            event: Team WebChat event.
            request: Shared provider input before its durable checkpoint.
        """
        record_hook("request")

    async def response_hook(event, response):
        """Transform a complete response as a normal installed plugin would.

        Args:
            event: Team WebChat event.
            response: Shared effective provider response.
        """
        if response.role == "assistant":
            from astrbot.core.message.components import Plain

            response.result_chain.chain.append(Plain("[Checked by completion hook]"))
            record_hook("response", response)

    async def done_hook(event, run_context, response):
        """Observe agent completion after the response transformation.

        Args:
            event: Team WebChat event.
            run_context: Actual Agent execution context.
            response: Shared effective provider response.
        """
        if response.role == "assistant":
            record_hook("done", response)

    for hook_type, callback in (
        (EventType.OnLLMRequestEvent, request_hook),
        (EventType.OnLLMResponseEvent, response_hook),
        (EventType.OnAgentDoneEvent, done_hook),
    ):
        star_handlers_registry._handlers.append(
            StarHandlerMetadata(
                event_type=hook_type,
                handler_full_name="t18_completion_hook_" + callback.__name__,
                handler_name=callback.__name__,
                handler_module_path="t18_completion_hook",
                handler=callback,
                event_filters=[],
            )
        )
    queue = WebChatQueueMgr()
    patch.setattr(webchat_event, "webchat_queue_mgr", queue)
    adapter = webchat_adapter.WebChatAdapter(
        {"id": "webchat", "type": "webchat", "enable": True}, {}, asyncio.Queue()
    )
    bus = EventBus(asyncio.Queue(), {"default": scheduler}, acm)

    async def listener(data):
        """Deliver through the production WebChat adapter and execution registry.

        Args:
            data: Public WebChat message with its execution association.
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
        ports_factory=lambda owner: build_ports_for_test(
            queue, owner, lambda event: None
        ),
    )
    if action == "start":
        role = await roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Remote researcher",
                    "prompt": "Check evidence.",
                    "runner_capabilities": []
                    if window.startswith("chat_") or scenario.startswith("dashscope:")
                    else ["remote_query"],
                },
            },
        )
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Remote evidence",
                    "role_id": role["role_id"],
                    "role_version": 1,
                    "task": "Find the original evidence.",
                }
            },
        )

        def after_flush(session, flush_context):
            """Select the real remote identity transaction for abrupt loss.

            Args:
                session: SQLAlchemy transaction storing the checkpoint.
                flush_context: ORM flush context.
            """
            for row in [*session.new, *session.dirty]:
                if (
                    window == "workflow_events_process_failure"
                    and isinstance(row, AgentWorkflowProcess)
                    and row.kind == "remote_event"
                    and row.data["event"].get("data", {}).get("id")
                    == "tool-execution-original"
                ):
                    raise RuntimeError("Injected workflow process storage failure")
                if (
                    window == "workflow_events_checkpoint_failure"
                    and isinstance(row, AgentWorkflowCheckpoint)
                    and any(
                        event["event"].get("data", {}).get("id")
                        == "tool-execution-original"
                        for event in row.data.get("remote", {})
                        .get("events", {})
                        .values()
                    )
                ):
                    raise RuntimeError("Injected workflow event checkpoint failure")
                if (
                    window == "chat_thought_process_failure"
                    and isinstance(row, AgentWorkflowProcess)
                    and row.kind == "remote_event"
                    and row.data["event"].get("observation")
                ):
                    raise RuntimeError("Injected remote process storage failure")
                if (
                    window == "chat_thought_checkpoint_failure"
                    and isinstance(row, AgentWorkflowCheckpoint)
                    and len(row.data.get("remote", {}).get("events", {})) > 1
                ):
                    raise RuntimeError("Injected remote event checkpoint failure")
                if isinstance(row, AgentWorkflowCheckpoint) and row.phase == (
                    "request"
                    if window == "pre_create"
                    else "remote_result"
                    if window
                    in {
                        "result_saved",
                        "end_success",
                        "chat_result_saved",
                        "chat_thoughts",
                        "workflow_events_saved",
                        "chat_workflow_events",
                        "usage_missing",
                        "usage_zero",
                    }
                    else "remote_running"
                ):
                    if window == "workflow_events_query" and not any(
                        event["event"].get("data", {}).get("id")
                        == "tool-execution-next"
                        for event in row.data.get("remote", {})
                        .get("events", {})
                        .values()
                    ):
                        continue
                    if window == "identity_save_failure":
                        raise RuntimeError("Injected remote identity storage failure")
                    if window == "end_interrupted" or window in {
                        "identity_missing",
                        "chat_unknown",
                        "workflow_eof",
                        "chat_eof",
                        "partial_eof",
                        "accepted_disconnect",
                        "accepted_redirect",
                        "chat_thought_process_failure",
                        "chat_thought_checkpoint_failure",
                        "workflow_events_process_failure",
                        "workflow_events_checkpoint_failure",
                    }:
                        continue
                    session.info["remote_boundary"] = row.run_id

        def after_commit(session):
            """Hold only after the selected identity is durably committed.

            Args:
                session: Committed real storage transaction.
            """
            identity = session.info.pop("remote_boundary", None)
            if identity:
                (folder / "kill-ready.json").write_text(
                    json.dumps({"run_id": identity}), encoding="utf-8"
                )
                while True:
                    time.sleep(0.1)

        sql_event.listen(Session, "after_flush", after_flush)
        sql_event.listen(Session, "after_commit", after_commit)
    dispatch = asyncio.create_task(bus.dispatch())
    try:
        if action == "serve":
            from fastapi import FastAPI
            from fastapi.responses import HTMLResponse
            from fastapi.staticfiles import StaticFiles
            from hypercorn.asyncio import serve
            from hypercorn.config import Config

            from astrbot.dashboard.api.agent_teams import router as team_router
            from astrbot.dashboard.api.auth import require_dashboard_user
            from astrbot.dashboard.api.role_assets import router as role_router
            from astrbot.dashboard.api.workflow_assets import router as workflow_router
            from astrbot.dashboard.services.agent_team_service import AgentTeamService
            from tests.agent_teams.test_agent_team_service import FakeChatService

            await service.boot_sweep()
            app = FastAPI()
            app.state.services = SimpleNamespace(
                workflow_assets=service,
                role_assets=roles,
                agent_teams=AgentTeamService(db, lifecycle, FakeChatService()),
            )
            app.dependency_overrides[require_dashboard_user] = lambda: "alice"
            app.include_router(workflow_router, prefix="/api/v1")
            app.include_router(role_router, prefix="/api/v1")
            app.include_router(team_router, prefix="/api/v1")
            dist = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

            @app.get("/")
            async def index():
                """Serve the actual compiled dashboard against the durable QA data.

                Returns:
                    Production HTML with isolated test-owner authentication.
                """
                content = (dist / "index.html").read_text(encoding="utf-8")
                return HTMLResponse(
                    content.replace(
                        "<head>",
                        "<head><script>localStorage.setItem('token','local-qa');localStorage.setItem('user','alice');</script>",
                    )
                )

            server_config = Config()
            server_config.bind = ["127.0.0.1:6218"]
            (folder / "preview-ready.json").write_text("ready", encoding="utf-8")
            await serve(app, server_config)
            return
        if action == "start":
            run = await service.start_run("alice", asset["asset_id"], {"version": 1})
            (folder / "started.json").write_text(
                json.dumps({"run_id": run["run_id"]}), encoding="utf-8"
            )
            if window in {
                "identity_save_failure",
                "workflow_eof",
                "chat_eof",
                "end_interrupted",
                "partial_eof",
                "accepted_disconnect",
                "accepted_redirect",
                "chat_thought_process_failure",
                "chat_thought_checkpoint_failure",
                "workflow_events_process_failure",
                "workflow_events_checkpoint_failure",
            }:
                for _ in range(500):
                    current = await service.get_run("alice", run["run_id"])
                    if current["status"] != "running":
                        break
                    await asyncio.sleep(0.02)
                (folder / "kill-ready.json").write_text(
                    json.dumps({"run_id": run["run_id"]}), encoding="utf-8"
                )
            await asyncio.Future()
        else:
            run_id = json.loads(
                (folder / "kill-ready.json").read_text(encoding="utf-8")
            )["run_id"]
            before = await service.get_run("alice", run_id)
            await service.boot_sweep()
            stopped = await service.get_run("alice", run_id)
            error = None
            try:
                await service.recover_run("alice", run_id)
            except RoleAssetError as exc:
                error = {"status": exc.status, "message": str(exc)}
            for _ in range(1000):
                current = await service.get_run("alice", run_id)
                if current["status"] != "running":
                    break
                await asyncio.sleep(0.02)
            repeated = None
            if current["status"] in {"completed", "awaiting_result"}:
                try:
                    repeated = await service.recover_run("alice", run_id)
                except RoleAssetError as exc:
                    repeated = {"error": exc.status}
            (folder / "recovered.json").write_text(
                json.dumps(
                    {
                        "before": before,
                        "stopped": stopped,
                        "after": current,
                        "repeated": repeated,
                        "error": error,
                    }
                ),
                encoding="utf-8",
            )
    finally:
        await service.shutdown()
        dispatch.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dispatch
        await preferences.close()
        await knowledge.terminate()
        await db.engine.dispose()
        patch.undo()


if __name__ == "__main__":
    asyncio.run(run_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]))
