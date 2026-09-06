"""Pure DAG helpers for Agent Teams manual orchestration (spec §6.3).

No IO here: everything operates on plain dicts so scheduling rules are
unit-testable without ports or database.
"""

import re

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class TeamDAGError(ValueError):
    """Raised for invalid workflow graphs or task template render failures.

    kind: machine-readable classification for field-error mapping — one of
    ``cycle | duplicate | dangling | missing_id``; None for render-time
    errors (those surface as node failures, not workflow field errors).
    """

    def __init__(self, message: str, kind: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind


def _node_ids(nodes: list[dict]) -> list[str]:
    ids = [str(n.get("id", "")) for n in nodes]
    if any(not i for i in ids):
        raise TeamDAGError("node missing id", kind="missing_id")
    if len(set(ids)) != len(ids):
        raise TeamDAGError("duplicate node id", kind="duplicate")
    return ids


def validate_dag(nodes: list[dict], edges: list[dict]) -> dict[str, list[str]]:
    """Validate a workflow graph and return the successor adjacency.

    Args:
        nodes: [{id, ...}] node list.
        edges: [{from, to}] directed edges.

    Returns:
        Adjacency dict mapping node id -> list of direct successor ids.

    Raises:
        TeamDAGError: On duplicate ids, edges referencing unknown nodes,
            or a cycle.
    """
    ids = _node_ids(nodes)
    id_set = set(ids)
    adjacency: dict[str, list[str]] = {i: [] for i in ids}
    for edge in edges:
        src, dst = str(edge.get("from", "")), str(edge.get("to", ""))
        if src not in id_set or dst not in id_set:
            raise TeamDAGError(
                f"edge references unknown node: {src!r}->{dst!r}", kind="dangling"
            )
        adjacency[src].append(dst)

    # Kahn's algorithm; leftover nodes mean a cycle.
    indegree = dict.fromkeys(ids, 0)
    for successors in adjacency.values():
        for dst in successors:
            indegree[dst] += 1
    queue = [i for i in ids if indegree[i] == 0]
    visited = 0
    while queue:
        node = queue.pop()
        visited += 1
        for dst in adjacency[node]:
            indegree[dst] -= 1
            if indegree[dst] == 0:
                queue.append(dst)
    if visited != len(ids):
        raise TeamDAGError("cycle detected in workflow graph", kind="cycle")
    return adjacency


def topological_layers(nodes: list[dict], edges: list[dict]) -> list[list[str]]:
    """Layer nodes so every node appears after all its predecessors.

    Args:
        nodes: [{id, ...}] node list (must validate).
        edges: [{from, to}] directed edges.

    Returns:
        List of layers, each a list of mutually independent node ids.
    """
    adjacency = validate_dag(nodes, edges)
    indegree = dict.fromkeys(adjacency, 0)
    for successors in adjacency.values():
        for dst in successors:
            indegree[dst] += 1
    layer = [i for i, degree in indegree.items() if degree == 0]
    layers: list[list[str]] = []
    while layer:
        layers.append(layer)
        next_layer: list[str] = []
        for node in layer:
            for dst in adjacency[node]:
                indegree[dst] -= 1
                if indegree[dst] == 0:
                    next_layer.append(dst)
        layer = next_layer
    return layers


def downstream_of(node_id: str, edges: list[dict]) -> list[str]:
    """Return every transitive successor of node_id (cascade-skip set)."""
    successors: dict[str, list[str]] = {}
    for edge in edges:
        successors.setdefault(str(edge.get("from", "")), []).append(
            str(edge.get("to", ""))
        )
    seen: list[str] = []
    stack = list(successors.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.append(current)
        stack.extend(successors.get(current, []))
    return seen


def referenced_placeholders(template: str) -> set[str]:
    """Return the placeholder names a template references.

    Args:
        template: Template text containing {{input}} / {{<node_id>}}.

    Returns:
        The set of referenced placeholder keys (whitespace-tolerant).
    """
    return set(_PLACEHOLDER_RE.findall(template))


def render_task(
    template: str,
    run_input: str,
    results: dict[str, str],
    max_length: int,
    *,
    auto_inject_predecessors: list[tuple[str, str]] | None = None,
) -> str:
    """Render a node task template.

    With `auto_inject_predecessors`, results of predecessors the template
    does NOT reference explicitly are appended after the rendered template
    as ``[上游结果]`` blocks (spec §3.2, edges-as-data-flow): each eligible
    ``(node_id, display_name)`` contributes

    ::

        [上游结果]
        ◆ {display_name} ({node_id})：
        {result tail-truncated to max_length}

    Blocks are separated by blank lines and attached to the template with a
    blank line; a predecessor that is referenced by a placeholder in the
    template, or whose result is missing, contributes nothing.

    Args:
        template: Template text containing {{input}} / {{<node_id>}}.
        run_input: The run-level input substituted for {{input}}.
        results: node_id -> full reply text for referenced predecessors.
        max_length: Tail-truncation limit for each substitution and injected
            result.
        auto_inject_predecessors: Optional (node_id, display_name) pairs to
            auto-inject; None keeps the plain substitution behavior.

    Returns:
        The rendered task text.

    Raises:
        TeamDAGError: If a placeholder references an unknown node.
    """

    def _substitute(match: re.Match) -> str:
        key = match.group(1)
        if key == "input":
            value = run_input
        elif key in results:
            value = results[key]
        else:
            raise TeamDAGError(
                f"unknown placeholder {{{{{key}}}}}; known: input, {sorted(results)}"
            )
        if len(value) > max_length:
            value = f"…[已截断，仅保留尾部]\n{value[-max_length:]}"
        return value

    rendered = _PLACEHOLDER_RE.sub(_substitute, template)
    if not auto_inject_predecessors:
        return rendered
    referenced = referenced_placeholders(template)
    blocks: list[str] = []
    for node_id, display_name in auto_inject_predecessors:
        # Defensive re-check: explicitly referenced preds are already in the
        # text, and a missing result means the pred never finished usefully.
        if node_id in referenced or node_id not in results:
            continue
        value = results[node_id]
        if len(value) > max_length:
            value = f"…[已截断，仅保留尾部]\n{value[-max_length:]}"
        blocks.append(f"[上游结果]\n◆ {display_name} ({node_id})：\n{value}")
    if not blocks:
        return rendered
    return rendered + "\n\n" + "\n\n".join(blocks)
