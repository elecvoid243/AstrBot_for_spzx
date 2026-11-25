"""Versioned workflow graphs and autonomous teams with durable execution."""

import asyncio
import base64
import copy
import hashlib
import json
import mimetypes
import time
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from string import Formatter
from typing import TYPE_CHECKING, Literal, cast

from jsonschema import Draft202012Validator, SchemaError
from mcp.types import CallToolResult, TextContent
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012, Schema
from sqlalchemy import Text, and_, case, func, literal, or_, true, tuple_
from sqlalchemy import select as sa_select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlmodel import col, select, text, update

from astrbot.core import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import (
    FunctionTool,
    ToolRecoveryResult,
    current_tool_invocation,
    has_tool_recovery,
)
from astrbot.core.agent_team_execution import (
    CheckpointRecoveryError,
    DebugWritePending,
    HumanInputRequired,
    ProcessPersistenceError,
    execute_team_turn,
)
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams import WORKFLOW_API_SCOPE
from astrbot.core.agent_teams.assets import (
    HumanWaitPolicy,
    RoleAssetError,
    RoleAssetService,
    RoleDefinition,
    RoleInstallation,
    RoleOverrides,
)
from astrbot.core.agent_teams.autonomous_tools import (
    MemberCommunicationRequest,
    TeamCommunicateTool,
    TeamManageTool,
    TeamOperationRequest,
)
from astrbot.core.agent_teams.components import (
    PLUGIN_COMPONENT,
    WorkflowComponent,
    WorkflowComponentContext,
)
from astrbot.core.agent_teams.graph import (
    DISPATCH_INPUT,
    JOIN_OUTPUT,
    MISSING_PORT_VALUE,
    NATIVE_NODE_OUTPUTS,
    REFERENCE_OUTPUTS,
    GraphDefinition,
    GraphTask,
    TaskInput,
    WorkflowCall,
    execute_port_transform,
    field_schema,
    matches_condition,
    normalize_port_inputs,
    port_requires_value,
    port_value,
    task_input_contract,
    validate_graph,
)
from astrbot.core.agent_teams.release_brief import RELEASE_BRIEF
from astrbot.core.agent_teams.result_tool import (
    PORT_FORMATS,
    ArtifactReference,
    ReadArtifactTool,
    StoreTaskFileTool,
    SubmitTaskResultTool,
    validate_component_result,
    validate_result,
)
from astrbot.core.agent_teams.starter_team import RELEASE_TEAM
from astrbot.core.agent_teams.usage import (
    RunLimits,
    UsageReport,
    UsageStart,
    summarize_usage,
)
from astrbot.core.config.agent_runner import (
    get_agent_runner_capabilities,
    get_agent_runner_capability_issues,
)
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import (
    AgentAssetDraft,
    AgentAssetDraftPublication,
    AgentAssetExample,
    AgentAssetTombstone,
    AgentRoleInstallation,
    AgentRoleVersion,
    AgentWorkflowApiAccess,
    AgentWorkflowArtifact,
    AgentWorkflowAttempt,
    AgentWorkflowChatDelivery,
    AgentWorkflowCheckpoint,
    AgentWorkflowControl,
    AgentWorkflowEvent,
    AgentWorkflowFile,
    AgentWorkflowLaunchReceipt,
    AgentWorkflowMessage,
    AgentWorkflowPackage,
    AgentWorkflowProcess,
    AgentWorkflowRerun,
    AgentWorkflowRun,
    AgentWorkflowSchedule,
    AgentWorkflowTodo,
    AgentWorkflowToolCall,
    AgentWorkflowToolRecovery,
    AgentWorkflowUsage,
    AgentWorkflowVersion,
    ApiKey,
    ConversationV2,
    DashboardAccount,
    PlatformSession,
)
from astrbot.core.knowledge_base.parsers.util import select_parser
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.provider.entities import (
    DecisionBooleanAnswer,
    DecisionBooleanQuestion,
    DecisionChoiceAnswer,
    DecisionChoiceQuestion,
    DecisionError,
    DecisionScoreAnswer,
    DecisionScoreQuestion,
)
from astrbot.core.provider.provider import DecisionProvider
from astrbot.core.workspace import API_KEY_USERNAME_PREFIX
from astrbot.dashboard.services.agent_team_ports import TeamPorts, build_ports_for_test

if TYPE_CHECKING:
    from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager


def _uses_execution_slot(snapshot: dict) -> bool:
    """Identify tasks eligible for the shared asynchronous execution slots.

    Args:
        snapshot: Frozen task snapshot.

    Returns:
        True for an Agent or a versioned native task.
    """
    return "member" in snapshot or snapshot.get("engine") in {
        "native_task_v2",
        "plugin_task_v2",
    }


def _capacity_key(snapshot: dict) -> tuple:
    """Identify the template quota, preset quota, or inline graph member quota.

    Args:
        snapshot: Frozen task execution snapshot.

    Returns:
        A namespace-qualified identity used for concurrency accounting.
    """
    if snapshot.get("engine") in {"native_task_v2", "plugin_task_v2"}:
        return ("component", snapshot["node_id"])
    definition = snapshot["definition"]
    if definition.get("role_id"):
        return ("role", definition["role_id"], definition["role_version"])
    return (
        ("preset", snapshot["asset_id"], snapshot["preset_id"])
        if snapshot.get("preset_id")
        else (
            "member",
            snapshot.get("dispatch_member_id") or snapshot["member"]["member_id"],
        )
    )


class TeamRole(BaseModel):
    """A stable preset with an inline or pinned role source."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128)
    role_id: str | None = Field(default=None, min_length=1, max_length=128)
    role_version: int | None = Field(default=None, ge=1)
    role: RoleDefinition | None = None
    installation: RoleInstallation | None = None
    overrides: RoleOverrides = Field(default_factory=RoleOverrides)

    @field_serializer("overrides")
    def serialize_overrides(self, overrides: RoleOverrides) -> dict:
        """Preserve explicit overrides, including null values.

        Args:
            overrides: Local settings.

        Returns:
            Explicitly supplied settings.
        """
        return overrides.model_dump(exclude_unset=True)

    @model_validator(mode="after")
    def validate_source(self):
        """Validate the same role sources accepted by a graph agent.

        Returns:
            The validated preset.

        Raises:
            ValueError: The role source is incomplete or ambiguous.
        """
        if (
            self.installation is not None
            and "profile_id" not in self.installation.model_fields_set
        ):
            raise ValueError("Inline agents require an explicit profile_id")
        GraphTask.model_validate(
            {
                "id": "preset",
                "name": "preset",
                "member": "preset",
                "task": "preset",
                **self.model_dump(exclude={"id"}, exclude_none=True),
            }
        )
        return self


class AutonomousDefinition(BaseModel):
    """A coordinator and allowed role templates sharing the workflow executor."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    kind: Literal["autonomous"]
    name: str = Field(min_length=1, max_length=128)
    default_provider_id: str | None = None
    coordinator: TeamRole
    allowed_roles: list[TeamRole] = Field(min_length=1, max_length=100)
    required_inputs: list[str] = Field(default_factory=lambda: ["goal"], max_length=30)
    max_members: int = Field(default=8, ge=2, le=100)
    max_parallel: int = Field(default=4, ge=1, le=100)
    max_tasks: int = Field(default=50, ge=1, le=100)
    max_depth: int = Field(default=3, ge=1, le=20)
    limits: RunLimits | None = None
    allowed_workflows: list[WorkflowCall] = Field(default_factory=list, max_length=30)
    output_contract: dict | None = None
    approval_question: str | None = Field(
        default=None, min_length=1, max_length=100_000
    )


class WorkflowSaveRequest(BaseModel):
    """Create an asset or append an immutable version using optimistic locking."""

    model_config = ConfigDict(extra="forbid", strict=True)
    definition: GraphDefinition | AutonomousDefinition
    expected_version: int | None = Field(default=None, ge=1)


class PackageDependency(BaseModel):
    """A source resource identity and its non-secret compatibility contract."""

    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal[
        "profiles",
        "models",
        "personas",
        "tools",
        "skills",
        "knowledge_bases",
        "decision_providers",
    ]
    source_id: str = Field(min_length=1, max_length=128)
    display_name: str = ""
    description: str = ""
    fingerprint: str | None = None
    runner_type: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    input_schema: dict | None = None


class PackageRole(BaseModel):
    """An exact role version and its source profile identity."""

    model_config = ConfigDict(extra="forbid", strict=True)
    role_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    definition: RoleDefinition
    installation: RoleInstallation


class PackageAsset(BaseModel):
    """One exact asset version in the entry's reference closure."""

    model_config = ConfigDict(extra="forbid", strict=True)
    asset_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    definition: GraphDefinition | AutonomousDefinition


class WorkflowPackage(PackageAsset):
    """A complete source closure with direct resource requirements."""

    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["astrbot.workflow"]
    format_version: Literal[3, 4]
    roles: list[PackageRole] = Field(max_length=100)
    assets: list[PackageAsset] = Field(default_factory=list, max_length=100)
    requirements: list[PackageDependency] = Field(default_factory=list, max_length=500)

    @field_validator("format_version", mode="before")
    @classmethod
    def validate_format_version(cls, value: object) -> object:
        """Reject non-integer JSON versions before literal validation.

        Args:
            value: Untrusted format version.

        Returns:
            The validated integer value.

        Raises:
            ValueError: The version is not an integer.
        """
        if type(value) is not int:
            raise ValueError("Package format_version must be an integer")
        return value

    @staticmethod
    def content_digest(content: dict) -> str:
        """Hash canonical JSON content.

        Args:
            content: Package content to hash.

        Returns:
            SHA-256 digest of the canonical representation.
        """
        return hashlib.sha256(
            json.dumps(
                content, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()


class ResourceSelection(BaseModel):
    """One explicit source-to-local resource choice."""

    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal[
        "profiles",
        "models",
        "personas",
        "tools",
        "skills",
        "knowledge_bases",
        "decision_providers",
    ]
    source_id: str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=128)


class WorkflowImportRequest(BaseModel):
    """Untrusted package and deliberate choices of this machine's resources."""

    model_config = ConfigDict(extra="forbid", strict=True)
    package: WorkflowPackage
    selections: list[ResourceSelection] = Field(default_factory=list, max_length=500)


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
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    source: str = Field(default="dashboard", min_length=1, max_length=512)
    request_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^\S+$"
    )
    input_artifacts: list[ArtifactReference] = Field(
        default_factory=list, max_length=30
    )


@dataclass
class WorkflowLaunchIdentity:
    """Authenticated source and request, before or after input normalization."""

    principal: str
    request_id: str
    fingerprint: str | None = None


class WorkflowLaunchConflict(RoleAssetError):
    """A committed source request was presented with different inputs."""

    def __init__(self) -> None:
        super().__init__(
            "Launch request ID was reused with a different payload",
            [
                {
                    "path": "request_id",
                    "code": "LAUNCH_CONFLICT",
                    "message": "Use a new request ID",
                }
            ],
            status=409,
        )


class WorkflowRerunRequest(BaseModel):
    """Start one new root from the completed run's exact launch inputs."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=128)


class TrialSource(BaseModel):
    """A manually supplied result or an exact existing artifact."""

    model_config = ConfigDict(extra="forbid", strict=True)
    content: dict | None = None
    artifact: ArtifactReference | None = None

    @model_validator(mode="after")
    def validate_source(self):
        """Require exactly one data source.

        Returns:
            The validated source.

        Raises:
            ValueError: Both or neither source was selected.
        """
        if (self.content is None) == (self.artifact is None):
            raise ValueError("Choose exactly one source")
        return self


class TrialRunRequest(BaseModel):
    """A trial scope and its explicit inputs against one saved draft revision."""

    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["workflow", "autonomous"]
    revision: int = Field(ge=1)
    mode: Literal["full", "single", "partial"]
    node_id: str | None = None
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    sources: dict[str, TrialSource] = Field(default_factory=dict)


class DebugForkAttempt(BaseModel):
    """Identify the exact consumer attempt to preview."""

    model_config = ConfigDict(extra="forbid", strict=True)
    scope_run_id: str = Field(min_length=1, max_length=128)
    task_run_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)


class DebugForkReplacement(BaseModel):
    """Supply one contract-checked test value or exact managed reference."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=128)
    value: JsonValue


class DebugForkRequest(DebugForkAttempt):
    """Confirm a preview and persist an independent single-node run."""

    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=1, max_length=128)
    replacement: DebugForkReplacement | None = None
    replacements: list[DebugForkReplacement] = Field(default_factory=list)


class ToolResolutionRequest(BaseModel):
    """An explicit, evidenced decision about an uncertain external effect."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=128)
    action: Literal["retry", "confirm_result", "confirm_failure", "reconcile"]
    evidence: str = Field(min_length=1, max_length=100_000)
    result: str | None = Field(default=None, min_length=1, max_length=1_000_000)


class WorkflowControlRequest(BaseModel):
    """Idempotent whole-run decision, optionally conditional on its revision."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=128)
    action: Literal["pause", "continue", "cancel"]
    expected_revision: int | None = Field(default=None, ge=0)


class WorkflowRetryRequest(BaseModel):
    """Explicit new attempt using the failed attempt's exact frozen inputs."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)
    input_policy: Literal["original"]


class HumanAnswerRequest(BaseModel):
    """Idempotent answer or explicit version decision with rejection feedback."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=128)
    action: Literal["answer", "approve", "reject"]
    answer: str | None = Field(default=None, min_length=1, max_length=100_000)
    feedback: str | None = Field(default=None, min_length=1, max_length=100_000)
    artifact_ref: ArtifactReference | None = None
    choice_id: str | None = Field(default=None, min_length=1, max_length=128)
    free_text: str | None = Field(default=None, min_length=1, max_length=10_000)


DEBUG_FORK_TRANSFORMS = {"parse_json", "map_fields", "json_text"}


def _debug_fork_reads(node: dict, snapshot: dict) -> dict | None:
    """Read the consumer's recorded inputs or recover exact legacy port evidence.

    Args:
        node: Consumer from the frozen workflow definition.
        snapshot: Saved attempt, including its round's feedback bindings.

    Returns:
        The recorded reads, or None if the source values cannot be proven.
    """
    reads = snapshot.get("input_reads")
    if reads is None:
        # Legacy V2 transforms recorded exact artifact and field on consumed ports.
        reads = {
            name: evidence
            for name, evidence in (snapshot.get("port_sources") or {}).items()
            if evidence.get("artifact") and evidence.get("field")
        }
        bound = (
            node.get("inputs", {}).keys() | snapshot.get("iteration_inputs", {}).keys()
        )
        if not bound <= reads.keys():
            return None
    return reads if reads.keys() <= (snapshot.get("inputs") or {}).keys() else None


def _current_task_runs(run: AgentWorkflowRun) -> dict[str, str]:
    """Select current instances without changing the immutable launch snapshot.

    Args:
        run: Root workflow with optional progress from later loop iterations.

    Returns:
        Current node-to-Task mapping shared by observation and admission controls.
    """
    return run.state.get("task_runs", run.snapshot.get("task_runs", {}))


def _run_definition(run: AgentWorkflowRun) -> dict:
    """Read frozen workflow nodes plus committed coordinator decisions.

    Args:
        run: Root run with immutable launch configuration and durable runtime state.

    Returns:
        Task relationship definition used by admission, recovery and observation.
    """
    definition = run.snapshot["definition"]
    return {
        **definition,
        "tasks": [*definition.get("tasks", []), *run.state.get("dynamic_tasks", [])],
        "edges": [*definition.get("edges", []), *run.state.get("dynamic_edges", [])],
    }


def _root_run_id(run: AgentWorkflowRun) -> str:
    """Resolve shared control and capacity while retaining the scope's parent.

    Args:
        run: Root, scope or task from a frozen launch.

    Returns:
        Whole-run identity frozen by the unified graph launch.

    Raises:
        RoleAssetError: The saved run lacks the current ownership format.
    """
    identity = run.snapshot.get("root_run_id")
    if not isinstance(identity, str) or not identity:
        raise RoleAssetError(
            "Stored run format is unsupported; recreate the asset and start a new run",
            status=409,
        )
    return identity


async def _descendant_runs(
    session: AsyncSession, run: AgentWorkflowRun
) -> list[AgentWorkflowRun]:
    """Read a persisted subtree for queries, controls and shared admission.

    Args:
        session: Existing transaction, including its admission lock when mutating.
        run: Root or scope whose descendants are required.

    Returns:
        Owned descendants including the starting row and historical iterations.
    """
    root_id = _root_run_id(run)
    family = (
        (
            await session.execute(
                select(AgentWorkflowRun).where(
                    AgentWorkflowRun.owner_username == run.owner_username,
                    or_(
                        col(AgentWorkflowRun.run_id) == root_id,
                        AgentWorkflowRun.snapshot["root_run_id"].as_string() == root_id,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    identities = {run.run_id}
    while True:
        expanded = identities | {
            row.run_id
            for row in family
            if row.snapshot.get("parent_run_id") in identities
        }
        if identities == expanded:
            break
        identities = expanded
    return [row for row in family if row.run_id in identities]


def _has_scope_capacity(
    root: AgentWorkflowRun,
    scope: AgentWorkflowRun,
    active: list[AgentWorkflowRun],
) -> bool:
    """Apply the shared root and local scope limits at every admission boundary.

    Args:
        root: Whole run whose frozen limit applies to all scopes.
        scope: Graph owning the task being admitted or resumed.
        active: Executing member tasks that still occupy capacity.

    Returns:
        Whether both limits allow another executing task.
    """
    return len(active) < root.snapshot["definition"]["max_parallel"] and all(
        sum(
            task.snapshot.get("parent_run_id") == constraint["run_id"]
            or any(
                item["run_id"] == constraint["run_id"]
                for item in task.snapshot.get("scope_limits", [])
            )
            for task in active
        )
        < constraint["max_parallel"]
        for constraint in scope.snapshot.get(
            "scope_limits",
            [
                {
                    "run_id": scope.run_id,
                    "max_parallel": scope.snapshot["definition"]["max_parallel"],
                }
            ],
        )
    )


def pinned_child_call_issues(
    port_version: int | None, task: GraphTask, child: dict, path: str
) -> list[dict]:
    """Check a static call against its exact asset in publish, import and launch.

    Args:
        port_version: Caller's port contract version.
        task: Static call and its declared bindings.
        child: Definition of the pinned asset version.
        path: Diagnostic prefix for the call node.

    Returns:
        Contract and reference diagnostics for the editor.
    """
    team = child.get("kind") == "autonomous"
    kind = "team" if task.team else "workflow"
    if team != bool(task.team):
        return [
            {
                "path": f"{path}.{kind}",
                "code": "INCOMPATIBLE",
                "message": "Choose an asset of the matching kind",
            }
        ]
    structured_child = child.get("port_contract_version") == 2
    if structured_child and port_version != 2:
        return [
            {
                "path": f"{path}.{kind}",
                "code": "INCOMPATIBLE",
                "message": "Structured-port workflows require a static V2 call node",
            }
        ]
    if team:
        output = child.get("output_contract")
    else:
        result = next(
            (
                node
                for node in child.get("tasks", [])
                if node["id"] == child.get("result_task_id")
            ),
            None,
        )
        explicit = child.get("entry_task_ids") is not None
        if result is None or (
            result.get("control")
            and not (explicit and result["control"]["kind"] == "join")
        ):
            return [
                {
                    "path": f"{path}.{kind}",
                    "code": "REFERENCE",
                    "message": "The pinned workflow has no valid public result",
                }
            ]
        output = GraphTask.model_validate(result).result_contract(
            explicit_control=explicit
        )

    fields = []
    required = child.get("required_inputs", [])
    if structured_child or task.input_contract is not None:
        contract = (
            child.get("input_contract")
            if structured_child
            else {
                "type": "object",
                "properties": {name: {"type": "string"} for name in required},
                "required": required,
            }
        )
        if task.input_contract != contract:
            fields.append(
                {
                    "path": f"{path}.input_contract",
                    "code": "CONTRACT",
                    "message": "Use the pinned asset's input contract",
                }
            )
    if (
        not structured_child
        and set(task.inputs) != set(required)
        and task.input_contract is None
    ):
        fields.append(
            {
                "path": f"{path}.inputs",
                "code": "CONTRACT",
                "message": "Declare exactly the pinned asset's required inputs",
            }
        )
    if task.input_contract is not None and output is not None:
        schemas = [output]
        for schema in schemas:
            schemas.extend(schema.get("allOf", []))
        public = {name for schema in schemas for name in schema.get("properties", {})}
        mandatory = {name for schema in schemas for name in schema.get("required", [])}
        closed = any(
            schema.get("additionalProperties") is False
            and not schema.get("patternProperties")
            for schema in schemas
        )
        if mandatory - public or (
            not closed
            and any(
                schema.get("minProperties", 0) > len(mandatory)
                or schema.keys()
                & {
                    "if",
                    "oneOf",
                    "anyOf",
                    "dependentRequired",
                    "dependentSchemas",
                    "not",
                }
                for schema in schemas
            )
        ):
            fields.append(
                {
                    "path": f"{path}.output_contract",
                    "code": "CONTRACT",
                    "message": "The public result contract must validate using only declared fields",
                }
            )
    if task.output_contract != output:
        fields.append(
            {
                "path": f"{path}.output_contract",
                "code": "CONTRACT",
                "message": "Use the pinned asset's public output contract",
            }
        )
    return fields


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
        self._executions: dict[str, asyncio.Task] = {}
        self._dispatching: dict[str, asyncio.Task] = {}
        self._closing = False
        self._document_parser_slot = asyncio.Semaphore(1)
        from astrbot.core.agent_teams.chat import WorkflowChat

        self.chat = WorkflowChat(self)

    def _installed_components(
        self,
    ) -> dict[tuple[str, str], tuple[dict, WorkflowComponent]]:
        """Index exact components from the active plugin lifecycle.

        Returns:
            Unique registered component versions and their local handlers.
        """
        context = getattr(self.roles.core_lifecycle, "star_context", None)
        result = {}
        conflicts = set()
        for plugin in context.get_all_stars() if context else ():
            if not plugin.activated:
                continue
            for component in plugin.workflow_components:
                entry = component.descriptor(plugin.plugin_id)
                key = (entry["reference"]["id"], component.version)
                if key in result:
                    conflicts.add(key)
                result[key] = (entry, component)
        return {key: value for key, value in result.items() if key not in conflicts}

    async def list_components(self, username: str) -> list[dict]:
        """Return portable catalog entries visible under Dashboard admission.

        Args:
            username: Stable owner of an enabled Dashboard administrator account.

        Returns:
            Registered definitions with no live callbacks or local credentials.
        """
        await self._require_component_owner(username, "components")
        return [entry for entry, _ in self._installed_components().values()]

    async def _resolve_component(
        self, username: str, node: dict, path: str
    ) -> WorkflowComponent:
        """Revalidate the exact node, owner and local resource before use.

        Args:
            username: Authenticated Dashboard resource owner.
            node: Node with pinned component, schemas and local bindings.
            path: Node path in the editable or fixed workflow.

        Returns:
            The currently installed implementation of the exact contract.

        Raises:
            RoleAssetError: The contract or its resource is inaccessible.
        """
        path_field = f"{path}.component"
        await self._require_component_owner(username, path_field)
        reference = node["component"]
        match = self._installed_components().get(
            (reference["id"], reference["version"])
        )
        if match is None:
            message = "Install and enable the exact workflow component version"
            raise RoleAssetError(
                message, [{"path": path_field, "code": "MISSING", "message": message}]
            )
        entry, component = match
        if reference != entry["reference"] or any(
            node.get(key) != entry[key] for key in ("input_contract", "output_contract")
        ):
            message = "The installed workflow component contract differs"
            raise RoleAssetError(
                message,
                [{"path": path_field, "code": "INCOMPATIBLE", "message": message}],
            )
        kb_id = node.get("knowledge_base_id")
        if bool(kb_id) != component.knowledge_base:
            message = "Choose exactly the resources declared by the component"
            raise RoleAssetError(
                message,
                [
                    {
                        "path": f"{path}.knowledge_base_id",
                        "code": "RESOURCE",
                        "message": message,
                    }
                ],
            )
        if kb_id:
            await self._require_native_knowledge_base(
                username, kb_id, f"{path}.knowledge_base_id"
            )
        # Resource checks can yield to a concurrent plugin reload or removal.
        live = self._installed_components().get((reference["id"], reference["version"]))
        if live is None or live[1] is not component:
            message = "The workflow component changed during admission"
            raise RoleAssetError(
                message,
                [{"path": path_field, "code": "UNAVAILABLE", "message": message}],
            )
        return component

    async def save_asset(
        self,
        username: str,
        payload: dict,
        asset_id: str | None = None,
        draft_revision: int | None = None,
        draft_kind: str | None = None,
        *,
        provision_example: bool = False,
    ) -> dict:
        """Save a version without creating any execution context.

        Args:
            username: Authenticated owner.
            payload: Definition and optional expected version.
            asset_id: Existing asset identity, or None for a new asset.
            draft_revision: Trusted draft revision to publish, when supplied.
            draft_kind: Workflow or autonomous draft category.
            provision_example: Register the built-in example on first workbench access.

        Returns:
            Persisted asset version.

        Raises:
            RoleAssetError: Invalid fields, inaccessible role or conflicting version.
        """
        draft = None
        if draft_revision is not None:
            if asset_id is None or draft_kind not in {"workflow", "autonomous"}:
                raise RoleAssetError("Draft identity and kind are required")
            try:
                draft = await self.roles.get_draft(username, draft_kind, asset_id)
            except RoleAssetError as exc:
                if exc.status == 404:
                    raise RoleAssetError("Draft is inaccessible", status=409) from exc
                raise
            self.roles._validate_draft_body(draft_kind, draft["body"])
            async with self.db.get_db() as session:
                if await session.get(AgentAssetTombstone, ("workflow", asset_id)):
                    raise RoleAssetError("Asset has been deleted", status=409)
                published = await session.get(
                    AgentAssetDraftPublication, (draft_kind, asset_id, draft_revision)
                )
                published_version = published.version if published else None
            if published_version is not None:
                return await self.get_asset(username, asset_id, published_version)
            if draft["revision"] != draft_revision:
                raise RoleAssetError("Draft was edited elsewhere", status=409)
            payload = {
                **{k: v for k, v in draft["body"].items() if k != "editor"},
                "expected_version": draft["base_version"],
            }
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
        if draft is not None and (
            isinstance(definition, AutonomousDefinition) != (draft_kind == "autonomous")
        ):
            raise RoleAssetError("Draft kind does not match definition")
        if isinstance(definition, AutonomousDefinition):
            if "goal" not in definition.required_inputs:
                raise RoleAssetError("Autonomous teams require a goal input")
            if (
                len(
                    {
                        role.id
                        for role in [definition.coordinator, *definition.allowed_roles]
                    }
                )
                != len(definition.allowed_roles) + 1
            ):
                raise RoleAssetError(
                    "Allowed role identities must be unique",
                    [
                        {
                            "path": "definition.allowed_roles",
                            "code": "DUPLICATE",
                            "message": "Choose a unique preset ID",
                        }
                    ],
                )
            for role in [definition.coordinator, *definition.allowed_roles]:
                await self._validate_task_definition(
                    username,
                    GraphTask.model_validate(
                        {
                            "id": "preset",
                            "name": definition.name,
                            "member": "preset",
                            "task": "{goal}",
                            "inputs": {
                                name: {"input": name}
                                for name in definition.required_inputs
                            },
                            "output_contract": definition.output_contract,
                            **role.model_dump(exclude={"id"}, exclude_none=True),
                        }
                    ),
                    required_inputs=definition.required_inputs,
                )
            references = set()
            for index, ref in enumerate(definition.allowed_workflows):
                path = f"definition.allowed_workflows.{index}"
                key = (ref.asset_id, ref.version)
                if key in references:
                    raise RoleAssetError(
                        "Allowed workflow versions must be unique",
                        [
                            {
                                "path": path,
                                "code": "DUPLICATE",
                                "message": "Choose each version once",
                            }
                        ],
                    )
                references.add(key)
                try:
                    child = await self.get_asset(username, ref.asset_id, ref.version)
                except RoleAssetError as exc:
                    raise RoleAssetError(
                        "Pinned workflow version is not accessible",
                        [
                            {
                                "path": path,
                                "code": "REFERENCE",
                                "message": "Choose an accessible exact workflow version",
                            }
                        ],
                        status=exc.status,
                    ) from exc
                child_definition = child["definition"]
                if child_definition.get("port_contract_version") == 2:
                    raise RoleAssetError(
                        "Invalid allowed workflow",
                        [
                            {
                                "path": path,
                                "code": "INCOMPATIBLE",
                                "message": "Structured-port workflows require a static V2 call node",
                            }
                        ],
                    )
                if child_definition.get("kind") == "autonomous":
                    raise RoleAssetError(
                        "Choose a saved workflow",
                        [
                            {
                                "path": path,
                                "code": "INCOMPATIBLE",
                                "message": "Choose a workflow, not an autonomous team",
                            }
                        ],
                    )
                result_id = child_definition.get("result_task_id")
                if not any(
                    node["id"] == result_id and not node.get("control")
                    for node in child_definition.get("tasks", [])
                ):
                    raise RoleAssetError(
                        "Pinned workflow has no public result",
                        [
                            {
                                "path": path,
                                "code": "REFERENCE",
                                "message": "Choose a workflow with a valid public result",
                            }
                        ],
                    )
        elif isinstance(definition, GraphDefinition):
            validate_graph(definition, result_requirement="reachable")
            for task_index, task in enumerate(definition.tasks):
                if task.component:
                    await self._resolve_component(
                        username, task.model_dump(), f"definition.tasks.{task_index}"
                    )
                elif task.decision_provider_id:
                    await self._require_decision_provider(
                        username,
                        task.decision_provider_id,
                        f"definition.tasks.{task_index}.decision_provider_id",
                    )
                elif task.knowledge_base_id:
                    await self._require_native_knowledge_base(
                        username,
                        task.knowledge_base_id,
                        f"definition.tasks.{task_index}.knowledge_base_id",
                    )
                if task.human or task.control or task.transform:
                    continue
                call = task.team or task.workflow
                if call is not None:
                    kind = "team" if task.team else "workflow"
                    path = f"definition.tasks.{task_index}"
                    try:
                        child = await self.get_asset(
                            username, call.asset_id, call.version
                        )
                    except RoleAssetError as exc:
                        raise RoleAssetError(
                            "Pinned child version is not accessible",
                            [
                                {
                                    "path": f"{path}.{kind}",
                                    "code": "REFERENCE",
                                    "message": "Choose an accessible exact asset version",
                                }
                            ],
                            status=exc.status,
                        ) from exc
                    child_definition = child["definition"]
                    fields = pinned_child_call_issues(
                        definition.port_contract_version, task, child_definition, path
                    )
                    if not fields and task.workflow:
                        try:
                            pinned_results = [child]
                            if task.id == definition.result_task_id:
                                included = await self._asset_closure(
                                    username, child, public_result_chain=True
                                )
                                pinned_results = list(included.values())
                            for pinned in pinned_results:
                                pinned_definition = pinned["definition"]
                                if pinned_definition.get("kind") == "autonomous":
                                    raise RoleAssetError(
                                        "A pinned result call must reference a workflow"
                                    )
                                validate_graph(
                                    GraphDefinition.model_validate(pinned_definition),
                                    result_requirement="reachable",
                                )
                        except RoleAssetError as exc:
                            fields.extend(
                                {**field, "path": f"{path}.workflow"}
                                for field in exc.fields
                                or [{"code": "REFERENCE", "message": str(exc)}]
                            )
                    if not fields:
                        try:
                            closure = await self._asset_closure(username, child)
                            for pinned in closure.values():
                                for index, node in enumerate(
                                    pinned["definition"].get("tasks", [])
                                ):
                                    if node.get("component"):
                                        await self._resolve_component(
                                            username,
                                            node,
                                            f"{path}.{kind}.tasks.{index}",
                                        )
                        except RoleAssetError as exc:
                            fields.extend(
                                exc.fields
                                or [
                                    {
                                        "path": f"{path}.{kind}",
                                        "code": "REFERENCE",
                                        "message": str(exc),
                                    }
                                ]
                            )
                    if fields:
                        raise RoleAssetError("Invalid child call", fields)
                    continue
                try:
                    await self._validate_task_definition(
                        username,
                        task,
                        required_inputs=list(task.inputs),
                    )
                except RoleAssetError as exc:
                    if exc.status != 404 or task.role_id is None:
                        raise RoleAssetError(
                            str(exc),
                            [
                                {
                                    **field,
                                    "path": f"definition.tasks.{task_index}."
                                    + field["path"].removeprefix("definition."),
                                }
                                for field in exc.fields
                            ],
                            status=exc.status,
                        ) from exc
                    raise RoleAssetError(
                        str(exc),
                        [
                            {
                                "path": f"definition.tasks.{task_index}.role_id",
                                "code": "REFERENCE",
                                "message": "Choose an accessible exact role version",
                            }
                        ],
                        status=exc.status,
                    ) from exc
            validate_graph(definition, check_structured_shapes=True)
        version = 1
        if asset_id is not None and (
            draft is None or draft["base_version"] is not None
        ):
            current = await self.get_asset(username, asset_id)
            if current["version"] != request.expected_version:
                raise RoleAssetError(
                    "Workflow was edited elsewhere; reload first", status=409
                )
            version = current["version"] + 1
        elif asset_id is None:
            asset_id = uuid.uuid4().hex
        async with self.db.get_db() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            if provision_example:
                example_kind = (
                    "autonomous"
                    if isinstance(definition, AutonomousDefinition)
                    else "workflow"
                )
                existing = await session.get(
                    AgentAssetExample, (username, example_kind)
                )
                if existing is not None:
                    # A concurrent workbench request already published the example.
                    existing_id = existing.asset_id
                    await session.rollback()
                    return await self.get_asset(username, existing_id)
                session.add(
                    AgentAssetExample(
                        owner_username=username, kind=example_kind, asset_id=asset_id
                    )
                )
            if await session.get(AgentAssetTombstone, ("workflow", asset_id)):
                raise RoleAssetError("Asset has been deleted", status=409)
            base = None
            base_version = (
                draft["base_version"] if draft is not None else request.expected_version
            )
            if base_version is not None:
                base_row = await session.get(
                    AgentWorkflowVersion, (asset_id, base_version)
                )
                if base_row and base_row.owner_username == username:
                    base = base_row.definition
            await self.roles.check_active_references(
                session, definition.model_dump(exclude_none=False), base
            )
            if draft is not None:
                result = await session.execute(
                    update(AgentAssetDraft)
                    .where(
                        AgentAssetDraft.kind == draft_kind,
                        AgentAssetDraft.asset_id == asset_id,
                        AgentAssetDraft.owner_username == username,
                        AgentAssetDraft.revision == draft_revision,
                        AgentAssetDraft.base_version == draft["base_version"],
                    )
                    .values(
                        base_version=version,
                        published_revision=draft_revision,
                        published_version=version,
                    )
                )
                if result.rowcount != 1:
                    await session.rollback()
                    published = await session.get(
                        AgentAssetDraftPublication,
                        (draft_kind, asset_id, draft_revision),
                    )
                    if published is not None:
                        return await self.get_asset(
                            username, asset_id, published.version
                        )
                    raise RoleAssetError("Draft was edited elsewhere", status=409)
                session.add(
                    AgentAssetDraftPublication(
                        kind=draft_kind,
                        asset_id=asset_id,
                        revision=draft_revision,
                        version=version,
                    )
                )
            session.add(
                AgentWorkflowVersion(
                    asset_id=asset_id,
                    version=version,
                    owner_username=username,
                    # Null is a meaningful condition value, not an unset option.
                    definition=definition.model_dump(exclude_none=False),
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
        self,
        username: str,
        definition: GraphTask,
        package_roles: set[tuple[str, int]] | None = None,
        required_inputs: list[str] | None = None,
    ) -> None:
        """Validate the shared role, template and contracted output semantics.

        Args:
            username: Authenticated asset author.
            definition: One task in the unified workflow graph.
            package_roles: Portable references validated before local installation.
            required_inputs: Declared graph and task inputs for a graph task.

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
        if isinstance(definition, GraphTask) and definition.dispatch:
            names = ["item"]
        else:
            names = (
                required_inputs
                if required_inputs is not None
                else list(definition.inputs)
            )
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
        if isinstance(definition, GraphTask) and definition.role is not None:
            self.roles._validate_request(
                {
                    "definition": definition.role.model_dump(),
                    "installation": definition.installation.model_dump(),
                }
            )
        elif package_roles is None:
            await self.roles.get_role(
                username, definition.role_id, definition.role_version
            )
        elif (definition.role_id, definition.role_version) not in package_roles:
            raise RoleAssetError(
                "Package role reference not found",
                [
                    {
                        "path": "definition.role_id",
                        "code": "REFERENCE",
                        "message": "Every task must reference an included exact role version",
                    }
                ],
            )

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
            await session.execute(text("BEGIN"))
            asset = AgentWorkflowVersion
            owned = (asset.asset_id == asset_id, asset.owner_username == username)
            query = sa_select(
                asset.version,
                col(asset.definition).cast(Text).label("body"),
                sa_select(AgentAssetExample.asset_id)
                .where(
                    AgentAssetExample.asset_id == asset_id,
                    AgentAssetExample.owner_username == username,
                    col(AgentAssetExample.kind).in_(["workflow", "autonomous"]),
                )
                .exists()
                .label("is_example"),
                sa_select(AgentAssetTombstone.asset_id)
                .where(
                    AgentAssetTombstone.kind == "workflow",
                    AgentAssetTombstone.asset_id == asset_id,
                )
                .exists()
                .label("deleted"),
            ).where(*owned)
            if version is not None:
                query = query.where(asset.version == version)
            row = (
                await session.execute(
                    query.order_by(col(asset.version).desc()).limit(1)
                )
            ).one_or_none()
            if row is None:
                raise RoleAssetError("Workflow version not found", status=404)
            try:
                definition = json.loads(row.body)
                WorkflowSaveRequest.model_validate({"definition": definition})
            except (ValidationError, ValueError) as exc:
                fields = (
                    [
                        {
                            "path": ".".join(map(str, error["loc"])),
                            "code": "UNSUPPORTED_FORMAT",
                            "message": error["msg"],
                        }
                        for error in exc.errors(include_input=False)
                    ]
                    if isinstance(exc, ValidationError)
                    else [
                        {
                            "path": "definition",
                            "code": "UNSUPPORTED_FORMAT",
                            "message": "Invalid stored JSON",
                        }
                    ]
                )
                raise RoleAssetError(
                    "Stored workflow format is unsupported; recreate it in the current editor",
                    fields,
                    status=409,
                ) from exc
            versions = (
                (
                    await session.execute(
                        sa_select(asset.version)
                        .where(*owned)
                        .order_by(col(asset.version).desc())
                    )
                )
                .scalars()
                .all()
            )
            return {
                "asset_id": asset_id,
                "version": row.version,
                "definition": definition,
                "versions": list(versions),
                "is_builtin_example": row.is_example,
                "deleted": row.deleted,
            }

    async def list_assets(
        self,
        username: str,
        *,
        include_example: bool = False,
        include_team_example: bool = False,
        summary_only: bool = False,
    ) -> dict:
        """List the owner's latest asset versions.

        Args:
            username: Authenticated owner.
            include_team_example: Provision the autonomous starter team.
            summary_only: Return compact fields for workbench cards.
            include_example: Provision this owner's sample on first workbench access.

        Returns:
            Latest definitions, without runtime state.
        """
        for requested, kind, definition in (
            (include_example, "workflow", RELEASE_BRIEF),
            (include_team_example, "autonomous", RELEASE_TEAM),
        ):
            if requested:
                async with self.db.get_db() as session:
                    marker = await session.get(AgentAssetExample, (username, kind))
                if marker is None:
                    await self.save_asset(
                        username, {"definition": definition}, provision_example=True
                    )
        assets = []
        unavailable = []
        async with self.db.get_db() as session:
            await session.execute(text("BEGIN"))
            latest = (
                sa_select(
                    AgentWorkflowVersion.asset_id,
                    func.max(AgentWorkflowVersion.version).label("version"),
                )
                .where(AgentWorkflowVersion.owner_username == username)
                .group_by(AgentWorkflowVersion.asset_id)
                .subquery()
            )
            rows = (
                await session.execute(
                    sa_select(
                        AgentWorkflowVersion.asset_id,
                        AgentWorkflowVersion.version,
                        col(AgentWorkflowVersion.definition).cast(Text).label("body"),
                        col(AgentAssetExample.asset_id)
                        .is_not(None)
                        .label("is_example"),
                    )
                    .join(
                        latest,
                        and_(
                            AgentWorkflowVersion.asset_id == latest.c.asset_id,
                            AgentWorkflowVersion.version == latest.c.version,
                        ),
                    )
                    .outerjoin(
                        AgentAssetExample,
                        and_(
                            AgentAssetExample.asset_id == AgentWorkflowVersion.asset_id,
                            AgentAssetExample.owner_username == username,
                            col(AgentAssetExample.kind).in_(["workflow", "autonomous"]),
                        ),
                    )
                    .where(
                        AgentWorkflowVersion.owner_username == username,
                        ~sa_select(AgentAssetTombstone.asset_id)
                        .where(
                            AgentAssetTombstone.kind == "workflow",
                            AgentAssetTombstone.asset_id
                            == AgentWorkflowVersion.asset_id,
                        )
                        .exists(),
                    )
                    .order_by(AgentWorkflowVersion.asset_id)
                )
            ).all()
            versions: dict[str, list[int]] = {}
            if rows and not summary_only:
                for identity, version in (
                    await session.execute(
                        select(
                            AgentWorkflowVersion.asset_id, AgentWorkflowVersion.version
                        )
                        .where(AgentWorkflowVersion.owner_username == username)
                        .order_by(col(AgentWorkflowVersion.version).desc())
                    )
                ).all():
                    versions.setdefault(identity, []).append(version)
            for row in rows:
                try:
                    definition = json.loads(row.body)
                    parsed = WorkflowSaveRequest.model_validate(
                        {"definition": definition}
                    ).definition
                except (ValidationError, ValueError):
                    unavailable.append(
                        {
                            "asset_id": row.asset_id,
                            "version": row.version,
                            "code": "UNSUPPORTED_FORMAT",
                        }
                    )
                    continue
                item = {
                    "asset_id": row.asset_id,
                    "version": row.version,
                    "is_builtin_example": row.is_example,
                    "deleted": False,
                }
                if summary_only:
                    team = isinstance(parsed, AutonomousDefinition)
                    item["summary"] = {
                        "name": parsed.name,
                        "kind": "autonomous" if team else "workflow",
                        "max_parallel": parsed.max_parallel,
                        "count": len(parsed.allowed_roles if team else parsed.tasks),
                        "preview": [
                            member.role.name if member.role else member.id
                            for member in parsed.allowed_roles[:4]
                        ]
                        if team
                        else [task.name for task in parsed.tasks[:4]],
                        **(
                            {
                                "preview_role_ids": [
                                    member.role_id
                                    for member in parsed.allowed_roles[:4]
                                ]
                            }
                            if team
                            else {}
                        ),
                    }
                else:
                    item.update(definition=definition, versions=versions[row.asset_id])
                assets.append(item)
        return {
            "assets": assets,
            **({"unavailable_assets": unavailable} if unavailable else {}),
        }

    async def _asset_closure(
        self,
        username: str,
        asset: dict,
        *,
        asset_limit: int | None = None,
        public_result_chain: bool = False,
    ) -> dict:
        """Read authorized fixed dependencies for export or publication.

        Args:
            username: Owner used for every pinned reference lookup.
            asset: Authorized saved entry returned by get_asset.
            asset_limit: Optional package export ceiling.
            public_result_chain: Follow only workflow calls selected as public results.

        Returns:
            Independent asset copies keyed by local identity and version.

        Raises:
            RoleAssetError: A reference is missing, cyclic, too deep or too large.
        """
        included = {}
        pending: list[tuple[dict, tuple[tuple[str, int], ...]]] = [(asset, ())]
        while pending:
            current, ancestors = pending.pop()
            key = (current["asset_id"], current["version"])
            if key in ancestors or len(ancestors) > 20:
                raise RoleAssetError(
                    "Nested references contain a cycle or exceed the depth limit"
                )
            if key in included:
                continue
            if asset_limit is not None and len(included) >= asset_limit:
                raise RoleAssetError("Package exceeds the supported asset limit")
            current = {
                field: copy.deepcopy(current[field])
                for field in ("asset_id", "version", "definition")
            }
            included[key] = current
            content = current["definition"]
            if (
                asset_limit is not None
                and content.get("kind") != "autonomous"
                and "tasks" not in content
            ):
                raise RoleAssetError("Legacy workflow definitions cannot be exported")
            if public_result_chain:
                references = [
                    task["workflow"]
                    for task in content.get("tasks", [])
                    if task["id"] == content.get("result_task_id")
                    and task.get("workflow")
                ]
            else:
                references = [
                    *content.get("allowed_workflows", []),
                    *[
                        task.get("team") or task.get("workflow")
                        for task in content.get("tasks", [])
                        if task.get("team") or task.get("workflow")
                    ],
                ]
            for reference in references:
                pending.append(
                    (
                        await self.get_asset(
                            username, reference["asset_id"], reference["version"]
                        ),
                        (*ancestors, key),
                    )
                )
        return included

    @staticmethod
    def _package_contents(
        package: WorkflowPackage, root: tuple[str, int] | None = None
    ) -> dict:
        """Validate and select a reachable fixed closure.

        Args:
            package: Typed source package.
            root: Optional included asset to use as the closure root.

        Returns:
            Source asset, role and resource identities reachable from the root.

        Raises:
            RoleAssetError: A reference is missing, duplicated, cyclic or unreachable.
        """
        assets = {(package.asset_id, package.version): package.definition.model_dump()}
        for asset in package.assets:
            key = (asset.asset_id, asset.version)
            if key in assets:
                raise RoleAssetError("Duplicate package asset versions")
            assets[key] = asset.definition.model_dump()
        roles = {}
        for role in package.roles:
            key = (role.role_id, role.version)
            if key in roles:
                raise RoleAssetError("Duplicate package role versions")
            roles[key] = role.model_dump()
        root = root or (package.asset_id, package.version)
        included, used_roles, resources = set(), set(), set()
        pending = [(root, ())]
        while pending:
            key, ancestors = pending.pop()
            if key in ancestors or len(ancestors) > 20:
                raise RoleAssetError(
                    "Nested references contain a cycle or exceed the depth limit"
                )
            if key not in assets:
                raise RoleAssetError("Package is missing a pinned asset")
            if key in included:
                continue
            included.add(key)
            definition = assets[key]
            if definition.get("default_provider_id"):
                resources.add(("models", definition["default_provider_id"]))
            members = (
                [definition["coordinator"], *definition["allowed_roles"]]
                if definition.get("kind") == "autonomous"
                else definition["tasks"]
            )
            if definition.get("kind") == "autonomous":
                if "goal" not in definition["required_inputs"] or len(
                    {m["id"] for m in members}
                ) != len(members):
                    raise RoleAssetError("Invalid autonomous team presets or goal")
                allowed = [
                    (ref["asset_id"], ref["version"])
                    for ref in definition.get("allowed_workflows", [])
                ]
                if len(allowed) != len(set(allowed)):
                    raise RoleAssetError("Allowed workflow versions must be unique")
            else:
                graph = GraphDefinition.model_validate(definition)
                validate_graph(
                    graph,
                    result_requirement="reachable"
                    if graph.port_contract_version
                    else "optional",
                )
            for member in members:
                if member.get("knowledge_base_id"):
                    resources.add(("knowledge_bases", member["knowledge_base_id"]))
                if member.get("decision_provider_id"):
                    resources.add(
                        ("decision_providers", member["decision_provider_id"])
                    )
                if member.get("role_id"):
                    role_key = (member["role_id"], member["role_version"])
                    if role_key not in roles:
                        raise RoleAssetError("Package is missing a pinned role")
                    used_roles.add(role_key)
                for config in (member.get("role") or {}, member.get("overrides") or {}):
                    for field, kind in (
                        ("provider_id", "models"),
                        ("persona_id", "personas"),
                    ):
                        if config.get(field):
                            resources.add((kind, config[field]))
                    for kind in ("tools", "skills", "knowledge_bases"):
                        resources.update(
                            (kind, value) for value in config.get(kind) or []
                        )
                if member.get("installation"):
                    resources.add(("profiles", member["installation"]["profile_id"]))
            refs = [
                *definition.get("allowed_workflows", []),
                *[
                    m.get("team") or m.get("workflow")
                    for m in members
                    if m.get("team") or m.get("workflow")
                ],
            ]
            for ref in refs:
                child_key = (ref["asset_id"], ref["version"])
                if child_key not in assets:
                    raise RoleAssetError("Package is missing a pinned asset")
                pending.append((child_key, (*ancestors, key)))
            if definition.get("kind") != "autonomous":
                for index, task in enumerate(graph.tasks):
                    ref = task.team or task.workflow
                    if ref:
                        fields = pinned_child_call_issues(
                            graph.port_contract_version,
                            task,
                            assets[(ref.asset_id, ref.version)],
                            f"definition.tasks.{index}",
                        )
                        if fields:
                            raise RoleAssetError("Invalid child call", fields)
            for ref in definition.get("allowed_workflows", []):
                child = assets[(ref["asset_id"], ref["version"])]
                if child.get("port_contract_version") == 2:
                    raise RoleAssetError(
                        "Structured-port workflows require a static V2 call node"
                    )
                if child.get("kind") == "autonomous" or not any(
                    task["id"] == child.get("result_task_id")
                    and not task.get("control")
                    for task in child["tasks"]
                ):
                    raise RoleAssetError("Allowed workflow must have a public result")
        for key in used_roles:
            role = roles[key]
            resources.add(("profiles", role["installation"]["profile_id"]))
            config = role["definition"]
            for field, kind in (("provider_id", "models"), ("persona_id", "personas")):
                if config.get(field):
                    resources.add((kind, config[field]))
            for kind in ("tools", "skills", "knowledge_bases"):
                resources.update((kind, value) for value in config.get(kind) or [])
        return {
            "assets": assets,
            "roles": roles,
            "included": included,
            "used_roles": used_roles,
            "resources": resources,
        }

    @staticmethod
    def _package_capabilities(contents: dict) -> dict:
        """Collect capabilities required by the selected fixed closure.

        Args:
            contents: Reachable fixed definitions and resource identities.

        Returns:
            Required model and profile capabilities keyed by source identity.
        """
        capabilities = {}
        for role_key in contents["used_roles"]:
            role = contents["roles"][role_key]
            definition = role["definition"]
            profile_id = role["installation"]["profile_id"]
            capabilities.setdefault(("profiles", profile_id), set()).update(
                ["task_result", *definition.get("runner_capabilities", [])]
            )
            if definition.get("provider_id"):
                capabilities.setdefault(
                    ("models", definition["provider_id"]), set()
                ).update(definition.get("model_capabilities", []))
        for asset_key in contents["included"]:
            definition = contents["assets"][asset_key]
            members = (
                [definition["coordinator"], *definition["allowed_roles"]]
                if definition.get("kind") == "autonomous"
                else definition["tasks"]
            )
            for member in members:
                if member.get("role_id"):
                    role = contents["roles"][
                        (member["role_id"], member["role_version"])
                    ]
                    config, installation = role["definition"], role["installation"]
                elif member.get("role"):
                    config, installation = member["role"], member["installation"]
                else:
                    continue
                runner_requirements = [
                    "task_result",
                    *config.get("runner_capabilities", []),
                ]
                if definition.get("kind") == "autonomous" and member is members[0]:
                    runner_requirements.append("task_tools")
                capabilities.setdefault(
                    ("profiles", installation["profile_id"]), set()
                ).update(runner_requirements)
                provider = (member.get("overrides") or {}).get(
                    "provider_id", config.get("provider_id")
                ) or definition.get("default_provider_id")
                if provider:
                    capabilities.setdefault(("models", provider), set()).update(
                        config.get("model_capabilities", [])
                    )
        return capabilities

    async def export_asset(
        self, username: str, asset_id: str, version: int | None = None
    ) -> dict:
        """Export the exact accessible closure and source resource requirements.

        Args:
            username: Authenticated owner.
            asset_id: Saved entry identity.
            version: Exact version, or latest when omitted.

        Returns:
            Native package containing only fixed definitions and safe metadata.

        Raises:
            RoleAssetError: An included version is missing or inaccessible.
        """
        asset = await self.get_asset(username, asset_id, version)
        if asset["deleted"]:
            raise RoleAssetError("Asset has been deleted", status=409)
        included = await self._asset_closure(username, asset, asset_limit=100)
        references = set()
        contains_components = False
        for current in included.values():
            definition = current["definition"]
            members = (
                [definition["coordinator"], *definition["allowed_roles"]]
                if definition.get("kind") == "autonomous"
                else definition["tasks"]
            )
            contains_components |= any(member.get("component") for member in members)
            references.update(
                (m["role_id"], m["role_version"]) for m in members if m.get("role_id")
            )
        roles = []
        for role_id, role_version in sorted(references):
            role = await self.roles.get_role(username, role_id, role_version)
            roles.append(
                {
                    key: role[key]
                    for key in ("role_id", "version", "definition", "installation")
                }
            )
        raw = {
            "format": "astrbot.workflow",
            "format_version": 4 if contains_components else 3,
            **included[(asset_id, asset["version"])],
            "assets": [
                included[key]
                for key in sorted(included)
                if key != (asset_id, asset["version"])
            ],
            "roles": roles,
            "requirements": [],
        }
        try:
            package = WorkflowPackage.model_validate(raw)
        except ValidationError as exc:
            raise RoleAssetError("Asset is not in the supported format") from exc
        contents = self._package_contents(package)
        catalog = await self.roles.get_resources()
        capabilities = self._package_capabilities(contents)
        for kind, source_id in sorted(contents["resources"]):
            resource = next((r for r in catalog[kind] if r["id"] == source_id), {})
            raw["requirements"].append(
                {
                    "kind": kind,
                    "source_id": source_id,
                    "display_name": resource.get("name") or source_id,
                    "description": resource.get("description") or "",
                    "fingerprint": resource.get("fingerprint"),
                    "capabilities": sorted(capabilities.get((kind, source_id), set())),
                    "runner_type": resource.get("runner_type", "local")
                    if kind == "profiles" and resource
                    else None,
                    "input_schema": resource.get("input_schema")
                    if kind == "tools"
                    else None,
                }
            )
        return WorkflowPackage.model_validate(raw).model_dump()

    async def _validate_package(
        self, username: str, payload: dict
    ) -> WorkflowImportRequest:
        """Validate source closure and all declarations before matching resources.

        Args:
            username: Authenticated importing owner.
            payload: Untrusted package and explicit selections.

        Returns:
            Validated request.

        Raises:
            RoleAssetError: Invalid schema, closure or declarations.
        """
        try:
            request = WorkflowImportRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid workflow package",
                [
                    {
                        "path": ".".join(map(str, e["loc"])),
                        "code": e["type"],
                        "message": e["msg"],
                    }
                    for e in exc.errors()
                ],
            ) from exc
        package = request.package
        try:
            WorkflowPackage.content_digest(package.model_dump())
        except (ValueError, TypeError) as exc:
            raise RoleAssetError("Package must contain finite JSON values") from exc
        for asset_index, definition in enumerate(
            [
                package.definition.model_dump(),
                *[asset.definition.model_dump() for asset in package.assets],
            ]
        ):
            members = (
                [definition["coordinator"], *definition["allowed_roles"]]
                if definition.get("kind") == "autonomous"
                else definition["tasks"]
            )
            for member_index, member in enumerate(members):
                if member.get("component") and package.format_version != 4:
                    raise RoleAssetError(
                        "Plugin components require native workflow package version 4"
                    )
                if (
                    member.get("human")
                    or member.get("control")
                    or member.get("transform")
                    or member.get("team")
                    or member.get("workflow")
                ):
                    continue
                task = (
                    GraphTask.model_validate(
                        {
                            "id": "preset",
                            "name": definition["name"],
                            "member": "preset",
                            "task": "{goal}",
                            "inputs": {"goal": {"input": "goal"}},
                            **{k: v for k, v in member.items() if k != "id"},
                        }
                    )
                    if definition.get("kind") == "autonomous"
                    else GraphTask.model_validate(member)
                )
                try:
                    await self._validate_task_definition(
                        username,
                        task,
                        {(role.role_id, role.version) for role in package.roles},
                        required_inputs=list(task.inputs),
                    )
                except RoleAssetError as exc:
                    prefix = (
                        "definition"
                        if asset_index == 0
                        else f"package.assets.{asset_index - 1}.definition"
                    )
                    if definition.get("kind") == "autonomous":
                        prefix += (
                            ".coordinator"
                            if member_index == 0
                            else f".allowed_roles.{member_index - 1}"
                        )
                    else:
                        prefix += f".tasks.{member_index}"
                    raise RoleAssetError(
                        str(exc),
                        [
                            {
                                **issue,
                                "path": prefix
                                + "."
                                + issue["path"].removeprefix("definition."),
                            }
                            for issue in exc.fields
                        ],
                        status=exc.status,
                    ) from exc
        contents = self._package_contents(package)
        if len(contents["assets"]) > 100:
            raise RoleAssetError("Package exceeds the supported asset limit")
        if contents["included"] != set(contents["assets"]) or contents[
            "used_roles"
        ] != set(contents["roles"]):
            raise RoleAssetError("Package contains unreachable definitions")
        requirements = [(r.kind, r.source_id) for r in package.requirements]
        if (
            len(requirements) != len(set(requirements))
            or set(requirements) != contents["resources"]
        ):
            raise RoleAssetError(
                "Resource requirements do not match the included definitions"
            )
        capabilities = self._package_capabilities(contents)
        for requirement in package.requirements:
            if requirement.kind in {"profiles", "models"} and set(
                requirement.capabilities
            ) != capabilities.get((requirement.kind, requirement.source_id), set()):
                raise RoleAssetError(
                    "Resource capabilities do not match the included definitions"
                )
        choices = [(s.kind, s.source_id) for s in request.selections]
        if len(choices) != len(set(choices)) or not set(choices).issubset(
            set(requirements)
        ):
            raise RoleAssetError(
                "Resource selections must uniquely identify declared requirements"
            )
        return request

    @staticmethod
    def _rewrite_package_config(config: dict, selections: dict) -> None:
        """Rewrite explicit resource references without changing omitted or null values.

        Args:
            config: Role, overrides, installation or asset definition to update.
            selections: Mapping from resource type and source identity to local ID.
        """
        for field, kind in (
            ("provider_id", "models"),
            ("default_provider_id", "models"),
            ("persona_id", "personas"),
            ("profile_id", "profiles"),
            ("knowledge_base_id", "knowledge_bases"),
            ("decision_provider_id", "decision_providers"),
        ):
            if config.get(field) is not None:
                config[field] = selections[(kind, config[field])]
        for kind in ("tools", "skills", "knowledge_bases"):
            if config.get(kind) is not None:
                config[kind] = [selections[(kind, value)] for value in config[kind]]

    @staticmethod
    def _package_manifests(
        package: WorkflowPackage, contents: dict, choices: dict
    ) -> dict:
        """Build each exact source closure and its selected local resource identities.

        Args:
            package: Validated source closure.
            contents: Reachability and source definitions.
            choices: Explicit mapping from source identities to local IDs.

        Returns:
            Canonical manifests keyed by fixed source asset identity.
        """
        manifests = {}
        for key in contents["included"]:
            closure = WorkflowService._package_contents(package, key)
            manifests[key] = {
                "assets": [
                    {
                        "asset_id": item[0],
                        "version": item[1],
                        "definition": contents["assets"][item],
                    }
                    for item in sorted(closure["included"])
                ],
                "roles": [
                    contents["roles"][item] for item in sorted(closure["used_roles"])
                ],
                "requirements": [
                    r.model_dump()
                    for r in package.requirements
                    if (r.kind, r.source_id) in closure["resources"]
                ],
                "selections": [
                    {"kind": item[0], "source_id": item[1], "target_id": choices[item]}
                    for item in sorted(closure["resources"])
                ],
            }
            capabilities = WorkflowService._package_capabilities(closure)
            for requirement in manifests[key]["requirements"]:
                if requirement["kind"] in {"profiles", "models"}:
                    requirement["capabilities"] = sorted(
                        capabilities.get(
                            (requirement["kind"], requirement["source_id"]), set()
                        )
                    )
            manifests[key]["requirements"].sort(
                key=lambda r: (r["kind"], r["source_id"])
            )
        return manifests

    async def preview_import(self, username: str, payload: dict) -> dict:
        """Match explicit source requirements against the current local catalog.

        Args:
            username: Authenticated owner.
            payload: Source package and current explicit selections.

        Returns:
            Candidate resources, readiness diagnostics and effective configurations.

        Raises:
            RoleAssetError: Package structure or fixed identity conflicts.
        """
        request = await self._validate_package(username, payload)
        package = request.package
        contents = self._package_contents(package)
        choices = {(s.kind, s.source_id): s.target_id for s in request.selections}
        catalog = await self.roles.get_resources()
        issues, requirements = [], []
        for dep in package.requirements:
            key = (dep.kind, dep.source_id)
            candidates = []
            for resource in catalog[dep.kind]:
                reasons = []
                warnings = []
                if not resource.get("enabled", True):
                    reasons.append("Resource is disabled")
                if (
                    dep.runner_type
                    and resource.get("runner_type", "local") != dep.runner_type
                ):
                    reasons.append("Executor type differs")
                if dep.fingerprint and resource.get("fingerprint") != dep.fingerprint:
                    warnings.append("Source fingerprint differs")
                if (
                    dep.input_schema is not None
                    and resource.get("input_schema") != dep.input_schema
                ):
                    reasons.append("Tool input contract differs")
                available_capabilities = (
                    get_agent_runner_capabilities(
                        {
                            "runner_type": resource.get("runner_type", "local"),
                            "config": resource.get("runner_config", {}),
                        }
                    )["capabilities"]
                    if dep.kind == "profiles"
                    else resource.get("capabilities", [])
                )
                if dep.capabilities and not set(dep.capabilities).issubset(
                    available_capabilities
                ):
                    reasons.append("Required capabilities are missing")
                candidates.append(
                    {
                        "id": resource["id"],
                        "name": resource.get("name") or resource["id"],
                        "disabled": bool(reasons),
                        "reasons": reasons,
                        "warnings": warnings,
                    }
                )
            selected = choices.get(key)
            matches = [
                r for r in candidates if r["id"] == selected and not r["disabled"]
            ]
            if len(matches) != 1:
                issues.append(
                    {
                        "path": f"requirements.{dep.kind}.{dep.source_id}",
                        "code": "MISSING" if not selected else "INCOMPATIBLE",
                        "message": "Choose an available compatible local resource",
                    }
                )
            requirements.append(
                {**dep.model_dump(), "selected": selected, "candidates": candidates}
            )
        effective = []
        checked_assets = {}
        if not issues:
            for asset_key, definition in contents["assets"].items():
                checked_members = {}
                checked_assets[asset_key] = checked_members
                local_asset = copy.deepcopy(definition)
                self._rewrite_package_config(local_asset, choices)
                members = (
                    [local_asset["coordinator"], *local_asset["allowed_roles"]]
                    if local_asset.get("kind") == "autonomous"
                    else local_asset["tasks"]
                )
                for member_index, member in enumerate(members):
                    if member.get("knowledge_base_id") or member.get(
                        "decision_provider_id"
                    ):
                        self._rewrite_package_config(member, choices)
                    if member.get("component"):
                        try:
                            await self._resolve_component(
                                username,
                                member,
                                f"assets.{asset_key[0]}.tasks.{member_index}",
                            )
                        except RoleAssetError as exc:
                            issues.extend(exc.fields)
                    if member.get("role_id"):
                        role = copy.deepcopy(
                            contents["roles"][
                                (member["role_id"], member["role_version"])
                            ]
                        )
                    elif member.get("role"):
                        role = {
                            "definition": member["role"],
                            "installation": member["installation"],
                        }
                    else:
                        continue
                    self._rewrite_package_config(role["definition"], choices)
                    self._rewrite_package_config(role["installation"], choices)
                    overrides = copy.deepcopy(member.get("overrides") or {})
                    self._rewrite_package_config(overrides, choices)
                    try:
                        checked = await self.roles.preview_role(role, overrides)
                        if (
                            local_asset.get("default_provider_id")
                            and checked["effective"]["runner"]["type"] == "local"
                            and not overrides.get(
                                "provider_id", role["definition"].get("provider_id")
                            )
                        ):
                            overrides["provider_id"] = local_asset[
                                "default_provider_id"
                            ]
                            checked = await self.roles.preview_role(role, overrides)
                            checked["sources"]["provider_id"] = (
                                "workflow:default_provider_id"
                            )
                    except RoleAssetError as exc:
                        issues.extend(
                            exc.fields
                            or [
                                {
                                    "path": "definition",
                                    "code": "INVALID",
                                    "message": str(exc),
                                }
                            ]
                        )
                        continue
                    if local_asset.get("kind") == "autonomous" and member.get(
                        "role_id"
                    ):
                        base = await self.roles.preview_role(role)
                        for field in ("max_instances", "max_parallel"):
                            base_limit = base["effective"].get(field)
                            current_limit = checked["effective"].get(field)
                            if base_limit is not None and (
                                current_limit is None or current_limit > base_limit
                            ):
                                checked["effective"][field] = base_limit
                                checked["sources"][field] = base["sources"][field]
                    effective.append(
                        {
                            "asset_id": asset_key[0],
                            "version": asset_key[1],
                            "asset_name": definition["name"],
                            "member": member.get("id"),
                            "effective": checked["effective"],
                            "sources": checked["sources"],
                        }
                    )
                    issues.extend(checked["issues"])
                    checked_members[member["id"]] = checked["effective"]
                    needed = {"definition": "task_result"}
                    if local_asset.get("kind") == "autonomous" and member is members[0]:
                        needed["definition.coordinator"] = "task_tools"
                    issues.extend(
                        get_agent_runner_capability_issues(
                            checked["effective"]["runner"], needed
                        )
                    )
                if not local_asset.get("kind"):
                    role_members = {}
                    for member in members:
                        if member.get("role_id"):
                            key = (member["role_id"], member["role_version"])
                            role_members.setdefault(key, set()).add(member["member"])
                    for member in members:
                        if member.get("role_id"):
                            limit = checked_members.get(member["id"], {}).get(
                                "max_instances"
                            )
                            if (
                                limit is not None
                                and len(
                                    role_members[
                                        (member["role_id"], member["role_version"])
                                    ]
                                )
                                > limit
                            ):
                                issues.append(
                                    {
                                        "path": "definition.tasks",
                                        "code": "LIMIT",
                                        "message": "Role member instance limit exceeded",
                                    }
                                )
                    for loop in local_asset.get("loops", []):
                        for member in members:
                            limit = checked_members.get(member["id"], {}).get(
                                "max_iterations"
                            )
                            if (
                                member["id"] in loop["body"]
                                and limit is not None
                                and loop["max_iterations"] > limit
                            ):
                                issues.append(
                                    {
                                        "path": "definition.loops",
                                        "code": "LIMIT",
                                        "message": "Loop exceeds the member iteration limit",
                                    }
                                )
            pending = (
                [((package.asset_id, package.version), 0, 20)] if not issues else []
            )
            visited = set()
            while pending:
                asset_key, depth, limit = pending.pop()
                state = (asset_key, depth, limit)
                if state in visited:
                    continue
                visited.add(state)
                definition = contents["assets"][asset_key]
                members = (
                    [definition["coordinator"], *definition["allowed_roles"]]
                    if definition.get("kind") == "autonomous"
                    else definition["tasks"]
                )
                if depth > limit or any(
                    depth > (value.get("max_depth") or 20)
                    for value in checked_assets[asset_key].values()
                ):
                    issues.append(
                        {
                            "path": "definition.max_depth",
                            "code": "LIMIT",
                            "message": "Nested asset exceeds an effective depth limit",
                        }
                    )
                    continue
                next_limit = min(limit, definition.get("max_depth", 3))
                for member in members:
                    ref = member.get("team") or member.get("workflow")
                    if ref:
                        pending.append(
                            ((ref["asset_id"], ref["version"]), depth + 1, next_limit)
                        )
                if definition.get("kind") == "autonomous":
                    coordinator_limit = (
                        checked_assets[asset_key][definition["coordinator"]["id"]].get(
                            "max_depth"
                        )
                        or 20
                    )
                    for ref in definition.get("allowed_workflows", []):
                        pending.append(
                            (
                                (ref["asset_id"], ref["version"]),
                                depth + 1,
                                min(next_limit, coordinator_limit),
                            )
                        )
        async with self.db.get_db() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentWorkflowPackage).where(
                            AgentWorkflowPackage.owner_username == username
                        )
                    )
                )
                .scalars()
                .all()
            )
            deleted = {
                (row.kind, row.asset_id)
                for row in (await session.execute(select(AgentAssetTombstone)))
                .scalars()
                .all()
            }
        records = {(row.package_id, row.version): row for row in rows}
        manifests = (
            self._package_manifests(package, contents, choices) if not issues else {}
        )
        actions = {}
        for key in contents["included"]:
            row = records.get(key)
            actions[key] = (
                ("reuse" if key in manifests else "review")
                if row
                else "new_version"
                if any(r.package_id == key[0] for r in rows)
                else "install"
            )
            if any(
                r.package_id == key[0] and ("workflow", r.asset_id) in deleted
                for r in rows
            ):
                actions[key] = "conflict"
                issues.append(
                    {
                        "path": f"assets.{key[0]}:{key[1]}",
                        "code": "CONFLICT",
                        "message": "The previously imported local asset has been deleted",
                    }
                )
            if (
                row
                and key in manifests
                and (
                    not isinstance(row.package, dict)
                    or row.package.get("format_version") not in {3, 4}
                    or row.digest != WorkflowPackage.content_digest(manifests[key])
                )
            ):
                actions[key] = "conflict"
                issues.append(
                    {
                        "path": f"assets.{key[0]}:{key[1]}",
                        "code": "CONFLICT",
                        "message": "Same package identity and version have different content or resource selections",
                    }
                )
        for row in rows:
            if not isinstance(row.package, dict) or row.package.get(
                "format_version"
            ) not in {3, 4}:
                continue
            for entry in row.package.get("role_records", []):
                source = entry["source"]
                if (
                    any(item[0] == source["role_id"] for item in contents["used_roles"])
                    and ("role", entry["local_id"]) in deleted
                ):
                    issues.append(
                        {
                            "path": f"roles.{source['role_id']}:{source['version']}",
                            "code": "CONFLICT",
                            "message": "The previously imported local role has been deleted",
                        }
                    )
        if not issues:
            for row in rows:
                if not isinstance(row.package, dict) or row.package.get(
                    "format_version"
                ) not in {3, 4}:
                    continue
                for entry in row.package.get("role_records", []):
                    source = entry["source"]
                    key = (source["role_id"], source["version"])
                    if not any(item[0] == key[0] for item in contents["used_roles"]):
                        continue
                    if key not in contents["used_roles"]:
                        continue
                    local = copy.deepcopy(contents["roles"][key])
                    self._rewrite_package_config(local["definition"], choices)
                    self._rewrite_package_config(local["installation"], choices)
                    if (
                        source != contents["roles"][key]
                        or entry["local_definition"] != local["definition"]
                        or entry["local_installation"] != local["installation"]
                    ):
                        issues.append(
                            {
                                "path": f"roles.{key[0]}:{key[1]}",
                                "code": "CONFLICT",
                                "message": "Same role identity and version have different content or resource selections",
                            }
                        )
        return {
            "package": package.model_dump(),
            "requirements": requirements,
            "effective": effective,
            "issues": issues,
            "ready": not issues,
            "assets": [
                {
                    "asset_id": k[0],
                    "version": k[1],
                    "name": contents["assets"][k]["name"],
                    "action": actions[k],
                }
                for k in sorted(contents["included"])
            ],
        }

    async def import_asset(self, username: str, payload: dict) -> dict:
        """Atomically install the selected closure with local resource identities.

        Args:
            username: Authenticated owner.
            payload: Source package and explicit local resource selections.

        Returns:
            Installed fixed entry and whether it was already imported.

        Raises:
            RoleAssetError: Unresolved resources, changed fixed content or conflict.
        """
        preview = await self.preview_import(username, payload)
        if not preview["ready"]:
            conflict = next(
                (issue for issue in preview["issues"] if issue["code"] == "CONFLICT"),
                None,
            )
            if conflict:
                raise RoleAssetError(conflict["message"], preview["issues"], status=409)
            raise RoleAssetError(
                "Configure package resources before installation", preview["issues"]
            )
        request = await self._validate_package(username, payload)
        package = request.package
        contents = self._package_contents(package)
        choices = {(s.kind, s.source_id): s.target_id for s in request.selections}
        normalized_roles = {}
        for key in contents["used_roles"]:
            role = copy.deepcopy(contents["roles"][key])
            self._rewrite_package_config(role["definition"], choices)
            self._rewrite_package_config(role["installation"], choices)
            normalized_roles[key] = role
        manifests = self._package_manifests(package, contents, choices)
        root = (package.asset_id, package.version)
        nodes = [
            (f"assets.{key[0]}.tasks.{index}", copy.deepcopy(node))
            for key, definition in contents["assets"].items()
            for index, node in enumerate(definition.get("tasks", []))
            if node.get("component")
        ]
        plugin_manager = getattr(self.roles.core_lifecycle, "plugin_manager", None)
        lifecycle_guard = (
            plugin_manager.lifecycle_lock if nodes and plugin_manager else nullcontext()
        )
        async with lifecycle_guard, self.db.get_db() as session:
            for path, node in nodes:
                self._rewrite_package_config(node, choices)
                await self._resolve_component(username, node, path)
            try:
                await session.execute(text("BEGIN IMMEDIATE"))
                rows = (
                    (
                        await session.execute(
                            select(AgentWorkflowPackage).where(
                                AgentWorkflowPackage.owner_username == username
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                records = {(r.package_id, r.version): r for r in rows}
                asset_map, role_map, known_roles = {}, {}, {}
                for row in rows:
                    if not isinstance(row.package, dict) or row.package.get(
                        "format_version"
                    ) not in {3, 4}:
                        continue
                    for entry in row.package.get("role_records", []):
                        key = (entry["source"]["role_id"], entry["source"]["version"])
                        current = known_roles.get(key)
                        if current and current != entry:
                            raise RoleAssetError(
                                "Stored package role identity has conflicting content",
                                status=409,
                            )
                        known_roles[key] = entry
                for key in sorted(
                    contents["included"], key=lambda item: (item == root, item)
                ):
                    row = records.get(key)
                    digest = WorkflowPackage.content_digest(manifests[key])
                    if row:
                        if (
                            row.package.get("format_version") not in {3, 4}
                            or row.digest != digest
                        ):
                            raise RoleAssetError(
                                "Same package identity and version have different content or resource selections",
                                status=409,
                            )
                        asset_map[key] = (row.asset_id, row.installed_version)
                    else:
                        previous = [
                            r
                            for r in rows
                            if r.package_id == key[0]
                            and r.package.get("format_version") in {3, 4}
                        ]
                        allocated = [
                            value
                            for source, value in asset_map.items()
                            if source[0] == key[0]
                        ]
                        identity = (
                            previous[0].asset_id
                            if previous
                            else allocated[0][0]
                            if allocated
                            else uuid.uuid4().hex
                        )
                        versions = (
                            (
                                await session.execute(
                                    select(AgentWorkflowVersion.version).where(
                                        AgentWorkflowVersion.owner_username == username,
                                        AgentWorkflowVersion.asset_id == identity,
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        version = max([*versions, *[v for a, v in allocated], 0]) + 1
                        asset_map[key] = (identity, version)
                    if await session.get(
                        AgentAssetTombstone, ("workflow", asset_map[key][0])
                    ):
                        raise RoleAssetError(
                            "The previously imported local asset has been deleted",
                            status=409,
                        )
                for key in sorted(contents["used_roles"]):
                    source, local = contents["roles"][key], normalized_roles[key]
                    existing = known_roles.get(key)
                    if existing:
                        if (
                            existing["source"] != source
                            or existing["local_definition"] != local["definition"]
                            or existing["local_installation"] != local["installation"]
                        ):
                            raise RoleAssetError(
                                "Same role identity and version have different content or resource selections",
                                status=409,
                            )
                        identity, role_version = (
                            existing["local_id"],
                            existing["local_version"],
                        )
                        saved = await session.get(
                            AgentRoleVersion, (identity, role_version)
                        )
                        installation = await session.get(
                            AgentRoleInstallation, (identity, role_version)
                        )
                        if (
                            not saved
                            or saved.owner_username != username
                            or saved.definition != local["definition"]
                            or not installation
                            or installation.installation != local["installation"]
                        ):
                            raise RoleAssetError(
                                "Installed role no longer matches its fixed receipt",
                                status=409,
                            )
                    else:
                        previous_roles = [
                            entry
                            for source_key, entry in known_roles.items()
                            if source_key[0] == key[0]
                        ]
                        identity = (
                            previous_roles[0]["local_id"]
                            if previous_roles
                            else uuid.uuid4().hex
                        )
                        versions = (
                            (
                                await session.execute(
                                    select(AgentRoleVersion.version).where(
                                        AgentRoleVersion.owner_username == username,
                                        AgentRoleVersion.role_id == identity,
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        role_version = (
                            max(
                                [
                                    *versions,
                                    *[
                                        entry["local_version"]
                                        for entry in previous_roles
                                    ],
                                    0,
                                ]
                            )
                            + 1
                        )
                        session.add(
                            AgentRoleVersion(
                                role_id=identity,
                                version=role_version,
                                owner_username=username,
                                definition=local["definition"],
                            )
                        )
                        session.add(
                            AgentRoleInstallation(
                                role_id=identity,
                                version=role_version,
                                installation=local["installation"],
                            )
                        )
                        known_roles[key] = {
                            "source": source,
                            "local_id": identity,
                            "local_version": role_version,
                            "local_definition": local["definition"],
                            "local_installation": local["installation"],
                        }
                    if await session.get(AgentAssetTombstone, ("role", identity)):
                        raise RoleAssetError(
                            "The previously imported local role has been deleted",
                            status=409,
                        )
                    role_map[key] = (identity, role_version)
                for key in sorted(contents["included"]):
                    if key in records:
                        continue
                    definition = copy.deepcopy(contents["assets"][key])
                    self._rewrite_package_config(definition, choices)
                    members = (
                        [definition["coordinator"], *definition["allowed_roles"]]
                        if definition.get("kind") == "autonomous"
                        else definition["tasks"]
                    )
                    for member in members:
                        if member.get("role_id"):
                            member["role_id"], member["role_version"] = role_map[
                                (member["role_id"], member["role_version"])
                            ]
                        if member.get("knowledge_base_id") or member.get(
                            "decision_provider_id"
                        ):
                            self._rewrite_package_config(member, choices)
                        for field in ("role", "installation", "overrides"):
                            if member.get(field) is not None:
                                self._rewrite_package_config(member[field], choices)
                    refs = [
                        *definition.get("allowed_workflows", []),
                        *[
                            m.get("team") or m.get("workflow")
                            for m in members
                            if m.get("team") or m.get("workflow")
                        ],
                    ]
                    for ref in refs:
                        ref["asset_id"], ref["version"] = asset_map[
                            (ref["asset_id"], ref["version"])
                        ]
                    identity, version = asset_map[key]
                    session.add(
                        AgentWorkflowVersion(
                            asset_id=identity,
                            version=version,
                            owner_username=username,
                            definition=definition,
                        )
                    )
                    closure = self._package_contents(package, key)
                    role_records = [
                        known_roles[item] for item in sorted(closure["used_roles"])
                    ]
                    session.add(
                        AgentWorkflowPackage(
                            owner_username=username,
                            package_id=key[0],
                            version=key[1],
                            asset_id=identity,
                            installed_version=version,
                            digest=WorkflowPackage.content_digest(manifests[key]),
                            package={
                                "format_version": 4
                                if any(
                                    task.get("component")
                                    for item in closure["assets"].values()
                                    for task in item.get("tasks", [])
                                )
                                else 3,
                                "manifest": manifests[key],
                                "role_records": role_records,
                            },
                            role_ids={
                                json.dumps([item[0], item[1]]): role_map[item]
                                for item in closure["used_roles"]
                            },
                        )
                    )
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise RoleAssetError(
                    "Concurrent installation conflicted; preview again", status=409
                ) from exc
            except BaseException:
                await session.rollback()
                raise
        identity, version = asset_map[root]
        return {
            "asset": await self.get_asset(username, identity, version),
            "existing": root in records,
            "ready": True,
        }

    async def set_api_access(
        self, username: str, asset_id: str, key_id: str, version: int | None
    ) -> None:
        """Grant an API key one fixed workflow version, or revoke its grant.

        Args:
            username: Dashboard owner changing the grant.
            asset_id: Owned workflow asset.
            key_id: Public identifier of the API key to authorize.
            version: Exact published version, or None to revoke.

        Raises:
            RoleAssetError: The key, asset or version is ineligible for a grant.
        """
        asset = await self.get_asset(username, asset_id, version)
        if version is not None:
            self.chat.require_external_definition(asset["definition"])
        async with self.db.get_db() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            if version is None:
                grant = await session.get(AgentWorkflowApiAccess, (asset_id, key_id))
                if grant is not None and grant.owner_username == username:
                    await session.delete(grant)
            else:
                if await session.get(AgentAssetTombstone, ("workflow", asset_id)):
                    raise RoleAssetError("Asset has been deleted", status=409)
                key = (
                    await session.execute(select(ApiKey).where(ApiKey.key_id == key_id))
                ).scalar_one_or_none()
                if key is None or not {WORKFLOW_API_SCOPE, "*"}.intersection(
                    key.scopes or []
                ):
                    raise RoleAssetError(
                        "API key with workflow scope not found", status=404
                    )
                await session.merge(
                    AgentWorkflowApiAccess(
                        asset_id=asset_id,
                        key_id=key_id,
                        owner_username=username,
                        version=version,
                    )
                )
            await session.commit()

    @staticmethod
    async def require_api_access(
        session: AsyncSession, key_id: str, asset_id: str, version: int
    ) -> AgentWorkflowApiAccess:
        """Resolve the owner from an active key and its exact workflow grant.

        Args:
            session: Session used for the launch or result authorization.
            key_id: Authenticated API key ID.
            asset_id: Requested workflow asset.
            version: Exact version to launch or inspect.

        Returns:
            Current grant and its owner.

        Raises:
            RoleAssetError: The key or grant is no longer authorized.
        """
        key = (
            await session.execute(
                select(ApiKey).where(
                    ApiKey.key_id == key_id,
                    col(ApiKey.revoked_at).is_(None),
                    or_(
                        col(ApiKey.expires_at).is_(None),
                        col(ApiKey.expires_at) > datetime.now(timezone.utc),
                    ),
                )
            )
        ).scalar_one_or_none()
        if key is None:
            raise RoleAssetError("Invalid API key", status=401)
        if not {WORKFLOW_API_SCOPE, "*"}.intersection(key.scopes or []):
            raise RoleAssetError("Insufficient API key scope", status=403)
        grant = await session.get(AgentWorkflowApiAccess, (asset_id, key_id))
        if grant is None or grant.version != version:
            raise RoleAssetError(
                "Workflow version is not available to this API key", status=404
            )
        return grant

    async def _require_api_run_access(
        self, session: AsyncSession, run: AgentWorkflowRun
    ) -> None:
        """Recheck an API grant for a root or its admitted execution scopes.

        Args:
            session: Current read or execution admission transaction.
            run: Persisted run whose root holds the original grant identity.

        Raises:
            RoleAssetError: The original key no longer has the root's grant.
        """
        root_id = _root_run_id(run)
        root = (
            run
            if run.run_id == root_id
            else await session.get(AgentWorkflowRun, root_id)
        )
        if root is None or root.owner_username != run.owner_username:
            raise RoleAssetError("Run not found", status=404)
        key_id = root.snapshot.get("api_key_id")
        if key_id is not None:
            grant = await self.require_api_access(
                session,
                key_id,
                root.snapshot["asset_id"],
                root.snapshot["asset_version"],
            )
            if grant.owner_username != root.owner_username:
                raise RoleAssetError(
                    "Workflow version is not available to this API key", status=404
                )

    async def get_api_run(self, key_id: str, run_id: str) -> dict:
        """Project only the originating API key's authorized root result.

        Args:
            key_id: Authenticated API key ID.
            run_id: Root identity returned by the API launch.

        Returns:
            Fixed version, status and published result after completion.

        Raises:
            RoleAssetError: The key cannot access this run.
        """
        async with self.db.get_db() as session:
            await session.execute(text("BEGIN"))
            run = await session.get(AgentWorkflowRun, run_id)
            if (
                run is None
                or run.snapshot.get("root_run_id") != run_id
                or run.snapshot.get("parent_run_id") is not None
                or run.snapshot.get("api_key_id") != key_id
            ):
                raise RoleAssetError("Run not found", status=404)
            asset_id = run.snapshot["asset_id"]
            version = run.snapshot["asset_version"]
            await self._require_api_run_access(session, run)
            return {
                "run_id": run_id,
                "asset_id": asset_id,
                "asset_version": version,
                "status": run.status,
                "result": run.result if run.status == "completed" else None,
            }

    async def start_run(
        self,
        username: str,
        asset_id: str,
        payload: dict,
        *,
        chat: dict | None = None,
        api_key_id: str | None = None,
        rerun_key: tuple[str, str] | None = None,
        replay_only: bool = False,
    ) -> dict:
        """Persist all execution identities and the effective snapshot before dispatch.

        Args:
            username: Authenticated asset owner retaining Dashboard control.
            asset_id: Saved asset identity.
            payload: Pinned version, declared inputs, source and request ID.
            chat: Trusted platform identity verified by the explicit chat entry.
            api_key_id: Authenticated API key identity, when invoked by the API.
            rerun_key: Completed source run and request ID for an authorized rerun.
            replay_only: Look up a committed Dashboard request without admitting new work.

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
        if chat is not None:
            if (
                chat["asset_id"] != asset_id
                or chat["version"] != request.version
                or chat["caller"]["source"] != request.source
            ):
                raise RoleAssetError(
                    "Chat invocation does not match its grant", status=403
                )
        launch = None
        if request.request_id is not None:
            if chat is not None:
                caller = chat["caller"]
                principal = [
                    "platform",
                    caller["platform_id"],
                    caller["source"],
                    caller["sender_id"],
                ]
            elif api_key_id is not None:
                principal = ["api", api_key_id]
            else:
                principal = ["dashboard", username]
            launch = WorkflowLaunchIdentity(
                principal=json.dumps(principal, separators=(",", ":")),
                request_id=request.request_id,
            )
        prior = None
        previous = None
        external = chat is not None or api_key_id is not None
        if launch is not None or external:
            async with self.db.get_db() as session:
                previous = await self._find_launch(
                    session,
                    username,
                    asset_id,
                    request.version,
                    launch,
                    chat,
                    api_key_id,
                )
                if previous is not None:
                    prior = await session.get(AgentWorkflowRun, previous.run_id)
                    if prior is None or prior.owner_username != username:
                        raise RoleAssetError("Run not found", status=404)
                    if (
                        chat is not None
                        and prior.snapshot.get("chat", {}).get("caller")
                        != chat["caller"]
                    ):
                        raise RoleAssetError(
                            "Launch request's original platform source authorization changed",
                            status=403,
                        )
                    if (
                        prior.snapshot["asset_id"],
                        prior.snapshot["asset_version"],
                    ) != (asset_id, request.version):
                        raise WorkflowLaunchConflict()
        if replay_only and prior is None:
            raise RoleAssetError(
                "Launch request has no committed run",
                [
                    {
                        "path": "request_id",
                        "code": "LAUNCH_NOT_COMMITTED",
                        "message": "No committed run",
                    }
                ],
                status=404,
            )
        asset = (
            prior.snapshot
            if prior is not None
            else await self.get_asset(username, asset_id, request.version)
        )
        definition = asset["definition"]
        structured = definition.get("port_contract_version") == 2
        input_error = None
        if structured:
            try:
                request.inputs = normalize_port_inputs(
                    definition["input_contract"], request.inputs, "inputs"
                )
            except RoleAssetError as exc:
                input_error = exc
        if launch is not None:
            launch.fingerprint = hashlib.sha256(
                json.dumps(
                    [
                        username,
                        asset_id,
                        chat["caller"]["profile_id"] if chat is not None else None,
                        request.model_dump(exclude={"request_id"}),
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode()
            ).hexdigest()
        if prior is not None:
            assert previous is not None and launch is not None
            if input_error is not None or previous.fingerprint != launch.fingerprint:
                raise WorkflowLaunchConflict()
            await self.advance_run(username, prior.run_id)
            return await self.get_run(username, prior.run_id)
        if input_error is not None:
            raise input_error

        input_refs = [ref.model_dump() for ref in request.input_artifacts]
        if external and input_refs:
            raise RoleAssetError(
                "Historical reference inputs require Dashboard authorization",
                status=403,
            )
        if input_refs and definition.get("kind") != "autonomous":
            raise RoleAssetError(
                "Workflow graphs require explicit node inputs",
                [
                    {
                        "path": "input_artifacts",
                        "code": "UNSUPPORTED",
                        "message": "Select upstream fields in the workflow editor; full artifact inputs are only available to autonomous teams",
                    }
                ],
            )
        for ref in input_refs:
            await self.read_artifact(username, **ref)
        if structured:
            if external and any(
                definition["input_contract"]["properties"][name].get("format")
                in REFERENCE_OUTPUTS.values()
                for name in request.inputs
            ):
                raise RoleAssetError(
                    "Historical reference inputs require Dashboard authorization",
                    status=403,
                )
        else:
            required = definition["required_inputs"]
            fields = [
                {
                    "path": f"inputs.{name}",
                    "code": "REQUIRED",
                    "message": "Provide non-empty text",
                }
                for name in required
                if not isinstance(request.inputs.get(name), str)
                or not request.inputs[name].strip()
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
        if "tasks" in definition or definition.get("kind") == "autonomous":
            return await self._start_graph(
                username,
                asset,
                request,
                chat=chat,
                api_key_id=api_key_id,
                launch=launch,
                rerun_key=rerun_key,
            )
        raise RoleAssetError("Legacy workflow definitions are not supported")

    async def _find_launch(
        self,
        session: AsyncSession,
        username: str,
        asset_id: str,
        version: int,
        launch: WorkflowLaunchIdentity | None,
        chat: dict | None,
        api_key_id: str | None,
    ) -> AgentWorkflowLaunchReceipt | None:
        """Check source authorization and the durable request before dispatch.

        Args:
            session: Session for the preliminary read or final write transaction.
            username: Owner of the pinned workflow version.
            asset_id: Pinned asset identity.
            version: Exact published version.
            launch: Candidate receipt for the verified source and request ID.
            chat: Exact platform caller and grant, when used.
            api_key_id: Validated API key ID, when used.

        Returns:
            Existing receipt if this request already committed.

        Raises:
            RoleAssetError: Source authorization changed or the payload conflicts.
        """
        if chat is not None:
            await self.chat.require_access(chat, username, _session=session)
        if api_key_id is not None:
            grant = await self.require_api_access(
                session, api_key_id, asset_id, version
            )
            if grant.owner_username != username:
                raise RoleAssetError(
                    "Workflow version is not available to this API key", status=404
                )
        if launch is None:
            return None
        receipt = await session.get(
            AgentWorkflowLaunchReceipt, (launch.principal, launch.request_id)
        )
        if (
            receipt is not None
            and launch.fingerprint
            and receipt.fingerprint != launch.fingerprint
        ):
            raise WorkflowLaunchConflict()
        return receipt

    async def rerun(
        self, username: str, run_id: str, request: WorkflowRerunRequest
    ) -> dict:
        """Start a new Dashboard run from a completed published root.

        Args:
            username: Current authenticated resource owner.
            run_id: Completed root whose frozen launch inputs should be reused.
            request: Idempotency identity for one explicit launch.

        Returns:
            The new run associated with the request.

        Raises:
            RoleAssetError: The source is inaccessible or cannot be started again.
        """
        rerun_key = (run_id, request.request_id)
        async with self.db.get_db() as session:
            source = await session.get(AgentWorkflowRun, run_id)
            if source is None or source.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            snapshot = source.snapshot
            if (
                source.status != "completed"
                or snapshot.get("parent_run_id")
                or snapshot.get("trial")
                or snapshot.get("debug_fork")
                or snapshot.get("engine") != "graph_v2"
            ):
                raise RoleAssetError(
                    "Only completed published root runs can be started again",
                    status=409,
                )
            receipt = await session.get(AgentWorkflowRerun, rerun_key)
        if receipt is not None:
            # The first request may have committed without reaching the scheduler.
            await self.advance_run(username, receipt.run_id)
            return await self.get_run(username, receipt.run_id)
        return await self.start_run(
            username,
            snapshot["asset_id"],
            {
                "version": snapshot["asset_version"],
                "inputs": snapshot["inputs"],
                "input_artifacts": snapshot.get("input_artifacts", []),
            },
            rerun_key=rerun_key,
        )

    async def _freeze_member(
        self,
        username: str,
        definition: dict,
        *,
        asset: dict,
        role_path: str = "definition.role_id",
    ) -> dict:
        """Freeze role capabilities and create an isolated member identity.

        Args:
            username: Actual initiating owner.
            definition: Task with a pinned role and explicit overrides.
            asset: Authorized fixed asset supplying the actual contract context.
            role_path: Role reference field in the selected asset definition.

        Returns:
            Frozen role, effective settings, safe profile, persona and member.

        Raises:
            RoleAssetError: Bound resources are missing or incompatible.
        """
        if definition.get("role") is not None:
            role = {
                "role_id": None,
                "version": None,
                "definition": definition["role"],
                "installation": definition["installation"],
            }
            overrides = {}
        else:
            role = await self.roles.get_role(
                username, definition["role_id"], definition["role_version"]
            )
            overrides = definition.get("overrides", {}).copy()
        default_provider = asset["definition"].get("default_provider_id")
        preview = await self.roles.preview_role(role, overrides)
        uses_default_provider = (
            default_provider
            and preview["effective"]["runner"]["type"] == "local"
            and not overrides.get("provider_id", role["definition"].get("provider_id"))
        )
        if uses_default_provider:
            overrides["provider_id"] = default_provider
            preview = await self.roles.preview_role(role, overrides)
            preview["sources"]["provider_id"] = "workflow:default_provider_id"
            for issue in preview["issues"]:
                if issue["path"] == "overrides.provider_id":
                    issue["path"] = "definition.default_provider_id"
        if asset["definition"].get("kind") == "autonomous" and role.get("role_id"):
            # A local override can reduce, but cannot increase, the template quota.
            base_preview = await self.roles.preview_role(role)
            for field in ("max_instances", "max_parallel"):
                template_limit = base_preview["effective"].get(field)
                effective_limit = preview["effective"].get(field)
                if template_limit is not None and (
                    effective_limit is None or effective_limit > template_limit
                ):
                    preview["effective"][field] = template_limit
                    preview["sources"][field] = base_preview["sources"][field]
        role.update(preview)
        if not role["ready"]:
            raise RoleAssetError("Role resources are not ready", role["issues"])
        issues = get_agent_runner_capability_issues(
            role["effective"]["runner"], {role_path: "task_result"}
        )
        if issues:
            raise RoleAssetError("Task runner cannot submit results", issues)
        if role["effective"]["runner"]["type"] != "local":
            for limit in ("token_budget", "max_output_tokens"):
                if (definition.get("limits") or {}).get(limit) is not None:
                    raise RoleAssetError(
                        "Remote executor cannot enforce this model limit",
                        [
                            {
                                "path": "definition.limits." + limit,
                                "code": "INCOMPATIBLE",
                                "message": f"{role['effective']['runner']['label']} does not provide model-level {limit} enforcement",
                            }
                        ],
                    )
        profile_id = role["installation"]["profile_id"]
        core = self.roles.core_lifecycle
        profile = core.astrbot_config_mgr.confs[profile_id] if core else {}
        # Credentials stay in the local provider manager. Freeze the pipeline's
        # behavior settings, never provider/platform definitions or dashboard auth.
        profile_snapshot = copy.deepcopy(
            {
                key: value
                for key, value in profile.items()
                if key not in {"provider", "provider_sources", "platform", "dashboard"}
            }
        )
        if profile_snapshot.get("agent_runner", {}).get("runner_type") != "local":
            for credential in (
                "dify_api_key",
                "coze_api_key",
                "dashscope_api_key",
                "deerflow_api_key",
                "deerflow_auth_header",
            ):
                profile_snapshot.get("agent_runner", {}).get("config", {}).pop(
                    credential, None
                )
        effective = copy.deepcopy(role["effective"])
        catalog = await self.roles.get_resources()
        persona = next(
            (p for p in catalog["personas"] if p["id"] == effective.get("persona_id")),
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

    async def check_readiness(
        self, username: str, asset_id: str, version: int | None = None
    ) -> dict:
        """Check local capabilities using the exact same resolution as launch.

        Args:
            username: Authenticated asset owner.
            asset_id: Saved team or workflow identity.
            version: Exact version, or latest when omitted.

        Returns:
            Readiness, field diagnostics and effective runner information.
            No run, member conversation or external operation is created.

        Raises:
            RoleAssetError: The saved asset is inaccessible.
        """
        asset = await self.get_asset(username, asset_id, version)
        definition = asset["definition"]
        try:
            if definition.get("kind") == "autonomous" or "tasks" in definition:
                frozen = await self._freeze_graph(
                    username,
                    asset,
                    WorkflowStartRequest(
                        version=asset["version"],
                        inputs={
                            name: "{" + name + "}"
                            for name in definition.get("required_inputs", [])
                        },
                    ),
                )
                members = [
                    *frozen["members"].values(),
                    *frozen.get("allowed_roles", {}).values(),
                ]
            else:
                raise RoleAssetError("Legacy workflow definitions are not supported")
        except RoleAssetError as exc:
            return {
                "ready": False,
                "issues": exc.fields
                or [
                    {
                        "path": "definition",
                        "code": "INCOMPATIBLE",
                        "message": str(exc),
                    }
                ],
                "roles": [],
            }
        return {
            "ready": True,
            "issues": [],
            "roles": [
                {
                    "role_id": member["role"]["role_id"],
                    "version": member["role"]["version"],
                    "name": member["role"]["definition"]["name"],
                    "runner": member["effective"]["runner"],
                }
                for member in members
            ],
        }

    async def _inspection_attempt(
        self,
        session: AsyncSession,
        username: str,
        run_id: str,
        scope_run_id: str,
        task_run_id: str,
        attempt_id: str | None,
    ) -> tuple[AgentWorkflowRun, AgentWorkflowRun, dict]:
        """Resolve an attempt in exactly one authorized root, scope and task.

        Args:
            session: Transaction shared by the caller's observation or admission.
            username: Authenticated Dashboard owner.
            run_id: Root run containing the invocation.
            scope_run_id: Exact workflow invocation.
            task_run_id: Exact task instance, including any round.
            attempt_id: Archived or current attempt, defaulting to the current one.

        Returns:
            Frozen scope, task instance, and the selected attempt's saved facts.

        Raises:
            RoleAssetError: The root, scope, task, or attempt is inaccessible.
        """
        root = await session.get(AgentWorkflowRun, run_id)
        scope = await session.get(AgentWorkflowRun, scope_run_id)
        task = await session.get(AgentWorkflowRun, task_run_id)
        if (
            root is None
            or scope is None
            or task is None
            or any(row.owner_username != username for row in (root, scope, task))
            or root.snapshot.get("root_run_id") != run_id
            or scope.snapshot.get("root_run_id") != run_id
            or task.snapshot.get("parent_run_id") != scope_run_id
            or task.snapshot.get("root_run_id") != run_id
            or scope.snapshot.get("engine") != "graph_v2"
        ):
            raise RoleAssetError("Workflow task not found", status=404)

        current_id = task.state["attempt_id"]
        if not attempt_id or attempt_id == current_id:
            return (
                scope,
                task,
                {
                    "attempt_id": current_id,
                    "snapshot": task.snapshot,
                    "state": task.state,
                    "status": task.status,
                    "result": task.result,
                },
            )
        attempt = await session.get(AgentWorkflowAttempt, attempt_id)
        if attempt is None or attempt.run_id != task_run_id:
            raise RoleAssetError("Attempt not found", status=404)
        return scope, task, attempt.data

    async def failed_child_debug_attempts(
        self, username: str, run_id: str, request: DebugForkAttempt
    ) -> list[dict]:
        """Find failed attempts recorded inside one fixed child call.

        Args:
            username: Authenticated Dashboard owner.
            run_id: Root run containing the call.
            request: Exact parent scope, call task and attempt.

        Returns:
            Failed attempts in the child scope, with replayable attempts first.

        Raises:
            RoleAssetError: The call is inaccessible or has no failed child scope.
        """
        async with self.db.get_db() as session:
            if self.db.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN"))
            _, caller, attempt = await self._inspection_attempt(
                session,
                username,
                run_id,
                request.scope_run_id,
                request.task_run_id,
                request.attempt_id,
            )
            child_id = (attempt.get("state") or {}).get("child_scope_run_id")
            if (
                caller.snapshot.get("engine") != "workflow_call_v2"
                or attempt["status"] not in {"blocked", "failed"}
                or not child_id
            ):
                raise RoleAssetError("Select a failed fixed workflow call", status=409)
            child = await session.get(AgentWorkflowRun, child_id)
            if (
                child is None
                or child.owner_username != username
                or child.snapshot.get("root_run_id") != run_id
                or child.snapshot.get("parent_run_id") != caller.run_id
                or child.snapshot.get("engine") != "graph_v2"
            ):
                raise RoleAssetError("Child workflow run not found", status=404)

            tasks = {
                task.run_id: task
                for task in (
                    await session.execute(
                        select(AgentWorkflowRun)
                        .where(
                            AgentWorkflowRun.owner_username == username,
                            AgentWorkflowRun.snapshot["root_run_id"].as_string()
                            == run_id,
                            AgentWorkflowRun.snapshot["parent_run_id"].as_string()
                            == child_id,
                        )
                        .order_by(
                            AgentWorkflowRun.snapshot["iteration"].as_integer(),
                            AgentWorkflowRun.snapshot["node_id"].as_string(),
                        )
                    )
                ).scalars()
            }
            archived = (
                await session.execute(
                    select(
                        AgentWorkflowAttempt.run_id,
                        AgentWorkflowAttempt.attempt_id,
                        AgentWorkflowAttempt.data["snapshot"],
                    )
                    .where(
                        col(AgentWorkflowAttempt.run_id).in_(tasks),
                        AgentWorkflowAttempt.data["status"].as_string() == "failed",
                    )
                    .order_by(col(AgentWorkflowAttempt.attempt_id))
                )
            ).all()
            nodes = {node["id"]: node for node in child.snapshot["definition"]["tasks"]}
            replayable, other = [], []
            for task_id, attempt_id, snapshot in [
                *(
                    (task.run_id, task.state["attempt_id"], task.snapshot)
                    for task in tasks.values()
                    if task.status == "failed"
                ),
                *archived,
            ]:
                task_snapshot = tasks[task_id].snapshot
                node = nodes.get(task_snapshot["node_id"], {})
                destination = (
                    replayable
                    if task_snapshot.get("engine") == "transform_task_v2"
                    and node.get("transform") in DEBUG_FORK_TRANSFORMS
                    and _debug_fork_reads(node, snapshot or {}) is not None
                    else other
                )
                destination.append(
                    {
                        "scope_run_id": child_id,
                        "task_run_id": task_id,
                        "attempt_id": attempt_id,
                        "node_name": task_snapshot["definition"]["name"],
                        "iteration": task_snapshot.get("iteration"),
                    }
                )
            return [*replayable, *other]

    async def _historical_reference_status(
        self,
        session: AsyncSession,
        username: str,
        value: JsonValue,
        reference_format: str,
        path: str,
    ) -> Literal["missing", "unavailable"] | None:
        """Check whether a historical port has an exact, accessible reference.

        Args:
            session: Current inspection transaction.
            username: Owner requesting the history.
            value: Recorded or upstream candidate.
            reference_format: Managed reference format of the port.
            path: Diagnostic path of the input.

        Returns:
            A status for incomplete or inaccessible evidence, or None if usable.
        """
        try:
            reference = PORT_FORMATS[reference_format].model_validate(value)
        except ValidationError:
            return "missing"
        try:
            await self._require_port_reference(
                session, username, reference.model_dump(), path
            )
        except RoleAssetError:
            # Ownership failures and nonexistent versions have the same public response.
            return "unavailable"
        return None

    async def _debug_fork_plan(
        self,
        session: AsyncSession,
        username: str,
        run_id: str,
        request: DebugForkAttempt,
    ) -> tuple[dict, dict]:
        """Project a frozen node and its exact recorded input boundary.

        Args:
            session: Transaction containing the frozen source attempt.
            username: Authenticated Dashboard owner.
            run_id: Root run containing the source.
            request: Scope, task and attempt selected in the inspector.

        Returns:
            Persistable comparison data and the isolated single-node definition.

        Raises:
            RoleAssetError: The node cannot safely run or lacks replayable evidence.
        """
        scope, task, attempt = await self._inspection_attempt(
            session,
            username,
            run_id,
            request.scope_run_id,
            request.task_run_id,
            request.attempt_id,
        )
        definition = scope.snapshot["definition"]
        node = next(
            (
                item
                for item in definition["tasks"]
                if item["id"] == task.snapshot["node_id"]
            ),
            None,
        )
        if node is None or not (
            node.get("transform") in DEBUG_FORK_TRANSFORMS
            or task.snapshot.get("engine") in {"plugin_task_v2", "single_task_v2"}
            and task.snapshot.get("graph_task")
            and definition.get("port_contract_version") == 2
        ):
            raise RoleAssetError(
                "This node has no supported debug execution boundary",
                status=409,
            )
        if attempt["status"] == "skipped":
            raise RoleAssetError(
                "This attempt was skipped and did not read its inputs. Select a completed or failed attempt.",
                status=409,
            )
        if attempt["status"] not in {"completed", "failed"}:
            raise RoleAssetError("Select a completed or failed attempt", status=409)
        snapshot = attempt.get("snapshot") or {}
        values = snapshot.get("inputs") or {}
        sources = snapshot.get("port_sources") or {}
        recorded_reads = snapshot.get("input_reads")
        legacy = recorded_reads is None
        # An exact producer and field can establish an individual read when its marker is missing.
        reads = {
            name: evidence
            for name, evidence in sources.items()
            if evidence.get("artifact") and evidence.get("field")
        }
        reads.update(recorded_reads or {})
        bound = (
            node.get("inputs", {}).keys() | snapshot.get("iteration_inputs", {}).keys()
        )

        contract = task_input_contract(
            node.get("input_contract"), node.get("transform")
        )
        if contract is None:
            # Agent bindings without a declared contract consume text values.
            contract = {
                "type": "object",
                "properties": {name: {"type": "string"} for name in reads},
            }
        inputs = []
        for name, schema in contract["properties"].items():
            reference_format = schema.get("format")
            managed = reference_format in REFERENCE_OUTPUTS.values()
            port = {
                "name": name,
                "kind": "reference"
                if managed
                else "text"
                if schema.get("type") == "string"
                else "json",
                "source": "absent",
                "changed": False,
            }
            if managed:
                port["format"] = reference_format
            if name in reads and name in values:
                value = values[name]
                if managed:
                    port["source"] = (
                        await self._historical_reference_status(
                            session, username, value, reference_format, f"inputs.{name}"
                        )
                        or "absent"
                    )
                evidence = {**(reads[name] or {}), **sources.get(name, {})}
                if (
                    "port_sources" not in snapshot
                    and (artifact := evidence.get("artifact"))
                    and artifact["version"] == 1
                ):
                    binding = snapshot.get("iteration_inputs", {}).get(
                        name
                    ) or node.get("inputs", {}).get(name, {})
                    if binding.get("field") in snapshot.get("selected_fields", {}).get(
                        artifact["artifact_id"], []
                    ):
                        evidence["field"] = binding["field"]
                if port["source"] == "absent" and (ref := evidence.get("artifact")):
                    try:
                        await self._artifact_source(session, username, ref)
                    except RoleAssetError:
                        port["source"] = "unavailable"
                if port["source"] == "absent":
                    port.update(
                        source="recorded",
                        original=value,
                        value=value,
                        selection="reused",
                        evidence=evidence,
                    )
            elif name in reads or (legacy and name in bound):
                port["source"] = "missing"
            elif "default" in schema and (
                name not in values or values[name] == schema["default"]
            ):
                port.update(
                    source="default",
                    original=schema["default"],
                    value=schema["default"],
                    selection="reused",
                )
            elif name in values or port_requires_value(contract, name):
                port["source"] = "missing"
            inputs.append(port)

        parent_call = None
        if attempt["status"] == "failed" and (
            caller_id := scope.snapshot.get("parent_run_id")
        ):
            caller = await session.get(AgentWorkflowRun, caller_id)
            if (
                caller is None
                or caller.owner_username != username
                or caller.snapshot.get("root_run_id") != run_id
            ):
                raise RoleAssetError("Parent call not found", status=404)
            if caller.snapshot.get("engine") == "workflow_call_v2":
                # A retry can replace the caller's current child. Resolve the
                # attempt that actually created the selected historical scope.
                caller_attempt = caller.state["attempt_id"]
                if caller.state.get("child_scope_run_id") != scope.run_id:
                    caller_attempt = await session.scalar(
                        select(AgentWorkflowAttempt.attempt_id).where(
                            AgentWorkflowAttempt.run_id == caller_id,
                            AgentWorkflowAttempt.data["state"][
                                "child_scope_run_id"
                            ].as_string()
                            == scope.run_id,
                        )
                    )
                if caller_attempt:
                    await self._inspection_attempt(
                        session,
                        username,
                        run_id,
                        caller.snapshot["parent_run_id"],
                        caller_id,
                        caller_attempt,
                    )
                    parent_call = {
                        "scope_run_id": caller.snapshot["parent_run_id"],
                        "task_run_id": caller_id,
                        "attempt_id": caller_attempt,
                        "node_name": caller.snapshot["definition"]["name"],
                        "iteration": caller.snapshot.get("iteration"),
                    }

        state = attempt.get("state") or {}
        plan = {
            "source": {
                "root_run_id": run_id,
                "scope_run_id": request.scope_run_id,
                "task_run_id": request.task_run_id,
                "attempt_id": request.attempt_id,
                "asset_id": scope.snapshot["asset_id"],
                "asset_version": scope.snapshot["asset_version"],
                **(
                    {"draft_revision": scope.snapshot["trial"]["draft_revision"]}
                    if scope.snapshot.get("trial")
                    else {}
                ),
                "workflow_name": definition["name"],
                "node_id": node["id"],
                "node_name": node["name"],
                "iteration": snapshot.get("iteration"),
                **({"parent_call": parent_call} if parent_call else {}),
            },
            "original": {
                "status": attempt["status"],
                "reason": state.get("reason"),
                "result": attempt.get("result"),
            },
            "inputs": inputs,
        }
        # Boundary bindings read the saved consumer values without scheduling producers.
        isolated = {
            "name": definition["name"],
            "port_contract_version": definition["port_contract_version"],
            "default_provider_id": definition.get("default_provider_id"),
            "input_contract": contract,
            "limits": definition.get("limits"),
            "entry_task_ids": [node["id"]],
            "result_task_id": node["id"],
            "tasks": [
                {
                    **node,
                    "inputs": {
                        name: {"input": name} for name in contract["properties"]
                    },
                }
            ],
        }
        plan["preview_token"] = hashlib.sha256(
            json.dumps([plan, isolated], ensure_ascii=True).encode()
        ).hexdigest()
        return plan, isolated

    async def preview_debug_fork(
        self, username: str, run_id: str, request: DebugForkAttempt
    ) -> dict:
        """Read a single-node plan without starting or modifying a run.

        Args:
            username: Authenticated Dashboard owner.
            run_id: Root run containing the source.
            request: Exact scope, task and attempt.

        Returns:
            The original definition's identity, recorded inputs and result.

        Raises:
            RoleAssetError: The attempt is inaccessible or cannot be replayed.
        """
        async with self.db.get_db() as session:
            if self.db.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN"))
            plan, _ = await self._debug_fork_plan(session, username, run_id, request)
            return plan

    async def start_debug_fork(
        self, username: str, run_id: str, request: DebugForkRequest
    ) -> dict:
        """Commit a single-node debug fork through the ordinary scheduler.

        Args:
            username: Authenticated Dashboard owner.
            run_id: Root run containing the source.
            request: Confirmed preview, request identity and optional replacement.

        Returns:
            The independent persisted root run.

        Raises:
            RoleAssetError: The confirmation, evidence, or replacement is invalid.
        """
        key = (f"debug-fork:{run_id}", request.request_id)
        try:
            serialized = json.dumps(
                request.model_dump(
                    exclude={"replacements"} if not request.replacements else None
                ),
                ensure_ascii=True,
                allow_nan=False,
            )
        except ValueError as exc:
            raise RoleAssetError(
                "Use finite JSON numbers in replacement values"
            ) from exc
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        async with self.db.get_db() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            root = await session.get(AgentWorkflowRun, run_id)
            if root is None or root.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            receipt = await session.get(AgentWorkflowLaunchReceipt, key)
            if receipt is not None:
                if receipt.fingerprint != fingerprint:
                    raise WorkflowLaunchConflict()
                fork_id = receipt.run_id
            else:
                plan, definition = await self._debug_fork_plan(
                    session, username, run_id, request
                )
                if plan.pop("preview_token") != request.preview_token:
                    raise RoleAssetError(
                        "Debug fork preview has changed. Reopen the preview to check the current input sources.",
                        status=409,
                    )
                contract = definition["input_contract"]
                ports = {port["name"]: port for port in plan["inputs"]}
                replacements = list(request.replacements)
                if request.replacement is not None:
                    replacements.append(request.replacement)
                selected = set()
                for replacement in replacements:
                    name = replacement.name
                    port = ports.get(name)
                    if port is None or name in selected:
                        raise RoleAssetError("Choose each declared input at most once")
                    selected.add(name)
                    normalize_port_inputs(
                        {
                            "type": "object",
                            "properties": {name: contract["properties"][name]},
                        },
                        {name: replacement.value},
                        "inputs",
                    )
                    if port["kind"] == "reference":
                        await self._require_port_reference(
                            session, username, replacement.value, f"inputs.{name}"
                        )
                    port["changed"] = (
                        "original" not in port
                        or not Draft202012Validator(
                            {"const": port["original"]}
                        ).is_valid(replacement.value)
                    )
                    # Reuse the recorded representation as well as its JSON value.
                    port["value"] = (
                        replacement.value if port["changed"] else port["original"]
                    )
                    port["selection"] = (
                        "filled"
                        if "original" not in port
                        else "replaced"
                        if port["changed"]
                        else "reused"
                    )

                issues = [
                    {
                        "path": f"inputs.{name}",
                        "code": "REFERENCE"
                        if port["source"] == "unavailable"
                        else "REQUIRED",
                        "message": (
                            "The recorded reference is unavailable or you no longer have access. Select an accessible exact version."
                            if port["source"] == "unavailable"
                            else "The historical value is missing. Supply a test value or select an exact reference for this input."
                        ),
                    }
                    for name, port in ports.items()
                    if port["source"] in {"missing", "unavailable"}
                    and name not in selected
                ]
                if issues:
                    raise RoleAssetError(issues[0]["message"], issues)
                inputs = {
                    name: port["value"]
                    for name, port in ports.items()
                    if "value" in port
                    and (port["source"] != "default" or name in selected)
                }
                # With no edits, let the scheduler reproduce the original contract failure.
                if replacements:
                    normalize_port_inputs(contract, inputs, "inputs")
                source = plan["source"]
                frozen = await self._freeze_graph(
                    username,
                    {
                        "asset_id": source["asset_id"],
                        "version": source["asset_version"],
                        "definition": definition,
                    },
                    WorkflowStartRequest(
                        version=source["asset_version"] or 1, inputs=inputs
                    ),
                )
                if frozen["members"]:
                    scope = await session.get(AgentWorkflowRun, source["scope_run_id"])
                    assert scope is not None
                    (member_name,) = frozen["members"]
                    original_member = scope.snapshot.get("members", {}).get(member_name)
                    if original_member is None:
                        raise RoleAssetError(
                            "Original Agent resources were not recorded", status=409
                        )
                    await self.roles.validate_frozen_role(
                        original_member["role"], original_member["profile"]
                    )
                    frozen["members"][member_name] = original_member
                    for port in plan["inputs"]:
                        evidence = port.get("evidence", {})
                        if (
                            not port["changed"]
                            and evidence.get("field")
                            and evidence.get("artifact")
                        ):
                            await self._require_port_reference(
                                session,
                                username,
                                evidence["artifact"],
                                f"inputs.{port['name']}",
                            )
                frozen["debug_fork"] = plan
                fork_id = await self._create_scope(session, username, frozen)
                session.add(
                    AgentWorkflowLaunchReceipt(
                        principal=key[0],
                        request_id=key[1],
                        fingerprint=fingerprint,
                        run_id=fork_id,
                    )
                )
                await session.commit()
        await self.advance_run(username, fork_id)
        return await self.get_run(username, fork_id)

    async def inspect_data_line(
        self,
        username: str,
        run_id: str,
        scope_run_id: str,
        task_run_id: str,
        input_name: str,
        attempt_id: str | None,
    ) -> dict:
        """Read one bound input from a persisted consumer attempt.

        Args:
            username: Authenticated Dashboard owner.
            run_id: Root run containing the selected scope.
            scope_run_id: Exact workflow invocation shown on the canvas.
            task_run_id: Exact consumer instance, including its round.
            input_name: Named input from the frozen workflow definition.
            attempt_id: Selected attempt, or the current attempt if omitted.

        Returns:
            The observed read, its sources and a separately identified upstream value.

        Raises:
            RoleAssetError: An identity, scope, attempt or binding is unavailable.
        """
        async with self.db.get_db() as session:
            if self.db.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN"))
            scope, task, attempt = await self._inspection_attempt(
                session, username, run_id, scope_run_id, task_run_id, attempt_id
            )
            current_id = task.state["attempt_id"]
            attempt_data = col(AgentWorkflowAttempt.data)
            archived = {
                identity: (status, previous)
                for identity, status, previous in (
                    await session.execute(
                        select(
                            AgentWorkflowAttempt.attempt_id,
                            attempt_data["status"].as_string(),
                            attempt_data["state"]["previous_attempt_id"].as_string(),
                        ).where(AgentWorkflowAttempt.run_id == task_run_id)
                    )
                ).all()
            }
            attempts = [{"attempt_id": current_id, "status": task.status}]
            previous_id = task.state.get("previous_attempt_id")
            seen = {current_id}
            while previous_id in archived and previous_id not in seen:
                saved_status, older_id = archived[previous_id]
                attempts.append({"attempt_id": previous_id, "status": saved_status})
                seen.add(previous_id)
                previous_id = older_id
            attempt_id = attempt["attempt_id"]
            snapshot = attempt.get("snapshot") or {}
            state = attempt.get("state") or {}
            status = attempt["status"]

            definition = _run_definition(scope)
            node_id = task.snapshot.get("node_id")
            node = next(
                (item for item in definition["tasks"] if item["id"] == node_id), None
            )
            if node is None:
                raise RoleAssetError("Data line not found", status=404)
            loop_id = task.snapshot.get("loop_id")
            iteration = task.snapshot.get("iteration")
            loop = next(
                (item for item in definition.get("loops", []) if item["id"] == loop_id),
                None,
            )
            feedback = (
                next(
                    (
                        item
                        for item in (loop or {}).get("feedback", [])
                        if item["target"] == node_id and item["input"] == input_name
                    ),
                    None,
                )
                if iteration and iteration > 1
                else None
            )
            binding = node.get("inputs", {}).get(input_name) or {}
            source_id = feedback["source"] if feedback else binding.get("task_id")
            scope_input = None if feedback else binding.get("input")
            trial_source = (snapshot.get("trial_input_sources") or {}).get(input_name)
            if trial_source:
                source_id = trial_source["task_id"]
                scope_input = None
            if source_id is None and scope_input is None:
                raise RoleAssetError("Data line not found", status=404)
            field = (
                trial_source["field"]
                if trial_source
                else feedback["field"]
                if feedback
                else scope_input
                if scope_input is not None
                else binding.get("field", "text")
            )
            contract = task_input_contract(
                node.get("input_contract"),
                node.get("transform"),
                dispatch=bool(node.get("dispatch")),
            )
            port_field = input_name
            if (node.get("control") or {}).get("kind") == "condition":
                source_node = next(
                    (item for item in definition["tasks"] if item["id"] == source_id),
                    None,
                )
                contract = (
                    source_node.get("output_contract")
                    if source_node
                    else definition.get("input_contract")
                )
                port_field = (
                    "text" if scope_input is not None and contract is None else field
                )
            port = {"type": "string"}
            if contract is not None or port_field != input_name:
                try:
                    port = field_schema(contract, port_field, structured=True)
                except ValueError:
                    port = {}
            data = {
                "scope_run_id": scope_run_id,
                "task_run_id": task_run_id,
                "iteration": iteration,
                "attempt_id": attempt_id,
                "task_status": status,
                "attempts": attempts,
                "source": {"node_id": source_id or "__input", "port": field},
                "target": {
                    "node_id": node_id,
                    "port": input_name,
                    "type": port.get("type"),
                    **({"format": port["format"]} if "format" in port else {}),
                },
                "debug_fork_supported": (
                    (
                        node.get("transform") in DEBUG_FORK_TRANSFORMS
                        or task.snapshot.get("engine")
                        in {"plugin_task_v2", "single_task_v2"}
                        and task.snapshot.get("graph_task")
                        and definition.get("port_contract_version") == 2
                    )
                    and status in {"completed", "failed"}
                ),
                "read_status": "missing",
            }
            reference_format = port.get("format")
            managed = reference_format in REFERENCE_OUTPUTS.values()
            path = f"inputs.{input_name}"
            upstream_ref = None
            if source_id and not trial_source:
                upstream = await self._inspect_upstream_port(
                    session,
                    scope,
                    task,
                    source_id,
                    field,
                    previous_round=bool(feedback),
                )
                if upstream is not None and (
                    not managed
                    or not await self._historical_reference_status(
                        session, username, upstream["value"], reference_format, path
                    )
                ):
                    data["upstream"] = upstream
                    upstream_ref = upstream.get("artifact")

            if status == "skipped":
                data["read_status"] = "skipped"
            elif status in {"pending", "dependency_failed"} or (
                not state.get("execution_id")
                and (
                    status == "cancelled"
                    and not snapshot.get("input_reads")
                    or status in {"running", "interrupted", "cancelled"}
                    and state.get("retry_pending")
                )
            ):
                data["read_status"] = "not_read"
            else:
                inputs = snapshot.get("inputs") or {}
                reads = snapshot.get("input_reads")
                evidence = (snapshot.get("port_sources") or {}).get(input_name, {})
                recorded = (
                    (
                        input_name in reads
                        or bool(evidence.get("artifact") and evidence.get("field"))
                    )
                    if reads is not None
                    else (
                        bool(evidence.get("artifact") or trial_source)
                        or (
                            "selected_fields" in snapshot
                            and (
                                scope_input in scope.snapshot.get("inputs", {})
                                or (
                                    upstream_ref is not None
                                    and field
                                    in snapshot["selected_fields"].get(
                                        upstream_ref["artifact_id"], []
                                    )
                                )
                            )
                        )
                    )
                )
                if recorded and input_name in inputs:
                    value = inputs[input_name]
                    if managed and (
                        reference_status := await self._historical_reference_status(
                            session, username, value, reference_format, path
                        )
                    ):
                        data["read_status"] = reference_status
                        return data
                    data["read_status"] = (
                        "null" if inputs[input_name] is None else "read"
                    )
                    data["value"] = inputs[input_name]
                    exact = {
                        **((reads or {}).get(input_name) or {}),
                        **(trial_source or {}),
                        **evidence,
                    }
                    if (
                        reads is None
                        and upstream_ref is not None
                        and "artifact" not in exact
                    ):
                        exact["artifact"] = upstream_ref
                    if exact:
                        data["evidence"] = exact
                elif reads is not None and not recorded:
                    data["read_status"] = "not_read"
            return data

    async def _inspect_upstream_port(
        self,
        session: AsyncSession,
        scope: AgentWorkflowRun,
        consumer: AgentWorkflowRun,
        source_id: str,
        field: str,
        *,
        previous_round: bool,
    ) -> dict | None:
        """Read a completed producer separately from the consumer's attempt.

        Args:
            session: Transaction holding the authorized run snapshot.
            scope: Frozen workflow invocation shared by the producer and consumer.
            consumer: Consumer instance that determines the relevant round.
            source_id: Producer node ID from the selected data binding.
            field: Bound output field or intrinsic reference port.
            previous_round: Select the preceding round for a feedback binding.

        Returns:
            The available upstream value and exact artifact reference, if saved.
        """
        source_loop = next(
            (
                loop["id"]
                for loop in scope.snapshot["definition"].get("loops", [])
                if source_id in loop["body"]
            ),
            None,
        )
        loop = scope.state.get("loops", {}).get(source_loop, {})
        if source_loop and source_loop == consumer.snapshot.get("loop_id"):
            iteration = consumer.snapshot.get("iteration")
            if iteration is None:
                return None
            if previous_round:
                iteration -= 1
            source_identity = next(
                (
                    entry["task_runs"].get(source_id)
                    for entry in loop.get("iterations", [])
                    if entry["iteration"] == iteration
                ),
                None,
            )
        elif not source_loop or loop.get("status") in {"passed", "exhausted"}:
            # A separate upstream loop has a stable final round at this point.
            source_identity = _current_task_runs(scope).get(source_id)
        else:
            return None
        if not source_identity:
            return None
        source = await session.get(AgentWorkflowRun, source_identity)
        if (
            source is None
            or source.owner_username != scope.owner_username
            or source.snapshot.get("parent_run_id") != scope.run_id
            or source.status != "completed"
            or source.result is None
        ):
            return None

        child_reference = source.state.get("child_result_ref")
        reference = child_reference or {"artifact_id": source.run_id, "version": 1}
        upstream_ref = None
        try:
            origin, _ = await self._artifact_source(
                session, scope.owner_username, reference
            )
        except RoleAssetError:
            pass
        else:
            # A fixed call may directly publish its child's exact reference.
            if origin.run_id == source.run_id or (
                child_reference
                and origin.snapshot.get("root_run_id") == scope.snapshot["root_run_id"]
            ):
                upstream_ref = reference
        upstream = port_value(source.result, field, upstream_ref)
        if upstream is MISSING_PORT_VALUE:
            return None
        return {
            "value": upstream,
            "source_run_id": source.run_id,
            **({"artifact": upstream_ref} if upstream_ref is not None else {}),
        }

    async def inspect_member_attempt(
        self, username: str, run_id: str, task_run_id: str, attempt_id: str
    ) -> dict:
        """Read a dynamic member's dispatch and evidence for one exact attempt.

        Args:
            username: Authenticated Dashboard owner.
            run_id: Root run containing the autonomous team.
            task_run_id: Exact dynamic dispatch within that root.
            attempt_id: Current or archived attempt of that dispatch.

        Returns:
            Frozen dispatch, confirmed incoming messages and readable artifact versions.

        Raises:
            RoleAssetError: The root, member task or attempt is unavailable.
        """
        async with self.db.get_db() as session:
            if self.db.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN"))
            root = await session.get(AgentWorkflowRun, run_id)
            task = await session.get(AgentWorkflowRun, task_run_id)
            if (
                root is None
                or task is None
                or root.owner_username != username
                or task.owner_username != username
                or root.snapshot.get("root_run_id") != run_id
                or task.snapshot.get("root_run_id") != run_id
                or task.snapshot.get("engine") != "single_task_v2"
                or not task.snapshot.get("created_by_task_id")
            ):
                raise RoleAssetError("Member attempt not found", status=404)
            scope = await session.get(
                AgentWorkflowRun, task.snapshot.get("parent_run_id")
            )
            if (
                scope is None
                or scope.owner_username != username
                or scope.snapshot.get("root_run_id") != run_id
                or not scope.snapshot.get("team_definition")
            ):
                raise RoleAssetError("Member attempt not found", status=404)

            snapshot, status = task.snapshot, task.status
            if attempt_id != task.state.get("attempt_id"):
                saved = await session.get(AgentWorkflowAttempt, attempt_id)
                if saved is None or saved.run_id != task_run_id:
                    raise RoleAssetError("Member attempt not found", status=404)
                snapshot = saved.data.get("snapshot") or {}
                status = saved.data["status"]
            member = snapshot.get("member") or {}
            if not member.get("member_id"):
                raise RoleAssetError("Member attempt not found", status=404)

            receipt = col(AgentWorkflowMessage.consumption)
            incoming = (
                await session.execute(
                    select(AgentWorkflowMessage)
                    .where(
                        AgentWorkflowMessage.root_run_id == run_id,
                        AgentWorkflowMessage.scope_id == scope.state["scope_id"],
                        AgentWorkflowMessage.recipient_member_id == member["member_id"],
                        or_(
                            receipt.is_(None),
                            receipt["task_run_id"].as_string().is_(None),
                            receipt["attempt_id"].as_string().is_(None),
                            and_(
                                receipt["task_run_id"].as_string() == task_run_id,
                                receipt["attempt_id"].as_string() == attempt_id,
                            ),
                        ),
                    )
                    .order_by(
                        col(AgentWorkflowMessage.created_at),
                        col(AgentWorkflowMessage.message_id),
                    )
                )
            ).scalars()
            messages = []
            unconfirmed = 0
            for message in incoming:
                evidence = message.consumption or {}
                if (
                    evidence.get("task_run_id") != task_run_id
                    or evidence.get("attempt_id") != attempt_id
                    or not evidence.get("invocation_id")
                    or not isinstance(evidence.get("context_position"), int)
                    or evidence["context_position"] < 0
                ):
                    unconfirmed += 1
                    continue
                messages.append(
                    {
                        "message_id": message.message_id,
                        "sender_member_id": message.sender_member_id,
                        "text": message.payload["text"],
                        "consumption": evidence,
                        "artifact_refs": message.payload.get("artifact_refs") or [],
                    }
                )

            input_refs = snapshot.get("input_artifacts") or []
            event_data = col(AgentWorkflowEvent.data)
            read_refs = (
                (
                    await session.execute(
                        select(event_data["reference"])
                        .where(
                            AgentWorkflowEvent.run_id == task_run_id,
                            AgentWorkflowEvent.kind == "artifact.read",
                            event_data["attempt_id"].as_string() == attempt_id,
                        )
                        .order_by(col(AgentWorkflowEvent.id))
                    )
                )
                .scalars()
                .all()
            )
            refs = {
                (ref["artifact_id"], ref["version"])
                for ref in [
                    *input_refs,
                    *read_refs,
                    *(ref for msg in messages for ref in msg["artifact_refs"]),
                ]
            }
            # References in old snapshots and messages do not grant access.
            readable = (
                set(
                    (
                        await session.execute(
                            select(
                                AgentWorkflowArtifact.artifact_id,
                                AgentWorkflowArtifact.version,
                            )
                            .join(
                                AgentWorkflowRun,
                                AgentWorkflowRun.run_id == AgentWorkflowArtifact.run_id,
                            )
                            .where(
                                AgentWorkflowRun.owner_username == username,
                                tuple_(
                                    AgentWorkflowArtifact.artifact_id,
                                    AgentWorkflowArtifact.version,
                                ).in_(refs),
                            )
                        )
                    ).all()
                )
                if refs
                else set()
            )
            input_refs = [
                ref
                for ref in input_refs
                if (ref["artifact_id"], ref["version"]) in readable
            ]
            for message in messages:
                message["artifact_refs"] = [
                    ref
                    for ref in message["artifact_refs"]
                    if (ref["artifact_id"], ref["version"]) in readable
                ]
            result = await session.get(AgentWorkflowArtifact, (task_run_id, 1))
            dispatch_text = snapshot["definition"].get("task")
            return {
                "task_run_id": task_run_id,
                "attempt_id": attempt_id,
                "status": status,
                "member": {"member_id": member["member_id"], "name": member["name"]},
                "dispatch": {
                    "name": snapshot["definition"]["name"],
                    # Dynamic dispatch escapes braces for the graph template renderer.
                    "task": dispatch_text.replace("{{", "{").replace("}}", "}")
                    if dispatch_text is not None
                    else None,
                    "dispatcher_task_id": snapshot["created_by_task_id"],
                },
                "team": {
                    "asset_id": scope.snapshot["asset_id"],
                    "name": scope.snapshot["definition"]["name"],
                    "version": scope.snapshot["asset_version"],
                    "draft_revision": (scope.snapshot.get("trial") or {}).get(
                        "draft_revision"
                    ),
                },
                "messages": messages,
                "unconfirmed_deliveries": unconfirmed,
                "input_artifacts": input_refs,
                "read_artifacts": [
                    ref
                    for ref in read_refs
                    if (ref["artifact_id"], ref["version"]) in readable
                ],
                "result_artifact": {
                    "artifact_id": result.artifact_id,
                    "version": result.version,
                }
                if result is not None
                and result.run_id == task_run_id
                and result.submission_id == attempt_id
                else None,
            }

    async def get_run(
        self,
        username: str,
        run_id: str,
        *,
        include_details: bool = True,
        history_limit: int | None = None,
    ) -> dict:
        """Query persisted progress independently of any active runner or browser.

        Args:
            username: Authenticated owner.
            run_id: Independent execution identity.
            include_details: Include complete frozen inputs, results and history.
            history_limit: Maximum recent process records in the snapshot, if set.

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
            identities = list(
                dict.fromkeys(
                    [
                        run_id,
                        *_current_task_runs(row).values(),
                        *row.state.get("task_runs", {}).values(),
                        *(
                            identity
                            for loop in row.state.get("loops", {}).values()
                            for iteration in loop["iterations"]
                            for identity in iteration["task_runs"].values()
                        ),
                    ]
                )
            )
            tree = await _descendant_runs(session, row)
            identities = list(
                dict.fromkeys([*identities, *[item.run_id for item in tree]])
            )
            root_id = _root_run_id(row)
            control = await session.get(AgentWorkflowControl, root_id)
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
                                or_(
                                    col(AgentWorkflowEvent.id).in_(
                                        select(AgentWorkflowEvent.id)
                                        .where(
                                            col(AgentWorkflowEvent.run_id).in_(
                                                identities
                                            )
                                        )
                                        .order_by(col(AgentWorkflowEvent.id).desc())
                                        .limit(history_limit)
                                    ),
                                    col(AgentWorkflowEvent.id).in_(
                                        select(AgentWorkflowEvent.id)
                                        .where(
                                            col(AgentWorkflowEvent.run_id).in_(
                                                identities
                                            ),
                                            col(AgentWorkflowEvent.kind).like(
                                                "process.%"
                                            ),
                                        )
                                        .order_by(col(AgentWorkflowEvent.id).desc())
                                        .limit(history_limit)
                                    ),
                                )
                                if history_limit is not None
                                else true(),
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
            recent_event_floor = (
                (
                    await session.execute(
                        select(AgentWorkflowEvent.id)
                        .where(col(AgentWorkflowEvent.run_id).in_(identities))
                        .order_by(col(AgentWorkflowEvent.id).desc())
                        .limit(1)
                        .offset(history_limit - 1)
                    )
                ).scalar_one_or_none()
                if include_details and history_limit is not None
                else None
            )
            latest_result_ids = (
                (
                    await session.execute(
                        select(func.max(AgentWorkflowEvent.id))
                        .where(
                            col(AgentWorkflowEvent.run_id).in_(identities),
                            col(AgentWorkflowEvent.kind).in_(
                                ["result.accepted", "result.rejected"]
                            ),
                        )
                        .group_by(AgentWorkflowEvent.run_id)
                    )
                )
                .scalars()
                .all()
                if history_limit is not None
                else []
            )
            latest_results = (
                (
                    await session.execute(
                        select(AgentWorkflowEvent).where(
                            col(AgentWorkflowEvent.id).in_(latest_result_ids)
                        )
                    )
                )
                .scalars()
                .all()
                if latest_result_ids
                else []
            )
            message_events = []
            if include_details and history_limit is not None:
                family_root = (
                    row
                    if root_id == run_id
                    else await session.get(AgentWorkflowRun, root_id)
                )
                if family_root is None or family_root.owner_username != username:
                    raise RoleAssetError("Root run not found", status=404)
                family = (
                    tree
                    if root_id == run_id
                    else await _descendant_runs(session, family_root)
                )
                family_ids = [item.run_id for item in family]
                message_filter = (
                    col(AgentWorkflowEvent.run_id).in_(identities)
                    if row.snapshot.get("engine") == "graph_v2"
                    else or_(
                        AgentWorkflowEvent.run_id == run_id,
                        (col(AgentWorkflowEvent.run_id).in_(family_ids))
                        & (
                            AgentWorkflowEvent.data["scope_id"].as_string()
                            == row.state.get("scope_id")
                        )
                        & (
                            AgentWorkflowEvent.data["recipient_member_id"].as_string()
                            == row.snapshot.get("member", {}).get("member_id")
                        ),
                    )
                )
                message_events = (
                    (
                        await session.execute(
                            select(AgentWorkflowEvent)
                            .where(
                                AgentWorkflowEvent.kind == "member.message_sent",
                                message_filter,
                            )
                            .order_by(col(AgentWorkflowEvent.id).desc())
                            .limit(history_limit)
                        )
                    )
                    .scalars()
                    .all()
                )
            if any(not event.data.get("message_id") for event in message_events):
                raise RoleAssetError(
                    "Message history is missing a record identity", status=409
                )
            message_positions = {
                event.data["message_id"]: event.id
                for event in message_events
                if "message_id" in event.data
            }
            messages = (
                (
                    await session.execute(
                        select(AgentWorkflowMessage)
                        .where(
                            AgentWorkflowMessage.root_run_id == root_id,
                            col(AgentWorkflowMessage.message_id).in_(message_positions)
                            if history_limit is not None
                            else true(),
                        )
                        .order_by(
                            col(AgentWorkflowMessage.created_at),
                            col(AgentWorkflowMessage.message_id),
                        )
                    )
                )
                .scalars()
                .all()
                if include_details
                else []
            )
            if history_limit is not None and set(message_positions) != {
                message.message_id for message in messages
            }:
                raise RoleAssetError(
                    "Message history is missing a persisted record", status=409
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
            if history_limit is not None and any(
                event.kind.startswith("process.") and not event.data.get("record_id")
                for event in events
            ):
                raise RoleAssetError(
                    "Process history is missing a record identity", status=409
                )
            process_ids = [
                event.data["record_id"]
                for event in events
                if event.kind.startswith("process.") and "record_id" in event.data
            ]
            process = (
                (
                    (
                        await session.execute(
                            select(AgentWorkflowProcess)
                            .where(
                                col(AgentWorkflowProcess.run_id).in_(identities),
                                col(AgentWorkflowProcess.record_id).in_(process_ids)
                                if history_limit is not None
                                else true(),
                            )
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
            if history_limit is not None and set(process_ids) != {
                record.record_id for record in process
            }:
                raise RoleAssetError(
                    "Process history is missing a persisted record", status=409
                )
            event_positions = {
                event.data["record_id"]: event.id
                for event in events
                if event.kind.startswith("process.") and "record_id" in event.data
            }
            process.sort(key=lambda record: event_positions.get(record.record_id, 0))
            remote_run_ids = [
                item.run_id
                for item in rows
                if include_details
                and item.snapshot.get("effective", {})
                .get("runner", {})
                .get("type", "local")
                != "local"
            ]
            checkpoints = {
                item.run_id: item
                for item in (
                    await session.execute(
                        sa_select(
                            col(AgentWorkflowCheckpoint.run_id),
                            col(AgentWorkflowCheckpoint.phase),
                            col(AgentWorkflowCheckpoint.invocation_id),
                            col(AgentWorkflowCheckpoint.started_tools),
                            col(AgentWorkflowCheckpoint.result_submission),
                            AgentWorkflowCheckpoint.data["response"]["role"]
                            .as_string()
                            .label("response_role"),
                            AgentWorkflowCheckpoint.data["stage"]
                            .as_string()
                            .label("stage"),
                            (
                                case(
                                    (
                                        col(AgentWorkflowCheckpoint.run_id).in_(
                                            remote_run_ids
                                        ),
                                        AgentWorkflowCheckpoint.data["remote"][
                                            "runner"
                                        ],
                                    ),
                                    else_=None,
                                )
                                if remote_run_ids
                                else literal(None)
                            ).label("remote_runner"),
                            (
                                case(
                                    (
                                        col(AgentWorkflowCheckpoint.run_id).in_(
                                            remote_run_ids
                                        ),
                                        AgentWorkflowCheckpoint.data["remote"][
                                            "identity"
                                        ],
                                    ),
                                    else_=None,
                                )
                                if remote_run_ids
                                else literal(None)
                            ).label("remote_identity"),
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
            results = {}
            prior_usage = (
                await session.execute(
                    sa_select(
                        col(AgentWorkflowAttempt.run_id),
                        AgentWorkflowAttempt.data["checkpoint"]["model_calls"]
                        .as_integer()
                        .label("model_calls"),
                        AgentWorkflowAttempt.data["checkpoint"]["step_budget_used"]
                        .as_integer()
                        .label("steps_used"),
                    ).where(col(AgentWorkflowAttempt.run_id).in_(identities))
                )
            ).all()
            calls = (
                (
                    await session.execute(
                        select(AgentWorkflowToolCall)
                        .where(col(AgentWorkflowToolCall.run_id).in_(identities))
                        .order_by(
                            col(AgentWorkflowToolCall.created_at),
                            col(AgentWorkflowToolCall.call_index),
                        )
                    )
                )
                .scalars()
                .all()
                if include_details
                else []
            )
            recovery_data = {
                item.record_id: item.data
                for item in (
                    (
                        await session.execute(
                            select(AgentWorkflowToolRecovery).where(
                                col(AgentWorkflowToolRecovery.run_id).in_(identities)
                            )
                        )
                    )
                    .scalars()
                    .all()
                    if include_details
                    else []
                )
            }
            todos = (
                (
                    await session.execute(
                        select(AgentWorkflowTodo).where(
                            col(AgentWorkflowTodo.task_run_id).in_(identities)
                        )
                    )
                )
                .scalars()
                .all()
            )
            attempts = (
                (
                    await session.execute(
                        select(AgentWorkflowAttempt).where(
                            col(AgentWorkflowAttempt.run_id).in_(identities)
                        )
                    )
                )
                .scalars()
                .all()
                if include_details
                else []
            )
            attempt_events = (
                (
                    await session.execute(
                        select(AgentWorkflowEvent)
                        .where(
                            col(AgentWorkflowEvent.run_id).in_(identities),
                            AgentWorkflowEvent.kind == "attempt.retried",
                        )
                        .order_by(col(AgentWorkflowEvent.id).desc())
                        .limit(history_limit)
                    )
                )
                .scalars()
                .all()
                if include_details and history_limit is not None
                else []
            )
            attempt_positions = {
                event.data["previous_attempt_id"]: event.id
                for event in attempt_events
                if "previous_attempt_id" in event.data
            }
            if history_limit is not None and (
                len(attempt_positions) != len(attempt_events)
                or len(attempt_events) < history_limit
                and len(attempts) > len(attempt_positions)
                or not set(attempt_positions).issubset(
                    {attempt.attempt_id for attempt in attempts}
                )
            ):
                raise RoleAssetError(
                    "Attempt history is missing a persisted record", status=409
                )
            usage_receipts = [
                {**receipt.model_dump(mode="json", exclude={"data"}), **receipt.data}
                for receipt in (
                    await session.execute(
                        select(AgentWorkflowUsage).where(
                            col(AgentWorkflowUsage.run_id).in_(identities)
                        )
                    )
                )
                .scalars()
                .all()
            ]
            for current in rows:
                boundary = checkpoints.get(current.run_id)
                runner = current.snapshot.get("effective", {}).get("runner")
                if runner is None and "member" in current.snapshot:
                    raise RoleAssetError(
                        "Stored runner configuration is unsupported; start a new run",
                        status=409,
                    )
                result_issues = (
                    get_agent_runner_capability_issues(
                        runner, {"definition.role_id": "task_result"}
                    )
                    if "member" in current.snapshot
                    else []
                )
                current_calls = [
                    call for call in calls if call.run_id == current.run_id
                ]
                invocation_calls = [
                    call
                    for call in current_calls
                    if boundary
                    and call.attempt_id == current.state.get("attempt_id")
                    and call.invocation_id == boundary.invocation_id
                ]
                pending_calls = [
                    call
                    for call in invocation_calls
                    if not recovery_data.get(call.record_id, {}).get(
                        "replacement_record_id"
                    )
                ]
                has_saved_tool_results = any(
                    call.attempt_id == current.state.get("attempt_id")
                    and call.status in {"result_saved", "applied"}
                    and (
                        call.raw_results
                        or recovery_data.get(call.record_id, {}).get("identity")
                    )
                    for call in current_calls
                )
                awaiting_saved_tool_recovery = (
                    current.status == "awaiting_result"
                    and has_saved_tool_results
                    and boundary is not None
                    and not boundary.remote_runner
                    and (
                        boundary.phase == "request"
                        or boundary.phase == "response"
                        and boundary.response_role == "err"
                        and boundary.stage == "main"
                        and not boundary.started_tools
                        and not invocation_calls
                    )
                )
                unknown_calls = [
                    call
                    for call in pending_calls
                    if call.status in {"started", "unknown"}
                ]
                current_events = [
                    event for event in events if event.run_id == current.run_id
                ]
                current_artifacts = [
                    artifact
                    for artifact in artifacts
                    if artifact.run_id == current.run_id
                ]
                errors = (
                    current.state.get("issues", [])
                    if current.snapshot.get("transform")
                    else []
                )
                result_events = (
                    [
                        event
                        for event in latest_results
                        if event.run_id == current.run_id
                    ]
                    if history_limit is not None
                    else current_events
                )
                for event in result_events:
                    if event.kind == "result.rejected":
                        errors = event.data["fields"]
                    elif event.kind == "result.accepted":
                        errors = []
                results[current.run_id] = {
                    "run_id": current.run_id,
                    "status": current.status,
                    "control": {
                        "status": control.status if control else "active",
                        "revision": control.revision if control else 0,
                        "root_run_id": root_id,
                    },
                    "result_ref": current.state.get("child_result_ref")
                    or (
                        {"artifact_id": current.run_id, "version": 1}
                        if current_artifacts
                        else None
                    ),
                    "artifacts": [
                        {
                            "artifact_id": a.artifact_id,
                            "version": a.version,
                            "summary": json.dumps(a.content, ensure_ascii=False)[:200],
                        }
                        for a in current_artifacts
                    ],
                    "todos": [
                        {
                            "todo_id": todo.todo_id,
                            "root_run_id": todo.root_run_id,
                            "task_run_id": todo.task_run_id,
                            **todo.data,
                        }
                        for todo in todos
                        if todo.task_run_id == current.run_id
                        and username in todo.data.get("answerable_by", [])
                    ]
                    if include_details
                    else [],
                    "usage": {
                        key: getattr(boundary, key)
                        + sum(
                            getattr(previous, key)
                            for previous in prior_usage
                            if previous.run_id == current.run_id
                        )
                        if boundary is not None
                        and getattr(boundary, key) is not None
                        and all(
                            getattr(previous, key) is not None
                            for previous in prior_usage
                            if previous.run_id == current.run_id
                        )
                        else None
                        for key in ("model_calls", "steps_used")
                    },
                }
                if include_details:
                    results[current.run_id].update(
                        {
                            "messages": [
                                {
                                    **message.model_dump(
                                        mode="json", exclude={"payload"}
                                    ),
                                    **(
                                        {
                                            "cursor": message_positions.get(
                                                message.message_id
                                            )
                                        }
                                        if history_limit is not None
                                        else {}
                                    ),
                                    **message.payload,
                                }
                                for message in messages
                                if message.sender_task_run_id == current.run_id
                                or (
                                    message.scope_id == current.state.get("scope_id")
                                    and message.recipient_member_id
                                    == current.snapshot.get("member", {}).get(
                                        "member_id"
                                    )
                                )
                            ],
                            "recovery": {
                                "capability": boundary.remote_runner["recovery"]
                                if boundary and boundary.remote_runner
                                else "saved_model_response"
                                if "member" in current.snapshot
                                else "task_boundaries",
                                "has_saved_tool_results": has_saved_tool_results,
                                "available": (
                                    current.status
                                    in {"interrupted", "failed", "blocked"}
                                    or awaiting_saved_tool_recovery
                                )
                                and not result_issues
                                and not current.state.get("debug_write_preview")
                                and boundary is not None
                                and (
                                    not boundary.remote_runner
                                    or boundary.phase in {"request", "remote_result"}
                                    or boundary.remote_identity is not None
                                    and "remote_query"
                                    in boundary.remote_runner["capabilities"]
                                )
                                and (
                                    not boundary.started_tools
                                    or pending_calls
                                    and (
                                        not unknown_calls
                                        or any(
                                            has_tool_recovery(
                                                recovery_data.get(
                                                    call.record_id, {}
                                                ).get("declaration")
                                            )
                                            for call in unknown_calls
                                        )
                                        or any(
                                            call.status == "intent"
                                            for call in pending_calls
                                        )
                                    )
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
                                **(
                                    {"remote_identity": boundary.remote_identity}
                                    if boundary and boundary.remote_runner
                                    else {}
                                ),
                                "reason": result_issues[0]["message"]
                                if result_issues
                                else "Remote result requires confirmation; no reliable run identity/query is available."
                                if boundary
                                and boundary.remote_runner
                                and boundary.phase not in {"request", "remote_result"}
                                and (
                                    boundary.remote_identity is None
                                    or "remote_query"
                                    not in boundary.remote_runner["capabilities"]
                                )
                                else "Started tools require confirmation before recovery."
                                if boundary
                                and (
                                    unknown_calls
                                    or boundary.started_tools
                                    and not pending_calls
                                )
                                and not boundary.result_submission
                                else "No saved execution checkpoint."
                                if boundary is None
                                else "The original checkpoint cannot continue producing a task result."
                                if current.status == "awaiting_result"
                                and has_saved_tool_results
                                and not awaiting_saved_tool_recovery
                                else None,
                            },
                            "snapshot": current.snapshot,
                            "attempts": [
                                a.data
                                if history_limit is None
                                else {
                                    "attempt_id": a.attempt_id,
                                    "status": a.data["status"],
                                    "cursor": attempt_positions[a.attempt_id],
                                    "state": {
                                        "reason": a.data.get("state", {}).get("reason")
                                    },
                                    "snapshot": {
                                        "inputs": a.data.get("snapshot", {}).get(
                                            "inputs"
                                        ),
                                        "input_artifacts": a.data.get(
                                            "snapshot", {}
                                        ).get("input_artifacts", []),
                                    },
                                    "checkpoint": {
                                        "stats": {
                                            "token_usage": (
                                                (a.data.get("checkpoint") or {}).get(
                                                    "stats"
                                                )
                                                or {}
                                            ).get("token_usage")
                                        }
                                    },
                                }
                                for a in attempts
                                if a.run_id == current.run_id
                                and (
                                    history_limit is None
                                    or a.attempt_id in attempt_positions
                                )
                            ],
                            "state": current.state,
                            "result": current.result,
                            "pending_result": current.pending_result,
                            "cursor": current_events[-1].id if current_events else 0,
                            **(
                                {
                                    "event_history_before": recent_event_floor or 1,
                                    "message_history_before": min(
                                        message_positions.values()
                                    )
                                    if len(message_events) == history_limit
                                    else 1,
                                    "attempt_history_before": min(
                                        attempt_positions.values()
                                    )
                                    if len(attempt_events) == history_limit
                                    else 1,
                                }
                                if history_limit is not None
                                else {}
                            ),
                            "process": [
                                {
                                    **record.model_dump(mode="json"),
                                    "cursor": event_positions.get(record.record_id),
                                    "root_run_id": root_id,
                                    "scope_id": current.state.get("scope_id"),
                                    "task_run_id": current.run_id,
                                    "node_id": current.snapshot.get("node_id"),
                                }
                                for record in process
                                if record.run_id == current.run_id
                            ],
                            "tool_calls": [
                                {
                                    **call.model_dump(mode="json"),
                                    "recovery": recovery_data.get(call.record_id, {}),
                                    "available_actions": [
                                        "retry",
                                        "confirm_result",
                                        "confirm_failure",
                                    ]
                                    + (
                                        ["reconcile"]
                                        if has_tool_recovery(
                                            recovery_data.get(call.record_id, {}).get(
                                                "declaration"
                                            )
                                        )
                                        else []
                                    )
                                    if call in unknown_calls
                                    and current.status
                                    in {"interrupted", "failed", "blocked"}
                                    and not any(
                                        decision.get("execution_id") is None
                                        for decision in recovery_data.get(
                                            call.record_id, {}
                                        ).get("decisions", [])
                                    )
                                    else [],
                                }
                                for call in current_calls
                                if history_limit is None
                                or boundary is not None
                                and call.attempt_id == current.state.get("attempt_id")
                                and call.invocation_id == boundary.invocation_id
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
                                        "root_run_id",
                                        "parent_run_id",
                                        "depth",
                                    )
                                    if key in current.snapshot
                                },
                                "definition": {
                                    "name": current.snapshot["definition"]["name"]
                                },
                                **(
                                    {
                                        "trial": {
                                            "draft_revision": current.snapshot["trial"][
                                                "draft_revision"
                                            ],
                                            "mode": current.snapshot["trial"]["mode"],
                                        }
                                    }
                                    if current.snapshot.get("trial")
                                    else {}
                                ),
                                "task": current.snapshot.get(
                                    "task", current.snapshot["definition"]["name"]
                                )[:200],
                            },
                            "state": {
                                key: current.state[key]
                                for key in (
                                    "task_id",
                                    "attempt_id",
                                    "reason",
                                    "issues",
                                    "capacity_released",
                                    "waiting_on",
                                    "wait_kind",
                                    "waiting_tasks",
                                    "scope_id",
                                    "child_scope_run_id",
                                    "child_result_ref",
                                )
                                if key in current.state
                            },
                        }
                    )
            result = results[run_id]
        if row.snapshot.get("engine") == "graph_v2":
            if row.snapshot.get("team_definition"):
                result["members"] = {
                    key: frozen["member"]
                    if include_details
                    else {
                        field: frozen["member"][field]
                        for field in (
                            "member_id",
                            "name",
                            "session_id",
                            "conversation_id",
                            "umo",
                        )
                    }
                    for key, frozen in {
                        **row.snapshot["members"],
                        **row.state.get("members", {}),
                    }.items()
                }
                result["relationships"] = _run_definition(row)["edges"]
                if include_details:
                    result["runtime_definition"] = {
                        **_run_definition(row),
                        "edges": [
                            *result["relationships"],
                            *(
                                {"source": "coordinator", "target": task["id"]}
                                for task in row.state.get("dynamic_tasks", [])
                            ),
                        ],
                    }
            result["tasks"] = [
                {**results[identity], "node_id": node_id}
                for node_id, identity in _current_task_runs(row).items()
            ]
            result["loops"] = [
                {
                    "loop_id": loop_id,
                    **loop,
                    "iterations": [
                        {
                            **iteration,
                            "tasks": [
                                {**results[identity], "node_id": node_id}
                                for node_id, identity in iteration["task_runs"].items()
                            ],
                        }
                        for iteration in loop["iterations"]
                    ],
                }
                for loop_id, loop in row.state.get("loops", {}).items()
            ]
            historical_tasks = {
                task["run_id"]: task
                for loop in result["loops"]
                for iteration in loop["iterations"]
                for task in iteration["tasks"]
            }
            all_tasks = list(
                {
                    **historical_tasks,
                    **{task["run_id"]: task for task in result["tasks"]},
                }.values()
            )
            if include_details:
                result["events"] = sorted(
                    [
                        *result["events"],
                        *[event for task in all_tasks for event in task["events"]],
                    ],
                    key=lambda event: event["cursor"],
                )
            result["artifacts"] = [
                artifact for task in all_tasks for artifact in task["artifacts"]
            ]
            result["result_ref"] = None
            if row.snapshot.get("team_definition") and row.status == "completed":
                result["result_ref"] = results[_current_task_runs(row)["coordinator"]][
                    "result_ref"
                ]
            elif row.status == "completed" and row.snapshot["definition"].get(
                "result_task_id"
            ):
                task_id = _current_task_runs(row)[
                    row.snapshot["definition"]["result_task_id"]
                ]
                task_result = results[task_id]
                result["result_ref"] = task_result["result_ref"]
                if include_details:
                    result["result"] = task_result["result"]
            result["todos"] = [todo for task in all_tasks for todo in task["todos"]]
            result["scopes"] = [
                {
                    **results[scope.run_id],
                    "tasks": [
                        {**results[identity], "node_id": node_id}
                        for node_id, identity in _current_task_runs(scope).items()
                    ],
                    "loops": [
                        {
                            "loop_id": loop_id,
                            **loop,
                            "iterations": [
                                {
                                    **iteration,
                                    "tasks": [
                                        {**results[identity], "node_id": node_id}
                                        for node_id, identity in iteration[
                                            "task_runs"
                                        ].items()
                                    ],
                                }
                                for iteration in loop["iterations"]
                            ],
                        }
                        for loop_id, loop in scope.state.get("loops", {}).items()
                    ],
                }
                for scope in tree
                if scope.run_id != run_id and scope.snapshot.get("engine") == "graph_v2"
            ]
            for scope in result["scopes"]:
                record = next(item for item in tree if item.run_id == scope["run_id"])
                if include_details:
                    scope["runtime_definition"] = {
                        **_run_definition(record),
                        "edges": [
                            *_run_definition(record)["edges"],
                            *(
                                {"source": "coordinator", "target": task["id"]}
                                for task in record.state.get("dynamic_tasks", [])
                            ),
                        ],
                    }
                scope["usage"] = {
                    key: sum(
                        task["usage"][key]
                        for task in scope["tasks"]
                        if task["usage"][key] is not None
                    )
                    if all(
                        task["usage"][key] is not None
                        for task in scope["tasks"]
                        if task["snapshot"]["engine"] == "single_task_v2"
                        and task["status"]
                        not in {"pending", "skipped", "dependency_failed"}
                    )
                    else None
                    for key in ("model_calls", "steps_used")
                }
            metered = [
                item
                for item in tree
                if "member" in item.snapshot
                and item.status not in {"pending", "skipped", "dependency_failed"}
            ]
            result["usage"] = {
                key: sum(results[item.run_id]["usage"][key] or 0 for item in metered)
                if all(
                    results[item.run_id]["usage"][key] is not None for item in metered
                )
                else None
                for key in ("model_calls", "steps_used")
            }
            result["artifacts"] = [
                artifact
                for identity in identities
                for artifact in results[identity]["artifacts"]
                if identity != run_id
            ]
            result["todos"] = [
                todo
                for identity in identities
                for todo in results[identity]["todos"]
                if identity != run_id
            ]
            if include_details:
                result["events"] = sorted(
                    {
                        event["cursor"]: event
                        for identity in identities
                        for event in results[identity]["events"]
                    }.values(),
                    key=lambda event: event["cursor"],
                )
        if include_details:
            result["messages"] = [
                {
                    **message.model_dump(mode="json", exclude={"payload"}),
                    **(
                        {"cursor": message_positions.get(message.message_id)}
                        if history_limit is not None
                        else {}
                    ),
                    **message.payload,
                }
                for message in messages
                if row.snapshot.get("engine") == "graph_v2"
                and message.scope_id in {item.state["scope_id"] for item in tree}
                or message.sender_task_run_id == run_id
                or (
                    message.scope_id == row.state.get("scope_id")
                    and message.recipient_member_id
                    == row.snapshot.get("member", {}).get("member_id")
                )
            ]
            result["cursor"] = max(
                (event["cursor"] for event in result["events"]), default=0
            )
        tasks = [
            *result.get("tasks", [result]),
            *[task for scope in result.get("scopes", []) for task in scope["tasks"]],
        ]
        views = [
            result,
            *tasks,
            *result.get("scopes", []),
            *[
                task
                for owner in [result, *result.get("scopes", [])]
                for loop in owner.get("loops", [])
                for iteration in loop["iterations"]
                for task in iteration["tasks"]
            ],
        ]
        for view in views:
            selected_ids = {view["run_id"]}
            while True:
                expanded = selected_ids | {
                    r.run_id
                    for r in tree
                    if r.snapshot.get("parent_run_id") in selected_ids
                }
                if expanded == selected_ids:
                    break
                selected_ids = expanded
            selected = [
                receipt
                for receipt in usage_receipts
                if receipt["run_id"] in selected_ids
            ]
            record = next(r for r in tree if r.run_id == view["run_id"])
            if selected:
                view["usage"] = summarize_usage(
                    selected,
                    record.snapshot["definition"].get("limits") or {},
                    include_details,
                )
            else:
                view["usage"].update(
                    {
                        "limits": record.snapshot["definition"].get("limits") or {},
                        "tokens": {
                            "reported": None,
                            "estimated": None,
                            "reserved": 0,
                            "unknown_calls": 1
                            if "member" in record.snapshot
                            and record.status not in {"pending", "skipped"}
                            else 0,
                        },
                        "ledger": [],
                    }
                )
            if include_details and history_limit is not None:
                ordered = sorted(
                    selected,
                    key=lambda receipt: (receipt["created_at"], receipt["receipt_id"]),
                )
                recent = ordered[-history_limit:]
                view["usage"]["ledger"] = recent
                view["usage"]["ledger_before"] = (
                    f"{recent[0]['created_at']}|{recent[0]['receipt_id']}"
                    if len(ordered) > history_limit
                    else None
                )
            view["usage"]["constraints"] = record.snapshot.get("scope_limits", [])
            if (
                not selected
                and record.snapshot.get("usage_protocol") == 1
                and all(
                    r.run_id in checkpoints and checkpoints[r.run_id].phase == "request"
                    for r in tree
                    if r.run_id in selected_ids
                    and "member" in r.snapshot
                    and r.status not in {"pending", "skipped", "dependency_failed"}
                )
            ):
                # A saved request is intent, not evidence of a provider delivery.
                # New admission receipts supersede the historical checkpoint count.
                view["usage"]["model_calls"] = 0
                view["usage"]["steps_used"] = 0
                view["usage"]["tokens"]["unknown_calls"] = 0
            view["usage"]["stops"] = [
                {"task_run_id": r.run_id, **stop}
                for r in tree
                if r.run_id in selected_ids
                for stop in [
                    r.state.get("limit_stop"),
                    *[
                        loop.get("limit_stop")
                        for loop in r.state.get("loops", {}).values()
                    ],
                ]
                if stop
            ]
        result["progress"] = {
            "total": len(tasks),
            "completed": sum(
                task["status"] in {"completed", "skipped"} for task in tasks
            ),
            "running": sum(
                task["status"] == "running"
                and not task["state"].get("capacity_released")
                for task in tasks
            ),
            "waiting": sum(
                task["status"] not in {"pending", "running", "completed", "skipped"}
                or task["status"] == "running"
                and task["state"].get("capacity_released", False)
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

    async def _check_member_limits(
        self, session: AsyncSession, row: AgentWorkflowRun, additional: int = 0
    ) -> None:
        """Apply instance ceilings to initial, nested and dynamic admission.

        Args:
            session: Existing root admission transaction.
            row: Existing scope/task or a new scope before it is persisted.
            additional: Dynamic instances not yet present in the scope snapshot.

        Raises:
            RoleAssetError: A frozen ancestor instance ceiling would be exceeded.
        """
        ancestors = [row]
        current = row
        while current.snapshot.get("parent_run_id"):
            current = await session.get(
                AgentWorkflowRun, current.snapshot["parent_run_id"]
            )
            assert current is not None
            ancestors.append(current)
        new_members = {
            item["member"]["member_id"]
            for item in {
                **row.snapshot.get("members", {}),
                **row.state.get("members", {}),
            }.values()
        }
        for scope in ancestors:
            limits = scope.snapshot["definition"].get("limits") or {}
            ceiling = limits.get("max_members")
            if ceiling is None:
                continue
            family = await _descendant_runs(session, scope)
            identities = (
                new_members
                | {
                    item["member"]["member_id"]
                    for r in family
                    for item in {
                        **r.snapshot.get("members", {}),
                        **r.state.get("members", {}),
                    }.values()
                }
                | {
                    r.snapshot["member"]["member_id"]
                    for r in family
                    if "member" in r.snapshot
                }
            )
            if len(identities) + additional > ceiling:
                raise RoleAssetError(
                    "Member instance limit exceeded",
                    [
                        {
                            "path": "limits.max_members",
                            "code": "LIMIT",
                            "message": f"Scope {scope.run_id} permits {ceiling} member instances",
                        }
                    ],
                    status=409,
                )

    async def begin_usage(
        self,
        username: str,
        run_id: str,
        attempt_id: str,
        execution_id: str,
        payload: dict,
    ) -> dict:
        """Reserve capacity for one delivery under the existing root admission lock.

        Args:
            username: Actual initiating owner.
            run_id: Task that will make the call.
            attempt_id: Current Attempt; recovery retains this identity.
            execution_id: Current executor fence.
            payload: Unique receipt and original Invocation with token estimates.

        Returns:
            Durable receipt and the permitted single-call output cap.

        Raises:
            RoleAssetError: Invalid identity, stale ownership, paused admission or
                exhausted limits.
        """
        try:
            request = UsageStart.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError("Invalid usage reservation") from exc
        wake_root_id: str | None = None
        try:
            async with self.db.get_db() as session:
                row = await session.get(AgentWorkflowRun, run_id)
                if row is None or row.owner_username != username:
                    raise RoleAssetError("Run not found", status=404)
                control = await self._lock_control(session, row)
                await session.refresh(row)
                await self._require_execution_owner(session, run_id, execution_id)
                await self._check_member_limits(session, row)
                if (
                    control.status == "paused"
                    and row.state.get("attempt_id") == attempt_id
                ):
                    raise RoleAssetError(
                        "Execution cannot admit another call",
                        [
                            {
                                "path": "control.status",
                                "code": "PAUSED",
                                "message": "Continue the run before reserving another call",
                            }
                        ],
                        status=409,
                    )
                if (
                    control.status != "active"
                    or row.state.get("attempt_id") != attempt_id
                ):
                    raise RoleAssetError(
                        "Execution cannot admit another call", status=409
                    )
                root = await session.get(AgentWorkflowRun, _root_run_id(row))
                assert root is not None
                if root.snapshot.get("api_key_id"):
                    await self._require_api_run_access(session, root)
                previous = await session.get(AgentWorkflowUsage, request.receipt_id)
                if previous:
                    if (
                        previous.run_id,
                        previous.attempt_id,
                        previous.execution_id,
                        previous.invocation_id,
                    ) != (
                        run_id,
                        attempt_id,
                        execution_id,
                        request.invocation_id,
                    ) or previous.data["request"] != request.model_dump():
                        raise RoleAssetError(
                            "Usage receipt identity was reused", status=409
                        )
                    return {
                        **previous.model_dump(mode="json", exclude={"data"}),
                        **previous.data,
                    }
                ancestors = [row]
                current = row
                while current.snapshot.get("parent_run_id"):
                    current = await session.get(
                        AgentWorkflowRun, current.snapshot["parent_run_id"]
                    )
                    assert current is not None
                    ancestors.append(current)
                receipts = (
                    (
                        await session.execute(
                            select(AgentWorkflowUsage).where(
                                AgentWorkflowUsage.root_run_id == root.run_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                output_caps = (
                    [request.max_output_tokens] if request.max_output_tokens else []
                )
                for scope in ancestors:
                    cap = (scope.snapshot["definition"].get("limits") or {}).get(
                        "max_output_tokens"
                    )
                    if cap:
                        output_caps.append(cap)
                output_cap = min(output_caps) if output_caps else 2048
                reserved = (
                    (request.input_tokens or 0) + output_cap
                    if request.kind == "model"
                    else 0
                )
                reason = None
                for scope in ancestors:
                    limits = scope.snapshot["definition"].get("limits") or {}
                    subtree = {r.run_id for r in await _descendant_runs(session, scope)}
                    selected = [r.data for r in receipts if r.run_id in subtree]
                    steps = limits.get("max_steps")
                    if steps is not None and len(selected) >= steps:
                        reason = {
                            "limit": "max_steps",
                            "scope_run_id": scope.run_id,
                            "value": steps,
                        }
                        break
                    budget = limits.get("token_budget")
                    if (
                        budget is not None
                        and row.snapshot.get("transform") == "retrieve_knowledge_v1"
                    ):
                        reason = {
                            "limit": "token_budget",
                            "scope_run_id": scope.run_id,
                            "value": budget,
                            "message": "Knowledge retrieval does not report token use",
                        }
                        break
                    committed = sum(
                        r["reported_tokens"]
                        if r.get("reported_tokens") is not None
                        else r["estimated_tokens"]
                        if r.get("estimated_tokens") is not None
                        else r["reserved_tokens"]
                        for r in selected
                    )
                    if budget is not None and (
                        committed >= budget
                        or request.kind == "model"
                        and (
                            request.input_tokens is None
                            or committed + reserved > budget
                        )
                    ):
                        in_flight = sum(
                            r["reserved_tokens"]
                            for r in selected
                            if r["quality"] == "in_flight"
                        )
                        if (
                            (
                                request.kind != "model"
                                or request.input_tokens is not None
                            )
                            and committed - in_flight + reserved <= budget
                            and in_flight
                        ):
                            new_wait = not row.state.get("usage_wait")
                            if new_wait:
                                session.add(
                                    AgentWorkflowEvent(
                                        run_id=run_id,
                                        kind="usage.waiting",
                                        data={
                                            "attempt_id": attempt_id,
                                            "scope_run_id": scope.run_id,
                                        },
                                    )
                                )
                            row.state = {
                                **row.state,
                                "capacity_released": True,
                                "usage_wait": True,
                                "reason": "Waiting for in-flight usage reservations.",
                            }
                            session.add(row)
                            await session.commit()
                            if new_wait and row.snapshot.get("parent_run_id"):
                                wake_root_id = root.run_id
                            return {"waiting": True, "scope_run_id": scope.run_id}
                        reason = {
                            "limit": "token_budget",
                            "scope_run_id": scope.run_id,
                            "value": budget,
                            "required_tokens": reserved,
                            "committed_tokens": committed,
                        }
                        break
                if reason:
                    row.state = {**row.state, "limit_stop": reason}
                    session.add(row)
                    session.add(
                        AgentWorkflowEvent(
                            run_id=run_id,
                            kind="limit.reached",
                            data={**reason, "attempt_id": attempt_id},
                        )
                    )
                    await session.commit()
                    message = f"Execution limit reached: {reason['limit']}"
                    if reason.get("message"):
                        message += f". {reason['message']}"
                    raise RoleAssetError(message, status=409)
                if row.state.get("usage_wait"):
                    parent_id = row.snapshot.get("parent_run_id")
                    if parent_id:
                        scope = await session.get(AgentWorkflowRun, parent_id)
                        assert scope is not None
                        family = await _descendant_runs(session, root)
                        active = [
                            r
                            for r in family
                            if r.status == "running"
                            and _uses_execution_slot(r.snapshot)
                            and not r.state.get("capacity_released")
                            and r.run_id != run_id
                        ]
                        if not _has_scope_capacity(root, scope, active):
                            return {"waiting": True, "scope_run_id": scope.run_id}
                        role_id = _capacity_key(row.snapshot)
                        role_limit = row.snapshot.get("effective", {}).get(
                            "max_parallel"
                        )
                        member_id = row.snapshot.get("member", {}).get("member_id")
                        if (
                            member_id is not None
                            and any(
                                r.snapshot["member"]["member_id"] == member_id
                                for r in family
                                if r.status == "running"
                                and "member" in r.snapshot
                                and r.run_id != run_id
                            )
                            or role_limit is not None
                            and sum(
                                _capacity_key(r.snapshot) == role_id for r in active
                            )
                            >= role_limit
                        ):
                            return {"waiting": True, "scope_run_id": scope.run_id}
                    row.state = {
                        **row.state,
                        "capacity_released": False,
                        "usage_wait": False,
                        "reason": None,
                    }
                    session.add(row)
                    session.add(
                        AgentWorkflowEvent(
                            run_id=run_id,
                            kind="usage.resumed",
                            data={"attempt_id": attempt_id},
                        )
                    )
                receipt = AgentWorkflowUsage(
                    receipt_id=request.receipt_id,
                    root_run_id=root.run_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    execution_id=execution_id,
                    invocation_id=request.invocation_id,
                    data={
                        "request": request.model_dump(),
                        "kind": request.kind,
                        "name": request.name,
                        "task_id": row.state["task_id"],
                        "scope_id": row.state["scope_id"],
                        "member_id": row.snapshot.get("member", {}).get("member_id"),
                        "quality": "in_flight",
                        "max_output_tokens": output_cap
                        if request.kind == "model"
                        else None,
                        "reserved_tokens": reserved,
                        "reported_tokens": None,
                        "estimated_tokens": None,
                    },
                )
                session.add(receipt)
                session.add(
                    AgentWorkflowEvent(
                        run_id=run_id,
                        kind="usage.started",
                        data={
                            "receipt_id": receipt.receipt_id,
                            "invocation_id": receipt.invocation_id,
                            "attempt_id": attempt_id,
                        },
                    )
                )
                await session.commit()
                return {
                    **receipt.model_dump(mode="json", exclude={"data"}),
                    **receipt.data,
                }
        finally:
            # Only a committed wait transition wakes scheduling, after releasing
            # the session and root lock. Polls retain the original executor.
            if wake_root_id is not None and not self._closing:
                await self.advance_run(username, wake_root_id)

    async def report_usage(
        self, username: str, receipt_id: str, execution_id: str, payload: dict
    ) -> dict:
        """Save late or repeated usage without changing current execution ownership.

        Args:
            username: Owner of the already admitted delivery.
            receipt_id: Durable identity allocated before the external call.
            execution_id: Executor that originally made this delivery.
            payload: Provider tokens, an estimate, or missing usage.

        Returns:
            Updated receipt, including its immutable original execution references.

        Raises:
            RoleAssetError: Inaccessible receipt or conflicting reported consumption.
        """
        try:
            report = UsageReport.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError("Invalid usage report") from exc
        async with self.db.get_db() as session:
            receipt = await session.get(AgentWorkflowUsage, receipt_id)
            row = (
                await session.get(AgentWorkflowRun, receipt.run_id) if receipt else None
            )
            if row is None or row.owner_username != username:
                raise RoleAssetError("Usage receipt not found", status=404)
            await self._lock_control(session, row)
            assert receipt is not None
            await session.refresh(receipt)
            if receipt.execution_id != execution_id:
                raise RoleAssetError(
                    "Usage report belongs to another executor", status=409
                )
            previous = receipt.data
            reported = (
                report.total_tokens is not None
                or report.input_tokens is not None
                and report.output_tokens is not None
            )
            report_data = report.model_dump(
                exclude={"total_tokens"} if report.total_tokens is None else set()
            )
            if (
                report.total_tokens is not None
                and report.input_tokens is not None
                and report.output_tokens is not None
                and report.total_tokens != report.input_tokens + report.output_tokens
            ):
                raise RoleAssetError(
                    "Reported total tokens do not match token components", status=409
                )
            if previous.get("report") == report_data:
                return {**receipt.model_dump(mode="json", exclude={"data"}), **previous}
            if previous["quality"] == "reported":
                raise RoleAssetError("Reported usage cannot be replaced", status=409)
            receipt.data = {
                **previous,
                "report": report_data,
                "quality": "reported"
                if reported
                else "estimated"
                if report.estimated_tokens is not None
                else "unknown",
                "reported_tokens": report.total_tokens
                if report.total_tokens is not None
                else (report.input_tokens or 0) + (report.output_tokens or 0)
                if reported
                else None,
                "estimated_tokens": None
                if reported
                else report.estimated_tokens
                if report.estimated_tokens is not None
                else previous["reserved_tokens"] or None,
            }
            session.add(receipt)
            session.add(
                AgentWorkflowEvent(
                    run_id=receipt.run_id,
                    kind="usage.reported",
                    data={
                        "receipt_id": receipt.receipt_id,
                        "attempt_id": receipt.attempt_id,
                        "quality": receipt.data["quality"],
                    },
                )
            )
            await session.commit()
            return {**receipt.model_dump(mode="json", exclude={"data"}), **receipt.data}

    async def _lock_control(
        self, session: AsyncSession, run: AgentWorkflowRun
    ) -> AgentWorkflowControl:
        """Serialize admission and controls on the existing root ownership row.

        Args:
            session: Transaction performing admission, recovery or a user decision.
            run: Root or existing child task whose control protocol is shared.

        Returns:
            Locked durable root decision, created on first admission.
        """
        root_id = _root_run_id(run)
        await session.execute(
            update(AgentWorkflowRun)
            .where(col(AgentWorkflowRun.run_id) == root_id)
            .values(state=AgentWorkflowRun.state)
        )
        control = await session.get(AgentWorkflowControl, root_id)
        if control is None:
            control = AgentWorkflowControl(run_id=root_id)
            session.add(control)
            await session.flush()
        else:
            await session.refresh(control)
        return control

    async def _await_execution_permission(
        self, username: str, run_id: str, execution_id: str
    ) -> None:
        """Park at a durable safe boundary until the root permits more work.

        Args:
            username: Frozen initiating owner.
            run_id: Existing task execution.
            execution_id: Fence preventing a displaced executor from continuing.

        Raises:
            RoleAssetError: Cancellation, changed ownership or incompatible frozen
                resources prevent this execution from continuing.
        """
        while True:
            async with self.db.get_db() as session:
                row = await session.get(AgentWorkflowRun, run_id)
                if row is None or row.owner_username != username:
                    raise RoleAssetError("Run not found", status=404)
                control = await self._lock_control(session, row)
                await self._require_execution_owner(session, run_id, execution_id)
                if control.status == "cancelled":
                    raise RoleAssetError("Run was cancelled", status=409)
                permitted = control.status == "active"
                if permitted and (row.snapshot.get("initiator") or "").startswith(
                    API_KEY_USERNAME_PREFIX
                ):
                    await self._require_api_run_access(session, row)
                if not permitted and not row.state.get("paused_resume"):
                    row.state = {**row.state, "paused_resume": True}
                    session.add(row)
                await session.commit()
            if permitted:
                if "chat" in row.snapshot:
                    await self.chat.require_access(row.snapshot["chat"], username)
                if row.snapshot.get("engine") not in {
                    "native_task_v2",
                    "plugin_task_v2",
                }:
                    await self.roles.validate_frozen_role(
                        row.snapshot["role"], row.snapshot.get("profile")
                    )
                return
            await asyncio.sleep(0.1)

    async def control_run(self, username: str, run_id: str, payload: dict) -> dict:
        """Persist a whole-run decision before changing execution admission.

        Args:
            username: Authenticated owner.
            run_id: Root run identity; child scopes share this same protocol.
            payload: Request identity, action and optional observed revision.

        Returns:
            Current persisted progress with the whole-run decision.

        Raises:
            RoleAssetError: Invalid, stale, inaccessible or conflicting decision.
        """
        try:
            request = WorkflowControlRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError("Invalid run control") from exc
        identities = []
        resume_ids = []
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            if row is None or row.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            if row.snapshot.get("parent_run_id"):
                raise RoleAssetError("Control the whole run using its root", status=409)
            control = await self._lock_control(session, row)
            await session.refresh(row)
            family = await _descendant_runs(session, row)
            decision = request.model_dump()
            previous = control.requests.get(request.request_id)
            if previous is not None:
                if previous != decision:
                    raise RoleAssetError(
                        "Control request identity was reused", status=409
                    )
            else:
                if control.status == "cancelled" or row.status == "completed":
                    raise RoleAssetError("The run is already terminal", status=409)
                if (
                    request.expected_revision is not None
                    and request.expected_revision != control.revision
                ):
                    raise RoleAssetError(
                        "Run control changed; reload first", status=409
                    )
                control.status = {
                    "pause": "paused",
                    "continue": "active",
                    "cancel": "cancelled",
                }[request.action]
                control.revision += 1
                control.requests = {**control.requests, request.request_id: decision}
                session.add(control)
                session.add(
                    AgentWorkflowEvent(
                        run_id=run_id,
                        kind="run." + request.action,
                        data={
                            "request_id": request.request_id,
                            "revision": control.revision,
                        },
                    )
                )
                if request.action == "pause":
                    for identity in [item.run_id for item in family]:
                        child = await session.get(AgentWorkflowRun, identity)
                        assert child is not None
                        if child.status == "running" and (
                            "member" in child.snapshot
                            or child.snapshot.get("engine") == "plugin_task_v2"
                        ):
                            child.state = {**child.state, "paused_resume": True}
                            session.add(child)
                if request.action == "cancel":
                    loops = copy.deepcopy(row.state.get("loops", {}))
                    for loop in loops.values():
                        if loop["status"] not in {"passed", "exhausted", "skipped"}:
                            loop["status"] = "cancelled"
                            if loop["iterations"][-1]["status"] in {
                                "running",
                                "blocked",
                            }:
                                loop["iterations"][-1]["status"] = "cancelled"
                    if loops:
                        row.state = {**row.state, "loops": loops}
                        session.add(row)
                    identities = [item.run_id for item in family]
                    pending_todos = (
                        (
                            await session.execute(
                                select(AgentWorkflowTodo).where(
                                    col(AgentWorkflowTodo.task_run_id).in_(identities)
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    for todo in pending_todos:
                        if todo.data["status"] in {"pending", "answered"}:
                            todo.data = {**todo.data, "status": "cancelled"}
                            session.add(todo)
                    for identity in identities:
                        child = await session.get(AgentWorkflowRun, identity)
                        assert child is not None
                        if child.status not in {"completed", "skipped", "cancelled"}:
                            child.status = "cancelled"
                            session.add(child)
                            session.add(
                                AgentWorkflowEvent(
                                    run_id=identity,
                                    kind="task.cancelled",
                                    data={
                                        "attempt_id": child.state.get("attempt_id"),
                                        "request_id": request.request_id,
                                    },
                                )
                            )
                await session.commit()
            if request.action == "continue":
                for identity in [item.run_id for item in family]:
                    child = await session.get(AgentWorkflowRun, identity)
                    if (
                        child is not None
                        and child.status in {"interrupted", "failed", "blocked"}
                        and (
                            child.state.get("resolution_pending")
                            or child.status == "interrupted"
                            and (
                                child.state.get("paused_resume")
                                or child.state.get("retry_pending")
                            )
                        )
                    ):
                        resume_ids.append(identity)
        for identity in identities:
            from astrbot.core.utils.active_event_registry import active_event_registry

            child = await self.get_run(username, identity)
            member = child["snapshot"].get("member")
            if member:
                active_event_registry.request_agent_stop_all(
                    member["umo"], message_id=child["state"]["message_id"]
                )
            task = self._executions.get(identity)
            if task is not None:
                task.cancel()
        if request.action == "continue":
            for identity in resume_ids:
                try:
                    await self.recover_run(username, identity)
                except RoleAssetError:
                    # A local tool or capacity wait must not reject the saved
                    # whole-run decision or prevent independent tasks resuming.
                    pass
            if row.snapshot.get("engine") == "graph_v2":
                await self.advance_run(username, run_id)
            await self._resume_human_choices(username, run_id)
        return await self.get_run(username, run_id)

    async def retry_task(self, username: str, run_id: str, payload: dict) -> dict:
        """Create one new attempt while retaining the original failed evidence.

        Args:
            username: Authenticated owner.
            run_id: Failed task identity within a root run or workflow.
            payload: Idempotent request, observed attempt and original input policy.

        Returns:
            Current task with archived failure, fixed inputs and unchanged usage.

        Raises:
            RoleAssetError: Stale request, unsafe tool effects or non-failed task.
        """
        try:
            request = WorkflowRetryRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid task retry; select original inputs explicitly"
            ) from exc
        dispatch = False
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            if row is None or row.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            control = await self._lock_control(session, row)
            await session.refresh(row)
            decision = {
                "task_run_id": run_id,
                "action": "retry",
                **request.model_dump(),
            }
            previous = control.requests.get(request.request_id)
            if previous is not None:
                if previous != decision:
                    raise RoleAssetError(
                        "Control request identity was reused", status=409
                    )
                # Re-notify a saved attempt if its first dispatch was interrupted.
                dispatch = (
                    control.status == "active"
                    and row.state.get("previous_attempt_id") == request.attempt_id
                    and bool(row.state.get("retry_pending"))
                    and row.status in {"pending", "running"}
                )
            else:
                if control.status == "cancelled":
                    raise RoleAssetError("The run was cancelled", status=409)
                if row.state.get("debug_write_preview"):
                    raise RoleAssetError(
                        "This call is held at the debug write preview", status=409
                    )
                root = (
                    row
                    if control.run_id == run_id
                    else await session.get(AgentWorkflowRun, control.run_id)
                )
                if root is None or root.status in {"completed", "cancelled"}:
                    raise RoleAssetError("The run is already terminal", status=409)
                if row.snapshot.get("engine") in {
                    "graph_v2",
                    "human_task_v2",
                    "control_task_v2",
                } or not row.state.get("attempt_id"):
                    raise RoleAssetError(
                        "Select a failed executable task with a current attempt",
                        status=409,
                    )
                if row.snapshot.get("engine") in {
                    "team_call_v2",
                    "workflow_call_v2",
                } and row.state.get("child_scope_run_id"):
                    raise RoleAssetError(
                        "Retry the failed task inside the child scope", status=409
                    )
                if row.state.get("attempt_id") != request.attempt_id:
                    raise RoleAssetError("Attempt is no longer active", status=409)
                if row.pending_result is not None:
                    raise RoleAssetError(
                        "Recover the accepted result in the original attempt",
                        status=409,
                    )
                calls = (
                    await session.execute(
                        select(AgentWorkflowToolCall, AgentWorkflowToolRecovery.data)
                        .outerjoin(
                            AgentWorkflowToolRecovery,
                            col(AgentWorkflowToolRecovery.record_id)
                            == col(AgentWorkflowToolCall.record_id),
                        )
                        .where(
                            col(AgentWorkflowToolCall.run_id) == run_id,
                            col(AgentWorkflowToolCall.attempt_id) == request.attempt_id,
                            col(AgentWorkflowToolCall.status).in_(
                                ["started", "unknown", "result_saved", "applied"]
                            ),
                        )
                    )
                ).all()
                saved_tool_results = False
                for call, metadata in calls:
                    recovery = metadata or {}
                    if call.status in {"started", "unknown"}:
                        if not recovery.get("replacement_record_id"):
                            raise RoleAssetError(
                                "Resolve uncertain tool effects before retrying the task",
                                status=409,
                            )
                    elif call.raw_results or recovery.get("identity"):
                        # A new attempt would lose the saved result and use a
                        # different connector identity for any repeated call.
                        saved_tool_results = True
                if saved_tool_results:
                    raise RoleAssetError(
                        "This attempt has saved tool results. Continue the original attempt "
                        "if recovery is available.",
                        status=409,
                    )
                if row.status not in {"failed", "awaiting_result", "blocked"}:
                    raise RoleAssetError(
                        "Only the current failed task can be retried", status=409
                    )
                checkpoint = await session.get(AgentWorkflowCheckpoint, run_id)
                if (
                    checkpoint is not None
                    and checkpoint.attempt_id == request.attempt_id
                    and checkpoint.started_tools
                    and not checkpoint.data.get("tool_calls")
                ):
                    raise RoleAssetError(
                        "This checkpoint has unverified tool effects. Review the original "
                        "attempt before starting another run.",
                        status=409,
                    )
                session.add(
                    AgentWorkflowAttempt(
                        attempt_id=request.attempt_id,
                        run_id=run_id,
                        data={
                            "attempt_id": request.attempt_id,
                            "status": row.status,
                            "state": row.state,
                            "snapshot": row.snapshot,
                            "pending_result": row.pending_result,
                            "checkpoint": checkpoint.data if checkpoint else None,
                        },
                    )
                )
                if checkpoint is not None:
                    await session.delete(checkpoint)
                old_state = row.state
                row.state = {
                    "scope_id": old_state["scope_id"],
                    "task_id": old_state["task_id"],
                    "attempt_id": uuid.uuid4().hex,
                    "message_id": uuid.uuid4().hex,
                    "safe_to_finish": False,
                    "reason": None,
                    "response": None,
                    "previous_attempt_id": request.attempt_id,
                    "input_policy": "original",
                    "retry_pending": True,
                }
                row.status = (
                    "pending" if row.snapshot.get("parent_run_id") else "running"
                )
                session.add(row)
                parent_id = row.snapshot.get("parent_run_id")
                if parent_id:
                    parent = await session.get(AgentWorkflowRun, parent_id)
                    assert parent is not None
                    if parent.status == "blocked":
                        parent.status = "running"
                        parent.state = {**parent.state, "reason": None}
                        session.add(parent)
                    for identity in _current_task_runs(parent).values():
                        child = await session.get(AgentWorkflowRun, identity)
                        if child is not None and child.status == "dependency_failed":
                            child.status = "pending"
                            child.state = {**child.state, "reason": None}
                            session.add(child)
                control.requests = {**control.requests, request.request_id: decision}
                session.add(control)
                session.add(
                    AgentWorkflowEvent(
                        run_id=run_id,
                        kind="attempt.retried",
                        data={
                            "request_id": request.request_id,
                            "attempt_id": row.state["attempt_id"],
                            "previous_attempt_id": request.attempt_id,
                            "input_policy": "original",
                            "inputs": row.snapshot["inputs"],
                            "input_artifacts": row.snapshot.get("input_artifacts", []),
                        },
                    )
                )
                await session.commit()
                dispatch = True
        if dispatch:
            parent_id = row.snapshot.get("parent_run_id")
            if parent_id:
                await self.advance_run(username, parent_id)
            else:
                task = asyncio.create_task(self._execute(username, run_id))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        return await self.get_run(username, run_id)

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
        """Save an immutable observation and its deduplicated event atomically.

        Args:
            username: Original owner, checked against the persisted run.
            run_id: Task run producing the record.
            attempt_id: Actual execution attempt; stale writers are rejected.
            record_id: Stable delivery identity within this attempt.
            kind: Model delivery, delta, response, tool observation, or remote event.
            data: Original observation, never reconstructed from final display text.
            execution_id: Durable execution owner; late executors cannot append records.

        Returns:
            Durable record with its task, attempt and member associations.

        Raises:
            RoleAssetError: Invalid identity, inaccessible run or conflicting replay.
        """
        if (
            kind
            not in {
                "model_response",
                "model_delta",
                "model_delivery",
                "model_context",
                "tool_start",
                "tool_progress",
                "tool_status",
                "tool_result",
                "remote_event",
            }
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
                or run.status == "cancelled"
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

    async def get_run_history_page(
        self,
        username: str,
        run_id: str,
        task_run_id: str,
        kind: Literal["events", "messages", "usage", "attempts"],
        before: str | None,
        limit: int = 128,
    ) -> dict:
        """Page authorized event, message, attempt or usage history without changing execution.

        Args:
            username: Authenticated run owner.
            run_id: Observed root or scope.
            task_run_id: Selected execution or subtree within that scope.
            kind: History category.
            before: Exclusive event ID or usage timestamp and receipt identity.
            limit: Maximum number of entries.

        Returns:
            Ordered entries and the next exclusive boundary, if any.

        Raises:
            RoleAssetError: Invalid boundary or inaccessible execution.
        """
        if (
            kind not in {"events", "messages", "usage", "attempts"}
            or not 1 <= limit <= 128
        ):
            raise RoleAssetError("Invalid history page")
        async with self.db.get_db() as session:
            if self.db.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN"))
            root = await session.get(AgentWorkflowRun, run_id)
            if root is None or root.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            tree = await _descendant_runs(session, root)
            task = next((item for item in tree if item.run_id == task_run_id), None)
            if task is None:
                raise RoleAssetError("Task run not found", status=404)
            selected = {task_run_id}
            while True:
                expanded = selected | {
                    item.run_id
                    for item in tree
                    if item.snapshot.get("parent_run_id") in selected
                }
                if expanded == selected:
                    break
                selected = expanded
            if kind == "usage":
                query = select(AgentWorkflowUsage).where(
                    col(AgentWorkflowUsage.run_id).in_(selected)
                )
                if before is not None:
                    try:
                        stamp, identity = before.rsplit("|", 1)
                        stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                        if stamp.tzinfo is not None:
                            stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
                        if not identity:
                            raise ValueError
                    except ValueError:
                        raise RoleAssetError("Invalid usage cursor") from None
                    query = query.where(
                        or_(
                            col(AgentWorkflowUsage.created_at) < stamp,
                            (col(AgentWorkflowUsage.created_at) == stamp)
                            & (col(AgentWorkflowUsage.receipt_id) < identity),
                        )
                    )
                rows = (
                    (
                        await session.execute(
                            query.order_by(
                                col(AgentWorkflowUsage.created_at).desc(),
                                col(AgentWorkflowUsage.receipt_id).desc(),
                            ).limit(limit + 1)
                        )
                    )
                    .scalars()
                    .all()
                )
                more = len(rows) > limit
                rows = list(reversed(rows[:limit]))
                entries = [
                    {**row.model_dump(mode="json", exclude={"data"}), **row.data}
                    for row in rows
                ]
                return {
                    "items": entries,
                    "next_before": f"{entries[0]['created_at']}|{entries[0]['receipt_id']}"
                    if more
                    else None,
                }
            try:
                position = int(before) if before is not None else None
                if position is not None and position < 1:
                    raise ValueError
            except ValueError:
                raise RoleAssetError("Invalid event cursor") from None
            query = select(AgentWorkflowEvent)
            if kind == "events":
                query = query.where(col(AgentWorkflowEvent.run_id).in_(selected))
            elif kind == "attempts":
                query = query.where(
                    AgentWorkflowEvent.run_id == task_run_id,
                    AgentWorkflowEvent.kind == "attempt.retried",
                )
            else:
                member_id = task.snapshot.get("member", {}).get("member_id")
                family_root = await session.get(AgentWorkflowRun, _root_run_id(task))
                if family_root is None or family_root.owner_username != username:
                    raise RoleAssetError("Root run not found", status=404)
                family = await _descendant_runs(session, family_root)
                message_filter = (
                    col(AgentWorkflowEvent.run_id).in_(selected)
                    if task.snapshot.get("engine") == "graph_v2"
                    else or_(
                        AgentWorkflowEvent.run_id == task_run_id,
                        col(AgentWorkflowEvent.run_id).in_(
                            [item.run_id for item in family]
                        )
                        & (
                            AgentWorkflowEvent.data["scope_id"].as_string()
                            == task.state.get("scope_id")
                        )
                        & (
                            AgentWorkflowEvent.data["recipient_member_id"].as_string()
                            == member_id
                        ),
                    )
                )
                query = query.where(
                    AgentWorkflowEvent.kind == "member.message_sent", message_filter
                )
            if position is not None:
                query = query.where(col(AgentWorkflowEvent.id) < position)
            rows = (
                (
                    await session.execute(
                        query.order_by(col(AgentWorkflowEvent.id).desc()).limit(
                            limit + 1
                        )
                    )
                )
                .scalars()
                .all()
            )
            more = len(rows) > limit
            rows = list(reversed(rows[:limit]))
            if kind == "events":
                entries = [
                    {
                        "cursor": event.id,
                        "run_id": event.run_id,
                        "kind": event.kind,
                        "data": event.data,
                    }
                    for event in rows
                ]
            elif kind == "attempts":
                if any(not event.data.get("previous_attempt_id") for event in rows):
                    raise RoleAssetError(
                        "Attempt history is missing a record identity", status=409
                    )
                ids = [event.data["previous_attempt_id"] for event in rows]
                attempts = (
                    (
                        await session.execute(
                            select(AgentWorkflowAttempt).where(
                                col(AgentWorkflowAttempt.attempt_id).in_(ids),
                                AgentWorkflowAttempt.run_id == task_run_id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                by_id = {attempt.attempt_id: attempt for attempt in attempts}
                if any(identity not in by_id for identity in ids):
                    raise RoleAssetError(
                        "Attempt history is missing a persisted record", status=409
                    )
                entries = [
                    {
                        "attempt_id": event.data["previous_attempt_id"],
                        "cursor": event.id,
                        "status": by_id[event.data["previous_attempt_id"]].data[
                            "status"
                        ],
                        "state": {
                            "reason": by_id[event.data["previous_attempt_id"]]
                            .data.get("state", {})
                            .get("reason")
                        },
                        "snapshot": {
                            "inputs": by_id[event.data["previous_attempt_id"]]
                            .data.get("snapshot", {})
                            .get("inputs"),
                            "input_artifacts": by_id[event.data["previous_attempt_id"]]
                            .data.get("snapshot", {})
                            .get("input_artifacts", []),
                        },
                        "checkpoint": {
                            "stats": {
                                "token_usage": (
                                    (
                                        by_id[
                                            event.data["previous_attempt_id"]
                                        ].data.get("checkpoint")
                                        or {}
                                    ).get("stats")
                                    or {}
                                ).get("token_usage")
                            }
                        },
                    }
                    for event in rows
                ]
            else:
                if any(not event.data.get("message_id") for event in rows):
                    raise RoleAssetError(
                        "Message history is missing a record identity", status=409
                    )
                ids = [event.data["message_id"] for event in rows]
                messages = (
                    (
                        await session.execute(
                            select(AgentWorkflowMessage).where(
                                col(AgentWorkflowMessage.message_id).in_(ids)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                by_id = {message.message_id: message for message in messages}
                if any(identity not in by_id for identity in ids):
                    raise RoleAssetError(
                        "Message history is missing a persisted record", status=409
                    )
                entries = [
                    {
                        **by_id[event.data["message_id"]].model_dump(
                            mode="json", exclude={"payload"}
                        ),
                        "cursor": event.id,
                        **by_id[event.data["message_id"]].payload,
                    }
                    for event in rows
                ]
            return {"items": entries, "next_before": str(rows[0].id) if more else None}

    async def get_process_page(
        self,
        username: str,
        run_id: str,
        task_run_id: str,
        before: int,
        limit: int = 128,
    ) -> dict:
        """Read a bounded older page of one authorized task's persisted observations.

        Args:
            username: Authenticated run owner.
            run_id: Observed root or scope.
            task_run_id: Exact task execution within the observed subtree.
            before: Exclusive durable event cursor.
            limit: Maximum records to return.

        Returns:
            Ordered records and the exclusive cursor for the next older page.

        Raises:
            RoleAssetError: Invalid page boundary or inaccessible task.
        """
        if before < 1 or not 1 <= limit <= 128:
            raise RoleAssetError("Invalid process page")
        async with self.db.get_db() as session:
            if self.db.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN"))
            root = await session.get(AgentWorkflowRun, run_id)
            if root is None or root.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            tree = await _descendant_runs(session, root)
            task = next((item for item in tree if item.run_id == task_run_id), None)
            if task is None:
                raise RoleAssetError("Task run not found", status=404)
            events = (
                (
                    await session.execute(
                        select(AgentWorkflowEvent)
                        .where(
                            AgentWorkflowEvent.run_id == task_run_id,
                            col(AgentWorkflowEvent.kind).like("process.%"),
                            col(AgentWorkflowEvent.id) < before,
                        )
                        .order_by(col(AgentWorkflowEvent.id).desc())
                        .limit(limit + 1)
                    )
                )
                .scalars()
                .all()
            )
            has_more = len(events) > limit
            events = list(reversed(events[:limit]))
            if any(not event.data.get("record_id") for event in events):
                raise RoleAssetError(
                    "Process history is missing a record identity", status=409
                )
            ids = [event.data["record_id"] for event in events]
            records = (
                (
                    await session.execute(
                        select(AgentWorkflowProcess).where(
                            col(AgentWorkflowProcess.record_id).in_(ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_id = {record.record_id: record for record in records}
            if any(identity not in by_id for identity in ids):
                raise RoleAssetError(
                    "Process history is missing a persisted record", status=409
                )
            return {
                "records": [
                    {
                        **by_id[event.data["record_id"]].model_dump(mode="json"),
                        "cursor": event.id,
                        "root_run_id": _root_run_id(task),
                        "scope_id": task.state.get("scope_id"),
                        "task_run_id": task.run_id,
                        "node_id": task.snapshot.get("node_id"),
                    }
                    for event in events
                ],
                "next_before": events[0].id if has_more else None,
            }

    async def get_events(self, username: str, run_id: str, after: int = 0) -> dict:
        """Read a bounded batch of committed observations after a cursor.

        Args:
            username: Authenticated owner.
            run_id: Run or graph identity to observe.
            after: Last committed cursor applied by the client; zero requests a snapshot.

        Returns:
            A consistent initial or reset snapshot, or ordered incremental records.

        Raises:
            RoleAssetError: Run is inaccessible or the cursor is negative.
        """
        if after < 0:
            raise RoleAssetError("Cursor must not be negative")
        if after == 0:
            snapshot = await self.get_run(username, run_id, history_limit=128)
            return {
                "cursor": snapshot["cursor"],
                "reset": True,
                "events": [],
                "records": [],
                "messages": [],
                "snapshot": snapshot,
            }
        async with self.db.get_db() as session:
            if self.db.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN"))
            run = await session.get(AgentWorkflowRun, run_id)
            if run is None or run.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            descendants = await _descendant_runs(session, run)
            by_id = {item.run_id: item for item in descendants}
            high_water = (
                await session.execute(
                    select(func.max(AgentWorkflowEvent.id)).where(
                        col(AgentWorkflowEvent.run_id).in_(by_id)
                    )
                )
            ).scalar_one() or 0
            if after <= high_water:
                events = (
                    (
                        await session.execute(
                            select(AgentWorkflowEvent)
                            .where(
                                col(AgentWorkflowEvent.run_id).in_(by_id),
                                col(AgentWorkflowEvent.id) > after,
                            )
                            .order_by(col(AgentWorkflowEvent.id))
                            .limit(128)
                        )
                    )
                    .scalars()
                    .all()
                )
                if any(
                    event.kind.startswith("process.")
                    and not event.data.get("record_id")
                    for event in events
                ):
                    raise RoleAssetError(
                        "Process history is missing a record identity", status=409
                    )
                record_ids = [
                    event.data["record_id"]
                    for event in events
                    if event.kind.startswith("process.")
                ]
                records = []
                if record_ids:
                    rows = (
                        (
                            await session.execute(
                                select(AgentWorkflowProcess).where(
                                    col(AgentWorkflowProcess.record_id).in_(record_ids)
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    by_record = {row.record_id: row for row in rows}
                    if any(identity not in by_record for identity in record_ids):
                        raise RoleAssetError(
                            "Process history is missing a persisted record", status=409
                        )
                    records = [
                        {
                            **by_record[identity].model_dump(mode="json"),
                            "cursor": next(
                                event.id
                                for event in events
                                if event.data.get("record_id") == identity
                            ),
                            "root_run_id": _root_run_id(
                                by_id[by_record[identity].run_id]
                            ),
                            "scope_id": by_id[by_record[identity].run_id].state.get(
                                "scope_id"
                            ),
                            "task_run_id": by_record[identity].run_id,
                            "node_id": by_id[by_record[identity].run_id].snapshot.get(
                                "node_id"
                            ),
                        }
                        for identity in record_ids
                        if identity in by_record
                    ]
                message_events = [
                    event for event in events if event.kind == "member.message_sent"
                ]
                if any(not event.data.get("message_id") for event in message_events):
                    raise RoleAssetError(
                        "Message history is missing a record identity", status=409
                    )
                message_ids = [event.data["message_id"] for event in message_events]
                messages = (
                    (
                        await session.execute(
                            select(AgentWorkflowMessage).where(
                                col(AgentWorkflowMessage.message_id).in_(message_ids)
                            )
                        )
                    )
                    .scalars()
                    .all()
                    if message_ids
                    else []
                )
                by_message = {message.message_id: message for message in messages}
                if any(identity not in by_message for identity in message_ids):
                    raise RoleAssetError(
                        "Message history is missing a persisted record", status=409
                    )
                message_records = [
                    {
                        **by_message[event.data["message_id"]].model_dump(
                            mode="json", exclude={"payload"}
                        ),
                        "cursor": event.id,
                        **by_message[event.data["message_id"]].payload,
                    }
                    for event in message_events
                ]
                return {
                    "cursor": events[-1].id if events else after,
                    "reset": False,
                    "events": [
                        {
                            "cursor": event.id,
                            "run_id": event.run_id,
                            "kind": event.kind,
                            "data": event.data,
                        }
                        for event in events
                    ],
                    "records": records,
                    "messages": message_records,
                    "snapshot": None,
                }
        snapshot = await self.get_run(username, run_id, history_limit=128)
        return {
            "cursor": snapshot["cursor"],
            "reset": True,
            "events": [],
            "records": [],
            "messages": [],
            "snapshot": snapshot,
        }

    async def list_human_requests(self, username: str) -> dict:
        """List pending requests owned by the authenticated dashboard user.

        Args:
            username: Authenticated dashboard user.

        Returns:
            Persisted requests and their run control state.
        """
        async with self.db.get_db() as session:
            rows = (
                await session.execute(
                    select(AgentWorkflowTodo, AgentWorkflowRun)
                    .join(
                        AgentWorkflowRun,
                        AgentWorkflowTodo.root_run_id == AgentWorkflowRun.run_id,
                    )
                    .where(
                        AgentWorkflowRun.owner_username == username,
                        AgentWorkflowTodo.data["status"].as_string() == "pending",
                    )
                    .order_by(col(AgentWorkflowTodo.created_at).desc())
                )
            ).all()
            requests = []
            for todo, root in rows:
                if username not in todo.data.get("answerable_by", []):
                    continue
                control = await session.get(AgentWorkflowControl, root.run_id)
                if control and control.status == "cancelled":
                    continue
                requests.append(
                    {
                        "todo_id": todo.todo_id,
                        "root_run_id": todo.root_run_id,
                        "task_run_id": todo.task_run_id,
                        **todo.data,
                        "trial": bool(root.snapshot.get("trial")),
                        "control_status": control.status if control else "active",
                    }
                )
            return {"requests": requests}

    async def list_runs(
        self,
        username: str,
        limit: int = 50,
        *,
        summary_only: bool = False,
        include_artifacts: bool = False,
    ) -> dict:
        """List persisted runs, including inactive and interrupted executions.

        Args:
            username: Authenticated owner.
            limit: Maximum number of recent records, capped at 100.
            summary_only: Read card progress and artifact counts in batches.
            include_artifacts: Include selectable artifact versions and readable previews in compact results.

        Returns:
            Recent run overviews, without full configuration, results or history.
        """
        if summary_only:
            return await self._list_run_cards(username, limit, include_artifacts)
        async with self.db.get_db() as session:
            identities = (
                (
                    await session.execute(
                        select(AgentWorkflowRun.run_id)
                        .where(
                            AgentWorkflowRun.owner_username == username,
                            AgentWorkflowRun.snapshot["root_run_id"]
                            .as_string()
                            .is_not(None),
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

    async def _list_run_cards(
        self, username: str, limit: int, include_artifacts: bool
    ) -> dict:
        """Read one consistent page without loading frozen prompts, results or process history.

        Args:
            username: Owner of the root runs and their descendants.
            limit: Maximum number of recent roots.
            include_artifacts: Fetch versions and previews when an input picker opens.

        Returns:
            Workbench summaries including current task progress and exact artifact choices.
        """
        run = AgentWorkflowRun
        root = run.snapshot["root_run_id"].as_string()
        async with self.db.get_db() as session:
            await session.execute(text("BEGIN"))
            roots = (
                await session.execute(
                    sa_select(
                        run.run_id,
                        run.status,
                        run.snapshot["definition"]["name"].as_string().label("name"),
                        func.substr(run.snapshot["task"].as_string(), 1, 200).label(
                            "goal"
                        ),
                        run.snapshot["trial"]["mode"].as_string().label("trial_mode"),
                        run.snapshot["trial"]["draft_revision"]
                        .as_integer()
                        .label("revision"),
                        run.snapshot["debug_fork"]["source"]["root_run_id"]
                        .as_string()
                        .is_not(None)
                        .label("debug_fork"),
                        run.state["reason"].as_string().label("reason"),
                        AgentWorkflowControl.status.label("control"),
                    )
                    .outerjoin(
                        AgentWorkflowControl, AgentWorkflowControl.run_id == run.run_id
                    )
                    .where(
                        run.owner_username == username,
                        root.is_not(None),
                        run.snapshot["parent_run_id"].as_string().is_(None),
                    )
                    .order_by(col(run.created_at).desc(), run.run_id)
                    .limit(max(1, min(limit, 100)))
                )
            ).all()
            cards = {
                r.run_id: {
                    "run_id": r.run_id,
                    "status": r.status,
                    "snapshot": {
                        "definition": {"name": r.name},
                        "task": r.goal if r.goal is not None else r.name,
                        **({"debug_fork": True} if r.debug_fork else {}),
                        **(
                            {
                                "trial": {
                                    "mode": r.trial_mode,
                                    "draft_revision": r.revision,
                                }
                            }
                            if r.trial_mode
                            else {}
                        ),
                    },
                    "state": {"reason": r.reason} if r.reason else {},
                    "control": {"status": r.control or "active"},
                    "artifact_count": 0,
                    **({"artifacts": []} if include_artifacts else {}),
                    "progress": {
                        "total": 0,
                        "completed": 0,
                        "running": 0,
                        "waiting": 0,
                    },
                }
                for r in roots
            }
            if not cards:
                return {"runs": []}
            family = (
                await session.execute(
                    sa_select(
                        run.run_id,
                        root.label("root"),
                        run.status,
                        run.snapshot["engine"].as_string().label("engine"),
                        run.state["task_runs"].label("current_tasks"),
                        run.snapshot["task_runs"].label("initial_tasks"),
                        run.state["capacity_released"].as_boolean().label("released"),
                    ).where(run.owner_username == username, root.in_(list(cards)))
                )
            ).all()
            by_id = {r.run_id: r for r in family}
            for r in family:
                if r.engine == "graph_v2":
                    ids = (
                        r.current_tasks
                        if r.current_tasks is not None
                        else r.initial_tasks
                    ) or {}
                    tasks = (
                        by_id[i]
                        for i in ids.values()
                        if i in by_id and by_id[i].root == r.root
                    )
                elif r.run_id == r.root:
                    tasks = (r,)
                else:
                    continue
                progress = cards[r.root]["progress"]
                for task in tasks:
                    progress["total"] += 1
                    if task.status in {"completed", "skipped"}:
                        progress["completed"] += 1
                    elif task.status == "running" and not task.released:
                        progress["running"] += 1
                    elif task.status != "pending":
                        progress["waiting"] += 1
            artifact = AgentWorkflowArtifact
            if include_artifacts:
                records = await session.execute(
                    sa_select(
                        root, artifact.artifact_id, artifact.version, artifact.content
                    )
                    .select_from(artifact)
                    .join(run, run.run_id == artifact.run_id)
                    .where(run.owner_username == username, root.in_(list(cards)))
                    .order_by(artifact.artifact_id, artifact.version)
                )
                for root_id, identity, version, content in records:
                    cards[root_id]["artifacts"].append(
                        {
                            "artifact_id": identity,
                            "version": version,
                            "summary": json.dumps(content, ensure_ascii=False)[:200],
                        }
                    )
                    cards[root_id]["artifact_count"] += 1
            else:
                counts = await session.execute(
                    sa_select(root, func.count())
                    .select_from(artifact)
                    .join(run, run.run_id == artifact.run_id)
                    .where(run.owner_username == username, root.in_(list(cards)))
                    .group_by(root)
                )
                for root_id, count in counts:
                    cards[root_id]["artifact_count"] = count
        return {"runs": list(cards.values())}

    async def _compile_trial(
        self, username: str, asset_id: str, request: TrialRunRequest
    ) -> dict:
        """Compile a saved draft into a bounded trial scope without executing it.

        Args:
            username: Authenticated draft owner.
            asset_id: Draft identity.
            request: Revision and requested scope.

        Returns:
            The original draft, selected definition, and boundary requirements.

        Raises:
            RoleAssetError: The draft or requested scope is invalid.
        """
        draft = await self.roles.get_draft(username, request.kind, asset_id)
        if draft["revision"] != request.revision:
            raise RoleAssetError("Draft revision changed", status=409)
        original = copy.deepcopy(draft["body"].get("definition", {}))
        if request.kind == "autonomous":
            if request.mode != "full" or request.node_id is not None:
                raise RoleAssetError("Autonomous teams support full trials only")
            try:
                team = AutonomousDefinition.model_validate(original)
            except ValidationError as exc:
                raise RoleAssetError(
                    "Invalid autonomous draft",
                    [
                        {
                            "path": ".".join(map(str, item["loc"])),
                            "code": item["type"],
                            "message": item["msg"],
                        }
                        for item in exc.errors()
                    ],
                ) from exc
            return {
                "draft": draft,
                "definition": team.model_dump(),
                "selected_nodes": [],
                "required_inputs": team.required_inputs,
                "requirements": {},
                "external": {},
                "feedback": {},
            }
        full_trial = request.mode == "full"
        draft_result_id = None
        if not full_trial:
            # A bounded trial can run while the editor's output is unfinished.
            value = original.pop("result_task_id", None)
            if isinstance(value, str):
                draft_result_id = value.strip()
        try:
            graph = GraphDefinition.model_validate(original)
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid workflow draft",
                [
                    {
                        "path": ".".join(map(str, item["loc"])),
                        "code": item["type"],
                        "message": item["msg"],
                    }
                    for item in exc.errors()
                ],
            ) from exc
        tasks = {task.id: task for task in graph.tasks}
        if full_trial:
            if request.node_id is not None:
                raise RoleAssetError("Full trials do not select a start node")
            selected = set(tasks)
        else:
            if request.node_id not in tasks:
                raise RoleAssetError("Choose an existing start node")
            selected = {request.node_id}
            if request.mode == "partial":
                while True:
                    expanded = selected | {
                        edge.target for edge in graph.edges if edge.source in selected
                    }
                    if expanded == selected:
                        break
                    selected = expanded
                for edge in graph.edges:
                    if (
                        edge.target in selected
                        and edge.source not in selected
                        and edge.target != request.node_id
                    ):
                        raise RoleAssetError(
                            "Partial scope requires an omitted parallel predecessor",
                            [
                                {
                                    "path": "definition.edges",
                                    "code": "SCOPE",
                                    "message": f"{edge.source} -> {edge.target}",
                                }
                            ],
                        )
        validate_graph(
            graph,
            result_requirement="reachable" if full_trial else "optional",
            check_structured_shapes=full_trial,
            text_port_scope=None if full_trial else selected,
        )
        feedback = {}
        loops = []
        for loop in graph.loops:
            overlap = set(loop.body) & selected
            if overlap and request.mode == "single":
                for item in loop.feedback:
                    if item.target == request.node_id:
                        feedback[item.input] = {
                            "task_id": item.source,
                            "field": item.field,
                        }
            elif overlap and overlap != set(loop.body):
                raise RoleAssetError("Partial trials must include complete loops")
            elif overlap:
                loops.append(loop)
        if not full_trial and any(
            task.control
            and task.control.kind == "join"
            and not any(
                edge.target == task.id and edge.source in selected
                for edge in graph.edges
            )
            for task in graph.tasks
            if task.id in selected
        ):
            raise RoleAssetError("An isolated ALL join cannot assume upstream success")
        chosen = [task for task in graph.tasks if task.id in selected]
        external = {}
        requirements: dict[str, dict] = {}
        required_inputs = set()
        for task in chosen:
            refs = dict(task.inputs)
            node_contract = task_input_contract(
                task.input_contract, task.transform, dispatch=bool(task.dispatch)
            )
            if task.id == request.node_id:
                refs.update(
                    {
                        name: TaskInput.model_validate(ref)
                        for name, ref in feedback.items()
                    }
                )
            for input_name, ref in refs.items():
                if ref.input is not None:
                    required_inputs.add(ref.input)
                elif ref.task_id is not None and (
                    ref.task_id not in selected
                    or (task.id == request.node_id and input_name in feedback)
                ):
                    source = tasks[ref.task_id]
                    contract = source.result_contract(
                        explicit_control=graph.entry_task_ids is not None
                    )
                    requirement = requirements.setdefault(
                        source.id,
                        {
                            "name": source.name,
                            "fields": {},
                            "pinned_only": False,
                            "optional": True,
                            "contract": contract,
                        },
                    )
                    requirement["optional"] &= (
                        graph.port_contract_version == 2
                        and task.id == request.node_id
                        and input_name in feedback
                        and not port_requires_value(node_contract, input_name)
                    )
                    source_schema = field_schema(
                        contract,
                        ref.field,
                        structured=graph.port_contract_version == 2,
                    )
                    if source_schema.get("format") in REFERENCE_OUTPUTS.values():
                        requirement["pinned_only"] = True
                    requirement["fields"][ref.field] = source_schema["type"]
                    if source.id not in selected:
                        external[source.id] = source
            if task.human:
                for source_id, fields in (
                    (task.human.artifact_task_id, []),
                    (
                        task.human.recommendation_task_id,
                        [
                            task.human.recommendation_value_field,
                            task.human.recommendation_reason_field,
                        ],
                    ),
                ):
                    if source_id and source_id not in selected:
                        source = tasks[source_id]
                        external[source_id] = source
                        requirement = requirements.setdefault(
                            source_id,
                            {
                                "name": source.name,
                                "fields": {},
                                "pinned_only": False,
                                "contract": source.output_contract,
                            },
                        )
                        requirement["pinned_only"] = True
                        requirement["optional"] = False
                        for name in fields:
                            requirement["fields"][name] = field_schema(
                                source.output_contract, name
                            )["type"]
        selected_definition = graph.model_dump()
        selected_definition["tasks"] = [task.model_dump() for task in chosen]
        selected_definition["edges"] = [
            edge.model_dump()
            for edge in graph.edges
            if edge.source in selected and edge.target in selected
        ]
        selected_definition["loops"] = [loop.model_dump() for loop in loops]
        if not full_trial and graph.entry_task_ids is not None:
            selected_definition["entry_task_ids"] = [request.node_id]
        selected_inputs = [
            name
            for name in graph.required_inputs
            if (full_trial and graph.port_contract_version) or name in required_inputs
        ]
        input_contract = None
        if graph.port_contract_version == 2:
            input_contract = graph.input_contract
            # Root-level constraints can depend on fields outside the selected nodes.
            # Keep their full input form when they cannot be projected safely.
            if not full_trial and input_contract.keys() <= {
                "type",
                "properties",
                "required",
                "additionalProperties",
                "title",
                "description",
                "$schema",
                "$comment",
            }:
                input_contract = {
                    **input_contract,
                    "properties": {
                        name: schema
                        for name, schema in input_contract["properties"].items()
                        if name in required_inputs
                    },
                    "required": [
                        name
                        for name in input_contract.get("required", [])
                        if name in required_inputs
                    ],
                }
            selected_definition["input_contract"] = input_contract
        if not full_trial:
            result = (
                tasks[draft_result_id]
                if draft_result_id is not None and draft_result_id in selected
                else None
            )
            if result is None:
                result = chosen[0] if request.mode == "single" else None
            selected_definition["result_task_id"] = (
                result.id
                if result is not None
                and (
                    result.control is None
                    or (
                        graph.entry_task_ids is not None
                        and result.control.kind == "join"
                    )
                )
                else None
            )
        compiled = GraphDefinition.model_validate(selected_definition)
        feedback_inputs = (
            {
                request.node_id: {
                    name: TaskInput.model_validate(ref)
                    for name, ref in feedback.items()
                }
            }
            if feedback
            else {}
        )
        validate_graph(
            compiled,
            external_sources=external,
            feedback_inputs=feedback_inputs,
            text_port_scope=None if full_trial else selected,
        )
        return {
            "draft": draft,
            "definition": compiled.model_dump(),
            "selected_nodes": [task.id for task in chosen],
            "required_inputs": selected_inputs,
            **(
                {"input_contract": input_contract} if input_contract is not None else {}
            ),
            "requirements": requirements,
            "external": external,
            "feedback": feedback,
        }

    async def preview_trial(self, username: str, asset_id: str, payload: dict) -> dict:
        """Return the server-computed scope and inputs for a draft trial.

        Args:
            username: Authenticated draft owner.
            asset_id: Draft identity.
            payload: Trial scope and revision.

        Returns:
            Selected nodes and required boundary data.

        Raises:
            RoleAssetError: The draft or requested scope is invalid.
        """
        try:
            request = TrialRunRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid trial request",
                [
                    {
                        "path": ".".join(map(str, issue["loc"])),
                        "code": issue["type"],
                        "message": issue["msg"],
                    }
                    for issue in exc.errors()
                ],
            ) from exc
        compiled = await self._compile_trial(username, asset_id, request)
        if request.kind == "workflow":
            for index, node in enumerate(
                compiled["draft"]["body"]["definition"]["tasks"]
            ):
                if node["id"] in compiled["selected_nodes"] and node.get("component"):
                    await self._resolve_component(
                        username, node, f"definition.tasks.{index}"
                    )
        return {
            key: compiled[key]
            for key in (
                "selected_nodes",
                "required_inputs",
                "requirements",
                "input_contract",
            )
            if key in compiled
        }

    async def start_trial(self, username: str, asset_id: str, payload: dict) -> dict:
        """Freeze and start a draft trial under the ordinary graph scheduler.

        Args:
            username: Authenticated draft owner.
            asset_id: Draft identity.
            payload: Scope, revision, workflow inputs and exact boundary sources.

        Returns:
            The independent persisted run.

        Raises:
            RoleAssetError: Scope, sources, resources or revision are invalid.
        """
        try:
            request = TrialRunRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid trial request",
                [
                    {
                        "path": ".".join(map(str, issue["loc"])),
                        "code": issue["type"],
                        "message": issue["msg"],
                    }
                    for issue in exc.errors()
                ],
            ) from exc
        compiled = await self._compile_trial(username, asset_id, request)
        required = set(compiled["required_inputs"])
        issues = [
            {
                "path": f"inputs.{name}",
                "code": "REQUIRED",
                "message": "Provide non-empty text",
            }
            for name in required
            if not isinstance(request.inputs.get(name), str)
            or not request.inputs[name].strip()
        ]
        issues += [
            {
                "path": f"inputs.{name}",
                "code": "UNKNOWN",
                "message": "Input is not required by this scope",
            }
            for name in set(request.inputs) - required
        ]
        input_contract = compiled.get("input_contract")
        if input_contract is not None:
            request.inputs = normalize_port_inputs(
                input_contract, request.inputs, "inputs"
            )
            issues = []
        expected_sources = set(compiled["requirements"])
        issues += [
            {
                "path": f"sources.{name}",
                "code": "REQUIRED",
                "message": "Provide this boundary source",
            }
            for name in expected_sources - set(request.sources)
            if not compiled["requirements"][name].get("optional")
        ]
        issues += [
            {
                "path": f"sources.{name}",
                "code": "UNKNOWN",
                "message": "Source is not required by this scope",
            }
            for name in set(request.sources) - expected_sources
        ]
        if issues:
            raise RoleAssetError("Invalid trial inputs", issues)
        sources = {}
        boundary_paths: dict[tuple[str, str], list[str]] = {}
        required_paths: set[str] = set()
        if input_contract is not None:
            for index, task in enumerate(
                compiled["draft"]["body"]["definition"]["tasks"]
            ):
                if task["id"] in compiled["selected_nodes"]:
                    node_contract = task_input_contract(
                        task.get("input_contract"),
                        task.get("transform"),
                        dispatch=bool(task.get("dispatch")),
                    )
                    bindings = dict(task.get("inputs", {}))
                    if request.node_id == task["id"]:
                        bindings.update(compiled["feedback"])
                    for name, binding in bindings.items():
                        path = f"definition.tasks.{index}.inputs.{name}"
                        boundary_paths.setdefault(
                            (binding.get("task_id"), binding.get("field", "text")), []
                        ).append(path)
                        if node_contract and port_requires_value(node_contract, name):
                            required_paths.add(path)
        for source_id, source in request.sources.items():
            requirement = compiled["requirements"][source_id]
            if requirement["pinned_only"] and source.artifact is None:
                message = "Select an exact artifact version for this boundary source"
                raise RoleAssetError(
                    message,
                    [
                        {
                            "path": f"sources.{source_id}.artifact",
                            "code": "REQUIRED",
                            "message": message,
                        }
                    ],
                )
            reference = source.artifact.model_dump() if source.artifact else None
            try:
                artifact = (
                    await self.read_artifact(username, **reference)
                    if reference
                    else None
                )
            except RoleAssetError as exc:
                paths = [
                    path
                    for (task_id, _), bindings in boundary_paths.items()
                    if source_id == task_id
                    for path in bindings
                ] or [f"sources.{source_id}.artifact"]
                raise RoleAssetError(
                    str(exc),
                    [
                        {"path": path, "code": "REFERENCE", "message": str(exc)}
                        for path in paths
                    ],
                    status=exc.status,
                ) from exc
            content = artifact["content"] if artifact else source.content
            issues = validate_result(
                requirement["contract"],
                content,
                port_formats=input_contract is not None,
            )
            if issues:
                fields = []
                for issue in issues:
                    original_path = issue["path"].removeprefix("result.")
                    consumers = [
                        {**issue, "path": path + original_path.removeprefix(field)}
                        for (task_id, field), bindings in boundary_paths.items()
                        if task_id == source_id
                        and (
                            original_path == field
                            or original_path.startswith(field + ".")
                        )
                        for path in bindings
                    ]
                    fields.extend(
                        consumers
                        or [{**issue, "path": f"sources.{source_id}." + issue["path"]}]
                    )
                raise RoleAssetError("Invalid boundary source", fields)
            if input_contract is not None:
                projection = {}
                reference_fields = {
                    field: schema["format"]
                    for field in requirement["fields"]
                    if (
                        schema := field_schema(
                            requirement["contract"], field, structured=True
                        )
                    ).get("format")
                    in REFERENCE_OUTPUTS.values()
                }
                async with self.db.get_db() as session:
                    historical_properties = {}
                    if reference and reference_fields.keys() - REFERENCE_OUTPUTS.keys():
                        stored = await session.get(
                            AgentWorkflowArtifact,
                            (reference["artifact_id"], reference["version"]),
                        )
                        origin = (
                            await session.get(AgentWorkflowRun, stored.run_id)
                            if stored
                            else None
                        )
                        if origin and origin.snapshot.get("port_contract_version") == 2:
                            historical_properties = (stored.contract or {}).get(
                                "properties", {}
                            )
                    for field in requirement["fields"]:
                        value = port_value(content, field, reference)
                        paths = boundary_paths.get(
                            (source_id, field), [f"sources.{source_id}.artifact"]
                        )
                        if value is MISSING_PORT_VALUE:
                            if field == "$file" and any(
                                path in required_paths for path in paths
                            ):
                                message = "The selected version must contain one managed attachment"
                                raise RoleAssetError(
                                    message,
                                    [
                                        {
                                            "path": path,
                                            "code": "REFERENCE",
                                            "message": message,
                                        }
                                        for path in paths
                                        if path in required_paths
                                    ],
                                )
                            continue
                        if field in reference_fields:
                            if field not in REFERENCE_OUTPUTS:
                                schema = historical_properties.get(field)
                                if (
                                    not isinstance(schema, dict)
                                    or schema.get("format") != reference_fields[field]
                                ):
                                    message = "The selected version did not publish this field as a managed reference"
                                    raise RoleAssetError(
                                        message,
                                        [
                                            {
                                                "path": path,
                                                "code": "INCOMPATIBLE",
                                                "message": message,
                                            }
                                            for path in paths
                                        ],
                                    )
                            await self._require_port_reference(
                                session, username, value, paths[0]
                            )
                        target = projection
                        parts = field.split(".")
                        for part in parts[:-1]:
                            target = target.setdefault(part, {})
                        target[parts[-1]] = value
                content = projection
            sources[source_id] = {
                "content": copy.deepcopy(content),
                "artifact": reference,
                **({"projected": True} if input_contract is not None else {}),
            }
        asset = {
            "asset_id": asset_id,
            "version": None,
            "definition": compiled["definition"],
        }
        start = WorkflowStartRequest(
            version=1, inputs=request.inputs, source="dashboard"
        )
        feedback_inputs = (
            {
                request.node_id: {
                    name: TaskInput.model_validate(ref)
                    for name, ref in compiled["feedback"].items()
                }
            }
            if compiled["feedback"]
            else {}
        )
        if request.kind == "workflow":
            for index, node in enumerate(
                compiled["draft"]["body"]["definition"]["tasks"]
            ):
                if node["id"] not in compiled["selected_nodes"]:
                    continue
                if node.get("component"):
                    await self._resolve_component(
                        username, node, f"definition.tasks.{index}"
                    )
                elif node.get("knowledge_base_id"):
                    await self._require_native_knowledge_base(
                        username,
                        node["knowledge_base_id"],
                        f"definition.tasks.{index}.knowledge_base_id",
                    )
        frozen = await self._freeze_graph(
            username,
            asset,
            start,
            external_sources=compiled["external"],
            feedback_inputs=feedback_inputs,
            trial=True,
        )
        max_depth = frozen["definition"].get("max_depth", 3)
        for child in frozen["child_assets"].values():
            for member in [
                *child["members"].values(),
                *child.get("allowed_roles", {}).values(),
            ]:
                limit = member["effective"].get("max_depth")
                if max_depth < 1 or limit is not None and limit < 1:
                    raise RoleAssetError("Nested team exceeds the frozen depth limit")
        frozen["trial"] = {
            "draft_revision": request.revision,
            "draft": copy.deepcopy(compiled["draft"]["body"]),
            "mode": request.mode,
            "node_id": request.node_id,
            "selected_nodes": compiled["selected_nodes"],
            "sources": sources,
            "feedback": compiled["feedback"],
        }
        async with self.db.get_db() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            current = await session.get(AgentAssetDraft, (request.kind, asset_id))
            if current is None or current.owner_username != username:
                raise RoleAssetError("Draft not found", status=404)
            if current.revision != request.revision:
                raise RoleAssetError("Draft revision changed", status=409)
            if await session.get(AgentAssetTombstone, ("workflow", asset_id)):
                raise RoleAssetError("Asset has been deleted", status=409)
            run_id = await self._create_scope(session, username, frozen)
            await session.commit()
        await self.advance_run(username, run_id)
        return await self.get_run(username, run_id)

    async def _start_graph(
        self,
        username: str,
        asset: dict,
        request: WorkflowStartRequest,
        *,
        chat: dict | None = None,
        api_key_id: str | None = None,
        launch: WorkflowLaunchIdentity | None = None,
        rerun_key: tuple[str, str] | None = None,
    ) -> dict:
        """Freeze all referenced resources before atomically starting a root scope.

        Args:
            username: Authenticated owner.
            asset: Saved immutable asset version.
            request: Validated launch inputs and source.
            chat: Verified platform caller, when launched from explicit chat.
            api_key_id: Authenticated API key identity, when invoked by the API.
            launch: Source request receipt to commit with the new root.
            rerun_key: Completed source run and request ID to commit with the new run.

        Returns:
            Persisted run and its initial tasks.

        Raises:
            RoleAssetError: A pinned definition, limit or resource is unavailable.
        """
        external = chat is not None or api_key_id is not None
        frozen = await self._freeze_graph(username, asset, request, external=external)
        if external:
            self.chat.prepare(
                frozen,
                chat,
                f"{API_KEY_USERNAME_PREFIX}{api_key_id}"
                if api_key_id is not None
                else None,
            )
        if api_key_id is not None:
            frozen["api_key_id"] = api_key_id
        max_depth = frozen["definition"].get("max_depth", 3)
        for child in frozen["child_assets"].values():
            for member in [
                *child["members"].values(),
                *child.get("allowed_roles", {}).values(),
            ]:
                limit = member["effective"].get("max_depth")
                if max_depth < 1 or limit is not None and limit < 1:
                    raise RoleAssetError("Nested team exceeds the frozen depth limit")
        async with self.db.get_db() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            receipt = await self._find_launch(
                session,
                username,
                asset["asset_id"],
                request.version,
                launch,
                chat,
                api_key_id,
            )
            if rerun_key is not None:
                receipt = await session.get(AgentWorkflowRerun, rerun_key)
            if receipt is not None:
                run_id = receipt.run_id
            else:
                if await session.get(
                    AgentAssetTombstone, ("workflow", asset["asset_id"])
                ):
                    raise RoleAssetError("Asset has been deleted", status=409)
                run_id = await self._create_scope(session, username, frozen)
                if launch is not None:
                    assert launch.fingerprint is not None
                    session.add(
                        AgentWorkflowLaunchReceipt(
                            principal=launch.principal,
                            request_id=launch.request_id,
                            fingerprint=launch.fingerprint,
                            run_id=run_id,
                        )
                    )
                if rerun_key is not None:
                    session.add(
                        AgentWorkflowRerun(
                            source_run_id=rerun_key[0],
                            request_id=rerun_key[1],
                            run_id=run_id,
                        )
                    )
                await session.commit()
        await self.advance_run(username, run_id)
        return await self.get_run(username, run_id)

    async def _freeze_graph(
        self,
        username: str,
        asset: dict,
        request: WorkflowStartRequest,
        nesting_depth: int = 0,
        depth_limit: int | None = None,
        *,
        external_sources: dict[str, GraphTask] | None = None,
        feedback_inputs: dict[str, dict[str, TaskInput]] | None = None,
        trial: bool = False,
        external: bool = False,
    ) -> dict:
        """Resolve a complete graph and its pinned child resources before any write.

        Args:
            username: Actual initiating owner used by every role and asset lookup.
            asset: Exact saved asset version.
            request: Declared inputs and source for this scope.
            nesting_depth: Reference traversal bounded by the supported nesting ceiling.
            depth_limit: Ancestor and caller ceiling for nested assets.
            external_sources: Trusted trial boundary contracts for this scope.
            feedback_inputs: Trusted initial feedback for a single-node trial.
            trial: Whether this is a draft trial root.
            external: Enforce the external source boundary before resolving resources.

        Returns:
            Frozen scope template; member identities are assigned at scope creation.

        Raises:
            RoleAssetError: A definition, contract, resource, depth or instance limit fails.
        """
        if nesting_depth > 20:
            raise RoleAssetError("Nested references exceed the supported depth limit")
        if depth_limit is not None and nesting_depth > depth_limit:
            raise RoleAssetError("Nested references exceed the declared depth limit")
        if external:
            self.chat.require_external_definition(asset["definition"])
        team = (
            asset["definition"]
            if asset["definition"].get("kind") == "autonomous"
            else None
        )
        allowed_roles = {}
        if team:
            for index, role in enumerate(team["allowed_roles"]):
                frozen = await self._freeze_member(
                    username,
                    role,
                    asset=asset,
                    role_path=f"definition.allowed_roles.{index}.role_id",
                )
                frozen.pop("member")
                frozen["preset_id"] = role["id"]
                allowed_roles[role["id"]] = frozen
            definition = GraphDefinition.model_validate(
                {
                    "name": team["name"],
                    "default_provider_id": team.get("default_provider_id"),
                    "required_inputs": team["required_inputs"],
                    "max_parallel": team["max_parallel"],
                    "max_depth": team.get("max_depth", 3),
                    "limits": team.get("limits"),
                    "tasks": [
                        {
                            "id": "coordinator",
                            "name": team["name"],
                            "member": "coordinator",
                            "task": "{goal}",
                            "inputs": {
                                name: {"input": name}
                                for name in team["required_inputs"]
                            },
                            **{
                                k: v
                                for k, v in team["coordinator"].items()
                                if k != "id"
                            },
                            "output_contract": team.get("output_contract"),
                        }
                    ],
                }
            )
        elif "tasks" in asset["definition"]:
            definition = GraphDefinition.model_validate(asset["definition"])
        else:
            raise RoleAssetError("Legacy workflow definitions are not supported")
        if team and team.get("approval_question"):
            definition = GraphDefinition.model_validate(
                {
                    **definition.model_dump(),
                    "tasks": [
                        *definition.model_dump()["tasks"],
                        {
                            "id": "approval",
                            "name": team["approval_question"][:128],
                            "human": {
                                "kind": "approval",
                                "question": team["approval_question"],
                                "artifact_task_id": "coordinator",
                                "artifact_version": 1,
                            },
                        },
                    ],
                    "edges": [{"source": "coordinator", "target": "approval"}],
                }
            )
        validate_graph(
            definition,
            result_requirement=(
                "optional"
                if team or trial
                else "reachable"
                if definition.port_contract_version
                else "required"
            ),
            external_sources=external_sources,
            feedback_inputs=feedback_inputs,
            text_port_scope={task.id for task in definition.tasks} if trial else None,
        )
        members = {}
        role_counts: dict[tuple, int] = {}
        for index, task in enumerate(definition.tasks):
            if task.component:
                await self._resolve_component(
                    username, task.model_dump(), f"definition.tasks.{index}"
                )
            elif task.decision_provider_id:
                await self._require_decision_provider(
                    username,
                    task.decision_provider_id,
                    f"definition.tasks.{index}.decision_provider_id",
                )
            elif task.knowledge_base_id and not trial:
                await self._require_native_knowledge_base(
                    username,
                    task.knowledge_base_id,
                    f"definition.tasks.{index}.knowledge_base_id",
                )
            if (
                task.human is not None
                or task.team is not None
                or task.workflow is not None
                or task.control is not None
                or task.transform is not None
            ):
                continue
            if task.member not in members:
                try:
                    frozen = await self._freeze_member(
                        username,
                        task.model_dump(exclude_none=True),
                        asset=asset,
                        role_path="definition.coordinator.role_id"
                        if team
                        else f"definition.tasks.{index}.role_id",
                    )
                except RoleAssetError as exc:
                    if team:
                        raise
                    fields = []
                    for issue in exc.fields:
                        path = issue["path"]
                        if (
                            path.startswith("definition.")
                            and path != "definition.default_provider_id"
                            and not path.startswith(f"definition.tasks.{index}.")
                        ):
                            path = (
                                f"definition.tasks.{index}.role."
                                + path.removeprefix("definition.")
                            )
                        elif path.startswith(("installation.", "overrides.")):
                            path = f"definition.tasks.{index}." + path
                        fields.append({**issue, "path": path})
                    if not fields and exc.status == 404 and task.role_id:
                        fields.append(
                            {
                                "path": f"definition.tasks.{index}.role_id",
                                "code": "REFERENCE",
                                "message": "Choose an accessible exact role version",
                            }
                        )
                    raise RoleAssetError(str(exc), fields, exc.status) from exc
                if team and task.id == "coordinator":
                    frozen["preset_id"] = team["coordinator"]["id"]
                if (
                    team
                    and task.id == "coordinator"
                    and "task_tools"
                    not in frozen["effective"]["runner"]["capabilities"]
                ):
                    raise RoleAssetError(
                        "Coordinator runner cannot execute team management tools",
                        [
                            {
                                "path": "definition.coordinator",
                                "code": "INCOMPATIBLE",
                                "message": f"{frozen['effective']['runner']['label']} does not support task_tools",
                            }
                        ],
                    )
                role_key = (
                    ("role", task.role_id, task.role_version)
                    if task.role_id
                    else ("member", task.member)
                )
                role_counts[role_key] = role_counts.get(role_key, 0) + 1
                members[task.member] = frozen
        for member_name, frozen in members.items():
            role_id = frozen["role"]["role_id"]
            role_key = (
                ("role", role_id, frozen["role"]["version"])
                if role_id
                else ("member", member_name)
            )
            limit = frozen["effective"].get("max_instances")
            if limit is not None and role_counts[role_key] > limit:
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
        for frozen in [*members.values(), *allowed_roles.values()]:
            limit = frozen["effective"].get("max_depth")
            if depth_limit is not None and limit is not None and nesting_depth > limit:
                raise RoleAssetError(
                    "Nested member exceeds the effective role depth limit",
                    [
                        {
                            "path": "definition",
                            "code": "LIMIT",
                            "message": f"Role {frozen['role']['role_id']} permits depth {limit}",
                        }
                    ],
                )
        for index, loop in enumerate(definition.loops):
            for task in definition.tasks:
                if (
                    task.id not in loop.body
                    or task.human is not None
                    or task.team is not None
                    or task.workflow is not None
                    or task.control is not None
                    or task.transform is not None
                ):
                    continue
                limit = members[task.member]["effective"].get("max_iterations")
                if limit is not None and loop.max_iterations > limit:
                    raise RoleAssetError(
                        "Loop exceeds the effective member iteration limit",
                        [
                            {
                                "path": f"definition.loops.{index}.max_iterations",
                                "code": "LIMIT",
                                "message": f"Member {task.member} permits at most {limit} iterations",
                            }
                        ],
                    )
        child_assets = {}
        for task_index, task in enumerate(definition.tasks):
            call = task.team or task.workflow
            if call is None:
                continue
            path = f"definition.tasks.{task_index}"
            kind = "team" if task.team else "workflow"
            try:
                child = await self.get_asset(username, call.asset_id, call.version)
            except RoleAssetError as exc:
                raise RoleAssetError(
                    "Pinned child version is not accessible",
                    [
                        {
                            "path": f"{path}.{kind}",
                            "code": "REFERENCE",
                            "message": "Choose an accessible exact asset version",
                        }
                    ],
                    status=exc.status,
                ) from exc
            fields = pinned_child_call_issues(
                definition.port_contract_version, task, child["definition"], path
            )
            if fields:
                raise RoleAssetError(
                    "Child call contracts no longer match the pinned asset", fields
                )
            try:
                child_assets[task.id] = await self._freeze_graph(
                    username,
                    child,
                    WorkflowStartRequest(version=call.version, source=request.source),
                    nesting_depth + 1,
                    min(depth_limit, definition.max_depth)
                    if depth_limit is not None
                    else None,
                    external=external,
                )
            except RoleAssetError as exc:
                raise RoleAssetError(
                    str(exc),
                    [
                        {
                            **issue,
                            "path": f"{path}.{kind}."
                            + issue["path"].removeprefix("definition."),
                        }
                        for issue in exc.fields
                    ],
                    exc.status,
                ) from exc
        allowed_workflows = {}
        for index, ref in enumerate(team.get("allowed_workflows", []) if team else []):
            child = await self.get_asset(username, ref["asset_id"], ref["version"])
            if child["definition"].get("kind") == "autonomous":
                raise RoleAssetError("Choose a pinned saved workflow")
            if child["definition"].get("port_contract_version") == 2:
                raise RoleAssetError(
                    "Structured-port workflows require a static V2 call node"
                )
            try:
                frozen = await self._freeze_graph(
                    username,
                    child,
                    WorkflowStartRequest(version=ref["version"], source=request.source),
                    nesting_depth + 1,
                    min(
                        depth_limit,
                        definition.max_depth,
                        members["coordinator"]["effective"].get("max_depth") or 20,
                    )
                    if depth_limit is not None
                    else None,
                    external=external,
                )
            except RoleAssetError as exc:
                raise RoleAssetError(
                    str(exc),
                    [
                        {
                            **issue,
                            "path": f"definition.allowed_workflows.{index}."
                            + issue["path"].removeprefix("definition."),
                        }
                        for issue in exc.fields
                    ],
                    exc.status,
                ) from exc
            allowed_workflows[f"{ref['asset_id']}:{ref['version']}"] = {
                **frozen,
                "result_task_id": frozen["definition"]["result_task_id"],
            }
        if definition.limits:
            remote_members = [*members.values(), *allowed_roles.values()]
            scopes = [*child_assets.values(), *allowed_workflows.values()]
            while scopes:
                scope = scopes.pop()
                remote_members.extend(
                    [
                        *scope["members"].values(),
                        *scope.get("allowed_roles", {}).values(),
                    ]
                )
                scopes.extend(
                    [
                        *scope.get("child_assets", {}).values(),
                        *scope.get("allowed_workflows", {}).values(),
                    ]
                )
            if any(
                member["effective"]["runner"]["type"] != "local"
                for member in remote_members
            ):
                for limit in ("token_budget", "max_output_tokens"):
                    if getattr(definition.limits, limit) is not None:
                        raise RoleAssetError(
                            "Remote executor cannot enforce this model limit",
                            [
                                {
                                    "path": "definition.limits." + limit,
                                    "code": "INCOMPATIBLE",
                                    "message": f"A remote executor in this scope does not provide model-level {limit} enforcement",
                                }
                            ],
                        )
        if definition.port_contract_version == 2 and not nesting_depth:
            references = {
                name: value
                for name, value in request.inputs.items()
                if definition.input_contract["properties"][name].get("format")
                in REFERENCE_OUTPUTS.values()
            }
            if references:
                async with self.db.get_db() as session:
                    for name, value in references.items():
                        await self._require_port_reference(
                            session, username, value, f"inputs.{name}"
                        )
        return {
            "engine": "graph_v2",
            "usage_protocol": 1,
            "asset_id": asset["asset_id"],
            "asset_version": asset["version"],
            "definition": definition.model_dump(),
            **(
                {
                    "team_definition": team,
                    "allowed_roles": allowed_roles,
                    "allowed_workflows": allowed_workflows,
                }
                if team
                else {}
            ),
            "inputs": request.inputs,
            "input_artifacts": [ref.model_dump() for ref in request.input_artifacts],
            "source": request.source,
            "initiator": username,
            "members": members,
            "child_assets": child_assets,
        }

    async def _create_dispatch_scope(
        self,
        session: AsyncSession,
        root: AgentWorkflowRun,
        row: AgentWorkflowRun,
        task: dict,
        items: list,
    ) -> str:
        """Persist bounded item tasks and their ordered join in the owning root run.

        Args:
            session: Transaction holding the root admission lock.
            root: Scope whose frozen definition owns the dispatch node.
            row: Waiting dispatch node and its frozen Agent configuration.
            task: Dispatch node from the frozen graph.
            items: Validated list, in declared aggregation order.

        Returns:
            The new execution scope containing one task per item.
        """
        item_ids = [f"item-{index}" for index in range(len(items))]
        member = root.snapshot["members"][task["member"]]
        member_slots = [
            f"worker-{index}"
            for index in range(
                min(
                    len(items),
                    member["effective"].get("max_instances") or len(items),
                    task["dispatch"]["max_parallel"],
                )
            )
        ]
        item_names = [f" #{index + 1}" for index in range(len(items))]
        input_names = [f"item_{index}" for index in range(len(items))]
        item_inputs = {
            name: value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False)
            for name, value in zip(input_names, items, strict=True)
        }
        frozen = {
            "engine": "graph_v2",
            "usage_protocol": 1,
            "asset_id": root.snapshot["asset_id"],
            "asset_version": root.snapshot["asset_version"],
            "definition": {
                "name": task["name"],
                "port_contract_version": 2,
                "input_contract": {
                    "type": "object",
                    "properties": {name: {"type": "string"} for name in input_names},
                    "required": input_names,
                },
                "entry_task_ids": item_ids,
                "max_parallel": task["dispatch"]["max_parallel"],
                "result_task_id": "collect",
                "tasks": [
                    {
                        key: task[key]
                        for key in (
                            "task",
                            "role_id",
                            "role_version",
                            "role",
                            "installation",
                            "overrides",
                        )
                    }
                    | {
                        "id": item_id,
                        "name": task["name"][: 128 - len(item_names[index])]
                        + item_names[index],
                        "member": member_slots[index % len(member_slots)],
                        "inputs": {"item": {"input": input_names[index]}},
                    }
                    for index, item_id in enumerate(item_ids)
                ]
                + [
                    {
                        "id": "collect",
                        "name": "Collect results",
                        "control": {"kind": "join"},
                    }
                ],
                "edges": [
                    {"source": item_id, "target": "collect"} for item_id in item_ids
                ],
            },
            "inputs": item_inputs,
            "input_artifacts": [],
            "source": root.snapshot["source"],
            "initiator": root.snapshot["initiator"],
            "members": {
                slot: {
                    **copy.deepcopy(member),
                    "dispatch_member_id": row.snapshot["member"]["member_id"],
                }
                for slot in member_slots
            },
            "dispatch_scope": True,
        }
        definition = GraphDefinition.model_validate(frozen["definition"])
        validate_graph(definition, result_requirement="reachable")
        frozen["definition"] = definition.model_dump()
        return await self._create_scope(
            session,
            row.owner_username,
            frozen,
            root_run_id=_root_run_id(root),
            parent_task=row,
            depth=root.snapshot["depth"],
        )

    async def _create_scope(
        self,
        session: AsyncSession,
        username: str,
        frozen: dict,
        *,
        root_run_id: str | None = None,
        parent_task: AgentWorkflowRun | None = None,
        depth: int = 0,
    ) -> str:
        """Create a scope and its task identities in the existing admission transaction.

        Args:
            session: Transaction holding root admission for a nested call.
            username: Frozen initiating owner.
            frozen: Resolved launch template, including exact role resources.
            root_run_id: Whole-run control and capacity identity for a child scope.
            parent_task: Calling task whose durable wait is committed with this scope.
            depth: Bounded call depth; root scopes have depth zero.

        Returns:
            New scope run identity, never dispatched before the caller commits.
        """
        definition = GraphDefinition.model_validate(frozen["definition"])
        run_id = uuid.uuid4().hex
        scope_id = uuid.uuid4().hex
        task_runs = {task.id: uuid.uuid4().hex for task in definition.tasks}
        snapshot = copy.deepcopy(frozen)
        snapshot["task_runs"] = task_runs
        snapshot["root_run_id"] = root_run_id or run_id
        snapshot["depth"] = depth
        snapshot["scope_limits"] = [
            *(parent_task.snapshot.get("scope_limits", []) if parent_task else []),
            {
                "run_id": run_id,
                "max_parallel": definition.max_parallel,
                "max_depth": definition.max_depth,
                "limits": definition.limits.model_dump(exclude_none=True)
                if definition.limits
                else {},
            },
        ]
        if parent_task is not None:
            snapshot["parent_run_id"] = parent_task.run_id
            snapshot["parent_scope_id"] = parent_task.state["scope_id"]
        members = snapshot["members"]
        for frozen_member in members.values():
            member = frozen_member["member"]
            frozen_member["member"] = {
                key: value
                for key, value in member.items()
                if key not in {"conversation_id", "session_id", "umo"}
            }
            frozen_member["member"]["member_id"] = uuid.uuid4().hex
            if snapshot.get("team_definition"):
                session_id = uuid.uuid4().hex
                frozen_member["member"].update(
                    conversation_id=str(uuid.uuid4()),
                    session_id=session_id,
                    umo=f"webchat:FriendMessage:{session_id}",
                )
        new_scope = AgentWorkflowRun(
            run_id=run_id, owner_username=username, snapshot=snapshot
        )
        await self._check_member_limits(session, new_scope)
        if parent_task is None and "chat" in snapshot:
            session.add(AgentWorkflowChatDelivery(run_id=run_id))
        session.add(
            AgentWorkflowRun(
                run_id=run_id,
                owner_username=username,
                snapshot=snapshot,
                state={
                    "scope_id": scope_id,
                    "reason": None,
                    "loops": {
                        loop.id: {
                            "status": "running",
                            "iterations": [
                                {
                                    "iteration": 1,
                                    "status": "running",
                                    "task_runs": {
                                        node_id: task_runs[node_id]
                                        for node_id in loop.body
                                    },
                                }
                            ],
                        }
                        for loop in definition.loops
                    },
                },
            )
        )
        session.add(AgentWorkflowSchedule(run_id=run_id))
        for frozen in members.values() if snapshot.get("team_definition") else []:
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
            task_member = None
            if not snapshot.get("team_definition") and not (
                task.human
                or task.team
                or task.workflow
                or task.control
                or task.transform
            ):
                task_member = copy.deepcopy(members[task.member])
                session_id = uuid.uuid4().hex
                task_member["member"] = {
                    **task_member["member"],
                    "conversation_id": str(uuid.uuid4()),
                    "session_id": session_id,
                    "umo": f"webchat:FriendMessage:{session_id}",
                }
                member = task_member["member"]
                session.add(
                    ConversationV2(
                        conversation_id=member["conversation_id"],
                        platform_id="webchat",
                        user_id=member["umo"],
                        content=[],
                        title=task.name,
                        persona_id=task_member["effective"].get("persona_id"),
                    )
                )
                session.add(
                    PlatformSession(
                        session_id=session_id,
                        creator=username,
                        display_name=task.name,
                        archived=1,
                    )
                )
            child_definition = {
                "name": task.name,
                "role_id": task.role_id,
                "role_version": task.role_version,
                "task": task.task,
                "overrides": task.overrides.model_dump(exclude_unset=True),
                "required_inputs": definition.required_inputs,
            }
            if task.decision:
                child_definition["decision"] = task.decision.model_dump()
            if task.component:
                child_definition.update(
                    component=task.component.model_dump(),
                    input_contract=task.input_contract,
                    knowledge_base_id=task.knowledge_base_id,
                )
            contract = task.result_contract(
                explicit_control=definition.entry_task_ids is not None
            )
            if contract is not None:
                child_definition["output_contract"] = contract
            session.add(
                AgentWorkflowRun(
                    run_id=task_runs[task.id],
                    owner_username=username,
                    status="pending",
                    snapshot={
                        "engine": "workflow_call_v2"
                        if task.workflow
                        else "team_call_v2"
                        if task.team
                        else "human_task_v2"
                        if task.human
                        else "control_task_v2"
                        if task.control or task.dispatch
                        else "native_task_v2"
                        if task.transform in NATIVE_NODE_OUTPUTS
                        else "plugin_task_v2"
                        if task.component
                        else "transform_task_v2"
                        if task.transform
                        else "single_task_v2",
                        "parent_run_id": run_id,
                        "usage_protocol": 1,
                        "root_run_id": snapshot["root_run_id"],
                        "depth": depth,
                        "scope_limits": snapshot["scope_limits"],
                        "node_id": task.id,
                        **(
                            {"port_contract_version": 2}
                            if definition.port_contract_version == 2
                            else {}
                        ),
                        **(
                            {"graph_task": True}
                            if not snapshot.get("team_definition")
                            else {}
                        ),
                        **(
                            {"coordinator": True}
                            if snapshot.get("team_definition")
                            and task.id == "coordinator"
                            else {}
                        ),
                        **next(
                            (
                                {"loop_id": loop.id, "iteration": 1}
                                for loop in definition.loops
                                if task.id in loop.body
                            ),
                            {},
                        ),
                        "asset_id": snapshot["asset_id"],
                        "asset_version": snapshot["asset_version"],
                        "definition": child_definition,
                        **(
                            {"human": task.human.model_dump()}
                            if task.human is not None
                            else {"team": task.team.model_dump()}
                            if task.team is not None
                            else {"workflow": task.workflow.model_dump()}
                            if task.workflow is not None
                            else {"control": task.control.model_dump()}
                            if task.control is not None
                            else {
                                "transform": task.transform,
                                **(
                                    {"decision_provider_id": task.decision_provider_id}
                                    if task.decision_provider_id
                                    else {}
                                ),
                                **(
                                    {"knowledge_base_id": task.knowledge_base_id}
                                    if task.knowledge_base_id
                                    else {}
                                ),
                            }
                            if task.transform
                            else task_member or members[task.member]
                        ),
                        "inputs": snapshot["inputs"],
                        "input_artifacts": snapshot["input_artifacts"],
                        "source": snapshot["source"],
                        "initiator": snapshot.get("initiator", username),
                        **({"chat": snapshot["chat"]} if "chat" in snapshot else {}),
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
                run_id=run_id,
                kind="scope.started" if parent_task is not None else "run.started",
                data={
                    "task_runs": task_runs,
                    "scope_id": scope_id,
                    "root_run_id": snapshot["root_run_id"],
                    "parent_run_id": snapshot.get("parent_run_id"),
                },
            )
        )
        await session.flush()
        return run_id

    async def _advance_loops(
        self, session: AsyncSession, root: AgentWorkflowRun, rows: dict
    ) -> bool:
        """Commit an exit or a fresh round under the existing admission lock.

        Args:
            session: Scheduler transaction holding the root control/revision lock.
            root: Frozen graph with mutable progress stored separately in state.
            rows: Current task instances; replacements join this scheduling pass.

        Returns:
            Whether loop progress changed. External work waits for the commit.
        """
        loops = copy.deepcopy(root.state.get("loops", {}))
        current_runs = dict(_current_task_runs(root))
        for definition in root.snapshot["definition"].get("loops", []):
            loop = loops[definition["id"]]
            if loop["status"] in {"passed", "exhausted", "skipped"}:
                continue
            iteration = loop["iterations"][-1]
            body = [rows[node_id] for node_id in definition["body"]]
            # A wholly unselected first round is neutral at downstream merges.
            if iteration["iteration"] == 1 and all(
                row.status == "skipped" for row in body
            ):
                loop["status"] = iteration["status"] = "skipped"
                session.add(
                    AgentWorkflowEvent(
                        run_id=root.run_id,
                        kind="loop.skipped",
                        data={"loop_id": definition["id"], "iteration": 1},
                    )
                )
                continue
            if any(
                row.status in {"pending", "running", "waiting_human", "waiting_child"}
                for row in body
            ):
                loop["status"] = iteration["status"] = "running"
                loop.pop("reason", None)
                continue
            if (
                any(row.status not in {"completed", "skipped"} for row in body)
                or rows[definition["exit_task_id"]].status != "completed"
            ):
                loop["status"] = iteration["status"] = "blocked"
                loop["reason"] = "Loop tasks or exit did not complete"
                continue
            if iteration.get("exit_reference") and loop["status"] == "blocked":
                continue
            loop.pop("reason", None)
            rule = definition["exit_condition"]
            source = rows[definition["exit_task_id"]]
            value = source.result
            for name in rule["field"].split("."):
                value = value[name]
            passed = matches_condition(
                cast(str | bool | int | float | None, value), rule
            )
            iteration["exit_reference"] = source.state.get("child_result_ref") or {
                "artifact_id": source.run_id,
                "version": 1,
            }
            iteration["exit_value"] = value
            iteration["status"] = "passed" if passed else "rejected"
            exhausted = iteration["iteration"] >= definition["max_iterations"]
            scope = root
            while True:
                ceiling = (scope.snapshot["definition"].get("limits") or {}).get(
                    "max_iterations"
                )
                if (
                    ceiling is not None
                    and iteration["iteration"] >= ceiling
                    and not passed
                ):
                    exhausted = True
                    loop["limit_stop"] = {
                        "limit": "max_iterations",
                        "scope_run_id": scope.run_id,
                        "value": ceiling,
                    }
                    loop["reason"] = "Execution limit reached: max_iterations"
                parent_id = scope.snapshot.get("parent_run_id")
                if not parent_id:
                    break
                scope = await session.get(AgentWorkflowRun, parent_id)
                assert scope is not None
            loop["status"] = (
                "passed" if passed else "exhausted" if exhausted else "running"
            )
            if exhausted and not passed and not loop.get("reason"):
                loop["reason"] = (
                    "Loop maximum iterations reached without satisfying the exit condition"
                )
            session.add(
                AgentWorkflowEvent(
                    run_id=root.run_id,
                    kind="loop.evaluated",
                    data={
                        "loop_id": definition["id"],
                        "iteration": iteration["iteration"],
                        "status": loop["status"],
                        "exit_reference": iteration["exit_reference"],
                        "exit_value": value,
                    },
                )
            )
            if passed or exhausted:
                continue
            feedback_inputs: dict[str, dict] = {}
            missing_feedback = []
            for feedback in definition["feedback"]:
                previous = rows[feedback["source"]]
                reference = previous.state.get("child_result_ref") or {
                    "artifact_id": previous.run_id,
                    "version": 1,
                }
                value = (
                    port_value(previous.result, feedback["field"], reference)
                    if previous.status == "completed" and previous.result is not None
                    else MISSING_PORT_VALUE
                )
                if value is MISSING_PORT_VALUE:
                    target = next(
                        task
                        for task in root.snapshot["definition"]["tasks"]
                        if task["id"] == feedback["target"]
                    )
                    contract = task_input_contract(
                        target.get("input_contract"),
                        target.get("transform"),
                        dispatch=bool(target.get("dispatch")),
                    )
                    if port_requires_value(contract, feedback["input"]):
                        missing_feedback.append(feedback)
                    else:
                        feedback_inputs.setdefault(feedback["target"], {})[
                            feedback["input"]
                        ] = {"missing": True}
                    continue
                feedback_inputs.setdefault(feedback["target"], {})[
                    feedback["input"]
                ] = {
                    "raw_value": value,
                    "value": value
                    if isinstance(value, str)
                    else json.dumps(value, ensure_ascii=False),
                    "reference": reference,
                    "field": feedback["field"],
                }
            if missing_feedback:
                loop["status"] = iteration["status"] = "blocked"
                loop["reason"] = (
                    "Next-round feedback has no validated result: "
                    + ", ".join(
                        f"{binding['source']}.{binding['field']} → {binding['target']}.{binding['input']}"
                        for binding in missing_feedback
                    )
                )
                continue
            next_runs = {}
            for node_id in definition["body"]:
                previous = rows[node_id]
                identity = uuid.uuid4().hex
                task = next(
                    task
                    for task in root.snapshot["definition"]["tasks"]
                    if task["id"] == node_id
                )
                snapshot = {
                    **copy.deepcopy(previous.snapshot),
                    "iteration": iteration["iteration"] + 1,
                    "inputs": root.snapshot["inputs"],
                    "input_artifacts": root.snapshot.get("input_artifacts", []),
                    "task": task["task"],
                    "iteration_inputs": feedback_inputs.get(node_id, {}),
                }
                for key in (
                    "input_reads",
                    "port_sources",
                    "selected_fields",
                    "trial_input_sources",
                ):
                    snapshot.pop(key, None)
                if snapshot.get("graph_task") and snapshot.get("member"):
                    session_id = uuid.uuid4().hex
                    snapshot["member"].update(
                        conversation_id=str(uuid.uuid4()),
                        session_id=session_id,
                        umo=f"webchat:FriendMessage:{session_id}",
                    )
                    member = snapshot["member"]
                    session.add(
                        ConversationV2(
                            conversation_id=member["conversation_id"],
                            platform_id="webchat",
                            user_id=member["umo"],
                            content=[],
                            title=task["name"],
                            persona_id=snapshot["effective"].get("persona_id"),
                        )
                    )
                    session.add(
                        PlatformSession(
                            session_id=session_id,
                            creator=root.owner_username,
                            display_name=task["name"],
                            archived=1,
                        )
                    )
                child = AgentWorkflowRun(
                    run_id=identity,
                    owner_username=root.owner_username,
                    status="pending",
                    snapshot=snapshot,
                    state={
                        "scope_id": root.state["scope_id"],
                        "task_id": identity,
                        "attempt_id": uuid.uuid4().hex,
                        "message_id": uuid.uuid4().hex,
                        "safe_to_finish": False,
                        "reason": None,
                        "response": None,
                    },
                )
                session.add(child)
                rows[node_id] = child
                next_runs[node_id] = identity
                current_runs[node_id] = identity
            loop["iterations"].append(
                {
                    "iteration": iteration["iteration"] + 1,
                    "status": "running",
                    "task_runs": next_runs,
                }
            )
            session.add(
                AgentWorkflowEvent(
                    run_id=root.run_id,
                    kind="loop.started",
                    data={
                        "loop_id": definition["id"],
                        "iteration": iteration["iteration"] + 1,
                        "task_runs": next_runs,
                    },
                )
            )
        changed = loops != root.state.get("loops", {})
        if changed:
            root.state = {**root.state, "loops": loops, "task_runs": current_runs}
            session.add(root)
        return changed

    async def communicate_member(
        self,
        username: str,
        run_id: str,
        attempt_id: str,
        execution_id: str,
        payload: dict,
    ) -> dict:
        """Persist a permitted message without altering scheduling or task results.

        Args:
            username: Authenticated initiating owner.
            run_id: Calling task, which determines member and scope authority.
            attempt_id: Active original attempt.
            execution_id: Current executor fence, checked before deduplication.
            payload: Recipient, literal message and exact visible artifact versions.

        Returns:
            Scoped member identities or a stable committed delivery receipt.

        Raises:
            RoleAssetError: Stale caller, inaccessible recipient/artifact or reused identity.
        """
        try:
            request = MemberCommunicationRequest.model_validate(payload)
            refs = [
                ArtifactReference.model_validate(ref).model_dump()
                for ref in request.artifact_refs
            ]
        except ValidationError as exc:
            raise RoleAssetError("Invalid member communication", status=400) from exc
        async with self.db.get_db() as session:
            caller = await session.get(AgentWorkflowRun, run_id)
            if caller is None or caller.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            control = await self._lock_control(session, caller)
            await session.refresh(caller)
            await self._require_execution_owner(session, run_id, execution_id)
            if (
                caller.state.get("attempt_id") != attempt_id
                or caller.pending_result is not None
                or control.status != "active"
            ):
                raise RoleAssetError(
                    "Communication requires the active task attempt", status=409
                )
            root_id = caller.snapshot.get("parent_run_id")
            root = await session.get(AgentWorkflowRun, root_id) if root_id else None
            if (
                root is None
                or not root.snapshot.get("team_definition")
                or caller.state["scope_id"] != root.state["scope_id"]
            ):
                raise RoleAssetError(
                    "Communication requires a shared execution scope", status=403
                )
            root_id = root.run_id
            members = {
                frozen["member"]["member_id"]: frozen
                for frozen in {
                    **root.snapshot["members"],
                    **root.state.get("members", {}),
                }.values()
            }
            sender_id = caller.snapshot["member"]["member_id"]
            if sender_id not in members:
                raise RoleAssetError("Caller is not a member of this scope", status=403)
            await self.roles.validate_frozen_role(
                caller.snapshot["role"], caller.snapshot.get("profile")
            )
            if request.action == "inspect":
                return {
                    "scope_id": caller.state["scope_id"],
                    "members": [
                        {
                            "member_id": identity,
                            "name": frozen["member"]["name"],
                            "role_id": frozen["role"]["role_id"],
                        }
                        for identity, frozen in members.items()
                    ],
                }
            if (
                not request.recipient_member_id
                or request.recipient_member_id not in members
                or not request.text
            ):
                raise RoleAssetError(
                    "Choose a member in the same scope and nonempty text", status=403
                )
            await self.roles.validate_frozen_role(
                members[request.recipient_member_id]["role"],
                members[request.recipient_member_id].get("profile"),
            )
            received = (
                (
                    await session.execute(
                        select(AgentWorkflowMessage).where(
                            AgentWorkflowMessage.root_run_id == _root_run_id(caller),
                            AgentWorkflowMessage.scope_id == caller.state["scope_id"],
                            AgentWorkflowMessage.recipient_member_id == sender_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for ref in refs:
                artifact = await session.get(
                    AgentWorkflowArtifact, (ref["artifact_id"], ref["version"])
                )
                origin = (
                    await session.get(AgentWorkflowRun, artifact.run_id)
                    if artifact
                    else None
                )
                scoped_output = (
                    origin is not None
                    and origin.owner_username == username
                    and origin.snapshot.get("parent_run_id") == root_id
                    and origin.state.get("scope_id") == caller.state["scope_id"]
                )
                if artifact is None or not (
                    scoped_output
                    and origin is not None
                    and (
                        caller.snapshot.get("coordinator")
                        or origin.snapshot.get("member", {}).get("member_id")
                        == sender_id
                        or ref in caller.snapshot.get("input_artifacts", [])
                        or any(
                            ref in message.payload["artifact_refs"]
                            for message in received
                        )
                    )
                    or ref in caller.snapshot.get("input_artifacts", [])
                    and ref in root.snapshot.get("input_artifacts", [])
                ):
                    raise RoleAssetError(
                        "Artifact version is outside the sender or recipient visibility",
                        status=403,
                    )
            content = {"text": request.text, "artifact_refs": refs}
            previous = (
                await session.execute(
                    select(AgentWorkflowMessage).where(
                        AgentWorkflowMessage.root_run_id == _root_run_id(caller),
                        AgentWorkflowMessage.scope_id == caller.state["scope_id"],
                        AgentWorkflowMessage.sender_member_id == sender_id,
                        AgentWorkflowMessage.request_id == request.request_id,
                    )
                )
            ).scalar_one_or_none()
            if previous is not None:
                if (
                    previous.recipient_member_id != request.recipient_member_id
                    or previous.payload != content
                ):
                    raise RoleAssetError(
                        "Message identity already belongs to another delivery",
                        status=409,
                    )
                return {"message_id": previous.message_id}
            message = AgentWorkflowMessage(
                message_id=uuid.uuid4().hex,
                root_run_id=_root_run_id(caller),
                scope_id=caller.state["scope_id"],
                sender_member_id=sender_id,
                recipient_member_id=request.recipient_member_id,
                sender_task_run_id=run_id,
                sender_attempt_id=attempt_id,
                request_id=request.request_id,
                payload=content,
            )
            session.add(message)
            session.add(
                AgentWorkflowEvent(
                    run_id=run_id,
                    kind="member.message_sent",
                    data={
                        "message_id": message.message_id,
                        "recipient_member_id": request.recipient_member_id,
                        "scope_id": caller.state["scope_id"],
                        "attempt_id": attempt_id,
                    },
                )
            )
            message_root_id = _root_run_id(caller)
            message_scope_id = caller.state["scope_id"]
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                previous = (
                    await session.execute(
                        select(AgentWorkflowMessage).where(
                            AgentWorkflowMessage.root_run_id == message_root_id,
                            AgentWorkflowMessage.scope_id == message_scope_id,
                            AgentWorkflowMessage.sender_member_id == sender_id,
                            AgentWorkflowMessage.request_id == request.request_id,
                        )
                    )
                ).scalar_one_or_none()
                if previous is None:
                    raise
                if (
                    previous.recipient_member_id != request.recipient_member_id
                    or previous.payload != content
                ):
                    raise RoleAssetError(
                        "Message identity already belongs to another delivery",
                        status=409,
                    ) from None
                return {"message_id": previous.message_id}
            return {"message_id": message.message_id}

    async def pending_member_messages(
        self,
        username: str,
        run_id: str,
        attempt_id: str,
        execution_id: str,
    ) -> list[dict]:
        """Read pending deliveries at the member runner's serialized safe point.

        Args:
            username: Authenticated initiating owner.
            run_id: Receiving task with the member's current execution ownership.
            attempt_id: Active original attempt.
            execution_id: Current executor fence.

        Returns:
            Unconsumed messages after global continuation permits receiving;
            consumption only commits with their model context.

        Raises:
            RoleAssetError: Stale execution, stale attempt or global cancellation.
        """
        while True:
            async with self.db.get_db() as session:
                caller = await session.get(AgentWorkflowRun, run_id)
                if caller is None or caller.owner_username != username:
                    raise RoleAssetError("Run not found", status=404)
                control = await self._lock_control(session, caller)
                await session.refresh(caller)
                await self._require_execution_owner(session, run_id, execution_id)
                if (
                    caller.state.get("attempt_id") != attempt_id
                    or control.status == "cancelled"
                ):
                    raise RoleAssetError(
                        "Message consumption requires active execution", status=409
                    )
                if control.status == "active":
                    rows = (
                        (
                            await session.execute(
                                select(AgentWorkflowMessage)
                                .where(
                                    AgentWorkflowMessage.root_run_id
                                    == _root_run_id(caller),
                                    AgentWorkflowMessage.scope_id
                                    == caller.state["scope_id"],
                                    AgentWorkflowMessage.recipient_member_id
                                    == caller.snapshot["member"]["member_id"],
                                    col(AgentWorkflowMessage.consumption).is_(None),
                                )
                                .order_by(
                                    col(AgentWorkflowMessage.created_at),
                                    col(AgentWorkflowMessage.message_id),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    return [
                        {
                            **message.model_dump(
                                mode="json", exclude={"payload", "consumption"}
                            ),
                            **message.payload,
                        }
                        for message in rows
                    ]
            # Pause can arrive after the runner's previous admission. Release the
            # transaction before parking, then recheck control and ownership.
            await self._await_execution_permission(username, run_id, execution_id)

    async def manage_team(
        self,
        username: str,
        run_id: str,
        attempt_id: str,
        execution_id: str,
        payload: dict,
    ) -> dict:
        """Apply an authorized coordinator decision using shared task persistence.

        Args:
            username: Frozen initiating owner.
            run_id: Calling coordinator task, never a caller-selected root authority.
            attempt_id: Original active task attempt.
            execution_id: Durable executor fence.
            payload: Idempotent member creation, task dispatch, wait or inspection.

        Returns:
            Committed operation result or complete member/task observations.

        Raises:
            RoleAssetError: Unauthorized, stale, invalid or over-limit decision.
        """
        try:
            request = TeamOperationRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid team operation",
                [
                    {
                        "path": ".".join(map(str, item["loc"])),
                        "code": item["type"],
                        "message": item["msg"],
                    }
                    for item in exc.errors()
                ],
            ) from exc
        decision = request.model_dump(exclude_none=True)
        result = None
        async with self.db.get_db() as session:
            caller = await session.get(AgentWorkflowRun, run_id)
            if caller is None or caller.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            if (
                not caller.snapshot.get("coordinator")
                or caller.state.get("attempt_id") != attempt_id
            ):
                raise RoleAssetError(
                    "Only the active coordinator can manage this team", status=403
                )
            control = await self._lock_control(session, caller)
            await session.refresh(caller)
            await self._require_execution_owner(session, run_id, execution_id)
            if control.status != "active" or caller.pending_result is not None:
                raise RoleAssetError(
                    "Team decisions require active execution before submission",
                    status=409,
                )
            root = await session.get(AgentWorkflowRun, caller.snapshot["parent_run_id"])
            assert root is not None
            state = copy.deepcopy(root.state)
            operations = state.setdefault("team_operations", {})
            prior = operations.get(request.request_id)
            if prior:
                if prior["decision"] != decision:
                    raise RoleAssetError(
                        "Request identity already belongs to another decision",
                        status=409,
                    )
                result = prior.get("result")
            team = root.snapshot["team_definition"]
            members = {**root.snapshot["members"], **state.get("members", {})}
            tasks = _current_task_runs(root)
            member_tasks = {task["id"] for task in state.get("dynamic_tasks", [])}
            if result is None and request.action == "create_member":
                if (
                    not request.name
                    or request.preset_id not in root.snapshot["allowed_roles"]
                ):
                    raise RoleAssetError(
                        "Choose a named instance of an allowed role",
                        [
                            {
                                "path": "preset_id",
                                "code": "FORBIDDEN",
                                "message": "Role is not allowed by the frozen asset",
                            }
                        ],
                    )
                frozen = copy.deepcopy(
                    root.snapshot["allowed_roles"][request.preset_id]
                )
                await self.roles.validate_frozen_role(
                    frozen["role"], frozen.get("profile")
                )
                count = sum(
                    (item["role"]["role_id"], item["role"]["version"])
                    == (frozen["role"]["role_id"], frozen["role"]["version"])
                    if frozen["role"]["role_id"]
                    else item.get("preset_id") == request.preset_id
                    for item in members.values()
                )
                limit = frozen["effective"].get("max_instances")
                if (
                    len(members) >= team["max_members"]
                    or limit is not None
                    and count >= limit
                ):
                    raise RoleAssetError(
                        "Member instance limit exceeded",
                        [
                            {
                                "path": "member_id",
                                "code": "LIMIT",
                                "message": "Asset or frozen role instance limit reached",
                            }
                        ],
                    )
                await self._check_member_limits(session, root, additional=1)
                member_id, session_id = uuid.uuid4().hex, uuid.uuid4().hex
                member = {
                    "member_id": member_id,
                    "session_id": session_id,
                    "conversation_id": str(uuid.uuid4()),
                    "umo": f"webchat:FriendMessage:{session_id}",
                    "name": request.name,
                    "system_prompt": frozen["effective"]["prompt"],
                }
                frozen["member"] = member
                state.setdefault("members", {})[member_id] = frozen
                session.add(
                    ConversationV2(
                        conversation_id=member["conversation_id"],
                        platform_id="webchat",
                        user_id=member["umo"],
                        content=[],
                        title=request.name,
                        persona_id=frozen["effective"].get("persona_id"),
                    )
                )
                session.add(
                    PlatformSession(
                        session_id=session_id,
                        creator=username,
                        display_name=request.name,
                        archived=1,
                    )
                )
                result = {
                    "member_id": member_id,
                    "name": request.name,
                    "preset_id": request.preset_id,
                }
            elif result is None and request.action in {"dispatch", "call_workflow"}:
                if request.action == "dispatch" and (
                    request.member_id not in state.get("members", {})
                    or not request.name
                    or not request.task
                ):
                    raise RoleAssetError(
                        "Dispatch requires a created member, name and task"
                    )
                if len(state.get("dynamic_tasks", [])) >= team["max_tasks"]:
                    raise RoleAssetError("Team task limit exceeded", status=409)
                if any(
                    identity not in member_tasks for identity in request.dependencies
                ):
                    raise RoleAssetError(
                        "Dependencies must reference existing member tasks"
                    )
                child, contract, refs = {}, None, []
                if request.action == "call_workflow":
                    key = f"{request.asset_id}:{request.version}"
                    child = root.snapshot.get("allowed_workflows", {}).get(key)
                    if child is None or not request.name:
                        raise RoleAssetError(
                            "Choose a named allowed workflow version",
                            [
                                {
                                    "path": "asset_id",
                                    "code": "FORBIDDEN",
                                    "message": "Workflow version is not allowed by the frozen asset",
                                }
                            ],
                            status=403,
                        )
                    assert request.asset_id is not None and request.version is not None
                    await self.get_asset(username, request.asset_id, request.version)
                    required = child["definition"]["required_inputs"]
                    fields = [
                        {
                            "path": f"inputs.{name}",
                            "code": "REQUIRED",
                            "message": "Provide non-empty text",
                        }
                        for name in required
                        if not isinstance(request.inputs.get(name), str)
                        or not request.inputs[name].strip()
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
                        raise RoleAssetError("Invalid workflow inputs", fields)
                    try:
                        refs = [
                            ArtifactReference.model_validate(ref).model_dump()
                            for ref in request.input_artifacts
                        ]
                    except ValidationError as exc:
                        raise RoleAssetError(
                            "Invalid workflow artifact inputs",
                            [
                                {
                                    "path": "input_artifacts",
                                    "code": "REFERENCE",
                                    "message": "Provide visible artifact identities and exact integer versions",
                                }
                            ],
                        ) from exc
                    for ref in refs:
                        await self.read_artifact(
                            username,
                            **ref,
                            consumer_run_id=run_id,
                            attempt_id=attempt_id,
                            execution_id=execution_id,
                            _session=session,
                        )
                    return_task = next(
                        task
                        for task in child["definition"]["tasks"]
                        if task["id"] == child["result_task_id"]
                    )
                    contract = return_task.get("output_contract")
                    if (
                        request.output_contract is not None
                        and request.output_contract != contract
                    ):
                        raise RoleAssetError(
                            "Use the pinned workflow return contract",
                            [
                                {
                                    "path": "output_contract",
                                    "code": "CONTRACT",
                                    "message": "Contract differs from the fixed return task",
                                }
                            ],
                        )
                    frozen, literal_task = {}, ""
                else:
                    assert request.name is not None and request.task is not None
                    frozen = members[request.member_id]
                    await self.roles.validate_frozen_role(
                        frozen["role"], frozen.get("profile")
                    )
                    # Dispatch text is literal. Graph placeholders are escaped so goals
                    # containing braces cannot accidentally acquire new input bindings.
                    literal_task = request.task.replace("{", "{{").replace("}", "}}")
                    await self._validate_task_definition(
                        username,
                        GraphTask.model_validate(
                            {
                                "id": "dispatch",
                                "name": request.name,
                                "member": request.member_id,
                                "task": literal_task,
                                "output_contract": request.output_contract,
                                **(
                                    {
                                        "role_id": frozen["role"]["role_id"],
                                        "role_version": frozen["role"]["version"],
                                    }
                                    if frozen["role"]["role_id"]
                                    else {
                                        "role": frozen["role"]["definition"],
                                        "installation": frozen["role"]["installation"],
                                    }
                                ),
                            }
                        ),
                        required_inputs=[],
                    )
                identity, node_id = uuid.uuid4().hex, "task_" + uuid.uuid4().hex
                node = (
                    {
                        "id": node_id,
                        "name": request.name,
                        "member": request.member_id,
                        **(
                            {
                                "role_id": frozen["role"]["role_id"],
                                "role_version": frozen["role"]["version"],
                            }
                            if frozen["role"]["role_id"]
                            else {
                                "role": frozen["role"]["definition"],
                                "installation": frozen["role"]["installation"],
                            }
                        ),
                        "task": literal_task,
                        "inputs": {},
                        "overrides": {},
                        "output_contract": request.output_contract,
                    }
                    if request.action == "dispatch"
                    else {}
                )
                if request.action == "call_workflow":
                    node = {
                        "id": node_id,
                        "name": request.name,
                        "workflow": {
                            "asset_id": request.asset_id,
                            "version": request.version,
                        },
                        "inputs": {},
                        "output_contract": contract,
                    }
                state.setdefault("dynamic_tasks", []).append(node)
                state.setdefault("dynamic_edges", []).extend(
                    {"source": dependency, "target": node_id}
                    for dependency in dict.fromkeys(request.dependencies)
                )
                state["task_runs"] = {**tasks, node_id: identity}
                session.add(
                    AgentWorkflowRun(
                        run_id=identity,
                        owner_username=username,
                        status="pending",
                        snapshot={
                            "engine": "workflow_call_v2"
                            if request.action == "call_workflow"
                            else "single_task_v2",
                            "parent_run_id": root.run_id,
                            "usage_protocol": 1,
                            "scope_limits": caller.snapshot.get("scope_limits", []),
                            "root_run_id": _root_run_id(root),
                            "depth": root.snapshot.get("depth", 0),
                            "node_id": node_id,
                            "created_by_task_id": caller.state["task_id"],
                            "asset_id": root.snapshot["asset_id"],
                            "asset_version": root.snapshot["asset_version"],
                            "definition": {**node, "required_inputs": []},
                            **frozen,
                            "inputs": request.inputs
                            if request.action == "call_workflow"
                            else {},
                            "input_artifacts": refs
                            if request.action == "call_workflow"
                            else root.snapshot["input_artifacts"],
                            **(
                                {
                                    "workflow": node["workflow"],
                                    "call_max_depth": caller.snapshot["effective"].get(
                                        "max_depth"
                                    ),
                                }
                                if request.action == "call_workflow"
                                else {}
                            ),
                            "source": root.snapshot["source"],
                            "initiator": root.snapshot.get("initiator", username),
                            **(
                                {"chat": root.snapshot["chat"]}
                                if "chat" in root.snapshot
                                else {}
                            ),
                            "task": literal_task,
                        },
                        state={
                            "scope_id": root.state["scope_id"],
                            "task_id": identity,
                            "attempt_id": uuid.uuid4().hex,
                            "message_id": uuid.uuid4().hex,
                            "safe_to_finish": False,
                            "reason": None,
                            "response": None,
                        },
                    )
                )
                result = {
                    "task_id": node_id,
                    "run_id": identity,
                    "member_id": request.member_id,
                }
            elif request.action == "wait":
                if not request.task_ids or any(
                    identity not in member_tasks for identity in request.task_ids
                ):
                    raise RoleAssetError("Wait requires existing member task_ids")
                caller.state = {
                    **caller.state,
                    "capacity_released": True,
                    "waiting_tasks": request.task_ids,
                    "reason": "Waiting for member task results.",
                }
                session.add(caller)
            if result is not None or request.action == "wait":
                operations[request.request_id] = {
                    "decision": decision,
                    "result": result,
                }
                root.state = state
                session.add(root)
                if not prior:
                    session.add(
                        AgentWorkflowEvent(
                            run_id=root.run_id,
                            kind="team." + request.action,
                            data={
                                "request_id": request.request_id,
                                "caller_task_id": caller.state["task_id"],
                                **(result or {"task_ids": request.task_ids}),
                            },
                        )
                    )
            root_id = root.run_id
            await session.commit()
        await self.advance_run(username, root_id)
        if result is not None:
            return result
        while True:
            if request.action == "wait":
                progress = await self.get_run(username, root_id, include_details=False)
                if any(
                    task["status"]
                    in {"pending", "running", "waiting_child", "waiting_human"}
                    for task in progress["tasks"]
                    if not request.task_ids or task["node_id"] in request.task_ids
                ):
                    await self._await_execution_permission(
                        username, run_id, execution_id
                    )
                    await asyncio.sleep(0.05)
                    continue
            observed = await self.get_run(username, root_id)
            selected = [
                task
                for task in observed["tasks"]
                if not request.task_ids or task["node_id"] in request.task_ids
            ]
            selected = [
                {
                    "task_id": task["node_id"],
                    "run_id": task["run_id"],
                    "status": task["status"],
                    "name": task["snapshot"]["definition"]["name"],
                    "member_id": task["snapshot"].get("member", {}).get("member_id"),
                    "reason": task["state"].get("reason"),
                    "result": task["result"],
                    "result_ref": task["result_ref"],
                }
                for task in selected
            ]
            if request.action != "wait":
                return {
                    "members": observed["members"],
                    "tasks": selected,
                    "relationships": observed["relationships"],
                }
            if all(
                task["status"]
                not in {"pending", "running", "waiting_child", "waiting_human"}
                for task in selected
            ):
                async with self.db.get_db() as session:
                    caller = await session.get(AgentWorkflowRun, run_id)
                    assert caller is not None
                    control = await self._lock_control(session, caller)
                    await session.refresh(caller)
                    await self._require_execution_owner(session, run_id, execution_id)
                    if control.status == "cancelled":
                        raise RoleAssetError("Run was cancelled", status=409)
                    whole_root = await session.get(
                        AgentWorkflowRun, _root_run_id(caller)
                    )
                    assert whole_root is not None
                    active = [
                        task
                        for task in await _descendant_runs(session, whole_root)
                        if task.run_id != run_id
                        and task.status == "running"
                        and _uses_execution_slot(task.snapshot)
                        and not task.state.get("capacity_released")
                    ]
                    role_limit = caller.snapshot["effective"].get("max_parallel")
                    if (
                        control.status == "active"
                        and _has_scope_capacity(whole_root, root, active)
                        and (
                            role_limit is None
                            or sum(
                                _capacity_key(task.snapshot)
                                == _capacity_key(caller.snapshot)
                                for task in active
                            )
                            < role_limit
                        )
                    ):
                        caller.state = {
                            **caller.state,
                            "capacity_released": False,
                            "waiting_tasks": [],
                            "reason": None,
                        }
                        session.add(caller)
                        await session.commit()
                        return {
                            "members": observed["members"],
                            "tasks": selected,
                            "relationships": observed["relationships"],
                        }
                    await session.commit()
            await self._await_execution_permission(username, run_id, execution_id)
            await asyncio.sleep(0.05)

    async def advance_run(
        self, username: str, run_id: str, *, wake_parent: bool = True
    ) -> dict:
        """Wake dependency scheduling; persistent claims make notifications idempotent.

        Args:
            username: Authenticated owner.
            run_id: Root graph run; no new attempt is created by a notification.
            wake_parent: Notify the owning scope after a child changes or releases capacity.

        Returns:
            Current public run snapshot including skipped and blocked paths.

        Raises:
            RoleAssetError: Run is inaccessible or is not a graph.
        """
        claimed = []
        child_scopes = []
        # The revision guard serializes claims, including same-member exclusions,
        # across service objects. Notifications carry no execution authority.
        for _ in range(20):
            async with self.db.get_db() as session:
                root = await session.get(AgentWorkflowRun, run_id)
                if root is None or root.owner_username != username:
                    raise RoleAssetError("Run not found", status=404)
                if root.snapshot.get("engine") != "graph_v2":
                    raise RoleAssetError("Run is not a dependency workflow")
                control = await self._lock_control(session, root)
                await session.refresh(root)
                if control.status != "active":
                    await session.commit()
                    break
                if root.status not in {"running", "blocked"}:
                    break
                previous_outcome = (root.status, root.state.get("reason"))
                if (root.snapshot.get("initiator") or "").startswith(
                    API_KEY_USERNAME_PREFIX
                ):
                    try:
                        await self._require_api_run_access(session, root)
                    except RoleAssetError:
                        reason = "The initiating API key no longer has access to this workflow version."
                        if previous_outcome != ("blocked", reason):
                            root.status = "blocked"
                            root.state = {**root.state, "reason": reason}
                            session.add(root)
                            session.add(
                                AgentWorkflowEvent(
                                    run_id=run_id, kind="run.blocked", data={}
                                )
                            )
                        await session.commit()
                        break
                external_root_id = (
                    _root_run_id(root)
                    if (root.snapshot.get("initiator") or "").startswith(
                        ("platform:", API_KEY_USERNAME_PREFIX)
                    )
                    else None
                )
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
                definition = _run_definition(root)
                entries = definition.get("entry_task_ids")
                trial_definition = (
                    root.snapshot.get("trial", {}).get("draft", {}).get("definition")
                )
                task_indexes = {
                    task["id"]: index
                    for index, task in enumerate(
                        (trial_definition or {}).get("tasks", definition["tasks"])
                    )
                }
                rows = {
                    node_id: await session.get(AgentWorkflowRun, identity)
                    for node_id, identity in _current_task_runs(root).items()
                }
                whole_root = await session.get(AgentWorkflowRun, _root_run_id(root))
                assert whole_root is not None
                family = await _descendant_runs(session, whole_root)
                # Child completion and the parent's exact output handoff share the
                # admission transaction. A repeated wake cannot create or return twice.
                for row in rows.values():
                    if (
                        row is None
                        or row.status not in {"waiting_child", "blocked"}
                        or not row.state.get("child_scope_run_id")
                    ):
                        continue
                    child = await session.get(
                        AgentWorkflowRun, row.state["child_scope_run_id"]
                    )
                    assert child is not None
                    if child.status == "completed" and row.state.get(
                        "child_return_failed"
                    ):
                        continue
                    if row.status == "blocked" and child.status in {
                        "running",
                        "completed",
                    }:
                        root.status = "running"
                        row.status = "waiting_child"
                        descendants = {row.snapshot["node_id"]}
                        while True:
                            expanded = descendants | {
                                edge["target"]
                                for edge in definition["edges"]
                                if edge["source"] in descendants
                            }
                            if expanded == descendants:
                                break
                            descendants = expanded
                        for sibling in rows.values():
                            if (
                                sibling is not None
                                and sibling.status == "dependency_failed"
                                and (
                                    entries is not None
                                    or sibling.snapshot["node_id"] in descendants
                                )
                            ):
                                sibling.status = "pending"
                                sibling.state = {**sibling.state, "reason": None}
                                session.add(sibling)
                    if child.status == "completed":
                        returned_task = await session.get(
                            AgentWorkflowRun,
                            _current_task_runs(child)[
                                "coordinator"
                                if child.snapshot.get("team_definition")
                                else child.snapshot["definition"]["result_task_id"]
                            ],
                        )
                        assert returned_task is not None
                        contract = row.snapshot["definition"].get("output_contract")
                        result = returned_task.result
                        typed = row.snapshot.get("typed_child_call", False)
                        if typed and result is not None:
                            pending = [contract or {"properties": {"text": {}}}]
                            names = set()
                            while pending:
                                schema = pending.pop()
                                names.update(schema.get("properties", {}))
                                pending.extend(schema.get("allOf", []))
                            result = {
                                key: value
                                for key, value in result.items()
                                if key in names
                            }
                        fields = (
                            [
                                {
                                    "path": "result_task_id",
                                    "code": "NOT_READY",
                                    "message": "The selected workflow return task has no validated result",
                                }
                            ]
                            if result is None
                            else validate_result(contract, result, port_formats=typed)
                        )
                        approval_required = typed and child.snapshot.get(
                            "team_definition", {}
                        ).get("approval_question")
                        if approval_required:
                            approval = await session.get(
                                AgentWorkflowTodo, _current_task_runs(child)["approval"]
                            )
                            if (
                                approval is None
                                or approval.data["status"] != "approved"
                                or approval.data.get("artifact_ref")
                                != {"artifact_id": returned_task.run_id, "version": 1}
                            ):
                                fields.append(
                                    {
                                        "path": f"definition.tasks.{task_indexes[row.snapshot['node_id']]}.team",
                                        "code": "APPROVAL_REQUIRED",
                                        "message": "The child team's exact result version was not approved.",
                                    }
                                )
                        if fields:
                            row.status = "blocked"
                            row.state = {
                                **row.state,
                                "reason": fields[-1]["message"],
                                "issues": fields,
                                "child_return_failed": True,
                            }
                        else:
                            assert result is not None
                            reference = returned_task.state.get("child_result_ref") or {
                                "artifact_id": returned_task.run_id,
                                "version": 1,
                            }
                            row.result = copy.deepcopy(result)
                            row.status = "completed"
                            row.state = {**row.state, "reason": None}
                            separately_reviewed = typed and any(
                                task.get("human", {}).get("kind") == "approval"
                                and task["human"]["artifact_task_id"]
                                == row.snapshot["node_id"]
                                for task in definition["tasks"]
                                if task.get("human")
                            )
                            if result != returned_task.result or separately_reviewed:
                                # Give a caller's own review a distinct version, even
                                # if every field in the child's result is public.
                                session.add(
                                    AgentWorkflowArtifact(
                                        artifact_id=row.run_id,
                                        version=1,
                                        run_id=row.run_id,
                                        submission_id=row.state["attempt_id"],
                                        contract=contract,
                                        content=result,
                                    )
                                )
                                row.state["child_source_ref"] = reference
                                reference = {"artifact_id": row.run_id, "version": 1}
                            row.state["child_result_ref"] = reference
                        session.add(row)
                        session.add(
                            AgentWorkflowEvent(
                                run_id=row.run_id,
                                kind="child.returned",
                                data={
                                    "child_scope_run_id": child.run_id,
                                    "attempt_id": row.state["attempt_id"],
                                    "status": row.status,
                                },
                            )
                        )
                    elif child.status in {"blocked", "cancelled"}:
                        row.status = "blocked"
                        reason = "Child scope requires attention: " + (
                            child.state.get("reason") or child.status
                        )
                        if child.snapshot.get("dispatch_scope"):
                            failed_items = []
                            for item_id, identity in _current_task_runs(child).items():
                                if item_id == "collect":
                                    continue
                                item = await session.get(AgentWorkflowRun, identity)
                                if item is not None and item.status != "completed":
                                    failed_items.append(item_id)
                            if failed_items:
                                reason = "List items require attention: " + ", ".join(
                                    failed_items
                                )
                        row.state = {**row.state, "reason": reason}
                        session.add(row)
                    else:
                        child_scopes.append(child.run_id)
                # A saved answer is consumed under the same root admission lock
                # as successor claims. Pause retains it; cancel cannot release it.
                for row in rows.values():
                    if row is None or row.status != "waiting_human":
                        continue
                    todo = await session.get(AgentWorkflowTodo, row.run_id)
                    if (
                        todo is None
                        or todo.data.get("source_kind") != "static_node"
                        or todo.data["status"] != "answered"
                    ):
                        continue
                    response = todo.data["response"]
                    rejected = response["action"] == "reject"
                    result = {
                        "text": response.get("answer")
                        or response.get("feedback")
                        or "Approved."
                    }
                    if todo.data["kind"] == "approval":
                        result["approved"] = not rejected
                    row.result = result
                    row.status = (
                        "rejected"
                        if rejected and todo.data["kind"] != "approval"
                        else "completed"
                    )
                    row.state = {
                        **row.state,
                        "reason": response.get("feedback") if rejected else None,
                    }
                    todo.data = {
                        **todo.data,
                        "status": "rejected"
                        if rejected
                        else "approved"
                        if todo.data["kind"] == "approval"
                        else "completed",
                        "consumed_at": time.time(),
                    }
                    session.add(todo)
                    session.add(row)
                    session.add(
                        AgentWorkflowArtifact(
                            artifact_id=row.run_id,
                            version=1,
                            run_id=row.run_id,
                            submission_id=response["request_id"],
                            contract=row.snapshot["definition"].get("output_contract"),
                            content=result,
                        )
                    )
                    session.add(
                        AgentWorkflowEvent(
                            run_id=row.run_id,
                            kind="human.consumed",
                            data={
                                "todo_id": todo.todo_id,
                                "request_id": response["request_id"],
                                "attempt_id": row.state["attempt_id"],
                            },
                        )
                    )
                await self._advance_loops(session, root, rows)
                active = [
                    row
                    for row in family
                    if row is not None
                    and row.status == "running"
                    and _uses_execution_slot(row.snapshot)
                    and not row.state.get("capacity_released")
                ]
                occupied = set()
                # Quota waits free execution slots but retain their session lock.
                # A sibling must not claim this member until the parked turn exits.
                occupied.update(
                    row.snapshot["member"]["member_id"]
                    for row in family
                    if row is not None
                    and row.status == "running"
                    and "member" in row.snapshot
                )
                role_active: dict[tuple, int] = {}
                for row in active:
                    role_id = _capacity_key(row.snapshot)
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
                        child_call = task.get("team") or task.get("workflow")
                        incoming = [
                            edge
                            for edge in definition["edges"]
                            if edge["target"] == task["id"]
                        ]
                        matched = []
                        waiting_on = []
                        failed_on = []
                        for edge in incoming:
                            source = rows[edge["source"]]
                            assert source is not None
                            source_loop = source.snapshot.get("loop_id")
                            if source_loop and source_loop != row.snapshot.get(
                                "loop_id"
                            ):
                                loop_status = root.state["loops"][source_loop]["status"]
                                if loop_status == "running":
                                    waiting_on.append(edge["source"])
                                    continue
                                if loop_status == "skipped":
                                    continue
                                if loop_status != "passed":
                                    failed_on.append(edge["source"])
                                    continue
                            if source.status in {
                                "pending",
                                "running",
                                "waiting_human",
                                "waiting_child",
                            }:
                                waiting_on.append(edge["source"])
                            elif source.status == "completed":
                                rule = edge.get("condition")
                                match = True
                                if rule:
                                    value = source.result
                                    assert value is not None
                                    for name in rule["field"].split("."):
                                        value = value[name]
                                    match = matches_condition(
                                        cast(str | bool | int | float | None, value),
                                        rule,
                                    )
                                if match:
                                    matched.append(source)
                            elif source.status != "skipped":
                                failed_on.append(edge["source"])
                        if entries is not None and not waiting_on:
                            row.state = {
                                **row.state,
                                "entered_from": (
                                    ["__input"]
                                    if task["id"] in entries
                                    else [
                                        source.snapshot["node_id"] for source in matched
                                    ]
                                ),
                            }
                        if failed_on:
                            row.status = "dependency_failed"
                            row.state = {
                                **row.state,
                                "reason": "An upstream task has no validated result.",
                                "waiting_on": failed_on,
                                "wait_kind": "dependency_failed",
                            }
                        elif waiting_on:
                            changed = (
                                row.state.get("wait_kind") != "predecessors"
                                or row.state.get("waiting_on") != waiting_on
                            )
                            row.state = {
                                **row.state,
                                "waiting_on": waiting_on,
                                "wait_kind": "predecessors",
                                "reason": "Waiting for upstream tasks: "
                                + ", ".join(waiting_on),
                            }
                            session.add(row)
                            if changed:
                                session.add(
                                    AgentWorkflowEvent(
                                        run_id=row.run_id,
                                        kind="task.waiting",
                                        data={
                                            "node_id": task["id"],
                                            "attempt_id": row.state["attempt_id"],
                                            "wait_kind": "predecessors",
                                            "waiting_on": waiting_on,
                                        },
                                    )
                                )
                            continue
                        elif not matched and (
                            incoming
                            or (task.get("control") or {}).get("kind") == "join"
                            or (entries is not None and task["id"] not in entries)
                        ):
                            row.status = "skipped"
                            row.state = {
                                **row.state,
                                "reason": (
                                    "No incoming control path was activated."
                                    if entries is not None
                                    else "No incoming path matched its field conditions."
                                ),
                                "waiting_on": [],
                                "wait_kind": "skipped",
                            }
                        else:
                            previous_wait_kind = row.state.get("wait_kind")
                            node_contract = task_input_contract(
                                task.get("input_contract"),
                                task.get("transform"),
                                dispatch=bool(task.get("dispatch")),
                            )
                            if entries is not None and not row.state.get(
                                "retry_pending"
                            ):
                                data_wait = []
                                data_issues = []
                                for name, ref in task["inputs"].items():
                                    source_id = ref.get("task_id")
                                    if (
                                        name in row.snapshot.get("iteration_inputs", {})
                                        or source_id is None
                                        or source_id not in rows
                                    ):
                                        continue
                                    source = rows[source_id]
                                    assert source is not None
                                    if source.status in {
                                        "pending",
                                        "running",
                                        "waiting_human",
                                        "waiting_child",
                                    }:
                                        if source_id not in data_wait:
                                            data_wait.append(source_id)
                                        continue
                                    required = port_requires_value(node_contract, name)
                                    field = ref["field"]
                                    source_loop = source.snapshot.get("loop_id")
                                    loop_status = (
                                        root.state["loops"][source_loop]["status"]
                                        if source_loop
                                        and source_loop != row.snapshot.get("loop_id")
                                        else None
                                    )
                                    if loop_status == "running":
                                        if source_id not in data_wait:
                                            data_wait.append(source_id)
                                        continue
                                    if loop_status not in (None, "passed", "skipped"):
                                        reason = f"belongs to a loop that {loop_status}"
                                    elif source.status == "completed":
                                        pinned = source.state.get(
                                            "child_result_ref"
                                        ) or {
                                            "artifact_id": source.run_id,
                                            "version": 1,
                                        }
                                        if (
                                            not required
                                            or port_value(source.result, field, pinned)
                                            is not MISSING_PORT_VALUE
                                        ):
                                            continue
                                        reason = "did not provide a value"
                                    elif source.status == "skipped" and not required:
                                        continue
                                    else:
                                        reason = (
                                            f"was {source.status.replace('_', ' ')}"
                                        )
                                    data_issues.append(
                                        {
                                            "path": f"definition.tasks.{task_indexes[task['id']]}.inputs.{name}",
                                            "code": "DATA_UNAVAILABLE",
                                            "message": (
                                                f"Input port {name} could not read {source_id}.{field} "
                                                f"because the source {reason}."
                                            ),
                                        }
                                    )
                                if data_issues or data_wait:
                                    if data_issues:
                                        row.status = "dependency_failed"
                                        row.state = {
                                            **row.state,
                                            "reason": " ".join(
                                                issue["message"]
                                                for issue in data_issues
                                            ),
                                            "issues": data_issues,
                                            "waiting_on": [],
                                            "wait_kind": "dependency_failed",
                                        }
                                        progress = True
                                    else:
                                        row.state = {
                                            **row.state,
                                            "reason": "Waiting for data from: "
                                            + ", ".join(data_wait),
                                            "waiting_on": data_wait,
                                            "wait_kind": "data",
                                        }
                                    session.add(row)
                                    if data_issues or previous_wait_kind != "data":
                                        session.add(
                                            AgentWorkflowEvent(
                                                run_id=row.run_id,
                                                kind="task.dependency_failed"
                                                if data_issues
                                                else "task.waiting",
                                                data={
                                                    "node_id": task["id"],
                                                    "attempt_id": row.state[
                                                        "attempt_id"
                                                    ],
                                                    "wait_kind": row.state["wait_kind"],
                                                    "waiting_on": row.state[
                                                        "waiting_on"
                                                    ],
                                                    "reason": row.state["reason"],
                                                },
                                            )
                                        )
                                    continue
                            row.state = {
                                key: value
                                for key, value in row.state.items()
                                if key
                                not in {"waiting_on", "wait_kind", "reason", "issues"}
                            }
                            if task.get("control"):
                                control = task["control"]
                                if control["kind"] == "condition":
                                    ref = task["inputs"]["value"]
                                    evidence = {}
                                    iteration_input = row.snapshot.get(
                                        "iteration_inputs", {}
                                    ).get("value")
                                    if iteration_input is not None:
                                        value = iteration_input["raw_value"]
                                        evidence["artifact"] = iteration_input[
                                            "reference"
                                        ]
                                    elif task["id"] == root.snapshot.get(
                                        "trial", {}
                                    ).get("node_id") and "value" in root.snapshot.get(
                                        "trial", {}
                                    ).get("feedback", {}):
                                        fixed = root.snapshot["trial"]["feedback"][
                                            "value"
                                        ]
                                        value = root.snapshot["trial"]["sources"][
                                            fixed["task_id"]
                                        ]["content"]
                                        evidence["artifact"] = root.snapshot["trial"][
                                            "sources"
                                        ][fixed["task_id"]]["artifact"]
                                        for name in fixed["field"].split("."):
                                            value = value[name]
                                    elif ref.get("input") is not None:
                                        value = root.snapshot["inputs"][ref["input"]]
                                    elif (
                                        ref["task_id"]
                                        in root.snapshot.get("trial", {}).get(
                                            "sources", {}
                                        )
                                        and ref["task_id"] not in rows
                                    ):
                                        value = root.snapshot["trial"]["sources"][
                                            ref["task_id"]
                                        ]["content"]
                                        evidence["artifact"] = root.snapshot["trial"][
                                            "sources"
                                        ][ref["task_id"]]["artifact"]
                                        for name in ref["field"].split("."):
                                            value = value[name]
                                    else:
                                        source = rows[ref["task_id"]]
                                        assert source is not None
                                        if source.status != "completed":
                                            row.status = "skipped"
                                            row.state = {
                                                **row.state,
                                                "reason": "The condition input was skipped.",
                                            }
                                            session.add(row)
                                            session.add(
                                                AgentWorkflowEvent(
                                                    run_id=row.run_id,
                                                    kind="task.skipped",
                                                    data={
                                                        "node_id": task["id"],
                                                        "attempt_id": row.state[
                                                            "attempt_id"
                                                        ],
                                                        "reason": row.state["reason"],
                                                    },
                                                )
                                            )
                                            progress = True
                                            continue
                                        value = source.result
                                        evidence = {
                                            "source_run_id": source.run_id,
                                            "artifact": source.state.get(
                                                "child_result_ref"
                                            )
                                            or {
                                                "artifact_id": source.run_id,
                                                "version": 1,
                                            },
                                        }
                                        for name in ref["field"].split("."):
                                            value = value[name]
                                    row.snapshot = {
                                        **row.snapshot,
                                        "inputs": {"value": value},
                                        "input_reads": {"value": evidence},
                                    }
                                    row.result = {
                                        "matched": matches_condition(value, control)
                                    }
                                elif entries is not None:
                                    row.result = {
                                        "results": [
                                            {
                                                "node_id": source.snapshot["node_id"],
                                                "result": source.result,
                                            }
                                            for source in matched
                                            if source.result is not None
                                        ]
                                    }
                                    session.add(
                                        AgentWorkflowArtifact(
                                            artifact_id=row.run_id,
                                            version=1,
                                            run_id=row.run_id,
                                            submission_id=row.state["attempt_id"],
                                            contract=JOIN_OUTPUT,
                                            content=row.result,
                                        )
                                    )
                                row.status = "completed"
                                session.add(row)
                                session.add(
                                    AgentWorkflowEvent(
                                        run_id=row.run_id,
                                        kind="task.completed",
                                        data={
                                            "node_id": task["id"],
                                            "attempt_id": row.state["attempt_id"],
                                        },
                                    )
                                )
                                progress = True
                                continue
                            if task.get("human"):
                                human = task["human"]
                                ref = None
                                if human["kind"] == "approval":
                                    source_id = human["artifact_task_id"]
                                    if source_id not in rows:
                                        ref = root.snapshot["trial"]["sources"][
                                            source_id
                                        ]["artifact"]
                                    else:
                                        source = rows[source_id]
                                        assert source is not None
                                        ref = source.state.get("child_result_ref") or {
                                            "artifact_id": source.run_id,
                                            "version": human["artifact_version"],
                                        }
                                    # A requested revision may arrive later through
                                    # an explicit edit. Keep its exact waiting
                                    # reference; never substitute the latest one.
                                recommendation = None
                                recommendation_error = None
                                recommendation_ref = None
                                source_id = human.get("recommendation_task_id")
                                if source_id:
                                    if source_id not in rows:
                                        recommendation_ref = root.snapshot["trial"][
                                            "sources"
                                        ][source_id]["artifact"]
                                    else:
                                        source = rows[source_id]
                                        assert source is not None
                                        recommendation_ref = {
                                            "artifact_id": source.run_id,
                                            "version": 1,
                                        }
                                    artifact = await session.get(
                                        AgentWorkflowArtifact,
                                        (
                                            recommendation_ref["artifact_id"],
                                            recommendation_ref["version"],
                                        ),
                                    )
                                    values = []
                                    for field in (
                                        human["recommendation_value_field"],
                                        human["recommendation_reason_field"],
                                    ):
                                        value = artifact.content if artifact else None
                                        for part in field.split("."):
                                            value = (
                                                value.get(part)
                                                if isinstance(value, dict)
                                                else None
                                            )
                                        values.append(value)
                                    choice, reason = values
                                    if (
                                        isinstance(choice, str)
                                        and choice.strip()
                                        and isinstance(reason, str)
                                        and reason.strip()
                                        and len(choice) <= human["max_length"]
                                        and (
                                            (
                                                human["kind"] == "approval"
                                                and choice in {"approve", "reject"}
                                            )
                                            or (
                                                human["kind"] == "question"
                                                and (
                                                    not human["choices"]
                                                    or choice in human["choices"]
                                                )
                                            )
                                        )
                                    ):
                                        recommendation = {
                                            "value": choice,
                                            "reason": reason,
                                        }
                                    else:
                                        recommendation_error = "The selected upstream fields do not contain a valid recommendation and reason."
                                todo = AgentWorkflowTodo(
                                    todo_id=row.run_id,
                                    root_run_id=_root_run_id(root),
                                    task_run_id=row.run_id,
                                    data={
                                        "kind": human["kind"],
                                        "source_kind": "static_node",
                                        "invocation_id": None,
                                        "tool_record_id": None,
                                        "recommendation": recommendation,
                                        "recommendation_error": recommendation_error,
                                        "recommendation_ref": recommendation_ref,
                                        "recommendation_fields": {
                                            "value": human.get(
                                                "recommendation_value_field"
                                            ),
                                            "reason": human.get(
                                                "recommendation_reason_field"
                                            ),
                                        }
                                        if source_id
                                        else None,
                                        "scope_run_id": root.run_id,
                                        "run_name": whole_root.snapshot["definition"][
                                            "name"
                                        ],
                                        "task_name": task["name"],
                                        "recommendation_source_name": next(
                                            (
                                                item["name"]
                                                for item in definition["tasks"]
                                                if item["id"] == source_id
                                            ),
                                            None,
                                        ),
                                        "node_id": task["id"],
                                        "answerable_by": [username],
                                        "human_wait": human["human_wait"],
                                        "deadline": time.time()
                                        + human["human_wait"]["timeout_seconds"]
                                        if human["human_wait"]["timeout_seconds"]
                                        is not None
                                        else None,
                                        "timeout_checked_at": None,
                                        "timeout_reason": None,
                                        "question": human["question"],
                                        "constraints": {
                                            "choices": human["choices"],
                                            "max_length": human["max_length"],
                                        },
                                        "artifact_ref": ref,
                                        "attempt_id": row.state["attempt_id"],
                                        "status": "pending",
                                        "response": None,
                                        "consumed_at": None,
                                    },
                                )
                                session.add(todo)
                                row.status = "waiting_human"
                                row.state = {**row.state, "reason": human["question"]}
                                session.add(row)
                                session.add(
                                    AgentWorkflowEvent(
                                        run_id=row.run_id,
                                        kind="human.waiting",
                                        data={
                                            "todo_id": todo.todo_id,
                                            "artifact_ref": ref,
                                        },
                                    )
                                )
                                progress = True
                                continue
                            transform = task.get("transform")
                            native = (
                                transform in NATIVE_NODE_OUTPUTS
                                or transform == PLUGIN_COMPONENT
                            )
                            member_id = row.snapshot.get("member", {}).get("member_id")
                            role_id = (
                                _capacity_key(row.snapshot)
                                if not (child_call or transform or task.get("dispatch"))
                                else None
                            )
                            role_limit = row.snapshot.get("effective", {}).get(
                                "max_parallel"
                            )
                            if (
                                native
                                or not (child_call or transform or task.get("dispatch"))
                            ) and (
                                not _has_scope_capacity(whole_root, root, active)
                                or member_id in occupied
                                or (
                                    role_limit is not None
                                    and role_active.get(role_id, 0) >= role_limit
                                )
                            ):
                                row.state = {
                                    **row.state,
                                    "wait_kind": "capacity",
                                    "waiting_on": [],
                                    "reason": "Waiting for execution capacity.",
                                }
                                session.add(row)
                                if previous_wait_kind != "capacity":
                                    session.add(
                                        AgentWorkflowEvent(
                                            run_id=row.run_id,
                                            kind="task.waiting",
                                            data={
                                                "node_id": task["id"],
                                                "attempt_id": row.state["attempt_id"],
                                                "wait_kind": "capacity",
                                                "waiting_on": [],
                                            },
                                        )
                                    )
                                continue
                            retrying = row.state.get("retry_pending")
                            inputs = dict(
                                row.snapshot["inputs"]
                                if retrying
                                or (
                                    task.get("workflow")
                                    and not row.snapshot.get("graph_task")
                                )
                                else {}
                            )
                            refs = list(
                                row.snapshot.get("input_artifacts", [])
                                if retrying
                                or (
                                    task.get("workflow")
                                    and not row.snapshot.get("graph_task")
                                )
                                else []
                                if child_call
                                else root.snapshot.get("input_artifacts", [])
                            )
                            if (
                                root.snapshot.get("team_definition")
                                and not retrying
                                and not task.get("workflow")
                            ):
                                for source in matched:
                                    ref = source.state.get("child_result_ref") or {
                                        "artifact_id": source.run_id,
                                        "version": 1,
                                    }
                                    if ref not in refs:
                                        refs.append(ref)
                            selected_fields = (
                                dict(row.snapshot.get("selected_fields", {}))
                                if retrying
                                else {}
                            )
                            if row.snapshot.get("graph_task") and not retrying:
                                refs = []
                            missing = False
                            port_sources = (
                                dict(row.snapshot.get("port_sources", {}))
                                if retrying
                                else {}
                            )
                            input_reads = (
                                row.snapshot.get("input_reads") if retrying else {}
                            )
                            trial_input_sources = (
                                dict(row.snapshot.get("trial_input_sources", {}))
                                if retrying
                                else {}
                            )
                            iteration_inputs = row.snapshot.get("iteration_inputs", {})
                            bindings = {
                                **task["inputs"],
                                **{name: {} for name in iteration_inputs},
                            }
                            trial_feedback = root.snapshot.get("trial", {})
                            if task["id"] == trial_feedback.get("node_id"):
                                bindings.update(trial_feedback.get("feedback", {}))
                            debug_fork = root.snapshot.get("debug_fork", {})
                            debug_ports = (
                                {
                                    port["name"]: port["evidence"]
                                    for port in debug_fork.get("inputs", [])
                                    if not port["changed"] and "evidence" in port
                                }
                                if (
                                    row.snapshot.get("engine") == "single_task_v2"
                                    and task["id"]
                                    == debug_fork.get("source", {}).get("node_id")
                                )
                                else {}
                            )
                            for key, ref in [] if retrying else bindings.items():
                                if key in iteration_inputs:
                                    fixed = iteration_inputs[key]
                                    if fixed.get("missing"):
                                        continue
                                    inputs[key] = (
                                        fixed["raw_value"]
                                        if node_contract
                                        else fixed["value"]
                                    )
                                    if node_contract:
                                        port_sources[key] = {
                                            "artifact": fixed["reference"],
                                            "field": fixed["field"],
                                        }
                                    input_reads[key] = {"artifact": fixed["reference"]}
                                    if fixed["reference"] not in refs:
                                        refs.append(fixed["reference"])
                                    if row.snapshot.get("graph_task"):
                                        selected_fields.setdefault(
                                            fixed["reference"]["artifact_id"], []
                                        ).append(fixed["field"])
                                    continue
                                if node_contract and not (
                                    ref.get("input") or ref.get("task_id")
                                ):
                                    continue
                                feedback = root.snapshot.get("trial", {}).get(
                                    "feedback", {}
                                )
                                trial_source = None
                                if (
                                    task["id"]
                                    == root.snapshot.get("trial", {}).get("node_id")
                                    and key in feedback
                                ):
                                    trial_source = feedback[key]
                                elif ref.get("task_id") not in rows and ref.get(
                                    "task_id"
                                ) in root.snapshot.get("trial", {}).get("sources", {}):
                                    trial_source = ref
                                if trial_source is not None:
                                    boundary = root.snapshot["trial"]["sources"].get(
                                        trial_source["task_id"]
                                    )
                                    if boundary is None:
                                        continue
                                    trial_input_sources[key] = {
                                        "task_id": trial_source["task_id"],
                                        "field": trial_source["field"],
                                        "artifact": boundary["artifact"],
                                    }
                                    value = port_value(
                                        boundary["content"], trial_source["field"]
                                    )
                                    if value is not MISSING_PORT_VALUE:
                                        inputs[key] = (
                                            value
                                            if node_contract or isinstance(value, str)
                                            else json.dumps(value, ensure_ascii=False)
                                        )
                                        input_reads[key] = {
                                            "artifact": boundary["artifact"]
                                        }
                                        if (
                                            node_contract
                                            and boundary.get("projected")
                                            and boundary["artifact"]
                                        ):
                                            port_sources[key] = {
                                                "artifact": boundary["artifact"],
                                                "field": trial_source["field"],
                                            }
                                            if boundary["artifact"] not in refs:
                                                refs.append(boundary["artifact"])
                                    continue
                                if ref.get("input") is not None:
                                    # Earlier task aliases must not change the source.
                                    supplied = root.snapshot["inputs"]
                                    if not node_contract or ref["input"] in supplied:
                                        inputs[key] = supplied[ref["input"]]
                                        input_reads[key] = {
                                            "source_run_id": root.run_id
                                        }
                                        # Preserve the exact field grant for an unchanged historical input.
                                        evidence = debug_ports.get(key, {})
                                        if (pinned := evidence.get("artifact")) and (
                                            field := evidence.get("field")
                                        ):
                                            input_reads[key] = {
                                                name: evidence[name]
                                                for name in (
                                                    "artifact",
                                                    "source_run_id",
                                                )
                                                if name in evidence
                                            }
                                            port_sources[key] = {
                                                "artifact": pinned,
                                                "field": field,
                                            }
                                            if pinned not in refs:
                                                refs.append(pinned)
                                            selected_fields.setdefault(
                                                pinned["artifact_id"], []
                                            ).append(field)
                                    inherited = root.snapshot.get(
                                        "trial_inputs", {}
                                    ).get(ref["input"])
                                    if inherited is not None:
                                        trial_input_sources[key] = inherited
                                else:
                                    source = rows[ref["task_id"]]
                                    assert source is not None
                                    if source.status != "completed":
                                        if (
                                            entries is not None
                                            and source.status == "skipped"
                                        ):
                                            # Required readings were checked after control admission.
                                            continue
                                        missing = True
                                        break
                                    pinned = source.state.get("child_result_ref") or {
                                        "artifact_id": source.run_id,
                                        "version": 1,
                                    }
                                    if node_contract:
                                        value = port_value(
                                            source.result, ref["field"], pinned
                                        )
                                        if value is not MISSING_PORT_VALUE:
                                            inputs[key] = value
                                            input_reads[key] = {
                                                "artifact": pinned,
                                                "source_run_id": source.run_id,
                                            }
                                            port_sources[key] = {
                                                "artifact": pinned,
                                                "field": ref["field"],
                                            }
                                    else:
                                        value = source.result
                                        for name in ref["field"].split("."):
                                            value = value[name]
                                        inputs[key] = (
                                            value
                                            if isinstance(value, str)
                                            else json.dumps(value, ensure_ascii=False)
                                        )
                                        input_reads[key] = {
                                            "artifact": pinned,
                                            "source_run_id": source.run_id,
                                        }
                                    if pinned not in refs:
                                        refs.append(pinned)
                                    if row.snapshot.get("graph_task"):
                                        selected_fields.setdefault(
                                            pinned["artifact_id"], []
                                        ).append(ref["field"])
                            if missing:
                                row.status = "skipped"
                                row.state = {
                                    **row.state,
                                    "reason": "A declared result input belongs to a skipped path.",
                                }
                            elif dispatch := task.get("dispatch"):
                                path = f"definition.tasks.{task_indexes[task['id']]}.inputs"
                                row.snapshot = {
                                    **row.snapshot,
                                    "inputs": inputs,
                                    "input_artifacts": refs,
                                    "selected_fields": selected_fields,
                                    "trial_input_sources": trial_input_sources,
                                    "port_sources": port_sources,
                                    "input_reads": input_reads,
                                }
                                try:
                                    items = normalize_port_inputs(
                                        DISPATCH_INPUT, inputs, path
                                    )["items"]
                                    if len(items) > dispatch["max_items"]:
                                        raise RoleAssetError(
                                            f"List dispatch permits at most {dispatch['max_items']} items.",
                                            [
                                                {
                                                    "path": f"{path}.items",
                                                    "code": "LIMIT",
                                                    "message": f"Received {len(items)} items.",
                                                }
                                            ],
                                        )
                                    child_id = (
                                        await self._create_dispatch_scope(
                                            session, root, row, task, items
                                        )
                                        if items
                                        else None
                                    )
                                except RoleAssetError as exc:
                                    row.status = "failed"
                                    row.state = {
                                        **row.state,
                                        "reason": str(exc),
                                        "issues": exc.fields,
                                    }
                                else:
                                    if child_id:
                                        row.status = "waiting_child"
                                        row.state = {
                                            **row.state,
                                            "child_scope_run_id": child_id,
                                            "reason": "Waiting for list items to complete.",
                                        }
                                        child_scopes.append(child_id)
                                    else:
                                        row.status = "completed"
                                        row.result = {"results": []}
                                        session.add(
                                            AgentWorkflowArtifact(
                                                artifact_id=row.run_id,
                                                version=1,
                                                run_id=row.run_id,
                                                submission_id=row.state["attempt_id"],
                                                contract=JOIN_OUTPUT,
                                                content=row.result,
                                            )
                                        )
                            elif node_contract and not child_call:
                                task_index = next(
                                    index
                                    for index, node in enumerate(definition["tasks"])
                                    if node["id"] == task["id"]
                                )
                                # Bounded trials keep diagnostics in the original canvas.
                                trial = root.snapshot.get("trial")
                                if trial:
                                    task_index = next(
                                        index
                                        for index, node in enumerate(
                                            trial["draft"]["definition"]["tasks"]
                                        )
                                        if node["id"] == task["id"]
                                    )
                                path = f"definition.tasks.{task_index}"
                                contract = task.get("output_contract")
                                try:
                                    if transform and not native:
                                        inputs, result = execute_port_transform(
                                            task, inputs, path
                                        )
                                    else:
                                        inputs = normalize_port_inputs(
                                            node_contract, inputs, f"{path}.inputs"
                                        )
                                    for name, value in inputs.items():
                                        if (
                                            node_contract["properties"][name].get(
                                                "format"
                                            )
                                            not in REFERENCE_OUTPUTS.values()
                                        ):
                                            continue
                                        await self._require_port_reference(
                                            session,
                                            username,
                                            value,
                                            f"{path}.inputs.{name}",
                                            external_root_id=external_root_id,
                                        )
                                        port_sources.setdefault(name, {})[
                                            "reference"
                                        ] = value
                                        ref = {
                                            key: value[key]
                                            for key in ("artifact_id", "version")
                                        }
                                        if ref not in refs:
                                            refs.append(ref)
                                except RoleAssetError as exc:
                                    row.status = "failed"
                                    row.state = {
                                        **row.state,
                                        "reason": str(exc),
                                        "issues": exc.fields,
                                    }
                                else:
                                    if native:
                                        row.status = "running"
                                        row.snapshot = {
                                            **row.snapshot,
                                            "component_path": path,
                                        }
                                        active.append(row)
                                    elif transform:
                                        row.status = "completed"
                                        row.result = result
                                        session.add(
                                            AgentWorkflowArtifact(
                                                artifact_id=row.run_id,
                                                version=1,
                                                run_id=row.run_id,
                                                submission_id=row.state["attempt_id"],
                                                contract=contract,
                                                content=result,
                                            )
                                        )
                                    else:
                                        row.status = "running"
                                        occupied.add(member_id)
                                        role_active[role_id] = (
                                            role_active.get(role_id, 0) + 1
                                        )
                                        active.append(row)
                                row.snapshot = {
                                    **row.snapshot,
                                    **(
                                        {"task": task["task"].format_map(inputs)}
                                        if not transform
                                        and not retrying
                                        and row.status == "running"
                                        else {}
                                    ),
                                    "inputs": inputs,
                                    "input_artifacts": refs,
                                    "selected_fields": selected_fields,
                                    "trial_input_sources": trial_input_sources,
                                    "port_sources": port_sources,
                                    "input_reads": input_reads,
                                }
                            elif child_call:
                                row.snapshot = {
                                    **row.snapshot,
                                    "inputs": inputs,
                                    "input_artifacts": refs,
                                    "trial_input_sources": trial_input_sources,
                                    "port_sources": port_sources,
                                    "input_reads": input_reads,
                                }
                                frozen = copy.deepcopy(
                                    root.snapshot["allowed_workflows"][
                                        f"{child_call['asset_id']}:{child_call['version']}"
                                    ]
                                    if task.get("workflow")
                                    and not row.snapshot.get("graph_task")
                                    else root.snapshot["child_assets"][task["id"]]
                                )
                                try:
                                    index = task_indexes.get(task["id"])
                                    path = (
                                        f"definition.tasks.{index}.inputs"
                                        if index is not None
                                        else "inputs"
                                    )
                                    if node_contract:
                                        inputs = normalize_port_inputs(
                                            node_contract, inputs, path
                                        )
                                        for name, value in inputs.items():
                                            if (
                                                node_contract["properties"][name].get(
                                                    "format"
                                                )
                                                not in REFERENCE_OUTPUTS.values()
                                            ):
                                                continue
                                            await self._require_port_reference(
                                                session,
                                                username,
                                                value,
                                                f"{path}.{name}",
                                                external_root_id=external_root_id,
                                            )
                                            port_sources.setdefault(name, {})[
                                                "reference"
                                            ] = value
                                        frozen["inputs"] = inputs
                                    else:
                                        frozen["inputs"] = (
                                            inputs
                                            if not row.snapshot.get("graph_task")
                                            else {
                                                key: inputs[key]
                                                for key in task["inputs"]
                                            }
                                        )
                                    fields = [
                                        {
                                            "path": f"{path}.{name}",
                                            "code": "REQUIRED",
                                            "message": "Provide non-empty text",
                                        }
                                        for name in frozen["definition"][
                                            "required_inputs"
                                        ]
                                        if not frozen["inputs"].get(name, "").strip()
                                    ]
                                    if fields:
                                        raise RoleAssetError(
                                            "Invalid run inputs", fields
                                        )
                                    if trial_input_sources:
                                        frozen["trial_inputs"] = trial_input_sources
                                    # Only declared values enter a static child scope.
                                    frozen["input_artifacts"] = (
                                        [] if row.snapshot.get("graph_task") else refs
                                    )
                                    row.snapshot = {
                                        **row.snapshot,
                                        "inputs": frozen["inputs"],
                                        "input_artifacts": refs,
                                        "trial_input_sources": trial_input_sources,
                                        "port_sources": port_sources,
                                        "input_reads": input_reads,
                                        **(
                                            {"typed_child_call": True}
                                            if node_contract
                                            else {}
                                        ),
                                    }
                                    depth = root.snapshot.get("depth", 0) + 1
                                    depth_limit = min(
                                        whole_root.snapshot["definition"].get(
                                            "max_depth", 3
                                        ),
                                        root.snapshot.get("depth_limit", 20),
                                        row.snapshot.get("call_max_depth") or 20,
                                    )
                                    if depth > depth_limit:
                                        raise RoleAssetError(
                                            "Child call exceeds the frozen depth limit"
                                        )
                                    for member in [
                                        *frozen["members"].values(),
                                        *frozen.get("allowed_roles", {}).values(),
                                    ]:
                                        await self.roles.validate_frozen_role(
                                            member["role"], member.get("profile")
                                        )
                                        if (
                                            member["effective"].get("max_depth")
                                            is not None
                                            and depth > member["effective"]["max_depth"]
                                        ):
                                            raise RoleAssetError(
                                                "Nested team exceeds the frozen role depth limit"
                                            )
                                    frozen["depth_limit"] = min(
                                        depth_limit,
                                        frozen["definition"].get("max_depth", 3),
                                    )
                                    child_id = await self._create_scope(
                                        session,
                                        username,
                                        frozen,
                                        root_run_id=whole_root.run_id,
                                        parent_task=row,
                                        depth=depth,
                                    )
                                except RoleAssetError as exc:
                                    row.status = "blocked"
                                    row.state = {
                                        **row.state,
                                        "reason": str(exc),
                                        "issues": exc.fields,
                                    }
                                else:
                                    row.snapshot = {
                                        **row.snapshot,
                                        "inputs": frozen["inputs"],
                                        "input_artifacts": refs,
                                    }
                                    row.status = "waiting_child"
                                    row.state = {
                                        **row.state,
                                        "child_scope_run_id": child_id,
                                        "capacity_released": True,
                                        "reason": "Waiting for child workflow results."
                                        if task.get("workflow")
                                        else "Waiting for child team results.",
                                    }
                                    child_scopes.append(child_id)
                            else:
                                row.snapshot = {
                                    **row.snapshot,
                                    "task": row.snapshot["task"]
                                    if retrying
                                    else task["task"].format_map(inputs),
                                    "inputs": inputs,
                                    "input_artifacts": refs,
                                    "selected_fields": selected_fields,
                                    "trial_input_sources": trial_input_sources,
                                    "input_reads": input_reads,
                                }
                                row.status = "running"
                                occupied.add(member_id)
                                role_active[role_id] = role_active.get(role_id, 0) + 1
                                active.append(row)
                        session.add(row)
                        session.add(
                            AgentWorkflowEvent(
                                run_id=row.run_id,
                                kind="task." + row.status,
                                data={
                                    "node_id": task["id"],
                                    "attempt_id": row.state["attempt_id"],
                                    "reason": row.state.get("reason"),
                                },
                            )
                        )
                        progress = True
                    if await self._advance_loops(session, root, rows):
                        progress = True
                loop_success = all(
                    loop["status"] in {"passed", "skipped"}
                    for loop in root.state.get("loops", {}).values()
                )
                if all(
                    row is not None
                    and row.status
                    not in {"pending", "running", "waiting_human", "waiting_child"}
                    for row in rows.values()
                ):
                    success = loop_success and all(
                        row is not None and row.status in {"completed", "skipped"}
                        for row in rows.values()
                    )
                    result_id = root.snapshot["definition"].get("result_task_id")
                    if not root.snapshot.get("team_definition"):
                        selected = rows.get(result_id)
                        no_trial_result = (
                            bool(root.snapshot.get("trial")) and result_id is None
                        )
                        success = success and (
                            no_trial_result
                            or (
                                selected is not None
                                and selected.status == "completed"
                                and selected.result is not None
                                and not (
                                    (
                                        selected.snapshot["definition"].get("human")
                                        or {}
                                    ).get("kind")
                                    == "approval"
                                    and selected.result.get("approved") is False
                                )
                            )
                        )
                    approval_missing = False
                    if (
                        success
                        and not root.snapshot.get("team_definition")
                        and selected is not None
                    ):
                        result_ref = selected.state.get("child_result_ref") or {
                            "artifact_id": selected.run_id,
                            "version": 1,
                        }
                        for definition_task in root.snapshot["definition"]["tasks"]:
                            human = definition_task.get("human") or {}
                            if (
                                human.get("kind") != "approval"
                                or human.get("artifact_task_id") != result_id
                            ):
                                continue
                            approval_row = rows[definition_task["id"]]
                            approval = await session.get(
                                AgentWorkflowTodo, approval_row.run_id
                            )
                            if (
                                approval is None
                                or approval.data.get("status") != "approved"
                                or approval.data.get("artifact_ref") != result_ref
                            ):
                                approval_missing = True
                                success = False
                                break
                    root.status = "completed" if success else "blocked"
                    root.state = {
                        **root.state,
                        "reason": None
                        if success
                        else "The selected result task did not produce a validated result."
                        if not root.snapshot.get("team_definition")
                        and (
                            selected is None
                            or selected.status == "skipped"
                            or selected.result is None
                            and selected.status == "completed"
                        )
                        else "The selected result version has not been approved."
                        if approval_missing
                        else "The selected approval was rejected; connect an approved delivery or a bounded rework path."
                        if not root.snapshot.get("team_definition")
                        and selected is not None
                        and (selected.snapshot["definition"].get("human") or {}).get(
                            "kind"
                        )
                        == "approval"
                        and selected.result is not None
                        and selected.result.get("approved") is False
                        else "One or more branches require attention; independent branches were allowed to finish.",
                    }
                    if success and (
                        root.snapshot.get("team_definition") or result_id is not None
                    ):
                        root.result = rows[
                            "coordinator"
                            if root.snapshot.get("team_definition")
                            else result_id
                        ].result
                    session.add(root)
                    if (root.status, root.state["reason"]) != previous_outcome:
                        session.add(
                            AgentWorkflowEvent(
                                run_id=run_id, kind="run." + root.status, data={}
                            )
                        )
                await session.commit()
                # A previous notification may have stopped after committing a
                # claim. Dispatch its frozen snapshot without admitting it again.
                claimed = [
                    row.run_id
                    for row in rows.values()
                    if row is not None
                    and row.status == "running"
                    and _uses_execution_slot(row.snapshot)
                    and not row.state.get("execution_id")
                ]
                break
        else:
            raise RoleAssetError("Scheduling contention; notify again", status=409)
        for identity in claimed:
            self._dispatch_graph_task(username, run_id, identity)
        for identity in dict.fromkeys(child_scopes):
            child = await self.advance_run(username, identity, wake_parent=False)
            if child["status"] in {"completed", "blocked"}:
                return await self.advance_run(username, run_id, wake_parent=wake_parent)
        if wake_parent and root.snapshot.get("parent_run_id"):
            async with self.db.get_db() as session:
                caller = await session.get(
                    AgentWorkflowRun, root.snapshot["parent_run_id"]
                )
                assert caller is not None
            await self.advance_run(username, caller.snapshot["parent_run_id"])
        await self._resume_human_choices(username, _root_run_id(root))
        current = await self.get_run(username, run_id)
        if current["control"]["status"] == "active":
            for child in current.get("tasks", []):
                if child["status"] in {"interrupted", "failed", "blocked"} and (
                    child["state"].get("resolution_pending")
                    or child["status"] == "interrupted"
                    and (
                        child["state"].get("paused_resume")
                        or child["state"].get("retry_pending")
                    )
                ):
                    try:
                        await self.recover_run(username, child["run_id"])
                    except RoleAssetError:
                        # Reconsider accepted resumes when capacity is released,
                        # retaining unresolved local tool waits as local waits.
                        pass
            current = await self.get_run(username, run_id)
        return current

    async def request_human_choice(
        self,
        username: str,
        run_id: str,
        attempt_id: str,
        execution_id: str,
        record_id: str,
        spec: dict,
    ) -> dict:
        """Save a question at its original tool boundary or read its saved answer.

        Args:
            username: Owner captured by the execution callback.
            run_id: Actual executing task, never supplied by the model.
            attempt_id: Original attempt being continued.
            execution_id: Current executor fence.
            record_id: Runner-owned tool invocation identity.
            spec: Validated question and option metadata from the built-in tool.

        Returns:
            Saved choice in the ordinary tool's response protocol.

        Raises:
            RoleAssetError: The execution identity or saved request conflicts.
            HumanInputRequired: The saved question still needs an answer.
        """
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            if row is None or row.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            control = await self._lock_control(session, row)
            await session.refresh(row)
            await self._require_execution_owner(session, run_id, execution_id)
            checkpoint = await session.get(AgentWorkflowCheckpoint, run_id)
            call = await session.get(AgentWorkflowToolCall, record_id)
            if (
                control.status == "cancelled"
                or row.state["attempt_id"] != attempt_id
                or checkpoint is None
                or checkpoint.attempt_id != attempt_id
                or call is None
                or call.run_id != run_id
                or call.attempt_id != attempt_id
                or call.invocation_id != checkpoint.invocation_id
                or call.name != "ask_user_choice"
                or call.status not in {"started", "unknown"}
            ):
                raise RoleAssetError(
                    "Human choice has no active original tool call", status=409
                )
            todo_id = "choice:" + record_id
            todo = await session.get(AgentWorkflowTodo, todo_id)
            if todo is None:
                root = await session.get(AgentWorkflowRun, _root_run_id(row))
                assert root is not None
                human_wait = (
                    row.snapshot["effective"].get("human_wait")
                    or HumanWaitPolicy().model_dump()
                )
                todo = AgentWorkflowTodo(
                    todo_id=todo_id,
                    root_run_id=root.run_id,
                    task_run_id=run_id,
                    data={
                        "kind": "question",
                        "source_kind": "tool_call",
                        "scope_run_id": row.snapshot.get("parent_run_id", row.run_id),
                        "node_id": row.snapshot.get("node_id"),
                        "attempt_id": attempt_id,
                        "invocation_id": call.invocation_id,
                        "tool_record_id": record_id,
                        "tool_call_id": call.call_id,
                        "run_name": root.snapshot["definition"]["name"],
                        "task_name": row.snapshot["definition"]["name"],
                        "question": spec["prompt"],
                        "choice_spec": copy.deepcopy(spec),
                        "constraints": {
                            "choices": [item["id"] for item in spec["options"]],
                            "max_length": 10_000,
                        },
                        "recommendation": spec.get("recommendation"),
                        "recommendation_ref": None,
                        "recommendation_error": None,
                        "artifact_ref": None,
                        "answerable_by": [username],
                        "human_wait": human_wait,
                        "deadline": time.time() + human_wait["timeout_seconds"]
                        if human_wait["timeout_seconds"] is not None
                        else None,
                        "timeout_checked_at": None,
                        "timeout_reason": None,
                        "status": "pending",
                        "response": None,
                        "consumed_at": None,
                    },
                )
                session.add(todo)
                session.add(
                    AgentWorkflowEvent(
                        run_id=run_id,
                        kind="human.requested",
                        data={
                            "todo_id": todo_id,
                            "source_kind": "tool_call",
                            "tool_record_id": record_id,
                        },
                    )
                )
            elif (
                todo.task_run_id != run_id
                or todo.data.get("attempt_id") != attempt_id
                or todo.data.get("choice_spec") != spec
            ):
                raise RoleAssetError("Original human question changed", status=409)
            if (
                todo.data["status"] in {"answered", "consumed"}
                and control.status == "active"
            ):
                return {
                    key: todo.data["response"].get(key)
                    for key in ("choice_id", "free_text")
                }
            if todo.data["status"] not in {"pending", "answered"}:
                raise RoleAssetError("Human choice is no longer active", status=409)
            row.status = "waiting_human"
            row.state = {
                **row.state,
                "human_request_id": todo_id,
                "reason": "Waiting for a human choice.",
            }
            session.add(row)
            await session.commit()
        raise HumanInputRequired(todo_id)

    async def _resume_human_choices(self, username: str, root_run_id: str) -> None:
        """Resume answered tool waits after their suspended executor has exited.

        Args:
            username: Authorized root owner.
            root_run_id: Root containing all nested request scopes.
        """
        async with self.db.get_db() as session:
            root = await session.get(AgentWorkflowRun, root_run_id)
            if root is None or root.owner_username != username:
                return
            control = await session.get(AgentWorkflowControl, root_run_id)
            if control is None or control.status != "active":
                return
            identities = (
                (
                    await session.execute(
                        select(AgentWorkflowTodo.task_run_id).where(
                            AgentWorkflowTodo.root_run_id == root_run_id,
                            AgentWorkflowTodo.data["source_kind"].as_string()
                            == "tool_call",
                            AgentWorkflowTodo.data["status"].as_string() == "answered",
                        )
                    )
                )
                .scalars()
                .all()
            )
        for identity in dict.fromkeys(identities):
            if identity in self._executions:
                continue
            try:
                await self.recover_run(username, identity)
            except RoleAssetError:
                # Capacity, pause and uncertain external effects remain durable waits.
                pass

    async def answer_todo(
        self,
        username: str,
        run_id: str,
        todo_id: str,
        payload: dict,
        *,
        timed_out: bool = False,
        caller: dict | None = None,
        browser_expires_at: float | None = None,
    ) -> dict:
        """Save a human response before attempting any dependency release.

        Args:
            username: Authenticated run owner.
            run_id: Root run containing the request.
            todo_id: Frozen request identity shown with its preview.
            payload: Unique response identity, answer or explicit decision.
            timed_out: Trusted service scheduler requesting the saved deadline action;
                never accepted from the external answer payload.
            caller: Verified platform event identity, if submitted from chat.
            browser_expires_at: Server-issued expiry when submitted from a browser.

        Returns:
            Current persisted run, including a response awaiting resume if paused.

        Raises:
            RoleAssetError: Inaccessible request, invalid answer or conflicting response.
        """
        request = None
        if not timed_out:
            try:
                request = HumanAnswerRequest.model_validate(payload)
            except ValidationError as exc:
                raise RoleAssetError("Invalid human response") from exc
        async with self.db.get_db() as session:
            root = await session.get(AgentWorkflowRun, run_id)
            if root is None or root.owner_username != username:
                raise RoleAssetError("Run not found", status=404)
            control = await self._lock_control(session, root)
            if caller is not None:
                chat = root.snapshot.get("chat") or {}
                if chat.get("caller") != caller:
                    raise RoleAssetError("Run not found", status=404)
                # Check the grant after acquiring the SQLite write lock, so a
                # committed revocation cannot be overtaken by a late decision.
                await self.chat.require_access(
                    chat,
                    username,
                    browser_expires_at=browser_expires_at,
                    _session=session,
                )
            todo = await session.get(AgentWorkflowTodo, todo_id)
            if todo is None or todo.root_run_id != run_id:
                raise RoleAssetError("Human request not found", status=404)
            if username not in todo.data.get("answerable_by", []):
                raise RoleAssetError("Human request not found", status=404)
            if timed_out:
                now = time.time()
                if (
                    control.status == "cancelled"
                    or todo.data["status"] != "pending"
                    or todo.data.get("timeout_checked_at") is not None
                    or todo.data.get("deadline") is None
                    or todo.data["deadline"] > now
                ):
                    await session.commit()
                    return await self.get_run(username, run_id)
                policy = todo.data["human_wait"]
                recommendation = todo.data.get("recommendation")
                reason = None
                value = recommendation.get("value") if recommendation else None
                rationale = recommendation.get("reason") if recommendation else None
                if policy["timeout_action"] != "use_recommendation":
                    reason = "wait_policy"
                elif not recommendation:
                    reason = "missing_recommendation"
                elif (
                    not isinstance(value, str)
                    or not isinstance(rationale, str)
                    or not rationale.strip()
                ):
                    reason = "invalid_recommendation"
                elif todo.data.get("source_kind") == "tool_call":
                    call = await session.get(
                        AgentWorkflowToolCall, todo.data["tool_record_id"]
                    )
                    if (
                        call is None
                        or call.run_id != todo.task_run_id
                        or call.attempt_id != todo.data["attempt_id"]
                        or todo.data["choice_spec"].get("recommendation")
                        != recommendation
                    ):
                        reason = "evidence_unavailable"
                    elif value not in todo.data["constraints"]["choices"]:
                        reason = "invalid_recommendation"
                else:
                    ref = todo.data.get("recommendation_ref")
                    artifact = (
                        await session.get(
                            AgentWorkflowArtifact, (ref["artifact_id"], ref["version"])
                        )
                        if ref
                        else None
                    )
                    fields = todo.data.get("recommendation_fields") or {}
                    values = []
                    for key in ("value", "reason"):
                        content = artifact.content if artifact else None
                        for part in (fields.get(key) or "").split("."):
                            content = (
                                content.get(part) if isinstance(content, dict) else None
                            )
                        values.append(content)
                    if values != [value, rationale]:
                        reason = "evidence_unavailable"
                    elif todo.data["kind"] == "approval":
                        ref = todo.data["artifact_ref"]
                        artifact = await session.get(
                            AgentWorkflowArtifact, (ref["artifact_id"], ref["version"])
                        )
                        if artifact is None:
                            reason = "evidence_unavailable"
                        elif value not in {"approve", "reject"}:
                            reason = "invalid_recommendation"
                    elif (
                        not value.strip()
                        or len(value) > todo.data["constraints"]["max_length"]
                        or todo.data["constraints"]["choices"]
                        and value not in todo.data["constraints"]["choices"]
                    ):
                        reason = "invalid_recommendation"
                if reason is None:
                    automatic = {
                        "request_id": "deadline:" + todo.todo_id,
                        "action": "answer",
                    }
                    if todo.data.get("source_kind") == "tool_call":
                        automatic["choice_id"] = value
                    elif todo.data["kind"] == "approval":
                        automatic.update(
                            action=value, artifact_ref=todo.data["artifact_ref"]
                        )
                        if value == "reject":
                            automatic["feedback"] = rationale
                    else:
                        automatic["answer"] = value
                    try:
                        request = HumanAnswerRequest.model_validate(automatic)
                    except ValidationError:
                        reason = "invalid_recommendation"
                todo.data = {
                    **todo.data,
                    "timeout_checked_at": now,
                    "timeout_reason": reason,
                }
                session.add(todo)
                if reason is not None:
                    session.add(
                        AgentWorkflowEvent(
                            run_id=todo.task_run_id,
                            kind="human.deadline_waiting",
                            data={"todo_id": todo.todo_id, "reason": reason},
                        )
                    )
                    await session.commit()
                    return await self.get_run(username, run_id)
            assert request is not None
            decision = request.model_dump()
            if todo.data["kind"] == "approval":
                if decision["artifact_ref"] != todo.data["artifact_ref"]:
                    raise RoleAssetError(
                        "Artifact version does not match the human request", status=409
                    )
            elif request.artifact_ref is not None:
                raise RoleAssetError("Questions cannot approve an artifact")
            previous = todo.data["response"]
            if previous is not None:
                if any(
                    previous.get(key) != decision[key]
                    for key in (
                        "action",
                        "answer",
                        "feedback",
                        "choice_id",
                        "free_text",
                        "artifact_ref",
                    )
                ):
                    raise RoleAssetError("Human request already answered", status=409)
            else:
                if control.status == "cancelled":
                    raise RoleAssetError("Run was cancelled", status=409)
                if todo.data["kind"] == "approval":
                    ref = todo.data["artifact_ref"]
                    artifact = await session.get(
                        AgentWorkflowArtifact, (ref["artifact_id"], ref["version"])
                    )
                    if artifact is None:
                        raise RoleAssetError(
                            "The requested artifact version is not available; create the revision before deciding",
                            status=409,
                        )
                if todo.data.get("source_kind") == "tool_call":
                    options = todo.data["constraints"]["choices"]
                    if (
                        request.action != "answer"
                        or request.answer is not None
                        or request.feedback is not None
                        or request.choice_id not in [*options, "__free_text__"]
                        or request.choice_id == "__free_text__"
                        and not request.free_text
                    ):
                        raise RoleAssetError(
                            "Choose an offered option or provide free text"
                        )
                elif request.choice_id is not None or request.free_text is not None:
                    raise RoleAssetError("Tool choice fields require a dynamic request")
                elif request.action == "reject":
                    if not request.feedback or request.answer is not None:
                        raise RoleAssetError(
                            "Rejection requires feedback and no answer"
                        )
                elif todo.data["kind"] == "approval":
                    if request.action != "approve" or request.answer is not None:
                        raise RoleAssetError(
                            "Choose approve or reject for this exact version"
                        )
                elif request.action != "answer" or not request.answer:
                    raise RoleAssetError("Provide an answer to this question")
                else:
                    constraints = todo.data["constraints"]
                    if (
                        len(request.answer) > constraints["max_length"]
                        or constraints["choices"]
                        and request.answer not in constraints["choices"]
                    ):
                        raise RoleAssetError(
                            "Answer does not satisfy the saved constraints"
                        )
                todo.data = {
                    **todo.data,
                    "status": "answered",
                    "response": decision,
                    "decision_source": {
                        "type": "recommendation",
                        "recommendation": todo.data["recommendation"],
                        "recommendation_ref": todo.data.get("recommendation_ref"),
                        "artifact_ref": todo.data.get("artifact_ref"),
                    }
                    if timed_out
                    else {
                        "type": "human",
                        "username": username,
                        **({"caller": caller} if caller is not None else {}),
                    },
                }
                session.add(todo)
                session.add(
                    AgentWorkflowEvent(
                        run_id=todo.task_run_id,
                        kind="human.answered",
                        data={
                            "todo_id": todo_id,
                            "request_id": request.request_id,
                            "action": request.action,
                            "decision_source": todo.data["decision_source"]["type"],
                        },
                    )
                )
            await session.commit()
        if control.status == "active":
            try:
                await self.advance_run(username, run_id)
            except Exception:
                # Submission already succeeded. Recovery/continue retries the
                # durable scheduling fact without changing the saved answer.
                logger.exception("Saved human response awaits scheduling: %s", todo_id)
            await self._resume_human_choices(username, run_id)
        return await self.get_run(username, run_id)

    async def sweep_human_deadlines(self) -> int:
        """Consider a bounded batch of expired requests without model calls.

        Returns:
            Number of expired requests considered; each decision is fenced again.
        """
        if self._closing:
            return 0
        async with self.db.get_db() as session:
            rows = (
                await session.execute(
                    select(
                        AgentWorkflowTodo.todo_id,
                        AgentWorkflowTodo.root_run_id,
                        AgentWorkflowRun.owner_username,
                    )
                    .join(
                        AgentWorkflowRun,
                        AgentWorkflowRun.run_id == AgentWorkflowTodo.root_run_id,
                    )
                    .where(
                        AgentWorkflowTodo.data["status"].as_string() == "pending",
                        AgentWorkflowTodo.data["deadline"].as_float() <= time.time(),
                        AgentWorkflowRun.snapshot["root_run_id"]
                        .as_string()
                        .is_not(None),
                        AgentWorkflowTodo.data["timeout_checked_at"]
                        .as_float()
                        .is_(None),
                    )
                    .order_by(
                        AgentWorkflowTodo.data["deadline"].as_float(),
                        AgentWorkflowTodo.todo_id,
                    )
                    .limit(100)
                )
            ).all()
        for todo_id, run_id, owner in rows:
            await self.answer_todo(owner, run_id, todo_id, {}, timed_out=True)
        return len(rows)

    async def sweep_pending_dispatches(self) -> None:
        """Wake persisted list scopes and claims with no active executor.

        The scheduler and executor recheck root control under the admission lock.

        Returns:
            None after waking eligible scopes and task claims.
        """
        scope = aliased(AgentWorkflowRun)
        parent = aliased(AgentWorkflowRun)
        async with self.db.get_db() as session:
            candidates = (
                await session.execute(
                    select(
                        AgentWorkflowRun.owner_username,
                        scope.run_id,
                        AgentWorkflowRun.run_id,
                    )
                    .join(
                        scope,
                        col(scope.run_id)
                        == AgentWorkflowRun.snapshot["parent_run_id"].as_string(),
                    )
                    .join(
                        AgentWorkflowControl,
                        col(AgentWorkflowControl.run_id)
                        == AgentWorkflowRun.snapshot["root_run_id"].as_string(),
                    )
                    .where(
                        col(AgentWorkflowRun.status) == "running",
                        AgentWorkflowRun.snapshot["engine"]
                        .as_string()
                        .in_(["single_task_v2", "native_task_v2", "plugin_task_v2"]),
                        AgentWorkflowRun.state["execution_id"].as_string().is_(None),
                        col(scope.status).in_(["running", "blocked"]),
                        AgentWorkflowControl.status == "active",
                    )
                )
            ).all()
            # Expansion commits before the first item claim. A lost wake must
            # still reach the child scope, even if no executor has started.
            pending_lists = (
                await session.execute(
                    select(AgentWorkflowRun.owner_username, AgentWorkflowRun.run_id)
                    .join(
                        parent,
                        col(parent.run_id)
                        == AgentWorkflowRun.snapshot["parent_run_id"].as_string(),
                    )
                    .join(
                        AgentWorkflowControl,
                        col(AgentWorkflowControl.run_id)
                        == AgentWorkflowRun.snapshot["root_run_id"].as_string(),
                    )
                    .where(
                        AgentWorkflowRun.snapshot["dispatch_scope"]
                        .as_boolean()
                        .is_(True),
                        AgentWorkflowControl.status == "active",
                        or_(
                            and_(
                                col(AgentWorkflowRun.status) == "running",
                                col(parent.status).in_(["waiting_child", "blocked"]),
                            ),
                            and_(
                                col(AgentWorkflowRun.status).in_(
                                    ["completed", "blocked"]
                                ),
                                col(parent.status) == "waiting_child",
                            ),
                        ),
                    )
                )
            ).all()
        for username, scope_id, task_id in candidates:
            self._dispatch_graph_task(username, scope_id, task_id)
        for username, scope_id in pending_lists:
            await self.advance_run(username, scope_id)

    async def run_maintenance(self) -> None:
        """Check persisted dispatches and human deadlines even without viewers.

        Returns:
            None when the workflow service is closing.
        """
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            while not self._closing:
                try:
                    await self.sweep_pending_dispatches()
                except Exception:
                    logger.exception("Failed to scan committed workflow dispatches")
                try:
                    await self.sweep_human_deadlines()
                except Exception:
                    logger.exception("Failed to process persisted human deadlines")
                await asyncio.sleep(1)
        finally:
            self._tasks.discard(task)

    def _dispatch_graph_task(
        self,
        username: str,
        run_id: str,
        task_run_id: str,
        execution_id: str | None = None,
    ) -> None:
        """Queue a graph task and coalesce local notifications for unowned claims.

        Args:
            username: Owner of the frozen run.
            run_id: Graph scope that owns scheduling for the task.
            task_run_id: Persisted task to dispatch.
            execution_id: Durable execution owner already acquired by recovery.
        """
        if self._closing or (execution_id is None and task_run_id in self._dispatching):
            return

        def forget_dispatch(task: asyncio.Task) -> None:
            """Release local bookkeeping when the dispatch task ends.

            Args:
                task: Dispatch task that ended or was cancelled before starting.
            """
            self._tasks.discard(task)
            if self._dispatching.get(task_run_id) is task:
                del self._dispatching[task_run_id]

        task = asyncio.create_task(
            self._execute_graph_task(username, run_id, task_run_id, execution_id)
        )
        if execution_id is None:
            self._dispatching[task_run_id] = task
        self._tasks.add(task)
        task.add_done_callback(forget_dispatch)

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
            # The attempt has released ownership. A retry notified during the
            # wake below must be able to dispatch the next durable claim.
            if self._dispatching.get(task_run_id) is asyncio.current_task():
                del self._dispatching[task_run_id]
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
            if row.snapshot.get("coordinator"):
                await self._lock_control(session, row)
                root = await session.get(
                    AgentWorkflowRun, row.snapshot["parent_run_id"]
                )
                assert root is not None
                for identity in _current_task_runs(root).values():
                    if identity == run_id:
                        continue
                    required = await session.get(AgentWorkflowRun, identity)
                    if required is not None and required.snapshot.get("human"):
                        continue
                    if (
                        required is None
                        or required.status != "completed"
                        or required.result is None
                    ):
                        raise RoleAssetError(
                            "Required member results are not complete", status=409
                        )
            contract = row.snapshot["definition"].get("output_contract")
            fields = validate_result(
                contract,
                payload,
                port_formats=row.snapshot.get("port_contract_version") == 2,
            )
            if not fields and row.snapshot.get("port_contract_version") == 2:
                for name, value in payload.items():
                    schema = (contract or {}).get("properties", {}).get(name)
                    if (
                        not isinstance(schema, dict)
                        or schema.get("format") not in REFERENCE_OUTPUTS.values()
                    ):
                        continue
                    path = f"result.{name}"
                    if not any(
                        all(value[key] == selected.get(key) for key in value)
                        for source in row.snapshot.get("port_sources", {}).values()
                        if (selected := source.get("reference"))
                    ):
                        fields.append(
                            {
                                "path": path,
                                "code": "REFERENCE",
                                "message": "Select a reference bound to this attempt",
                            }
                        )
                    else:
                        await self._require_port_reference(
                            session, username, value, path
                        )
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
        if run["result"] is None or (
            run["snapshot"].get("engine") == "control_task_v2"
            and run["snapshot"]["definition"].get("output_contract") is None
        ):
            raise RoleAssetError("Run has no completed result", status=409)
        return run["result"]

    async def _artifact_source(
        self, session: AsyncSession, username: str, ref: dict
    ) -> tuple[AgentWorkflowRun, dict]:
        """Resolve the original committed artifact behind public call results.

        Args:
            session: Transaction reading exact provenance.
            username: Owner authorized for the artifact chain.
            ref: Exact artifact reference exposed by the run.

        Returns:
            The originating task and its original exact reference.

        Raises:
            RoleAssetError: The stored source chain is invalid or unavailable.
        """
        visited = set()
        while ref["artifact_id"] not in visited:
            visited.add(ref["artifact_id"])
            artifact = await session.get(
                AgentWorkflowArtifact, (ref["artifact_id"], ref["version"])
            )
            row = (
                await session.get(AgentWorkflowRun, artifact.run_id)
                if artifact
                else None
            )
            if row is None or row.owner_username != username:
                break
            source = (
                row.state.get("child_source_ref")
                if row.state.get("child_result_ref") == ref
                else None
            )
            if source is None:
                return row, ref
            ref = source
        raise RoleAssetError("Artifact source is unavailable", status=409)

    async def _require_port_reference(
        self,
        session: AsyncSession,
        username: str,
        value: dict,
        path: str,
        *,
        external_root_id: str | None = None,
    ) -> None:
        """Check the exact owned artifact and the file's membership in that version.

        Args:
            session: Transaction resolving a launch or a consumer attempt.
            username: Authenticated owner of the workflow run.
            value: Already type-checked artifact or file reference.
            path: Diagnostic path of the selected input.
            external_root_id: The sole root that an external launch may reference.

        Raises:
            RoleAssetError: The exact version or managed file is unavailable.
        """
        ref = {key: value[key] for key in ("artifact_id", "version")}
        try:
            if external_root_id is not None:
                artifact_source, _ = await self._artifact_source(session, username, ref)
                if _root_run_id(artifact_source) != external_root_id:
                    raise RoleAssetError(
                        "Historical reference inputs require Dashboard authorization",
                        status=403,
                    )
            artifact = await self.read_artifact(username, **ref, _session=session)
        except RoleAssetError as exc:
            raise RoleAssetError(
                str(exc),
                [{"path": path, "code": "REFERENCE", "message": str(exc)}],
                status=exc.status,
            ) from exc
        if "file_id" in value:
            file_id = value["file_id"]
            _, file_owner = await self._artifact_source(session, username, ref)
            owned_file = await session.scalar(
                select(AgentWorkflowFile.file_id)
                .join(
                    AgentWorkflowArtifact,
                    AgentWorkflowArtifact.run_id == AgentWorkflowFile.run_id,
                )
                .where(
                    AgentWorkflowArtifact.artifact_id == file_owner["artifact_id"],
                    AgentWorkflowArtifact.version == file_owner["version"],
                    AgentWorkflowFile.file_id == file_id,
                )
            )
            if not owned_file or {"file_id": file_id} not in artifact["content"].get(
                "files", []
            ):
                message = "The file does not belong to the selected artifact version"
                raise RoleAssetError(
                    message, [{"path": path, "code": "REFERENCE", "message": message}]
                )

    async def read_artifact(
        self,
        username: str,
        artifact_id: str,
        version: int,
        consumer_run_id: str | None = None,
        attempt_id: str | None = None,
        execution_id: str | None = None,
        *,
        _session: AsyncSession | None = None,
    ) -> dict:
        """Read full content and record actual reads by a pinned consumer.

        Args:
            username: Authenticated owner.
            artifact_id: Stable artifact identity.
            version: Exact immutable version.
            consumer_run_id: Task run recording a read, when invoked by an Agent.
            attempt_id: Active attempt required for task reads.
            execution_id: Current executor fence for runtime tool reads.
            _session: Existing dependent-write transaction, when recording a read
                must commit atomically with its coordinator operation. The caller
                commits this session; standalone reads own their transaction.

        Returns:
            Exact reference and complete content.

        Raises:
            RoleAssetError: Inaccessible version or a read outside the pinned inputs.
        """
        async with (
            self.db.get_db() if _session is None else nullcontext(_session)
        ) as session:
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
                if execution_id is not None:
                    await self._require_execution_owner(
                        session, consumer_run_id, execution_id
                    )
                graph_task = consumer.snapshot.get("graph_task", False)
                selected = consumer.snapshot.get("selected_fields", {}).get(
                    artifact_id, []
                )
                port_sources = consumer.snapshot.get("port_sources")
                if graph_task and port_sources is not None:
                    selected = [
                        source["field"]
                        for source in port_sources.values()
                        if source.get("artifact") == ref
                        and source["field"] not in REFERENCE_OUTPUTS
                    ]
                    allowed = bool(selected) or any(
                        all(
                            source.get("reference", {}).get(key) == value
                            for key, value in ref.items()
                        )
                        for source in port_sources.values()
                    )
                else:
                    allowed = version == 1 and bool(selected)
                if graph_task and (
                    not allowed
                    or ref not in consumer.snapshot.get("input_artifacts", [])
                ):
                    raise RoleAssetError(
                        "Artifact version is not a selected graph input", status=403
                    )
                if not graph_task:
                    team_output = (
                        consumer.snapshot.get("coordinator")
                        and origin.snapshot.get("parent_run_id")
                        == consumer.snapshot.get("parent_run_id")
                        and origin.state.get("scope_id")
                        == consumer.state.get("scope_id")
                    )
                    if consumer.snapshot.get("coordinator") and not team_output:
                        scope = await session.get(
                            AgentWorkflowRun, consumer.snapshot.get("parent_run_id")
                        )
                        if scope is not None:
                            for task_run_id in _current_task_runs(scope).values():
                                task_run = await session.get(
                                    AgentWorkflowRun, task_run_id
                                )
                                if (
                                    task_run
                                    and task_run.status == "completed"
                                    and task_run.state.get("child_result_ref") == ref
                                ):
                                    team_output = True
                                    break
                    own_output = (
                        origin.snapshot.get("parent_run_id")
                        == consumer.snapshot.get("parent_run_id")
                        and origin.state.get("scope_id")
                        == consumer.state.get("scope_id")
                        and origin.snapshot.get("member", {}).get("member_id")
                        == consumer.snapshot.get("member", {}).get("member_id")
                    )
                    deliveries = (
                        (
                            await session.execute(
                                select(AgentWorkflowMessage).where(
                                    AgentWorkflowMessage.root_run_id
                                    == _root_run_id(consumer),
                                    AgentWorkflowMessage.scope_id
                                    == consumer.state["scope_id"],
                                    AgentWorkflowMessage.recipient_member_id
                                    == consumer.snapshot["member"]["member_id"],
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    shared_output = any(
                        ref in message.payload["artifact_refs"]
                        for message in deliveries
                    )
                    if (
                        ref not in consumer.snapshot.get("input_artifacts", [])
                        and not team_output
                        and not own_output
                        and not shared_output
                    ):
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
                    if _session is None:
                        await session.commit()
            if consumer_run_id is not None and graph_task:
                projection = {}
                for path in selected:
                    source = artifact.content
                    target = projection
                    parts = path.split(".")
                    for part in parts[:-1]:
                        source = source[part]
                        target = target.setdefault(part, {})
                    target[parts[-1]] = source[parts[-1]]
                return {**ref, "content": projection, "files": []}
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

    async def export_artifact(
        self, username: str, artifact_id: str, version: int
    ) -> dict:
        """Export precisely the version consumed by a durable human approval.

        Args:
            username: Authenticated artifact owner.
            artifact_id: Immutable artifact identity shown in the preview.
            version: Approved version, never resolved to the latest revision.

        Returns:
            Full approved content and its exact reference and file metadata.

        Raises:
            RoleAssetError: Version is inaccessible or has no consumed approval.
        """
        artifact = await self.read_artifact(username, artifact_id, version)
        async with self.db.get_db() as session:
            approvals = (
                (
                    await session.execute(
                        select(AgentWorkflowTodo).where(
                            AgentWorkflowTodo.data["status"].as_string() == "approved",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not any(
                todo.data.get("artifact_ref")
                == {"artifact_id": artifact_id, "version": version}
                for todo in approvals
            ):
                raise RoleAssetError(
                    "This artifact version has not been approved", status=409
                )
        return artifact

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
            fields = validate_result(
                latest.contract,
                request.content,
                port_formats=origin.snapshot.get("port_contract_version") == 2,
            )
            if not fields and origin.snapshot.get("port_contract_version") == 2:
                for name, schema in (
                    (latest.contract or {}).get("properties", {}).items()
                ):
                    if (
                        not isinstance(schema, dict)
                        or schema.get("format") not in REFERENCE_OUTPUTS.values()
                    ):
                        continue
                    path = f"content.{name}"
                    value = request.content.get(name)
                    if value != latest.content.get(name):
                        fields.append(
                            {
                                "path": path,
                                "code": "REFERENCE",
                                "message": "A revision must keep the original bound reference",
                            }
                        )
                    elif value is not None:
                        await self._require_port_reference(
                            session, username, value, path
                        )
            if not fields:
                # A child handoff authorizes only the files in this parent version,
                # without changing their storage owner or granting scope-wide access.
                retained_files = {
                    ref["file_id"] for ref in latest.content.get("files", [])
                }
                for index, file_ref in enumerate(request.content.get("files", [])):
                    file = await session.get(AgentWorkflowFile, file_ref["file_id"])
                    if file is None or (
                        file.run_id != latest.run_id
                        and file_ref["file_id"] not in retained_files
                    ):
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
        self,
        username: str,
        artifact_id: str,
        version: int,
        file_id: str,
        *,
        download: bool = True,
    ) -> dict:
        """Read ready file bytes through a committed immutable artifact version.

        Args:
            username: Authenticated owner.
            artifact_id: Stable artifact identity.
            version: Exact version whose file is being read.
            file_id: Stable managed file reference in that version.
            download: Require the exact approval for files governed by an approval node.

        Returns:
            Complete bytes, filename and MIME type for preview or download.

        Raises:
            RoleAssetError: Inaccessible or uncommitted file reference.
        """
        artifact = await self.read_artifact(username, artifact_id, version)
        if not any(item["file_id"] == file_id for item in artifact["files"]):
            raise RoleAssetError("Artifact file not found", status=404)
        if download:
            async with self.db.get_db() as session:
                source, approval_ref = await self._artifact_source(
                    session, username, {"artifact_id": artifact_id, "version": version}
                )
                visited = set()
                approvals = []
                while source.snapshot.get("parent_run_id"):
                    if source.run_id in visited:
                        raise RoleAssetError(
                            "Invalid artifact scope ancestry", status=409
                        )
                    visited.add(source.run_id)
                    scope = await session.get(
                        AgentWorkflowRun, source.snapshot["parent_run_id"]
                    )
                    if scope is None:
                        raise RoleAssetError(
                            "Artifact scope is unavailable", status=409
                        )
                    governed = any(
                        task.get("human")
                        and task["human"]["kind"] == "approval"
                        and task["human"]["artifact_task_id"]
                        == source.snapshot.get("node_id")
                        for task in scope.snapshot["definition"]["tasks"]
                    )
                    if governed and approval_ref not in approvals:
                        approvals.append(approval_ref)
                    if not scope.snapshot.get("parent_run_id"):
                        break
                    result_node = (
                        "coordinator"
                        if scope.snapshot.get("team_definition")
                        else scope.snapshot["definition"].get("result_task_id")
                    )
                    if result_node != source.snapshot.get("node_id"):
                        break
                    call = await session.get(
                        AgentWorkflowRun, scope.snapshot["parent_run_id"]
                    )
                    if call is None:
                        raise RoleAssetError(
                            "Artifact caller is unavailable", status=409
                        )
                    returned = call.state.get("child_result_ref")
                    if (
                        returned
                        and returned["artifact_id"] != approval_ref["artifact_id"]
                    ):
                        original_ref = call.state.get("child_source_ref")
                        if (
                            not original_ref
                            or original_ref["artifact_id"]
                            != approval_ref["artifact_id"]
                        ):
                            break
                        if original_ref == approval_ref:
                            approval_ref = returned
                    source = call
            for ref in approvals:
                await self.export_artifact(username, **ref)
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
        if run["snapshot"]["engine"] == "native_task_v2":
            raise RoleAssetError(
                "Retry the native node with its frozen inputs", status=409
            )
        if run["snapshot"]["engine"] == "human_task_v2":
            raise RoleAssetError(
                "Answer the persistent human request instead of recovering an agent checkpoint",
                status=409,
            )
        if run["snapshot"]["engine"] in {"team_call_v2", "workflow_call_v2"}:
            raise RoleAssetError(
                "Recover the interrupted task inside the child scope", status=409
            )
        plugin_node = run["snapshot"]["engine"] == "plugin_task_v2"
        if plugin_node:
            await self._resolve_component(
                username,
                run["snapshot"]["definition"],
                run["snapshot"]["component_path"],
            )
        runner = run["snapshot"].get("effective", {}).get("runner")
        if runner is None and not plugin_node:
            raise RoleAssetError(
                "Stored runner configuration is unsupported; start a new run",
                status=409,
            )
        issues = (
            get_agent_runner_capability_issues(
                runner, {"definition.role_id": "task_result"}
            )
            if runner is not None
            else []
        )
        if issues and run["status"] in {
            "interrupted",
            "failed",
            "blocked",
            "awaiting_result",
        }:
            reason = "Frozen task runner cannot submit results"
            async with self.db.get_db() as session:
                row = await session.get(AgentWorkflowRun, run_id)
                assert row is not None
                await self._lock_control(session, row)
                await session.refresh(row)
                if row.status != run["status"]:
                    raise RoleAssetError("Execution ownership has changed", status=409)
                row.status = "blocked"
                row.state = {**row.state, "reason": reason, "issues": issues}
                session.add(row)
                session.add(
                    AgentWorkflowEvent(
                        run_id=run_id,
                        kind="task.blocked",
                        data={"attempt_id": row.state["attempt_id"], "reason": reason},
                    )
                )
                await session.commit()
            raise RoleAssetError(reason, issues, status=409)
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            if row is not None:
                control = await self._lock_control(session, row)
                await session.refresh(row)
                if control.status != "active":
                    raise RoleAssetError(
                        "Continue the whole run before recovering a task", status=409
                    )
            checkpoint = await session.get(AgentWorkflowCheckpoint, run_id)
            retry_model_request = (
                row is not None
                and row.status == "awaiting_result"
                and checkpoint is not None
                and checkpoint.phase == "response"
                and (checkpoint.data.get("response") or {}).get("role") == "err"
                and checkpoint.data.get("stage") == "main"
                and not checkpoint.started_tools
                and not checkpoint.data.get("tool_calls")
            )
            if row is not None and row.status == "awaiting_result":
                if (
                    row.state["attempt_id"] != run["state"]["attempt_id"]
                    or not run["recovery"]["has_saved_tool_results"]
                    or checkpoint is None
                    or "remote" in checkpoint.data
                    or (checkpoint.phase != "request" and not retry_model_request)
                ):
                    raise RoleAssetError(
                        "The original checkpoint cannot continue producing a task result",
                        status=409,
                    )
            elif row is None or row.status not in {
                "interrupted",
                "failed",
                "blocked",
                "waiting_human",
            }:
                raise RoleAssetError(
                    "Only interrupted execution can be recovered", status=409
                )
            if row.state.get("debug_write_preview"):
                raise RoleAssetError(
                    "This call is held at the debug write preview", status=409
                )
            if row.status == "waiting_human":
                todo = await session.get(
                    AgentWorkflowTodo, row.state.get("human_request_id")
                )
                if (
                    todo is None
                    or todo.data.get("source_kind") != "tool_call"
                    or todo.data.get("attempt_id") != row.state["attempt_id"]
                    or todo.data["status"] != "answered"
                    or run_id in self._executions
                ):
                    raise RoleAssetError(
                        "The original tool is still waiting for a human answer",
                        status=409,
                    )
            parent = None
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
                whole_root = await session.get(AgentWorkflowRun, _root_run_id(row))
                assert whole_root is not None
                family = await _descendant_runs(session, whole_root)
                active = [
                    task
                    for task in family
                    if task.status == "running"
                    and _uses_execution_slot(task.snapshot)
                    and not task.state.get("capacity_released")
                ]
                role_id = _capacity_key(row.snapshot)
                role_limit = row.snapshot.get("effective", {}).get("max_parallel")
                member_id = row.snapshot.get("member", {}).get("member_id")
                member_busy = member_id is not None and any(
                    task.status == "running"
                    and task.snapshot.get("member", {}).get("member_id") == member_id
                    for task in family
                )
                if (
                    member_busy
                    or not row.state.get("capacity_released")
                    and (
                        not _has_scope_capacity(whole_root, parent, active)
                        or role_limit is not None
                        and sum(
                            _capacity_key(task.snapshot) == role_id for task in active
                        )
                        >= role_limit
                    )
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
                        for edge in _run_definition(parent)["edges"]
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
            if checkpoint is None and (
                row.state.get("paused_resume") or row.state.get("retry_pending")
            ):
                # Pausing can precede the first invocation. There is no external
                # effect to replay; continuing still uses the existing attempt.
                row.status = "running"
                row.state = {**row.state, "execution_id": None}
                session.add(row)
                session.add(
                    AgentWorkflowEvent(
                        run_id=run_id,
                        kind="attempt.recovered",
                        data={"attempt_id": row.state["attempt_id"], "boundary": None},
                    )
                )
                await session.commit()
                parent_id = row.snapshot.get("parent_run_id")
                if parent_id:
                    self._dispatch_graph_task(username, parent_id, run_id)
                else:
                    task = asyncio.create_task(self._execute(username, run_id))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                return await self.get_run(username, run_id)
            if checkpoint is None or checkpoint.attempt_id != row.state["attempt_id"]:
                raise RoleAssetError("No resumable execution checkpoint", status=409)
            checkpoint.data = copy.deepcopy(checkpoint.data)
            calls = checkpoint.data.get("tool_calls", [])
            if "remote" not in checkpoint.data and (
                checkpoint.data.get("format_version") != 2
                or "tool_result_files" not in checkpoint.data
                or "tool_calls" not in checkpoint.data
            ):
                raise RoleAssetError(
                    "Unsupported execution checkpoint format", status=409
                )
            if retry_model_request:
                # Retry only the failed model request. Its frozen context already
                # includes saved tool results, and the failed observation keeps its ID.
                checkpoint.invocation_id = uuid.uuid4().hex
                checkpoint.phase = "request"
                checkpoint.data.update(
                    invocation_id=checkpoint.invocation_id,
                    phase=checkpoint.phase,
                    model_calls=checkpoint.data["model_calls"] + 1,
                    # Frozen contexts retain the messages. Their consumption
                    # receipts already belong to the original invocation.
                    member_messages=[],
                    response=None,
                    response_receipt_id=None,
                )
            for call in calls:
                if call["name"] == "submit_task_result" and call["status"] in {
                    "started",
                    "unknown",
                }:
                    # This system-owned tool repeats the existing idempotent
                    # submission transaction; external tools retain uncertainty.
                    call["status"] = "intent"
                    checkpoint.started_tools = [
                        identity
                        for identity in checkpoint.started_tools
                        if identity != call["call_id"]
                    ]
                    checkpoint.data["started_tools"] = checkpoint.started_tools
                    stored = await session.get(AgentWorkflowToolCall, call["record_id"])
                    if stored:
                        stored.status = "intent"
                        session.add(stored)
            session.add(checkpoint)
            unknown = [
                call for call in calls if call["status"] in {"started", "unknown"}
            ]
            remote = checkpoint.data.get("remote")
            if (
                remote
                and checkpoint.phase not in {"request", "remote_result"}
                and (
                    not remote.get("identity")
                    or "remote_query" not in remote["runner"]["capabilities"]
                )
            ):
                raise RoleAssetError(
                    "Remote result requires confirmation; no reliable run identity/query is available",
                    status=409,
                )
            if (
                checkpoint.started_tools
                and not checkpoint.result_submission
                and (
                    not calls
                    or unknown
                    and not any(
                        call["status"] == "intent"
                        or has_tool_recovery(
                            call.get("recovery", {}).get("declaration")
                        )
                        for call in calls
                    )
                )
            ):
                raise RoleAssetError(
                    "Tool execution has started; its result requires confirmation.",
                    status=409,
                )
            execution_id = uuid.uuid4().hex
            for call in calls:
                pending = call.get("recovery", {}).get("pending_decision")
                if pending is None:
                    continue
                recovery = await session.get(
                    AgentWorkflowToolRecovery, pending["record_id"]
                )
                assert recovery is not None
                metadata = copy.deepcopy(recovery.data)
                for decision in metadata["decisions"]:
                    decision.setdefault("execution_id", execution_id)
                recovery.data = metadata
                session.add(recovery)
                if pending["record_id"] == call["record_id"]:
                    call["recovery"] = metadata
            if calls:
                checkpoint.data["tool_calls"] = calls
                session.add(checkpoint)
            state = {
                **row.state,
                "execution_id": execution_id,
                "message_id": uuid.uuid4().hex,
                "reason": None,
                "resolution_pending": False,
            }
            if row.status == "awaiting_result":
                # The previous response ended safely, but this execution has
                # not reached a safe point for any future submission.
                state["safe_to_finish"] = False
            finish_result = (
                checkpoint.result_submission and row.pending_result is not None
            )
            if (
                parent_id
                and parent is not None
                and parent.snapshot.get("team_definition")
            ):
                # A team instance continues its conversation in later tasks. Resume
                # the normal pipeline so accepted submission recovery also saves
                # the original context before the next task can claim this member.
                finish_result = False
            if finish_result:
                for call in calls:
                    if call["status"] in {"result_saved", "applied"}:
                        continue
                    payload = call["args"]
                    if row.snapshot["definition"].get("output_contract") is not None:
                        try:
                            payload = (
                                json.loads(payload["result_json"])
                                if set(payload) == {"result_json"}
                                else None
                            )
                        except (TypeError, ValueError):
                            payload = None
                    if payload != row.pending_result:
                        finish_result = False
                        break
            if finish_result:
                state["safe_to_finish"] = True
                # The accepted intent proves this internal transaction's outcome,
                # even if the tool return was interrupted before being recorded.
                for call in calls:
                    if call["name"] != "submit_task_result":
                        continue
                    records = call.get("process_records", [])
                    if call["status"] not in {"result_saved", "applied"}:
                        raw = CallToolResult(
                            content=[TextContent(type="text", text="Result accepted.")]
                        ).model_dump(mode="json")
                        call["status"] = "result_saved"
                        call["raw_results"] = [raw]
                        stored = await session.get(
                            AgentWorkflowToolCall, call["record_id"]
                        )
                        assert stored is not None
                        stored.status = "result_saved"
                        stored.raw_results = [raw]
                        stored.execution_id = execution_id
                        session.add(stored)
                        records = [
                            {
                                "id": call["record_id"] + ":raw:0",
                                "data": {
                                    "id": call["call_id"],
                                    "tool_record_id": call["record_id"],
                                    "index": 0,
                                    "name": call["name"],
                                    "raw_result": raw,
                                },
                            }
                        ]
                        checkpoint.phase = "tool_result"
                        checkpoint.data["phase"] = "tool_result"
                    for record in records:
                        identity = uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{run_id}:{state['attempt_id']}:{record['id']}",
                        ).hex
                        if (
                            await session.get(AgentWorkflowProcess, identity)
                            is not None
                        ):
                            continue
                        session.add(
                            AgentWorkflowProcess(
                                record_id=identity,
                                run_id=run_id,
                                task_id=state["task_id"],
                                attempt_id=state["attempt_id"],
                                member_id=row.snapshot["member"]["member_id"],
                                kind="tool_result",
                                data=record["data"],
                            )
                        )
                        session.add(
                            AgentWorkflowEvent(
                                run_id=run_id,
                                kind="process.tool_result",
                                data={
                                    "record_id": identity,
                                    "attempt_id": state["attempt_id"],
                                },
                            )
                        )
                    status_id = uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{run_id}:{state['attempt_id']}:{call['record_id']}:status:completed",
                    ).hex
                    if await session.get(AgentWorkflowProcess, status_id) is None:
                        session.add(
                            AgentWorkflowProcess(
                                record_id=status_id,
                                run_id=run_id,
                                task_id=state["task_id"],
                                attempt_id=state["attempt_id"],
                                member_id=row.snapshot["member"]["member_id"],
                                kind="tool_status",
                                data={
                                    "tool_record_id": call["record_id"],
                                    "id": call["call_id"],
                                    "name": call["name"],
                                    "args": call["args"],
                                    "status": "completed",
                                },
                            )
                        )
                        session.add(
                            AgentWorkflowEvent(
                                run_id=run_id,
                                kind="process.tool_status",
                                data={
                                    "record_id": status_id,
                                    "attempt_id": state["attempt_id"],
                                },
                            )
                        )
                if calls:
                    session.add(checkpoint)
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
                for call in checkpoint.data.get("tool_calls", []):
                    if call["status"] in {"started", "unknown"}:
                        call["status"] = "intent"
                        stored = await session.get(
                            AgentWorkflowToolCall, call["record_id"]
                        )
                        if stored:
                            stored.status = "intent"
                            session.add(stored)
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
        if parent_id:
            self._dispatch_graph_task(username, parent_id, run_id, execution_id)
        else:
            task = asyncio.create_task(self._execute(username, run_id, execution_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return await self.get_run(username, run_id)

    async def resolve_tool_call(
        self, username: str, run_id: str, record_id: str, payload: dict
    ) -> dict:
        """Persist a human decision before continuing the original saved plan.

        Args:
            username: Authenticated original run owner.
            run_id: Affected task run, rather than the whole graph.
            record_id: Original durable tool call requiring confirmation.
            payload: Idempotency identity, explicit action and observed evidence.

        Returns:
            Current task detail. Saved decisions wait for whole-run admission or
            local capacity; duplicate decisions never schedule a second call.

        Raises:
            RoleAssetError: Missing evidence, inaccessible call or conflicting action.
        """
        try:
            request = ToolResolutionRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError("Invalid tool resolution", status=422) from exc
        if (
            request.action in {"confirm_result", "confirm_failure"}
            and request.result is None
        ):
            raise RoleAssetError("An observed tool result is required", status=422)
        run = await self.get_run(username, run_id)
        if run["snapshot"]["engine"] == "plugin_task_v2":
            await self._resolve_component(
                username,
                run["snapshot"]["definition"],
                run["snapshot"]["component_path"],
            )
        duplicate = False
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            assert row is not None
            control = await self._lock_control(session, row)
            # Acquire the same durable execution fence before reading the decision.
            changed = await session.execute(
                update(AgentWorkflowRun)
                .where(
                    col(AgentWorkflowRun.run_id) == run_id,
                    col(AgentWorkflowRun.owner_username) == username,
                    AgentWorkflowRun.state["execution_id"].as_string()
                    == run["state"].get("execution_id"),
                )
                .values(state=AgentWorkflowRun.state)
            )
            if cast(CursorResult, changed).rowcount != 1:
                raise RoleAssetError("Execution ownership has changed", status=409)
            row = await session.get(AgentWorkflowRun, run_id)
            stored = await session.get(AgentWorkflowToolCall, record_id)
            recovery = await session.get(AgentWorkflowToolRecovery, record_id)
            if stored is None or stored.run_id != run_id:
                raise RoleAssetError("Tool call not found", status=404)
            metadata = copy.deepcopy(recovery.data if recovery else {})
            decisions = metadata.setdefault("decisions", [])
            previous = next(
                (
                    item
                    for item in decisions
                    if item["request_id"] == request.request_id
                ),
                None,
            )
            if previous is not None:
                if previous["request"] != request.model_dump():
                    raise RoleAssetError("Decision identity was reused", status=409)
                duplicate = (
                    previous.get("execution_id") is not None
                    or row is None
                    or row.status == "running"
                )
            else:
                if control.status == "cancelled":
                    raise RoleAssetError("Run was cancelled", status=409)
                if any(item.get("execution_id") is None for item in decisions):
                    raise RoleAssetError(
                        "A decision is already saved; continue the whole run first",
                        status=409,
                    )
                checkpoint = await session.get(AgentWorkflowCheckpoint, run_id)
                if (
                    row is None
                    or row.status not in {"interrupted", "failed", "blocked"}
                    or checkpoint is None
                ):
                    raise RoleAssetError(
                        "Only stopped uncertain calls can be resolved", status=409
                    )
                data = copy.deepcopy(checkpoint.data)
                calls = data.get("tool_calls", [])
                index = next(
                    (
                        i
                        for i, call in enumerate(calls)
                        if call["record_id"] == record_id
                    ),
                    None,
                )
                if (
                    index is None
                    or calls[index]["status"] not in {"started", "unknown"}
                    or metadata.get("replacement_record_id")
                ):
                    raise RoleAssetError(
                        "This call no longer requires a decision", status=409
                    )
                declaration = metadata.get("declaration") or {}
                if request.action == "reconcile" and not has_tool_recovery(declaration):
                    raise RoleAssetError(
                        "This tool has no declared reconciliation capability",
                        status=409,
                    )
                decision = {
                    "request_id": request.request_id,
                    "request": request.model_dump(),
                    "attempt_id": stored.attempt_id,
                    "previous_execution_id": row.state["execution_id"],
                }
                if request.action == "retry":
                    replacement_id = uuid.uuid4().hex
                    metadata["replacement_record_id"] = replacement_id
                    decision["replacement_record_id"] = replacement_id
                    calls[index] = {
                        **calls[index],
                        "record_id": replacement_id,
                        "status": "intent",
                        "raw_results": [],
                        "protocol_results": [],
                        "process_records": [],
                        "context_position": None,
                        "recovery": {"replaces_record_id": record_id},
                    }
                    session.add(
                        AgentWorkflowToolCall(
                            record_id=replacement_id,
                            run_id=run_id,
                            attempt_id=stored.attempt_id,
                            invocation_id=stored.invocation_id,
                            call_id=stored.call_id,
                            call_index=stored.call_index,
                            name=stored.name,
                            args=stored.args,
                            execution_id=row.state["execution_id"],
                        )
                    )
                    session.add(
                        AgentWorkflowToolRecovery(
                            record_id=replacement_id,
                            run_id=run_id,
                            data={"replaces_record_id": record_id},
                        )
                    )
                elif request.action in {"confirm_result", "confirm_failure"}:
                    structured = None
                    if (
                        row.snapshot["engine"] == "plugin_task_v2"
                        and request.action == "confirm_result"
                    ):
                        try:
                            structured = json.loads(request.result or "")
                        except ValueError as exc:
                            raise RoleAssetError(
                                "Enter the original structured result as a JSON object"
                            ) from exc
                        if not isinstance(
                            structured, dict
                        ) or validate_component_result(
                            row.snapshot["definition"]["output_contract"],
                            structured,
                            row.snapshot.get("port_sources", {}),
                        ):
                            raise RoleAssetError(
                                "The confirmed result does not match the component output contract"
                            )
                    raw = CallToolResult(
                        content=[TextContent(type="text", text=request.result or "")],
                        structuredContent=structured,
                        isError=request.action == "confirm_failure",
                    ).model_dump(mode="json")
                    call = calls[index]
                    call["raw_results"].append(raw)
                    call["protocol_results"] = []
                    call["process_records"].append(
                        {
                            "id": record_id + ":decision:" + request.request_id,
                            "data": {
                                "id": stored.call_id,
                                "tool_record_id": stored.record_id,
                                "index": len(call["raw_results"]) - 1,
                                "name": stored.name,
                                "raw_result": raw,
                            },
                        }
                    )
                    call["status"] = "result_saved"
                    stored.status = "result_saved"
                    stored.raw_results = call["raw_results"]
                    session.add(stored)
                decisions.append(decision)
                if request.action != "retry":
                    calls[index]["recovery"] = metadata
                calls[index]["recovery"]["pending_decision"] = {
                    "record_id": record_id,
                    "request_id": request.request_id,
                }
                if recovery is None:
                    recovery = AgentWorkflowToolRecovery(
                        record_id=record_id, run_id=run_id
                    )
                recovery.data = metadata
                session.add(recovery)
                # Acceptance and the intent to continue must survive a pause or
                # process loss between this commit and acquiring execution ownership.
                row.state = {**row.state, "resolution_pending": True}
                session.add(row)
                checkpoint.started_tools = [
                    identity
                    for identity in checkpoint.started_tools
                    if identity != stored.call_id or request.action == "reconcile"
                ]
                data["started_tools"] = checkpoint.started_tools
                checkpoint.data = data
                session.add(checkpoint)
                session.add(
                    AgentWorkflowEvent(
                        run_id=run_id,
                        kind="tool.resolved",
                        data={
                            "record_id": record_id,
                            "request_id": request.request_id,
                            "action": request.action,
                        },
                    )
                )
                await session.commit()
        if duplicate:
            return await self.get_run(username, run_id)
        try:
            return await self.recover_run(username, run_id)
        except RoleAssetError:
            # The decision is already accepted. Admission, capacity or another
            # local wait may defer execution without turning acceptance into failure.
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
            applied_choices = [
                call
                for call in data.get("tool_calls", [])
                if call["name"] == "ask_user_choice" and call["status"] == "applied"
            ]
            if applied_choices:
                row = await session.get(AgentWorkflowRun, run_id)
                if row is None or row.owner_username != username:
                    raise RoleAssetError("Run not found", status=404)
                control = await self._lock_control(session, row)
                if control.status == "cancelled":
                    raise RoleAssetError("Run was cancelled", status=409)
                if control.status == "paused":
                    for call in applied_choices:
                        todo = await session.get(
                            AgentWorkflowTodo, "choice:" + call["record_id"]
                        )
                        if todo and todo.data["status"] == "answered":
                            raise RoleAssetError(
                                "Continue the run before consuming a human answer",
                                [
                                    {
                                        "path": "control",
                                        "code": "PAUSED",
                                        "message": "The human answer remains saved.",
                                    }
                                ],
                                status=409,
                            )
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
            if checkpoint and (
                data.get("model_calls", 0) < checkpoint.data.get("model_calls", 0)
                or data["invocation_id"] != checkpoint.invocation_id
                and data["phase"] != "request"
                or data["invocation_id"] == checkpoint.invocation_id
                and checkpoint.phase == "tools_applied"
                and data != checkpoint.data
            ):
                raise RoleAssetError(
                    "A newer execution checkpoint is already saved", status=409
                )
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
            for receipt in data.get("member_messages", []):
                message = await session.get(AgentWorkflowMessage, receipt["message_id"])
                consumption = {
                    "task_run_id": run_id,
                    "attempt_id": attempt_id,
                    "invocation_id": data["invocation_id"],
                    "context_position": receipt["context_position"],
                }
                if (
                    message is None
                    or message.root_run_id != _root_run_id(row)
                    or message.scope_id != row.state["scope_id"]
                    or message.recipient_member_id
                    != row.snapshot["member"]["member_id"]
                ):
                    raise RoleAssetError(
                        "Message is outside this member's scope", status=403
                    )
                if (
                    message.consumption is not None
                    and message.consumption != consumption
                ):
                    raise RoleAssetError(
                        "Message was already consumed by another context", status=409
                    )
                message.consumption = consumption
                session.add(message)
            checkpoint.result_submission = (data.get("response") or {}).get(
                "tools_call_name"
            ) == ["submit_task_result"]
            for call_index, call in enumerate(data.get("tool_calls", [])):
                previous = await session.get(AgentWorkflowToolCall, call["record_id"])
                if previous:
                    ranks = {
                        "intent": 0,
                        "started": 1,
                        "unknown": 1,
                        "result_saved": 2,
                        "applied": 3,
                    }
                    if (
                        previous.run_id != run_id
                        or previous.attempt_id != attempt_id
                        or previous.invocation_id != data["invocation_id"]
                        or previous.call_id != call["call_id"]
                        or previous.name != call["name"]
                        or previous.args != call["args"]
                        or ranks[call["status"]] < ranks[previous.status]
                        or call["raw_results"][: len(previous.raw_results)]
                        != previous.raw_results
                        or previous.status in {"result_saved", "applied"}
                        and call["raw_results"] != previous.raw_results
                        or previous.context_position is not None
                        and previous.context_position != call["context_position"]
                    ):
                        raise RoleAssetError(
                            "Tool call identity or saved result was reused", status=409
                        )
                else:
                    previous = AgentWorkflowToolCall(
                        record_id=call["record_id"],
                        run_id=run_id,
                        attempt_id=attempt_id,
                        invocation_id=data["invocation_id"],
                        call_id=call["call_id"],
                        call_index=call_index,
                        name=call["name"],
                        args=call["args"],
                        execution_id=execution_id,
                    )
                previous.status = call["status"]
                previous.execution_id = execution_id
                if call["status"] != "intent" and previous.started_execution_id is None:
                    previous.started_execution_id = execution_id
                previous.raw_results = call["raw_results"]
                previous.context_position = call["context_position"]
                session.add(previous)
                if call["name"] == "ask_user_choice" and call["status"] == "applied":
                    todo = await session.get(
                        AgentWorkflowTodo, "choice:" + call["record_id"]
                    )
                    if todo is not None:
                        if (
                            todo.task_run_id != run_id
                            or todo.data.get("attempt_id") != attempt_id
                            or todo.data["status"] not in {"answered", "consumed"}
                            or call["context_position"] is None
                        ):
                            raise RoleAssetError(
                                "Human answer cannot be applied to this context",
                                status=409,
                            )
                        if todo.data["status"] == "answered":
                            todo.data = {
                                **todo.data,
                                "status": "consumed",
                                "consumed_at": time.time(),
                                "consumption": {
                                    "tool_record_id": call["record_id"],
                                    "attempt_id": attempt_id,
                                    "invocation_id": data["invocation_id"],
                                    "context_position": call["context_position"],
                                },
                            }
                            session.add(todo)
                            session.add(
                                AgentWorkflowEvent(
                                    run_id=run_id,
                                    kind="human.consumed",
                                    data={
                                        "todo_id": todo.todo_id,
                                        "tool_record_id": call["record_id"],
                                    },
                                )
                            )
                if "recovery" in call:
                    recovery = await session.get(
                        AgentWorkflowToolRecovery, call["record_id"]
                    )
                    if recovery is None:
                        recovery = AgentWorkflowToolRecovery(
                            record_id=call["record_id"], run_id=run_id
                        )
                    recovery.data = call["recovery"]
                    session.add(recovery)
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
        """Interrupt abandoned executions and resume already accepted decisions.

        Startup must be exclusive because this sweep fences previous owners.

        Returns:
            None. Paused or uncertain calls remain stopped; accepted continuations
            use the original attempt when whole-run admission permits execution.
        """
        async with self.db.get_db() as session:
            for receipt in (
                (
                    await session.execute(
                        select(AgentWorkflowUsage)
                        .join(
                            AgentWorkflowRun,
                            AgentWorkflowUsage.run_id == AgentWorkflowRun.run_id,
                        )
                        .where(
                            AgentWorkflowRun.snapshot["root_run_id"]
                            .as_string()
                            .is_not(None),
                            AgentWorkflowUsage.data["quality"].as_string()
                            == "in_flight",
                        )
                    )
                )
                .scalars()
                .all()
            ):
                receipt.data = {
                    **receipt.data,
                    "quality": "unknown",
                    "estimated_tokens": receipt.data["reserved_tokens"] or None,
                }
                session.add(receipt)
            rows = (
                (
                    await session.execute(
                        select(AgentWorkflowRun).where(
                            AgentWorkflowRun.snapshot["root_run_id"]
                            .as_string()
                            .is_not(None),
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
                            AgentWorkflowRun.snapshot["root_run_id"]
                            .as_string()
                            .is_not(None),
                            col(AgentWorkflowRun.status).in_(
                                ["running", "interrupted"]
                            ),
                            AgentWorkflowRun.snapshot["engine"].as_string()
                            != "graph_v2",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for run in abandoned:
                await self._lock_control(session, run)
                await session.refresh(run)
                if run.status not in {"running", "interrupted"}:
                    continue
                if run.snapshot.get(
                    "engine"
                ) == "native_task_v2" and await session.scalar(
                    select(AgentWorkflowUsage.receipt_id)
                    .where(
                        AgentWorkflowUsage.run_id == run.run_id,
                        AgentWorkflowUsage.attempt_id == run.state["attempt_id"],
                    )
                    .limit(1)
                ):
                    # A receipt proves admission reached the call boundary even
                    # when a result was never committed. Do not silently replay it.
                    reason = "Native task stopped after call admission. Retry with the original inputs."
                    run.status = "failed"
                    run.state = {**run.state, "reason": reason}
                    session.add(run)
                    session.add(
                        AgentWorkflowEvent(
                            run_id=run.run_id,
                            kind="task.failed",
                            data={
                                "attempt_id": run.state["attempt_id"],
                                "reason": reason,
                            },
                        )
                    )
                    continue
                checkpoint = await session.get(AgentWorkflowCheckpoint, run.run_id)
                if (
                    run.snapshot.get("parent_run_id")
                    and checkpoint is None
                    and run.pending_result is None
                ):
                    if run.status == "running" and not run.state.get("execution_id"):
                        # A committed claim without an owner is still dispatchable.
                        continue
                    # The built-in executor saves its first request before any
                    # external model/tool call. Ownership alone is not work;
                    # fence it and reclaim the same frozen attempt if no request
                    # or accepted submission reached durable storage.
                    run.status = "pending"
                    run.state = {
                        **run.state,
                        "execution_id": None,
                        "retry_pending": True,
                        "reason": None,
                    }
                    session.add(run)
                    continue
                if run.status != "running":
                    continue
                run.status = "interrupted"
                if checkpoint:
                    data = copy.deepcopy(checkpoint.data)
                    for call in data.get("tool_calls", []):
                        if call["status"] == "started":
                            call["status"] = "unknown"
                            stored = await session.get(
                                AgentWorkflowToolCall, call["record_id"]
                            )
                            if stored:
                                stored.status = "unknown"
                                session.add(stored)
                    checkpoint.data = data
                    session.add(checkpoint)
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
            resume = (
                (
                    await session.execute(
                        select(AgentWorkflowRun).where(
                            AgentWorkflowRun.snapshot["root_run_id"]
                            .as_string()
                            .is_not(None),
                            col(AgentWorkflowRun.status).in_(
                                ["interrupted", "failed", "blocked"]
                            ),
                            (
                                (col(AgentWorkflowRun.status) == "interrupted")
                                & (
                                    (
                                        AgentWorkflowRun.state[
                                            "paused_resume"
                                        ].as_boolean()
                                        == true()
                                    )
                                    | (
                                        AgentWorkflowRun.state[
                                            "retry_pending"
                                        ].as_boolean()
                                        == true()
                                    )
                                )
                            )
                            | (
                                AgentWorkflowRun.state[
                                    "resolution_pending"
                                ].as_boolean()
                                == true()
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
        for row in resume:
            current = await self.get_run(row.owner_username, row.run_id)
            if current["control"]["status"] == "active":
                try:
                    await self.recover_run(row.owner_username, row.run_id)
                except RoleAssetError:
                    # Preserve local tool uncertainty or missing frozen resources.
                    pass

        async with self.db.get_db() as session:
            roots = (
                (
                    await session.execute(
                        select(AgentWorkflowRun).where(
                            AgentWorkflowRun.snapshot["root_run_id"]
                            .as_string()
                            .is_not(None),
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
        async with self.db.get_db() as session:
            waiting_roots = (
                await session.execute(
                    select(
                        AgentWorkflowRun.owner_username, AgentWorkflowTodo.root_run_id
                    )
                    .join(
                        AgentWorkflowTodo,
                        AgentWorkflowRun.run_id == AgentWorkflowTodo.root_run_id,
                    )
                    .where(
                        AgentWorkflowRun.snapshot["root_run_id"]
                        .as_string()
                        .is_not(None),
                        AgentWorkflowTodo.data["source_kind"].as_string()
                        == "tool_call",
                        AgentWorkflowTodo.data["status"].as_string() == "answered",
                    )
                    .distinct()
                )
            ).all()
        for owner, root_id in waiting_roots:
            await self._resume_human_choices(owner, root_id)
        await self.sweep_human_deadlines()

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

    async def _require_component_owner(
        self,
        username: str,
        path: str,
        *,
        resource: str = "installed workflow components",
    ) -> None:
        """Recheck current Dashboard privileges for a saved component binding.

        Args:
            username: Stable resource owner.
            path: Field to report when access is denied.
            resource: Display name of the privileged resource.

        Raises:
            RoleAssetError: An enabled Dashboard administrator is required.
        """
        async with self.db.get_db() as session:
            account = await session.scalar(
                select(DashboardAccount).where(
                    or_(
                        DashboardAccount.legacy_owner == username,
                        and_(
                            col(DashboardAccount.legacy_owner).is_(None),
                            DashboardAccount.account_id == username,
                        ),
                    )
                )
            )
        if account is None or not account.enabled or account.role != "admin":
            message = f"The workflow owner cannot access {resource}"
            raise RoleAssetError(
                message,
                [{"path": path, "code": "FORBIDDEN", "message": message}],
                status=403,
            )

    async def _require_decision_provider(
        self, username: str, provider_id: str, path: str
    ) -> DecisionProvider:
        """Resolve an exact decision resource under the owner's current permissions.

        Args:
            username: Owner of the bound workflow.
            provider_id: Provider instance bound to this node.
            path: Field used for diagnostics.

        Returns:
            An available decision provider.

        Raises:
            RoleAssetError: The owner is unauthorized or the provider is unavailable.
        """
        await self._require_component_owner(
            username, path, resource="decision providers"
        )
        manager = getattr(self.roles.core_lifecycle, "provider_manager", None)
        provider = await manager.get_provider_by_id(provider_id) if manager else None
        if not isinstance(provider, DecisionProvider):
            message = "The selected decision provider is unavailable"
            raise RoleAssetError(
                message, [{"path": path, "code": "UNAVAILABLE", "message": message}]
            )
        return provider

    async def _require_native_knowledge_base(
        self, username: str, kb_id: str, path: str
    ) -> "KnowledgeBaseManager":
        """Resolve one exact KB under the Dashboard knowledge access policy.

        Args:
            username: Stable resource owner who selected the knowledge base.
            kb_id: Local knowledge base identity frozen in the node.
            path: Original canvas field for diagnostics.

        Returns:
            The live knowledge base manager for this selection.

        Raises:
            RoleAssetError: The owner or selected knowledge base is unavailable.
        """
        await self._require_component_owner(username, path, resource="knowledge bases")
        manager = getattr(self.roles.core_lifecycle, "kb_manager", None)
        kb = await manager.get_kb(kb_id) if manager else None
        if kb is None or kb.init_error:
            message = "The selected knowledge base is unavailable"
            raise RoleAssetError(
                message, [{"path": path, "code": "UNAVAILABLE", "message": message}]
            )
        return manager

    async def _read_native_document(self, username: str, reference: dict) -> dict:
        """Read and parse an authorized immutable file in the shared parser slot.

        Args:
            username: Stable owner of the exact artifact.
            reference: Bound artifact identity, version, and file identity.

        Returns:
            Extracted text and the managed filename.

        Raises:
            RoleAssetError: The file is unavailable or its version is not approved.
            ValueError: The document format or its size is unsupported.
            TimeoutError: The parser did not finish within the node's time limit.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 60
        await asyncio.wait_for(self._document_parser_slot.acquire(), timeout=60)
        try:
            file = await self.get_file(username, **reference)
            if len(file["content"]) > 10 * 1024 * 1024:
                raise ValueError("Document exceeds 10 MiB")
            filename = file["filename"]
            parser = await select_parser(Path(filename).suffix.lower())
            conversion = asyncio.create_task(
                asyncio.to_thread(
                    lambda: asyncio.run(parser.parse(file["content"], filename))
                )
            )
        except BaseException:
            self._document_parser_slot.release()
            raise

        def release_parser_slot(task: asyncio.Task) -> None:
            """Release capacity and observe errors from a late conversion.

            Args:
                task: Conversion that is no longer using the parser slot.
            """
            self._document_parser_slot.release()
            if not task.cancelled():
                task.exception()

        # A cancelled or timed-out attempt leaves the lease with the parser
        # until the actual CPU work stops. It cannot publish a late result.
        conversion.add_done_callback(release_parser_slot)
        parsed = await asyncio.wait_for(
            asyncio.shield(conversion), timeout=max(0, deadline - loop.time())
        )
        if len(parsed.text) > 100_000:
            raise ValueError("Extracted text exceeds 100,000 characters")
        return {"text": parsed.text, "filename": filename}

    async def _execute_native_node(
        self, username: str, run_id: str, execution_id: str, snapshot: dict, state: dict
    ) -> None:
        """Execute a bound native node and commit a result under the attempt fence.

        Args:
            username: Authenticated workflow owner.
            run_id: Persisted task identity.
            execution_id: Current durable executor fence.
            snapshot: Frozen configuration, inputs and provenance.
            state: Current task attempt identity.

        Raises:
            RoleAssetError: Admission, resource or component execution failed.
        """
        path = snapshot["component_path"]
        inputs = snapshot["inputs"]
        transform = snapshot["transform"]
        attempt = state["attempt_id"]

        reads_file = transform == "read_document_v1"
        makes_decision = transform == "decision_v1"
        diagnostic_path = (
            f"{path}.decision_provider_id"
            if makes_decision
            else f"{path}.inputs.file"
            if reads_file
            else f"{path}.knowledge_base_id"
        )
        decision_details = None
        decision_usage = {}
        usage_request = {}
        if makes_decision:
            # Reserve conservatively because decision endpoints do not expose a tokenizer.
            request_text = json.dumps(
                [inputs["state"], snapshot["definition"]["decision"]],
                ensure_ascii=False,
            )
            usage_request = {
                "input_tokens": len(request_text.encode("utf-8")) + 64,
                "max_output_tokens": 1,
            }
        invocation = uuid.uuid4().hex
        while True:
            await self._await_execution_permission(username, run_id, execution_id)
            if makes_decision:
                provider_id = snapshot["decision_provider_id"]
                decision_provider = await self._require_decision_provider(
                    username, provider_id, diagnostic_path
                )
            elif not reads_file:
                kb_id = snapshot["knowledge_base_id"]
                manager = await self._require_native_knowledge_base(
                    username, kb_id, diagnostic_path
                )
            try:
                receipt = await self.begin_usage(
                    username,
                    run_id,
                    attempt,
                    execution_id,
                    {
                        "receipt_id": invocation,
                        "invocation_id": invocation,
                        "kind": "model"
                        if makes_decision
                        else "tool"
                        if reads_file
                        else "query",
                        "name": transform,
                        **usage_request,
                    },
                )
            except RoleAssetError as exc:
                if any(issue["code"] == "PAUSED" for issue in exc.fields):
                    continue
                raise
            if not receipt.get("waiting"):
                break
            await asyncio.sleep(0.1)

        try:
            if makes_decision:
                decision_provider = await self._require_decision_provider(
                    username, provider_id, diagnostic_path
                )
                spec = snapshot["definition"]["decision"]
                criteria = spec["criteria"]
                instructions = spec["instructions"]
                if spec["kind"] == "boolean":
                    question = DecisionBooleanQuestion(instructions, criteria)
                elif spec["kind"] == "choice":
                    question = DecisionChoiceQuestion(instructions, criteria)
                else:
                    question = DecisionScoreQuestion(instructions, criteria)
                answers = await decision_provider.ask(
                    {"answer": question}, inputs["state"]
                )
                decision_usage = getattr(answers, "usage", {})
                answer = answers["answer"]
                if isinstance(answer, DecisionBooleanAnswer):
                    result = {
                        "matched": answer.probability >= spec["threshold"],
                        "probability": answer.probability,
                    }
                elif isinstance(answer, DecisionChoiceAnswer):
                    result = {
                        "choice": answer.choice,
                        "probability": answer.probability,
                    }
                elif isinstance(answer, DecisionScoreAnswer):
                    result = {"score": answer.score}
                else:
                    raise DecisionError("Unexpected answer type")
                decision_details = {
                    "provider_id": provider_id,
                    "model": decision_provider.get_model(),
                }
            elif reads_file:
                result = await self._read_native_document(username, inputs["file"])
            else:
                warnings: list[str] = []
                search = (
                    await asyncio.wait_for(
                        manager.retrieve(
                            query=inputs["query"], kb_ids=[kb_id], warnings=warnings
                        ),
                        timeout=60,
                    )
                    or {}
                )
                if not search.get("results") and warnings:
                    message = "Knowledge retrieval failed without usable matches"
                    raise RoleAssetError(
                        message,
                        [
                            {
                                "path": diagnostic_path,
                                "code": warnings[0],
                                "message": message,
                            }
                        ],
                    )
                result = {
                    "text": search.get("context_text", ""),
                    "matches": search.get("results", []),
                    "warnings": warnings,
                }
        except RoleAssetError as exc:
            if exc.fields:
                raise
            raise RoleAssetError(
                str(exc),
                [{"path": diagnostic_path, "code": "REFERENCE", "message": str(exc)}],
                status=exc.status,
            ) from exc
        except DecisionError as exc:
            message = str(exc) or "Decision provider failed"
            raise RoleAssetError(
                message,
                [{"path": diagnostic_path, "code": "DECISION", "message": message}],
            ) from exc
        except asyncio.TimeoutError as exc:
            message = (
                "Decision request timed out"
                if makes_decision
                else "Document parsing timed out"
                if reads_file
                else "Knowledge retrieval timed out"
            )
            raise RoleAssetError(
                message,
                [{"path": diagnostic_path, "code": "TIMEOUT", "message": message}],
            ) from exc
        except Exception as exc:
            logger.warning(
                "Native workflow node %s failed: %s", transform, type(exc).__name__
            )
            message = (
                "Decision request failed"
                if makes_decision
                else "Could not parse this document. Choose a supported file up to 10 MiB with at most 100,000 extracted characters."
                if reads_file
                else "Knowledge retrieval failed"
            )
            raise RoleAssetError(
                message,
                [{"path": diagnostic_path, "code": "EXECUTION", "message": message}],
            ) from exc
        finally:
            await self.report_usage(
                username,
                invocation,
                execution_id,
                {"total_tokens": 0} if reads_file else decision_usage,
            )

        contract = snapshot["definition"]["output_contract"]
        fields = validate_result(contract, result, port_formats=True)
        if fields:
            raise RoleAssetError(
                "Native node returned invalid data",
                [
                    {
                        **field,
                        "path": f"{path}.output_contract"
                        + field["path"].removeprefix("result"),
                    }
                    for field in fields
                ],
            )
        await self._finish_component_result(
            username,
            run_id,
            execution_id,
            attempt,
            contract,
            result,
            decision_details=decision_details,
        )

    async def _finish_component_result(
        self,
        username: str,
        run_id: str,
        execution_id: str,
        attempt: str,
        contract: dict,
        result: dict,
        *,
        decision_details: dict | None = None,
    ) -> None:
        """Commit a validated node result under the native attempt and control fence.

        Args:
            username: Original workflow owner.
            run_id: Persisted task run.
            execution_id: Current durable executor.
            attempt: Original attempt identity.
            contract: Frozen result contract.
            result: Validated structured output.
            decision_details: Resolved provider and model for a decision node.
        """
        await self._await_execution_permission(username, run_id, execution_id)
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            assert row is not None
            control = await self._lock_control(session, row)
            await self._require_execution_owner(session, run_id, execution_id)
            await session.refresh(row)
            if control.status == "cancelled":
                raise RoleAssetError("Run was cancelled", status=409)
            row.status = "completed"
            row.result = result
            row.state = {**row.state, "reason": None}
            if decision_details is not None:
                row.state["decision"] = decision_details
            session.add(row)
            session.add(
                AgentWorkflowArtifact(
                    artifact_id=run_id,
                    version=1,
                    run_id=run_id,
                    submission_id=attempt,
                    contract=contract,
                    content=result,
                )
            )
            session.add(
                AgentWorkflowEvent(
                    run_id=run_id,
                    kind="task.completed",
                    data={
                        "attempt_id": attempt,
                        "reference": {"artifact_id": run_id, "version": 1},
                    },
                )
            )
            await session.commit()

    async def _pause_debug_write(
        self,
        username: str,
        run_id: str,
        execution_id: str,
        record_id: str,
        operation: str,
        parameters: dict,
        target: object,
        *,
        parameters_complete: bool = True,
    ) -> None:
        """Fence the attempt and keep its write intent separate from tool receipts.

        Args:
            username: Owner authorized to run and inspect the fork.
            run_id: Fork task holding the current invocation.
            execution_id: Executor admitted for the current attempt.
            record_id: Identity of the captured invocation.
            operation: Resolved connector or tool name.
            parameters: Actual supplied arguments at the execution boundary.
            target: Destination identified by the installed implementation, if available.
            parameters_complete: Whether the arguments meet the installed tool schema.

        Raises:
            DebugWritePending: Stop dependent execution at the saved boundary.
            CheckpointRecoveryError: The fork lost execution admission.
        """
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            assert row is not None
            control = await self._lock_control(session, row)
            await self._require_execution_owner(session, run_id, execution_id)
            await session.refresh(row)
            root = await session.get(AgentWorkflowRun, control.run_id)
            if (
                control.status == "cancelled"
                or root is None
                or root.owner_username != username
                or not root.snapshot.get("debug_fork")
            ):
                raise CheckpointRecoveryError("Debug write preview is no longer active")
            source = root.snapshot["debug_fork"]["source"]
            historical = (
                await session.execute(
                    select(AgentWorkflowToolCall)
                    .where(
                        col(AgentWorkflowToolCall.run_id) == source["task_run_id"],
                        col(AgentWorkflowToolCall.attempt_id) == source["attempt_id"],
                        col(AgentWorkflowToolCall.name) == operation,
                        col(AgentWorkflowToolCall.status) != "intent",
                    )
                    .order_by(
                        col(AgentWorkflowToolCall.created_at),
                        col(AgentWorkflowToolCall.record_id),
                    )
                )
            ).scalars()
            destination = target if isinstance(target, str) and target.strip() else None
            ready = bool(destination and parameters_complete)
            reason = (
                "Paused before external write"
                if ready
                else "External write parameters are incomplete or invalid"
                if destination
                else "External write target could not be determined"
            )
            row.status = "blocked"
            row.state = {
                **row.state,
                "reason": reason,
                "debug_write_preview": {
                    "record_id": record_id,
                    "status": "pending" if ready else "unavailable",
                    "operation": operation,
                    "target": destination,
                    "parameters": parameters,
                    "historical_receipts": [
                        {
                            "record_id": call.record_id,
                            "status": call.status,
                            "results": call.raw_results,
                        }
                        for call in historical
                    ],
                },
            }
            session.add(row)
            session.add(
                AgentWorkflowEvent(
                    run_id=run_id,
                    kind="task.blocked",
                    data={"attempt_id": row.state["attempt_id"], "reason": reason},
                )
            )
            await session.commit()
        raise DebugWritePending(reason)

    async def _execute_plugin_component(
        self,
        username: str,
        run_id: str,
        execution_id: str,
        snapshot: dict,
        state: dict,
        checkpoint: dict | None,
        *,
        debug_fork: bool,
    ) -> None:
        """Execute one deterministic tool call using the native durable receipt.

        Args:
            username: Original workflow owner.
            run_id: Persisted task run.
            execution_id: Current durable executor.
            snapshot: Frozen component, resource binding and node input values.
            state: Original task attempt identity.
            checkpoint: Native tool receipt envelope when continuing an attempt.
            debug_fork: Pause external components at their original invocation boundary.

        Raises:
            CheckpointRecoveryError: The external outcome remains unconfirmed.
            RoleAssetError: The component or result violates its frozen contract.
        """
        node = snapshot["definition"]
        path = snapshot["component_path"]
        attempt = state["attempt_id"]
        await self._await_execution_permission(username, run_id, execution_id)
        component = await self._resolve_component(username, node, path)
        if checkpoint is None:
            invocation = uuid.uuid5(
                uuid.NAMESPACE_URL, f"{run_id}:{attempt}:component"
            ).hex
            checkpoint = {
                "format_version": 2,
                "invocation_id": invocation,
                "tool_result_files": {},
                "tool_calls": [
                    {
                        "record_id": invocation,
                        "call_id": invocation,
                        "name": node["component"]["id"],
                        "args": snapshot["inputs"],
                        "status": "intent",
                        "raw_results": [],
                        "protocol_results": [],
                        "process_records": [],
                        "context_position": None,
                    }
                ],
            }
        call = checkpoint["tool_calls"][0]
        identity = call["record_id"]
        if debug_fork and component.effect == "external" and call["status"] == "intent":
            await self._pause_debug_write(
                username,
                run_id,
                execution_id,
                identity,
                call["name"],
                call["args"],
                call["args"].get(component.write_target)
                if component.write_target
                else None,
            )
        recovery = call.setdefault("recovery", {})
        context = ContextWrapper(
            context=WorkflowComponentContext(identity, node.get("knowledge_base_id")),
            tool_call_timeout=60,
        )

        async def admit(kind: str) -> str:
            """Acquire one step under the root and recheck the live binding.

            Args:
                kind: Tool delivery or authoritative query.

            Returns:
                Usage receipt owned by this execution.
            """
            receipt_id = uuid.uuid4().hex
            while True:
                await self._await_execution_permission(username, run_id, execution_id)
                try:
                    receipt = await self.begin_usage(
                        username,
                        run_id,
                        attempt,
                        execution_id,
                        {
                            "receipt_id": receipt_id,
                            "invocation_id": identity,
                            "kind": kind,
                            "name": component.name,
                        },
                    )
                except RoleAssetError as exc:
                    if any(issue["code"] == "PAUSED" for issue in exc.fields):
                        continue
                    raise
                if not receipt.get("waiting"):
                    return receipt_id
                await asyncio.sleep(0.1)

        if call["status"] in {"started", "unknown"}:
            declaration = recovery.get("declaration")
            if declaration != component.recovery_declaration or not has_tool_recovery(
                declaration
            ):
                raise CheckpointRecoveryError(
                    "Component outcome is unknown and requires confirmation."
                )
            assert declaration is not None
            if component.recovery and component.recovery.query is not None:
                usage = await admit("query")
                try:
                    component = await self._resolve_component(username, node, path)
                    if component.recovery is None or component.recovery.query is None:
                        raise CheckpointRecoveryError(
                            "The original component query is unavailable."
                        )
                    queried = await asyncio.wait_for(
                        component.recovery.query(
                            context, identity, copy.deepcopy(call["args"])
                        ),
                        timeout=context.tool_call_timeout,
                    )
                    queried = ToolRecoveryResult.model_validate(queried)
                except (RoleAssetError, CheckpointRecoveryError):
                    raise
                except Exception as exc:
                    queried = ToolRecoveryResult(
                        status="unknown",
                        evidence=f"Status query failed: {type(exc).__name__}",
                    )
                finally:
                    await self.report_usage(
                        username, usage, execution_id, {"total_tokens": 0}
                    )
                recovery.setdefault("queries", []).append(
                    queried.model_dump(mode="json", exclude={"results"})
                )
                if queried.status in {"succeeded", "failed"}:
                    call["raw_results"] = [
                        raw.model_dump(mode="json") for raw in queried.results
                    ]
                    call["status"] = "result_saved"
                checkpoint["phase"] = (
                    "tool_result"
                    if call["status"] == "result_saved"
                    else "tool_progress"
                )
                await self.save_checkpoint(
                    username, run_id, attempt, execution_id, checkpoint
                )
                safe_delivery = queried.status == "not_executed"
            else:
                safe_delivery = bool(
                    declaration.get("safe_retry")
                    or declaration.get("idempotency_key_arg")
                )
            if call["status"] != "result_saved" and not safe_delivery:
                raise CheckpointRecoveryError(
                    "Component outcome is unknown and requires confirmation."
                )
            if safe_delivery:
                recovery.setdefault("retries", []).append(
                    {
                        "identity": identity,
                        "reason": "not_executed"
                        if declaration.get("query")
                        else "idempotency_key"
                        if declaration.get("idempotency_key_arg")
                        else "safe_retry",
                        "protocol_before": [],
                    }
                )

        if call["status"] not in {"result_saved", "applied"}:
            usage = await admit("tool")
            try:
                recovery.update(
                    declaration=component.recovery_declaration, identity=identity
                )
                call["status"] = "started"
                checkpoint.update(
                    phase="tools_started", started_tools=[call["call_id"]]
                )
                await self.save_checkpoint(
                    username, run_id, attempt, execution_id, checkpoint
                )
                component = await self._resolve_component(username, node, path)
                args = copy.deepcopy(call["args"])
                if component.recovery and component.recovery.idempotency_key_arg:
                    args[component.recovery.idempotency_key_arg] = identity
                token = current_tool_invocation.set(identity)
                try:
                    result = await asyncio.wait_for(
                        component.execute(context, args),
                        timeout=context.tool_call_timeout,
                    )
                except Exception as exc:
                    logger.warning(
                        "Workflow component %s raised %s",
                        component.name,
                        type(exc).__name__,
                    )
                    call["status"] = "unknown"
                    checkpoint["phase"] = "tool_progress"
                    await self.save_checkpoint(
                        username, run_id, attempt, execution_id, checkpoint
                    )
                    raise CheckpointRecoveryError(
                        "Component outcome is unknown and requires confirmation."
                    ) from exc
                finally:
                    current_tool_invocation.reset(token)
                call["raw_results"] = [
                    CallToolResult(
                        content=[],
                        structuredContent=result if isinstance(result, dict) else None,
                    ).model_dump(mode="json")
                ]
                call["status"] = "result_saved"
                checkpoint["phase"] = "tool_result"
                await self.save_checkpoint(
                    username, run_id, attempt, execution_id, checkpoint
                )
            finally:
                await self.report_usage(
                    username, usage, execution_id, {"total_tokens": 0}
                )

        raw = call["raw_results"][-1]
        if raw["isError"]:
            raise RoleAssetError(
                "The original component invocation was confirmed to have failed"
            )
        result = raw.get("structuredContent")
        contract = node["output_contract"]
        fields = (
            validate_component_result(
                contract, result, snapshot.get("port_sources", {})
            )
            if isinstance(result, dict)
            else [
                {
                    "path": "result",
                    "code": "TYPE",
                    "message": "Expected a structured object",
                }
            ]
        )
        if fields:
            raise RoleAssetError(
                "Component returned invalid data",
                [
                    {
                        **field,
                        "path": f"{path}.output_contract"
                        + field["path"].removeprefix("result"),
                    }
                    for field in fields
                ],
            )
        await self._await_execution_permission(username, run_id, execution_id)
        await self._resolve_component(username, node, path)
        call["status"] = "applied"
        checkpoint["phase"] = "tools_applied"
        await self.save_checkpoint(username, run_id, attempt, execution_id, checkpoint)
        await self._finish_component_result(
            username, run_id, execution_id, attempt, contract, result
        )

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
            row = await session.get(AgentWorkflowRun, run_id)
            assert row is not None
            control = await self._lock_control(session, row)
            if control.status == "cancelled" or (
                control.status == "paused"
                and execution_id is None
                and snapshot.get("parent_run_id")
            ):
                return
            await session.refresh(row)
            if row.state.get("attempt_id") != state.get("attempt_id"):
                return
            state = copy.deepcopy(row.state)
            if execution_id is None:
                execution_id = uuid.uuid4().hex
                state = {**state, "execution_id": execution_id}
                changed = await session.execute(
                    update(AgentWorkflowRun)
                    .where(
                        col(AgentWorkflowRun.run_id) == run_id,
                        col(AgentWorkflowRun.status) == "running",
                        AgentWorkflowRun.state["execution_id"].as_string().is_(None),
                        AgentWorkflowRun.state["attempt_id"].as_string()
                        == state["attempt_id"],
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
            root = await session.get(AgentWorkflowRun, control.run_id)
            debug_fork = bool(root and root.snapshot.get("debug_fork"))
        member = snapshot.get("member", {})
        ports = None
        tools: list[FunctionTool] = []
        status = "failed"
        outcome: dict[str, object] = {}
        checkpoint_error: str | RoleAssetError | None = None
        current_task = asyncio.current_task()
        assert current_task is not None
        self._executions[run_id] = current_task
        try:
            if snapshot.get("engine") == "plugin_task_v2":
                await self._execute_plugin_component(
                    username,
                    run_id,
                    execution_id,
                    snapshot,
                    state,
                    checkpoint,
                    debug_fork=debug_fork,
                )
                return
            if snapshot.get("engine") == "native_task_v2":
                await self._execute_native_node(
                    username, run_id, execution_id, snapshot, state
                )
                return
            await self._await_execution_permission(username, run_id, execution_id)
            effective = snapshot["effective"]
            if debug_fork and effective["runner"]["type"] != "local":
                raise RoleAssetError(
                    "The configured runner cannot preview external writes", status=409
                )
            ports = self.ports_factory(snapshot.get("initiator", username))

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

            async def request_human(record_id: str, spec: dict) -> dict:
                """Bind a built-in choice to this task's durable executor.

                Args:
                    record_id: Original runner-owned tool record.
                    spec: Validated question with no client-provided identities.

                Returns:
                    Previously saved choice for the same tool call.
                """
                nonlocal checkpoint_error
                try:
                    return await self.request_human_choice(
                        username,
                        run_id,
                        state["attempt_id"],
                        execution_id,
                        record_id,
                        spec,
                    )
                except HumanInputRequired:
                    raise
                except Exception as exc:
                    checkpoint_error = "Failed to persist the original human choice"
                    raise ProcessPersistenceError(checkpoint_error) from exc

            tools = [
                SubmitTaskResultTool(
                    accept, snapshot["definition"].get("output_contract")
                )
            ]
            if snapshot.get("coordinator"):
                root = await self.get_run(username, snapshot["parent_run_id"])

                async def manage(payload: dict) -> dict:
                    """Apply a decision with the active coordinator's durable authority.

                    Args:
                        payload: Validated dynamic member/task operation.

                    Returns:
                        Committed decision or full task observations.
                    """
                    nonlocal checkpoint_error
                    try:
                        return await self.manage_team(
                            username, run_id, state["attempt_id"], execution_id, payload
                        )
                    except RoleAssetError:
                        raise
                    except Exception as exc:
                        checkpoint_error = "Failed to save team decision"
                        raise ProcessPersistenceError(checkpoint_error) from exc

                tools.append(
                    TeamManageTool(
                        manage,
                        root["snapshot"]["allowed_roles"],
                        {
                            key: {
                                "asset_id": child["asset_id"],
                                "version": child["asset_version"],
                                "name": child["definition"]["name"],
                                "required_inputs": child["definition"][
                                    "required_inputs"
                                ],
                                "output_contract": next(
                                    task.get("output_contract")
                                    for task in child["definition"]["tasks"]
                                    if task["id"] == child["result_task_id"]
                                ),
                            }
                            for key, child in root["snapshot"]
                            .get("allowed_workflows", {})
                            .items()
                        },
                    )
                )
            if snapshot.get("parent_run_id") and not snapshot.get("graph_task"):

                async def communicate(payload: dict) -> dict:
                    """Apply communication using the current attempt's authority.

                    Args:
                        payload: Scoped member message operation.

                    Returns:
                        Durable delivery receipt or visible member identities.
                    """
                    nonlocal checkpoint_error
                    try:
                        return await self.communicate_member(
                            username, run_id, state["attempt_id"], execution_id, payload
                        )
                    except RoleAssetError:
                        raise
                    except Exception as exc:
                        checkpoint_error = "Failed to save member communication"
                        raise ProcessPersistenceError(checkpoint_error) from exc

                tools.append(TeamCommunicateTool(communicate))
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
            if snapshot.get("input_artifacts") or snapshot.get("parent_run_id"):

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
                    if (
                        file_id is not None
                        and snapshot.get("graph_task")
                        and not any(
                            source.get("reference")
                            == {
                                "artifact_id": artifact_id,
                                "version": version,
                                "file_id": file_id,
                            }
                            for source in snapshot.get("port_sources", {}).values()
                        )
                    ):
                        raise RoleAssetError(
                            "Graph inputs do not grant file access", status=403
                        )
                    artifact = await self.read_artifact(
                        username,
                        artifact_id,
                        version,
                        run_id,
                        state["attempt_id"],
                        execution_id,
                    )
                    if file_id is None:
                        return artifact
                    file = await self.get_file(
                        username,
                        artifact_id,
                        version,
                        file_id,
                        download=bool(snapshot.get("graph_task")),
                    )
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

                read_tool = ReadArtifactTool(read)
                if snapshot.get("graph_task"):
                    read_tool.description = (
                        "Read only selected fields of an exact input artifact. "
                        "Supply file_id only for an explicitly bound managed file."
                    )
                    if not any(
                        "file_id" in source.get("reference", {})
                        for source in snapshot.get("port_sources", {}).values()
                    ):
                        read_tool.parameters["properties"].pop("file_id", None)
                tools.append(read_tool)
            AgentTeamToolRegistry.register(member["umo"], tools)

            async def preview_tool_write(
                tool: FunctionTool, arguments: dict, record_id: str
            ) -> None:
                """Hold a resolved tool unless it belongs to the fork's own task.

                Args:
                    tool: Tool resolved from the Agent's authorized tool set.
                    arguments: Parameters after the runner's handler filtering.
                    record_id: Durable identity assigned by the runner.
                """
                if type(tool) not in (
                    ReadArtifactTool,
                    StoreTaskFileTool,
                    SubmitTaskResultTool,
                ):
                    try:
                        parameters_complete = Draft202012Validator(
                            tool.parameters, registry=Registry()
                        ).is_valid(arguments)
                    except (TypeError, ValueError, SchemaError, Unresolvable):
                        parameters_complete = False
                    await self._pause_debug_write(
                        username,
                        run_id,
                        execution_id,
                        record_id,
                        tool.name,
                        arguments,
                        arguments.get(tool.write_target) if tool.write_target else None,
                        parameters_complete=parameters_complete,
                    )

            async def admit_work() -> None:
                """Stop the runner while retaining an actionable admission error.

                Raises:
                    CheckpointRecoveryError: Frozen resources or run permission
                        prevent further execution at this boundary.
                """
                nonlocal checkpoint_error
                try:
                    await self._await_execution_permission(
                        username, run_id, execution_id
                    )
                except RoleAssetError as exc:
                    checkpoint_error = exc
                    # The runner must not treat a denied boundary as a failed
                    # model/tool call and attempt fallback or dependent work.
                    raise CheckpointRecoveryError(str(exc)) from exc

            async def record(record_id: str, kind: str, data: dict) -> None:
                """Persist a structured observation before execution advances.

                Args:
                    record_id: Stable response or tool delivery identity.
                    kind: Model response, tool result, or original remote event.
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

                Raises:
                    CheckpointRecoveryError: Current admission is blocked.
                    ProcessPersistenceError: The checkpoint could not be saved.
                """
                nonlocal checkpoint_error
                if data["phase"] in {"request", "tools_started"}:
                    await admit_work()
                while True:
                    try:
                        await self.save_checkpoint(
                            username, run_id, state["attempt_id"], execution_id, data
                        )
                        break
                    except Exception as exc:
                        if isinstance(exc, RoleAssetError) and any(
                            field.get("code") == "PAUSED" for field in exc.fields
                        ):
                            await admit_work()
                            continue
                        checkpoint_error = "Failed to save execution checkpoint"
                        raise ProcessPersistenceError(
                            "Failed to save execution checkpoint"
                        ) from exc
                if data["phase"] in {"response", "tool_result", "tools_applied"}:
                    await admit_work()

            async def begin_usage(payload: dict) -> dict:
                """Wait for shared reservations while retaining the existing executor.

                Args:
                    payload: Original invocation and one unique delivery identity.

                Returns:
                    Committed usage receipt before any external effect.

                Raises:
                    CheckpointRecoveryError: Limits or persistence forbid execution.
                """
                nonlocal checkpoint_error
                while True:
                    await admit_work()
                    try:
                        receipt = await self.begin_usage(
                            username, run_id, state["attempt_id"], execution_id, payload
                        )
                    except Exception as exc:
                        if isinstance(exc, RoleAssetError) and any(
                            field.get("code") == "PAUSED" for field in exc.fields
                        ):
                            # Pause can win after admission but before reservation.
                            continue
                        checkpoint_error = str(exc)
                        raise CheckpointRecoveryError(str(exc)) from exc
                    if not receipt.get("waiting"):
                        return receipt
                    await asyncio.sleep(0.05)

            async def report_usage(receipt_id: str, payload: dict) -> dict:
                """Keep late consumption separate from checkpoint ownership.

                Args:
                    receipt_id: Actual delivery admitted by this executor.
                    payload: Provider report, estimate or missing usage.

                Returns:
                    Persisted consumption for this original delivery.

                Raises:
                    ProcessPersistenceError: Accounting could not be saved.
                """
                nonlocal checkpoint_error
                try:
                    reporting_execution_id = execution_id
                    if (
                        checkpoint
                        and checkpoint.get("remote", {}).get("receipt_id") == receipt_id
                    ):
                        async with self.db.get_db() as session:
                            original = await session.get(AgentWorkflowUsage, receipt_id)
                            if (
                                original is None
                                or original.run_id != run_id
                                or original.attempt_id != state["attempt_id"]
                            ):
                                raise RoleAssetError(
                                    "Remote usage belongs to another attempt",
                                    status=409,
                                )
                            reporting_execution_id = original.execution_id
                    return await self.report_usage(
                        username, receipt_id, reporting_execution_id, payload
                    )
                except Exception as exc:
                    checkpoint_error = "Failed to save usage report"
                    raise ProcessPersistenceError(checkpoint_error) from exc

            turn = asyncio.create_task(
                execute_team_turn(
                    ports=ports,
                    member=member,
                    run_id=run_id,
                    team_id=snapshot["asset_id"],
                    username=snapshot.get("initiator", username),
                    turn_id=state["attempt_id"],
                    node_id=snapshot.get("node_id", state["task_id"]),
                    record_process=record,
                    save_checkpoint=save,
                    checkpoint=checkpoint,
                    begin_usage=begin_usage,
                    report_usage=report_usage,
                    request_human=request_human,
                    pending_messages=(
                        lambda: self.pending_member_messages(
                            username, run_id, state["attempt_id"], execution_id
                        )
                    )
                    if snapshot.get("parent_run_id") and not snapshot.get("graph_task")
                    else None,
                    admit_work=admit_work,
                    preview_tool_write=preview_tool_write if debug_fork else None,
                    text=snapshot["task"],
                    context="Submit the final result with submit_task_result according to its declared contract. Plain text does not complete this task. "
                    + (
                        "Read only selected fields with read_task_artifact. Pinned fields: "
                        + json.dumps(snapshot.get("selected_fields", {}))
                        + ". "
                        if snapshot.get("graph_task")
                        else "Read full input content with read_task_artifact. "
                    )
                    + "Pinned input references: "
                    + json.dumps(snapshot.get("input_artifacts", []))
                    + (
                        "\nTrial input sources: "
                        + json.dumps(
                            {
                                name: {**source, "value": snapshot["inputs"][name]}
                                for name, source in snapshot[
                                    "trial_input_sources"
                                ].items()
                                if name in snapshot["inputs"]
                            },
                            ensure_ascii=False,
                        )
                        if snapshot.get("trial_input_sources")
                        else ""
                    )
                    + (
                        # Frozen inputs are data, never a second task template.
                        "\nDeclared run inputs (JSON):\n"
                        + json.dumps(snapshot["inputs"], ensure_ascii=False)
                        if snapshot.get("coordinator") or "port_sources" in snapshot
                        else ""
                    ),
                    execution={
                        **effective,
                        "config_id": snapshot["profile_id"],
                        "kb_ids": effective["knowledge_bases"],
                        "tools": [*effective["tools"], *[tool.name for tool in tools]],
                        "message_id": state["message_id"],
                        "profile_snapshot": snapshot["profile"],
                        "persona_snapshot": snapshot["persona"],
                        "caller": snapshot.get("chat", {}).get("caller"),
                    },
                ),
            )
            # Paused time is not execution time. Keep collection alive while
            # the built-in runner parks at its saved boundary.
            remaining = ports.reply_timeout
            previous = time.monotonic()
            previous_suspended = False
            try:
                while not turn.done():
                    await asyncio.wait({turn}, timeout=min(0.5, max(remaining, 0.01)))
                    if turn.done():
                        break
                    now = time.monotonic()
                    async with self.db.get_db() as session:
                        decision = await session.get(
                            AgentWorkflowControl,
                            snapshot["root_run_id"],
                        )
                        paused = decision is not None and decision.status == "paused"
                        cancelled = (
                            decision is not None and decision.status == "cancelled"
                        )
                        capacity_released = False
                        current = await session.get(AgentWorkflowRun, run_id)
                        capacity_released = bool(
                            current is not None
                            and current.state.get("capacity_released")
                        )
                    if cancelled:
                        raise asyncio.CancelledError
                    suspended = paused or capacity_released
                    # A resumed observation can end an interval spent suspended.
                    if not suspended and not previous_suspended:
                        remaining -= now - previous
                        if remaining <= 0:
                            raise asyncio.TimeoutError("Workflow execution timed out")
                    previous_suspended = suspended
                    previous = now
                reply, parts = await turn
            finally:
                if not turn.done():
                    turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)
            if effective.get("runner", {}).get("type") == "dify":
                async with self.db.get_db() as session:
                    saved_remote = await session.get(AgentWorkflowCheckpoint, run_id)
                    remote_data = (
                        copy.deepcopy(saved_remote.data) if saved_remote else {}
                    )
                if (
                    remote_data.get("phase") == "remote_result"
                    and snapshot["profile"]["agent_runner"]["config"].get(
                        "dify_api_type"
                    )
                    == "workflow"
                ):
                    output_key = snapshot["profile"]["agent_runner"]["config"].get(
                        "dify_workflow_output_key", "astrbot_wf_output"
                    )
                    output = remote_data["remote"]["result"]["data"]["outputs"].get(
                        output_key
                    )
                    if isinstance(output, dict):
                        # Only the declared structured remote output is a result
                        # submission. Chat text or displayed JSON is not one.
                        await accept(output)
            outcome["response"] = {
                "text": reply,
                "parts": parts,
                "member_id": member["member_id"],
                "task_id": state["task_id"],
                "attempt_id": state["attempt_id"],
                "message_id": state["message_id"],
            }
            if isinstance(checkpoint_error, RoleAssetError):
                raise checkpoint_error
            if checkpoint_error is not None:
                if checkpoint_error.startswith("Execution limit reached"):
                    saved = await self.get_run(username, run_id)
                    calls = [
                        call
                        for call in saved["tool_calls"]
                        if call["attempt_id"] == state["attempt_id"]
                    ]
                    # A validated submission and its fully applied tool batch are
                    # sufficient to finish. A denied narration request must not
                    # strand that committed result behind the exhausted quota.
                    if (
                        saved["pending_result"] is None
                        or not calls
                        or any(call["status"] != "applied" for call in calls)
                    ):
                        raise CheckpointRecoveryError(checkpoint_error)
                else:
                    raise ProcessPersistenceError(checkpoint_error)
            outcome["safe_to_finish"] = True
            async with self.db.get_db() as session:
                row = await session.get(AgentWorkflowRun, run_id)
                if row is None:
                    return
                await self._lock_control(session, row)
                await session.refresh(row)
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
                    .values(state={**row.state, **outcome})
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
            outcome["reason"] = (
                "The Agent ended without submitting a valid task result."
            )
        except HumanInputRequired:
            status = "waiting_human"
            outcome["reason"] = "Waiting for a human choice."
        except asyncio.CancelledError:
            status = "interrupted"
            outcome["reason"] = "Core execution stopped; this task was not replayed."
        except RoleAssetError as exc:
            status = "blocked"
            outcome["reason"] = str(exc)
            outcome["issues"] = exc.fields
        except CheckpointRecoveryError as exc:
            status = "blocked"
            if isinstance(checkpoint_error, RoleAssetError):
                outcome["reason"] = str(checkpoint_error)
                outcome["issues"] = checkpoint_error.fields
            else:
                outcome["reason"] = str(exc)
        except Exception as exc:
            outcome["reason"] = str(exc)
            logger.error("Single-task workflow execution failed: %s", exc)
        finally:
            if self._executions.get(run_id) is current_task:
                self._executions.pop(run_id, None)
            if tools and AgentTeamToolRegistry.get_tools(member["umo"]) == tools:
                AgentTeamToolRegistry.unregister(member["umo"])
            if ports and ports.close:
                try:
                    await ports.close()
                except Exception as exc:
                    logger.error("Workflow task %s cleanup failed: %s", run_id, exc)
                    # Cleanup is diagnostic even if the task already committed.
                    try:
                        async with self.db.get_db() as session:
                            session.add(
                                AgentWorkflowEvent(
                                    run_id=run_id,
                                    kind="task.cleanup_failed",
                                    data={
                                        "attempt_id": state["attempt_id"],
                                        "reason": str(exc),
                                    },
                                )
                            )
                            await session.commit()
                    except Exception:
                        logger.exception(
                            "Failed to save workflow task %s cleanup diagnostic", run_id
                        )
        async with self.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            if row is None:
                return
            await self._lock_control(session, row)
            await session.refresh(row)
            # Execution owns only its outcome fields. Preserve control and resume
            # decisions saved after dispatch, while still fencing displaced owners.
            changed = await session.execute(
                update(AgentWorkflowRun)
                .where(
                    col(AgentWorkflowRun.run_id) == run_id,
                    col(AgentWorkflowRun.status) == "running",
                    col(AgentWorkflowRun.state)["attempt_id"].as_string()
                    == state["attempt_id"],
                    AgentWorkflowRun.state["execution_id"].as_string() == execution_id,
                )
                .values(status=status, state={**row.state, **outcome})
            )
            if cast(CursorResult, changed).rowcount == 1:
                session.add(
                    AgentWorkflowEvent(
                        run_id=run_id,
                        kind="task." + status,
                        data={
                            "attempt_id": state["attempt_id"],
                            "reason": outcome["reason"],
                        },
                    )
                )
            await session.commit()
