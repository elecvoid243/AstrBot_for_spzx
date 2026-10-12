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
from sqlalchemy import func, true
from sqlalchemy import select as sa_select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select, text, update

from astrbot.core import logger
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent_team_execution import (
    CheckpointRecoveryError,
    ProcessPersistenceError,
    execute_team_turn,
)
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import (
    RoleAssetError,
    RoleAssetService,
    RoleOverrides,
)
from astrbot.core.agent_teams.graph import GraphDefinition, validate_graph
from astrbot.core.agent_teams.result_tool import (
    ReadArtifactTool,
    StoreTaskFileTool,
    SubmitTaskResultTool,
    validate_result,
)
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import (
    AgentWorkflowArtifact,
    AgentWorkflowCheckpoint,
    AgentWorkflowEvent,
    AgentWorkflowFile,
    AgentWorkflowProcess,
    AgentWorkflowRun,
    AgentWorkflowSchedule,
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
    definition: WorkflowDefinition | GraphDefinition
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
        self._closing = False

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
        if isinstance(definition, GraphDefinition):
            for task in definition.tasks:
                await self._validate_task_definition(
                    username,
                    WorkflowDefinition(
                        name=task.name,
                        role_id=task.role_id,
                        role_version=task.role_version,
                        task=task.task,
                        required_inputs=list(
                            dict.fromkeys([*definition.required_inputs, *task.inputs])
                        ),
                        overrides=task.overrides,
                        output_contract=task.output_contract,
                    ),
                )
            validate_graph(definition)
        else:
            await self._validate_task_definition(username, definition)
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
                    # Null is a meaningful condition value, not an unset option.
                    definition=definition.model_dump(
                        exclude_none=not isinstance(definition, GraphDefinition)
                    ),
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                raise RoleAssetError(
                    "Workflow was edited elsewhere; reload first", status=409
                ) from exc
        return await self.get_asset(username, asset_id, version)

    async def _validate_task_definition(
        self, username: str, definition: WorkflowDefinition
    ) -> None:
        """Validate the shared role, template and contracted output semantics.

        Args:
            username: Authenticated asset author.
            definition: One task, from either a single-task asset or a graph.

        Raises:
            RoleAssetError: Invalid contract, template or inaccessible pinned role.
        """
        if definition.output_contract is not None:
            try:
                Draft202012Validator.check_schema(definition.output_contract)
                if definition.output_contract.get("type") != "object":
                    raise ValueError("Result contracts must declare an object")
                remaining: list[Schema] = [definition.output_contract]
                visited: set[int] = set()
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
                    if id(value) in visited:
                        continue
                    visited.add(id(value))
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
                                # References can turn annotation data into schemas.
                                target = resolver.lookup(value[key]).contents
                                Draft202012Validator.check_schema(target)
                                remaining.append(target)
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
        if "tasks" in definition:
            return await self._start_graph(username, asset, request)
        frozen = await self._freeze_member(username, definition)
        effective = frozen["effective"]
        member = frozen["member"]
        session_id = member["session_id"]
        snapshot = {
            "engine": "single_task_v2",
            "asset_id": asset_id,
            "asset_version": request.version,
            "definition": definition,
            **frozen,
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
            session.add(
                AgentWorkflowEvent(
                    run_id=run_id,
                    kind="run.started",
                    data={"attempt_id": state["attempt_id"]},
                )
            )
            await session.commit()
        task = asyncio.create_task(self._execute(username, run_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await self.get_run(username, run_id)

    async def _freeze_member(self, username: str, definition: dict) -> dict:
        """Freeze role capabilities and create an isolated member identity.

        Args:
            username: Actual initiating owner.
            definition: Task with a pinned role and explicit overrides.

        Returns:
            Frozen role, effective settings, safe profile, persona and member.

        Raises:
            RoleAssetError: Bound resources are missing or incompatible.
        """
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
        return {
            "role": role,
            "effective": effective,
            "profile_id": profile_id,
            "profile": profile_snapshot,
            "persona": persona,
            "member": member,
        }

    async def get_run(
        self, username: str, run_id: str, *, include_details: bool = True
    ) -> dict:
        """Query persisted progress independently of any active runner or browser.

        Args:
            username: Authenticated owner.
            run_id: Independent execution identity.
            include_details: Include complete frozen inputs, results and history.

        Returns:
            Progress, attempts and artifact entries, with complete detail on request.

        Raises:
            RoleAssetError: Run is inaccessible.
        """
        async with self.db.get_db() as session:
            # SQLite's legacy transaction mode does not begin on SELECT. Pin all
            # graph facts and their cursor to one read snapshot before querying.
            if self.db.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN"))
            row = await session.get(AgentWorkflowRun, run_id)
            if row is None or row.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            identities = [run_id, *row.snapshot.get("task_runs", {}).values()]
            rows = (
                (
                    await session.execute(
                        select(AgentWorkflowRun).where(
                            col(AgentWorkflowRun.run_id).in_(identities),
                            AgentWorkflowRun.owner_username == username,
                        )
                    )
                )
                .scalars()
                .all()
            )
            events = (
                (
                    (
                        await session.execute(
                            select(AgentWorkflowEvent)
                            .where(
                                col(AgentWorkflowEvent.run_id).in_(identities),
                            )
                            .order_by(col(AgentWorkflowEvent.id))
                        )
                    )
                    .scalars()
                    .all()
                )
                if include_details
                else []
            )
            artifacts = (
                (
                    await session.execute(
                        select(AgentWorkflowArtifact)
                        .where(
                            col(AgentWorkflowArtifact.run_id).in_(identities),
                        )
                        .order_by(col(AgentWorkflowArtifact.version))
                    )
                )
                .scalars()
                .all()
            )
            process = (
                (
                    (
                        await session.execute(
                            select(AgentWorkflowProcess)
                            .where(col(AgentWorkflowProcess.run_id).in_(identities))
                            .order_by(
                                col(AgentWorkflowProcess.created_at),
                                col(AgentWorkflowProcess.record_id),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if include_details
                else []
            )
            checkpoints = (
                {
                    item.run_id: item
                    for item in (
                        await session.execute(
                            sa_select(
                                col(AgentWorkflowCheckpoint.run_id),
                                col(AgentWorkflowCheckpoint.phase),
                                col(AgentWorkflowCheckpoint.invocation_id),
                                col(AgentWorkflowCheckpoint.started_tools),
                                col(AgentWorkflowCheckpoint.result_submission),
                                AgentWorkflowCheckpoint.data["step_budget_used"]
                                .as_integer()
                                .label("steps_used"),
                                AgentWorkflowCheckpoint.data["model_calls"]
                                .as_integer()
                                .label("model_calls"),
                            ).where(col(AgentWorkflowCheckpoint.run_id).in_(identities))
                        )
                    ).all()
                }
                if include_details
                else {}
            )
            results = {}
            for current in rows:
                boundary = checkpoints.get(current.run_id)
                current_events = [
                    event for event in events if event.run_id == current.run_id
                ]
                current_artifacts = [
                    artifact
                    for artifact in artifacts
                    if artifact.run_id == current.run_id
                ]
                errors = []
                for event in current_events:
                    if event.kind == "result.rejected":
                        errors = event.data["fields"]
                    elif event.kind == "result.accepted":
                        errors = []
                results[current.run_id] = {
                    "run_id": current.run_id,
                    "status": current.status,
                    "result_ref": {"artifact_id": current.run_id, "version": 1}
                    if current_artifacts
                    else None,
                    "artifacts": [
                        {
                            "artifact_id": a.artifact_id,
                            "version": a.version,
                            "summary": json.dumps(a.content, ensure_ascii=False)[:200],
                        }
                        for a in current_artifacts
                    ],
                }
                if include_details:
                    results[current.run_id].update(
                        {
                            "recovery": {
                                "capability": "saved_model_response"
                                if "member" in current.snapshot
                                else "task_boundaries",
                                "available": current.status
                                in {"interrupted", "failed", "blocked"}
                                and boundary is not None
                                and (
                                    not boundary.started_tools
                                    or boundary.result_submission
                                ),
                                "boundary": boundary.phase if boundary else None,
                                "invocation_id": boundary.invocation_id
                                if boundary
                                else None,
                                "steps_used": boundary.steps_used if boundary else None,
                                "model_calls": boundary.model_calls
                                if boundary
                                else None,
                                "reason": "Started tools require confirmation; tool-result recovery is not supported yet."
                                if boundary
                                and boundary.started_tools
                                and not boundary.result_submission
                                else "No saved execution checkpoint."
                                if boundary is None
                                else None,
                            },
                            "snapshot": current.snapshot,
                            "state": current.state,
                            "result": current.result,
                            "pending_result": current.pending_result,
                            "cursor": current_events[-1].id if current_events else 0,
                            "process": [
                                record.model_dump(mode="json")
                                for record in process
                                if record.run_id == current.run_id
                            ],
                            "validation_errors": errors,
                            "events": [
                                {
                                    "cursor": event.id,
                                    "kind": event.kind,
                                    "data": event.data,
                                    "run_id": current.run_id,
                                    "task_id": current.state.get("task_id"),
                                    "attempt_id": event.data.get(
                                        "attempt_id", current.state.get("attempt_id")
                                    ),
                                    "member_id": current.snapshot.get("member", {}).get(
                                        "member_id"
                                    ),
                                }
                                for event in current_events
                            ],
                            "read_artifacts": [
                                event.data["reference"]
                                for event in current_events
                                if event.kind == "artifact.read"
                            ],
                        }
                    )
                else:
                    results[current.run_id].update(
                        {
                            "snapshot": {
                                **{
                                    key: current.snapshot[key]
                                    for key in (
                                        "engine",
                                        "asset_id",
                                        "asset_version",
                                        "source",
                                        "node_id",
                                    )
                                    if key in current.snapshot
                                },
                                "definition": {
                                    "name": current.snapshot["definition"]["name"]
                                },
                                "task": current.snapshot.get(
                                    "task", current.snapshot["definition"]["name"]
                                )[:200],
                            },
                            "state": {
                                key: current.state[key]
                                for key in ("task_id", "attempt_id", "reason", "issues")
                                if key in current.state
                            },
                        }
                    )
            result = results[run_id]
        if row.snapshot.get("engine") == "graph_v2":
            result["tasks"] = [
                {**results[identity], "node_id": node_id}
                for node_id, identity in row.snapshot["task_runs"].items()
            ]
            if include_details:
                result["events"] = sorted(
                    [
                        *result["events"],
                        *[
                            event
                            for task in result["tasks"]
                            for event in task["events"]
                        ],
                    ],
                    key=lambda event: event["cursor"],
                )
            result["artifacts"] = [
                artifact for task in result["tasks"] for artifact in task["artifacts"]
            ]
            result["result_ref"] = None
        if include_details:
            result["cursor"] = max(
                (event["cursor"] for event in result["events"]), default=0
            )
        tasks = result.get("tasks", [result])
        result["progress"] = {
            "total": len(tasks),
            "completed": sum(
                task["status"] in {"completed", "skipped"} for task in tasks
            ),
            "running": sum(task["status"] == "running" for task in tasks),
            "waiting": sum(
                task["status"] not in {"pending", "running", "completed", "skipped"}
                for task in tasks
            ),
        }
        return result

    async def _require_execution_owner(
        self, session: AsyncSession, run_id: str, execution_id: str
    ) -> None:
        """Fence durable writes inside their existing transaction.

        Args:
            session: Transaction that will persist the dependent workflow write.
            run_id: Run whose execution ownership must still be active.
            execution_id: Durable owner assigned before this invocation began.

        Raises:
            RoleAssetError: Recovery or completion has displaced this executor.
        """
        changed = await session.execute(
            update(AgentWorkflowRun)
            .where(
                col(AgentWorkflowRun.run_id) == run_id,
                col(AgentWorkflowRun.status) == "running",
                AgentWorkflowRun.state["execution_id"].as_string() == execution_id,
            )
            .values(state=AgentWorkflowRun.state)
        )
        if cast(CursorResult, changed).rowcount != 1:
            raise RoleAssetError("Execution ownership has changed", status=409)

    async def record_process(
        self,
        username: str,
        run_id: str,
        attempt_id: str,
        record_id: str,
        kind: str,
        data: dict,
        *,
        execution_id: str | None = None,
    ) -> dict:
        """Save complete structured observations and one deduplicated event.

        Args:
            username: Original owner, checked against the persisted run.
            run_id: Task run producing the record.
            attempt_id: Actual execution attempt; stale writers are rejected.
            record_id: Stable delivery identity within this attempt.
            kind: Model response or tool result from the real execution chain.
            data: Complete returned payload, never parsed from display text.
            execution_id: Durable execution owner; late executors cannot append records.

        Returns:
            Durable record with its task, attempt and member associations.

        Raises:
            RoleAssetError: Invalid identity, inaccessible run or conflicting replay.
        """
        if (
            kind not in {"model_response", "tool_result"}
            or not record_id
            or len(record_id) > 256
        ):
            raise RoleAssetError("Invalid process record")
        identity = uuid.uuid5(
            uuid.NAMESPACE_URL, f"{run_id}:{attempt_id}:{record_id}"
        ).hex
        async with self.db.get_db() as session:
            run = await session.get(AgentWorkflowRun, run_id)
            if run is None or run.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            if (
                run.state.get("attempt_id") != attempt_id
                or "member" not in run.snapshot
            ):
                raise RoleAssetError(
                    "Process record belongs to a stale attempt", status=409
                )
            if execution_id is not None:
                await self._require_execution_owner(session, run_id, execution_id)
            previous = await session.get(AgentWorkflowProcess, identity)
            if previous:
                if previous.kind != kind or previous.data != data:
                    raise RoleAssetError(
                        "Process record identity was reused", status=409
                    )
                return previous.model_dump(mode="json")
            record = AgentWorkflowProcess(
                record_id=identity,
                run_id=run_id,
                task_id=run.state["task_id"],
                attempt_id=attempt_id,
                member_id=run.snapshot["member"]["member_id"],
                kind=kind,
                data=data,
            )
            session.add(record)
            session.add(
                AgentWorkflowEvent(
                    run_id=run_id,
                    kind="process." + kind,
                    data={"record_id": identity, "attempt_id": attempt_id},
                )
            )
            try:
                await session.commit()
                await session.refresh(record)
            except IntegrityError:
                await session.rollback()
                previous = await session.get(AgentWorkflowProcess, identity)
                if previous is None:
                    raise
                if previous.kind != kind or previous.data != data:
                    raise RoleAssetError(
                        "Process record identity was reused", status=409
                    ) from None
                return previous.model_dump(mode="json")
        return record.model_dump(mode="json")

    async def get_events(self, username: str, run_id: str, after: int = 0) -> dict:
        """Continue observation from a committed cursor or return a fresh snapshot.

        Args:
            username: Authenticated owner.
            run_id: Run or graph identity to observe.
            after: Last cursor applied by the client; zero starts observation.

        Returns:
            Ordered events and their consistent snapshot when facts changed.
            Cursors outside the last 1000 events restart from the snapshot.

        Raises:
            RoleAssetError: Run is inaccessible or the cursor is negative.
        """
        if after < 0:
            raise RoleAssetError("Cursor must not be negative")
        async with self.db.get_db() as session:
            if self.db.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN"))
            run = (
                await session.execute(
                    select(
                        AgentWorkflowRun.owner_username,
                        AgentWorkflowRun.snapshot["task_runs"],
                    ).where(AgentWorkflowRun.run_id == run_id)
                )
            ).first()
            if run is None or run[0] != username:
                raise RoleAssetError("Run not found", status=404)
            identities = [run_id, *(run[1] or {}).values()]
            cursor = (
                await session.execute(
                    select(func.max(AgentWorkflowEvent.id)).where(
                        col(AgentWorkflowEvent.run_id).in_(identities)
                    )
                )
            ).scalar_one() or 0
            if after == cursor:
                return {
                    "cursor": cursor,
                    "reset": False,
                    "events": [],
                    "snapshot": None,
                }
        snapshot = await self.get_run(username, run_id)
        events = snapshot["events"]
        reset = after > snapshot["cursor"] or (
            len(events) > 1000 and after < events[-1001]["cursor"]
        )
        changes = (
            [] if reset else [event for event in events if event["cursor"] > after]
        )
        return {
            "cursor": snapshot["cursor"],
            "reset": reset,
            "events": changes,
            "snapshot": snapshot if reset or changes else None,
        }

    async def list_runs(self, username: str, limit: int = 50) -> dict:
        """List persisted runs, including inactive and interrupted executions.

        Args:
            username: Authenticated owner.
            limit: Maximum number of recent records, capped at 100.

        Returns:
            Recent run overviews, without full configuration, results or history.
        """
        async with self.db.get_db() as session:
            identities = (
                (
                    await session.execute(
                        select(AgentWorkflowRun.run_id)
                        .where(
                            AgentWorkflowRun.owner_username == username,
                            AgentWorkflowRun.snapshot["parent_run_id"]
                            .as_string()
                            .is_(None),
                        )
                        .order_by(col(AgentWorkflowRun.created_at).desc())
                        .limit(max(1, min(limit, 100)))
                    )
                )
                .scalars()
                .all()
            )
        return {
            "runs": [
                await self.get_run(username, identity, include_details=False)
                for identity in identities
            ]
        }

    async def _start_graph(
        self, username: str, asset: dict, request: WorkflowStartRequest
    ) -> dict:
        """Persist a complete graph, member contexts and pending tasks atomically.

        Args:
            username: Authenticated owner.
            asset: Saved immutable graph version.
            request: Validated launch inputs and source identity.

        Returns:
            Persisted root run with all task snapshots.

        Raises:
            RoleAssetError: Invalid graph, missing resources or instance limit.
        """
        definition = GraphDefinition.model_validate(asset["definition"])
        validate_graph(definition)
        members = {}
        role_counts: dict[str, int] = {}
        for task in definition.tasks:
            if task.member not in members:
                frozen = await self._freeze_member(
                    username, task.model_dump(exclude_none=True)
                )
                role_counts[task.role_id] = role_counts.get(task.role_id, 0) + 1
                members[task.member] = frozen
        for frozen in members.values():
            role_id = frozen["role"]["role_id"]
            limit = frozen["effective"].get("max_instances")
            if limit is not None and role_counts[role_id] > limit:
                raise RoleAssetError(
                    "Role member instance limit exceeded",
                    [
                        {
                            "path": "definition.tasks",
                            "code": "LIMIT",
                            "message": f"Role {role_id} permits {limit} member instances",
                        }
                    ],
                )
        run_id = uuid.uuid4().hex
        scope_id = uuid.uuid4().hex
        task_runs = {task.id: uuid.uuid4().hex for task in definition.tasks}
        snapshot = {
            "engine": "graph_v2",
            "asset_id": asset["asset_id"],
            "asset_version": asset["version"],
            "definition": asset["definition"],
            "inputs": request.inputs,
            "input_artifacts": [ref.model_dump() for ref in request.input_artifacts],
            "source": request.source,
            "initiator": username,
            "members": members,
            "task_runs": task_runs,
        }
        async with self.db.get_db() as session:
            session.add(
                AgentWorkflowRun(
                    run_id=run_id,
                    owner_username=username,
                    snapshot=snapshot,
                    state={"scope_id": scope_id, "reason": None},
                )
            )
            session.add(AgentWorkflowSchedule(run_id=run_id))
            for frozen in members.values():
                member = frozen["member"]
                session.add(
                    ConversationV2(
                        conversation_id=member["conversation_id"],
                        platform_id="webchat",
                        user_id=member["umo"],
                        content=[],
                        title=definition.name,
                        persona_id=frozen["effective"].get("persona_id"),
                    )
                )
                session.add(
                    PlatformSession(
                        session_id=member["session_id"],
                        creator=username,
                        display_name=definition.name,
                        archived=1,
                    )
                )
            for task in definition.tasks:
                child_definition = {
                    "name": task.name,
                    "role_id": task.role_id,
                    "role_version": task.role_version,
                    "task": task.task,
                    "overrides": task.overrides.model_dump(exclude_none=True),
                    "required_inputs": definition.required_inputs,
                }
                if task.output_contract is not None:
                    child_definition["output_contract"] = task.output_contract
                session.add(
                    AgentWorkflowRun(
                        run_id=task_runs[task.id],
                        owner_username=username,
                        status="pending",
                        snapshot={
                            "engine": "single_task_v2",
                            "parent_run_id": run_id,
                            "node_id": task.id,
                            "asset_id": asset["asset_id"],
                            "asset_version": asset["version"],
                            "definition": child_definition,
                            **members[task.member],
                            "inputs": request.inputs,
                            "input_artifacts": snapshot["input_artifacts"],
                            "source": request.source,
                            "initiator": username,
                            "task": task.task,
                        },
                        state={
                            "scope_id": scope_id,
                            "task_id": task_runs[task.id],
                            "attempt_id": uuid.uuid4().hex,
                            "message_id": uuid.uuid4().hex,
                            "safe_to_finish": False,
                            "reason": None,
                            "response": None,
                        },
                    )
                )
            session.add(
                AgentWorkflowEvent(
                    run_id=run_id, kind="run.started", data={"task_runs": task_runs}
                )
            )
            await session.commit()
        await self.advance_run(username, run_id)
        return await self.get_run(username, run_id)

    async def advance_run(self, username: str, run_id: str) -> dict:
        """Wake dependency scheduling; persistent claims make notifications idempotent.

        Args:
            username: Authenticated owner.
            run_id: Root graph run; no new attempt is created by a notification.

        Returns:
            Current public run snapshot including skipped and blocked paths.

        Raises:
            RoleAssetError: Run is inaccessible or is not a graph.
        """
        claimed = []
        # The revision guard serializes claims, including same-member exclusions,
        # across service objects. Notifications carry no execution authority.
        for _ in range(20):
            async with self.db.get_db() as session:
                root = await session.get(AgentWorkflowRun, run_id)
                if root is None or root.owner_username != username:
                    raise RoleAssetError("Run not found", status=404)
                if root.snapshot.get("engine") != "graph_v2":
                    raise RoleAssetError("Run is not a dependency workflow")
                if root.status != "running":
                    break
                schedule = await session.get(AgentWorkflowSchedule, run_id)
                assert schedule is not None
                changed = await session.execute(
                    update(AgentWorkflowSchedule)
                    .where(
                        col(AgentWorkflowSchedule.run_id) == run_id,
                        col(AgentWorkflowSchedule.revision) == schedule.revision,
                    )
                    .values(revision=schedule.revision + 1)
                )
                if cast(CursorResult, changed).rowcount != 1:
                    await session.rollback()
                    continue
                definition = root.snapshot["definition"]
                rows = {
                    node_id: await session.get(AgentWorkflowRun, identity)
                    for node_id, identity in root.snapshot["task_runs"].items()
                }
                active = [
                    row
                    for row in rows.values()
                    if row is not None and row.status == "running"
                ]
                occupied = {row.snapshot["member"]["member_id"] for row in active}
                role_active: dict[str, int] = {}
                for row in active:
                    role_id = row.snapshot["definition"]["role_id"]
                    role_active[role_id] = role_active.get(role_id, 0) + 1
                # Repeat terminal propagation so definition order does not turn
                # a skipped branch into a barrier for its downstream merge.
                progress = True
                while progress:
                    progress = False
                    for task in definition["tasks"]:
                        row = rows[task["id"]]
                        assert row is not None
                        if row.status != "pending":
                            continue
                        incoming = [
                            edge
                            for edge in definition["edges"]
                            if edge["target"] == task["id"]
                        ]
                        matched = []
                        unresolved = False
                        failed = False
                        for edge in incoming:
                            source = rows[edge["source"]]
                            assert source is not None
                            if source.status in {"pending", "running"}:
                                unresolved = True
                            elif source.status == "completed":
                                rule = edge.get("condition")
                                match = True
                                if rule:
                                    value = source.result
                                    assert value is not None
                                    for name in rule["field"].split("."):
                                        value = value[name]
                                    other = rule["value"]
                                    op = rule["operator"]
                                    # JSON booleans are distinct from numeric 0/1.
                                    equal = value == other and isinstance(
                                        value, bool
                                    ) == isinstance(other, bool)
                                    match = (
                                        equal
                                        if op == "eq"
                                        else not equal
                                        if op == "ne"
                                        else value > other
                                        if op == "gt"
                                        else value >= other
                                        if op == "ge"
                                        else value < other
                                        if op == "lt"
                                        else value <= other
                                    )
                                if match:
                                    matched.append(source)
                            elif source.status != "skipped":
                                failed = True
                        if failed:
                            row.status = "dependency_failed"
                            row.state = {
                                **row.state,
                                "reason": "An upstream task has no validated result.",
                            }
                        elif unresolved:
                            continue
                        elif incoming and not matched:
                            row.status = "skipped"
                            row.state = {
                                **row.state,
                                "reason": "No incoming path matched its field conditions.",
                            }
                        else:
                            member_id = row.snapshot["member"]["member_id"]
                            role_id = row.snapshot["definition"]["role_id"]
                            role_limit = row.snapshot["effective"].get("max_parallel")
                            if (
                                len(active) >= definition["max_parallel"]
                                or member_id in occupied
                                or (
                                    role_limit is not None
                                    and role_active.get(role_id, 0) >= role_limit
                                )
                            ):
                                continue
                            inputs = dict(root.snapshot["inputs"])
                            refs = list(root.snapshot.get("input_artifacts", []))
                            missing = False
                            for key, ref in task["inputs"].items():
                                if ref.get("input") is not None:
                                    # Earlier task aliases must not change the source.
                                    inputs[key] = root.snapshot["inputs"][ref["input"]]
                                else:
                                    source = rows[ref["task_id"]]
                                    assert source is not None
                                    if source.status != "completed":
                                        missing = True
                                        break
                                    value = source.result
                                    assert value is not None
                                    for name in ref["field"].split("."):
                                        value = value[name]
                                    inputs[key] = (
                                        value
                                        if isinstance(value, str)
                                        else json.dumps(value, ensure_ascii=False)
                                    )
                                    pinned = {
                                        "artifact_id": source.run_id,
                                        "version": 1,
                                    }
                                    if pinned not in refs:
                                        refs.append(pinned)
                            if missing:
                                row.status = "skipped"
                                row.state = {
                                    **row.state,
                                    "reason": "A declared result input belongs to a skipped path.",
                                }
                            else:
                                row.snapshot = {
                                    **row.snapshot,
                                    "task": task["task"].format_map(inputs),
                                    "inputs": inputs,
                                    "input_artifacts": refs,
                                }
                                row.status = "running"
                                occupied.add(member_id)
                                role_active[role_id] = role_active.get(role_id, 0) + 1
                                active.append(row)
                                claimed.append(row.run_id)
                        session.add(row)
                        session.add(
                            AgentWorkflowEvent(
                                run_id=row.run_id,
                                kind="task." + row.status,
                                data={
                                    "node_id": task["id"],
                                    "attempt_id": row.state["attempt_id"],
                                },
                            )
                        )
                        progress = True
                if all(
                    row is not None and row.status not in {"pending", "running"}
                    for row in rows.values()
                ):
                    success = all(
                        row is not None and row.status in {"completed", "skipped"}
                        for row in rows.values()
                    )
                    root.status = "completed" if success else "blocked"
                    root.state = {
                        **root.state,
                        "reason": None
                        if success
                        else "One or more branches require attention; independent branches were allowed to finish.",
                    }
                    if success:
                        root.result = {
                            "tasks": {
                                name: row.result
                                for name, row in rows.items()
                                if row is not None and row.status == "completed"
                            }
                        }
                    session.add(root)
                    session.add(
                        AgentWorkflowEvent(
                            run_id=run_id, kind="run." + root.status, data={}
                        )
                    )
                await session.commit()
                break
        else:
            raise RoleAssetError("Scheduling contention; notify again", status=409)
        for identity in claimed:
            task = asyncio.create_task(
                self._execute_graph_task(username, run_id, identity)
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return await self.get_run(username, run_id)

    async def _execute_graph_task(
        self,
        username: str,
        run_id: str,
        task_run_id: str,
        execution_id: str | None = None,
    ) -> None:
        """Run a claimed task through the shared pipeline and wake successors.

        Args:
            username: Frozen initiating owner.
            run_id: Root graph owning scheduling capacity.
            task_run_id: Durably claimed task and attempt.
            execution_id: Recovered task's new durable execution owner, if any.
        """
        try:
            await self._execute(username, task_run_id, execution_id)
        finally:
            if not self._closing:
                await self.advance_run(username, run_id)

    async def submit_result(
        self,
        username: str,
        run_id: str,
        attempt_id: str,
        payload: dict,
        *,
        execution_id: str | None = None,
    ) -> None:
        """Validate and durably accept contracted output without completing early.

        Args:
            username: Actual owner from the bound execution.
            run_id: Owning run.
            attempt_id: Attempt that is authorized to submit.
            payload: Complete output matching the run's frozen task contract.
            execution_id: Current durable executor, required for bound Agent tools.

        Raises:
            RoleAssetError: Invalid result, stale attempt or conflicting submission.
        """
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            if row is None or row.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            if row.state.get("attempt_id") != attempt_id:
                raise RoleAssetError("Attempt is no longer active", status=409)
            if (
                execution_id is not None
                and row.state.get("execution_id") != execution_id
            ):
                raise RoleAssetError("Execution ownership has changed", status=409)
            if execution_id is not None and row.pending_result is None:
                await self._require_execution_owner(session, run_id, execution_id)
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
                    col(AgentWorkflowRun.state)["attempt_id"].as_string() == attempt_id,
                    AgentWorkflowRun.state["execution_id"].as_string() == execution_id
                    if execution_id is not None
                    else true(),
                )
                .values(pending_result=payload)
            )
            if cast(CursorResult, changed).rowcount != 1:
                await session.rollback()
                await session.refresh(row)
                if (
                    row.state.get("attempt_id") == attempt_id
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
                    or not attempt_id
                    or consumer.state.get("attempt_id") != attempt_id
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
        *,
        execution_id: str | None = None,
    ) -> dict:
        """Store complete file bytes before a stable reference can be submitted.

        Args:
            username: Actual authenticated task owner.
            run_id: Owning task run.
            attempt_id: Active attempt, or None for an owner's completed-output revision.
            filename: Display name without directory components.
            content: Complete ready bytes, limited to 50 MiB.
            execution_id: Durable owner when a bound Agent tool writes this file.

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
            if execution_id is not None:
                await self._require_execution_owner(session, run_id, execution_id)
            if not run.state.get("attempt_id") or not (
                (attempt_id is None and run.status == "completed")
                or (
                    attempt_id is not None
                    and run.status == "running"
                    and run.state.get("attempt_id") == attempt_id
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

    async def recover_run(self, username: str, run_id: str) -> dict:
        """Continue the original attempt from a durable execution boundary.

        Args:
            username: Authenticated owner of the original run.
            run_id: Interrupted task identity, never a new attempt or iteration.

        Returns:
            Persisted progress; completed runs return their original result.

        Raises:
            RoleAssetError: The run is inaccessible or has no resumable boundary.
        """
        run = await self.get_run(username, run_id)
        if run["status"] == "completed":
            return run
        if run["snapshot"]["engine"] == "graph_v2":
            raise RoleAssetError(
                "Recover an interrupted task in the workflow", status=409
            )
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            checkpoint = await session.get(AgentWorkflowCheckpoint, run_id)
            if row is None or row.status not in {"interrupted", "failed", "blocked"}:
                raise RoleAssetError(
                    "Only interrupted execution can be recovered", status=409
                )
            if checkpoint is None or checkpoint.attempt_id != row.state["attempt_id"]:
                raise RoleAssetError("No resumable execution checkpoint", status=409)
            if checkpoint.started_tools and not checkpoint.result_submission:
                raise RoleAssetError(
                    "Tool execution has started; its result requires confirmation. Tool-result recovery is not supported yet.",
                    status=409,
                )
            parent_id = row.snapshot.get("parent_run_id")
            if parent_id:
                parent = await session.get(AgentWorkflowRun, parent_id)
                schedule = await session.get(AgentWorkflowSchedule, parent_id)
                if (
                    parent is None
                    or schedule is None
                    or parent.status not in {"running", "blocked"}
                ):
                    raise RoleAssetError(
                        "The parent workflow cannot continue", status=409
                    )
                changed = await session.execute(
                    update(AgentWorkflowSchedule)
                    .where(
                        col(AgentWorkflowSchedule.run_id) == parent_id,
                        col(AgentWorkflowSchedule.revision) == schedule.revision,
                    )
                    .values(revision=schedule.revision + 1)
                )
                if cast(CursorResult, changed).rowcount != 1:
                    raise RoleAssetError("Scheduling ownership has changed", status=409)
                siblings = (
                    (
                        await session.execute(
                            select(AgentWorkflowRun).where(
                                AgentWorkflowRun.snapshot["parent_run_id"].as_string()
                                == parent_id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                active = [task for task in siblings if task.status == "running"]
                role_id = row.snapshot["definition"]["role_id"]
                role_limit = row.snapshot["effective"].get("max_parallel")
                if (
                    len(active) >= parent.snapshot["definition"]["max_parallel"]
                    or any(
                        task.snapshot["member"]["member_id"]
                        == row.snapshot["member"]["member_id"]
                        for task in active
                    )
                    or role_limit is not None
                    and sum(
                        task.snapshot["definition"]["role_id"] == role_id
                        for task in active
                    )
                    >= role_limit
                ):
                    raise RoleAssetError(
                        "Recovery is waiting for workflow or member capacity",
                        status=409,
                    )
                parent.status = "running"
                parent.state = {**parent.state, "reason": None}
                session.add(parent)
                # Only unstarted descendants blocked by this interrupted task
                # become pending again; unrelated failures keep their own state.
                descendants = {row.snapshot["node_id"]}
                while True:
                    expanded = descendants | {
                        edge["target"]
                        for edge in parent.snapshot["definition"]["edges"]
                        if edge["source"] in descendants
                    }
                    if expanded == descendants:
                        break
                    descendants = expanded
                for sibling in siblings:
                    if (
                        sibling.status == "dependency_failed"
                        and sibling.snapshot["node_id"] in descendants
                    ):
                        sibling.status = "pending"
                        sibling.state = {**sibling.state, "reason": None}
                        session.add(sibling)
            execution_id = uuid.uuid4().hex
            state = {
                **row.state,
                "execution_id": execution_id,
                "message_id": uuid.uuid4().hex,
                "reason": None,
            }
            finish_result = (
                checkpoint.result_submission and row.pending_result is not None
            )
            if finish_result:
                state["safe_to_finish"] = True
            elif checkpoint.result_submission:
                # Result submission is an internal idempotent transaction. This
                # exception does not permit replaying any other started tool.
                checkpoint.started_tools = []
                checkpoint.data = {
                    **checkpoint.data,
                    "phase": "response",
                    "started_tools": [],
                }
                checkpoint.phase = "response"
                session.add(checkpoint)
            changed = await session.execute(
                update(AgentWorkflowRun)
                .where(
                    col(AgentWorkflowRun.run_id) == run_id,
                    col(AgentWorkflowRun.status) == row.status,
                    AgentWorkflowRun.state["execution_id"].as_string()
                    == row.state.get("execution_id")
                    if row.state.get("execution_id")
                    else AgentWorkflowRun.state["execution_id"].as_string().is_(None),
                )
                .values(status="running", state=state)
            )
            if cast(CursorResult, changed).rowcount != 1:
                raise RoleAssetError("Execution ownership has changed", status=409)
            session.add(
                AgentWorkflowEvent(
                    run_id=run_id,
                    kind="attempt.recovered",
                    data={
                        "attempt_id": state["attempt_id"],
                        "boundary": checkpoint.phase,
                    },
                )
            )
            await session.commit()
        if finish_result:
            finished = await self.finish_submission(username, run_id)
            if parent_id:
                await self.advance_run(username, parent_id)
            return finished
        parent_id = run["snapshot"].get("parent_run_id")
        task = asyncio.create_task(
            self._execute_graph_task(username, parent_id, run_id, execution_id)
            if parent_id
            else self._execute(username, run_id, execution_id)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await self.get_run(username, run_id)

    async def save_checkpoint(
        self, username: str, run_id: str, attempt_id: str, execution_id: str, data: dict
    ) -> None:
        """Commit a consistent effective context before dependent external work.

        Args:
            username: Original authenticated owner.
            run_id: Original task run.
            attempt_id: Attempt being continued.
            execution_id: Current executor; replaced ownership rejects late writes.
            data: Complete runner state with a model invocation identity and boundary.

        Raises:
            RoleAssetError: Ownership changed or the attempt is no longer running.
        """
        async with self.db.get_db() as session:
            changed = await session.execute(
                update(AgentWorkflowRun)
                .where(
                    col(AgentWorkflowRun.run_id) == run_id,
                    col(AgentWorkflowRun.owner_username) == username,
                    col(AgentWorkflowRun.status) == "running",
                    AgentWorkflowRun.state["attempt_id"].as_string() == attempt_id,
                    AgentWorkflowRun.state["execution_id"].as_string() == execution_id,
                )
                .values(state=AgentWorkflowRun.state)
            )
            if cast(CursorResult, changed).rowcount != 1:
                raise RoleAssetError("Execution ownership has changed", status=409)
            checkpoint = await session.get(AgentWorkflowCheckpoint, run_id)
            if checkpoint is None:
                checkpoint = AgentWorkflowCheckpoint(
                    run_id=run_id,
                    attempt_id=attempt_id,
                    phase=data["phase"],
                    invocation_id=data["invocation_id"],
                    data=data,
                )
            checkpoint.phase = data["phase"]
            checkpoint.invocation_id = data["invocation_id"]
            checkpoint.started_tools = data.get("started_tools", [])
            checkpoint.data = data
            row = await session.get(AgentWorkflowRun, run_id)
            assert row is not None
            checkpoint.result_submission = row.snapshot["definition"].get(
                "output_contract"
            ) is None and (data.get("response") or {}).get("tools_call_name") == [
                "submit_task_result"
            ]
            session.add(checkpoint)
            session.add(
                AgentWorkflowEvent(
                    run_id=run_id,
                    kind="checkpoint." + data["phase"],
                    data={
                        "attempt_id": attempt_id,
                        "invocation_id": data["invocation_id"],
                    },
                )
            )
            await session.commit()

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
            abandoned = (
                (
                    await session.execute(
                        select(AgentWorkflowRun).where(
                            col(AgentWorkflowRun.status) == "running",
                            AgentWorkflowRun.snapshot["engine"].as_string()
                            != "graph_v2",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for run in abandoned:
                run.status = "interrupted"
                session.add(run)
                session.add(
                    AgentWorkflowEvent(
                        run_id=run.run_id,
                        kind="task.interrupted",
                        data={
                            "attempt_id": run.state["attempt_id"],
                            "reason": "Core execution stopped; this task was not replayed.",
                        },
                    )
                )
            await session.commit()

        for username, run_id in safe_intents:
            try:
                await self.finish_submission(username, run_id)
            except Exception:
                logger.exception(
                    "Failed to finish saved workflow submission: %s", run_id
                )
        async with self.db.get_db() as session:
            roots = (
                (
                    await session.execute(
                        select(AgentWorkflowRun).where(
                            col(AgentWorkflowRun.status) == "running",
                            AgentWorkflowRun.snapshot["engine"].as_string()
                            == "graph_v2",
                        )
                    )
                )
                .scalars()
                .all()
            )
        for root in roots:
            await self.advance_run(root.owner_username, root.run_id)

    async def shutdown(self) -> None:
        """Stop core-owned tasks before providers and database are terminated.

        Returns:
            None after every execution has released its tools and subscriptions.
        """
        tasks = list(self._tasks)
        self._closing = True
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(
        self, username: str, run_id: str, execution_id: str | None = None
    ) -> None:
        """Advance the single saved attempt through the existing Agent pipeline.

        Args:
            username: Original owner.
            run_id: Already persisted run to dispatch.
            execution_id: Ownership acquired by recovery, or acquired here on initial dispatch.
        """
        run = await self.get_run(username, run_id)
        if run["status"] != "running":
            return
        snapshot, state = run["snapshot"], run["state"]
        async with self.db.get_db() as session:
            if execution_id is None:
                execution_id = uuid.uuid4().hex
                state = {**state, "execution_id": execution_id}
                changed = await session.execute(
                    update(AgentWorkflowRun)
                    .where(
                        col(AgentWorkflowRun.run_id) == run_id,
                        col(AgentWorkflowRun.status) == "running",
                        AgentWorkflowRun.state["execution_id"].as_string().is_(None),
                    )
                    .values(state=state)
                )
                if cast(CursorResult, changed).rowcount != 1:
                    return
                await session.commit()
            elif state.get("execution_id") != execution_id:
                return
            saved = await session.get(AgentWorkflowCheckpoint, run_id)
            checkpoint = copy.deepcopy(saved.data) if saved else None
        member, effective = snapshot["member"], snapshot["effective"]
        ports = None
        tools: list[FunctionTool] = []
        status = "failed"
        checkpoint_error = None
        try:
            await self.roles.validate_frozen_role(snapshot["role"])
            ports = self.ports_factory(username)

            async def accept(payload: dict) -> None:
                """Persist the submission for this authorized attempt.

                Args:
                    payload: Complete output to validate against the frozen contract.
                """
                await self.submit_result(
                    username,
                    run_id,
                    state["attempt_id"],
                    payload,
                    execution_id=execution_id,
                )

            tools = [
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
                        username,
                        run_id,
                        state["attempt_id"],
                        filename,
                        content,
                        execution_id=execution_id,
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

            async def record(record_id: str, kind: str, data: dict) -> None:
                """Persist a structured observation before execution advances.

                Args:
                    record_id: Stable response or tool delivery identity.
                    kind: Model response or tool result.
                    data: Complete original payload from the runner.
                """
                nonlocal checkpoint_error
                try:
                    await self.record_process(
                        username,
                        run_id,
                        state["attempt_id"],
                        record_id,
                        kind,
                        data,
                        execution_id=execution_id,
                    )
                except Exception as exc:
                    checkpoint_error = "Failed to save complete process record"
                    raise ProcessPersistenceError(
                        "Failed to save complete process record"
                    ) from exc

            async def save(data: dict) -> None:
                """Fail closed if an execution boundary cannot be durably saved.

                Args:
                    data: Effective runner checkpoint preceding dependent work.
                """
                nonlocal checkpoint_error
                try:
                    await self.save_checkpoint(
                        username, run_id, state["attempt_id"], execution_id, data
                    )
                except Exception as exc:
                    checkpoint_error = "Failed to save execution checkpoint"
                    raise ProcessPersistenceError(
                        "Failed to save execution checkpoint"
                    ) from exc

            reply, parts = await asyncio.wait_for(
                execute_team_turn(
                    ports=ports,
                    member=member,
                    run_id=run_id,
                    team_id=snapshot["asset_id"],
                    username=username,
                    turn_id=state["attempt_id"],
                    node_id=snapshot.get("node_id", state["task_id"]),
                    record_process=record,
                    save_checkpoint=save,
                    checkpoint=checkpoint,
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
            if checkpoint_error is not None:
                raise ProcessPersistenceError(checkpoint_error)
            state["safe_to_finish"] = True
            async with self.db.get_db() as session:
                changed = await session.execute(
                    update(AgentWorkflowRun)
                    .where(
                        col(AgentWorkflowRun.run_id) == run_id,
                        col(AgentWorkflowRun.status) == "running",
                        col(AgentWorkflowRun.state)["attempt_id"].as_string()
                        == state["attempt_id"],
                        AgentWorkflowRun.state["execution_id"].as_string()
                        == execution_id,
                    )
                    .values(state=state)
                )
                if cast(CursorResult, changed).rowcount == 1:
                    session.add(
                        AgentWorkflowEvent(
                            run_id=run_id,
                            kind="response.completed",
                            data={"attempt_id": state["attempt_id"]},
                        )
                    )
                await session.commit()
                if cast(CursorResult, changed).rowcount != 1:
                    return
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
        except CheckpointRecoveryError as exc:
            status = "blocked"
            state["reason"] = str(exc)
        except Exception as exc:
            state["reason"] = str(exc)
            logger.error("Single-task workflow execution failed: %s", exc)
        finally:
            if tools and AgentTeamToolRegistry.get_tools(member["umo"]) == tools:
                AgentTeamToolRegistry.unregister(member["umo"])
            if ports and ports.close:
                await ports.close()
        async with self.db.get_db() as session:
            changed = await session.execute(
                update(AgentWorkflowRun)
                .where(
                    col(AgentWorkflowRun.run_id) == run_id,
                    col(AgentWorkflowRun.status) == "running",
                    col(AgentWorkflowRun.state)["attempt_id"].as_string()
                    == state["attempt_id"],
                    AgentWorkflowRun.state["execution_id"].as_string() == execution_id,
                )
                .values(status=status, state=state)
            )
            if cast(CursorResult, changed).rowcount == 1:
                session.add(
                    AgentWorkflowEvent(
                        run_id=run_id,
                        kind="task." + status,
                        data={
                            "attempt_id": state["attempt_id"],
                            "reason": state["reason"],
                        },
                    )
                )
            await session.commit()
