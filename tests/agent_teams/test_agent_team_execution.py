"""Agent team node execution registry + EventBus token routing tests (spec §2.3).

Covers `AgentTeamExecutionRegistry` / `NodeExecutionBinding`: per-turn token
issuance, umo-scoped resolution (forged/foreign token defense), and
runner-owned lifecycle (resolve does not unregister; unregister is idempotent).
Also covers EventBus token routing (profile scheduler selection, fallback to
umo-based routing) and the webchat adapter `execution_token` passthrough.
"""

import asyncio
import contextlib
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.agent_team_execution import (
    AgentTeamExecutionRegistry,
    NodeExecutionBinding,
)
from astrbot.core.agent_team_tools import AgentTeamToolRegistry, build_team_tools
from astrbot.core.astr_main_agent import (
    _ensure_persona_and_skills,
    normalize_umo_for_workspace,
)
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.event_bus import EventBus
from astrbot.core.pipeline.process_stage.method.agent_sub_stages import third_party
from astrbot.core.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    PlatformMetadata,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import WebChatQueueMgr
from astrbot.core.provider.entities import ProviderRequest
from astrbot.dashboard.services.agent_team_ports import TeamPorts, build_ports_for_test
from astrbot.dashboard.services.agent_team_run_service import (
    AgentTeamRunService,
    DAGRunner,
    RunEventBus,
)
from astrbot.dashboard.services.agent_team_service import (
    AgentTeamService,
    AgentTeamsServiceError,
)
from tests.agent_teams.test_agent_team_service import (
    MEMBERS,
    FakeChatService,
    FakeCoreLifecycle,
)

UMO = "webchat:FriendMessage:conv-1"


def make_binding(**overrides) -> NodeExecutionBinding:
    """Build a valid binding with defaults, overridable per test.

    Args:
        **overrides: Field values replacing the defaults.

    Returns:
        A `NodeExecutionBinding` instance.
    """
    fields = {
        "run_id": "run-1",
        "team_id": "team-1",
        "member_id": "coder",
        "node_id": "node-1",
        "umo": UMO,
        "owner_username": "admin",
        "config_id": "cfg-default",
    }
    fields.update(overrides)
    return NodeExecutionBinding(**fields)


def test_register_resolve_roundtrip():
    binding = make_binding(persona_id="p1", tools=["t1"], skills=["s1"])
    token = AgentTeamExecutionRegistry.register(binding)
    try:
        # uuid4 hex token
        assert re.fullmatch(r"[0-9a-f]{32}", token)
        resolved = AgentTeamExecutionRegistry.resolve(token, umo=UMO)
        assert resolved is binding
        assert resolved.run_id == "run-1"
        assert resolved.team_id == "team-1"
        assert resolved.member_id == "coder"
        assert resolved.node_id == "node-1"
        assert resolved.owner_username == "admin"
        assert resolved.config_id == "cfg-default"
        assert resolved.persona_id == "p1"
        assert resolved.tools == ["t1"]
        assert resolved.skills == ["s1"]
    finally:
        AgentTeamExecutionRegistry.unregister(token)


def test_register_issues_distinct_tokens():
    token_a = AgentTeamExecutionRegistry.register(make_binding())
    token_b = AgentTeamExecutionRegistry.register(make_binding(node_id="node-2"))
    try:
        assert token_a != token_b
    finally:
        AgentTeamExecutionRegistry.unregister(token_a)
        AgentTeamExecutionRegistry.unregister(token_b)


def test_resolve_unknown_token_returns_none():
    assert AgentTeamExecutionRegistry.resolve("0" * 32) is None


def test_resolve_umo_mismatch_returns_none():
    token = AgentTeamExecutionRegistry.register(make_binding())
    try:
        # A token forged/replayed from another conversation must not resolve.
        assert (
            AgentTeamExecutionRegistry.resolve(token, umo="webchat:FriendMessage:other")
            is None
        )
    finally:
        AgentTeamExecutionRegistry.unregister(token)


def test_resolve_without_umo_skips_umo_check():
    token = AgentTeamExecutionRegistry.register(make_binding())
    try:
        resolved = AgentTeamExecutionRegistry.resolve(token)
        assert resolved is not None
        assert resolved.member_id == "coder"
    finally:
        AgentTeamExecutionRegistry.unregister(token)


def test_resolve_does_not_unregister():
    """Resolution must not consume the token — the runner owns the lifecycle."""
    token = AgentTeamExecutionRegistry.register(make_binding())
    try:
        for _ in range(2):
            resolved = AgentTeamExecutionRegistry.resolve(token, umo=UMO)
            assert resolved is not None
    finally:
        AgentTeamExecutionRegistry.unregister(token)


def test_unregister_idempotent():
    token = AgentTeamExecutionRegistry.register(make_binding())
    AgentTeamExecutionRegistry.unregister(token)
    AgentTeamExecutionRegistry.unregister(token)  # must not raise
    assert AgentTeamExecutionRegistry.resolve(token) is None


# ---------------------------------------------------------------------------
# EventBus token routing (spec §2.3)
# ---------------------------------------------------------------------------


