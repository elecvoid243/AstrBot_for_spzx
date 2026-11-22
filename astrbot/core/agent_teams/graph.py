"""Declarative DAG definitions and field rules; no user code is evaluated."""

import copy
import json
from graphlib import CycleError, TopologicalSorter
from operator import ge, gt, le, lt
from string import Formatter
from typing import Literal, cast

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

from astrbot.core.agent_teams.assets import (
    HumanWaitPolicy,
    RoleAssetError,
    RoleDefinition,
    RoleInstallation,
    RoleOverrides,
)
from astrbot.core.agent_teams.components import PLUGIN_COMPONENT, ComponentReference
from astrbot.core.agent_teams.result_tool import (
    PORT_FORMAT_CHECKER,
    PORT_FORMATS,
    validate_inline_port_contract,
    validate_result,
)
from astrbot.core.agent_teams.usage import RunLimits

HUMAN_APPROVAL_CONTRACT = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "text": {"type": "string"},
    },
    "required": ["approved", "text"],
}


REFERENCE_OUTPUTS = {"$artifact": "astrbot-artifact", "$file": "astrbot-file"}
MISSING_PORT_VALUE = object()

JSON_TYPES = {"string", "boolean", "integer", "number", "null", "object", "array"}
JOIN_OUTPUT = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "result": {"type": "object"},
                },
                "required": ["node_id", "result"],
            },
        }
    },
    "required": ["results"],
}
DISPATCH_INPUT = {
    "type": "object",
    "properties": {"items": {"type": "array"}},
    "required": ["items"],
}
TRANSFORM_INPUTS = {
    "parse_json": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    "json_text": {
        "type": "object",
        "properties": {"value": {"type": sorted(JSON_TYPES)}},
        "required": ["value"],
    },
}


# Versioned native nodes share the same port contracts at every entry point.
NATIVE_NODE_OUTPUTS = {
    "read_document_v1": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "filename": {"type": "string"}},
        "required": ["text", "filename"],
    },
    "retrieve_knowledge_v1": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kb_id": {"type": "string"},
                        "doc_id": {"type": "string"},
                        "chunk_id": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["kb_id", "doc_id", "chunk_id", "content"],
                },
            },
        },
        "required": ["text", "matches", "warnings"],
    },
}
TRANSFORM_INPUTS.update(
    {
        "read_document_v1": {
            "type": "object",
            "properties": {"file": {"type": "object", "format": "astrbot-file"}},
            "required": ["file"],
        },
        "retrieve_knowledge_v1": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 2000}
            },
            "required": ["query"],
        },
    }
)


class TaskInput(BaseModel):
    """A declared workflow input or an upstream validated result field."""

    model_config = ConfigDict(extra="forbid", strict=True)
    input: str | None = None
    task_id: str | None = None
    field: str = "text"


