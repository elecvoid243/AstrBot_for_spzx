"""Versioned single-task assets and independent runs, shared by all entry points."""

import asyncio
import base64
import copy
import json
import mimetypes
import uuid
from collections.abc import Callable
from string import Formatter
from typing import cast

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012, Schema
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select, update

from astrbot.core import logger
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent_team_execution import execute_team_turn
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import (
    RoleAssetError,
    RoleAssetService,
    RoleOverrides,
)
from astrbot.core.agent_teams.result_tool import (
    ReadArtifactTool,
    StoreTaskFileTool,
    SubmitTaskResultTool,
    validate_result,
)
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import (
    AgentWorkflowArtifact,
    AgentWorkflowEvent,
    AgentWorkflowFile,
    AgentWorkflowRun,
    AgentWorkflowVersion,
    ConversationV2,
    PlatformSession,
)
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.dashboard.services.agent_team_ports import TeamPorts, build_ports_for_test


class WorkflowDefinition(BaseModel):
    """A single task with a pinned role and declared input/output contracts."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=128)
    role_id: str = Field(min_length=1, max_length=128)
    role_version: int = Field(ge=1)
    task: str = Field(min_length=1, max_length=100_000)
    required_inputs: list[str] = Field(default_factory=list, max_length=30)
    overrides: RoleOverrides = Field(default_factory=RoleOverrides)
    output_contract: dict | None = None


class WorkflowSaveRequest(BaseModel):
    """Create an asset or append an immutable version using optimistic locking."""

    model_config = ConfigDict(extra="forbid", strict=True)
    definition: WorkflowDefinition
    expected_version: int | None = Field(default=None, ge=1)


class ArtifactReference(BaseModel):
    """An exact immutable artifact version; latest is never inferred."""

    model_config = ConfigDict(extra="forbid", strict=True)
    artifact_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)


class ArtifactRevisionRequest(BaseModel):
    """Explicit, idempotent revision without downstream scheduling effects."""

    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    content: dict


class WorkflowStartRequest(BaseModel):
    """Start a pinned version with explicit inputs and source identity."""

    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1)
    inputs: dict[str, str] = Field(default_factory=dict)
    source: str = Field(default="dashboard", min_length=1, max_length=512)
    input_artifacts: list[ArtifactReference] = Field(
        default_factory=list, max_length=30
    )


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
        if definition.output_contract is not None:
            try:
                Draft202012Validator.check_schema(definition.output_contract)
                if definition.output_contract.get("type") != "object":
                    raise ValueError("Result contracts must declare an object")
                remaining: list[Schema] = [definition.output_contract]
                resolver = (
                    Registry()
                    .with_resource(
                        "",
                        Resource.from_contents(
                            definition.output_contract,
                            default_specification=DRAFT202012,
                        ),
                    )
                    .resolver()
                )
                while remaining:
                    value = remaining.pop()
                    if isinstance(value, dict):
                        if "$id" in value:
                            raise ValueError(
                                "Use local root-schema definitions instead of schema identifiers"
                            )
                        for key in ("$ref", "$dynamicRef"):
                            if key in value and not value[key].startswith("#"):
                                raise ValueError(
                                    "Only local schema references are allowed"
                                )
                            if key in value:
                                resolver.lookup(value[key])
                        # Only schema locations contain schema keywords; values in
                        # const, enum and default are ordinary result data.
                        remaining.extend(DRAFT202012.subresources_of(value))
            except (SchemaError, ValueError, Unresolvable) as exc:
                raise RoleAssetError(
                    "Invalid output contract",
                    [
                        {
                            "path": "definition.output_contract",
                            "code": "INVALID",
                            "message": str(exc),
                        }
                    ],
                ) from exc
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
        input_refs = [ref.model_dump() for ref in request.input_artifacts]
        for ref in input_refs:
            await self.read_artifact(username, **ref)
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
            "input_artifacts": input_refs,
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
            events = (
                (
                    await session.execute(
                        select(AgentWorkflowEvent)
                        .where(
                            AgentWorkflowEvent.run_id == run_id,
                        )
                        .order_by(col(AgentWorkflowEvent.id))
                    )
                )
                .scalars()
                .all()
            )
            errors = []
            for event in events:
                if event.kind == "result.rejected":
                    errors = event.data["fields"]
                elif event.kind == "result.accepted":
                    errors = []
            artifacts = (
                (
                    await session.execute(
                        select(AgentWorkflowArtifact)
                        .where(
                            AgentWorkflowArtifact.run_id == run_id,
                        )
                        .order_by(col(AgentWorkflowArtifact.version))
                    )
                )
                .scalars()
                .all()
            )
            return {
                "run_id": run_id,
                "status": row.status,
                "snapshot": row.snapshot,
                "state": row.state,
                "result": row.result,
                "pending_result": row.pending_result,
                "validation_errors": errors,
                "events": [
                    {"cursor": event.id, "kind": event.kind, "data": event.data}
                    for event in events
                ],
                "result_ref": {"artifact_id": run_id, "version": 1}
                if artifacts
                else None,
                "artifacts": [
                    {
                        "artifact_id": a.artifact_id,
                        "version": a.version,
                        "summary": json.dumps(a.content, ensure_ascii=False)[:200],
                    }
                    for a in artifacts
                ],
                "read_artifacts": [
                    event.data["reference"]
                    for event in events
                    if event.kind == "artifact.read"
                ],
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
        """Validate and durably accept contracted output without completing early.

        Args:
            username: Actual owner from the bound execution.
            run_id: Owning run.
            attempt_id: Attempt that is authorized to submit.
            payload: Complete output matching the run's frozen task contract.

        Raises:
            RoleAssetError: Invalid result, stale attempt or conflicting submission.
        """
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
            if row.status != "running":
                raise RoleAssetError("Attempt is no longer active", status=409)
            contract = row.snapshot["definition"].get("output_contract")
            fields = validate_result(contract, payload)
            if not fields:
                for index, ref in enumerate(payload.get("files", [])):
                    file = await session.get(AgentWorkflowFile, ref["file_id"])
                    if file is None or file.run_id != run_id:
                        fields.append(
                            {
                                "path": f"result.files.{index}.file_id",
                                "code": "NOT_READY",
                                "message": "File is not ready in this task's managed storage",
                            }
                        )
            if fields:
                session.add(
                    AgentWorkflowEvent(
                        run_id=run_id,
                        kind="result.rejected",
                        data={"fields": fields, "attempt_id": attempt_id},
                    )
                )
                await session.commit()
                raise RoleAssetError("Invalid task result", fields)
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
                await session.rollback()
                await session.refresh(row)
                if (
                    row.state["attempt_id"] == attempt_id
                    and row.pending_result == payload
                ):
                    return
                raise RoleAssetError("Attempt is no longer active", status=409)
            session.add(
                AgentWorkflowEvent(
                    run_id=run_id,
                    kind="result.accepted",
                    data={"attempt_id": attempt_id},
                )
            )
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
            if row.status != "completed":
                if row.pending_result is None or not row.state.get("safe_to_finish"):
                    raise RoleAssetError("No result at a saved safe point", status=409)
                changed = await session.execute(
                    update(AgentWorkflowRun)
                    .where(
                        col(AgentWorkflowRun.run_id) == run_id,
                        col(AgentWorkflowRun.status).in_(
                            ["running", "interrupted", "failed"]
                        ),
                    )
                    .values(status="completed", result=row.pending_result)
                )
                if cast(CursorResult, changed).rowcount == 1:
                    session.add(
                        AgentWorkflowArtifact(
                            artifact_id=run_id,
                            version=1,
                            run_id=run_id,
                            submission_id=row.state["attempt_id"],
                            contract=row.snapshot["definition"].get("output_contract"),
                            content=row.pending_result,
                        )
                    )
                    session.add(
                        AgentWorkflowEvent(
                            run_id=run_id,
                            kind="task.completed",
                            data={
                                "task_id": row.state["task_id"],
                                "attempt_id": row.state["attempt_id"],
                                "reference": {"artifact_id": run_id, "version": 1},
                            },
                        )
                    )
                    await session.commit()
        return await self.get_run(username, run_id)

    async def get_result(self, username: str, run_id: str) -> dict:
        """Read the complete validated result, without display truncation.

        Args:
            username: Authenticated owner.
            run_id: Completed run identity.

        Returns:
            The original immutable task result, including stable file references.

        Raises:
            RoleAssetError: Run is inaccessible or has not submitted a result.
        """
        run = await self.get_run(username, run_id)
        if run["result"] is None:
            raise RoleAssetError("Run has no completed result", status=409)
        return run["result"]

    async def read_artifact(
        self,
        username: str,
        artifact_id: str,
        version: int,
        consumer_run_id: str | None = None,
        attempt_id: str | None = None,
    ) -> dict:
        """Read full content and record actual reads by a pinned consumer.

        Args:
            username: Authenticated owner.
            artifact_id: Stable artifact identity.
            version: Exact immutable version.
            consumer_run_id: Task run recording a read, when invoked by an Agent.
            attempt_id: Active attempt required for task reads.

        Returns:
            Exact reference and complete content.

        Raises:
            RoleAssetError: Inaccessible version or a read outside the pinned inputs.
        """
        async with self.db.get_db() as session:
            artifact = await session.get(AgentWorkflowArtifact, (artifact_id, version))
            origin = (
                await session.get(AgentWorkflowRun, artifact.run_id)
                if artifact
                else None
            )
            if artifact is None or origin is None or origin.owner_username != username:
                raise RoleAssetError("Artifact version not found", status=404)
            ref = {"artifact_id": artifact_id, "version": version}
            if consumer_run_id is not None:
                consumer = await session.get(AgentWorkflowRun, consumer_run_id)
                if (
                    consumer is None
                    or consumer.owner_username != username
                    or consumer.status != "running"
                    or consumer.state["attempt_id"] != attempt_id
                ):
                    raise RoleAssetError(
                        "Consumer attempt is no longer active", status=409
                    )
                if ref not in consumer.snapshot.get("input_artifacts", []):
                    raise RoleAssetError(
                        "Artifact version is not a pinned task input", status=403
                    )
                exists = (
                    (
                        await session.execute(
                            select(AgentWorkflowEvent).where(
                                AgentWorkflowEvent.run_id == consumer_run_id,
                                AgentWorkflowEvent.kind == "artifact.read",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if not any(event.data["reference"] == ref for event in exists):
                    session.add(
                        AgentWorkflowEvent(
                            run_id=consumer_run_id,
                            kind="artifact.read",
                            data={"reference": ref, "attempt_id": attempt_id},
                        )
                    )
                    await session.commit()
            files = []
            for file_ref in artifact.content.get("files", []):
                metadata = (
                    await session.execute(
                        select(
                            AgentWorkflowFile.file_id,
                            AgentWorkflowFile.filename,
                            AgentWorkflowFile.media_type,
                        ).where(AgentWorkflowFile.file_id == file_ref["file_id"])
                    )
                ).first()
                if metadata is not None:
                    files.append(
                        {
                            "file_id": metadata[0],
                            "filename": metadata[1],
                            "media_type": metadata[2],
                        }
                    )
            return {**ref, "content": artifact.content, "files": files}

    async def revise_artifact(
        self, username: str, artifact_id: str, payload: dict
    ) -> dict:
        """Append a validated version without changing any consumer or task result.

        Args:
            username: Authenticated owner explicitly requesting the revision.
            artifact_id: Stable identity of the original output.
            payload: Expected version, idempotency request identity and new content.

        Returns:
            The newly created or previously accepted exact reference.

        Raises:
            RoleAssetError: Invalid fields, inaccessible artifact or conflicting edit.
        """
        try:
            request = ArtifactRevisionRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid artifact revision",
                [
                    {
                        "path": ".".join(map(str, issue["loc"])),
                        "code": issue["type"],
                        "message": issue["msg"],
                    }
                    for issue in exc.errors()
                ],
            ) from exc
        async with self.db.get_db() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentWorkflowArtifact)
                        .where(
                            AgentWorkflowArtifact.artifact_id == artifact_id,
                        )
                        .order_by(col(AgentWorkflowArtifact.version).desc())
                    )
                )
                .scalars()
                .all()
            )
            origin = (
                await session.get(AgentWorkflowRun, rows[0].run_id) if rows else None
            )
            if origin is None or origin.owner_username != username:
                raise RoleAssetError("Artifact version not found", status=404)
            for row in rows:
                if row.submission_id == request.request_id:
                    if row.content != request.content:
                        raise RoleAssetError(
                            "Revision request was already used for different content",
                            status=409,
                        )
                    return {"artifact_id": artifact_id, "version": row.version}
            latest = rows[0]
            if latest.version != request.expected_version:
                raise RoleAssetError(
                    "Artifact was revised elsewhere; reload first", status=409
                )
            fields = validate_result(latest.contract, request.content)
            if not fields:
                for index, file_ref in enumerate(request.content.get("files", [])):
                    file = await session.get(AgentWorkflowFile, file_ref["file_id"])
                    if file is None or file.run_id != latest.run_id:
                        fields.append(
                            {
                                "path": f"result.files.{index}.file_id",
                                "code": "NOT_READY",
                                "message": "File is not ready in this task's managed storage",
                            }
                        )
            if fields:
                raise RoleAssetError("Invalid task result", fields)
            ref = {"artifact_id": artifact_id, "version": latest.version + 1}
            session.add(
                AgentWorkflowArtifact(
                    **ref,
                    run_id=latest.run_id,
                    submission_id=request.request_id,
                    contract=latest.contract,
                    content=request.content,
                )
            )
            session.add(
                AgentWorkflowEvent(
                    run_id=latest.run_id,
                    kind="artifact.revised",
                    data={"reference": ref},
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                repeated = (
                    await session.execute(
                        select(AgentWorkflowArtifact).where(
                            AgentWorkflowArtifact.artifact_id == artifact_id,
                            AgentWorkflowArtifact.submission_id == request.request_id,
                        )
                    )
                ).scalar_one_or_none()
                if repeated is not None and repeated.content == request.content:
                    return {"artifact_id": artifact_id, "version": repeated.version}
                raise RoleAssetError(
                    "Artifact was revised elsewhere; reload first", status=409
                ) from exc
            return ref

    async def store_file(
        self,
        username: str,
        run_id: str,
        attempt_id: str | None,
        filename: str,
        content: bytes,
    ) -> dict:
        """Store complete file bytes before a stable reference can be submitted.

        Args:
            username: Actual authenticated task owner.
            run_id: Owning task run.
            attempt_id: Active attempt, or None for an owner's completed-output revision.
            filename: Display name without directory components.
            content: Complete ready bytes, limited to 50 MiB.

        Returns:
            Stable file identity and display metadata.

        Raises:
            RoleAssetError: Invalid file or inactive attempt.
        """
        if (
            not filename
            or any(char in filename for char in ("/", "\\", "\r", "\n", "\0"))
            or len(filename) > 255
            or len(content) > 50 * 1024 * 1024
        ):
            raise RoleAssetError(
                "Provide a filename and a ready file of at most 50 MiB"
            )
        async with self.db.get_db() as session:
            run = await session.get(AgentWorkflowRun, run_id)
            if run is None or run.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            if not (
                (attempt_id is None and run.status == "completed")
                or (
                    attempt_id is not None
                    and run.status == "running"
                    and run.state["attempt_id"] == attempt_id
                )
            ):
                raise RoleAssetError("Attempt is no longer active", status=409)
            file_id = uuid.uuid4().hex
            media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            session.add(
                AgentWorkflowFile(
                    file_id=file_id,
                    run_id=run_id,
                    filename=filename,
                    media_type=media_type,
                    content=content,
                )
            )
            await session.commit()
            return {"file_id": file_id, "filename": filename, "media_type": media_type}

    async def get_file(
        self, username: str, artifact_id: str, version: int, file_id: str
    ) -> dict:
        """Read ready file bytes through a committed immutable artifact version.

        Args:
            username: Authenticated owner.
            artifact_id: Stable artifact identity.
            version: Exact version whose file is being read.
            file_id: Stable managed file reference in that version.

        Returns:
            Complete bytes, filename and MIME type for preview or download.

        Raises:
            RoleAssetError: Inaccessible or uncommitted file reference.
        """
        artifact = await self.read_artifact(username, artifact_id, version)
        if {"file_id": file_id} not in artifact["content"].get("files", []):
            raise RoleAssetError("Artifact file not found", status=404)
        async with self.db.get_db() as session:
            file = await session.get(AgentWorkflowFile, file_id)
            if file is None:
                raise RoleAssetError("Artifact file not found", status=404)
            return {
                "content": file.content,
                "filename": file.filename,
                "media_type": file.media_type,
            }

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
                """Persist the submission for this authorized attempt.

                Args:
                    payload: Complete output to validate against the frozen contract.
                """
                await self.submit_result(username, run_id, state["attempt_id"], payload)

            tools: list[FunctionTool] = [
                SubmitTaskResultTool(
                    accept, snapshot["definition"].get("output_contract")
                )
            ]
            # Composed schemas can declare files through local references or allOf.
            # The frozen contract decides whether the final file result is valid.
            if snapshot["definition"].get("output_contract") is not None:

                async def store(filename: str, content: bytes) -> dict:
                    """Publish ready bytes for this authorized attempt.

                    Args:
                        filename: Display name without directory components.
                        content: Complete file bytes.

                    Returns:
                        Stable file metadata after durable storage succeeds.
                    """
                    return await self.store_file(
                        username, run_id, state["attempt_id"], filename, content
                    )

                tools.append(StoreTaskFileTool(store))
            if snapshot.get("input_artifacts"):

                async def read(
                    artifact_id: str, version: int, file_id: str | None
                ) -> dict:
                    """Read the exact input and optionally its complete file.

                    Args:
                        artifact_id: Pinned input identity.
                        version: Exact input version.
                        file_id: Optional file within that version.

                    Returns:
                        Full artifact or encoded file content.
                    """
                    artifact = await self.read_artifact(
                        username, artifact_id, version, run_id, state["attempt_id"]
                    )
                    if file_id is None:
                        return artifact
                    file = await self.get_file(username, artifact_id, version, file_id)
                    try:
                        content = file["content"].decode("utf-8")
                        encoding = "utf-8"
                    except UnicodeDecodeError:
                        content = base64.b64encode(file["content"]).decode("ascii")
                        encoding = "base64"
                    return {
                        "artifact_id": artifact_id,
                        "version": version,
                        "file_id": file_id,
                        "filename": file["filename"],
                        "content": content,
                        "encoding": encoding,
                    }

                tools.append(ReadArtifactTool(read))
            AgentTeamToolRegistry.register(member["umo"], tools)
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
                    context="Submit the final result with submit_task_result according to its declared contract. Plain text does not complete this task. Read full input content with read_task_artifact. Pinned input references: "
                    + json.dumps(snapshot.get("input_artifacts", [])),
                    execution={
                        **effective,
                        "config_id": snapshot["profile_id"],
                        "kb_names": effective["knowledge_bases"],
                        "tools": [*effective["tools"], *[tool.name for tool in tools]],
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
            state["reason"] = "The Agent ended without submitting a valid task result."
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