class RecordingScheduler:
    """Minimal PipelineScheduler stand-in that records executed events."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.events: list[AstrMessageEvent] = []

    async def execute(self, event: AstrMessageEvent) -> None:
        self.events.append(event)


class StubConfigManager:
    """Minimal AstrBotConfigManager stand-in: every umo maps to `default`."""

    def get_conf_info(self, umo: str) -> dict:
        return {"id": "default", "name": "default", "path": ""}


class TeamNodeEvent(AstrMessageEvent):
    """Synthetic event for dispatch tests (CronMessageEvent style)."""

    def __init__(self, umo: str, extras: dict | None = None) -> None:
        platform_id, message_type, session_id = umo.split(":", 2)
        platform_meta = PlatformMetadata(
            name=platform_id, description="test", id=platform_id
        )
        msg_obj = AstrBotMessage()
        msg_obj.type = MessageType(message_type)
        msg_obj.self_id = "bot"
        msg_obj.session_id = session_id
        msg_obj.message_id = "m-1"
        msg_obj.sender = MessageMember(user_id="user", nickname="user")
        msg_obj.message = []
        msg_obj.message_str = "hi"
        msg_obj.raw_message = "hi"
        super().__init__("hi", msg_obj, platform_meta, session_id)
        if extras:
            self._extras.update(extras)

    async def send(self, message) -> None:
        return None

    async def send_streaming(self, generator, use_fallback: bool = False) -> None:
        return None


async def dispatch_one(
    bus: EventBus, event: AstrMessageEvent, expected: RecordingScheduler
) -> None:
    """Queue one event through `bus.dispatch()` until `expected` records it.

    Args:
        bus: The EventBus under test.
        event: The event to push into the queue.
        expected: The scheduler expected to receive the event.

    Raises:
        AssertionError: If the event is not delivered within a short deadline.
    """
    dispatch_task = asyncio.create_task(bus.dispatch())
    try:
        await bus.event_queue.put(event)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.0
        while not expected.events:
            if loop.time() > deadline:
                raise AssertionError(f"event not delivered to {expected.name}")
            await asyncio.sleep(0.01)
    finally:
        dispatch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dispatch_task


def make_bus() -> tuple[EventBus, RecordingScheduler, RecordingScheduler]:
    """Build an EventBus with a default and a profile recording scheduler.

    Returns:
        A `(bus, default_scheduler, profile_scheduler)` tuple; the profile
        scheduler is keyed by `make_binding()`'s default `config_id`.
    """
    default = RecordingScheduler("default")
    profile = RecordingScheduler("cfg-default")
    bus = EventBus(
        asyncio.Queue(),
        {"default": default, "cfg-default": profile},
        StubConfigManager(),
    )
    return bus, default, profile


@pytest.mark.asyncio
async def test_eventbus_routes_token_event_to_profile_scheduler():
    binding = make_binding()
    token = AgentTeamExecutionRegistry.register(binding)
    bus, default, profile = make_bus()
    event = TeamNodeEvent(UMO, extras={"execution_token": token})

    await dispatch_one(bus, event, profile)

    assert profile.events == [event]
    assert default.events == []
    assert event.get_extra("agent_team_execution") is binding
    # EventBus must not unregister — the runner owns the token lifecycle.
    assert AgentTeamExecutionRegistry.resolve(token, umo=UMO) is binding
    AgentTeamExecutionRegistry.unregister(token)


@pytest.mark.asyncio
async def test_eventbus_foreign_token_falls_back_to_default():
    # Token is bound to UMO but the event arrives from another conversation.
    token = AgentTeamExecutionRegistry.register(make_binding())
    bus, default, profile = make_bus()
    event = TeamNodeEvent(
        "webchat:FriendMessage:conv-2", extras={"execution_token": token}
    )

    await dispatch_one(bus, event, default)

    assert default.events == [event]
    assert profile.events == []
    assert event.get_extra("agent_team_execution") is None
    AgentTeamExecutionRegistry.unregister(token)


@pytest.mark.asyncio
async def test_eventbus_unknown_token_falls_back_to_default():
    bus, default, profile = make_bus()
    event = TeamNodeEvent(UMO, extras={"execution_token": "0" * 32})

    await dispatch_one(bus, event, default)

    assert default.events == [event]
    assert profile.events == []
    assert event.get_extra("agent_team_execution") is None


@pytest.mark.asyncio
async def test_eventbus_unknown_config_id_falls_back_with_binding_set():
    # Defense-in-depth: a resolved binding whose config profile has no
    # scheduler falls back to umo routing, but the binding extra stays set.
    token = AgentTeamExecutionRegistry.register(make_binding(config_id="cfg-gone"))
    bus, default, profile = make_bus()
    event = TeamNodeEvent(UMO, extras={"execution_token": token})

    await dispatch_one(bus, event, default)

    assert default.events == [event]
    assert profile.events == []
    resolved_extra = event.get_extra("agent_team_execution")
    assert resolved_extra is not None
    assert resolved_extra.config_id == "cfg-gone"
    AgentTeamExecutionRegistry.unregister(token)


@pytest.mark.asyncio
async def test_eventbus_no_token_uses_default_routing():
    # Ordinary chat events carry no execution_token: routing must be a no-op.
    bus, default, profile = make_bus()
    event = TeamNodeEvent(UMO)

    await dispatch_one(bus, event, default)

    assert default.events == [event]
    assert profile.events == []
    assert event.get_extra("agent_team_execution") is None


# ---------------------------------------------------------------------------
# Webchat adapter passthrough (spec §2.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webchat_adapter_passthrough_execution_token():
    adapter = WebChatAdapter(
        {"id": "webchat", "type": "webchat", "enable": True},
        {},
        asyncio.Queue(),
    )
    abm = await adapter.convert_message(
        ("user", "conv-1", {"execution_token": "tok-123", "message": []})
    )

    event = adapter.create_event(abm)

    assert event.get_extra("execution_token") == "tok-123"


# ---------------------------------------------------------------------------
# Node execution overrides in _ensure_persona_and_skills (spec §2.3)
# ---------------------------------------------------------------------------


class RecordingPersonaManager:
    """Persona manager stand-in recording resolve_selected_persona kwargs."""

    def __init__(self, resolved=None, v3_by_id=None) -> None:
        self.personas_v3 = []
        self.resolve_calls: list[dict] = []
        self._resolved = resolved or (None, None, None, False)
        self._v3_by_id = v3_by_id or {}

    async def resolve_selected_persona(self, **kwargs):
        self.resolve_calls.append(kwargs)
        return self._resolved

    def get_persona_v3_by_id(self, persona_id):
        return self._v3_by_id.get(persona_id)


class FakeToolManager:
    """Minimal LLM tool manager over a fixed tool list."""

    def __init__(self, tools) -> None:
        self.func_list = list(tools)

    def get_full_tool_set(self) -> ToolSet:
        return ToolSet(tools=list(self.func_list))

    def get_func(self, name: str):
        return next((t for t in self.func_list if t.name == name), None)


class FakePluginContext:
    """Minimal Context stand-in for _ensure_persona_and_skills."""

    def __init__(self, persona_manager, tool_manager) -> None:
        self.persona_manager = persona_manager
        self.subagent_orchestrator = None
        self._tool_manager = tool_manager

    def get_llm_tool_manager(self):
        return self._tool_manager

    def get_config(self) -> dict:
        # No subagent_orchestrator section: the subagent block short-circuits.
        return {}


def make_two_tools() -> list[FunctionTool]:
    """Two distinguishable persona-default tools."""
    return [
        FunctionTool(
            name="tool_a",
            description="tool a",
            parameters={"type": "object", "properties": {}},
        ),
        FunctionTool(
            name="tool_b",
            description="tool b",
            parameters={"type": "object", "properties": {}},
        ),
    ]


def make_override_req(persona_id=None) -> ProviderRequest:
    """Build a ProviderRequest whose conversation carries a persona id."""
    req = ProviderRequest(prompt="hi")
    req.conversation = SimpleNamespace(persona_id=persona_id)
    return req


def binding_event(binding: NodeExecutionBinding | None) -> "TeamNodeEvent":
    """Build a TeamNodeEvent carrying the binding as the execution extra."""
    extras = {"agent_team_execution": binding} if binding else {}
    return TeamNodeEvent(UMO, extras=extras)


@pytest.fixture
def skills_env(tmp_path, monkeypatch):
    """Isolate skill discovery paths; create global skills alpha and beta.

    Returns:
        The workspaces root directory (empty; tests may add workspace skills).
    """
    data_dir = tmp_path / "data"
    global_skills_dir = tmp_path / "global_skills"
    plugins_dir = tmp_path / "plugins"
    workspaces_dir = tmp_path / "workspaces"
    for path in (data_dir, global_skills_dir, plugins_dir):
        path.mkdir(parents=True, exist_ok=True)

    for name, desc in (
        ("skill-alpha", "Alpha skill description."),
        ("skill-beta", "Beta skill description."),
    ):
        skill_dir = global_skills_dir / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\ndescription: {desc}\n---\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "astrbot.core.skills.skill_manager.get_astrbot_data_path",
        lambda: str(data_dir),
    )
    monkeypatch.setattr(
        "astrbot.core.skills.skill_manager.get_astrbot_skills_path",
        lambda: str(global_skills_dir),
    )
    monkeypatch.setattr(
        "astrbot.core.skills.skill_manager.get_astrbot_plugin_path",
        lambda: str(plugins_dir),
    )
    import astrbot.core.astr_main_agent as ama

    monkeypatch.setattr(ama, "get_astrbot_workspaces_path", lambda: str(workspaces_dir))
    return workspaces_dir


@pytest.mark.asyncio
async def test_node_persona_override_skips_resolution():
    """A node persona id resolves directly; conversation resolution is skipped."""
    node_persona = {"name": "node-persona", "prompt": "NODE PERSONA PROMPT"}
    pm = RecordingPersonaManager(v3_by_id={"node-persona": node_persona})
    ctx = FakePluginContext(pm, FakeToolManager([]))
    binding = make_binding(persona_id="node-persona", config_id="cfg-team")
    req = make_override_req(persona_id="conv-persona")

    await _ensure_persona_and_skills(req, {}, ctx, binding_event(binding))

    assert "NODE PERSONA PROMPT" in req.system_prompt
    assert pm.resolve_calls == []


@pytest.mark.asyncio
async def test_node_persona_not_found_falls_back_to_resolution():
    """An unknown node persona falls back to resolution with config_id."""
    resolved_persona = {"name": "conv-persona", "prompt": "CONV PERSONA PROMPT"}
    pm = RecordingPersonaManager(
        resolved=("conv-persona", resolved_persona, None, False)
    )
    ctx = FakePluginContext(pm, FakeToolManager([]))
    binding = make_binding(persona_id="ghost-persona", config_id="cfg-team")
    req = make_override_req(persona_id="conv-persona")

    await _ensure_persona_and_skills(req, {}, ctx, binding_event(binding))

    assert "CONV PERSONA PROMPT" in req.system_prompt
    assert len(pm.resolve_calls) == 1
    assert pm.resolve_calls[0]["config_id"] == "cfg-team"


@pytest.mark.asyncio
async def test_node_config_id_threaded_to_resolution():
    """A binding config_id reaches resolve_selected_persona as a kwarg."""
    pm = RecordingPersonaManager()
    ctx = FakePluginContext(pm, FakeToolManager([]))
    binding = make_binding(config_id="cfg-team")
    req = make_override_req()

    await _ensure_persona_and_skills(req, {}, ctx, binding_event(binding))

    assert len(pm.resolve_calls) == 1
    assert pm.resolve_calls[0]["config_id"] == "cfg-team"


@pytest.mark.asyncio
async def test_node_tools_allowlist_filters_all_injected_tools():
    """The whitelist binds after persona AND team tools were merged."""
    pm = RecordingPersonaManager()
    ctx = FakePluginContext(pm, FakeToolManager(make_two_tools()))
    AgentTeamToolRegistry.register(UMO, build_team_tools(["a", "b"], None, None))
    try:
        binding = make_binding(tools=["tool_a", "team_dispatch"])
        req = make_override_req()

        await _ensure_persona_and_skills(req, {}, ctx, binding_event(binding))

        assert req.func_tool.names() == ["tool_a", "team_dispatch"]
    finally:
        AgentTeamToolRegistry.unregister(UMO)


@pytest.mark.asyncio
async def test_node_tools_empty_list_empties_toolset():
    """An empty node tools list removes every injected tool."""
    pm = RecordingPersonaManager()
    ctx = FakePluginContext(pm, FakeToolManager(make_two_tools()))
    AgentTeamToolRegistry.register(UMO, build_team_tools(["a"], None, None))
    try:
        binding = make_binding(tools=[])
        req = make_override_req()

        await _ensure_persona_and_skills(req, {}, ctx, binding_event(binding))

        assert len(req.func_tool) == 0
    finally:
        AgentTeamToolRegistry.unregister(UMO)


@pytest.mark.asyncio
async def test_node_tools_none_keeps_default_toolset():
    """tools=None inherits the persona default plus team tools untouched."""
    pm = RecordingPersonaManager()
    ctx = FakePluginContext(pm, FakeToolManager(make_two_tools()))
    AgentTeamToolRegistry.register(UMO, build_team_tools(["a", "b"], None, None))
    try:
        binding = make_binding()
        req = make_override_req()

        await _ensure_persona_and_skills(req, {}, ctx, binding_event(binding))

        assert set(req.func_tool.names()) == {
            "tool_a",
            "tool_b",
            "team_dispatch",
            "team_finish",
        }
    finally:
        AgentTeamToolRegistry.unregister(UMO)


@pytest.mark.asyncio
async def test_node_skills_empty_empties_merged_skills(skills_env):
    """An empty node skills list empties persona AND workspace-merged skills."""
    workspaces_dir = skills_env
    workspace_root = workspaces_dir / normalize_umo_for_workspace(UMO)
    ws_skill_dir = workspace_root / "skills" / "skill-ws"
    ws_skill_dir.mkdir(parents=True)
    (ws_skill_dir / "SKILL.md").write_text(
        "---\ndescription: Workspace skill description.\n---\n",
        encoding="utf-8",
    )

    pm = RecordingPersonaManager()
    ctx = FakePluginContext(pm, FakeToolManager([]))
    binding = make_binding(skills=[])
    req = make_override_req()

    await _ensure_persona_and_skills(req, {}, ctx, binding_event(binding))

    assert "## Skills" not in req.system_prompt
    assert "skill-alpha" not in req.system_prompt
    assert "skill-ws" not in req.system_prompt


@pytest.mark.asyncio
async def test_node_skills_allowlist_filters(skills_env):
    """A non-empty node skills list is an allowlist over discovered skills."""
    pm = RecordingPersonaManager()
    ctx = FakePluginContext(pm, FakeToolManager([]))
    binding = make_binding(skills=["skill-beta"])
    req = make_override_req()

    await _ensure_persona_and_skills(req, {}, ctx, binding_event(binding))

    assert "**skill-alpha**" not in req.system_prompt
    assert "**skill-beta**" in req.system_prompt


@pytest.mark.asyncio
async def test_node_skills_none_keeps_defaults(skills_env):
    """skills=None inherits all discovered skills untouched."""
    pm = RecordingPersonaManager()
    ctx = FakePluginContext(pm, FakeToolManager([]))
    binding = make_binding()
    req = make_override_req()

    await _ensure_persona_and_skills(req, {}, ctx, binding_event(binding))

    assert "**skill-alpha**" in req.system_prompt
    assert "**skill-beta**" in req.system_prompt


@pytest.mark.asyncio
async def test_no_binding_keeps_default_behavior(skills_env):
    """Without the execution extra, resolution/persona/tools/skills are default."""
    pm = RecordingPersonaManager()
    ctx = FakePluginContext(pm, FakeToolManager(make_two_tools()))
    req = make_override_req()

    await _ensure_persona_and_skills(req, {}, ctx, binding_event(None))

    assert len(pm.resolve_calls) == 1
    assert pm.resolve_calls[0].get("config_id") is None
    assert set(req.func_tool.names()) == {"tool_a", "tool_b"}
    assert "**skill-alpha**" in req.system_prompt
    assert "**skill-beta**" in req.system_prompt


@pytest.mark.asyncio
async def test_resolve_selected_persona_prefers_config_profile(monkeypatch):
    """resolve_selected_persona resolves the default persona from the profile."""
    from astrbot.core import persona_mgr as persona_mgr_module
    from astrbot.core.persona_mgr import PersonaManager

    mgr = PersonaManager.__new__(PersonaManager)
    mgr.personas_v3 = [{"name": "cfg-persona", "prompt": "P"}]
    profile_conf = {
        "agent_runner": {
            "runner_type": "remote",
            "config": {"persona_id": "cfg-persona"},
        }
    }
    get_conf_calls: list[str] = []
    mgr.acm = SimpleNamespace(
        confs={"cfg-team": profile_conf},
        get_conf=lambda umo: get_conf_calls.append(umo) or {"agent_runner": {}},
    )
    monkeypatch.setattr(persona_mgr_module.sp, "get_async", AsyncMock(return_value={}))

    persona_id, persona, _, _ = await mgr.resolve_selected_persona(
        umo=UMO,
        conversation_persona_id=None,
        platform_name="webchat",
        config_id="cfg-team",
    )

    assert persona_id == "cfg-persona"
    assert persona is mgr.personas_v3[0]
    assert get_conf_calls == []


@pytest.mark.asyncio
async def test_resolve_selected_persona_unknown_config_id_falls_back(monkeypatch):
    """An unknown config_id falls back to the umo config; None never hits confs."""
    from astrbot.core import persona_mgr as persona_mgr_module
    from astrbot.core.persona_mgr import PersonaManager

    mgr = PersonaManager.__new__(PersonaManager)
    mgr.personas_v3 = []
    umo_conf = {
        "agent_runner": {
            "runner_type": "remote",
            "config": {"persona_id": "umo-persona"},
        }
    }
    get_conf_calls: list[str] = []
    mgr.acm = SimpleNamespace(
        confs={"cfg-team": {"agent_runner": {}}},
        get_conf=lambda umo: get_conf_calls.append(umo) or umo_conf,
    )
    monkeypatch.setattr(persona_mgr_module.sp, "get_async", AsyncMock(return_value={}))

    _, persona, _, _ = await mgr.resolve_selected_persona(
        umo=UMO,
        conversation_persona_id=None,
        platform_name="webchat",
        config_id="cfg-gone",
    )
    assert get_conf_calls == [UMO]
    assert persona is None  # "umo-persona" not in empty personas_v3

    get_conf_calls.clear()
    await mgr.resolve_selected_persona(
        umo=UMO,
        conversation_persona_id=None,
        platform_name="webchat",
    )
    assert get_conf_calls == [UMO]


# ---------------------------------------------------------------------------
# Third-party runner persona error reply (spec §2.3)
# ---------------------------------------------------------------------------


def third_party_stage(
    pm: RecordingPersonaManager,
) -> "third_party.ThirdPartyAgentSubStage":
    """Build the third-party sub stage over the given persona manager.

    Args:
        pm: The persona manager stand-in the stage must consult.

    Returns:
        An initialized-enough stage (only `ctx` is needed by the persona
        error-reply resolution).
    """
    stage = third_party.ThirdPartyAgentSubStage()
    stage.ctx = SimpleNamespace(
        plugin_manager=SimpleNamespace(
            context=SimpleNamespace(
                conversation_manager=SimpleNamespace(
                    get_curr_conversation_id=AsyncMock(return_value=None)
                ),
                persona_manager=pm,
            )
        )
    )
    return stage


@pytest.mark.asyncio
async def test_third_party_persona_error_reply_uses_node_persona():
    """A bound node persona supplies the custom error reply directly."""
    node_persona = {"name": "node-persona", "custom_error_message": "NODE ERROR REPLY"}
    pm = RecordingPersonaManager(v3_by_id={"node-persona": node_persona})
    stage = third_party_stage(pm)
    event = binding_event(make_binding(persona_id="node-persona", config_id="cfg-team"))

    message = await stage._resolve_persona_custom_error_message(event)

    assert message == "NODE ERROR REPLY"
    assert pm.resolve_calls == []


@pytest.mark.asyncio
async def test_third_party_persona_error_reply_unknown_node_persona_falls_back():
    """An unknown node persona falls back to resolution with the config_id."""
    resolved_persona = {
        "name": "cfg-persona",
        "custom_error_message": "CFG ERROR REPLY",
    }
    pm = RecordingPersonaManager(
        resolved=("cfg-persona", resolved_persona, None, False)
    )
    stage = third_party_stage(pm)
    event = binding_event(
        make_binding(persona_id="ghost-persona", config_id="cfg-team")
    )

    message = await stage._resolve_persona_custom_error_message(event)

    assert message == "CFG ERROR REPLY"
    assert pm.resolve_calls[0]["config_id"] == "cfg-team"


@pytest.mark.asyncio
async def test_third_party_persona_error_reply_threads_node_config_id():
    """A binding config_id (no persona override) reaches persona resolution."""
    resolved_persona = {
        "name": "cfg-persona",
        "custom_error_message": "CFG ERROR REPLY",
    }
    pm = RecordingPersonaManager(
        resolved=("cfg-persona", resolved_persona, None, False)
    )
    stage = third_party_stage(pm)
    event = binding_event(make_binding(config_id="cfg-team"))

    message = await stage._resolve_persona_custom_error_message(event)

    assert message == "CFG ERROR REPLY"
    assert len(pm.resolve_calls) == 1
    assert pm.resolve_calls[0]["config_id"] == "cfg-team"


# ---------------------------------------------------------------------------
# Workflow execution validation + runner config checks (spec §2.3)
# ---------------------------------------------------------------------------


async def make_validation_service(tmp_path, confs=None, personas=None):
    """Build an AgentTeamService whose lifecycle knows the given profiles."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    return db, AgentTeamService(
        db=db,
        core_lifecycle=FakeCoreLifecycle(confs=confs, personas=personas),
        chat_service=FakeChatService(),
    )


