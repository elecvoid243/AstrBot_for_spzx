"""Agent Teams team/member management (spec §6.1).

Workflow CRUD is appended to this class in Task 5; run lifecycle lives in
agent_team_run_service.py.
"""

import uuid
from collections.abc import Callable

from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import AgentTeam
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import ProviderType

MAX_MEMBERS = 10
MAX_NAME_LEN = 32

DEFAULT_TEAM_CONFIG = {
    "failure_policy": "pause",  # pause | auto_skip
    "reply_timeout": 600.0,
    "max_rounds": 20,
    "max_parallel": 5,
    "inject_max_length": 4000,
}
VALID_FAILURE_POLICIES = ("pause", "auto_skip")


class AgentTeamsServiceError(Exception):
    """Raised for agent team service errors; the message is user-facing."""


def _new_id(n: int = 8) -> str:
    return uuid.uuid4().hex[:n]


def _team_to_dict(team: AgentTeam) -> dict:
    return {
        "team_id": team.team_id,
        "owner_username": team.owner_username,
        "name": team.name,
        "coordinator_member_id": team.coordinator_member_id,
        "members": team.members,
        "config": {**DEFAULT_TEAM_CONFIG, **(team.config or {})},
        "created_at": team.created_at,
        "updated_at": team.updated_at,
    }


