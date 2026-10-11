"""Run-service acceptance through real pipelines with a scripted model boundary."""

import asyncio
import contextlib
import copy
import json
import sys
from types import SimpleNamespace

import pytest

from astrbot.core import astr_main_agent, core_lifecycle
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.config import AstrBotConfig
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.conversation_mgr import ConversationManager
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
from astrbot.core.provider.entities import LLMResponse, ProviderMeta, ProviderType
from astrbot.core.provider.func_tool_manager import FunctionToolManager
from astrbot.core.provider.manager import ProviderManager
from astrbot.core.skills import skill_manager
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
from astrbot.dashboard.services.agent_team_run_service import AgentTeamRunService
from astrbot.dashboard.services.agent_team_service import AgentTeamService
from tests.agent_teams.test_agent_team_service import FakeChatService
from tests.unit.test_knowledge_base_service_contract import FakeEmbeddingProvider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["dag", "auto", "ordinary", "dag_cancel", "auto_cancel"]
)
async def test_main_pipeline_applies_role_capabilities_and_hooks(
    tmp_path, monkeypatch, mode
):
    """Existing run entries preserve real agent construction and plugin hooks.

    Args:
        tmp_path: Isolated configuration, skills, and database directory.
        monkeypatch: Redirects system storage and plugin registration.
        mode: Workflow, autonomous team, or ordinary chat regression.
    """
    db = SQLiteDatabase(str(tmp_path / "pipeline.db"))
    await db.initialize()
    preferences = SharedPreferences(db)
    config = AstrBotConfig(str(tmp_path / "config.json"), copy.deepcopy(DEFAULT_CONFIG))
    config["provider_settings"]["streaming_response"] = False
    config["provider_settings"]["display_reasoning_text"] = True
    config["provider_settings"]["show_tool_call_result"] = True
    config["provider_settings"]["llm_safety_mode"] = False
    config["provider_settings"]["computer_use_runtime"] = "none"
    config["provider_settings"]["file_extract"]["enable"] = False
    config["agent_runner"]["config"]["misc"]["add_cron_tools"] = False
    config["agent_runner"]["config"]["model"]["provider_id"] = "default-model"
    acm = AstrBotConfigManager(config, UmopConfigRouter(preferences), preferences)
    await acm.initialize()
    research = AstrBotConfig(
        str(tmp_path / "research.json"), copy.deepcopy(dict(config))
    )
    research["agent_runner"]["config"]["model"]["provider_id"] = "research-model"
    research["agent_runner"]["config"]["persona"]["persona_id"] = "researcher"
    research["test_profile"] = "research"
    config["test_profile"] = "default"
    acm.confs["research"] = research
    personas = PersonaManager(db, acm)
    await personas.create_persona("researcher", "Use verified research evidence.")
    providers = ProviderManager(acm, db, personas)
    providers.llm_tools = FunctionToolManager()
    providers.llm_tools.func_list = [
        FunctionTool(
            name="excluded_tool",
            description="A tool outside the role allowlist.",
            parameters={"type": "object", "properties": {}},
        )
    ]
    model_started = asyncio.Event()
    model_cancelled = asyncio.Event()

    class ScriptedProvider(Provider):
        """External model double observing actual prompts and requesting team tools."""

        def get_current_key(self):
            return "test"

        def set_key(self, key):
            pass

        async def get_models(self):
            return [self.provider_config["id"]]

        def meta(self):
            return ProviderMeta(
                id=self.provider_config["id"],
                model=self.get_model(),
                type="scripted",
                provider_type=ProviderType.CHAT_COMPLETION,
            )

        async def text_chat_stream(self, **kwargs):
            response = await self.text_chat(**kwargs)
            if response.role == "assistant":
                yield LLMResponse(
                    role="assistant",
                    is_chunk=True,
                    completion_text=response.completion_text,
                    reasoning_content=response.reasoning_content,
                )
            yield response

        async def text_chat(self, **kwargs):
            tools = kwargs.get("func_tool")
            names = tools.names() if tools else []
            prompt = "\n".join(
                str(message.get("content", ""))
                for message in kwargs.get("contexts", [])
                if message.get("role") == "system"
            )
            session = kwargs.get("session_id", "")
            if "team_dispatch" in names:
                self.turns[session] = self.turns.get(session, 0) + 1
                number = self.turns[session]
                if number in (1, 3):
                    tool = "team_dispatch" if number == 1 else "team_finish"
                    args = (
                        {
                            "assignments": [
                                {"member": "Writer", "task": "Write evidence."}
                            ]
                        }
                        if number == 1
                        else {"summary": "Research complete."}
                    )
                    return LLMResponse(
                        role="tool",
                        tools_call_name=[tool],
                        tools_call_args=[args],
                        tools_call_ids=[f"call-{number}"],
                    )
            elif mode.endswith("_cancel"):
                model_started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    model_cancelled.set()
                    raise
            return LLMResponse(
                role="assistant",
                reasoning_content="Verified the evidence.",
                completion_text=json.dumps(
                    {
                        "model": self.provider_config["id"],
                        "persona": "Use verified research evidence." in prompt,
                        "skill": "Alpha research skill" in prompt,
                        "other_skill": "Beta excluded skill" in prompt,
                        "hook": "profile=research;sender=alice;role=member" in prompt,
                        "tools": names,
                        "role_prompt": "Write with evidence." in str(kwargs),
                        "knowledge": "Verified research knowledge" in str(kwargs),
                        "other_knowledge": "Excluded knowledge" in str(kwargs),
                    }
                ),
            )

    for name in ("default-model", "research-model"):
        provider = ScriptedProvider(
            {
                "id": name,
                "type": "openai_chat_completion",
                "modalities": ["text", "tool_use"],
                "max_context_tokens": 8192,
            },
            {},
        )
        provider.set_model(name)
        provider.turns = {}
        providers.inst_map[name] = provider
        providers.provider_insts.append(provider)
    conversations = ConversationManager(db)
    providers.inst_map["embedding"] = FakeEmbeddingProvider()
    monkeypatch.setattr(kb_mgr, "DB_PATH", tmp_path / "knowledge" / "kb.db")
    monkeypatch.setattr(kb_mgr, "FILES_PATH", str(tmp_path / "knowledge"))
    knowledge = KnowledgeBaseManager(providers)
    await knowledge.initialize()
    for name, evidence in (
        ("research-evidence", "Verified research knowledge"),
        ("excluded-evidence", "Excluded knowledge"),
    ):
        helper = await knowledge.create_kb(name, embedding_provider_id="embedding")
        await helper.upload_document(
            "evidence.txt", None, "txt", pre_chunked_text=[evidence]
        )
    config["kb_names"] = ["excluded-evidence"]
    research["kb_names"] = ["excluded-evidence"]
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
    plugin_manager = SimpleNamespace(context=context)
    schedulers = {}
    for name, profile in (("default", config), ("research", research)):
        scheduler = PipelineScheduler(
            PipelineContext(profile, plugin_manager, name, db)
        )
        await scheduler.initialize()
        schedulers[name] = scheduler
    # Isolate the actual preference storage used by all initialized stages.
    from astrbot import core

    original_preferences = core.sp
    for name, module in list(sys.modules.items()):
        if (
            name.startswith("astrbot")
            and getattr(module, "sp", None) is original_preferences
        ):
            monkeypatch.setattr(module, "sp", preferences)
    monkeypatch.setattr(webchat_adapter, "db_helper", db)
    monkeypatch.setattr(webchat_event, "db_helper", db)
    skills = tmp_path / "skills"
    for name, description in (
        ("alpha", "Alpha research skill"),
        ("beta", "Beta excluded skill"),
    ):
        directory = skills / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\nResearch.\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(skill_manager, "get_astrbot_skills_path", lambda: str(skills))
    monkeypatch.setattr(star_handlers_registry, "_handlers", [])
    monkeypatch.setattr(star_handlers_registry, "star_handlers_map", {})
    monkeypatch.setitem(star_map, "pipeline_test", StarMetadata(name="pipeline_test"))

    async def on_request(event, request):
        request.system_prompt += (
            f"\nprofile={context.get_config().get('test_profile')};"
            f"sender={event.get_sender_id()};role={event.role}"
        )
        if request.func_tool is not None:
            request.func_tool.add_tool(providers.llm_tools.func_list[0])

    star_handlers_registry.append(
        StarHandlerMetadata(
            EventType.OnLLMRequestEvent,
            "pipeline_test_request",
            "on_request",
            "pipeline_test",
            on_request,
            [],
        )
    )
    queue_manager = WebChatQueueMgr()
    monkeypatch.setattr(webchat_event, "webchat_queue_mgr", queue_manager)
    adapter = webchat_adapter.WebChatAdapter(
        {"id": "webchat", "type": "webchat", "enable": True}, {}, asyncio.Queue()
    )
    event_bus = EventBus(asyncio.Queue(), schedulers, acm)

    async def listener(data):
        queue_manager.get_or_create_back_queue(data[2]["message_id"], data[1])
        await event_bus.event_queue.put(
            adapter.create_event(await adapter.convert_message(data))
        )

    queue_manager.set_listener(listener)
    chat = FakeChatService()
    lifecycle = SimpleNamespace(
        conversation_manager=conversations,
        provider_manager=providers,
        persona_mgr=personas,
        astrbot_config_mgr=acm,
        kb_manager=context.kb_manager,
    )
    teams = AgentTeamService(db=db, core_lifecycle=lifecycle, chat_service=chat)
    team = await teams.create_team(
        "alice",
        {
            "name": "Research",
            "coordinator": "Lead",
            "members": [
                {"name": "Lead", "system_prompt": "Coordinate research."},
                {"name": "Writer", "system_prompt": "Write with evidence."},
            ],
        },
    )
    for member in team["members"]:
        team = await teams.update_member(
            "alice",
            team["team_id"],
            member["member_id"],
            {
                "runner_config": {
                    "config_id": "research",
                    "skills": ["alpha"],
                    "kb_names": []
                    if member["name"] == "Lead"
                    else ["research-evidence"],
                    "tools": ["team_dispatch", "team_finish"]
                    if member["name"] == "Lead"
                    else [],
                },
            },
        )
    runs = AgentTeamRunService(db, chat, config_checker=lambda cid: cid in acm.confs)
    runs.ports_factory = lambda username, emit: build_ports_for_test(
        queue_manager, username, emit
    )
    dispatch_task = asyncio.create_task(event_bus.dispatch())
    run_id = None
    try:
        if mode == "ordinary":
            ports = build_ports_for_test(queue_manager, "alice", lambda event: None)
            mid = await ports.deliver("ordinary", "Hello.")
            reply, parts = await asyncio.wait_for(ports.collect("ordinary", mid), 10)
            assert json.loads(reply)["model"] == "default-model"
            assert json.loads(reply)["hook"] is False
            assert json.loads(reply)["other_knowledge"] is True
            await ports.close()
        else:
            workflow = await teams.create_workflow(
                "alice",
                team["team_id"],
                {
                    "name": "Report",
                    "graph": {
                        "nodes": [
                            {
                                "id": "write",
                                "member_id": team["members"][1]["member_id"],
                                "task": "Write evidence.",
                            }
                        ],
                        "edges": [],
                    },
                },
            )
            snapshot = await runs.start_run(
                "alice",
                team["team_id"],
                {
                    "mode": mode.removesuffix("_cancel"),
                    "input": "Research.",
                    **(
                        {"workflow_id": workflow["workflow_id"]}
                        if mode.startswith("dag")
                        else {}
                    ),
                },
            )
            run_id = snapshot["run_id"]
            if mode.endswith("_cancel"):
                await asyncio.wait_for(model_started.wait(), 10)
                await runs.request_stop_run("alice", run_id)
                await asyncio.wait_for(model_cancelled.wait(), 2)
                deadline = asyncio.get_running_loop().time() + 2
                while (await runs.get_run_snapshot("alice", run_id))[
                    "status"
                ] == "running":
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.01)
                assert (await runs.get_run_snapshot("alice", run_id))[
                    "status"
                ] == "stopped"
                return
            deadline = asyncio.get_running_loop().time() + 15
            while (await runs.get_run_snapshot("alice", run_id))["status"] == "running":
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            assert (await runs.get_run_snapshot("alice", run_id))[
                "status"
            ] == "completed"
            transcript = await runs.get_transcript(
                "alice", run_id, team["members"][1]["member_id"]
            )
            replies = [m for m in transcript["messages"] if m["direction"] == "reply"]
            assert json.loads(replies[0]["text"]) == {
                "model": "research-model",
                "persona": True,
                "skill": True,
                "other_skill": False,
                "hook": True,
                "tools": [],
                "role_prompt": True,
                "knowledge": True,
                "other_knowledge": False,
            }
            assert any(part["type"] == "think" for part in replies[0]["parts"])
    finally:
        if run_id is not None:
            await runs.request_stop_run("alice", run_id)
        dispatch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dispatch_task
        for member in team["members"]:
            queue_manager.remove_queues(member["session_id"])
        queue_manager.remove_queues("ordinary")
        await preferences.close()
        await knowledge.terminate()
        await db.engine.dispose()
