"""Declarative DAG definitions and field rules; no user code is evaluated."""

from operator import ge, gt, le, lt
from string import Formatter
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from astrbot.core.agent_teams.assets import RoleAssetError, RoleOverrides

HUMAN_APPROVAL_CONTRACT = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "text": {"type": "string"},
    },
    "required": ["approved", "text"],
}


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


class TeamCall(BaseModel):
    """An exact autonomous asset version invoked in an independent scope."""

    model_config = ConfigDict(extra="forbid", strict=True)
    asset_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)


class WorkflowCall(TeamCall):
    """An allowed workflow version and the task whose exact result is returned."""

    result_task_id: str = Field(min_length=1, max_length=128)


class GraphTask(BaseModel):
    """A task bound to a named member instance with a fixed role version."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z][\w-]*$")
    name: str = Field(min_length=1, max_length=128)
    member: str = Field(default="", max_length=128)
    role_id: str | None = Field(default=None, min_length=1, max_length=128)
    role_version: int | None = Field(default=None, ge=1)
    task: str = Field(default="", max_length=100_000)
    human: HumanTask | None = None
    team: TeamCall | None = None
    workflow: WorkflowCall | None = None
    inputs: dict[str, TaskInput] = Field(default_factory=dict, max_length=30)
    overrides: RoleOverrides = Field(default_factory=RoleOverrides)
    output_contract: dict | None = None
    position: dict[Literal["x", "y"], float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_execution(self):
        """Require either agent execution fields or an unambiguous human node.

        Returns:
            Validated node.

        Raises:
            ValueError: The selected node kind has missing or conflicting fields.
        """
        if self.team is not None or self.workflow is not None:
            if (
                self.team is not None
                and self.workflow is not None
                or self.human is not None
                or self.member
                or self.role_id
                or self.role_version
                or self.task
                or self.overrides.model_dump(exclude_none=True)
            ):
                raise ValueError(
                    "Child calls cannot also configure agent or human execution"
                )
        elif self.human is None:
            if (
                not self.member
                or not self.role_id
                or not self.role_version
                or not self.task
            ):
                raise ValueError("Agent tasks require member, pinned role and task")
        else:
            if (
                self.member
                or self.role_id
                or self.role_version
                or self.task
                or self.inputs
                or self.output_contract
                not in (
                    None,
                    HUMAN_APPROVAL_CONTRACT if self.human.kind == "approval" else None,
                )
                or self.overrides.model_dump(exclude_none=True)
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


class GraphDefinition(BaseModel):
    """A bounded, versioned workflow graph with persisted editor layout."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=128)
    required_inputs: list[str] = Field(default_factory=list, max_length=30)
    max_parallel: int = Field(default=4, ge=1, le=100)
    max_depth: int = Field(default=3, ge=1, le=20)
    tasks: list[GraphTask] = Field(min_length=1, max_length=100)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=500)
    loops: list[GraphLoop] = Field(default_factory=list, max_length=30)


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
            targets = set()
            for feedback in loop.feedback:
                if feedback.source not in body or feedback.target not in body:
                    raise ValueError(
                        "Feedback must explicitly connect tasks within the loop body"
                    )
                target = tasks[feedback.target]
                if target.human or feedback.input not in target.inputs:
                    raise ValueError(
                        "Feedback must override a declared agent task input"
                    )
                if (feedback.target, feedback.input) in targets:
                    raise ValueError(
                        "Each next-round input has exactly one feedback source"
                    )
                targets.add((feedback.target, feedback.input))
                field_schema(tasks[feedback.source].output_contract, feedback.field)
        except ValueError as exc:
            fields.append({"path": path, "code": "LOOP", "message": str(exc)})
        grouped |= body
        loop_ids.add(loop.id)
    members = {}
    for index, task in enumerate(definition.tasks):
        path = f"definition.tasks.{index}"
        if task.human is not None:
            if task.human.kind == "approval" and (
                task.human.artifact_task_id not in ancestors.get(task.id, set())
                or tasks[task.human.artifact_task_id].human is not None
            ):
                fields.append(
                    {
                        "path": path + ".human.artifact_task_id",
                        "code": "REFERENCE",
                        "message": "Approve an upstream dependency's exact artifact version",
                    }
                )
            continue
        config = (task.role_id, task.role_version, task.overrides.model_dump())
        if not task.team and task.member in members and members[task.member] != config:
            fields.append(
                {
                    "path": path + ".member",
                    "code": "CONFLICT",
                    "message": "A member must use the same frozen role and configuration across tasks",
                }
            )
        if not task.team:
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