class AgentTeamService:
    """Team and member management over the agent_teams tables."""

    def __init__(
        self,
        db: BaseDatabase,
        core_lifecycle,
        chat_service,
        busy_checker: Callable[[str], bool] | None = None,
    ) -> None:
        """Args:
        db: Database helper (repo methods from the agent-teams migration).
        core_lifecycle: Core lifecycle providing conversation/provider managers.
        chat_service: Dashboard ChatService for member session creation.
        busy_checker: Optional session_id -> bool; wired to the chat run
            registry to guard member removal while a turn is in flight.
        """
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.chat_service = chat_service
        self.busy_checker = busy_checker or (lambda session_id: False)

    # ---------- helpers ----------

    async def _get_owned(self, username: str, team_id: str) -> AgentTeam:
        team = await self.db.get_agent_team(team_id)
        if team is None or team.owner_username != username:
            raise AgentTeamsServiceError(f"团队 '{team_id}' 不存在")
        return team

    @staticmethod
    def _merged_config(config: dict | None) -> dict:
        merged = {**DEFAULT_TEAM_CONFIG, **(config or {})}
        if merged["failure_policy"] not in VALID_FAILURE_POLICIES:
            raise AgentTeamsServiceError(
                f"failure_policy 必须是 {VALID_FAILURE_POLICIES} 之一"
            )
        if not 0 < merged["max_parallel"] <= 5:
            raise AgentTeamsServiceError("max_parallel 必须在 1..5 之间")
        if not 0 < merged["max_rounds"] <= 20:
            raise AgentTeamsServiceError("max_rounds 必须在 1..20 之间")
        return merged

    async def _create_member(self, username: str, payload: dict) -> dict:
        """Create one member: session + conversation persona (+ provider).

        Args:
            username: Requesting dashboard user (session creator).
            payload: {name, persona_id? | system_prompt?, provider_id?}.

        Returns:
            The member dict stored in team.members.

        Raises:
            AgentTeamsServiceError: On invalid payload.
        """
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > MAX_NAME_LEN:
            raise AgentTeamsServiceError(f"成员名必填且 ≤{MAX_NAME_LEN} 字符")
        persona_id = payload.get("persona_id") or None
        system_prompt = str(payload.get("system_prompt") or "").strip()
        provider_id = str(payload.get("provider_id") or "").strip() or None
        if not persona_id and not system_prompt:
            raise AgentTeamsServiceError(
                f"成员 '{name}' 需要 persona_id 或 system_prompt 之一"
            )

        session = await self.chat_service.new_session(username, "webchat")
        session_id = session["session_id"]
        umo = str(MessageSession("webchat", MessageType.FRIEND_MESSAGE, session_id))
        await self.core_lifecycle.conversation_manager.new_conversation(
            umo, "webchat", persona_id=persona_id
        )
        if provider_id:
            await self.core_lifecycle.provider_manager.set_provider(
                provider_id, ProviderType.CHAT_COMPLETION, umo
            )
        # A bare system_prompt (no persona_id) is stored on the member and
        # injected per-turn by the run service via TeamPorts context.
        return {
            "member_id": _new_id(),
            "name": name,
            "session_id": session_id,
            "umo": umo,
            "persona_id": persona_id,
            "provider_id": provider_id,
            "system_prompt": system_prompt or None,
        }

    # ---------- teams ----------

    async def create_team(self, username: str, payload: dict) -> dict:
        """Create a team and all its member sessions in one call."""
        name = str(payload.get("name") or "").strip()
        if not name:
            raise AgentTeamsServiceError("团队名不能为空")
        raw_members = payload.get("members") or []
        if not isinstance(raw_members, list) or len(raw_members) < 2:
            raise AgentTeamsServiceError("至少需要 2 名成员")
        if len(raw_members) > MAX_MEMBERS:
            raise AgentTeamsServiceError(f"成员数不能超过 {MAX_MEMBERS}")
        coordinator_name = str(payload.get("coordinator") or "").strip()

        members: list[dict] = []
        seen_names: set[str] = set()
        seen_sessions: set[str] = set()
        for raw in raw_members:
            member = await self._create_member(username, raw)
            if member["name"].lower() in seen_names:
                raise AgentTeamsServiceError(f"成员名必须 unique: {member['name']}")
            if member["session_id"] in seen_sessions:
                raise AgentTeamsServiceError("成员会话冲突，请重试")
            seen_names.add(member["name"].lower())
            seen_sessions.add(member["session_id"])
            members.append(member)

        if coordinator_name not in {m["name"] for m in members}:
            raise AgentTeamsServiceError(
                f"coordinator 必须是成员之一: {coordinator_name!r}"
            )
        team_id = _new_id()
        await self.db.create_agent_team(
            team_id=team_id,
            owner_username=username,
            name=name,
            coordinator_member_id=next(
                m["member_id"] for m in members if m["name"] == coordinator_name
            ),
            members=members,
            config=self._merged_config(payload.get("config")),
        )
        return await self.get_team(username, team_id)

    async def list_teams(self, username: str) -> dict:
        rows = await self.db.get_agent_teams_by_owner(username)
        return {"teams": [_team_to_dict(t) for t in rows]}

    async def get_team(self, username: str, team_id: str) -> dict:
        return _team_to_dict(await self._get_owned(username, team_id))

    async def update_team(self, username: str, team_id: str, payload: dict) -> dict:
        team = await self._get_owned(username, team_id)
        updates: dict = {}
        if "name" in payload:
            name = str(payload["name"] or "").strip()
            if not name:
                raise AgentTeamsServiceError("团队名不能为空")
            updates["name"] = name
        if "coordinator" in payload:
            coordinator = str(payload["coordinator"]).strip()
            match = next((m for m in team.members if m["name"] == coordinator), None)
            if match is None:
                raise AgentTeamsServiceError(f"协调者必须是成员之一: {coordinator!r}")
            updates["coordinator_member_id"] = match["member_id"]
        if "config" in payload:
            updates["config"] = self._merged_config(
                {**(team.config or {}), **(payload.get("config") or {})}
            )
        if updates:
            await self.db.update_agent_team(team_id, **updates)
        return await self.get_team(username, team_id)

    async def delete_team(self, username: str, team_id: str) -> dict:
        await self._get_owned(username, team_id)
        active = await self.db.get_active_agent_team_run(team_id)
        if active is not None:
            raise AgentTeamsServiceError("团队有 active run，无法删除")
        # Member sessions are preserved by design (spec §6.1): users manage
        # them from the chat page.
        await self.db.delete_agent_team(team_id)
        return {"message": "团队已删除"}

    # ---------- members ----------

    async def add_member(self, username: str, team_id: str, payload: dict) -> dict:
        team = await self._get_owned(username, team_id)
        if len(team.members) >= MAX_MEMBERS:
            raise AgentTeamsServiceError(f"成员数不能超过 {MAX_MEMBERS}")
        member = await self._create_member(username, payload)
        if any(m["name"].lower() == member["name"].lower() for m in team.members):
            raise AgentTeamsServiceError(f"成员名必须 unique: {member['name']}")
        await self.db.update_agent_team(team_id, members=[*team.members, member])
        return member

    async def remove_member(self, username: str, team_id: str, member_id: str) -> dict:
        team = await self._get_owned(username, team_id)
        member = next((m for m in team.members if m["member_id"] == member_id), None)
        if member is None:
            raise AgentTeamsServiceError(f"成员 '{member_id}' 不存在")
        if member["member_id"] == team.coordinator_member_id:
            raise AgentTeamsServiceError("不能移除协调者 (coordinator)，请先切换协调者")
        if self.busy_checker(member["session_id"]):
            raise AgentTeamsServiceError("成员会话正在执行 (busy)，无法移除")
        if await self.db.get_active_agent_team_run(team_id):
            raise AgentTeamsServiceError("团队有 active run，无法移除成员")
        await self.db.update_agent_team(
            team_id, members=[m for m in team.members if m["member_id"] != member_id]
        )
        return {"message": "成员已移除"}