class HumanTask(BaseModel):
    """A frozen question or approval of one upstream artifact version."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    kind: Literal["question", "approval"]
    question: str = Field(min_length=1, max_length=100_000)
    choices: list[str] = Field(default_factory=list, max_length=100)
    max_length: int = Field(default=10_000, ge=1, le=100_000)
    artifact_task_id: str | None = None
    artifact_version: int = Field(default=1, ge=1)
    recommendation_task_id: str | None = None
    recommendation_value_field: str | None = None
    recommendation_reason_field: str | None = None
    human_wait: HumanWaitPolicy = Field(default_factory=HumanWaitPolicy)


class TeamCall(BaseModel):
    """An exact autonomous asset version invoked in an independent scope."""

    model_config = ConfigDict(extra="forbid", strict=True)
    asset_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)


class WorkflowCall(TeamCall):
    """An exact workflow version invoked in an independent scope."""


class ConditionTask(BaseModel):
    """A scalar comparison evaluated by the graph scheduler."""

    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["condition"]
    operator: Literal["eq", "ne", "gt", "ge", "lt", "le"] = "eq"
    value: str | bool | int | float | None


class JoinTask(BaseModel):
    """An ALL join evaluated by the graph scheduler."""

    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["join"]


class DispatchLimits(BaseModel):
    """Bound the number of items and running Agents in a list dispatch."""

    model_config = ConfigDict(extra="forbid", strict=True)
    max_items: int = Field(default=20, ge=1, le=30)
    max_parallel: int = Field(default=2, ge=1, le=30)


class GraphTask(BaseModel):
    """A task bound to a named member with inline or pinned configuration."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z][\w-]*$")
    name: str = Field(min_length=1, max_length=128)
    member: str = Field(default="", max_length=128)
    role_id: str | None = Field(default=None, min_length=1, max_length=128)
    role_version: int | None = Field(default=None, ge=1)
    role: RoleDefinition | None = None
    installation: RoleInstallation | None = None
    task: str = Field(default="", max_length=100_000)
    human: HumanTask | None = None
    control: ConditionTask | JoinTask | None = None
    team: TeamCall | None = None
    workflow: WorkflowCall | None = None
    inputs: dict[str, TaskInput] = Field(default_factory=dict, max_length=30)
    overrides: RoleOverrides = Field(default_factory=RoleOverrides)
    output_contract: dict | None = None
    dispatch: DispatchLimits | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    transform: (
        Literal[
            "parse_json",
            "map_fields",
            "json_text",
            "read_document_v1",
            "retrieve_knowledge_v1",
            "plugin_component_v1",
        ]
        | None
    ) = Field(default=None, exclude_if=lambda value: value is None)
    component: ComponentReference | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    knowledge_base_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        exclude_if=lambda value: value is None,
    )
    input_contract: dict | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    position: dict[Literal["x", "y"], float] = Field(default_factory=dict)

    def result_contract(self, *, explicit_control: bool = False) -> dict | None:
        """Resolve the same public result contract for graph validation and execution.

        Args:
            explicit_control: Whether the graph declares its control entry tasks.

        Returns:
            Declared output, including the native aggregate for a dispatch or V2 join.
        """
        if self.dispatch or (
            explicit_control and self.control and self.control.kind == "join"
        ):
            return JOIN_OUTPUT
        return self.output_contract

    @field_serializer("overrides")
    def serialize_overrides(self, overrides: RoleOverrides) -> dict:
        """Preserve the difference between omitted fields and explicit nulls.

        Args:
            overrides: Explicit node-local role settings.

        Returns:
            Only the settings supplied by the user, including explicit nulls.
        """
        return overrides.model_dump(exclude_unset=True)

    @model_validator(mode="after")
    def validate_execution(self):
        """Require either agent execution fields or an unambiguous human node.

        Returns:
            Validated node.

        Raises:
            ValueError: The selected node kind has missing or conflicting fields.
        """
        if (self.component is not None) != (self.transform == PLUGIN_COMPONENT):
            raise ValueError("Plugin nodes require an exact component reference")
        if self.component and (
            self.input_contract is None or self.output_contract is None
        ):
            raise ValueError(
                "Plugin nodes require registered input and output contracts"
            )
        if self.knowledge_base_id is not None and self.transform not in {
            "retrieve_knowledge_v1",
            PLUGIN_COMPONENT,
        }:
            raise ValueError("This node cannot select a knowledge base")
        if self.dispatch is not None and (
            self.transform
            or self.control
            or self.human
            or self.team
            or self.workflow
            or self.input_contract is not None
            or self.output_contract is not None
            or set(self.inputs) != {"items"}
        ):
            raise ValueError(
                "List dispatch requires an Agent task with the items port and default text output"
            )
        if self.transform is not None:
            if (
                self.control
                or self.human
                or self.team
                or self.workflow
                or self.member
                or self.role_id
                or self.role_version
                or self.role
                or self.installation
                or self.task
                or self.overrides.model_fields_set
                or (
                    self.input_contract is not None
                    and self.transform not in {"map_fields", PLUGIN_COMPONENT}
                )
                or (self.output_contract is not None and self.transform == "json_text")
            ):
                raise ValueError("Transform nodes cannot configure other execution")
            if self.transform in NATIVE_NODE_OUTPUTS:
                contract = NATIVE_NODE_OUTPUTS[self.transform]
                if (
                    self.output_contract is not None
                    and self.output_contract != contract
                ):
                    raise ValueError("The native node has a fixed output contract")
                self.output_contract = copy.deepcopy(contract)
            return self
        if self.input_contract is not None and (self.control or self.human):
            raise ValueError("Control and human nodes cannot declare an input contract")
        if self.control is not None:
            if (
                self.human is not None
                or self.team is not None
                or self.workflow is not None
                or self.member
                or self.role_id
                or self.role_version
                or self.role is not None
                or self.installation is not None
                or self.task
                or self.output_contract is not None
                or self.overrides.model_dump(exclude_unset=True)
                or set(self.inputs)
                != ({"value"} if self.control.kind == "condition" else set())
            ):
                raise ValueError(
                    "Control nodes cannot configure execution and conditions require exactly one value input"
                )
        elif self.team is not None or self.workflow is not None:
            if (
                self.team is not None
                and self.workflow is not None
                or self.human is not None
                or self.member
                or self.role_id
                or self.role_version
                or self.role is not None
                or self.installation is not None
                or self.task
                or self.overrides.model_dump(exclude_unset=True)
            ):
                raise ValueError(
                    "Child calls cannot also configure agent or human execution"
                )
        elif self.human is None:
            pinned = bool(self.role_id and self.role_version)
            inline = self.role is not None and self.installation is not None
            if (
                not self.member
                or not self.task
                or pinned == inline
                or bool(self.role_id) != bool(self.role_version)
                or (self.role is None) != (self.installation is None)
            ):
                raise ValueError(
                    "Agent tasks require a member, task and exactly one complete role source"
                )
            if inline and self.overrides.model_dump(exclude_unset=True):
                raise ValueError("Inline agents do not use role overrides")
            if inline and "profile_id" not in self.installation.model_fields_set:
                raise ValueError("Inline agents require an explicit profile_id")
        else:
            if (
                self.member
                or self.role_id
                or self.role_version
                or self.role is not None
                or self.installation is not None
                or self.task
                or self.inputs
                or self.output_contract
                not in (
                    None,
                    HUMAN_APPROVAL_CONTRACT if self.human.kind == "approval" else None,
                )
                or self.overrides.model_dump(exclude_unset=True)
            ):
                raise ValueError("Human tasks cannot also configure agent execution")
            if self.human.kind == "approval":
                if not self.human.artifact_task_id or self.human.choices:
                    raise ValueError(
                        "Approval requires an artifact task and no answer choices"
                    )
                self.output_contract = HUMAN_APPROVAL_CONTRACT
            elif self.human.artifact_task_id is not None:
                raise ValueError("Questions do not approve artifacts")
            if any(
                not value or len(value) > self.human.max_length
                for value in self.human.choices
            ) or len(set(self.human.choices)) != len(self.human.choices):
                raise ValueError(
                    "Answer choices must be unique, nonempty and fit the length limit"
                )
        return self


class FieldCondition(BaseModel):
    """A typed comparison against the source task's validated output."""

    model_config = ConfigDict(extra="forbid", strict=True)
    field: str = Field(min_length=1, max_length=256)
    operator: Literal["eq", "ne", "gt", "ge", "lt", "le"] = "eq"
    value: str | bool | int | float | None


class GraphEdge(BaseModel):
    """A required dependency, optionally activated by a field condition."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source: str
    target: str
    condition: FieldCondition | None = None


class LoopFeedback(BaseModel):
    """Explicit next-round input from a completed previous-round result."""

    model_config = ConfigDict(extra="forbid", strict=True)
    target: str
    input: str
    source: str
    field: str = "text"


class GraphLoop(BaseModel):
    """A disjoint, finite task group with a typed exit and explicit feedback."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z][\w-]*$")
    name: str = Field(min_length=1, max_length=128)
    body: list[str] = Field(min_length=1, max_length=100)
    exit_task_id: str
    exit_condition: FieldCondition
    max_iterations: int = Field(ge=1, le=100)
    feedback: list[LoopFeedback] = Field(default_factory=list, max_length=100)


class CanvasGroup(BaseModel):
    """A visual group of workflow tasks."""

    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=128)
    task_ids: list[str] = Field(min_length=1, max_length=100)


class CanvasNote(BaseModel):
    """Plain text positioned independently on the workflow canvas."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=4000)
    position: dict[Literal["x", "y"], float] = Field(default_factory=dict)


class CanvasAnnotations(BaseModel):
    """Editor annotations carried with the definition and native asset package."""

    model_config = ConfigDict(extra="forbid", strict=True)
    groups: list[CanvasGroup] = Field(default_factory=list, max_length=100)
    notes: list[CanvasNote] = Field(default_factory=list, max_length=100)


class GraphDefinition(BaseModel):
    """A bounded, versioned workflow graph with persisted editor layout."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=128)
    default_provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    required_inputs: list[str] = Field(default_factory=list, max_length=30)
    port_contract_version: int | None = Field(
        default=None, ge=1, le=2, exclude_if=lambda value: value is None
    )
    input_contract: dict | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    entry_task_ids: list[str] | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )
    result_task_id: str | None = Field(default=None, min_length=1, max_length=128)
    max_parallel: int = Field(default=4, ge=1, le=100)
    max_depth: int = Field(default=3, ge=1, le=20)
    limits: RunLimits | None = None
    tasks: list[GraphTask] = Field(min_length=1, max_length=100)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=500)
    loops: list[GraphLoop] = Field(default_factory=list, max_length=30)
    canvas: CanvasAnnotations | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


