"""Declarative DAG definitions and field rules; no user code is evaluated."""

from string import Formatter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from astrbot.core.agent_teams.assets import RoleAssetError, RoleOverrides


class TaskInput(BaseModel):
    """A declared workflow input or an upstream validated result field."""

    model_config = ConfigDict(extra="forbid", strict=True)
    input: str | None = None
    task_id: str | None = None
    field: str = "text"


class GraphTask(BaseModel):
    """A task bound to a named member instance with a fixed role version."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z][\w-]*$")
    name: str = Field(min_length=1, max_length=128)
    member: str = Field(min_length=1, max_length=128)
    role_id: str = Field(min_length=1, max_length=128)
    role_version: int = Field(ge=1)
    task: str = Field(min_length=1, max_length=100_000)
    inputs: dict[str, TaskInput] = Field(default_factory=dict, max_length=30)
    overrides: RoleOverrides = Field(default_factory=RoleOverrides)
    output_contract: dict | None = None
    position: dict[Literal["x", "y"], float] = Field(default_factory=dict)


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


class GraphDefinition(BaseModel):
    """A bounded, versioned workflow graph with persisted editor layout."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=128)
    required_inputs: list[str] = Field(default_factory=list, max_length=30)
    max_parallel: int = Field(default=4, ge=1, le=100)
    tasks: list[GraphTask] = Field(min_length=1, max_length=100)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=500)


def field_schema(contract: dict | None, path: str) -> dict:
    """Resolve a guaranteed scalar field through local references and allOf.

    Args:
        contract: Frozen output object contract, or the default text contract.
        path: Dot-separated property path, never an expression or array lookup.

    Returns:
        Declared scalar schema of a required output field.

    Raises:
        ValueError: The field can be absent or is not a declared scalar.
    """
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
        if name not in required or not properties:
            raise ValueError(
                f"Field {path} must be declared and required in the output contract"
            )
        # Constraints on the same property combine across allOf branches.
        current = {"allOf": properties}
    visited = set()
    candidates = [current]
    declared: set[str] | None = None
    while candidates:
        item = candidates.pop()
        if not isinstance(item, dict) or id(item) in visited:
            continue
        visited.add(id(item))
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
    if not declared or not declared <= {
        "string",
        "boolean",
        "integer",
        "number",
        "null",
    }:
        raise ValueError(f"Field {path} must declare a scalar type")
    return {"type": sorted(declared)}


def validate_graph(definition: GraphDefinition) -> None:
    """Validate references, member identity, field rules and acyclic dependencies.

    Args:
        definition: Parsed graph whose contracts and roles are also validated.

    Raises:
        RoleAssetError: Field-specific graph diagnostics before saving or running.
    """
    fields = []
    tasks = {task.id: task for task in definition.tasks}
    names = definition.required_inputs
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
    for index, edge in enumerate(definition.edges):
        path = f"definition.edges.{index}"
        if edge.source not in tasks or edge.target not in tasks:
            fields.append(
                {
                    "path": path,
                    "code": "REFERENCE",
                    "message": "Dependency must refer to existing tasks",
                }
            )
            continue
        if (edge.source, edge.target) in seen:
            fields.append(
                {
                    "path": path,
                    "code": "DUPLICATE",
                    "message": "Dependency already exists",
                }
            )
        seen.add((edge.source, edge.target))
        predecessors[edge.target].add(edge.source)
        if edge.condition:
            rule = edge.condition
            try:
                schema = field_schema(tasks[edge.source].output_contract, rule.field)
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
    members = {}
    for index, task in enumerate(definition.tasks):
        path = f"definition.tasks.{index}"
        config = (task.role_id, task.role_version, task.overrides.model_dump())
        if task.member in members and members[task.member] != config:
            fields.append(
                {
                    "path": path + ".member",
                    "code": "CONFLICT",
                    "message": "A member must use the same frozen role and configuration across tasks",
                }
            )
        members[task.member] = config
        for key, ref in task.inputs.items():
            try:
                if not key.isidentifier():
                    raise ValueError("Input names must be identifiers")
                if ref.input is not None:
                    if ref.input not in names or ref.task_id is not None:
                        raise ValueError(
                            "Reference exactly one declared workflow input"
                        )
                elif ref.task_id not in ancestors.get(task.id, set()):
                    raise ValueError(
                        "Result input must refer to an upstream dependency"
                    )
                else:
                    field_schema(tasks[ref.task_id].output_contract, ref.field)
            except ValueError as exc:
                fields.append(
                    {
                        "path": path + f".inputs.{key}",
                        "code": "REFERENCE",
                        "message": str(exc),
                    }
                )
        try:
            for _, field, spec, conversion in Formatter().parse(task.task):
                if field is not None and (
                    field not in {*names, *task.inputs} or spec or conversion
                ):
                    raise ValueError("Use only declared {input_name} placeholders")
        except ValueError as exc:
            fields.append(
                {"path": path + ".task", "code": "INVALID", "message": str(exc)}
            )
    if fields:
        raise RoleAssetError("Invalid workflow graph", fields)
