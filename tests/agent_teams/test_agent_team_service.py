"""AgentTeamService tests: team CRUD and member session lifecycle."""

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


class FakeCoreLifecycle:
    def __init__(self):
        self.conversation_manager = FakeConversationManager()
        self.provider_manager = FakeProviderManager()


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
