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
    AgentWorkflowEvent,
    AgentWorkflowFile,
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
            result = {
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
        if row.snapshot.get("engine") == "graph_v2":
            result["tasks"] = [
                {**await self.get_run(username, identity), "node_id": node_id}
                for node_id, identity in row.snapshot["task_runs"].items()
            ]
            result["events"] = sorted(
                [
                    *result["events"],
                    *[event for task in result["tasks"] for event in task["events"]],
                ],
                key=lambda event: event["cursor"],
            )
            result["artifacts"] = [
                artifact for task in result["tasks"] for artifact in task["artifacts"]
            ]
            result["result_ref"] = None
        return result

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
            "runs": [await self.get_run(username, identity) for identity in identities]
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
                                    match = (
                                        value == other
                                        if op == "eq"
                                        else value != other
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
                                    inputs[key] = inputs[ref["input"]]
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
        self, username: str, run_id: str, task_run_id: str
    ) -> None:
        """Run a claimed task through the shared pipeline and wake successors.

        Args:
            username: Frozen initiating owner.
            run_id: Root graph owning scheduling capacity.
            task_run_id: Durably claimed task and attempt.
        """
        try:
            await self._execute(username, task_run_id)
        finally:
            if not self._closing:
                await self.advance_run(username, run_id)

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
            if row.state.get("attempt_id") != attempt_id:
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
                    col(AgentWorkflowRun.state)["attempt_id"].as_string() == attempt_id,
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
                    AgentWorkflowRun.snapshot["engine"].as_string() != "graph_v2",
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

    async def _execute(self, username: str, run_id: str) -> None:
        """Advance the single saved attempt through the existing Agent pipeline.

        Args:
            username: Original owner.
            run_id: Already persisted run to dispatch.
        """
        run = await self.get_run(username, run_id)
        if run["status"] != "running":
            return
        snapshot, state = run["snapshot"], run["state"]
        member, effective = snapshot["member"], snapshot["effective"]
        ports = None
        tools: list[FunctionTool] = []
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
                    node_id=snapshot.get("node_id", state["task_id"]),
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
                        col(AgentWorkflowRun.state)["attempt_id"].as_string()
                        == state["attempt_id"],
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
            if tools and AgentTeamToolRegistry.get_tools(member["umo"]) == tools:
                AgentTeamToolRegistry.unregister(member["umo"])
            if ports and ports.close:
                await ports.close()
        async with self.db.get_db() as session:
            await session.execute(
                update(AgentWorkflowRun)
                .where(
                    col(AgentWorkflowRun.run_id) == run_id,
                    col(AgentWorkflowRun.status) == "running",
                    col(AgentWorkflowRun.state)["attempt_id"].as_string()
                    == state["attempt_id"],
                )
                .values(status=status, state=state)
            )
            await session.commit()
