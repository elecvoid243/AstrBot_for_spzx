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

import pytest

from astrbot.core.agent_team_execution import (
    AgentTeamExecutionRegistry,
    NodeExecutionBinding,
)
from astrbot.core.event_bus import EventBus
from astrbot.core.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    PlatformMetadata,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter

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