async def make_validation_team(svc):
    return await svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )


def exec_graph(team: dict, n2_execution) -> dict:
    """Two-node linear graph; only n2 carries an execution block."""
    ids = [m["member_id"] for m in team["members"]]
    nodes = [
        {"id": "n1", "member_id": ids[0], "task": "调研"},
        {"id": "n2", "member_id": ids[1], "task": "写作 {{n1}}"},
    ]
    if n2_execution is not None:
        nodes[1]["execution"] = n2_execution
    return {"nodes": nodes, "edges": [{"from": "n1", "to": "n2"}]}


@pytest.mark.asyncio
async def test_workflow_save_rejects_unknown_config_id(tmp_path):
    _, svc = await make_validation_service(tmp_path, confs={"cfg-live": {}})
    team = await make_validation_team(svc)

    with pytest.raises(
        AgentTeamsServiceError, match="节点 n2 的配置档案不存在: cfg-gone"
    ):
        await svc.create_workflow(
            "alice",
            team["team_id"],
            {"name": "w", "graph": exec_graph(team, {"config_id": "cfg-gone"})},
        )


@pytest.mark.asyncio
async def test_workflow_save_rejects_unknown_persona_id(tmp_path):
    _, svc = await make_validation_service(tmp_path, confs={"cfg-live": {}})
    team = await make_validation_team(svc)

    with pytest.raises(AgentTeamsServiceError, match="节点 n2"):
        await svc.create_workflow(
            "alice",
            team["team_id"],
            {"name": "w", "graph": exec_graph(team, {"persona_id": "ghost"})},
        )


