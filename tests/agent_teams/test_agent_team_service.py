"""AgentTeamService tests: team CRUD and member session lifecycle."""

from types import SimpleNamespace

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_service import (
    AgentTeamService,
    AgentTeamsServiceError,
)


class FakeChatService:
    def __init__(self):
        self.counter = 0

    async def new_session(self, username: str, platform_id: str) -> dict:
        self.counter += 1
        return {"session_id": f"conv-{self.counter:04d}", "platform_id": platform_id}


class FakeConversationManager:
    def __init__(self):
        self.created = []

    async def new_conversation(
        self, unified_msg_origin, platform_id, content=None, title=None, persona_id=None
    ):
        self.created.append({"umo": unified_msg_origin, "persona_id": persona_id})
        return "cid-1"


class FakeProviderManager:
    def __init__(self):
        self.set = []

    async def set_provider(self, provider_id, provider_type, umo=None):
        self.set.append((provider_id, umo))


class FakePersonaManager:
    """Persona manager stand-in mirroring get_persona_v3_by_id (name lookup)."""

    def __init__(self, personas=None):
        self.personas_v3 = list(personas or [])

    def get_persona_v3_by_id(self, persona_id):
        return next((p for p in self.personas_v3 if p["name"] == persona_id), None)


class FakeKnowledgeBaseHelper:
    def __init__(self, kb_id, kb_name):
        self.kb = SimpleNamespace(kb_id=kb_id, kb_name=kb_name)


class FakeKnowledgeBaseManager:
    """kb manager stand-in mirroring get_kb_by_name (lookup, never raises)."""

    def __init__(self, kbs=None):
        self.kbs = list(kbs or [])

    async def get_kb_by_name(self, name):
        return next((h for h in self.kbs if h.kb.kb_name == name), None)


class FakeCoreLifecycle:
    def __init__(self, confs=None, personas=None):
        self.conversation_manager = FakeConversationManager()
        self.provider_manager = FakeProviderManager()
        self.astrbot_config_mgr = SimpleNamespace(confs=dict(confs or {}))
        self.persona_mgr = FakePersonaManager(personas)
        self.kb_manager = FakeKnowledgeBaseManager()


async def make_service(tmp_path, busy=None):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    return db, AgentTeamService(
        db=db,
        core_lifecycle=FakeCoreLifecycle(),
        chat_service=FakeChatService(),
        busy_checker=busy or (lambda sid: False),
    )


MEMBERS = [
    {"name": "主管", "persona_id": "p1"},
    {"name": "写手", "system_prompt": "你是写手", "provider_id": "prov-a"},
    {"name": "审校", "persona_id": "p2"},
]


@pytest.mark.asyncio
async def test_create_team_creates_member_sessions(tmp_path):
    db, svc = await make_service(tmp_path)
    team = await svc.create_team(
        "alice", {"name": "内容团队", "members": MEMBERS, "coordinator": "主管"}
    )
    assert team["coordinator_member_id"] == team["members"][0]["member_id"]
    assert len(team["members"]) == 3
    assert team["members"][1]["provider_id"] == "prov-a"
    assert all(m["session_id"].startswith("conv-") for m in team["members"])
    assert team["config"]["failure_policy"] == "pause"
    row = await db.get_agent_team(team["team_id"])
    assert row is not None and row.name == "内容团队"


@pytest.mark.asyncio
async def test_create_team_rejects_bad_coordinator_and_duplicates(tmp_path):
    _, svc = await make_service(tmp_path)
    with pytest.raises(AgentTeamsServiceError, match="coordinator"):
        await svc.create_team(
            "alice", {"name": "t", "members": MEMBERS, "coordinator": "不存在"}
        )
    with pytest.raises(AgentTeamsServiceError, match="unique"):
        await svc.create_team(
            "alice",
            {
                "name": "t",
                "members": MEMBERS + [dict(MEMBERS[0])],
                "coordinator": "主管",
            },
        )


@pytest.mark.asyncio
async def test_sessions_unique_across_teams(tmp_path):
    _, svc = await make_service(tmp_path)
    t1 = await svc.create_team(
        "alice", {"name": "a", "members": MEMBERS, "coordinator": "主管"}
    )
    t2 = await svc.create_team(
        "bob", {"name": "b", "members": MEMBERS, "coordinator": "写手"}
    )
    s1 = {m["session_id"] for m in t1["members"]}
    s2 = {m["session_id"] for m in t2["members"]}
    assert s1.isdisjoint(s2)


