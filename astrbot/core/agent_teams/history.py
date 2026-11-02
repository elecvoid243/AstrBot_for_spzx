"""Owner-scoped history of persisted workflow and autonomous team runs."""

from sqlmodel import col, select

from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import AgentWorkflowRun


class RunHistoryService:
    """Page root runs without mixing chat conversations or retired engines."""

    def __init__(self, db: BaseDatabase, workflows: WorkflowService):
        """Connect the shared persisted run service.

        Args:
            db: Shared application database.
            workflows: Asset and run service enforcing ownership.
        """
        self.db = db
        self.workflows = workflows

    async def list_records(
        self, username: str, limit: int = 20, offset: int = 0
    ) -> dict:
        """Page owned root runs without loading full results or process content.

        Args:
            username: Authenticated Dashboard owner.
            limit: Maximum number of records, capped at 100.
            offset: Number of records already read.

        Returns:
            Run overviews and the next page offset when present.
        """
        limit, offset = max(1, min(limit, 100)), max(0, offset)
        async with self.db.get_db() as session:
            rows = (
                await session.execute(
                    select(
                        col(AgentWorkflowRun.run_id),
                        col(AgentWorkflowRun.created_at),
                    )
                    .where(
                        col(AgentWorkflowRun.owner_username) == username,
                        AgentWorkflowRun.snapshot["root_run_id"]
                        .as_string()
                        .is_not(None),
                        col(AgentWorkflowRun.snapshot)["parent_run_id"]
                        .as_string()
                        .is_(None),
                    )
                    .order_by(
                        col(AgentWorkflowRun.created_at).desc(),
                        col(AgentWorkflowRun.run_id).desc(),
                    )
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
        records = []
        for identity, created_at in rows[:limit]:
            run = await self.workflows.get_run(
                username, identity, include_details=False
            )
            records.append(
                {
                    "source": {"kind": "v2_run", "id": identity},
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
