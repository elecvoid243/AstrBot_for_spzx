"""Read original legacy records alongside v2 runs without execution conversion."""

from sqlalchemy import literal, union_all
from sqlalchemy import select as sa_select
from sqlmodel import col, select

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import (
    AgentLegacyMigration,
    AgentTeam,
    AgentTeamRun,
    AgentTeamRunMessage,
    AgentWorkflowRun,
    ConversationV2,
    PlatformSession,
)
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.dashboard.services.agent_team_run_service import AgentTeamRunService
from astrbot.dashboard.services.agent_team_service import AgentTeamsServiceError


class RunHistoryService:
    """Owner-scoped history backed by the original run and conversation stores."""

    def __init__(
        self, db: BaseDatabase, workflows: WorkflowService, legacy: AgentTeamRunService
    ):
        """Connect existing public services without creating another executor.

        Args:
            db: Shared application database.
            workflows: V2 asset and run service.
            legacy: Existing legacy execution service, including its live registry.
        """
        self.db = db
        self.workflows = workflows
        self.legacy = legacy

    async def _migrated_assets(
        self, username: str, references: list[tuple[str, str | None]]
    ) -> list[dict]:
        """Locate receipt targets, then enforce the normal asset read boundary.

        Args:
            username: Actual authenticated owner of the checked source.
            references: Source teams and optional exact legacy workflow identities.

        Returns:
            Accessible migrated definitions suitable for a separate new-run flow.
        """
        assets = {}
        for team_id, workflow_id in references:
            async with self.db.get_db() as session:
                receipt = await session.get(AgentLegacyMigration, (username, team_id))
            if receipt is None:
                continue
            mapping = receipt.result["mapping"]
            target = (
                mapping.get("workflows", {}).get(workflow_id)
                if workflow_id
                else mapping.get("team")
            )
            if target is None:
                continue
            try:
                asset = await self.workflows.get_asset(username, target["asset_id"])
            except RoleAssetError as exc:
                if exc.status == 404:
                    continue
                raise
            assets[asset["asset_id"]] = {
                "asset_id": asset["asset_id"],
                "version": asset["version"],
                "name": asset["definition"]["name"],
                "kind": asset["definition"].get("kind", "workflow"),
            }
        return list(assets.values())

    async def _member_sources(self, username: str) -> dict[str, list[dict]]:
        """Resolve long-term source links using both team and session ownership.

        Args:
            username: Actual authenticated owner.

        Returns:
            Canonical message origins and their owned legacy member associations.
        """
        async with self.db.get_db() as session:
            teams = (
                (
                    await session.execute(
                        select(AgentTeam).where(
                            col(AgentTeam.owner_username) == username
                        )
                    )
                )
                .scalars()
                .all()
            )
            sessions = set(
                (
                    await session.execute(
                        select(col(PlatformSession.session_id)).where(
                            col(PlatformSession.creator) == username,
                            col(PlatformSession.platform_id) == "webchat",
                        )
                    )
                )
                .scalars()
                .all()
            )
        sources: dict[str, list[dict]] = {}
        for team in teams:
            for member in team.members:
                session_id = member.get("session_id")
                if session_id not in sessions:
                    continue
                # Stored umo text is not authority to read a different session.
                umo = str(
                    MessageSession("webchat", MessageType.FRIEND_MESSAGE, session_id)
                )
                sources.setdefault(umo, []).append(
                    {
                        "team_id": team.team_id,
                        "team_name": team.name,
                        "member_id": member["member_id"],
                        "member_name": member.get("name", member["member_id"]),
                        "session_id": session_id,
                    }
                )
        return sources

    async def list_records(
        self, username: str, limit: int = 20, offset: int = 0
    ) -> dict:
        """Page original histories without loading conversation content into the list.

        Args:
            username: Actual authenticated owner.
            limit: Maximum number of records, capped at 100.
            offset: Number of records already read.

        Returns:
            Source-labelled overviews and a next page offset when present.
        """
        sources = await self._member_sources(username)
        limit, offset = max(1, min(limit, 100)), max(0, offset)
        async with self.db.get_db() as session:
            histories = union_all(
                select(
                    literal("legacy_conversation").label("kind"),
                    col(ConversationV2.conversation_id).label("identity"),
                    col(ConversationV2.created_at).label("created_at"),
                ).where(
                    col(ConversationV2.platform_id) == "webchat",
                    col(ConversationV2.user_id).in_(sources),
                ),
                select(
                    literal("legacy_run"),
                    col(AgentTeamRun.run_id),
                    col(AgentTeamRun.created_at),
                )
                .join(AgentTeam, col(AgentTeam.team_id) == col(AgentTeamRun.team_id))
                .where(col(AgentTeam.owner_username) == username),
                select(
                    literal("v2_run"),
                    col(AgentWorkflowRun.run_id),
                    col(AgentWorkflowRun.created_at),
                ).where(
                    col(AgentWorkflowRun.owner_username) == username,
                    col(AgentWorkflowRun.snapshot)["parent_run_id"]
                    .as_string()
                    .is_(None),
                ),
            ).subquery()
            rows = (
                await session.execute(
                    sa_select(histories)
                    .order_by(
                        histories.c.created_at.desc(),
                        histories.c.kind.desc(),
                        histories.c.identity.desc(),
                    )
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
        records = []
        for kind, identity, created_at in rows[:limit]:
            if kind == "legacy_conversation":
                async with self.db.get_db() as session:
                    title = (
                        await session.execute(
                            select(col(ConversationV2.title)).where(
                                col(ConversationV2.conversation_id) == identity
                            )
                        )
                    ).scalar_one()
                records.append(
                    {
                        "source": {"kind": kind, "id": identity},
                        "engine": "legacy",
                        "read_only": True,
                        "title": title or identity,
                        "status": "saved",
                        "goal": "",
                        "created_at": created_at,
                    }
                )
            elif kind == "legacy_run":
                run = await self.legacy.get_history_run(username, identity)
                records.append(
                    {
                        "source": {"kind": kind, "id": identity},
                        "engine": "legacy",
                        "read_only": True,
                        "title": run["team_name"],
                        "status": run["history_status"],
                        "goal": run["input"][:200],
                        "created_at": created_at,
                    }
                )
            else:
                run = await self.workflows.get_run(
                    username, identity, include_details=False
                )
                records.append(
                    {
                        "source": {"kind": kind, "id": identity},
                        "engine": "v2",
                        "read_only": False,
                        "title": run["snapshot"]["definition"]["name"],
                        "status": run["status"],
                        "goal": run["snapshot"].get("task", ""),
                        "created_at": created_at,
                    }
                )
        return {
            "records": records,
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    async def get_record(
        self, username: str, kind: str, identity: str, offset: int = 0, limit: int = 50
    ) -> dict:
        """Read one source record with its original identifiers and content.

        Args:
            username: Actual authenticated owner.
            kind: Explicit source discriminator; legacy identities are never v2 runs.
            identity: Original persisted source identity.
            offset: First long-term message to read, in original order.
            limit: Maximum number of long-term messages, capped at 100.

        Returns:
            A source-labelled record, with legacy content explicitly read-only.

        Raises:
            RoleAssetError: The record is inaccessible or its source is unknown.
        """
        if kind == "v2_run":
            return {
                "source": {"kind": kind, "id": identity},
                "engine": "v2",
                "read_only": False,
                "run": await self.workflows.get_run(username, identity),
            }
        if kind == "legacy_conversation":
            sources = await self._member_sources(username)
            conversation = await self.db.get_conversation_by_id(identity)
            if (
                conversation is None
                or conversation.platform_id != "webchat"
                or conversation.user_id not in sources
            ):
                raise RoleAssetError("History record not found", status=404)
            content = conversation.content or []
            limit, offset = max(1, min(limit, 100)), max(0, offset)
            return {
                "source": {"kind": kind, "id": identity},
                "engine": "legacy",
                "read_only": True,
                "source_links": sources[conversation.user_id],
                "migrated_assets": await self._migrated_assets(
                    username,
                    [
                        (source["team_id"], None)
                        for source in sources[conversation.user_id]
                    ],
                ),
                "conversation": {
                    "conversation_id": identity,
                    "title": conversation.title,
                    "messages": content[offset : offset + limit],
                    "next_offset": offset + limit
                    if len(content) > offset + limit
                    else None,
                },
            }
        if kind != "legacy_run":
            raise RoleAssetError("History record not found", status=404)
        try:
            run = await self.legacy.get_history_run(username, identity)
        except AgentTeamsServiceError as exc:
            raise RoleAssetError("History record not found", status=404) from exc
        async with self.db.get_db() as session:
            members = (
                (
                    await session.execute(
                        select(col(AgentTeamRunMessage.member_id))
                        .where(col(AgentTeamRunMessage.run_id) == identity)
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
        return {
            "source": {"kind": kind, "id": identity},
            "engine": "legacy",
            "read_only": True,
            "run": run,
            "migrated_assets": await self._migrated_assets(
                username, [(run["team_id"], run["workflow_id"])]
            ),
            "members": sorted(
                set(members)
                | {
                    state["member_id"]
                    for state in run["node_states"].values()
                    if state.get("member_id")
                }
            ),
        }

    async def get_process(
        self,
        username: str,
        run_id: str,
        member_id: str,
        before_id: int | None = None,
        limit: int = 50,
    ) -> dict:
        """Read original run transcript pages without interpreting them as checkpoints.

        Args:
            username: Actual authenticated owner.
            run_id: Original legacy run identity.
            member_id: Original member identity, scoped to this run.
            before_id: Exclusive original transcript cursor.
            limit: Maximum number of rows, capped at 100.

        Returns:
            Original chronological rows and their pagination cursor.

        Raises:
            RoleAssetError: The run is inaccessible.
        """
        await self.get_record(username, "legacy_run", run_id)
        limit = max(1, min(limit, 100))
        page = await self.legacy.get_transcript(
            username, run_id, member_id, before_id, limit + 1
        )
        messages = page["messages"][-limit:]
        return {
            "messages": messages,
            "next_before_id": messages[0]["id"]
            if len(page["messages"]) > limit
            else None,
        }
