"""Independent real-pipeline worker for public crash-recovery acceptance."""

import asyncio
import base64
import contextlib
import copy
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import psutil
from mcp.types import CallToolResult
from pytest import MonkeyPatch
from sqlalchemy import event as sql_event
from sqlalchemy import text
from sqlalchemy.orm import Session

from astrbot.core.agent.message import ImageURLPart, TextPart
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.config import AstrBotConfig
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db.po import (
    AgentWorkflowCheckpoint,
    AgentWorkflowEvent,
    AgentWorkflowProcess,
    AgentWorkflowToolCall,
    AgentWorkflowUsage,
)
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
from astrbot.core.provider.entities import (
    LLMResponse,
    ProviderMeta,
    ProviderType,
    TokenUsage,
)
from astrbot.core.provider.func_tool_manager import FunctionToolManager
from astrbot.core.provider.manager import ProviderManager
from astrbot.core.star.context import Context
from astrbot.core.star.star import StarMetadata, star_map
from astrbot.core.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)
from astrbot.core.umop_config_router import UmopConfigRouter
from astrbot.core.utils.shared_preferences import SharedPreferences
from astrbot.dashboard.services.agent_team_ports import build_ports_for_test


async def run_worker(
    folder: Path, endpoint: str, action: str, scenario: str = "response", limits: dict | None = None
) -> None:
    """Build actual AstrBot services and stop at a committed response boundary.

    Args:
        folder: Isolated durable data shared across worker restarts.
        endpoint: External model/tool recorder owned by the parent process.
        action: Start an initial run or recover the saved original attempt.
        scenario: Persistence or external boundary at which to terminate the worker.
        limits: Optional frozen root limits for accounting acceptance scenarios.
    """
    monkeypatch = MonkeyPatch()
    guarded_case = scenario.removeprefix("guarded_") if scenario.startswith("guarded_") else None
    if guarded_case:
        scenario = {
            "query": "reconcile_succeeded",
            "query_denied": "reconcile_succeeded",
            "query_changed": "reconcile_succeeded",
            "query_missing": "reconcile_succeeded",
            "idempotent": "idempotent_retry",
            "idempotent_denied": "idempotent_retry",
            "safe": "safe_retry",
            "safe_denied": "safe_retry",
            "unknown": "started",
            "legacy": "started",
        }[guarded_case]
        identity = {
            "pid": os.getpid(), "parent_pid": os.getppid(),
            "creation_time": psutil.Process().create_time(), "action": action,
        }
        (folder / f"worker-{action}.tmp").write_text(json.dumps(identity), encoding="utf-8")
        (folder / f"worker-{action}.tmp").replace(folder / f"worker-{action}.json")
    db = SQLiteDatabase(str(folder / "pipeline.db"))
    await db.initialize()
    live_partial = "partial" in scenario and scenario.startswith(("idempotent_", "safe_", "reconcile_partial_not_executed"))
    preferences = SharedPreferences(db)
    config = AstrBotConfig(str(folder / "config.json"), copy.deepcopy(DEFAULT_CONFIG))
    config["provider_settings"].update({
        "streaming_response": False, "llm_safety_mode": False,
        "computer_use_runtime": "none", "display_reasoning_text": False,
    })
    config["provider_settings"]["file_extract"]["enable"] = False
    if scenario.startswith("file_mode_"):
        case = json.loads((folder / "case.json").read_text(encoding="utf-8"))
        config["provider_settings"].update(
            case.get("recovery_settings", case["settings"])
            if action != "start"
            else case["settings"]
        )
        config["admins_id"] = case.get(
            "admins" if action == "start" else "recovery_admins", []
        )
    if scenario.startswith("overflow"):
        config["provider_settings"]["computer_use_runtime"] = "local"
    config["agent_runner"]["config"]["misc"]["add_cron_tools"] = False
    if scenario == "usage_compression":
        config["agent_runner"]["config"]["compression"].update({"overflow_strategy": "llm_compress", "provider_id": "model-original", "keep_recent_ratio": 0.15, "max_turns": 50})
    if scenario.startswith(("parameters", "repair", "team_message_parameters")):
        config["agent_runner"]["config"]["misc"]["tool_schema_mode"] = "skills_like"
    config["agent_runner"]["config"]["model"]["provider_id"] = "model-alternate" if scenario.startswith(("inline_", "team_")) else "model-original"
    if scenario.startswith(("inline_", "team_")):
        config["agent_runner"]["config"]["persona"]["persona_id"] = "writer"
    config["test_profile"] = "original" if action == "start" else "edited"
    acm = AstrBotConfigManager(config, UmopConfigRouter(preferences), preferences)
    await acm.initialize()
    personas = PersonaManager(db, acm)
    if action == "start" or action == "serve" and await db.get_persona_by_id("writer") is None:
        await personas.create_persona("writer", "Original verified persona.")
    else:
        await personas.update_persona("writer", "Edited persona.")
        await personas.initialize()
    providers = ProviderManager(acm, db, personas)
    providers.llm_tools = FunctionToolManager()

    mixed_started = asyncio.Event()

    async def external_tool(event, value, idempotency_key=None):
        """Record a real external effect outside the worker process.

        Args:
            event: Actual pipeline event supplied by the tool executor.
            value: Original model-planned argument.
            idempotency_key: Whole-call deduplication key from a declared adapter.

        Returns:
            Complete external tool evidence.
        """
        # Keep loopback fixture traffic independent of machine proxy settings.
        async with httpx.AsyncClient(trust_env=False) as client:
            payload = {"value": value}
            if scenario.startswith(("reconcile_", "idempotent_", "safe_")) or scenario == "manual_retry_key":
                from astrbot.core.agent.tool import current_tool_invocation
                payload["identity"] = current_tool_invocation.get()
            if idempotency_key:
                payload["idempotency_key"] = idempotency_key
            response = await client.post(endpoint + "/tool", json=payload)
            if action == "start" and scenario == "control_mixed":
                child = await service.get_run("alice", event.get_extra("agent_team_execution").run_id)
                if child["snapshot"]["node_id"] == "a":
                    mixed_started.set()
                    await asyncio.Future()
                await mixed_started.wait()
                await service.control_run("alice", child["snapshot"]["parent_run_id"], {"request_id": "pause-mixed", "action": "pause"})
            if action == "start" and (scenario in {"started", "control_unknown"} or scenario.startswith(("manual_", "reconcile_", "idempotent_", "safe_")) and scenario != "manual_batch" or scenario in {"batch_unknown", "manual_batch"} and value == "unknown-effect"):
                (folder / "kill-ready.json").write_text(json.dumps({"run_id": event.get_extra("agent_team_execution").run_id}), encoding="utf-8")
                await asyncio.Future()
            data = response.json()
            if action == "start" and scenario in {"child_pause", "child_cancel", "reverse_pause", "reverse_cancel"}:
                child = await service.get_run("alice", event.get_extra("agent_team_execution").run_id)
                await service.control_run("alice", child["control"]["root_run_id"], {"request_id": "child-root-control", "action": "pause" if scenario.endswith("pause") else "cancel"})
            if action == "start" and scenario in {"team_unknown", "child_unknown", "reverse_unknown"}:
                (folder / "kill-ready.json").write_text("ready", encoding="utf-8")
                await asyncio.Future()
            if action == "start" and scenario in {
                "control_pause",
                "control_continue",
                "control_cancel",
                "control_shutdown",
            }:
                run_id = event.get_extra("agent_team_execution").run_id
                decision = {"request_id": "original-control", "action": "cancel" if scenario == "control_cancel" else "pause"}
                await service.control_run("alice", run_id, decision)
                await service.control_run("alice", run_id, decision)
            return CallToolResult.model_validate(data["raw_result"]) if "raw_result" in data else data["text"]

    async def external_stream(event, value, idempotency_key=None):
        """Yield external output before a later effect or uncertain completion.

        Args:
            event: Actual normal pipeline event.
            value: Frozen tool argument.
            idempotency_key: Adapter-declared identity for a recoverable live stream.

        Yields:
            Separate partial and final external results.

        Raises:
            RuntimeError: The external completion outcome is unavailable.
        """
        if scenario == "overflow_legacy_unaligned":
            yield CallToolResult.model_validate({"content": [{"type": "resource_link", "name": "Progress reference", "uri": "https://example.org/evidence"}]})
        async with httpx.AsyncClient(trust_env=False) as client:
            if live_partial:
                from astrbot.core.agent.tool import current_tool_invocation
                payload = {"value": value, "identity": current_tool_invocation.get()}
                if idempotency_key:
                    payload["idempotency_key"] = idempotency_key
                response = await client.post(endpoint + "/tool", json=payload)
                for part in response.json()["sequence"]:
                    yield part
                return
            if scenario.startswith("reconcile_partial"):
                from astrbot.core.agent.tool import current_tool_invocation
                identity = {"identity": current_tool_invocation.get()}
            else:
                identity = {}
            initial = await client.post(endpoint + "/tool", json={"value": value, **identity, **({} if scenario == "overflow_legacy_unaligned" else {"part": "initial"})})
            yield initial.json()["text"]
            if scenario == "overflow_legacy_unaligned":
                return
            if scenario == "generator_error":
                raise RuntimeError("External completion outcome is unavailable")
            final = await client.post(endpoint + "/tool", json={"value": value, "part": "final"})
            yield final.json()["text"]

    providers.llm_tools.func_list = [FunctionTool(
        name="external_evidence", description="Read external evidence.",
        parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
        handler=external_stream if scenario.startswith(("reconcile_partial", "idempotent_partial", "safe_partial")) or scenario in {"partial_generator", "manual_retry_partial", "generator_error", "completed_generator", "overflow_generator", "overflow_legacy_unaligned"} else external_tool,
    )]
    if scenario.startswith("reconcile_") or scenario == "tool_progress_gap" or guarded_case == "legacy" and action != "start":
        from astrbot.core.agent.tool import ToolRecovery, ToolRecoveryResult

        async def query_tool(context, identity, arguments):
            """Query the external service by the original durable call identity.

            Args:
                context: Restored and authorized main-pipeline tool context.
                identity: Original durable invocation identity.
                arguments: Original tool arguments.

            Returns:
                External evidence and a complete result, or an unknown outcome.
            """
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(endpoint + "/query", json={"identity": identity, "arguments": arguments})
                response.raise_for_status()
                return ToolRecoveryResult.model_validate(response.json())

        providers.llm_tools.func_list[0].recovery = ToolRecovery(query=query_tool)
    if scenario.startswith(("idempotent_", "safe_")) or scenario == "manual_retry_key":
        from astrbot.core.agent.tool import ToolRecovery
        providers.llm_tools.func_list[0].recovery = ToolRecovery(idempotency_key_arg="idempotency_key" if scenario.startswith("idempotent_") or scenario == "manual_retry_key" else None, safe_retry=scenario.startswith("safe_"))
    if guarded_case and providers.llm_tools.func_list[0].recovery is not None:
        providers.llm_tools.func_list[0].recovery.version = "permission-v1"
        if action != "start" and guarded_case == "query_changed":
            providers.llm_tools.func_list[0].recovery.version = "permission-v2"
        if action != "start" and guarded_case == "query_missing":
            providers.llm_tools.func_list[0].recovery = None
    if scenario.startswith("overflow"):
        async def read_saved_text(event, path):
            """Read the complete text through the enabled file tool boundary.

            Args:
                event: Actual member event.
                path: Saved tool output path supplied by the model.

            Returns:
                The complete original tool text.
            """
            return Path(path).read_text(encoding="utf-8")

        providers.llm_tools.func_list.append(FunctionTool(
            name="astrbot_file_read_tool", description="Read a saved text file.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=read_saved_text,
        ))
        from astrbot.core import astr_main_agent
        monkeypatch.setattr(astr_main_agent, "get_astrbot_system_tmp_path", lambda: str(folder / "overflow"))

    class RecordedProvider(Provider):
        """External HTTP model boundary with ledger in the parent process."""

        def get_current_key(self):
            return "test"

        def set_key(self, key):
            pass

        async def get_models(self):
            return ["model-original"]

        def meta(self):
            return ProviderMeta(id=self.provider_config["id"], model=self.get_model(), type="scripted", provider_type=ProviderType.CHAT_COMPLETION)

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
            from astrbot.core.agent_team_execution import current_team_execution
            execution = current_team_execution.get()
            async with httpx.AsyncClient(
                timeout=60 if scenario.startswith("team_deadline_") else 5,
                trust_env=False,
            ) as client:
                response = await client.post(endpoint + "/model", json={
                    "contexts": [m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in kwargs["contexts"]],
                    "tools": kwargs["func_tool"].names() if kwargs["func_tool"] else [], "model": kwargs.get("model"),
                    "parameter_query": bool(kwargs["func_tool"]) and all(not tool.description for tool in kwargs["func_tool"].tools),
                    "repair_query": "Do not return an empty response." in str(kwargs["contexts"]),
                    **({"llm_params": kwargs.get("llm_params")} if scenario.startswith("usage_") else {}),
                    **({"system_prompt": kwargs.get("system_prompt"), "provider_id": self.provider_config["id"], "tool_descriptions": {tool.name: tool.description for tool in kwargs["func_tool"].tools}} if scenario.startswith(("team_", "child_", "reverse_", "inline_")) else {}),
                    **({"run_id": execution.run_id, "member_id": execution.member_id, "attempt_id": execution.turn_id} if execution and scenario.startswith(("team_", "child_", "reverse_", "file_mode_")) else {}),
                })
                if action == "start" and scenario in {"request", "control_request"}:
                    from astrbot.core.agent_team_execution import current_team_execution
                    (folder / "kill-ready.json").write_text(json.dumps({"run_id": current_team_execution.get().run_id}), encoding="utf-8")
                    await asyncio.Future()
                data = response.json()
                if scenario.startswith("file_mode_") and case.get("revoke_admin_during_model"):
                    # Apply a real profile edit after waking assigned the event role.
                    acm.confs["default"]["admins_id"] = []
                if data.get("error"):
                    raise RuntimeError(data["error"])
                if data.get("usage"):
                    data["usage"] = TokenUsage(**data["usage"])
                return LLMResponse(**data)

    provider = RecordedProvider({"id": "model-original", "type": "openai_chat_completion", "modalities": ["text", "image", "tool_use"]}, {})
    if scenario.startswith("usage_native_"):
        from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
        provider = ProviderOpenAIOfficial({"id": "model-original", "type": "openai_chat_completion", "model": "model-original", "api_base": endpoint + "/v1", "key": ["test-key"], "modalities": ["text", "tool_use"]}, {})
        if scenario == "usage_native_anthropic":
            from astrbot.core.provider.sources.anthropic_source import ProviderAnthropic
            provider = ProviderAnthropic({"id": "model-original", "type": "anthropic_chat_completion", "model": "model-original", "api_base": endpoint, "key": ["test-key"], "modalities": ["text", "tool_use"], "custom_extra_body": {"max_tokens": 500}}, {})
        elif scenario.startswith("usage_native_gemini"):
            from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI
            provider = ProviderGoogleGenAI({"id": "model-original", "type": "googlegenai_chat_completion", "model": "model-original", "api_base": endpoint, "key": ["test-key"], "modalities": ["text", "tool_use"]}, {})
    provider.set_model("model-original")
    if scenario == "usage_compression":
        # The summary must fit the same model window, including its instruction
        # and schema. Trigger below that window so this exercises a real summary
        # delivery rather than the safe fallback for an oversized summary input.
        provider.provider_config.update(
            {"max_context_tokens": 12000, "compress_threshold_tokens": 6000}
        )
    providers.inst_map["model-original"] = provider
    providers.provider_insts.append(provider)
    if scenario.startswith(("inline_", "team_")):
        alternate = RecordedProvider({"id": "model-alternate", "type": "openai_chat_completion", "modalities": ["text", "tool_use"]}, {})
        alternate.set_model("model-alternate")
        providers.inst_map["model-alternate"] = alternate
        providers.provider_insts.append(alternate)
    conversations = ConversationManager(db)
    monkeypatch.setattr(kb_mgr, "DB_PATH", folder / "knowledge" / "kb.db")
    monkeypatch.setattr(kb_mgr, "FILES_PATH", str(folder / "knowledge"))
    knowledge = KnowledgeBaseManager(providers)
    await knowledge.initialize()
    kb_requests = []
    if scenario.startswith("inline_kb"):
        from astrbot.core.knowledge_base.models import KnowledgeBase
        from astrbot.core.knowledge_base.retrieval.manager import RetrievalResult

        class TestKnowledgeBase:
            def __init__(self, kb_id, name):
                self.kb = KnowledgeBase(kb_id=kb_id, kb_name=name, doc_count=1)
                self.init_error = None

            async def terminate(self):
                pass

        class TestRetrieval:
            async def retrieve(self, query, kb_ids, kb_id_helper_map, **kwargs):
                kb_requests.append(kb_ids)
                return [RetrievalResult(chunk_id="chunk", doc_id="doc", doc_name="evidence", kb_id=key,
                    kb_name=kb_id_helper_map[key].kb.kb_name,
                    content="Selected knowledge" if key == "selected-id" else "Wrong knowledge",
                    score=1.0, metadata={}) for key in kb_ids]

        knowledge.kb_insts = {
            "selected-id": TestKnowledgeBase("selected-id", "Renamed" if scenario == "inline_kb_renamed" else "Selected"),
            "other-id": TestKnowledgeBase("other-id", "selected-id"),
        }
        knowledge.retrieval_manager = TestRetrieval()
        config["kb_names"] = ["selected-id"]
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
        if scenario.startswith("file_mode_"):
            request.system_prompt += f";sender={event.get_sender_id()};role={event.role}"
        if action == "start" and scenario == "control_capacity":
            child = await service.get_run("alice", event.get_extra("agent_team_execution").run_id)
            root_id = child["snapshot"]["parent_run_id"]
            await service.control_run("alice", root_id, {"request_id": "pause-before-first-request", "action": "pause"})
            (folder / "kill-ready.json").write_text(json.dumps({"run_id": root_id}), encoding="utf-8")
            await asyncio.Future()
        if action != "start" and scenario == "control_request":
            run_id = event.get_extra("agent_team_execution").run_id
            await service.control_run("alice", run_id, {"request_id": "pause-restored-request", "action": "pause"})
        if not guarded_case and not scenario.startswith("inline_"):
            request.func_tool.add_tool(providers.llm_tools.func_list[0])
        elif action != "start" and guarded_case and guarded_case.endswith("_denied"):
            # Revoke after resource preflight so the invocation must recheck.
            assert not event.is_admin()
            await preferences.global_put("tool_permissions", {"_default": {"external_evidence": "admin"}})
        if scenario.startswith("overflow"):
            request.func_tool.add_tool(providers.llm_tools.func_list[1])
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
        if guarded_case:
            data[2]["_api_key_allow_admin_role"] = False
        queue_manager.get_or_create_back_queue(data[2]["message_id"], data[1])
        await event_bus.event_queue.put(adapter.create_event(await adapter.convert_message(data)))

    queue_manager.set_listener(listener)
    lifecycle = SimpleNamespace(conversation_manager=conversations, provider_manager=providers, persona_mgr=personas, astrbot_config_mgr=acm, kb_manager=knowledge)
    roles = RoleAssetService(db, lifecycle)
    service = WorkflowService(db, roles, ports_factory=lambda username: build_ports_for_test(queue_manager, username, lambda event: None))
    if action == "start" and scenario.startswith("usage_legacy_"):
        # Replay committed native T16 inputs, independent of checkout history.
        # Recovery still runs in a new process through the actual current pipeline.
        fixture_path = Path(__file__).parent / "fixtures" / "usage_pre_protocol.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8").replace("{data_dir}", folder.as_posix()))
        assert fixture["format_version"] == 1
        for filename, encoded in fixture["files"].items():
            assert Path(filename).name == filename
            (folder / filename).write_bytes(base64.b64decode(encoded))
        prepared = fixture["cases"][scenario]
        async with httpx.AsyncClient(trust_env=False) as client:
            for request in prepared["external"]["requests"]:
                response = await client.post(endpoint + "/model", json=request)
                response.raise_for_status()
                assert response.json()["id"] == "plan-original"
            for effect in prepared["external"]["effects"]:
                response = await client.post(endpoint + "/tool", json=effect)
                response.raise_for_status()
                assert response.json()["text"] == "Verified evidence."
        async with db.engine.begin() as connection:
            for table_name, rows in prepared["tables"].items():
                assert table_name.isidentifier()
                for row in rows:
                    assert all(column.isidentifier() for column in row)
                    columns = ", ".join('"' + column + '"' for column in row)
                    values = ", ".join(":" + column for column in row)
                    await connection.execute(text(f'INSERT OR REPLACE INTO "{table_name}" ({columns}) VALUES ({values})'), row)
        run_id = prepared["tables"]["agent_workflow_runs"][0]["run_id"]
        (folder / "kill-ready.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
        await asyncio.Future()
    if action == "recover_first":
        def retry_flush(session, flush_context):
            """Select a complete retried result transaction for a second crash.

            Args:
                session: Actual persistent workflow transaction.
                flush_context: SQLAlchemy flush context.
            """
            for row in [*session.new, *session.dirty]:
                if isinstance(row, AgentWorkflowCheckpoint) and row.phase == "tool_result" and row.data["response"]["id"] == "plan-original":
                    session.info["retry_boundary"] = True

        def retry_commit(session):
            """Wait at a committed result boundary until externally terminated.

            Args:
                session: Transaction that has finished committing.
            """
            if session.info.pop("retry_boundary", None):
                (folder / "second-ready.json").write_text("ready", encoding="utf-8")
                while True:
                    time.sleep(1)

        sql_event.listen(Session, "after_flush", retry_flush)
        sql_event.listen(Session, "after_commit", retry_commit)
    if action == "start" and not scenario.startswith(("file_mode_", "inline_", "loop_", "choice_")):
        if scenario in {"legacy_response", "legacy_upgrade_failure"} or scenario.startswith("overflow_legacy"):
            def before_flush(session, flush_context, instances):
                """Construct a pre-T08 saved response through the real storage seam.

                Args:
                    session: Transaction preparing the first complete response.
                    flush_context: SQLAlchemy's flush context.
                    instances: Explicitly flushed instances, if supplied.
                """
                for checkpoint in [*session.new, *session.dirty]:
                    if isinstance(checkpoint, AgentWorkflowCheckpoint) and scenario.startswith("overflow_legacy"):
                        checkpoint.data = {key: value for key, value in checkpoint.data.items() if key != "tool_result_files"}
                    elif isinstance(checkpoint, AgentWorkflowCheckpoint) and checkpoint.phase == "response":
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
                if isinstance(row, AgentWorkflowUsage) and row.data["kind"] == "model" and ((scenario == "usage_reserved" and row.data["quality"] == "in_flight") or (scenario == "usage_reported" and row.data["quality"] == "reported")):
                    session.info["kill_boundary"] = row.run_id
                if scenario.startswith("overflow") and not scenario.endswith("request") and isinstance(row, AgentWorkflowCheckpoint) and row.phase == ("tools_applied" if scenario.endswith("applied") else "tool_result") and row.data["response"]["id"] == "plan-original":
                    session.info["kill_boundary"] = row.run_id
                if scenario.startswith("overflow") and scenario.endswith("request") and isinstance(row, AgentWorkflowCheckpoint) and row.phase == "request" and row.data["model_calls"] == 2:
                    session.info["kill_boundary"] = row.run_id
                if scenario == "submit_conflict_result" and isinstance(row, AgentWorkflowCheckpoint) and row.phase == "tool_result" and row.data["response"]["id"] == "submission-conflict":
                    session.info["kill_boundary"] = row.run_id
                if scenario in {"submit_conflict_applied", "submit_conflict_pre"} and isinstance(row, AgentWorkflowCheckpoint) and row.phase == ("tools_applied" if scenario.endswith("applied") else "response") and row.data["response"]["id"] == "submission-conflict":
                    session.info["kill_boundary"] = row.run_id
                if scenario in {"response", "no_thinking", "graph", "concurrent", "missing_model", "changed_tool", "legacy_response", "legacy_upgrade_failure", "usage_legacy_response"} and isinstance(row, AgentWorkflowProcess) and row.kind == "model_response" and row.data["id"] == "plan-original":
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
                if scenario == "tool_progress_gap" and isinstance(row, AgentWorkflowCheckpoint) and row.phase == "tool_progress" and row.data["tool_calls"][0]["raw_results"]:
                    session.info["kill_boundary"] = row.run_id
                if scenario in {"tool_checkpoint", "tool_applied"} and isinstance(row, AgentWorkflowCheckpoint) and row.phase == ("tool_result" if scenario == "tool_checkpoint" else "tools_applied") and row.data["response"]["id"] == "plan-original":
                    session.info["kill_boundary"] = row.run_id
                if scenario == "usage_legacy_applied" and isinstance(row, AgentWorkflowCheckpoint) and row.phase == "tools_applied" and row.data["response"]["id"] == "plan-original":
                    session.info["kill_boundary"] = row.run_id
                if scenario == "tool_image_protocol" and isinstance(row, AgentWorkflowCheckpoint) and row.phase == "tool_result" and row.data["tool_calls"][0]["protocol_results"]:
                    session.info["kill_boundary"] = row.run_id
                if (scenario.startswith(("reconcile_partial", "idempotent_partial", "safe_partial")) or scenario in {"partial_generator", "manual_retry_partial"}) and isinstance(row, AgentWorkflowCheckpoint) and row.phase == "tool_progress" and len(row.data["tool_calls"][0]["protocol_results" if live_partial and not scenario.endswith("raw") else "raw_results"]) >= (2 if scenario.endswith("short") else 1):
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
            "definition": {"name": "Writer", "prompt": "Original role prompt.", "provider_id": "model-original", "persona_id": "writer", "tools": ["external_evidence", "astrbot_file_read_tool"] if scenario.startswith("overflow") else ["external_evidence"], "max_steps": 4, **({"context_length": 12000} if scenario == "usage_compression" else {}), **({"max_parallel": 2} if scenario == "control_mixed" else {}), **({"max_instances": 3} if scenario in {"manual_graph", "control_capacity", "control_mixed"} else {})},
            "installation": {"profile_id": "default"},
        })
        definition = {"name": "Original report", "role_id": role["role_id"], "role_version": 1, "task": "Read evidence and submit a report."}
        if limits is not None:
            definition["limits"] = limits
        if scenario in {"usage_reservation_failure", "usage_report_failure"}:
            async with db.engine.begin() as connection:
                await connection.execute(text("CREATE TRIGGER reject_usage BEFORE INSERT ON agent_workflow_usage BEGIN SELECT RAISE(ABORT, 'usage storage unavailable'); END" if scenario == "usage_reservation_failure" else "CREATE TRIGGER reject_usage BEFORE UPDATE ON agent_workflow_usage WHEN json_extract(NEW.data, '$.quality') = 'reported' BEGIN SELECT RAISE(ABORT, 'usage report unavailable'); END"))
        if scenario == "usage_report_pre":
            def usage_before_flush(session, flush_context, instances):
                """Terminate after the response, before its accounting commit.

                Args:
                    session: Real SQLite transaction.
                    flush_context: Actual flush context.
                    instances: Explicit flush targets, if any.
                """
                for receipt in session.dirty:
                    if isinstance(receipt, AgentWorkflowUsage) and receipt.data["quality"] == "reported":
                        (folder / "kill-ready.json").write_text(json.dumps({"run_id": receipt.run_id}), encoding="utf-8")
                        while True:
                            time.sleep(1)
            sql_event.listen(Session, "before_flush", usage_before_flush)
        if scenario.startswith(("submit_custom", "submit_file")):
            contract = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}
            if scenario.startswith("submit_file"):
                contract["properties"]["files"] = {"type": "array", "items": {"type": "object", "properties": {"file_id": {"type": "string"}}, "required": ["file_id"], "additionalProperties": False}}
                contract["required"].append("files")
            definition["output_contract"] = contract
        if scenario.startswith("human_") or scenario in {"response", "parameters", "repair", "parameters_checkpoint", "repair_checkpoint", "parameters_checkpoint_failure", "tool_progress_gap"}:
            definition = {"name": "Original report", "result_task_id": "write", "tasks": [{"id": "write", "name": "Writer", "member": "writer", "role_id": role["role_id"], "role_version": 1, "task": "Read evidence and submit a report."}], "edges": []}
        if scenario.startswith("team_"):
            definition = {"name": definition["name"], "result_task_id": "result", "tasks": [{"id": "result", "name": definition["name"], "member": "writer", "role_id": role["role_id"], "role_version": 1, "task": definition["task"]}]}
        asset = await service.save_asset("alice", {"definition": definition})
        if scenario in {"usage_parallel", "usage_compression", "usage_shared_wait"}:
            asset = await service.save_asset("alice", {"definition": {"name": "Shared last reservation", "max_parallel": 2, "limits": limits, "tasks": [
                {"id": key, "name": key, "member": "shared" if scenario == "usage_compression" or scenario == "usage_shared_wait" and key.startswith("a") else key, "role_id": role["role_id"], "role_version": 1, "task": key + ": Read evidence and submit a report." if scenario == "usage_shared_wait" else "Read evidence and submit a report.", "overrides": {"max_instances": 2, "max_parallel": 2}}
                for key in (["b", "a", "a2"] if scenario == "usage_shared_wait" else ["a", "b"])
            ], "edges": [{"source": "a", "target": "b"}] if scenario == "usage_compression" else []}})
        if scenario == "manual_graph":
            asset = await service.save_asset("alice", {"definition": {"name": "Unknown effect graph", "max_parallel": 1, "tasks": [
                {"id": "write", "name": "Unknown effect", "member": "writer", "role_id": role["role_id"], "role_version": 1, "task": "Read evidence and submit a report."},
                {"id": "independent", "name": "Independent task", "member": "independent", "role_id": role["role_id"], "role_version": 1, "task": "Independent task: submit the independent report."},
                {"id": "dependent", "name": "Dependent task", "member": "dependent", "role_id": role["role_id"], "role_version": 1, "task": "Dependent task: submit the dependent report."},
            ], "edges": [{"source": "write", "target": "dependent"}]}})
        if scenario == "graph":
            asset = await service.save_asset("alice", {"definition": {"name": "Original graph", "tasks": [{"id": "write", "name": "Write", "member": "writer", "role_id": role["role_id"], "role_version": 1, "task": "Read evidence and submit a report."}], "edges": []}})
        if scenario in {"control_capacity", "control_mixed"}:
            asset = await service.save_asset("alice", {"definition": {"name": "Paused graph", "max_parallel": 2 if scenario == "control_mixed" else 1, "tasks": [{"id": name, "name": name, "member": name, "role_id": role["role_id"], "role_version": 1, "task": name} for name in ["a", "b"]], "edges": []}})
        if scenario in {"save_failure", "observation_failure"}:
            async with db.engine.begin() as connection:
                await connection.execute(text("CREATE TRIGGER reject_response_checkpoint BEFORE UPDATE ON agent_workflow_checkpoints WHEN NEW.phase = 'response' BEGIN SELECT RAISE(ABORT, 'checkpoint storage unavailable'); END" if scenario == "save_failure" else "CREATE TRIGGER reject_response_checkpoint BEFORE INSERT ON agent_workflow_process BEGIN SELECT RAISE(ABORT, 'observation storage unavailable'); END"))
        if scenario in {"tool_save_failure", "tool_observation_failure"}:
            async with db.engine.begin() as connection:
                await connection.execute(text("CREATE TRIGGER reject_response_checkpoint BEFORE UPDATE ON agent_workflow_checkpoints WHEN NEW.phase = 'tool_result' BEGIN SELECT RAISE(ABORT, 'tool result storage unavailable'); END" if scenario == "tool_save_failure" else "CREATE TRIGGER reject_response_checkpoint BEFORE INSERT ON agent_workflow_process WHEN NEW.kind = 'tool_result' BEGIN SELECT RAISE(ABORT, 'tool observation storage unavailable'); END"))
    if action == "start" and scenario == "inline_recovery":
        def mark_inline_checkpoint(session, flush_context):
            for row in [*session.new, *session.dirty]:
                if isinstance(row, AgentWorkflowCheckpoint) and row.phase == "response" and row.data["response"]["id"] == "plan-original":
                    session.info["inline_recovery"] = row.run_id

        def hold_inline_checkpoint(session):
            run_id = session.info.pop("inline_recovery", None)
            if run_id:
                (folder / "kill-ready.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
                while True:
                    time.sleep(1)

        sql_event.listen(Session, "after_flush", mark_inline_checkpoint)
        sql_event.listen(Session, "after_commit", hold_inline_checkpoint)
    dispatch = asyncio.create_task(event_bus.dispatch())
    try:
        if scenario.startswith("choice_"):
            from astrbot.builtin_stars.ask_user_choice.ask_user_choice_tool import (
                AskUserChoiceTool,
            )
            from tests.agent_teams.choice_worker import run_choice_scenario

            providers.llm_tools.func_list.append(AskUserChoiceTool())
            await run_choice_scenario(folder, action, scenario, service, roles)
            return
        if scenario.startswith("inline_") and action == "start":
            definition = {"name": "Inline pipeline", "result_task_id": "agent", "default_provider_id": "model-original", "required_inputs": ["topic"], "tasks": [{
                "id": "agent", "name": "Writer", "member": "writer", "task": "Unique task {topic}",
                "inputs": {"topic": {"input": "topic"}}, "installation": {"profile_id": "default"},
                "role": {"name": "Writer", "prompt": "Unique responsibility.", "persona_id": "writer" if scenario == "inline_persona" else None,
                         "provider_id": "model-alternate" if scenario == "inline_override" else None,
                         "tools": ["external_evidence"], "skills": [], "knowledge_bases": ["selected-id"] if scenario.startswith("inline_kb") and scenario != "inline_kb_empty" else []},
            }]}
            if scenario == "inline_isolation":
                first = definition["tasks"][0]
                first["task"] = "Upstream private chat history secret-history"
                first["inputs"] = {}
                first["output_contract"] = {"type": "object", "properties": {"public": {"type": "string"}, "secret": {"type": "string"}}, "required": ["public", "secret"], "additionalProperties": False}
                second = {**first, "id": "second", "name": "Second", "task": "Consume {value}", "inputs": {"value": {"task_id": "agent", "field": "public"}}, "output_contract": None}
                definition["tasks"].append(second)
                definition["edges"] = [{"source": "agent", "target": "second"}]
                definition["result_task_id"] = "second"
            asset = await service.save_asset("alice", {"definition": definition})
            reopened = await service.get_asset("alice", asset["asset_id"], 1)
            readiness = await service.check_readiness("alice", asset["asset_id"], 1)
            assert readiness["ready"], readiness
            run = await service.start_run("alice", asset["asset_id"], {"version": 1, "inputs": {"topic": "energy"}})
            for _ in range(1000):
                current = await service.get_run("alice", run["run_id"])
                if current["status"] in {"completed", "failed", "interrupted"}:
                    break
                await asyncio.sleep(.02)
            (folder / "completed.json").write_text(json.dumps({"asset": reopened, "run": current, "kb_requests": kb_requests}), encoding="utf-8")
            return
        if scenario.startswith("file_mode_"):
            from tests.agent_teams.file_mode_worker import run_file_mode_scenario

            await run_file_mode_scenario(folder, endpoint, action, service, roles, queue_manager)
            return
        if scenario.startswith("team_deadline_"):
            from tests.agent_teams.team_deadline_worker import run_deadline_scenario

            await run_deadline_scenario(
                folder, endpoint, action, scenario, service, roles, context, adapter
            )
            return
        if scenario.startswith("reverse_") and action != "serve":
            from tests.agent_teams.reverse_scope_worker import run_reverse_scenario

            await run_reverse_scenario(folder, action, scenario, service, roles)
            return
        if scenario.startswith("child_") and action != "serve":
            from tests.agent_teams.child_scope_worker import run_child_scenario

            await run_child_scenario(folder, action, scenario, service, roles)
            return
        if scenario.startswith("team_") and action != "serve":
            from tests.agent_teams.team_worker import run_team_scenario

            await run_team_scenario(folder, action, scenario, service, roles)
            return
        if scenario.startswith("loop_") and action != "serve":
            from tests.agent_teams.loop_worker import run_loop_scenario

            await run_loop_scenario(folder, action, scenario, service, roles)
            return
        if scenario.startswith("human_") and action != "serve":
            from tests.agent_teams.human_worker import run_human_scenario

            await run_human_scenario(folder, action, scenario, service, roles)
            return
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
            app.state.services = SimpleNamespace(workflow_assets=service, role_assets=roles, agent_teams=AgentTeamService(db, lifecycle, FakeChatService()))
            app.dependency_overrides[require_dashboard_user] = lambda: "alice"
            app.include_router(workflow_router, prefix="/api/v1")
            app.include_router(role_router, prefix="/api/v1")
            app.include_router(team_router, prefix="/api/v1")
            if scenario.startswith("reverse_"):
                @app.middleware("http")
                async def delayed_fixed_version(request, call_next):
                    """Delay a selected real HTTP response for editing-race acceptance.

                    Args:
                        request: Actual browser HTTP request.
                        call_next: Existing API application handler.

                    Returns:
                        Original public response after the selected test delay.
                    """
                    marker = folder / "delay-asset-version.json"
                    if marker.exists():
                        decision = json.loads(marker.read_text(encoding="utf-8"))
                        if request.url.path.endswith("/" + decision["asset_id"]) and request.query_params.get("version") == str(decision["version"]):
                            marker.unlink()
                            (folder / "delayed-http-started.json").write_text(json.dumps(decision), encoding="utf-8")
                            await asyncio.sleep(3)
                    return await call_next(request)
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
            config.bind = ["127.0.0.1:6206" if scenario == "reverse_race" else "127.0.0.1:6205" if scenario.startswith("reverse_") else "127.0.0.1:6204" if scenario.startswith("child_") else "127.0.0.1:6202" if scenario.startswith("loop_") else "127.0.0.1:6201" if scenario.startswith("human_") else "127.0.0.1:6199" if scenario == "partial_generator" else "127.0.0.1:6198"]
            (folder / "preview-ready.json").write_text(json.dumps({"pid": __import__("os").getpid()}), encoding="utf-8")
            await serve(app, config)
            return
        if action == "start":
            run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        else:
            run_id = json.loads((folder / "kill-ready.json").read_text(encoding="utf-8"))["run_id"]
            before = await service.get_run("alice", run_id)
            if scenario in {"usage_parallel", "usage_compression", "usage_shared_wait"} or scenario.startswith("usage_native_"):
                await service.boot_sweep()
                (folder / "recovered.json").write_text(json.dumps({"before": before, "after": await service.get_run("alice", run_id)}), encoding="utf-8")
                return
            if scenario in {"usage_reservation_failure", "usage_report_failure"}:
                async with db.engine.begin() as connection:
                    await connection.execute(text("DROP TRIGGER reject_usage"))
            if action == "pause_decision":
                await service.control_run(
                    "alice",
                    run_id,
                    {"request_id": "pause-resolution", "action": "pause"},
                )
                return
            if scenario == "control_mixed":
                await service.boot_sweep()
                await service.control_run("alice", run_id, {"request_id": "continue-mixed", "action": "continue"})
                for _ in range(500):
                    current = await service.get_run("alice", run_id)
                    if current["tasks"][1]["status"] == "completed":
                        break
                    await asyncio.sleep(0.02)
                (folder / "recovered.json").write_text(json.dumps({"before": before, "after": current}), encoding="utf-8")
                return
            if scenario == "control_capacity":
                await service.boot_sweep()
                if action == "recover_decision":
                    def capacity_flush(session, flush_context):
                        """Select the durable continue decision for process loss.

                        Args:
                            session: Actual persistence transaction.
                            flush_context: SQLAlchemy flush context.
                        """
                        if any(isinstance(row, AgentWorkflowEvent) and row.kind == "run.continue" for row in session.new):
                            session.info["capacity_continue"] = True

                    def capacity_commit(session):
                        """Hold before no-checkpoint recovery can dispatch.

                        Args:
                            session: Actual committed transaction.
                        """
                        if session.info.pop("capacity_continue", False):
                            (folder / "second-ready.json").write_text("{}", encoding="utf-8")
                            while True:
                                time.sleep(1)

                    sql_event.listen(Session, "after_flush", capacity_flush)
                    sql_event.listen(Session, "after_commit", capacity_commit)
                    await service.control_run("alice", run_id, {"request_id": "continue-before-dispatch", "action": "continue"})
                for _ in range(200):
                    current = await service.get_run("alice", run_id)
                    if current["progress"]["running"] == 1 and current["tasks"][0]["recovery"]["boundary"] == "request":
                        break
                    await asyncio.sleep(0.03)
                await asyncio.sleep(0.4)
                (folder / "recovered.json").write_text(json.dumps({"before": before, "after": await service.get_run("alice", run_id)}), encoding="utf-8")
                return
            if scenario.startswith("control_"):
                await service.boot_sweep()
                stopped = await service.get_run("alice", run_id)
                if scenario in {
                    "control_pause",
                    "control_continue",
                    "control_shutdown",
                }:
                    if stopped["control"]["status"] == "paused":
                        assert stopped["status"] == "interrupted"
                        try:
                            await service.recover_run("alice", run_id)
                        except RoleAssetError as exc:
                            assert exc.status == 409
                        else:
                            raise AssertionError("Paused recovery must not admit external calls")
                    if action == "recover_decision":
                        def continue_flush(session, flush_context):
                            """Select a persisted continue decision for termination.

                            Args:
                                session: Actual transaction.
                                flush_context: SQLAlchemy flush context.
                            """
                            if any(isinstance(row, AgentWorkflowEvent) and row.kind == "run.continue" for row in session.new):
                                session.info["continue_control"] = True

                        def continue_commit(session):
                            """Hold after the continue decision and before resuming.

                            Args:
                                session: Actual committed transaction.
                            """
                            if session.info.pop("continue_control", False):
                                (folder / "second-ready.json").write_text("{}", encoding="utf-8")
                                while True:
                                    time.sleep(1)

                        sql_event.listen(Session, "after_flush", continue_flush)
                        sql_event.listen(Session, "after_commit", continue_commit)
                    await service.control_run("alice", run_id, {"request_id": "original-control", "action": "pause"})
                    await service.control_run(
                        "alice",
                        run_id,
                        {"request_id": "continue-control", "action": "continue"},
                    )
                    await service.control_run(
                        "alice",
                        run_id,
                        {"request_id": "continue-control", "action": "continue"},
                    )
                elif scenario == "control_cancel":
                    assert stopped["control"]["status"] == "cancelled"
                    assert stopped["status"] == "cancelled"
                    await service.control_run("alice", run_id, {"request_id": "original-control", "action": "cancel"})
                    for operation in [service.control_run("alice", run_id, {"request_id": "late-continue", "action": "continue"}), service.recover_run("alice", run_id), service.retry_task("alice", run_id, {"request_id": "unsafe-retry", "attempt_id": before["state"]["attempt_id"], "input_policy": "original"})]:
                        try:
                            await operation
                        except RoleAssetError as exc:
                            assert exc.status == 409
                        else:
                            raise AssertionError("Cancelled execution must remain terminal")
                    (folder / "recovered.json").write_text(json.dumps({"before": before, "after": await service.get_run("alice", run_id)}), encoding="utf-8")
                    return
                elif scenario == "control_retry":
                    old = before["attempts"][0]
                    await service.retry_task("alice", run_id, {"request_id": "retry-control", "attempt_id": old["attempt_id"], "input_policy": "original"})
                elif scenario == "control_unknown":
                    with contextlib.suppress(RoleAssetError):
                        await service.recover_run("alice", run_id)
                    try:
                        await service.retry_task("alice", run_id, {"request_id": "unsafe-retry", "attempt_id": before["state"]["attempt_id"], "input_policy": "original"})
                    except RoleAssetError as exc:
                        assert exc.status == 409 and "uncertain" in str(exc)
                    else:
                        raise AssertionError("Task retry must not bypass tool resolution")
                if scenario == "control_request":
                    await service.recover_run("alice", run_id)
                    for _ in range(500):
                        current = await service.get_run("alice", run_id)
                        if current["control"]["status"] == "paused":
                            break
                        await asyncio.sleep(0.02)
                    await asyncio.sleep(0.5)
                    (folder / "recovered.json").write_text(json.dumps({"before": before, "after": await service.get_run("alice", run_id)}), encoding="utf-8")
                    return
                for _ in range(1500):
                    current = await service.get_run("alice", run_id)
                    if current["status"] != "running":
                        break
                    await asyncio.sleep(0.02)
                (folder / "recovered.json").write_text(json.dumps({"before": before, "stopped": stopped, "after": current}), encoding="utf-8")
                return
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
            independent_before_resolution = None
            resolution_wait = None
            if scenario == "manual_graph":
                parent_id = before["snapshot"]["parent_run_id"]
                for _ in range(500):
                    parent = await service.get_run("alice", parent_id)
                    independent = next(task for task in parent["tasks"] if task["node_id"] == "independent")
                    if independent["status"] == "completed":
                        independent_before_resolution = parent
                        break
                    await asyncio.sleep(0.02)
                assert independent_before_resolution is not None
            if scenario in {"manual_save_failure", "reconcile_save_failure"} and action == "recover":
                async with db.engine.begin() as connection:
                    await connection.execute(text("CREATE TRIGGER reject_decision BEFORE INSERT ON agent_workflow_events WHEN NEW.kind = 'tool.resolved' BEGIN SELECT RAISE(ABORT, 'decision storage unavailable'); END" if scenario == "manual_save_failure" else "CREATE TRIGGER reject_decision BEFORE UPDATE ON agent_workflow_tool_recovery BEGIN SELECT RAISE(ABORT, 'reconciliation storage unavailable'); END"))
            elif scenario in {"manual_save_failure", "reconcile_save_failure"}:
                async with db.engine.begin() as connection:
                    await connection.execute(text("DROP TRIGGER IF EXISTS reject_decision"))
            if action == "recover_decision" or scenario == "manual_paused_race":

                def decision_flush(session, flush_context):
                    """Select the real decision transaction for a second crash.

                    Args:
                        session: Real transaction containing the explicit decision.
                        flush_context: SQLAlchemy flush context.
                    """
                    for item in session.new:
                        if isinstance(item, AgentWorkflowEvent) and item.kind == "tool.resolved":
                            session.info["decision_boundary"] = True

                def decision_commit(session):
                    """Hold an accepted human decision before scheduling begins.

                    Args:
                        session: Session whose selected decision is now durable.
                    """
                    if session.info.pop("decision_boundary", False):
                        (folder / "decision-ready.json").write_text("{}", encoding="utf-8")
                        if scenario == "manual_paused_race":
                            while not (folder / "release-decision").exists():
                                time.sleep(0.02)
                            return
                        while True:
                            time.sleep(1)

                sql_event.listen(Session, "after_flush", decision_flush)
                sql_event.listen(Session, "after_commit", decision_commit)
            recoveries = []
            try:
                if scenario.startswith("manual_") or scenario == "reconcile_paused":
                    if "paused" in scenario and scenario != "manual_paused_race":
                        await service.control_run(
                            "alice",
                            run_id,
                            {"request_id": "pause-resolution", "action": "pause"},
                        )
                    available_calls = (await service.get_run("alice", run_id))["tool_calls"]
                    call = next((item for item in available_calls if item["status"] == "unknown"), available_calls[0])
                    decision = {
                        "request_id": "human-decision",
                        "action": "reconcile"
                        if scenario == "reconcile_paused"
                        else "retry"
                        if scenario.startswith("manual_retry")
                        or scenario == "manual_save_failure"
                        else "confirm_failure"
                        if scenario.endswith("_failure")
                        else "confirm_result",
                        "evidence": "External service receipt independently verified by the owner.",
                    }
                    if decision["action"] in {"confirm_result", "confirm_failure"}:
                        decision["result"] = (
                            "External operation rejected: verified receipt."
                            if scenario.endswith("_failure")
                            else "Verified external evidence."
                        )
                    if scenario == "manual_http":
                        from fastapi import FastAPI

                        from astrbot.dashboard.api.auth import require_dashboard_user
                        from astrbot.dashboard.api.workflow_assets import router
                        app = FastAPI()
                        app.state.services = SimpleNamespace(workflow_assets=service)
                        app.include_router(router, prefix="/api/v1")
                        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                            url = f"/api/v1/workflow_runs/{run_id}/tool_calls/{call['record_id']}/resolve"
                            assert (await client.post(url, json=decision)).status_code == 404
                            assert (await client.get(f"/api/v1/workflow_runs/{run_id}")).status_code == 404
                            app.dependency_overrides[require_dashboard_user] = lambda: "alice"
                            assert (await client.post(url, json={**decision, "evidence": " "})).status_code == 422
                            assert (await client.post(url, json={**decision, "result": None})).status_code == 422
                            assert (await client.post(url, json=decision)).status_code == 200
                            assert (await client.post(url, json=decision)).status_code == 200
                            assert (await client.post(url, json={**decision, "result": "Altered result"})).status_code == 409
                    else:
                        await service.resolve_tool_call("alice", run_id, call["record_id"], decision)
                    await service.resolve_tool_call("alice", run_id, call["record_id"], decision)
                    if "paused" in scenario:
                        resolution_wait = await service.get_run("alice", run_id)
                        if scenario == "reconcile_paused":
                            try:
                                await service.resolve_tool_call(
                                    "alice",
                                    run_id,
                                    call["record_id"],
                                    {
                                        "request_id": "conflicting-resolution",
                                        "action": "retry",
                                        "evidence": "Another execution must not replace the queued reconciliation.",
                                    },
                                )
                            except RoleAssetError as exc:
                                assert exc.status == 409
                            else:
                                raise AssertionError(
                                    "An accepted pending decision cannot be replaced"
                                )
                        await service.control_run(
                            "alice",
                            run_id,
                            {"request_id": "continue-resolution", "action": "continue"},
                        )
                elif scenario == "concurrent":
                    recoveries = await asyncio.gather(service.recover_run("alice", run_id), service.recover_run("alice", run_id), return_exceptions=True)
                else:
                    await service.recover_run("alice", run_id)
            except RoleAssetError as exc:
                (folder / "recovered.json").write_text(json.dumps({"before": before, "after": await service.get_run("alice", run_id), "error": {"status": exc.status, "message": str(exc)}}), encoding="utf-8")
                return
            except Exception as exc:
                if scenario != "manual_save_failure":
                    raise
                (folder / "recovered.json").write_text(json.dumps({"before": before, "after": await service.get_run("alice", run_id), "error": {"message": str(exc)}}), encoding="utf-8")
                return
            run = before
        if action == "start" and scenario.startswith("control_"):
            for _ in range(1500):
                current = await service.get_run("alice", run["run_id"])
                ready = (
                    current["control"]["status"] == "paused"
                    and current["tasks"][1]["recovery"]["boundary"] == "tool_result"
                    if scenario == "control_mixed"
                    else current["control"]["status"] == "cancelled"
                    if scenario == "control_cancel"
                    else current["control"]["status"] == "paused"
                    and current["recovery"]["boundary"] == "tool_result"
                    if scenario
                    in {"control_pause", "control_continue", "control_shutdown"}
                    else current["status"] == "awaiting_result"
                )
                if ready:
                    break
                await asyncio.sleep(0.02)
            assert ready, current
            if scenario == "control_shutdown":
                await service.shutdown()
                (folder / "shutdown.json").write_text(
                    json.dumps(
                        {
                            "before": current,
                            "after": await service.get_run("alice", run["run_id"]),
                        }
                    ),
                    encoding="utf-8",
                )
                (folder / "kill-ready.json").write_text(
                    json.dumps({"run_id": run["run_id"]}), encoding="utf-8"
                )
                return
            if scenario == "control_retry":
                def retry_decision_flush(session, flush_context):
                    """Select the committed new-attempt decision for termination.

                    Args:
                        session: Actual persistence transaction.
                        flush_context: SQLAlchemy flush context.
                    """
                    if any(isinstance(row, AgentWorkflowEvent) and row.kind == "attempt.retried" for row in session.new):
                        session.info["retry_control"] = True

                def retry_decision_commit(session):
                    """Hold after the retry commits, before dispatching its executor.

                    Args:
                        session: Actual committed transaction.
                    """
                    if session.info.pop("retry_control", False):
                        (folder / "kill-ready.json").write_text(json.dumps({"run_id": run["run_id"]}), encoding="utf-8")
                        while True:
                            time.sleep(1)

                sql_event.listen(Session, "after_flush", retry_decision_flush)
                sql_event.listen(Session, "after_commit", retry_decision_commit)
                await service.retry_task("alice", run["run_id"], {"request_id": "retry-control", "attempt_id": current["state"]["attempt_id"], "input_policy": "original"})
            (folder / "kill-ready.json").write_text(
                json.dumps({"run_id": run["run_id"]}), encoding="utf-8"
            )
            await asyncio.Future()
        during_resolution_errors = []
        if action != "start" and scenario == "manual_retry_late":
            for _ in range(300):
                active = await service.get_run("alice", run["run_id"])
                if any(call["status"] == "started" and call["record_id"] != before["tool_calls"][0]["record_id"] for call in active["tool_calls"]):
                    break
                await asyncio.sleep(0.02)
            assert active["status"] == "running"
            old_id = before["state"]["execution_id"]
            attempt_id = before["state"]["attempt_id"]
            for operation in [
                service.submit_result("alice", run["run_id"], attempt_id, {"text": "Late old completion"}, execution_id=old_id),
                service.record_process("alice", run["run_id"], attempt_id, "late-active", "tool_result", {"result": "Late old result"}, execution_id=old_id),
                service.store_file("alice", run["run_id"], attempt_id, "late.txt", b"Late old file", execution_id=old_id),
                service.save_checkpoint("alice", run["run_id"], attempt_id, old_id, {"phase": "tool_result", "invocation_id": "late-old"}),
            ]:
                try:
                    await operation
                except RoleAssetError as exc:
                    during_resolution_errors.append(exc.status)
            async with httpx.AsyncClient(trust_env=False) as client:
                await client.post(endpoint + "/release", json={})
        for _ in range(2000):
            current = await service.get_run("alice", run["run_id"])
            if (
                action == "start"
                and scenario in {"usage_parallel", "usage_shared_wait"}
                and any(task["state"].get("usage_wait") for task in current["tasks"])
                and not (folder / "usage-waiting.json").exists()
            ):
                # Relay only the public observation while the external model is held.
                waiting_path = folder / "usage-waiting.tmp"
                waiting_path.write_text(json.dumps(current), encoding="utf-8")
                waiting_path.replace(folder / "usage-waiting.json")
            if current["status"] != "running":
                break
            await asyncio.sleep(0.01)
        if action != "start":
            if scenario == "reconcile_again" and current["status"] == "blocked":
                decision = {"request_id": "explicit-recheck", "action": "reconcile", "evidence": "Remote ledger is ready for another authoritative query."}
                record_id = current["tool_calls"][0]["record_id"]
                await service.resolve_tool_call("alice", run["run_id"], record_id, decision)
                await service.resolve_tool_call("alice", run["run_id"], record_id, decision)
                for _ in range(500):
                    current = await service.get_run("alice", run["run_id"])
                    if current["status"] != "running":
                        break
                    await asyncio.sleep(0.02)
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
            (folder / "recovered.json").write_text(
                json.dumps(
                    {
                        "before": before,
                        "after": current,
                        **({"overview": await service.get_run("alice", run["run_id"], include_details=False)} if scenario.startswith("usage_legacy_") else {}),
                        "result": result,
                        "file": file_result,
                        "parent": parent,
                        "during_resolution_errors": during_resolution_errors,
                        "independent_before_resolution": independent_before_resolution,
                        "resolution_wait": resolution_wait,
                        "late_errors": late_errors,
                        "repeated": repeated,
                        "stats": stats,
                        "recovery_errors": [
                            item.status
                            for item in recoveries
                            if isinstance(item, RoleAssetError)
                        ],
                    }
                ),
                encoding="utf-8",
            )
        elif scenario.startswith("usage_"):
            if scenario.startswith("usage_native_"):
                (folder / "native-config.json").write_text(json.dumps(provider.provider_config), encoding="utf-8")
            (folder / "completed.json").write_text(json.dumps({"after": current}), encoding="utf-8")
            (folder / "kill-ready.json").write_text(json.dumps({"run_id": run["run_id"]}), encoding="utf-8")
            await asyncio.Future()
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
    asyncio.run(run_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "response", json.loads(sys.argv[5]) if len(sys.argv) > 5 else None))
