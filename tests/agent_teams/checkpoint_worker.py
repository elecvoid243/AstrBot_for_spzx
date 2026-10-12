"""Independent real-pipeline worker for public crash-recovery acceptance."""

import asyncio
import contextlib
import copy
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from pytest import MonkeyPatch
from sqlalchemy import event as sql_event, text
from sqlalchemy.orm import Session
from mcp.types import CallToolResult

from astrbot.core.agent.message import ImageURLPart, TextPart
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.config import AstrBotConfig
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db.po import AgentWorkflowCheckpoint, AgentWorkflowEvent, AgentWorkflowProcess, AgentWorkflowToolCall
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.event_bus import EventBus
from astrbot.core.knowledge_base import kb_mgr
from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.sources.webchat import webchat_adapter, webchat_event
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import WebChatQueueMgr
from astrbot.core.provider import Provider
from astrbot.core.provider.entities import LLMResponse, ProviderMeta, ProviderType, TokenUsage
from astrbot.core.provider.func_tool_manager import FunctionToolManager
from astrbot.core.provider.manager import ProviderManager
from astrbot.core.star.context import Context
from astrbot.core.star.star import StarMetadata, star_map
from astrbot.core.star.star_handler import EventType, StarHandlerMetadata, star_handlers_registry
from astrbot.core.umop_config_router import UmopConfigRouter
from astrbot.core.utils.shared_preferences import SharedPreferences
from astrbot.dashboard.services.agent_team_ports import build_ports_for_test