@pytest.mark.asyncio
async def test_workflow_save_accepts_valid_execution(tmp_path):
    """A resolvable execution block saves verbatim; unknown tool/skill names
    are NOT rejected at save time (availability is dynamic)."""
    _, svc = await make_validation_service(
        tmp_path, confs={"cfg-live": {}}, personas=[{"name": "p1", "prompt": "x"}]
    )
    team = await make_validation_team(svc)
    graph = exec_graph(
        team,
        {
            "config_id": "cfg-live",
            "persona_id": "p1",
            "tools": ["ghost_tool"],
            "skills": [],
        },
    )

    wf = await svc.create_workflow(
        "alice", team["team_id"], {"name": "w", "graph": graph}
    )

    assert wf["graph"]["nodes"][1]["execution"]["tools"] == ["ghost_tool"]


@pytest.mark.asyncio
async def test_workflow_save_accepts_empty_execution(tmp_path):
    """The whole block and every field within it are optional."""
    _, svc = await make_validation_service(tmp_path, confs={"cfg-live": {}})
    team = await make_validation_team(svc)

    wf = await svc.create_workflow(
        "alice", team["team_id"], {"name": "w", "graph": exec_graph(team, {})}
    )

    assert wf["graph"]["nodes"][1]["execution"] == {}


