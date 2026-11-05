"""Real ChatUI admission, main Agent tools, follow-ups and disconnected history."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from astrbot.builtin_stars.session_messages.main import Main
from astrbot.builtin_stars.session_messages.tools import SendChatMessageTool
from astrbot.core import astr_main_agent as ama
from astrbot.core import sp
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
    InternalAgentSubStage,
)
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.provider.func_tool_manager import FunctionToolManager
from astrbot.core.provider.provider import Provider
from astrbot.core.star.context import Context
from astrbot.core.tools.interactive_choice_registry import registry
from astrbot.core.utils.webchat_access import DashboardPrincipal
from astrbot.dashboard.services.chat_service import ChatServiceError
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat


class ControlledProvider(Provider):
    """Deterministic model operating through the production function tool loop."""

    def __init__(self, model, respond):
        super().__init__(
            {
                "id": model,
                "type": "openai_chat_completion",
                "modalities": ["text", "tool_use"],
                "max_context_tokens": 100000,
            },
            {},
        )
        self.set_model(model)
        self.respond = respond
        self.calls = []

    def get_current_key(self):
        return "test"

    def set_key(self, key):
        pass

    async def get_models(self):
        return [self.get_model()]

    async def text_chat(self, **kwargs):
        self.calls.append(
            json.loads(
                json.dumps(
                    {
                        key: kwargs.get(key)
                        for key in ("prompt", "model", "llm_params", "contexts")
                    },
                    default=str,
                )
            )
        )
        return await self.respond(kwargs)

    async def text_chat_stream(self, **kwargs):
        response = await self.text_chat(**kwargs)
        if not response.tools_call_name:
            response.is_chunk = True
            yield response
        response.is_chunk = False
        yield response

    def meta(self):
        return SimpleNamespace(type="controlled", id=self.get_model())


class MessagingContext(Context):
    def __init__(self, core, service, providers):
        self.core = core
        self.session_message_service = service
        self.conversation_manager = core.conversation_manager
        self.provider_manager = SimpleNamespace(llm_tools=FunctionToolManager())
        self.providers = providers
        self.subagent_orchestrator = None
        self.kb_manager = SimpleNamespace()

        async def persona(umo, **kwargs):
            return (
                "test",
                {
                    "prompt": self.get_config(umo).get(
                        "test_persona_prompt", "Use the supplied tools when requested."
                    ),
                    "_begin_dialogs_processed": [],
                },
                None,
                False,
            )

        self.persona_manager = SimpleNamespace(
            resolve_selected_persona=AsyncMock(side_effect=persona)
        )

    def get_config(self, umo=None):
        key = self.core.umop_config_router.get_conf_id_for_umop(umo) if umo else None
        return self.core.astrbot_config_mgr.confs[key or "default"]

    def get_llm_tool_manager(self):
        return self.provider_manager.llm_tools

    def get_provider_by_id(self, provider_id):
        return self.providers.get(provider_id)

    async def get_using_provider_async(self, umo=None):
        return self.providers["gpt-mini"]


@pytest_asyncio.fixture
async def messaging(ordinary_chat, monkeypatch, tmp_path):
    """Run real admission, main Agent and response stages on isolated resources.

    Args:
        ordinary_chat: Production API and temporary account database.
        monkeypatch: Isolates optional metrics, skills and image storage.
        tmp_path: Storage for this test's adapter files.

    Yields:
        Production services, controlled models and owned session identities.
    """
    client, server, core, db, admin, users = ordinary_chat
    service = server.asgi_app.state.services.chat
    service.conv_mgr = core.conversation_manager
    monkeypatch.setattr(
        "astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal.db_helper",
        db,
    )
    monkeypatch.setattr("astrbot.core.utils.metrics.Metric.upload", AsyncMock())
    monkeypatch.setattr(
        "astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal._record_internal_agent_stats",
        AsyncMock(),
    )
    cfg = core.astrbot_config
    cfg["provider_settings"].update(
        streaming_response=False,
        computer_use_runtime="none",
        datetime_system_prompt=False,
        identifier=False,
        web_search=False,
    )
    cfg["provider_settings"]["proactive_capability"]["add_cron_tools"] = False
    cfg["subagent_orchestrator"]["main_enable"] = False
    monkeypatch.setattr(ama, "_handle_webchat", AsyncMock())
    monkeypatch.setattr(ama, "retrieve_knowledge_base", AsyncMock(return_value=None))
    skill_manager = MagicMock()
    skill_manager.list_skills.return_value = []
    skill_manager.list_workspace_skills.return_value = []
    monkeypatch.setattr(ama, "SkillManager", lambda: skill_manager)
    monkeypatch.setattr(
        "astrbot.core.platform.sources.webchat.webchat_adapter.get_astrbot_data_path",
        lambda: str(tmp_path),
    )
    providers = {}
    context = MessagingContext(core, service, providers)
    core.plugin_manager.context = context
    await Main(context).initialize()
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    ctx = PipelineContext(cfg, core.plugin_manager, "default", db)
    waking = WakingCheckStage()
    await waking.initialize(ctx)

    respond = RespondStage()
    await respond.initialize(ctx)

    class ExecuteAgent:
        async def process(self, event):
            stage = InternalAgentSubStage()
            await stage.initialize(
                PipelineContext(
                    context.get_config(event.unified_msg_origin),
                    core.plugin_manager,
                    "default",
                    db,
                )
            )
            async for _ in stage.process(event, ""):
                yield

    pipeline = PipelineScheduler.__new__(PipelineScheduler)
    pipeline.ctx = ctx
    pipeline.stages = [waking, ExecuteAgent(), respond]
    tasks = []
    events = []

    async def deliver(item):
        event = adapter.create_event(await adapter.convert_message(item))
        events.append(event)
        tasks.append(asyncio.create_task(pipeline.execute(event)))

    webchat_queue_mgr.set_listener(deliver)
    headers = {"Authorization": f"Bearer {users[0]['token']}"}
    ids = []
    for label in ("A", "B"):
        sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
            "data"
        ]["session_id"]
        await db.update_platform_session(sid, display_name=label)
        ids.append(sid)
    data = SimpleNamespace(
        client=client,
        server=server,
        core=core,
        db=db,
        service=service,
        context=context,
        providers=providers,
        events=events,
        tasks=tasks,
        headers=headers,
        ids=ids,
        user=users[0],
        foreign=users[1],
        admin=admin,
    )
    data.adapter = adapter
    try:
        yield data
    finally:
        await webchat_queue_mgr.clear_listener()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for sid in ids:
            webchat_queue_mgr.remove_queues(sid)


def tool_call(name, args):
    return LLMResponse(
        role="assistant",
        tools_call_name=[name],
        tools_call_args=[args],
        tools_call_ids=[f"call-{name}"],
    )


async def wait_until(predicate):
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for chat execution")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_mode", ["provider", "full_queue", "queue_error", "overlapping_failure"]
)
async def test_failed_delivery_does_not_claim_a_running_recipient(
    messaging, monkeypatch, failure_mode
):
    """A failed A send must leave B available for an independent C chain.

    Args:
        messaging: Real SQLite, admission and main Agent execution fixture.
        monkeypatch: Injects an exception before queue acceptance.
        failure_mode: Configuration rejection, queue saturation, enqueue failure,
            or delayed rejection overlapping a different successful chain.
    """
    m = messaging
    a, b = m.ids
    c = (
        await m.db.create_platform_session(creator=m.user["resource_owner"])
    ).session_id
    m.ids.append(c)
    entered = asyncio.Event()
    release = asyncio.Event()
    expect_c = False

    class HoldRecipient(FunctionTool):
        async def call(self, context, **kwargs):
            entered.set()
            await release.wait()
            return "recipient released"

    m.context.add_llm_tools(
        HoldRecipient(
            name="hold_recipient",
            description="Hold the real recipient tool round.",
            parameters={"type": "object", "properties": {}},
        )
    )

    async def respond(kwargs):
        contexts = kwargs.get("contexts", [])
        if not any(item.get("role") == "tool" for item in contexts):
            return tool_call("hold_recipient", {})
        if expect_c:
            assert "independent C message" in json.dumps(contexts)
        return LLMResponse(role="assistant", completion_text="C message received")

    provider = ControlledProvider("gpt-mini", respond)
    m.providers["gpt-mini"] = provider
    m.core.provider_manager.inst_map["gpt-mini"] = provider
    selected = await m.client.put(
        f"/api/v1/chat/sessions/{b}/selection",
        headers=m.headers,
        json={"selected_provider": "gpt-mini", "selected_model": "gpt-4o-mini"},
    )
    assert selected.json()["status"] == "ok"
    human = asyncio.create_task(
        m.client.post(
            "/api/v1/chat",
            headers=m.headers,
            json={"session_id": b, "message": "human starts B"},
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        recipient = m.events[0]
        account = DashboardPrincipal(
            m.user["account_id"],
            m.user["resource_owner"],
            "user",
            m.user["username"],
        )
        contexts = []
        for sid in (a, c):
            event = m.adapter.create_event(
                await m.adapter.convert_message(
                    (
                        account.resource_owner,
                        sid,
                        {
                            "message": [{"type": "plain", "text": "source"}],
                            "message_id": "source-" + sid,
                            "_dashboard_account": account,
                        },
                    )
                )
            )
            contexts.append(
                SimpleNamespace(context=SimpleNamespace(context=m.context, event=event))
            )
        tool = SendChatMessageTool()
        accepted = None
        if failure_mode == "provider":
            m.core.provider_manager.inst_map.pop("gpt-mini")
            failed = await tool.call(contexts[0], session_id=b, text="failed A message")
            assert "Chat model is unavailable" in failed
        elif failure_mode == "full_queue":
            # Pause only the input listener; B's real model/tool run remains live.
            listener = webchat_queue_mgr._listener_tasks[b]
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
            queue = webchat_queue_mgr.get_or_create_queue(b)
            while not queue.full():
                queue.put_nowait(None)
            try:
                assert "queue is full" in await tool.call(
                    contexts[0], session_id=b, text="failed A message"
                )
            finally:
                while not queue.empty():
                    queue.get_nowait()
                webchat_queue_mgr._start_listener_if_needed(b)
        elif failure_mode == "queue_error":
            queue = webchat_queue_mgr.get_or_create_queue(b)

            def fail_enqueue(item):
                raise RuntimeError("injected enqueue failure")

            with monkeypatch.context() as scoped:
                scoped.setattr(queue, "put_nowait", fail_enqueue)
                with pytest.raises(RuntimeError, match="injected enqueue failure"):
                    await tool.call(contexts[0], session_id=b, text="failed A message")
        else:
            preparing = asyncio.Event()
            finish_failure = asyncio.Event()
            original_selection = m.service.validate_chat_selection

            async def delayed_failure(owner, sid, data, **kwargs):
                result = await original_selection(owner, sid, data, **kwargs)
                if data.get("message") == "failed A message":
                    preparing.set()
                    await finish_failure.wait()
                    raise ChatServiceError("delayed settings rejection")
                return result

            with monkeypatch.context() as scoped:
                scoped.setattr(m.service, "validate_chat_selection", delayed_failure)
                pending = asyncio.create_task(
                    tool.call(contexts[0], session_id=b, text="failed A message")
                )
                try:
                    await asyncio.wait_for(preparing.wait(), 5)
                    accepted = json.loads(
                        await tool.call(
                            contexts[1], session_id=b, text="independent C message"
                        )
                    )
                finally:
                    finish_failure.set()
                    failed = await pending
                assert "delayed settings rejection" in failed
        failed_chain = contexts[0].context.event.get_extra("_session_message_chain")
        assert failed_chain.count == 0
        assert b not in failed_chain.sessions
        assert recipient.get_extra("_session_message_chain") is (
            contexts[1].context.event.get_extra("_session_message_chain")
            if accepted
            else None
        )

        m.core.provider_manager.inst_map["gpt-mini"] = provider
        if accepted is None:
            accepted = json.loads(
                await tool.call(
                    contexts[1],
                    session_id=b,
                    text="independent C message",
                )
            )
        assert accepted["status"] == "accepted_follow_up"
        expect_c = True
        committed = contexts[1].context.event.get_extra("_session_message_chain")
        assert committed.count == 1
        assert recipient.get_extra("_session_message_chain") is committed
        # A later failure in the same chain cannot undo the accepted C send.
        m.core.provider_manager.inst_map.pop("gpt-mini")
        assert "Chat model is unavailable" in await tool.call(
            contexts[1],
            session_id=b,
            text="second C send fails",
        )
        assert committed.count == 1
        assert recipient.get_extra("_session_message_chain") is committed
        from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS

        runner = _ACTIVE_AGENT_RUNNERS[recipient.unified_msg_origin]
        await wait_until(lambda: bool(runner._pending_follow_ups))
        release.set()
        assert "C message received" in (await asyncio.wait_for(human, 5)).text
    finally:
        m.core.provider_manager.inst_map["gpt-mini"] = provider
        release.set()
        if not human.done():
            await asyncio.wait_for(asyncio.shield(human), 5)
        await asyncio.gather(*m.tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_accepted_chain_survives_announcement_failure(messaging, monkeypatch):
    """An exception after enqueue cannot turn an accepted delivery into a rollback.

    Args:
        messaging: Real SQLite and main Agent execution fixture.
        monkeypatch: Fails the announcement after its normal publication.
    """
    m = messaging
    a, b = m.ids

    async def receive(kwargs):
        return LLMResponse(role="assistant", completion_text="accepted output saved")

    m.providers["gpt-mini"] = ControlledProvider("gpt-mini", receive)
    account = DashboardPrincipal(
        m.user["account_id"], m.user["resource_owner"], "user", m.user["username"]
    )
    source = m.adapter.create_event(
        await m.adapter.convert_message(
            (
                account.resource_owner,
                a,
                {
                    "message": [{"type": "plain", "text": "source"}],
                    "message_id": "source",
                    "_dashboard_account": account,
                },
            )
        )
    )
    original = webchat_queue_mgr.put_system_event

    async def fail_announcement(cid, payload):
        result = await original(cid, payload)
        if payload.get("synthetic") and payload.get("type") == "run_started":
            raise RuntimeError("injected post-acceptance failure")
        return result

    monkeypatch.setattr(webchat_queue_mgr, "put_system_event", fail_announcement)
    accepted = await m.service.send_session_message(
        source, b, "accepted despite announcement failure"
    )
    assert accepted["status"] == "accepted"
    chain = source.get_extra("_session_message_chain")
    assert chain.count == 1 and b in chain.sessions
    await wait_until(lambda: not m.service.chat_runs_by_session)
    assert m.events[0].get_extra("_session_message_chain") is chain
    history = (
        await m.client.get(f"/api/v1/chat/sessions/{b}", headers=m.headers)
    ).json()["data"]["history"]
    assert "accepted output saved" in json.dumps(history)
    await m.service.stop_session(account.resource_owner, b)
    with pytest.raises(ChatServiceError, match="stopped by the user"):
        await m.service.send_session_message(source, b, "same chain cannot restart")
    assert chain.count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_during_validation", [False, True])
async def test_concurrent_admissions_recheck_limit_and_stop(
    messaging, monkeypatch, stop_during_validation
):
    """Overlapping validations still commit one final slot and respect user stop.

    Args:
        messaging: Real SQLite and main Agent execution fixture.
        monkeypatch: Pauses both sends inside the ordinary settings resolver.
        stop_during_validation: Stop a not-yet-accepted recipient while settings wait.
    """
    from astrbot.builtin_stars.session_messages.tools import SessionMessageChain

    m = messaging
    a, b = m.ids
    c = (
        await m.db.create_platform_session(creator=m.user["resource_owner"])
    ).session_id
    m.ids.append(c)

    async def receive(kwargs):
        return LLMResponse(role="assistant", completion_text="concurrent input saved")

    m.providers["gpt-mini"] = ControlledProvider("gpt-mini", receive)
    account = DashboardPrincipal(
        m.user["account_id"], m.user["resource_owner"], "user", m.user["username"]
    )
    source = m.adapter.create_event(
        await m.adapter.convert_message(
            (
                account.resource_owner,
                a,
                {
                    "message": [{"type": "plain", "text": "source"}],
                    "message_id": "source",
                    "_dashboard_account": account,
                },
            )
        )
    )
    chain = SessionMessageChain(count=7, sessions={a})
    source.set_extra("_session_message_chain", chain)
    callback = webchat_queue_mgr._listener_callback
    await webchat_queue_mgr.clear_listener()
    prepared = []
    release = asyncio.Event()
    original = m.service.validate_chat_selection

    async def pause_selection(owner, sid, data, **kwargs):
        result = await original(owner, sid, data, **kwargs)
        prepared.append(sid)
        await release.wait()
        return result

    monkeypatch.setattr(m.service, "validate_chat_selection", pause_selection)
    tasks = [
        asyncio.create_task(
            m.service.send_session_message(source, sid, "concurrent input")
        )
        for sid in (b, c)
    ]
    try:
        await wait_until(lambda: len(prepared) == 2)
        if stop_during_validation:
            await m.service.stop_session(account.resource_owner, b)
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        accepted = [result for result in results if isinstance(result, dict)]
        rejected = [
            result for result in results if isinstance(result, ChatServiceError)
        ]
        assert len(accepted) == len(rejected) == 1, results
        assert chain.count == 8
        assert (
            "stopped by the user" in str(rejected[0])
            if stop_during_validation
            else "limit reached" in str(rejected[0])
        )
        assert chain.sessions == {a, accepted[0]["session_id"]}
        if stop_during_validation:
            assert b in chain.stopped
            with pytest.raises(ChatServiceError, match="stopped by the user"):
                await m.service.send_session_message(source, b, "same chain retry")
        assert (
            sum(webchat_queue_mgr.get_or_create_queue(sid).qsize() for sid in (b, c))
            == 1
        )
        webchat_queue_mgr.set_listener(callback)
        await wait_until(lambda: not m.service.chat_runs_by_session)
        history = (
            await m.client.get(
                f"/api/v1/chat/sessions/{accepted[0]['session_id']}", headers=m.headers
            )
        ).json()["data"]["history"]
        assert "concurrent input saved" in json.dumps(history)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        webchat_queue_mgr.set_listener(callback)


@pytest.mark.asyncio
async def test_roundtrip_uses_real_main_tools_target_settings_and_history(messaging):
    """Verify bidirectional tool delivery, recipient choices and saved provenance.

    Args:
        messaging: Isolated production chat path and controlled model registry.
    """
    m = messaging
    a, b = m.ids
    reply_accepted = asyncio.Event()
    results = []

    class WaitForReply(FunctionTool):
        async def call(self, context, **kwargs):
            await reply_accepted.wait()
            await wait_until(
                lambda: bool(
                    context.context.extra["main_agent_runner"]._pending_follow_ups
                )
            )
            return "reply accepted"

    m.context.add_llm_tools(
        WaitForReply(
            name="wait_for_reply",
            description="Hold the sending run until reply admission.",
            parameters={"type": "object", "properties": {}},
        )
    )

    async def respond_a(kwargs):
        contexts = kwargs.get("contexts", [])
        text = json.dumps(contexts, ensure_ascii=False)
        tools = [item for item in contexts if item.get("role") == "tool"]
        if len(tools) == 0:
            return tool_call("list_chat_sessions", {})
        if len(tools) == 1:
            assert b in text
            return tool_call(
                "send_chat_message",
                {"session_id": b, "text": "/stop Please reply to A"},
            )
        if len(tools) == 2:
            assert "accepted" in text
            return tool_call("wait_for_reply", {})
        assert "reply from B" in text
        assert "server-verified source" in text
        assert "Incoming Agent messages" in text
        results.append("A received B")
        return LLMResponse(role="assistant", completion_text="A received B")

    async def respond_b(kwargs):
        text = json.dumps(kwargs.get("contexts", []), ensure_ascii=False)
        assert "Agent message; server-verified source" in text
        assert "Please reply to A" in text
        assert kwargs["model"] == "recipient-model"
        assert kwargs["llm_params"]["thinking_effort"] == "xhigh"
        assert "Recipient profile only" in text
        if not any(item.get("role") == "tool" for item in kwargs.get("contexts", [])):
            return tool_call(
                "send_chat_message", {"session_id": a, "text": "reply from B"}
            )
        assert "accepted_follow_up" in text
        reply_accepted.set()
        return LLMResponse(role="assistant", completion_text="B complete")

    m.providers.update(
        {
            "gpt-mini": ControlledProvider("gpt-mini", respond_a),
            "recipient": ControlledProvider("recipient", respond_b),
        }
    )
    m.core.provider_manager.inst_map.update(m.providers)
    m.core.provider_manager.providers_config.append(
        {
            "id": "recipient",
            "enable": True,
            "provider_source_id": "openai-source",
            "model": "recipient-model",
        }
    )
    recipient_profile = deepcopy(dict(m.core.astrbot_config))
    recipient_profile["test_persona_prompt"] = "Recipient profile only"
    m.core.astrbot_config_mgr.confs["recipient-profile"] = recipient_profile
    config_result = await m.client.put(
        f"/api/v1/chat/sessions/{b}/config",
        headers=m.headers,
        json={"config_id": "recipient-profile"},
    )
    assert config_result.json()["status"] == "ok", config_result.text
    project = await m.server.asgi_app.state.services.chat_projects.create_project(
        m.user["resource_owner"], {"title": "Recipient project"}
    )
    await m.db.add_session_to_project(b, project["project_id"], 0)
    response = await m.client.put(
        f"/api/v1/chat/sessions/{b}/selection",
        headers=m.headers,
        json={
            "selected_provider": "recipient",
            "selected_model": "recipient-model",
            "thinking_effort": "xhigh",
        },
    )
    assert response.json()["status"] == "ok", response.text
    response = await asyncio.wait_for(
        m.client.post(
            "/api/v1/chat",
            headers=m.headers,
            json={"session_id": a, "message": "start roundtrip"},
        ),
        15,
    )
    assert response.status_code == 200
    assert m.providers["gpt-mini"].calls, response.text
    await wait_until(lambda: bool(results))
    await wait_until(lambda: not m.service.chat_runs_by_session)
    assert all(task.exception() is None for task in m.tasks if task.done())
    for sid, source in ((b, a), (a, b)):
        history = (
            await m.client.get(f"/api/v1/chat/sessions/{sid}", headers=m.headers)
        ).json()["data"]["history"]
        incoming = [row for row in history if row["content"].get("agent_source")]
        assert len(incoming) == 1
        assert incoming[0]["content"]["agent_source"]["session_id"] == source
        assert (
            incoming[0]["content"]["agent_source"]["account_id"] == m.user["account_id"]
        )
        assert any(row["content"]["type"] == "bot" for row in history)
    assert (
        len([event for event in m.events if event.get_extra("_agent_message_source")])
        == 2
    )
    assert m.events[0].get_extra("_session_message_chain").count == 2
    assert (
        await m.db.get_project_by_session(b, m.user["resource_owner"])
    ).project_id == project["project_id"]


@pytest.mark.asyncio
async def test_rejections_stop_limit_and_human_restart(messaging, monkeypatch):
    """Verify access, human-choice admission, deletion races and stop boundaries.

    Args:
        messaging: Isolated production chat path.
        monkeypatch: Schedules deletion immediately before shared admission.
    """
    m = messaging
    a, b = m.ids
    account = DashboardPrincipal(
        m.user["account_id"], m.user["resource_owner"], "user", m.user["username"]
    )
    source = m.adapter.create_event(
        await m.adapter.convert_message(
            (
                account.resource_owner,
                a,
                {
                    "message": [{"type": "plain", "text": "source"}],
                    "message_id": "source",
                    "_dashboard_account": account,
                },
            )
        )
    )
    context = SimpleNamespace(context=SimpleNamespace(context=m.context, event=source))
    tool = SendChatMessageTool()
    foreign = await m.db.create_platform_session(creator=m.foreign["resource_owner"])
    archived = await m.db.create_platform_session(creator=account.resource_owner)
    await m.db.update_platform_session(archived.session_id, archived=1)
    for sid in (a, foreign.session_id, archived.session_id, "deleted", "side-thread"):
        result = await tool.call(context, session_id=sid, text="test")
        assert result.startswith("error:"), result
    assert "Only session_id and text" in await tool.call(
        context, session_id=b, text="test", sender_id="forged"
    )
    source.set_extra("_dashboard_account", {"account_id": account.account_id})
    assert "trusted" in await tool.call(context, session_id=b, text="test")
    source.set_extra("_dashboard_account", account)
    await sp.global_put(
        "tool_permissions", {"_default": {"send_chat_message": "admin"}}
    )
    guarded = (
        m.context.get_llm_tool_manager()
        .get_full_tool_set()
        .get_tool("send_chat_message")
    )
    assert "requires admin" in await guarded.call(
        context, session_id=b, text="not authorized"
    )
    await sp.global_put("tool_permissions", {})
    target = await m.db.get_platform_session_by_id(b)
    from astrbot.dashboard.services.chat_service import build_webchat_unified_msg_origin

    umo = build_webchat_unified_msg_origin(target)
    future = asyncio.get_running_loop().create_future()
    registry.add("waiting", umo, future, {"prompt": "Human only"}, 0, 99999999999)
    assert "waiting for a user" in await tool.call(context, session_id=b, text="yes")
    assert not future.done()
    registry.remove("waiting")
    m.service.chat_runs_by_session[b] = {"preparing"}
    assert "busy" in await tool.call(context, session_id=b, text="test")
    m.service.chat_runs_by_session.pop(b)

    async def reply(kwargs):
        return LLMResponse(role="assistant", completion_text="background complete")

    m.providers["gpt-mini"] = ControlledProvider("gpt-mini", reply)
    accepted = json.loads(
        await tool.call(context, session_id=b, text="background text")
    )
    assert accepted["status"] == "accepted"
    await wait_until(lambda: not m.service.chat_runs_by_session)
    assert m.providers["gpt-mini"].calls
    initial_history = (
        await m.client.get(f"/api/v1/chat/sessions/{b}", headers=m.headers)
    ).json()["data"]["history"]
    assert "background complete" in json.dumps(initial_history)
    # Delete after discovery, immediately before the shared admission entry.
    disappearing = await m.db.create_platform_session(creator=account.resource_owner)
    original_build = m.service.build_chat_stream

    async def delete_before_admission(*args, **kwargs):
        await m.db.delete_platform_session(disappearing.session_id)
        return await original_build(*args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(m.service, "build_chat_stream", delete_before_admission)
        assert "unavailable" in await tool.call(
            context, session_id=disappearing.session_id, text="racing deletion"
        )
    assert await m.db.get_platform_session_by_id(disappearing.session_id) is None
    chain = source.get_extra("_session_message_chain")
    await m.service.stop_session(account.resource_owner, b)
    assert "stopped by the user" in await tool.call(
        context, session_id=b, text="restart attempt"
    )
    # A real human starts a fresh turn even while the old chain remains stopped.
    response = await m.client.post(
        "/api/v1/chat",
        headers=m.headers,
        json={"session_id": b, "message": "human restart"},
    )
    assert "background complete" in response.text
    assert b in chain.stopped
    chain.stopped.clear()
    chain.count = 8
    assert "limit reached" in await tool.call(context, session_id=b, text="over limit")
    assert chain.count == 8
    from astrbot.core.goal.goal_service import build_goal_turn_event

    continuation = build_goal_turn_event(source, "goal continuation", "goal-test")
    assert continuation.get_extra("_session_message_chain") is chain
    history = (
        await m.client.get(f"/api/v1/chat/sessions/{b}", headers=m.headers)
    ).json()["data"]["history"]
    agent = next(row for row in history if row["content"].get("agent_source"))
    with pytest.raises(ChatServiceError, match="provenance"):
        await m.service.update_message(
            account.resource_owner,
            {"session_id": b, "message_id": agent["id"], "content": agent["content"]},
        )


@pytest.mark.asyncio
async def test_stop_between_acceptance_and_execution_keeps_input_but_does_not_run(
    messaging,
):
    m = messaging
    a, b = m.ids
    account = DashboardPrincipal(
        m.user["account_id"], m.user["resource_owner"], "user", m.user["username"]
    )
    source = m.adapter.create_event(
        await m.adapter.convert_message(
            (
                account.resource_owner,
                a,
                {
                    "message": [{"type": "plain", "text": "source"}],
                    "message_id": "source",
                    "_dashboard_account": account,
                },
            )
        )
    )
    await webchat_queue_mgr.clear_listener()
    accepted = await m.service.send_session_message(source, b, "do not run after stop")
    await m.service.stop_session(account.resource_owner, b)
    item = await webchat_queue_mgr.get_or_create_queue(b).get()
    event = m.adapter.create_event(await m.adapter.convert_message(item))
    stage = InternalAgentSubStage()
    stage._send_llm_error_message = AsyncMock(wraps=stage._send_llm_error_message)
    async for _ in stage.process(event, ""):
        pass
    await event.send(None)
    await m.service.chat_runs[accepted["run_id"]].task
    stage._send_llm_error_message.assert_awaited_once()
    history = (
        await m.client.get(f"/api/v1/chat/sessions/{b}", headers=m.headers)
    ).json()["data"]["history"]
    assert any(row["content"].get("agent_source") for row in history)
    assert "chain stopped" in json.dumps(history)


@pytest.mark.asyncio
async def test_real_tool_loop_hits_finite_chain_limit(messaging):
    """Exercise the fixed message limit through actual model tool calls.

    Args:
        messaging: Isolated production chat path.
    """
    m = messaging
    a, b = m.ids
    limit_seen = asyncio.Event()

    async def bounce(kwargs):
        text = json.dumps(kwargs.get("contexts", []), ensure_ascii=False)
        if "limit reached" in text:
            limit_seen.set()
            return LLMResponse(
                role="assistant", completion_text="automatic chain ended"
            )
        return tool_call(
            "send_chat_message", {"session_id": b, "text": "bounded automatic message"}
        )

    async def receive(kwargs):
        return LLMResponse(role="assistant", completion_text="recipient processed text")

    # Repeated model calls are real sends; no test edits the server counter.
    m.providers["gpt-mini"] = ControlledProvider("gpt-mini", bounce)
    m.providers["recipient"] = ControlledProvider("recipient", receive)
    m.core.provider_manager.inst_map.update(m.providers)
    m.core.provider_manager.providers_config.append(
        {
            "id": "recipient",
            "enable": True,
            "provider_source_id": "openai-source",
            "model": "recipient",
        }
    )
    selected = await m.client.put(
        f"/api/v1/chat/sessions/{b}/selection",
        headers=m.headers,
        json={"selected_provider": "recipient", "selected_model": "recipient"},
    )
    assert selected.json()["status"] == "ok"
    response = await asyncio.wait_for(
        m.client.post(
            "/api/v1/chat",
            headers=m.headers,
            json={"session_id": a, "message": "bounded chain"},
        ),
        15,
    )
    assert limit_seen.is_set(), response.text
    assert m.events[0].get_extra("_session_message_chain").count == 8
    await wait_until(lambda: not m.service.chat_runs_by_session)
    history = (
        await m.client.get(f"/api/v1/chat/sessions/{b}", headers=m.headers)
    ).json()["data"]["history"]
    assert len([row for row in history if row["content"].get("agent_source")]) == 8


@pytest.mark.asyncio
async def test_real_waiting_choice_rejects_agent_and_preserves_human_answer(messaging):
    """Keep a real blocking choice pending until its human answer arrives.

    Args:
        messaging: Isolated production chat path.
    """
    from astrbot.builtin_stars.ask_user_choice.ask_user_choice_tool import (
        AskUserChoiceTool,
    )
    from astrbot.builtin_stars.ask_user_choice.main import Main as ChoiceMain
    from astrbot.dashboard.services.chat_service import build_webchat_unified_msg_origin

    m = messaging
    a, b = m.ids
    m.context.add_llm_tools(AskUserChoiceTool())

    async def choose(kwargs):
        if not any(item.get("role") == "tool" for item in kwargs.get("contexts", [])):
            return tool_call(
                "ask_user_choice",
                {
                    "prompt": "Human decision",
                    "options": [
                        {"id": "yes", "label": "Yes"},
                        {"id": "no", "label": "No"},
                    ],
                },
            )
        return LLMResponse(role="assistant", completion_text="human answer received")

    m.providers["gpt-mini"] = ControlledProvider("gpt-mini", choose)
    request = asyncio.create_task(
        m.client.post(
            "/api/v1/chat",
            headers=m.headers,
            json={"session_id": b, "message": "ask human"},
        )
    )
    umo = build_webchat_unified_msg_origin(await m.db.get_platform_session_by_id(b))
    await wait_until(lambda: registry.find_pending_by_umo(umo) is not None)
    account = DashboardPrincipal(
        m.user["account_id"], m.user["resource_owner"], "user", m.user["username"]
    )
    source = m.adapter.create_event(
        await m.adapter.convert_message(
            (
                account.resource_owner,
                a,
                {
                    "message": [{"type": "plain", "text": "source"}],
                    "message_id": "source",
                    "_dashboard_account": account,
                },
            )
        )
    )
    with pytest.raises(ChatServiceError, match="waiting for a user"):
        await m.service.send_session_message(source, b, "yes")
    pending = registry.find_pending_by_umo(umo)
    assert pending is not None and not pending.future.done()
    # Defense in depth for an Agent input already accepted before the question.
    from astrbot.builtin_stars.session_messages.tools import (
        AgentMessageSource,
        SessionMessageChain,
    )

    late = m.adapter.create_event(
        await m.adapter.convert_message(
            (
                account.resource_owner,
                b,
                {
                    "message": [{"type": "plain", "text": "yes"}],
                    "message_id": "late",
                    "_agent_message_source": AgentMessageSource(
                        account, a, "A", SessionMessageChain()
                    ),
                },
            )
        )
    )
    await ChoiceMain(m.context).on_message(late)
    assert not pending.future.done()
    registry.resolve(
        pending.request_id, {"choice_id": "yes", "free_text": "human approved"}
    )
    response = await asyncio.wait_for(request, 5)
    assert "human answer received" in response.text