@pytest.mark.asyncio
async def test_remove_member_guards(tmp_path):
    db, svc = await make_service(tmp_path)
    team = await svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    victim = team["members"][1]
    busy_svc = AgentTeamService(
        db=db,
        core_lifecycle=FakeCoreLifecycle(),
        chat_service=FakeChatService(),
        busy_checker=lambda sid: sid == victim["session_id"],
    )
    with pytest.raises(AgentTeamsServiceError, match="busy"):
        await busy_svc.remove_member("alice", team["team_id"], victim["member_id"])
    coordinator = team["members"][0]
    with pytest.raises(AgentTeamsServiceError, match="coordinator"):
        await svc.remove_member("alice", team["team_id"], coordinator["member_id"])
    await svc.remove_member("alice", team["team_id"], victim["member_id"])
    updated = await svc.get_team("alice", team["team_id"])
    assert len(updated["members"]) == 2


@pytest.mark.asyncio
async def test_delete_team_guard_and_ownership(tmp_path):
    db, svc = await make_service(tmp_path)
    team = await svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    with pytest.raises(AgentTeamsServiceError, match="不存在"):
        await svc.get_team("bob", team["team_id"])
    await db.create_agent_team_run(
        run_id="r1",
        team_id=team["team_id"],
        workflow_id=None,
        mode="dag",
        input="x",
        status="running",
        graph_snapshot={},
        node_states={},
        rounds=[],
    )
    with pytest.raises(AgentTeamsServiceError, match="active run"):
        await svc.delete_team("alice", team["team_id"])


@pytest.mark.asyncio
async def test_create_team_validates_before_sessions(tmp_path):
    _, svc = await make_service(tmp_path)
    with pytest.raises(AgentTeamsServiceError, match="coordinator"):
        await svc.create_team(
            "alice", {"name": "t", "members": MEMBERS, "coordinator": "不存在"}
        )
    assert svc.chat_service.counter == 0
    with pytest.raises(AgentTeamsServiceError, match="unique"):
        await svc.create_team(
            "alice",
            {
                "name": "t",
                "members": MEMBERS + [dict(MEMBERS[0])],
                "coordinator": "主管",
            },
        )
    assert svc.chat_service.counter == 0


@pytest.mark.asyncio
async def test_add_member_rejects_duplicate_before_session(tmp_path):
    _, svc = await make_service(tmp_path)
    team = await svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    before = svc.chat_service.counter
    with pytest.raises(AgentTeamsServiceError, match="unique"):
        await svc.add_member(
            "alice", team["team_id"], {"name": "主管", "persona_id": "p9"}
        )
    assert svc.chat_service.counter == before


@pytest.mark.asyncio
async def test_create_team_hardens_config_and_member_types(tmp_path):
    """Bad config/member payloads raise AgentTeamsServiceError (400-grade),
    never TypeError/AttributeError; no sessions leak on rejection."""
    _, svc = await make_service(tmp_path)
    base = {"name": "t", "members": MEMBERS, "coordinator": "主管"}

    with pytest.raises(AgentTeamsServiceError):
        await svc.create_team("alice", {**base, "config": {"max_parallel": "nine"}})
    with pytest.raises(AgentTeamsServiceError):
        await svc.create_team("alice", {**base, "config": {"inject_max_length": 0}})
    with pytest.raises(AgentTeamsServiceError):
        await svc.create_team("alice", {**base, "config": {"reply_timeout": "soon"}})
    with pytest.raises(AgentTeamsServiceError):
        await svc.create_team("alice", {**base, "config": {"max_rounds": 21}})
    with pytest.raises(AgentTeamsServiceError, match="成员配置格式错误"):
        await svc.create_team("alice", {**base, "members": ["x", "y"]})

    assert svc.chat_service.counter == 0