@pytest.mark.asyncio
async def test_workflow_save_rejects_malformed_execution(tmp_path):
    """Non-dict blocks and malformed tools/skills lists surface as 400-grade
    service errors naming the node, never TypeError."""
    _, svc = await make_validation_service(tmp_path, confs={"cfg-live": {}})
    team = await make_validation_team(svc)

    for bad in (
        "cfg-gone",
        {"tools": "web_search"},
        {"tools": [""]},
        {"tools": [1]},
        {"skills": {"a": 1}},
    ):
        with pytest.raises(AgentTeamsServiceError, match="节点 n2"):
            await svc.create_workflow(
                "alice",
                team["team_id"],
                {"name": "w", "graph": exec_graph(team, bad)},
            )


# Runner-level config checks.


RUNNER_CONFIG = {
    "failure_policy": "pause",
    "reply_timeout": 5.0,
    "max_parallel": 5,
    "inject_max_length": 4000,
}


def runner_members() -> list[dict]:
    """Two single-session members for linear runner graphs."""
    return [
        {
            "member_id": "mA",
            "name": "甲",
            "session_id": "conv-0",
            "umo": "webchat:FriendMessage:conv-0",
            "persona_id": None,
            "provider_id": None,
            "system_prompt": None,
        },
        {
            "member_id": "mB",
            "name": "乙",
            "session_id": "conv-1",
            "umo": "webchat:FriendMessage:conv-1",
            "persona_id": None,
            "provider_id": None,
            "system_prompt": None,
        },
    ]