def matches_condition(value: str | bool | int | float | None, rule: dict) -> bool:
    """Compare a validated scalar with the same rules at dependencies and exits.

    Args:
        value: Scalar resolved from the source's validated result.
        rule: Frozen, validated field comparison with a typed expected value.

    Returns:
        Whether the comparison matches, treating JSON booleans distinctly.
    """
    other = rule["value"]
    equal = value == other and isinstance(value, bool) == isinstance(other, bool)
    if rule["operator"] in {"eq", "ne"}:
        return equal if rule["operator"] == "eq" else not equal
    return {"gt": gt, "ge": ge, "lt": lt, "le": le}[rule["operator"]](
        cast(int | float, value), other
    )


def field_schema(contract: dict | None, path: str, *, structured: bool = False) -> dict:
    """Resolve a bound field through local references and allOf.

    Args:
        contract: Frozen output object contract, or the default text contract.
        path: Dot-separated property path, never an expression or array lookup.
        structured: Permit optional and structured fields on a versioned data port.

    Returns:
        The possible JSON types and, for structured ports, requiredness and format.

    Raises:
        ValueError: The field does not have a compatible declared port schema.
    """
    if structured and path in REFERENCE_OUTPUTS:
        if path == "$file" and "files" not in (contract or {}).get("properties", {}):
            raise ValueError("Declare managed attachments in the output contract")
        return {"type": ["object"], "required": True, "format": REFERENCE_OUTPUTS[path]}
    root = (
        contract
        if contract is not None
        else {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }
    )
    resolver = (
        Registry()
        .with_resource(
            "", Resource.from_contents(root, default_specification=DRAFT202012)
        )
        .resolver()
    )
    current: dict = root
    guaranteed = True
    for name in path.split("."):
        candidates = [current]
        visited: set[int] = set()
        properties = []
        required: set[str] = set()
        object_types: set[str] | None = None
        while candidates:
            item = candidates.pop()
            if not isinstance(item, dict) or id(item) in visited:
                continue
            visited.add(id(item))
            kinds = item.get("type")
            if kinds is not None:
                kinds = set(kinds if isinstance(kinds, list) else [kinds])
                object_types = kinds if object_types is None else object_types & kinds
            if name in item.get("properties", {}):
                properties.append(item["properties"][name])
            required.update(item.get("required", []))
            candidates.extend(item.get("allOf", []))
            if "$ref" in item:
                candidates.append(resolver.lookup(item["$ref"]).contents)
        if object_types != {"object"}:
            raise ValueError(f"Field {path} must traverse guaranteed object properties")
        guaranteed &= name in required
        if (not structured and not guaranteed) or not properties:
            raise ValueError(
                f"Field {path} must be declared and required in the output contract"
            )
        # Constraints on the same property combine across allOf branches.
        current = {"allOf": properties}
    visited = set()
    candidates = [current]
    declared: set[str] | None = None
    formats = set()
    while candidates:
        item = candidates.pop()
        if not isinstance(item, dict) or id(item) in visited:
            continue
        visited.add(id(item))
        if structured and item.get("format") in PORT_FORMATS:
            formats.add(item["format"])
        kinds = item.get("type")
        if kinds is not None:
            kinds = set(kinds if isinstance(kinds, list) else [kinds])
            # JSON Schema's number includes integer; allOf intersects types.
            if "number" in kinds:
                kinds.add("integer")
            declared = kinds if declared is None else declared & kinds
        candidates.extend(item.get("allOf", []))
        if "$ref" in item:
            candidates.append(resolver.lookup(item["$ref"]).contents)
    if not declared or not declared <= (
        JSON_TYPES if structured else JSON_TYPES - {"object", "array"}
    ):
        expected = "compatible" if structured else "scalar"
        raise ValueError(f"Field {path} must declare a {expected} type")
    if formats and (len(formats) != 1 or declared != {"object"}):
        raise ValueError(f"Field {path} must declare one semantic object format")
    return {
        "type": sorted(declared),
        **({"format": next(iter(formats))} if formats else {}),
        **({"required": guaranteed} if structured else {}),
    }


def port_value(content: dict, field: str, reference: dict | None = None):
    """Resolve a selected field or a reference to the committed result.

    Args:
        content: Validated result or a frozen projection.
        field: Bound property path or intrinsic reference output.
        reference: Exact committed result when resolving an intrinsic output.

    Returns:
        The native value, or a sentinel when the port has no value.
    """
    if reference is not None:
        if field == "$artifact":
            return reference
        if field == "$file":
            files = content.get("files", [])
            if len(files) == 1:
                return {**reference, "file_id": files[0]["file_id"]}
            return MISSING_PORT_VALUE
    value = content
    for name in field.split("."):
        if not isinstance(value, dict) or name not in value:
            return MISSING_PORT_VALUE
        value = value[name]
    return value


def task_input_contract(
    explicit: dict | None, transform: str | None, *, dispatch: bool = False
) -> dict | None:
    """Resolve the input contract used during validation and task admission.

    Args:
        explicit: Input contract declared on the task.
        transform: Built-in transform name, if any.
        dispatch: Whether this task consumes a list for dispatch.

    Returns:
        The declared or built-in contract when available.
    """
    return explicit or (DISPATCH_INPUT if dispatch else TRANSFORM_INPUTS.get(transform))


def port_requires_value(contract: dict | None, name: str) -> bool:
    """Check whether an unavailable binding prevents task admission.

    Args:
        contract: Effective task input contract, when defined.
        name: Input name on the consuming task.

    Returns:
        True for a required port without a default or an implicit required binding.
    """
    return contract is None or (
        name in contract.get("required", [])
        and "default" not in contract["properties"][name]
    )


