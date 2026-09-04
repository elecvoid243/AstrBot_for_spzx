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
        # Validate numeric settings here so bad payloads surface as 400-grade
        # service errors instead of TypeError inside the runner. reply_timeout
        # / inject_max_length must be positive (render_task truncation needs a
        # limit >= 1); max_parallel / max_rounds keep their UI ranges.
        for key, cast, message in (
            ("reply_timeout", float, "reply_timeout 必须为大于 0 的数字"),
            ("inject_max_length", int, "inject_max_length 必须为正整数"),
        ):
            value = merged[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise AgentTeamsServiceError(message)
            merged[key] = cast(value)
        for key, high in (("max_parallel", 5), ("max_rounds", 20)):
            value = merged[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 < value <= high
            ):
                raise AgentTeamsServiceError(f"{key} 必须在 1..{high} 之间")
            merged[key] = int(value)
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
        # Validate the config up front too, so a bad payload never leaks
        # already-created WebChat sessions.
        config = self._merged_config(payload.get("config"))

        # Validate member names and the coordinator up front so a bad payload
        # never leaks already-created WebChat sessions.
        raw_names: list[str] = []
        for raw in raw_members:
            if not isinstance(raw, dict):
                raise AgentTeamsServiceError("成员配置格式错误")
            raw_name = str(raw.get("name") or "").strip()
            if not raw_name or len(raw_name) > MAX_NAME_LEN:
                raise AgentTeamsServiceError(f"成员名必填且 ≤{MAX_NAME_LEN} 字符")
            if raw_name.lower() in {n.lower() for n in raw_names}:
                raise AgentTeamsServiceError(f"成员名必须 unique: {raw_name}")
            raw_names.append(raw_name)
        if coordinator_name not in raw_names:
            raise AgentTeamsServiceError(
                f"coordinator 必须是成员之一: {coordinator_name!r}"
            )

        members: list[dict] = []
        seen_sessions: set[str] = set()
        for raw in raw_members:
            member = await self._create_member(username, raw)
            if member["session_id"] in seen_sessions:
                raise AgentTeamsServiceError("成员会话冲突，请重试")
            seen_sessions.add(member["session_id"])
            members.append(member)

        team_id = _new_id()
        await self.db.create_agent_team(
            team_id=team_id,
            owner_username=username,
            name=name,
            coordinator_member_id=next(
                m["member_id"] for m in members if m["name"] == coordinator_name
            ),
            members=members,
            config=config,
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
        # Workflows have no FK cascade; purge them explicitly so they do not
        # outlive the team.
        workflows = await self.db.get_agent_team_workflows_by_team(team_id)
        for workflow in workflows:
            await self.db.delete_agent_team_workflow(workflow.workflow_id)
        await self.db.delete_agent_team(team_id)
        return {"message": "团队已删除"}

    # ---------- members ----------

    async def add_member(self, username: str, team_id: str, payload: dict) -> dict:
        team = await self._get_owned(username, team_id)
        if len(team.members) >= MAX_MEMBERS:
            raise AgentTeamsServiceError(f"成员数不能超过 {MAX_MEMBERS}")
        # Check the name before creating the session so a duplicate never
        # leaves an orphan session behind.
        new_name = str(payload.get("name") or "").strip()
        if any(m["name"].lower() == new_name.lower() for m in team.members):
            raise AgentTeamsServiceError(f"成员名必须 unique: {new_name}")
        member = await self._create_member(username, payload)
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

    # ---------- workflows ----------

    MAX_NODES = 20

    @staticmethod
    def _workflow_to_dict(row) -> dict:
        return {
            "workflow_id": row.workflow_id,
            "team_id": row.team_id,
            "name": row.name,
            "graph": row.graph,
            "layout": row.layout,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def validate_member_bindings(graph: dict, members: list[dict]) -> None:
        """Raise when any node references a member not on the team roster.

        Args:
            graph: {nodes, edges} graph dict.
            members: Current team members.

        Raises:
            AgentTeamsServiceError: Listing every missing member id.
        """
        known = {m["member_id"] for m in members}
        missing = sorted(
            {
                str(n.get("member_id"))
                for n in graph.get("nodes") or []
                if n.get("member_id") not in known
            }
        )
        if missing:
            raise AgentTeamsServiceError(f"节点绑定的成员不存在: {', '.join(missing)}")

    def _validate_workflow_payload(self, team: AgentTeam, graph: dict) -> dict:
        """Validate a workflow graph against DAG rules and the team roster.

        Args:
            team: The owning team row.
            graph: {nodes, edges} raw payload.

        Returns:
            The normalized graph dict.

        Raises:
            AgentTeamsServiceError: On any validation failure.
        """
        from astrbot.dashboard.services.agent_team_dag import (
            TeamDAGError,
            validate_dag,
        )

        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        if not isinstance(nodes, list) or len(nodes) < 1:
            raise AgentTeamsServiceError("工作流至少需要 1 个节点")
        if len(nodes) > self.MAX_NODES:
            raise AgentTeamsServiceError(f"节点数不能超过 {self.MAX_NODES}")
        for node in nodes:
            if not str(node.get("task") or "").strip():
                raise AgentTeamsServiceError(f"节点 {node.get('id')!r} 缺少任务模板")
        try:
            validate_dag(nodes, edges)
        except TeamDAGError as e:
            raise AgentTeamsServiceError(str(e)) from e
        self.validate_member_bindings(graph, team.members)
        return {"nodes": nodes, "edges": edges}

    async def create_workflow(self, username: str, team_id: str, payload: dict) -> dict:
        team = await self._get_owned(username, team_id)
        name = str(payload.get("name") or "").strip()
        if not name:
            raise AgentTeamsServiceError("工作流名不能为空")
        graph = self._validate_workflow_payload(team, payload.get("graph") or {})
        workflow_id = _new_id()
        await self.db.create_agent_team_workflow(
            workflow_id=workflow_id,
            team_id=team_id,
            name=name,
            graph=graph,
            layout=payload.get("layout") or {},
        )
        return self._workflow_to_dict(
            await self.db.get_agent_team_workflow(workflow_id)
        )

    async def get_workflows(self, username: str, team_id: str) -> dict:
        await self._get_owned(username, team_id)
        rows = await self.db.get_agent_team_workflows_by_team(team_id)
        return {"workflows": [self._workflow_to_dict(w) for w in rows]}

    async def update_workflow(
        self, username: str, team_id: str, workflow_id: str, payload: dict
    ) -> dict:
        team = await self._get_owned(username, team_id)
        row = await self.db.get_agent_team_workflow(workflow_id)
        if row is None or row.team_id != team_id:
            raise AgentTeamsServiceError(f"工作流 '{workflow_id}' 不存在")
        updates: dict = {}
        if "name" in payload:
            name = str(payload["name"] or "").strip()
            if not name:
                raise AgentTeamsServiceError("工作流名不能为空")
            updates["name"] = name
        if "graph" in payload:
            updates["graph"] = self._validate_workflow_payload(
                team, payload.get("graph") or {}
            )
        else:
            # Re-validate the stored graph against the CURRENT roster so a
            # removed member invalidates dependent workflows immediately.
            self.validate_member_bindings(row.graph, team.members)
        if "layout" in payload:
            updates["layout"] = payload.get("layout") or {}
        if updates:
            await self.db.update_agent_team_workflow(workflow_id, **updates)
        return self._workflow_to_dict(
            await self.db.get_agent_team_workflow(workflow_id)
        )

    async def delete_workflow(
        self, username: str, team_id: str, workflow_id: str
    ) -> dict:
        await self._get_owned(username, team_id)
        row = await self.db.get_agent_team_workflow(workflow_id)
        if row is None or row.team_id != team_id:
            raise AgentTeamsServiceError(f"工作流 '{workflow_id}' 不存在")
        await self.db.delete_agent_team_workflow(workflow_id)
        return {"message": "工作流已删除"}