def scripted_ports_of(deliver, collect, events: list) -> TeamPorts:
    return TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda sid: False,
        emit=events.append,
    )


@pytest.mark.asyncio
async def test_runner_fails_node_with_deleted_config_profile(tmp_path):
    """A node bound to a config profile that fails the checker fails before
    the busy-wait with 配置档案已删除 — persisted, emitted, never delivered."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = runner_members()
    delivered: list = []
    events: list = []

    async def deliver(session_id, text, context=None, execution_token=None):
        delivered.append(session_id)
        return "mid-1"

    async def collect(session_id, message_id, member_id=None):
        return "回复", []

    graph = {
        "nodes": [
            {
                "id": "n1",
                "member_id": "mA",
                "task": "t",
                "execution": {"config_id": "cfg-gone"},
            }
        ],
        "edges": [],
    }
    bus = RunEventBus()
    runner = DAGRunner(
        run_id="rcfg",
        team_id="t1",
        graph=graph,
        config=RUNNER_CONFIG,
        members=members,
        ports=scripted_ports_of(deliver, collect, events),
        db=db,
        bus=bus,
        username="alice",
        run_input="x",
        config_checker=lambda cid: False,
    )
    await runner.run()

    state = runner.node_states["n1"]
    assert state["status"] == "failed"
    assert state["error"] == "配置档案已删除"
    assert delivered == []  # failed before the busy-wait, never delivered
    row = await db.get_agent_team_run("rcfg")
    assert row.node_states["n1"]["status"] == "failed"
    failed = [
        e
        for e in bus.history()
        if e.get("type") == "node_status" and e.get("status") == "failed"
    ]
    assert failed and failed[0]["error"] == "配置档案已删除"


@pytest.mark.asyncio
async def test_runner_config_checker_pass_runs_normally(tmp_path):
    """A config_id the checker accepts does not alter node execution."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = runner_members()
    delivered: list = []

    async def deliver(session_id, text, context=None, execution_token=None):
        delivered.append(session_id)
        return "mid-1"

    async def collect(session_id, message_id, member_id=None):
        return "回复", []

    graph = {
        "nodes": [
            {
                "id": "n1",
                "member_id": "mA",
                "task": "t",
                "execution": {"config_id": "cfg-live"},
            }
        ],
        "edges": [],
    }
    runner = DAGRunner(
        run_id="rcfgok",
        team_id="t1",
        graph=graph,
        config=RUNNER_CONFIG,
        members=members,
        ports=scripted_ports_of(deliver, collect, []),
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="x",
        config_checker=lambda cid: cid == "cfg-live",
    )
    await runner.run()

    assert runner.status == "completed"
    assert delivered == ["conv-0"]