def normalize_port_inputs(contract: dict, values: dict, path: str) -> dict:
    """Apply declared defaults and validate supplied values at a port boundary.

    Args:
        contract: Frozen object schema for the input ports.
        values: Supplied JSON values, including explicit nulls.
        path: Diagnostic prefix of the consumer.

    Returns:
        Values with defaults added only for missing properties.

    Raises:
        RoleAssetError: At least one value does not match the contract.
    """
    normalized = dict(values)
    properties = contract["properties"]
    for name, schema in properties.items():
        if name not in normalized and "default" in schema:
            normalized[name] = copy.deepcopy(schema["default"])
    issues = validate_result(
        {**contract, "additionalProperties": False},
        normalized,
        validate_files=False,
        port_formats=True,
    )
    issues = [
        {**issue, "path": path + issue["path"].removeprefix("result")}
        for issue in issues
    ]
    if issues:
        raise RoleAssetError("Invalid port values", issues)
    return normalized


def execute_port_transform(task: dict, inputs: dict, path: str) -> tuple[dict, dict]:
    """Resolve one deterministic node to a validated native result.

    Args:
        task: Frozen node with a validated transform and port contracts.
        inputs: Selected native values before applying port defaults.
        path: Diagnostic path of the node in the original graph.

    Returns:
        The resolved inputs and the validated object result.

    Raises:
        RoleAssetError: An input cannot be parsed or a value violates a contract.
    """
    transform = task["transform"]
    contract = task.get("output_contract")
    inputs = normalize_port_inputs(
        task.get("input_contract") or TRANSFORM_INPUTS[transform],
        inputs,
        f"{path}.inputs",
    )
    if transform == "parse_json":
        try:
            result = json.loads(inputs["text"])
        except (ValueError, RecursionError) as exc:
            raise RoleAssetError(
                "Could not parse JSON",
                [
                    {
                        "path": f"{path}.inputs.text",
                        "code": "INVALID",
                        "message": "Provide valid JSON text",
                    }
                ],
            ) from exc
        if not isinstance(result, dict):
            raise RoleAssetError(
                "Could not parse JSON object",
                [
                    {
                        "path": f"{path}.inputs.text",
                        "code": "INVALID",
                        "message": "Provide a JSON object",
                    }
                ],
            )
    elif transform == "json_text":
        result = {"text": json.dumps(inputs["value"], ensure_ascii=False)}
    else:
        result = inputs
    if "files" in result:
        raise RoleAssetError(
            "Invalid transform result",
            [
                {
                    "path": f"{path}.output_contract.files",
                    "code": "UNSUPPORTED",
                    "message": "The top-level files field is reserved for managed attachments",
                }
            ],
        )
    issues = validate_result(contract, result, port_formats=True)
    if issues:
        prefix = (
            f"{path}.inputs.text"
            if transform == "parse_json"
            else f"{path}.output_contract"
        )
        raise RoleAssetError(
            "Transform result does not match its contract",
            [
                {
                    **issue,
                    "path": prefix + issue["path"].removeprefix("result"),
                }
                for issue in issues
            ],
        )
    return inputs, result