async def run_worker(folder: Path, endpoint: str, action: str, scenario: str = "response") -> None:
    """Build actual AstrBot services and stop at a committed response boundary.

    Args:
        folder: Isolated durable data shared across worker restarts.
        endpoint: External model/tool recorder owned by the parent process.
        action: Start an initial run or recover the saved original attempt.
        scenario: Persistence or external boundary at which to terminate the worker.
    """
    monkeypatch = MonkeyPatch()
    db = SQLiteDatabase(str(folder / "pipeline.db"))
    await db.initialize()
    preferences = SharedPreferences(db)
    config = AstrBotConfig(str(folder / "config.json"), copy.deepcopy(DEFAULT_CONFIG))
    config["provider_settings"].update({
        "streaming_response": False, "llm_safety_mode": False,
        "computer_use_runtime": "none", "display_reasoning_text": False,
    })
    config["provider_settings"]["file_extract"]["enable"] = False
    config["agent_runner"]["config"]["misc"]["add_cron_tools"] = False
    if scenario.startswith(("parameters", "repair")):
        config["agent_runner"]["config"]["misc"]["tool_schema_mode"] = "skills_like"
    config["agent_runner"]["config"]["model"]["provider_id"] = "model-original"
    config["test_profile"] = "original" if action == "start" else "edited"
    acm = AstrBotConfigManager(config, UmopConfigRouter(preferences), preferences)
    await acm.initialize()
    personas = PersonaManager(db, acm)
    if action == "start":
        await personas.create_persona("writer", "Original verified persona.")
    else:
        await personas.update_persona("writer", "Edited persona.")
        await personas.initialize()
    providers = ProviderManager(acm, db, personas)
    providers.llm_tools = FunctionToolManager()

    async def external_tool(event, value):
        """Record a real external effect outside the worker process.

        Args:
            event: Actual pipeline event supplied by the tool executor.
            value: Original model-planned argument.

        Returns:
            Complete external tool evidence.
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint + "/tool", json={"value": value})
            if action == "start" and (scenario == "started" or scenario == "batch_unknown" and value == "unknown-effect"):
                (folder / "kill-ready.json").write_text(json.dumps({"run_id": event.get_extra("agent_team_execution").run_id}), encoding="utf-8")
                await asyncio.Future()
            data = response.json()
            return CallToolResult.model_validate(data["raw_result"]) if "raw_result" in data else data["text"]

    async def external_stream(event, value):
        """Yield external output before a later effect or uncertain completion.

        Args:
            event: Actual normal pipeline event.
            value: Frozen tool argument.

        Yields:
            Separate partial and final external results.

        Raises:
            RuntimeError: The external completion outcome is unavailable.
        """
        async with httpx.AsyncClient() as client:
            initial = await client.post(endpoint + "/tool", json={"value": value, "part": "initial"})
            yield initial.json()["text"]
            if scenario == "generator_error":
                raise RuntimeError("External completion outcome is unavailable")
            final = await client.post(endpoint + "/tool", json={"value": value, "part": "final"})
            yield final.json()["text"]

    providers.llm_tools.func_list = [FunctionTool(
        name="external_evidence", description="Read external evidence.",
        parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
        handler=external_stream if scenario in {"partial_generator", "generator_error", "completed_generator"} else external_tool,
    )]

    class RecordedProvider(Provider):
        """External HTTP model boundary with ledger in the parent process."""

        def get_current_key(self):
            return "test"

        def set_key(self, key):
            pass

        async def get_models(self):
            return ["model-original"]

        def meta(self):
            return ProviderMeta(id="model-original", model=self.get_model(), type="scripted", provider_type=ProviderType.CHAT_COMPLETION)

        async def text_chat_stream(self, **kwargs):
            """Yield the recorded provider's complete response.

            Args:
                **kwargs: Actual provider request.

            Yields:
                Complete model response through the standard streaming path.
            """
            yield await self.text_chat(**kwargs)

        async def text_chat(self, **kwargs):
            """Send the actual provider-facing context to the independent recorder.

            Args:
                **kwargs: Effective real-pipeline model input.

            Returns:
                An original plan, explicit result submission or final response.
            """
            async with httpx.AsyncClient() as client:
                response = await client.post(endpoint + "/model", json={
                    "contexts": [m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in kwargs["contexts"]],
                    "tools": kwargs["func_tool"].names(), "model": kwargs.get("model"),
                    "parameter_query": all(not tool.description for tool in kwargs["func_tool"].tools),
                    "repair_query": "Do not return an empty response." in str(kwargs["contexts"]),
                })
                if action == "start" and scenario == "request":
                    from astrbot.core.agent_team_execution import current_team_execution
                    (folder / "kill-ready.json").write_text(json.dumps({"run_id": current_team_execution.get().run_id}), encoding="utf-8")
                    await asyncio.Future()
                data = response.json()
                if data.get("usage"):
                    data["usage"] = TokenUsage(**data["usage"])
                return LLMResponse(**data)

    provider = RecordedProvider({"id": "model-original", "type": "openai_chat_completion", "modalities": ["text", "image", "tool_use"]}, {})
    provider.set_model("model-original")
    providers.inst_map["model-original"] = provider
    providers.provider_insts.append(provider)
    conversations = ConversationManager(db)
    monkeypatch.setattr(kb_mgr, "DB_PATH", folder / "knowledge" / "kb.db")
    monkeypatch.setattr(kb_mgr, "FILES_PATH", str(folder / "knowledge"))
    knowledge = KnowledgeBaseManager(providers)
    await knowledge.initialize()
    context = Context(asyncio.Queue(), config, db, providers, None, conversations, None, personas, acm, knowledge, None)
    scheduler = PipelineScheduler(PipelineContext(config, SimpleNamespace(context=context), "default", db))
    await scheduler.initialize()
    from astrbot import core
    original_preferences = core.sp
    for name, module in list(sys.modules.items()):
        if name.startswith("astrbot") and getattr(module, "sp", None) is original_preferences:
            monkeypatch.setattr(module, "sp", preferences)
    monkeypatch.setattr(webchat_adapter, "db_helper", db)
    monkeypatch.setattr(webchat_event, "db_helper", db)
    monkeypatch.setattr(star_handlers_registry, "_handlers", [])
    monkeypatch.setattr(star_handlers_registry, "star_handlers_map", {})
    monkeypatch.setitem(star_map, "checkpoint_test", StarMetadata(name="checkpoint_test"))

    async def on_request(event, request):
        """Supply temporary content that normal chat-history saving would discard.

        Args:
            event: Actual member event.
            request: Effective request built by the main pipeline.
        """
        request.system_prompt += "\nprofile=" + context.get_config().get("test_profile", "")
        request.func_tool.add_tool(providers.llm_tools.func_list[0])
        if action == "start":
            request.extra_user_content_parts.extend([
                TextPart(text="Temporary original framing.").mark_as_temp(),
                ImageURLPart(image_url=ImageURLPart.ImageURL(url="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==")).mark_as_temp(),
            ])

    star_handlers_registry.append(StarHandlerMetadata(EventType.OnLLMRequestEvent, "checkpoint_request", "on_request", "checkpoint_test", on_request, []))
    queue_manager = WebChatQueueMgr()
    monkeypatch.setattr(webchat_event, "webchat_queue_mgr", queue_manager)
    adapter = webchat_adapter.WebChatAdapter({"id": "webchat", "type": "webchat", "enable": True}, {}, asyncio.Queue())
    event_bus = EventBus(asyncio.Queue(), {"default": scheduler}, acm)

    async def listener(data):
        """Deliver an actual WebChat event into the real scheduler.

        Args:
            data: Member delivery and execution token.
        """
        queue_manager.get_or_create_back_queue(data[2]["message_id"], data[1])
        await event_bus.event_queue.put(adapter.create_event(await adapter.convert_message(data)))

    queue_manager.set_listener(listener)
    lifecycle = SimpleNamespace(conversation_manager=conversations, provider_manager=providers, persona_mgr=personas, astrbot_config_mgr=acm, kb_manager=knowledge)
    roles = RoleAssetService(db, lifecycle)
    service = WorkflowService(db, roles, ports_factory=lambda username: build_ports_for_test(queue_manager, username, lambda event: None))
    if action == "start":
        if scenario in {"legacy_response", "legacy_upgrade_failure"}:
            def before_flush(session, flush_context, instances):
                """Construct a pre-T08 saved response through the real storage seam.

                Args:
                    session: Transaction preparing the first complete response.
                    flush_context: SQLAlchemy's flush context.
                    instances: Explicitly flushed instances, if supplied.
                """
                for checkpoint in [*session.new, *session.dirty]:
                    if isinstance(checkpoint, AgentWorkflowCheckpoint) and checkpoint.phase == "response":
                        checkpoint.data = {**{key: value for key, value in checkpoint.data.items() if key != "tool_calls"}, "format_version": 1}
                        session.info["legacy_response_run_id"] = checkpoint.run_id
                # Autoflush can persist the checkpoint before its call rows are added.
                for call in list(session.new):
                    if isinstance(call, AgentWorkflowToolCall) and call.run_id == session.info.get("legacy_response_run_id"):
                        session.expunge(call)
            sql_event.listen(Session, "before_flush", before_flush)
        # Fault injection runs only AFTER the real response transaction commits.
        # Correctness assertions use public services and the external HTTP ledger.
        def after_flush(session, flush_context):
            """Mark the selected durable boundary before the transaction commits.

            Args:
                session: Real SQLAlchemy session containing pending workflow rows.
                flush_context: SQLAlchemy's flush context supplied to the listener.

            Returns:
                None after recording any matching kill boundary on the session.
            """
            for row in [*session.new, *session.dirty]:
                if scenario in {"response", "no_thinking", "graph", "concurrent", "missing_model", "changed_tool", "legacy_response", "legacy_upgrade_failure"} and isinstance(row, AgentWorkflowProcess) and row.kind == "model_response" and row.data["id"] == "plan-original":
                    session.info["kill_boundary"] = row.run_id
                if scenario in {"parameters", "repair"} and isinstance(row, AgentWorkflowProcess) and row.kind == "model_response" and row.data["id"] == "parameters-original":
                    session.info["kill_boundary"] = row.run_id
                if scenario in {"parameters_checkpoint", "repair_checkpoint", "parameters_checkpoint_failure"} and isinstance(row, AgentWorkflowCheckpoint) and row.phase == "response" and row.data["response"]["id"] == "parameters-original":
                    session.info["kill_boundary"] = row.run_id
                if scenario in {"submit_pre", "submit_mixed_pre", "submit_custom_pre", "submit_file_pre"} and isinstance(row, AgentWorkflowCheckpoint) and row.phase == "tools_started" and row.data["response"]["id"] == "submission-original":
                    session.info["kill_boundary"] = row.run_id
                if scenario in {"submit_post", "submit_mixed_post", "submit_custom_post", "submit_file_post"} and isinstance(row, AgentWorkflowEvent) and row.kind == "result.accepted":
                    session.info["kill_boundary"] = row.run_id
                if scenario in {"tool_result", "parameters_tool_result"} and isinstance(row, AgentWorkflowProcess) and row.kind == "tool_result":
                    session.info["kill_boundary"] = row.run_id
                if scenario in {"tool_checkpoint", "tool_applied"} and isinstance(row, AgentWorkflowCheckpoint) and row.phase == ("tool_result" if scenario == "tool_checkpoint" else "tools_applied") and row.data["response"]["id"] == "plan-original":
                    session.info["kill_boundary"] = row.run_id
                if scenario == "tool_image_protocol" and isinstance(row, AgentWorkflowCheckpoint) and row.phase == "tool_result" and row.data["tool_calls"][0]["protocol_results"]:
                    session.info["kill_boundary"] = row.run_id
                if scenario == "partial_generator" and isinstance(row, AgentWorkflowCheckpoint) and row.phase == "tool_progress" and row.data["tool_calls"][0]["raw_results"]:
                    session.info["kill_boundary"] = row.run_id
                if scenario == "completed_generator" and isinstance(row, AgentWorkflowCheckpoint) and row.phase == "tool_result" and row.data["response"]["id"] == "plan-original":
                    session.info["kill_boundary"] = row.run_id

        def after_commit(session):
            """Hold a committed boundary until the parent terminates this process.

            Args:
                session: Session whose marked transaction has just committed.

            Returns:
                None for other transactions; a marked transaction waits for termination.
            """
            run_id = session.info.pop("kill_boundary", None)
            if run_id:
                (folder / "kill-ready.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
                while True:
                    time.sleep(1)

        sql_event.listen(Session, "after_flush", after_flush)
        sql_event.listen(Session, "after_commit", after_commit)
        role = await roles.save_role("alice", {
            "definition": {"name": "Writer", "prompt": "Original role prompt.", "persona_slot": "persona", "tools": ["evidence"], "max_steps": 4},
            "installation": {"bindings": {"model": "model-original", "persona": "writer", "evidence": "external_evidence"}},
        })
        definition = {"name": "Original report", "role_id": role["role_id"], "role_version": 1, "task": "Read evidence and submit a report."}
        if scenario.startswith(("submit_custom", "submit_file")):
            contract = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}
            if scenario.startswith("submit_file"):
                contract["properties"]["files"] = {"type": "array", "items": {"type": "object", "properties": {"file_id": {"type": "string"}}, "required": ["file_id"], "additionalProperties": False}}
                contract["required"].append("files")
            definition["output_contract"] = contract
        asset = await service.save_asset("alice", {"definition": definition})
        if scenario == "graph":
            asset = await service.save_asset("alice", {"definition": {"name": "Original graph", "tasks": [{"id": "write", "name": "Write", "member": "writer", "role_id": role["role_id"], "role_version": 1, "task": "Read evidence and submit a report."}], "edges": []}})
        if scenario in {"save_failure", "observation_failure"}:
            async with db.engine.begin() as connection:
                await connection.execute(text("CREATE TRIGGER reject_response_checkpoint BEFORE UPDATE ON agent_workflow_checkpoints WHEN NEW.phase = 'response' BEGIN SELECT RAISE(ABORT, 'checkpoint storage unavailable'); END" if scenario == "save_failure" else "CREATE TRIGGER reject_response_checkpoint BEFORE INSERT ON agent_workflow_process BEGIN SELECT RAISE(ABORT, 'observation storage unavailable'); END"))
        if scenario in {"tool_save_failure", "tool_observation_failure"}:
            async with db.engine.begin() as connection:
                await connection.execute(text("CREATE TRIGGER reject_response_checkpoint BEFORE UPDATE ON agent_workflow_checkpoints WHEN NEW.phase = 'tool_result' BEGIN SELECT RAISE(ABORT, 'tool result storage unavailable'); END" if scenario == "tool_save_failure" else "CREATE TRIGGER reject_response_checkpoint BEFORE INSERT ON agent_workflow_process WHEN NEW.kind = 'tool_result' BEGIN SELECT RAISE(ABORT, 'tool observation storage unavailable'); END"))
    dispatch = asyncio.create_task(event_bus.dispatch())
    try:
        if action == "serve":
            from fastapi import FastAPI
            from fastapi.responses import HTMLResponse
            from fastapi.staticfiles import StaticFiles
            from hypercorn.asyncio import serve
            from hypercorn.config import Config
            from astrbot.dashboard.api.auth import require_dashboard_user
            from astrbot.dashboard.api.workflow_assets import router as workflow_router
            from astrbot.dashboard.api.role_assets import router as role_router
            from astrbot.dashboard.api.agent_teams import router as team_router
            from astrbot.dashboard.services.agent_team_service import AgentTeamService
            from tests.agent_teams.test_agent_team_service import FakeChatService
            await service.boot_sweep()
            app = FastAPI()
            app.state.services = SimpleNamespace(workflow_assets=service, role_assets=roles, agent_teams=AgentTeamService(db, lifecycle, FakeChatService()))
            app.dependency_overrides[require_dashboard_user] = lambda: "alice"
            app.include_router(workflow_router, prefix="/api/v1")
            app.include_router(role_router, prefix="/api/v1")
            app.include_router(team_router, prefix="/api/v1")
            dist = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

            @app.get("/")
            async def index():
                """Serve the actual built dashboard for isolated visual acceptance.

                Returns:
                    Production HTML with the test owner's local authentication state.
                """
                content = (dist / "index.html").read_text(encoding="utf-8")
                return HTMLResponse(content.replace("<head>", "<head><script>localStorage.setItem('token','local-qa');localStorage.setItem('user','alice');</script>"))

            config = Config()
            config.bind = ["127.0.0.1:6199" if scenario == "partial_generator" else "127.0.0.1:6198"]
            (folder / "preview-ready.json").write_text(json.dumps({"pid": __import__('os').getpid()}), encoding="utf-8")
            await serve(app, config)
            return
        if action == "start":
            run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        else:
            run_id = json.loads((folder / "kill-ready.json").read_text(encoding="utf-8"))["run_id"]
            before = await service.get_run("alice", run_id)
            # Observe cumulative usage through the public WebChat transport.
            stats_queue = queue_manager.subscribe_system(before["snapshot"]["member"]["session_id"])
            if scenario == "parameters_checkpoint_failure":
                async with db.engine.begin() as connection:
                    await connection.execute(text("CREATE TRIGGER reject_response_checkpoint BEFORE INSERT ON agent_workflow_process BEGIN SELECT RAISE(ABORT, 'observation storage unavailable'); END" if action == "recover" else "DROP TRIGGER reject_response_checkpoint"))
            if scenario == "legacy_upgrade_failure":
                async with db.engine.begin() as connection:
                    await connection.execute(text("CREATE TRIGGER reject_response_checkpoint BEFORE UPDATE ON agent_workflow_checkpoints WHEN NEW.phase = 'response' BEGIN SELECT RAISE(ABORT, 'legacy upgrade storage unavailable'); END" if action == "recover" else "DROP TRIGGER reject_response_checkpoint"))
            if scenario in {"save_failure", "observation_failure", "tool_save_failure", "tool_observation_failure"}:
                async with db.engine.begin() as connection:
                    await connection.execute(text("DROP TRIGGER reject_response_checkpoint"))
            if scenario == "missing_model":
                providers.inst_map.pop("model-original")
                providers.provider_insts.clear()
            if scenario == "changed_tool":
                providers.llm_tools.func_list[0].description = "Changed external evidence capability."
            await service.boot_sweep()
            recoveries = []
            try:
                if scenario == "concurrent":
                    recoveries = await asyncio.gather(service.recover_run("alice", run_id), service.recover_run("alice", run_id), return_exceptions=True)
                else:
                    await service.recover_run("alice", run_id)
            except RoleAssetError as exc:
                (folder / "recovered.json").write_text(json.dumps({"before": before, "after": await service.get_run("alice", run_id), "error": {"status": exc.status, "message": str(exc)}}), encoding="utf-8")
                return
            run = before
        for _ in range(2000):
            current = await service.get_run("alice", run["run_id"])
            if current["status"] != "running":
                break
            await asyncio.sleep(0.01)
        if action != "start":
            result = await service.get_result("alice", run["run_id"]) if current["status"] == "completed" else None
            file_result = None
            if result and result.get("files"):
                file_result = await service.get_file("alice", run["run_id"], 1, result["files"][0]["file_id"])
                file_result["content"] = file_result["content"].decode("utf-8")
            late_errors = []
            repeated = None
            if result is not None:
                old_id = before["state"]["execution_id"]
                attempt_id = before["state"]["attempt_id"]
                operations = [
                    service.submit_result("alice", run["run_id"], attempt_id, {"text": "Late result"}, execution_id=old_id),
                    service.record_process("alice", run["run_id"], attempt_id, "late-record", "model_response", {"text": "Late response"}, execution_id=old_id),
                    service.store_file("alice", run["run_id"], attempt_id, "late.txt", b"Late file", execution_id=old_id),
                    service.save_checkpoint("alice", run["run_id"], attempt_id, old_id, {"phase": "request", "invocation_id": "late"}),
                ]
                for operation in operations:
                    try:
                        await operation
                    except RoleAssetError as exc:
                        late_errors.append(exc.status)
                repeated = await service.recover_run("alice", run["run_id"])
                await service.finish_submission("alice", run["run_id"])
            parent_id = current["snapshot"].get("parent_run_id")
            parent = await service.get_run("alice", parent_id) if parent_id else None
            if parent_id:
                for _ in range(200):
                    parent = await service.get_run("alice", parent_id)
                    if parent["status"] != "running":
                        break
                    await asyncio.sleep(0.01)
            stats = None
            while not stats_queue.empty():
                payload = stats_queue.get_nowait()
                if payload.get("chain_type") == "agent_stats":
                    stats = json.loads(payload["data"])
            queue_manager.unsubscribe_system(before["snapshot"]["member"]["session_id"], stats_queue)
            (folder / "recovered.json").write_text(json.dumps({"before": before, "after": current, "result": result, "file": file_result, "parent": parent, "late_errors": late_errors, "repeated": repeated, "stats": stats, "recovery_errors": [item.status for item in recoveries if isinstance(item, RoleAssetError)]}), encoding="utf-8")
        elif scenario in {"save_failure", "observation_failure", "tool_save_failure", "tool_observation_failure", "generator_error"}:
            (folder / "kill-ready.json").write_text(json.dumps({"run_id": run["run_id"]}), encoding="utf-8")
            await asyncio.Future()
    finally:
        await service.shutdown()
        dispatch.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dispatch
        await preferences.close()
        await knowledge.terminate()
        await db.engine.dispose()
        monkeypatch.undo()


if __name__ == "__main__":
    asyncio.run(run_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "response"))
