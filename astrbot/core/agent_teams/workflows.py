"""Versioned single-task assets and independent runs, shared by all entry points."""

import asyncio
import copy
import uuid
from collections.abc import Callable
from string import Formatter
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select, update

from astrbot.core import logger
from astrbot.core.agent_team_execution import execute_team_turn
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import (
    RoleAssetError,
    RoleAssetService,
    RoleOverrides,
)
from astrbot.core.agent_teams.result_tool import SubmitTextResultTool
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import (
    AgentWorkflowRun,
    AgentWorkflowVersion,
    ConversationV2,
    PlatformSession,
)
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.dashboard.services.agent_team_ports import TeamPorts, build_ports_for_test


class WorkflowDefinition(BaseModel):
    """The minimal executable workflow: one role, task and text input contract."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=128)
    role_id: str = Field(min_length=1, max_length=128)
    role_version: int = Field(ge=1)
    task: str = Field(min_length=1, max_length=100_000)
    required_inputs: list[str] = Field(default_factory=list, max_length=30)
    overrides: RoleOverrides = Field(default_factory=RoleOverrides)


class WorkflowSaveRequest(BaseModel):
    """Create an asset or append an immutable version using optimistic locking."""

    model_config = ConfigDict(extra="forbid", strict=True)
    definition: WorkflowDefinition
    expected_version: int | None = Field(default=None, ge=1)


class WorkflowStartRequest(BaseModel):
    """Start a pinned version with explicit inputs and source identity."""

    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1)
    inputs: dict[str, str] = Field(default_factory=dict)
    source: str = Field(default="dashboard", min_length=1, max_length=512)


class WorkflowService:
    """Public asset/run boundary backed by the existing database and execution port."""

    def __init__(
        self,
        db: BaseDatabase,
        roles: RoleAssetService,
        ports_factory: Callable[[str], TeamPorts] | None = None,
    ):
        """Connect immutable assets to role resolution.

        Args:
            db: Existing persistence service.
            roles: Shared role definitions and local resource readiness checks.
            ports_factory: Existing injectable delivery/collection boundary.
        """
        self.db = db
        self.roles = roles
        self.ports_factory = ports_factory or (
            lambda username: build_ports_for_test(
                webchat_queue_mgr,
                username,
                lambda event: None,
            )
        )
        self._tasks: set[asyncio.Task] = set()

    async def save_asset(
        self, username: str, payload: dict, asset_id: str | None = None
    ) -> dict:
        """Save a version without creating any execution context.

        Args:
            username: Authenticated owner.
            payload: Definition and optional expected version.
            asset_id: Existing asset identity, or None for a new asset.

        Returns:
            Persisted asset version.

        Raises:
            RoleAssetError: Invalid fields, inaccessible role or conflicting version.
        """
        try:
            request = WorkflowSaveRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid workflow",
                [
                    {
                        "path": ".".join(map(str, e["loc"])),
                        "code": e["type"],
                        "message": e["msg"],
                    }
                    for e in exc.errors()
                ],
            ) from exc
        definition = request.definition
        names = definition.required_inputs
        if len(set(names)) != len(names) or any(
            not name.isidentifier() for name in names
        ):
            raise RoleAssetError(
                "Input names must be unique identifiers",
                [
                    {
                        "path": "definition.required_inputs",
                        "code": "INVALID",
                        "message": "Use unique input names",
                    }
                ],
            )
        try:
            for _, field, spec, conversion in Formatter().parse(definition.task):
                if field is not None and (field not in names or spec or conversion):
                    raise ValueError("Use only declared {input_name} placeholders")
        except ValueError as exc:
            raise RoleAssetError(
                "Invalid task template",
                [{"path": "definition.task", "code": "INVALID", "message": str(exc)}],
            ) from exc
        await self.roles.get_role(username, definition.role_id, definition.role_version)
        version = 1
        if asset_id is not None:
            current = await self.get_asset(username, asset_id)
            if current["version"] != request.expected_version:
                raise RoleAssetError(
                    "Workflow was edited elsewhere; reload first", status=409
                )
            version = current["version"] + 1
        else:
            asset_id = uuid.uuid4().hex
        async with self.db.get_db() as session:
            session.add(
                AgentWorkflowVersion(
                    asset_id=asset_id,
                    version=version,
                    owner_username=username,
                    definition=definition.model_dump(exclude_none=True),
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                raise RoleAssetError(
                    "Workflow was edited elsewhere; reload first", status=409
                ) from exc
        return await self.get_asset(username, asset_id, version)

    async def get_asset(
        self, username: str, asset_id: str, version: int | None = None
    ) -> dict:
        """Read a pinned or latest definition with its version history.

        Args:
            username: Authenticated owner.
            asset_id: Stable asset identity.
            version: Pinned version, or latest when omitted.

        Returns:
            Definition, identity and available versions.

        Raises:
            RoleAssetError: Asset version is inaccessible.
        """
        async with self.db.get_db() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentWorkflowVersion)
                        .where(
                            AgentWorkflowVersion.asset_id == asset_id,
                            AgentWorkflowVersion.owner_username == username,
                        )
                        .order_by(col(AgentWorkflowVersion.version).desc())
                    )
                )
                .scalars()
                .all()
            )
            row = next(
                (r for r in rows if version is None or r.version == version), None
            )
            if row is None:
                raise RoleAssetError("Workflow version not found", status=404)
            return {
                "asset_id": asset_id,
                "version": row.version,
                "definition": row.definition,
                "versions": [r.version for r in rows],
            }

    async def list_assets(self, username: str) -> dict:
        """List the owner's latest asset versions.

        Args:
            username: Authenticated owner.

        Returns:
            Latest definitions, without runtime state.
        """
        async with self.db.get_db() as session:
            identities = (
                (
                    await session.execute(
                        select(AgentWorkflowVersion.asset_id)
                        .where(
                            AgentWorkflowVersion.owner_username == username,
                        )
                        .distinct()
                        .order_by(AgentWorkflowVersion.asset_id)
                    )
                )
                .scalars()
                .all()
            )
        return {
            "assets": [
                await self.get_asset(username, identity) for identity in identities
            ]
        }

    async def start_run(self, username: str, asset_id: str, payload: dict) -> dict:
        """Persist all execution identities and the effective snapshot before dispatch.

        Args:
            username: Actual initiating owner.
            asset_id: Saved asset identity.
            payload: Pinned version, text inputs and source.

        Returns:
            The new independent run's persisted snapshot.

        Raises:
            RoleAssetError: Invalid inputs or unavailable bound resources.
        """
        try:
            request = WorkflowStartRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid run inputs",
                [
                    {
                        "path": ".".join(map(str, e["loc"])),
                        "code": e["type"],
                        "message": e["msg"],
                    }
                    for e in exc.errors()
                ],
            ) from exc
        asset = await self.get_asset(username, asset_id, request.version)
        definition = asset["definition"]
        required = definition["required_inputs"]
        fields = [
            {
                "path": f"inputs.{name}",
                "code": "REQUIRED",
                "message": "Provide non-empty text",
            }
            for name in required
            if not request.inputs.get(name, "").strip()
        ] + [
            {
                "path": f"inputs.{name}",
                "code": "UNKNOWN",
                "message": "Input is not declared",
            }
            for name in request.inputs
            if name not in required
        ]
        if fields:
            raise RoleAssetError("Invalid run inputs", fields)
        role = await self.roles.resolve_role(
            username,
            definition["role_id"],
            definition["role_version"],
            definition["overrides"],
        )
        if not role["ready"]:
            raise RoleAssetError("Role resources are not ready", role["issues"])
        profile_id = role["installation"]["profile_id"]
        core = self.roles.core_lifecycle
        profile = core.astrbot_config_mgr.confs[profile_id] if core else {}
        if profile.get("agent_runner", {}).get("runner_type", "local") != "local":
            raise RoleAssetError(
                "Single-task workflows require the built-in Agent executor",
                [
                    {
                        "path": "installation.profile_id",
                        "code": "INCOMPATIBLE",
                        "message": "Explicit result submission requires the built-in Agent executor",
                    }
                ],
            )
        # Credentials stay in the local provider manager. Freeze the pipeline's
        # behavior settings, never provider/platform definitions or dashboard auth.
        profile_snapshot = copy.deepcopy(
            {
                key: value
                for key, value in profile.items()
                if key not in {"provider", "provider_sources", "platform", "dashboard"}
            }
        )
        effective = copy.deepcopy(role["effective"])
        persona = next(
            (
                p
                for p in (await self.roles.get_resources())["personas"]
                if p["id"] == effective.get("persona_id")
            ),
            None,
        )
        if core and effective.get("persona_id"):
            persona = copy.deepcopy(
                core.persona_mgr.get_persona_v3_by_id(effective["persona_id"])
            )
        session_id = uuid.uuid4().hex
        member = {
            "conversation_id": str(uuid.uuid4()),
            "member_id": uuid.uuid4().hex,
            "session_id": session_id,
            "umo": f"webchat:FriendMessage:{session_id}",
            "name": role["definition"]["name"],
            "system_prompt": effective["prompt"],
        }
        snapshot = {
            "engine": "single_task_v2",
            "asset_id": asset_id,
            "asset_version": request.version,
            "definition": definition,
            "role": role,
            "effective": effective,
            "profile_id": profile_id,
            "profile": profile_snapshot,
            "persona": persona,
            "inputs": request.inputs,
            "source": request.source,
            "initiator": username,
            "task": definition["task"].format_map(request.inputs),
            "member": member,
        }
        run_id = uuid.uuid4().hex
        state = {
            "scope_id": uuid.uuid4().hex,
            "task_id": uuid.uuid4().hex,
            "attempt_id": uuid.uuid4().hex,
            "message_id": uuid.uuid4().hex,
            "safe_to_finish": False,
            "reason": None,
            "response": None,
        }
        async with self.db.get_db() as session:
            session.add(
                ConversationV2(
                    conversation_id=member["conversation_id"],
                    platform_id="webchat",
                    user_id=member["umo"],
                    content=[],
                    title=definition["name"],
                    persona_id=effective.get("persona_id"),
                )
            )
            session.add(
                PlatformSession(
                    session_id=session_id,
                    creator=username,
                    display_name=definition["name"],
                    archived=1,
                )
            )
            session.add(
                AgentWorkflowRun(
                    run_id=run_id,
                    owner_username=username,
                    snapshot=snapshot,
                    state=state,
                )
            )
            await session.commit()
        task = asyncio.create_task(self._execute(username, run_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await self.get_run(username, run_id)

    async def get_run(self, username: str, run_id: str) -> dict:
        """Query persisted progress independently of any active runner or browser.

        Args:
            username: Authenticated owner.
            run_id: Independent execution identity.

        Returns:
            Frozen configuration, progress, attempt, process and submitted result.

        Raises:
            RoleAssetError: Run is inaccessible.
        """
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            if row is None or row.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            return {
                "run_id": run_id,
                "status": row.status,
                "snapshot": row.snapshot,
                "state": row.state,
                "result": row.result,
                "pending_result": row.pending_result,
            }

    async def list_runs(self, username: str, limit: int = 50) -> dict:
        """List persisted runs, including inactive and interrupted executions.

        Args:
            username: Authenticated owner.
            limit: Maximum number of recent records, capped at 100.

        Returns:
            Recent run snapshots.
        """
        async with self.db.get_db() as session:
            identities = (
                (
                    await session.execute(
                        select(AgentWorkflowRun.run_id)
                        .where(
                            AgentWorkflowRun.owner_username == username,
                        )
                        .order_by(col(AgentWorkflowRun.created_at).desc())
                        .limit(max(1, min(limit, 100)))
                    )
                )
                .scalars()
                .all()
            )
        return {
            "runs": [await self.get_run(username, identity) for identity in identities]
        }

    async def submit_result(
        self, username: str, run_id: str, attempt_id: str, payload: dict
    ) -> None:
        """Validate and durably accept the default text result without completing early.

        Args:
            username: Actual owner from the bound execution.
            run_id: Owning run.
            attempt_id: Attempt that is authorized to submit.
            payload: Default text contract, containing only non-empty text.

        Raises:
            RoleAssetError: Invalid result, stale attempt or conflicting submission.
        """
        if (
            set(payload) != {"text"}
            or not isinstance(payload.get("text"), str)
            or not payload["text"].strip()
        ):
            raise RoleAssetError(
                "Invalid text result",
                [
                    {
                        "path": "result.text",
                        "code": "REQUIRED",
                        "message": "Provide non-empty text",
                    }
                ],
            )
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            if row is None or row.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            if row.state["attempt_id"] != attempt_id:
                raise RoleAssetError("Attempt is no longer active", status=409)
            if row.pending_result is not None:
                if row.pending_result == payload:
                    return
                raise RoleAssetError(
                    "A different result was already submitted", status=409
                )
            changed = await session.execute(
                update(AgentWorkflowRun)
                .where(
                    col(AgentWorkflowRun.run_id) == run_id,
                    col(AgentWorkflowRun.status) == "running",
                    col(AgentWorkflowRun.pending_result).is_(None),
                )
                .values(pending_result=payload)
            )
            if cast(CursorResult, changed).rowcount != 1:
                raise RoleAssetError("Attempt is no longer active", status=409)
            await session.commit()

    async def finish_submission(self, username: str, run_id: str) -> dict:
        """Idempotently finish an accepted intent only after the saved safe point.

        Args:
            username: Authenticated owner.
            run_id: Run whose tools have finished and response has been saved.

        Returns:
            Persisted progress after consistent result completion.

        Raises:
            RoleAssetError: No accepted result or the execution is not yet safe.
        """
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            if row is None or row.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            if row.status == "completed":
                return await self.get_run(username, run_id)
            if row.pending_result is None or not row.state.get("safe_to_finish"):
                raise RoleAssetError("No result at a saved safe point", status=409)
            await session.execute(
                update(AgentWorkflowRun)
                .where(
                    col(AgentWorkflowRun.run_id) == run_id,
                    col(AgentWorkflowRun.status).in_(
                        ["running", "interrupted", "failed"]
                    ),
                )
                .values(status="completed", result=row.pending_result)
            )
            await session.commit()
        return await self.get_run(username, run_id)

    async def get_result(self, username: str, run_id: str) -> dict:
        """Read the complete default text result, without display truncation.

        Args:
            username: Authenticated owner.
            run_id: Completed run identity.

        Returns:
            The immutable text result.

        Raises:
            RoleAssetError: Run is inaccessible or has not submitted a result.
        """
        run = await self.get_run(username, run_id)
        if run["result"] is None:
            raise RoleAssetError("Run has no completed result", status=409)
        return run["result"]

    async def boot_sweep(self) -> None:
        """Finish saved safe intents and interrupt other abandoned executions.

        Returns:
            None. No model or tool is dispatched; unsafe intents remain readable.
        """
        async with self.db.get_db() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentWorkflowRun).where(
                            col(AgentWorkflowRun.status).in_(
                                ["running", "interrupted", "failed"]
                            ),
                            col(AgentWorkflowRun.pending_result).is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            safe_intents = [
                (row.owner_username, row.run_id)
                for row in rows
                if row.state.get("safe_to_finish")
            ]
            await session.execute(
                update(AgentWorkflowRun)
                .where(
                    col(AgentWorkflowRun.status) == "running",
                )
                .values(status="interrupted")
            )
            await session.commit()

        for username, run_id in safe_intents:
            try:
                await self.finish_submission(username, run_id)
            except Exception:
                logger.exception(
                    "Failed to finish saved workflow submission: %s", run_id
                )

    async def shutdown(self) -> None:
        """Stop core-owned tasks before providers and database are terminated.

        Returns:
            None after every execution has released its tools and subscriptions.
        """
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(self, username: str, run_id: str) -> None:
        """Advance the single saved attempt through the existing Agent pipeline.

        Args:
            username: Original owner.
            run_id: Already persisted run to dispatch.
        """
        run = await self.get_run(username, run_id)
        snapshot, state = run["snapshot"], run["state"]
        member, effective = snapshot["member"], snapshot["effective"]
        ports = None
        status = "failed"
        try:
            await self.roles.validate_frozen_role(snapshot["role"])
            ports = self.ports_factory(username)

            async def accept(payload: dict) -> None:
                await self.submit_result(username, run_id, state["attempt_id"], payload)

            AgentTeamToolRegistry.register(
                member["umo"], [SubmitTextResultTool(accept)]
            )
            reply, parts = await asyncio.wait_for(
                execute_team_turn(
                    ports=ports,
                    member=member,
                    run_id=run_id,
                    team_id=snapshot["asset_id"],
                    username=username,
                    turn_id=state["attempt_id"],
                    node_id=state["task_id"],
                    text=snapshot["task"],
                    context="Submit the final result with submit_task_result(text). Plain text does not complete this task.",
                    execution={
                        **effective,
                        "config_id": snapshot["profile_id"],
                        "kb_names": effective["knowledge_bases"],
                        "tools": [*effective["tools"], "submit_task_result"],
                        "message_id": state["message_id"],
                        "profile_snapshot": snapshot["profile"],
                        "persona_snapshot": snapshot["persona"],
                    },
                ),
                timeout=ports.reply_timeout,
            )
            state["response"] = {
                "text": reply,
                "parts": parts,
                "member_id": member["member_id"],
                "task_id": state["task_id"],
                "attempt_id": state["attempt_id"],
                "message_id": state["message_id"],
            }
            state["safe_to_finish"] = True
            async with self.db.get_db() as session:
                await session.execute(
                    update(AgentWorkflowRun)
                    .where(
                        col(AgentWorkflowRun.run_id) == run_id,
                        col(AgentWorkflowRun.status) == "running",
                    )
                    .values(state=state)
                )
                await session.commit()
            current = await self.get_run(username, run_id)
            if current["pending_result"] is not None:
                await self.finish_submission(username, run_id)
                return
            status = "awaiting_result"
            state["reason"] = "The Agent ended without submitting a text result."
        except asyncio.CancelledError:
            status = "interrupted"
            state["reason"] = "Core execution stopped; this task was not replayed."
        except RoleAssetError as exc:
            status = "blocked"
            state["reason"] = str(exc)
            state["issues"] = exc.fields
        except Exception as exc:
            state["reason"] = str(exc)
            logger.error("Single-task workflow execution failed: %s", exc)
        finally:
            AgentTeamToolRegistry.unregister(member["umo"])
            if ports and ports.close:
                await ports.close()
        async with self.db.get_db() as session:
            await session.execute(
                update(AgentWorkflowRun)
                .where(
                    col(AgentWorkflowRun.run_id) == run_id,
                    col(AgentWorkflowRun.status) == "running",
                )
                .values(status=status, state=state)
            )
            await session.commit()