@pytest.mark.asyncio
async def test_update_member_persists_runner_config(tmp_path, monkeypatch):
    """runner_config is validated, persisted, and legacy fields stay intact."""
    _, svc = await make_service(tmp_path)
    monkeypatch.setattr(svc.core_lifecycle.astrbot_config_mgr, "confs", {"conf0": {}})
    team = await svc.create_team(
        "owner0", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    member_id = team["members"][0]["member_id"]
    updated = await svc.update_member(
        "owner0",
        team["team_id"],
        member_id,
        {"name": "主管", "runner_config": {"config_id": "conf0", "max_steps": 12}},
    )
    member = next(m for m in updated["members"] if m["member_id"] == member_id)
    assert member["runner_config"]["config_id"] == "conf0"
    assert member["runner_config"]["max_steps"] == 12
    # legacy fields untouched
    assert member["session_id"]


@pytest.mark.asyncio
async def test_update_member_rejects_unknown_member(tmp_path):
    _, svc = await make_service(tmp_path)
    team = await svc.create_team(
        "owner0", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    with pytest.raises(AgentTeamsServiceError, match="不存在"):
        await svc.update_member("owner0", team["team_id"], "nope", {"name": "X"})


@pytest.mark.asyncio
async def test_update_member_rejects_invalid_config_id(tmp_path, monkeypatch):
    _, svc = await make_service(tmp_path)
    monkeypatch.setattr(svc.core_lifecycle.astrbot_config_mgr, "confs", {})
    team = await svc.create_team(
        "owner0", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    member_id = team["members"][0]["member_id"]
    with pytest.raises(AgentTeamsServiceError, match="配置档案不存在"):
        await svc.update_member(
            "owner0",
            team["team_id"],
            member_id,
            {"runner_config": {"config_id": "missing"}},
        )


@pytest.mark.asyncio
async def test_update_member_rejects_out_of_range_numbers(tmp_path):
    _, svc = await make_service(tmp_path)
    team = await svc.create_team(
        "owner0", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    member_id = team["members"][0]["member_id"]
    with pytest.raises(AgentTeamsServiceError):
        await svc.update_member(
            "owner0", team["team_id"], member_id, {"runner_config": {"max_steps": 0}}
        )
    with pytest.raises(AgentTeamsServiceError):
        await svc.update_member(
            "owner0",
            team["team_id"],
            member_id,
            {"runner_config": {"tool_call_timeout": 99999}},
        )
    with pytest.raises(AgentTeamsServiceError):
        await svc.update_member(
            "owner0",
            team["team_id"],
            member_id,
            {"runner_config": {"context_length": -5}},
        )


@pytest.mark.asyncio
async def test_update_member_name_only_does_not_re_pin_session(tmp_path):
    """A name/runner_config edit must not recreate the member conversation."""
    _, svc = await make_service(tmp_path)
    team = await svc.create_team(
        "owner0", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    member = team["members"][0]
    conversations_before = len(svc.core_lifecycle.conversation_manager.created)
    pins_before = len(svc.core_lifecycle.provider_manager.set)
    await svc.update_member(
        "owner0",
        team["team_id"],
        member["member_id"],
        {"name": "主管", "runner_config": {"max_steps": 5}},
    )
    assert len(svc.core_lifecycle.conversation_manager.created) == conversations_before
    assert len(svc.core_lifecycle.provider_manager.set) == pins_before


@pytest.mark.asyncio
async def test_update_member_changed_persona_provider_re_pins(tmp_path, monkeypatch):
    """An actual persona/provider change re-pins; same-value edits do not."""
    _, svc = await make_service(tmp_path)
    monkeypatch.setattr(svc.core_lifecycle.persona_mgr, "personas_v3", [{"name": "p9"}])
    team = await svc.create_team(
        "owner0", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    # 主管 has persona p1 -> switching to p9 must create a new conversation.
    member = team["members"][0]
    conversations_before = len(svc.core_lifecycle.conversation_manager.created)
    await svc.update_member(
        "owner0", team["team_id"], member["member_id"], {"persona_id": "p9"}
    )
    assert (
        len(svc.core_lifecycle.conversation_manager.created) == conversations_before + 1
    )
    assert svc.core_lifecycle.conversation_manager.created[-1]["persona_id"] == "p9"
    # Setting the same persona again must NOT re-pin.
    await svc.update_member(
        "owner0", team["team_id"], member["member_id"], {"persona_id": "p9"}
    )
    assert (
        len(svc.core_lifecycle.conversation_manager.created) == conversations_before + 1
    )
    # 写手 has provider prov-a -> switching to prov-b must set the provider.
    writer = team["members"][1]
    pins_before = len(svc.core_lifecycle.provider_manager.set)
    await svc.update_member(
        "owner0", team["team_id"], writer["member_id"], {"provider_id": "prov-b"}
    )
    assert len(svc.core_lifecycle.provider_manager.set) == pins_before + 1
    assert svc.core_lifecycle.provider_manager.set[-1][0] == "prov-b"


@pytest.mark.asyncio
async def test_update_member_clear_persona_provider_re_pins(tmp_path, monkeypatch):
    """Clearing persona re-pins to no persona; clearing provider drops the pin."""
    import astrbot.core as core_mod

    _, svc = await make_service(tmp_path)
    removed = []

    async def fake_session_remove(umo, key):
        removed.append((umo, key))

    monkeypatch.setattr(core_mod.sp, "session_remove", fake_session_remove)
    team = await svc.create_team(
        "owner0", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    # 主管 has persona p1 -> clearing it must create a fresh conversation with
    # persona_id None so no persona stays pinned on the session.
    leader = team["members"][0]
    conversations_before = len(svc.core_lifecycle.conversation_manager.created)
    await svc.update_member(
        "owner0", team["team_id"], leader["member_id"], {"persona_id": None}
    )
    assert (
        len(svc.core_lifecycle.conversation_manager.created) == conversations_before + 1
    )
    assert svc.core_lifecycle.conversation_manager.created[-1]["persona_id"] is None
    # Clearing an already-clear persona must NOT re-pin.
    await svc.update_member(
        "owner0", team["team_id"], leader["member_id"], {"persona_id": None}
    )
    assert (
        len(svc.core_lifecycle.conversation_manager.created) == conversations_before + 1
    )
    # 写手 has provider prov-a -> clearing it must remove the pinned provider
    # key (no set_provider call) so the session falls back to the default.
    writer = team["members"][1]
    pins_before = len(svc.core_lifecycle.provider_manager.set)
    await svc.update_member(
        "owner0", team["team_id"], writer["member_id"], {"provider_id": None}
    )
    assert len(svc.core_lifecycle.provider_manager.set) == pins_before
    assert removed == [(writer["umo"], "provider_perf_chat_completion")]
    # Clearing an already-clear provider must not touch the session again.
    await svc.update_member(
        "owner0", team["team_id"], writer["member_id"], {"provider_id": None}
    )
    assert removed == [(writer["umo"], "provider_perf_chat_completion")]


async def _capture_session_puts(tmp_path, monkeypatch, kbs=None):
    """Create a service with a fake kb manager and captured sp.session_put calls."""
    import astrbot.core as core_mod

    _, svc = await make_service(tmp_path)
    svc.core_lifecycle.kb_manager = FakeKnowledgeBaseManager(kbs or [])
    calls = []

    async def fake_session_put(umo, key, value):
        calls.append((umo, key, value))

    monkeypatch.setattr(core_mod.sp, "session_put", fake_session_put)
    return svc, calls


@pytest.mark.asyncio
async def test_update_member_kb_names_pins_session_kb_config(tmp_path, monkeypatch):
    """kb_names maps to kb ids and pins the member session kb_config."""
    svc, calls = await _capture_session_puts(
        tmp_path,
        monkeypatch,
        [
            FakeKnowledgeBaseHelper("kb-uuid-1", "指南"),
            FakeKnowledgeBaseHelper("kb-uuid-2", "法规"),
        ],
    )
    team = await svc.create_team(
        "owner0", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    member = team["members"][0]
    await svc.update_member(
        "owner0",
        team["team_id"],
        member["member_id"],
        {"runner_config": {"kb_names": ["指南", "法规"]}},
    )
    # ids are stored in name order.
    assert calls == [
        (
            member["umo"],
            "kb_config",
            {"kb_ids": ["kb-uuid-1", "kb-uuid-2"], "top_k": 5},
        )
    ]


@pytest.mark.asyncio
async def test_update_member_kb_names_unknown_raises_before_session_write(
    tmp_path, monkeypatch
):
    """An unknown kb name rejects the update before any session pin is written."""
    svc, calls = await _capture_session_puts(
        tmp_path,
        monkeypatch,
        [FakeKnowledgeBaseHelper("kb-uuid-1", "指南")],
    )
    team = await svc.create_team(
        "owner0", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    member = team["members"][0]
    with pytest.raises(AgentTeamsServiceError, match="知识库不存在: 不存在"):
        await svc.update_member(
            "owner0",
            team["team_id"],
            member["member_id"],
            {"runner_config": {"kb_names": ["指南", "不存在"]}},
        )
    # The error surfaces instead of silently keeping/scribbling a pin.
    assert calls == []


@pytest.mark.asyncio
async def test_update_member_kb_names_empty_clears_session_kb_config(
    tmp_path, monkeypatch
):
    """Empty kb_names pins `kb_ids: []` (retrieval disables); absent writes nothing."""
    svc, calls = await _capture_session_puts(
        tmp_path,
        monkeypatch,
        [FakeKnowledgeBaseHelper("kb-uuid-1", "指南")],
    )
    team = await svc.create_team(
        "owner0", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    member = team["members"][0]
    await svc.update_member(
        "owner0",
        team["team_id"],
        member["member_id"],
        {"runner_config": {"kb_names": []}},
    )
    assert calls == [(member["umo"], "kb_config", {"kb_ids": []})]
    # runner_config without kb_names must not touch the session kb_config.
    calls.clear()
    await svc.update_member(
        "owner0",
        team["team_id"],
        member["member_id"],
        {"runner_config": {"max_steps": 5}},
    )
    assert calls == []
    # Payload without runner_config must not touch the session kb_config either.
    await svc.update_member(
        "owner0", team["team_id"], member["member_id"], {"name": "主管"}
    )
    assert calls == []