@pytest.mark.asyncio
async def test_resume_run_prefails_pending_nodes_with_deleted_config(tmp_path):
    """Resuming an interrupted run whose pending node's config profile was
    deleted surfaces the failure immediately — no turn is dispatched."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = runner_members()
    delivered: list = []
    events: list = []

    async def deliver(session_id, text, context=None, execution_token=None):
        delivered.append(session_id)
        return "mid-1"

    async def collect(session_id, message_id, member_id=None):
        return "回复", []

    run_svc = AgentTeamRunService(
        db=db,
        chat_service=FakeChatService(),
        config_checker=lambda cid: cid == "cfg-live",
    )
    run_svc.ports_factory = lambda username, emit: scripted_ports_of(
        deliver, collect, events
    )
    graph = {
        "nodes": [
            {
                "id": "n1",
                "member_id": "mA",
                "task": "t1",
                "execution": {"config_id": "cfg-live"},
            },
            {
                "id": "n2",
                "member_id": "mB",
                "task": "t2 {{n1}}",
                "execution": {"config_id": "cfg-gone"},
            },
        ],
        "edges": [{"from": "n1", "to": "n2"}],
    }
    node_states = {
        "n1": {
            "status": "done",
            "member_id": "mA",
            "task_rendered": "t1",
            "result": "前驱结果",
            "error": None,
            "started_at": None,
            "finished_at": None,
        },
        "n2": {
            "status": "running",
            "member_id": "mB",
            "task_rendered": None,
            "result": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
        },
    }
    await db.create_agent_team(
        team_id="t1",
        owner_username="alice",
        name="t",
        coordinator_member_id="mA",
        members=members,
        config={},
    )
    await db.create_agent_team_run(
        run_id="rres",
        team_id="t1",
        workflow_id=None,
        mode="dag",
        input="x",
        status="interrupted",
        graph_snapshot=graph,
        node_states=node_states,
        rounds=[],
    )

    await run_svc.resume_run("alice", "rres")
    runner = run_svc._runners["rres"]
    for _ in range(250):
        if runner.status in ("completed", "paused", "stopped", "failed"):
            break
        await asyncio.sleep(0.02)

    # n2 (re-built pending, cfg-gone) pre-fails before any spawn; n1 stays done.
    assert runner.node_states["n2"]["status"] == "failed"
    assert runner.node_states["n2"]["error"] == "配置档案已删除"
    assert runner.node_states["n1"]["status"] == "done"
    assert delivered == []
    row = await db.get_agent_team_run("rres")
    assert row.node_states["n2"]["status"] == "failed"
    assert row.node_states["n2"]["error"] == "配置档案已删除"


# ---------------------------------------------------------------------------
# Runner execution-token wiring (spec §2.3)
# ---------------------------------------------------------------------------


def token_runner(tmp_path, run_id: str, deliver, collect):
    """Build (db, runner) over a single node carrying an execution block."""
    db = SQLiteDatabase(str(tmp_path / f"{run_id}.db"))
    runner = DAGRunner(
        run_id=run_id,
        team_id="team-1",
        graph={
            "nodes": [
                {
                    "id": "n1",
                    "member_id": "mA",
                    "task": "t",
                    "execution": {"config_id": "cfg-1"},
                }
            ],
            "edges": [],
        },
        config=RUNNER_CONFIG,
        members=runner_members()[:1],
        ports=scripted_ports_of(deliver, collect, []),
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="x",
    )
    return db, runner


@pytest.mark.asyncio
async def test_runner_delivers_node_turn_with_execution_token(tmp_path):
    """A node with an execution block dispatches under a registered token:
    the scripted deliver resolves the binding mid-turn, and the registry is
    empty again once the node settles."""
    captured: dict = {}

    async def deliver(session_id, text, context=None, execution_token=None):
        captured["token"] = execution_token
        captured["binding"] = AgentTeamExecutionRegistry.resolve(
            execution_token, umo="webchat:FriendMessage:conv-0"
        )
        return "mid-1"

    async def collect(session_id, message_id, member_id=None):
        return "回复", []

    db, runner = token_runner(tmp_path, "rtok", deliver, collect)
    await db.initialize()
    await runner.run()

    assert runner.status == "completed"
    token = captured.get("token")
    assert token, "deliver must receive an execution token"
    binding = captured["binding"]
    assert binding is not None
    assert binding.config_id == "cfg-1"
    assert binding.node_id == "n1"
    assert binding.member_id == "mA"
    assert binding.umo == "webchat:FriendMessage:conv-0"
    assert binding.owner_username == "alice"
    assert binding.run_id == "rtok"
    assert binding.team_id == "team-1"
    # The token lives exactly for the turn: gone once the node settles.
    assert AgentTeamExecutionRegistry.resolve(token) is None


@pytest.mark.asyncio
async def test_runner_deliver_failure_still_unregisters_token(tmp_path):
    """A delivery failure fails the node AND releases the token."""
    captured: dict = {}

    async def deliver(session_id, text, context=None, execution_token=None):
        captured["token"] = execution_token
        raise RuntimeError("deliver exploded")

    async def collect(session_id, message_id, member_id=None):
        return "", []

    db, runner = token_runner(tmp_path, "rtokfail", deliver, collect)
    await db.initialize()
    await runner.run()

    assert runner.status == "paused"
    assert runner.node_states["n1"]["status"] == "failed"
    assert "deliver exploded" in runner.node_states["n1"]["error"]
    token = captured.get("token")
    assert token, "deliver must receive an execution token"
    # The failure path must not leak the token.
    assert AgentTeamExecutionRegistry.resolve(token) is None


@pytest.mark.asyncio
async def test_runner_node_without_execution_dispatches_no_token(tmp_path):
    """Nodes without an execution block deliver with execution_token=None;
    auto-mode member turns behave the same by design (spec §2.2)."""
    db = SQLiteDatabase(str(tmp_path / "rnotok.db"))
    await db.initialize()
    captured: dict = {}

    async def deliver(session_id, text, context=None, execution_token=None):
        captured["token"] = execution_token
        return "mid-1"

    async def collect(session_id, message_id, member_id=None):
        return "回复", []

    runner = DAGRunner(
        run_id="rnotok",
        team_id="t1",
        graph={"nodes": [{"id": "n1", "member_id": "mA", "task": "t"}], "edges": []},
        config=RUNNER_CONFIG,
        members=runner_members()[:1],
        ports=scripted_ports_of(deliver, collect, []),
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="x",
    )
    await runner.run()

    assert runner.status == "completed"
    assert captured["token"] is None


@pytest.mark.asyncio
async def test_deliver_puts_execution_token_into_payload():
    """The real deliver stamps the internal-only execution_token into the
    queued payload; the webchat adapter lifts it into the event extra."""
    mgr = WebChatQueueMgr()
    ports = build_ports_for_test(mgr, "alice", emit=lambda e: None)
    try:
        await ports.deliver("conv-tok", "hi", None, execution_token="tok-1")
        payload = mgr.queues["conv-tok"].get_nowait()[2]
        await ports.deliver("conv-tok", "hi", None)
        default_payload = mgr.queues["conv-tok"].get_nowait()[2]
    finally:
        await ports.close()

    assert payload.get("execution_token") == "tok-1"
    assert default_payload.get("execution_token") is None


@pytest.mark.asyncio
async def test_runner_stop_mid_turn_still_unregisters_token(tmp_path):
    """A stop landing mid-collection abandons the turn AND releases the
    token — the unregister finally spans the whole delivery try."""
    gate = asyncio.Event()  # the reply never arrives
    captured: dict = {}

    async def deliver(session_id, text, context=None, execution_token=None):
        captured["token"] = execution_token
        return "mid-1"

    async def collect(session_id, message_id, member_id=None):
        await gate.wait()
        return "", []

    db, runner = token_runner(tmp_path, "rstoptok", deliver, collect)
    await db.initialize()
    runner.config["reply_timeout"] = 60.0  # prove abandonment, not timeout
    task = asyncio.create_task(runner.run())
    for _ in range(250):
        if runner.node_states["n1"]["status"] == "running":
            break
        await asyncio.sleep(0.02)
    runner.request_stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert runner.status == "stopped"
    token = captured.get("token")
    assert token
    assert AgentTeamExecutionRegistry.resolve(token) is None