def validate_graph(
    definition: GraphDefinition,
    *,
    result_requirement: Literal["optional", "required", "reachable"] = "optional",
    external_sources: dict[str, GraphTask] | None = None,
    feedback_inputs: dict[str, dict[str, TaskInput]] | None = None,
    text_port_scope: set[str] | None = None,
) -> None:
    """Validate references, member identity, field rules and acyclic dependencies.

    Args:
        definition: Parsed graph whose contracts and roles are also validated.
        result_requirement: Whether the graph may leave its result unresolved,
            must name an existing result, or must also give it a possible path.
        external_sources: Trusted boundary contracts supplied by the trial compiler.
        feedback_inputs: Trusted single-node trial feedback bindings.
        text_port_scope: Executed tasks in a bounded trial, which may omit the
            workflow's public result and leave other required ports unbound.

    Raises:
        RoleAssetError: Field-specific graph diagnostics before saving or running.
    """
    fields = []
    tasks = {task.id: task for task in definition.tasks}
    external_sources = external_sources or {}
    feedback_inputs = feedback_inputs or {}
    if set(tasks) & set(external_sources) or any(
        key != task.id for key, task in external_sources.items()
    ):
        raise RoleAssetError("Invalid external source identities")
    data_sources = {**external_sources, **tasks}
    structured = definition.port_contract_version == 2
    names = definition.required_inputs
    if not structured and (
        definition.input_contract is not None
        or any(t.transform or t.input_contract is not None for t in definition.tasks)
    ):
        fields.append(
            {
                "path": "definition.port_contract_version",
                "code": "REQUIRED",
                "message": "Structured ports require port contract version 2",
            }
        )
    if structured:
        contracts = [
            ("definition.input_contract", definition.input_contract, True),
            *[
                pair
                for i, task in enumerate(definition.tasks)
                for pair in (
                    (f"definition.tasks.{i}.input_contract", task.input_contract, True),
                    (
                        f"definition.tasks.{i}.output_contract",
                        task.output_contract,
                        False,
                    ),
                )
                if pair[1] is not None
                or (
                    task.transform in {"map_fields", "parse_json"}
                    and (not pair[2] or task.transform == "map_fields")
                )
            ],
        ]
        if definition.required_inputs:
            fields.append(
                {
                    "path": "definition.required_inputs",
                    "code": "INVALID",
                    "message": "Declare inputs in the structured input contract",
                }
            )
        for path, contract, is_input in contracts:
            try:
                validate_inline_port_contract(contract)
                if not is_input and path.endswith(".output_contract"):
                    index = int(path.split(".")[2])
                    if definition.tasks[index].transform and "files" in contract.get(
                        "properties", {}
                    ):
                        raise ValueError(
                            "The top-level files field is reserved for managed attachments"
                        )
                node = (
                    definition.tasks[int(path.split(".")[2])]
                    if path.startswith("definition.tasks.")
                    else None
                )
                if not is_input:
                    for name, schema in contract.get("properties", {}).items():
                        if (
                            not isinstance(schema, dict)
                            or schema.get("format") not in REFERENCE_OUTPUTS.values()
                        ):
                            continue
                        if node.transform == "parse_json":
                            raise ValueError(
                                "A JSON parser cannot create a managed reference"
                            )
                        if node.transform == "map_fields":
                            inputs = (node.input_contract or {}).get("properties")
                            incoming = (
                                inputs.get(name) if isinstance(inputs, dict) else None
                            )
                            if (
                                not isinstance(incoming, dict)
                                or incoming.get("format") != schema["format"]
                            ):
                                raise ValueError(
                                    "Field mapping must preserve a named reference input"
                                )
                if is_input:
                    properties = contract.get("properties")
                    if (
                        not isinstance(properties, dict)
                        or len(properties) > 30
                        or any(
                            not name.isidentifier() or not isinstance(schema, dict)
                            for name, schema in properties.items()
                        )
                        or not set(contract.get("required", [])) <= properties.keys()
                    ):
                        raise ValueError("Declare up to 30 named input properties")
                    for name, schema in properties.items():
                        port = field_schema(contract, name, structured=True)
                        if (
                            node is not None
                            and not (node.transform or node.team or node.workflow)
                            and (
                                not port.get("format")
                                and set(port["type"]) != {"string"}
                            )
                        ):
                            raise ValueError(
                                "Agent inputs must be text, messages or exact references"
                            )
                        if "default" in schema and not Draft202012Validator(
                            schema, format_checker=PORT_FORMAT_CHECKER
                        ).is_valid(schema["default"]):
                            raise ValueError(
                                f"Default for {name} must match its schema"
                            )
            except (ValueError, SchemaError, Unresolvable) as exc:
                fields.append({"path": path, "code": "CONTRACT", "message": str(exc)})
        if fields:
            raise RoleAssetError("Invalid structured port contracts", fields)
        names = list(definition.input_contract["properties"])
    if len(set(names)) != len(names) or any(not name.isidentifier() for name in names):
        fields.append(
            {
                "path": "definition.required_inputs",
                "code": "INVALID",
                "message": "Use unique input identifiers",
            }
        )
    if len(tasks) != len(definition.tasks):
        fields.append(
            {
                "path": "definition.tasks",
                "code": "DUPLICATE",
                "message": "Task identities must be unique",
            }
        )
    predecessors: dict[str, set[str]] = {name: set() for name in tasks}
    seen = set()
    # Exit rules share the exact scalar/type validation used by graph edges.
    rules = [
        (edge, f"definition.edges.{i}", False)
        for i, edge in enumerate(definition.edges)
    ]
    rules += [
        (
            GraphEdge(
                source=loop.exit_task_id,
                target=loop.exit_task_id,
                condition=loop.exit_condition,
            ),
            f"definition.loops.{i}.exit",
            True,
        )
        for i, loop in enumerate(definition.loops)
    ]
    for edge, path, exit_rule in rules:
        if edge.source not in tasks or edge.target not in tasks:
            fields.append(
                {
                    "path": path,
                    "code": "REFERENCE",
                    "message": "Dependency must refer to existing tasks",
                }
            )
            continue
        if not exit_rule and (edge.source, edge.target) in seen:
            fields.append(
                {
                    "path": path,
                    "code": "DUPLICATE",
                    "message": "Dependency already exists",
                }
            )
        if not exit_rule:
            seen.add((edge.source, edge.target))
            predecessors[edge.target].add(edge.source)
        source_human = tasks[edge.source].human
        if (
            source_human
            and source_human.kind == "approval"
            and (
                edge.condition is None
                or edge.condition.field != "approved"
                or edge.condition.operator != "eq"
                or type(edge.condition.value) is not bool
            )
        ):
            fields.append(
                {
                    "path": path + ".condition",
                    "code": "CONDITION",
                    "message": "Approval exits must select approve or reject",
                }
            )
        source_control = tasks[edge.source].control
        if source_control and (
            exit_rule or (source_control.kind == "condition" and not edge.condition)
        ):
            fields.append(
                {
                    "path": path + ".condition",
                    "code": "CONDITION",
                    "message": "Condition exits must explicitly select matched true or false",
                }
            )
        if edge.condition:
            rule = edge.condition
            try:
                source_task = tasks[edge.source]
                if source_task.control:
                    if (
                        source_task.control.kind != "condition"
                        or rule.field != "matched"
                        or rule.operator != "eq"
                        or type(rule.value) is not bool
                    ):
                        raise ValueError(
                            "Condition exits must compare matched to true or false"
                        )
                    schema = {"type": ["boolean"]}
                else:
                    schema = field_schema(source_task.output_contract, rule.field)
                kinds = set(schema["type"])
                value = rule.value
                valid = (
                    ("boolean" in kinds and isinstance(value, bool))
                    or ("string" in kinds and isinstance(value, str))
                    or ("null" in kinds and value is None)
                    or ("integer" in kinds and type(value) is int)
                    or ("number" in kinds and type(value) in (int, float))
                )
                if not valid or (
                    rule.operator not in {"eq", "ne"}
                    and not kinds <= {"integer", "number"}
                ):
                    raise ValueError(
                        "Comparison and value must match the declared field type"
                    )
            except ValueError as exc:
                fields.append(
                    {
                        "path": path + ".condition",
                        "code": "CONDITION",
                        "message": str(exc),
                    }
                )
    ancestors: dict[str, set[str]] = {}
    pending = set(tasks)
    while pending:
        ready = {name for name in pending if predecessors[name] <= ancestors.keys()}
        if not ready:
            fields.append(
                {
                    "path": "definition.edges",
                    "code": "CYCLE",
                    "message": "Unbounded back edges are not supported; use an acyclic workflow",
                }
            )
            break
        for name in ready:
            ancestors[name] = predecessors[name] | set().union(
                *(ancestors[p] for p in predecessors[name])
            )
        pending -= ready
    entries = definition.entry_task_ids
    loop_exits = {
        task_id: loop.exit_task_id
        for loop in definition.loops
        if loop.exit_task_id in tasks
        for task_id in loop.body
    }
    execution_order = tuple(ancestors)
    if entries is not None:
        if not structured or (not entries and text_port_scope is None):
            fields.append(
                {
                    "path": "definition.entry_task_ids",
                    "code": "REQUIRED" if structured else "ENTRY",
                    "message": (
                        "Select at least one control entry task"
                        if structured
                        else "Control entry tasks require port contract version 2"
                    ),
                }
            )
        for index, entry in enumerate(entries):
            if (
                entry not in tasks
                or entry in entries[:index]
                or predecessors.get(entry)
                or (tasks[entry].control and tasks[entry].control.kind == "join")
            ):
                fields.append(
                    {
                        "path": f"definition.entry_task_ids.{index}",
                        "code": "ENTRY",
                        "message": "Select a unique V2 task with no incoming control edge, other than a join",
                    }
                )
        dependencies = {}
        loop_gate_paths: dict[tuple[str, str], str] = {}
        for index, task in enumerate(definition.tasks):
            sources = dict.fromkeys(predecessors[task.id], "definition.edges")
            sources.update(
                {
                    ref.task_id: f"definition.tasks.{index}.inputs.{name}"
                    for name, ref in task.inputs.items()
                    if ref.task_id in tasks
                }
            )
            dependencies[task.id] = set(sources)
            for source, path in sources.items():
                exit_task = loop_exits.get(source)
                if exit_task is not None and exit_task != loop_exits.get(task.id):
                    dependencies[task.id].add(exit_task)
                    loop_gate_paths[exit_task, task.id] = path
        try:
            execution_order = tuple(TopologicalSorter(dependencies).static_order())
        except CycleError as exc:
            if not any(issue["code"] == "CYCLE" for issue in fields):
                cycle = exc.args[1]
                gated_path = next(
                    (
                        loop_gate_paths[pair]
                        for pair in zip(cycle, cycle[1:])
                        if pair in loop_gate_paths
                    ),
                    None,
                )
                fields.append(
                    {
                        "path": gated_path or "definition.tasks",
                        "code": "CYCLE",
                        "message": (
                            "The loop must pass before an outside task can use its result, forming a dependency cycle."
                            if gated_path
                            else "Control edges and data bindings cannot form a dependency cycle"
                        ),
                    }
                )
    grouped: set[str] = set()
    loop_ids: set[str] = set()
    for index, loop in enumerate(definition.loops):
        path = f"definition.loops.{index}"
        body = set(loop.body)
        try:
            if loop.id in loop_ids or len(body) != len(loop.body):
                raise ValueError("Loop and body identities must be unique")
            if not body <= tasks.keys() or body & grouped:
                raise ValueError(
                    "Loop bodies must use existing, disjoint tasks; nesting is not supported"
                )
            if loop.exit_task_id not in body or not body <= {
                loop.exit_task_id,
                *ancestors.get(loop.exit_task_id, set()),
            }:
                raise ValueError("The exit task must depend on every task in its body")
            for node in body:
                for ancestor in ancestors.get(node, set()) - body:
                    if ancestors.get(ancestor, set()) & body:
                        raise ValueError(
                            "A loop body cannot leave the group and reenter it"
                        )
            if structured and loop.max_iterations > 1 and not loop.feedback:
                fields.append(
                    {
                        "path": f"{path}.feedback",
                        "code": "REQUIRED",
                        "message": "Connect a next-round input for this loop",
                    }
                )
            targets = set()
            for feedback_index, feedback in enumerate(loop.feedback):
                feedback_binding_path = f"{path}.feedback.{feedback_index}"
                outside = [
                    endpoint
                    for endpoint in ("source", "target")
                    if getattr(feedback, endpoint) not in body
                ]
                if outside:
                    fields.extend(
                        {
                            "path": f"{feedback_binding_path}.{endpoint}",
                            "code": "SCOPE",
                            "message": "Feedback must explicitly connect tasks within the loop body",
                        }
                        for endpoint in outside
                    )
                    continue
                target = tasks[feedback.target]
                source = tasks[feedback.source]
                if source.control and not (
                    entries is not None and source.control.kind == "join"
                ):
                    raise ValueError("Feedback requires a validated artifact")
                target_contract = task_input_contract(
                    target.input_contract,
                    target.transform,
                    dispatch=bool(target.dispatch),
                )
                if target.human or (
                    feedback.input not in target.inputs
                    and feedback.input
                    not in (target_contract or {}).get("properties", {})
                ):
                    fields.append(
                        {
                            "path": f"{feedback_binding_path}.input",
                            "code": "REFERENCE",
                            "message": "Feedback must override a declared input port",
                        }
                    )
                    continue
                if (feedback.target, feedback.input) in targets:
                    raise ValueError(
                        "Each next-round input has exactly one feedback source"
                    )
                targets.add((feedback.target, feedback.input))
                feedback_schema = field_schema(
                    source.result_contract(explicit_control=entries is not None),
                    feedback.field,
                    structured=structured and bool(target_contract),
                )
                feedback_path = f"definition.tasks.{definition.tasks.index(target)}.inputs.{feedback.input}"
                if target_contract:
                    target_schema = field_schema(
                        target_contract, feedback.input, structured=True
                    )
                    if not set(feedback_schema["type"]) <= set(
                        target_schema["type"]
                    ) or feedback_schema.get("format") != target_schema.get("format"):
                        fields.append(
                            {
                                "path": feedback_path,
                                "code": "INCOMPATIBLE",
                                "message": "Feedback must match the declared next-round port type",
                            }
                        )
                elif (
                    definition.port_contract_version in (1, 2)
                    and not target.control
                    and (structured or not (target.team or target.workflow))
                    and (text_port_scope is None or target.id in text_port_scope)
                    and set(feedback_schema["type"]) != {"string"}
                ):
                    fields.append(
                        {
                            "path": feedback_path,
                            "code": "INCOMPATIBLE",
                            "message": "Loop feedback for this port must provide text",
                        }
                    )
                if target.control and target.control.kind == "condition":
                    kinds = set(feedback_schema["type"])
                    value = target.control.value
                    valid = (
                        ("boolean" in kinds and type(value) is bool)
                        or ("string" in kinds and isinstance(value, str))
                        or ("null" in kinds and value is None)
                        or ("integer" in kinds and type(value) is int)
                        or ("number" in kinds and type(value) in (int, float))
                    )
                    if not valid or (
                        target.control.operator not in {"eq", "ne"}
                        and not kinds <= {"integer", "number"}
                    ):
                        raise ValueError(
                            "Feedback comparison must match the declared field type"
                        )
        except ValueError as exc:
            fields.append({"path": path, "code": "LOOP", "message": str(exc)})
        grouped |= body
        loop_ids.add(loop.id)
    members = {}
    for index, task in enumerate(definition.tasks):
        path = f"definition.tasks.{index}"
        if task.human is not None:
            human = task.human
            recommendation = (
                human.recommendation_task_id,
                human.recommendation_value_field,
                human.recommendation_reason_field,
            )
            if any(recommendation):
                try:
                    if not all(recommendation):
                        raise ValueError(
                            "Select an upstream task and both recommendation fields"
                        )
                    source = data_sources.get(human.recommendation_task_id)
                    if (
                        source is None
                        or (
                            source.id not in ancestors.get(task.id, set())
                            and source.id not in external_sources
                        )
                        or source.human
                        or source.control
                        or source.team
                        or source.workflow
                    ):
                        raise ValueError(
                            "Recommendation must come from an upstream agent result"
                        )
                    for field in recommendation[1:]:
                        if set(field_schema(source.output_contract, field)["type"]) != {
                            "string"
                        }:
                            raise ValueError("Recommendation fields must be strings")
                except (ValueError, KeyError) as exc:
                    fields.append(
                        {
                            "path": path + ".human.recommendation_task_id",
                            "code": "REFERENCE",
                            "message": str(exc),
                        }
                    )
            if task.human.kind == "approval" and (
                (
                    task.human.artifact_task_id not in ancestors.get(task.id, set())
                    and task.human.artifact_task_id not in external_sources
                )
                or data_sources[task.human.artifact_task_id].human is not None
                or data_sources[task.human.artifact_task_id].control is not None
            ):
                fields.append(
                    {
                        "path": path + ".human.artifact_task_id",
                        "code": "REFERENCE",
                        "message": "Approve an upstream dependency's exact artifact version",
                    }
                )
            if (
                structured
                and human.kind == "approval"
                and human.artifact_version != 1
                and (approved_task := data_sources.get(human.artifact_task_id))
                and approved_task.input_contract is not None
                and (approved_task.workflow or approved_task.team)
            ):
                fields.append(
                    {
                        "path": path + ".human.artifact_version",
                        "code": "INCOMPATIBLE",
                        "message": "A fixed call approval uses the exact version returned by the call",
                    }
                )
            continue
        config = (
            task.role_id,
            task.role_version,
            task.role,
            task.installation,
            {
                key: value
                for key, value in task.overrides.model_dump(exclude_unset=True).items()
                if value is not None
                or key in ("persona_id", "provider_id", "human_wait")
            },
        )
        if (
            not (task.team or task.control or task.transform)
            and task.member in members
            and members[task.member] != config
        ):
            fields.append(
                {
                    "path": path + ".member",
                    "code": "CONFLICT",
                    "message": "A member must use the same frozen role and configuration across tasks",
                }
            )
        if not (task.team or task.control or task.transform):
            members[task.member] = config
        typed_agent = definition.port_contract_version in (1, 2) and not (
            task.control or task.team or task.workflow or task.transform
        )
        node_contract = task_input_contract(
            task.input_contract, task.transform, dispatch=bool(task.dispatch)
        )
        if (
            task.transform == "retrieve_knowledge_v1"
            and not task.knowledge_base_id
            and (text_port_scope is None or task.id in text_port_scope)
        ):
            fields.append(
                {
                    "path": path + ".knowledge_base_id",
                    "code": "REQUIRED",
                    "message": "Choose an existing knowledge base",
                }
            )
        if task.dispatch and entries is None:
            fields.append(
                {
                    "path": path + ".dispatch",
                    "code": "ENTRY",
                    "message": "List dispatch requires explicit V2 control entry tasks",
                }
            )
        if node_contract and (text_port_scope is None or task.id in text_port_scope):
            for key in node_contract.get("required", []):
                if port_requires_value(node_contract, key) and not (
                    (
                        binding := feedback_inputs.get(task.id, {}).get(key)
                        or task.inputs.get(key)
                    )
                    and (binding.input or binding.task_id)
                ):
                    fields.append(
                        {
                            "path": path + f".inputs.{key}",
                            "code": "REQUIRED",
                            "message": "Connect this required input port",
                        }
                    )
        check_text_ports = typed_agent and (
            text_port_scope is None or task.id in text_port_scope
        )
        check_text_binding = (check_text_ports and node_contract is None) or (
            structured and node_contract is None and bool(task.team or task.workflow)
        )
        for key, ref in {**task.inputs, **feedback_inputs.get(task.id, {})}.items():
            code = "REFERENCE"
            try:
                feedback_ref = feedback_inputs.get(task.id, {}).get(key)
                if feedback_ref is not None:
                    ref = feedback_ref
                if not key.isidentifier():
                    raise ValueError("Input names must be identifiers")
                if node_contract and key not in node_contract["properties"]:
                    code = "UNKNOWN"
                    raise ValueError("Input port is not declared")
                if node_contract and ref.input is None and ref.task_id is None:
                    continue
                if typed_agent and ref.input is None and ref.task_id is None:
                    if check_text_ports:
                        code = "REQUIRED"
                        raise ValueError("Connect a text source to this input port")
                    continue
                if ref.input is not None:
                    if ref.input not in names or ref.task_id is not None:
                        raise ValueError(
                            "Reference exactly one declared workflow input"
                        )
                    if check_text_ports and ref.field != "text":
                        raise ValueError(
                            "Workflow bindings directly select a declared port"
                        )
                    source_schema = (
                        field_schema(
                            definition.input_contract, ref.input, structured=True
                        )
                        if structured
                        else {"type": ["string"], "required": True}
                    )
                    if (
                        structured
                        and "default"
                        in definition.input_contract["properties"][ref.input]
                    ):
                        source_schema["required"] = True
                elif (
                    ref.task_id not in ancestors.get(task.id, set())
                    and ref.task_id not in external_sources
                    and not (feedback_ref is not None and ref.task_id in tasks)
                    and not (entries is not None and ref.task_id in tasks)
                ):
                    raise ValueError(
                        "Result input must refer to an upstream dependency"
                    )
                else:
                    producer = data_sources[ref.task_id]
                    collecting = (
                        entries is not None
                        and producer.control
                        and producer.control.kind == "join"
                    )
                    if producer.control and not collecting:
                        raise ValueError(
                            "This control node cannot be referenced as data"
                        )
                    source_schema = field_schema(
                        producer.result_contract(explicit_control=entries is not None),
                        ref.field,
                        structured=structured and bool(node_contract or typed_agent),
                    )
                source_types = set(source_schema["type"])
                if check_text_binding and (
                    source_types != {"string"}
                    or not source_schema.get("required", True)
                ):
                    code = "INCOMPATIBLE"
                    raise ValueError("Connect an output guaranteed to provide text")
                if node_contract:
                    target_schema = field_schema(node_contract, key, structured=True)
                    if not source_types <= set(
                        target_schema["type"]
                    ) or source_schema.get("format") != target_schema.get("format"):
                        code = "INCOMPATIBLE"
                        raise ValueError(
                            "Connect a source matching the declared port type"
                        )
            except ValueError as exc:
                fields.append(
                    {
                        "path": path + f".inputs.{key}",
                        "code": code,
                        "message": str(exc),
                    }
                )
        if task.control and task.control.kind == "condition" and "value" in task.inputs:
            ref = feedback_inputs.get(task.id, {}).get("value", task.inputs["value"])
            try:
                kinds = (
                    (
                        set(field_schema(definition.input_contract, ref.input)["type"])
                        if structured
                        else {"string"}
                    )
                    if ref.input is not None
                    else set(
                        field_schema(
                            data_sources[ref.task_id].output_contract, ref.field
                        )["type"]
                    )
                )
                value = task.control.value
                valid = (
                    ("boolean" in kinds and type(value) is bool)
                    or ("string" in kinds and isinstance(value, str))
                    or ("null" in kinds and value is None)
                    or ("integer" in kinds and type(value) is int)
                    or ("number" in kinds and type(value) in (int, float))
                )
                if not valid or (
                    task.control.operator not in {"eq", "ne"}
                    and not kinds <= {"integer", "number"}
                ):
                    raise ValueError(
                        "Comparison and value must match the declared field type"
                    )
            except (ValueError, KeyError) as exc:
                fields.append(
                    {
                        "path": path + ".control",
                        "code": "CONDITION",
                        "message": str(exc),
                    }
                )
        try:
            missing_ports = set()
            for _, field, spec, conversion in Formatter().parse(task.task):
                if field is None:
                    continue
                if (
                    spec
                    or conversion
                    or (
                        not field.isidentifier()
                        if typed_agent
                        else field not in task.inputs
                    )
                ):
                    raise ValueError("Use only declared {input_name} placeholders")
                if task.dispatch:
                    if field != "item":
                        raise ValueError(
                            "List dispatch prompts can only use the {item} placeholder"
                        )
                    missing_ports.add(field)
                    continue
                if check_text_ports and node_contract is not None:
                    schema = node_contract["properties"].get(field, {})
                    if schema.get("type") != "string" or (
                        field not in node_contract.get("required", [])
                        and "default" not in schema
                    ):
                        missing_ports.add(field)
                        fields.append(
                            {
                                "path": path + f".inputs.{field}",
                                "code": "INCOMPATIBLE",
                                "message": "Prompt placeholders require a guaranteed text port",
                            }
                        )
                if (
                    check_text_ports
                    and field not in task.inputs
                    and "default"
                    not in (node_contract or {}).get("properties", {}).get(field, {})
                    and field not in missing_ports
                ):
                    missing_ports.add(field)
                    fields.append(
                        {
                            "path": path + f".inputs.{field}",
                            "code": "REQUIRED",
                            "message": "Connect a text source to this input port",
                        }
                    )
            if task.dispatch and "item" not in missing_ports:
                raise ValueError(
                    "Include the {item} placeholder in the list dispatch prompt"
                )
        except ValueError as exc:
            fields.append(
                {"path": path + ".task", "code": "INVALID", "message": str(exc)}
            )
    result = tasks.get(definition.result_task_id) if definition.result_task_id else None
    if result_requirement != "optional" and result is None:
        fields.append(
            {
                "path": "definition.result_task_id",
                "code": (
                    "REQUIRED"
                    if definition.port_contract_version == 1
                    and not definition.result_task_id
                    else "REFERENCE"
                ),
                "message": "Select an existing task as the workflow result",
            }
        )
    elif (
        result is not None
        and result.control
        and not (entries is not None and result.control.kind == "join")
    ):
        fields.append(
            {
                "path": "definition.result_task_id",
                "code": "REFERENCE",
                "message": "Control nodes cannot be workflow results",
            }
        )
    elif (
        result is not None
        and definition.port_contract_version == 1
        and text_port_scope is None
    ):
        try:
            if set(field_schema(result.output_contract, "text")["type"]) != {"string"}:
                raise ValueError("The result port requires text")
        except ValueError:
            fields.append(
                {
                    "path": "definition.result_task_id",
                    "code": "INCOMPATIBLE",
                    "message": "Connect a task with a required text output",
                }
            )
    if result_requirement == "reachable" and result is not None and not fields:
        reachable: set[str] = set()
        task_indexes = {task.id: index for index, task in enumerate(definition.tasks)}
        feedback_bindings: set[tuple[str, str]] = set()
        # Walk in dependency order. Treat conditional edges as possible because
        # their values are runtime data. Revisit nodes when a loop can pass or
        # provide next-round feedback.
        while True:
            unavailable = []
            previous_count = len(reachable)
            for task_id in execution_order:
                incoming = predecessors[task_id]
                task = tasks[task_id]
                can_enter = (
                    not incoming
                    and (entries is None or task_id in entries)
                    and (task.control is None or task.control.kind != "join")
                ) or any(
                    source in reachable
                    and (
                        source not in loop_exits
                        or loop_exits[source] == loop_exits.get(task_id)
                        or loop_exits[source] in reachable
                    )
                    for source in incoming
                )
                if not can_enter or (
                    task.human
                    and task.human.kind == "approval"
                    and task.human.artifact_task_id not in reachable
                ):
                    continue
                contract = task_input_contract(
                    task.input_contract, task.transform, dispatch=bool(task.dispatch)
                )
                missing = [
                    (name, binding)
                    for name, binding in task.inputs.items()
                    if binding.task_id is not None
                    and (
                        binding.task_id not in reachable
                        or (
                            entries is not None
                            and binding.task_id in loop_exits
                            and loop_exits[binding.task_id] != loop_exits.get(task_id)
                            and loop_exits[binding.task_id] not in reachable
                        )
                    )
                    and (task_id, name) not in feedback_bindings
                    and (entries is None or port_requires_value(contract, name))
                ]
                if not missing:
                    reachable.add(task_id)
                elif entries is not None:
                    unavailable.extend(
                        {
                            "path": f"definition.tasks.{task_indexes[task_id]}.inputs.{name}",
                            "code": "UNREACHABLE_DATA",
                            "message": f"No execution path can produce {binding.task_id}.{binding.field}.",
                        }
                        for name, binding in missing
                    )
            if not loop_exits or len(reachable) == previous_count:
                break
            feedback_bindings = {
                (binding.target, binding.input)
                for loop in definition.loops
                if loop.max_iterations > 1
                and loop.exit_task_id in reachable
                and all(binding.source in reachable for binding in loop.feedback)
                for binding in loop.feedback
            }
        fields.extend(unavailable)
        if result.id not in reachable:
            fields.append(
                {
                    "path": "definition.result_task_id",
                    "code": "UNREACHABLE",
                    "message": "No execution path can reach the workflow result task",
                }
            )
    if fields:
        raise RoleAssetError("Invalid workflow graph", fields)
