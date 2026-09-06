// Pure workflow-graph validators for the agent-teams workflow editor
// (Task 7). These mirror the backend DAG rules enforced by
// astrbot/dashboard/services/agent_team_dag.py + agent_team_service.py:
// node ids must be unique, every edge endpoint must reference a known
// node, and the graph must stay acyclic. No Vue imports — usable from
// any component, the save gate and unit tests.

/** Minimal node shape required by the validators (payload nodes carry more). */
export interface DagCheckNode {
  id: string;
  [key: string]: unknown;
}

/** Minimal directed edge shape of the workflow payload graph. */
export interface DagCheckEdge {
  from: string;
  to: string;
}

/** Raw workflow payload graph ({nodes, edges}). */
export interface DagCheckGraph {
  nodes?: DagCheckNode[] | null;
  edges?: DagCheckEdge[] | null;
}

/**
 * Find a cycle in the graph via depth-first search with stack tracking.
 *
 * Node and neighbor iteration follow array order, so the returned path is
 * deterministic for a given graph.
 *
 * Args:
 *   nodes: Graph nodes (only `id` is read).
 *   edges: Directed edges (`from` -> `to`).
 *
 * Returns:
 *   The cycle path closed with its start node (e.g. `["a","b","a"]`, or
 *   `["a","a"]` for a self-loop), or null when the graph is acyclic.
 */
export function findCycle(
  nodes: DagCheckNode[],
  edges: DagCheckEdge[],
): string[] | null {
  const adjacency = new Map<string, string[]>();
  for (const edge of edges ?? []) {
    if (!edge || !edge.from || !edge.to) continue;
    const list = adjacency.get(edge.from) ?? [];
    list.push(edge.to);
    adjacency.set(edge.from, list);
  }

  // 0 = unvisited, 1 = on the current DFS stack, 2 = fully explored.
  const state = new Map<string, 0 | 1 | 2>();
  const stack: string[] = [];

  const visit = (id: string): string[] | null => {
    state.set(id, 1);
    stack.push(id);
    for (const next of adjacency.get(id) ?? []) {
      const nextState = state.get(next) ?? 0;
      if (nextState === 1) {
        // Back edge: the cycle runs from the first stack occurrence of
        // `next` up to here, closed by repeating `next`.
        const start = stack.indexOf(next);
        return [...stack.slice(start), next];
      }
      if (nextState === 0) {
        const cycle = visit(next);
        if (cycle) return cycle;
      }
    }
    stack.pop();
    state.set(id, 2);
    return null;
  };

  for (const node of nodes ?? []) {
    if (!node?.id || (state.get(node.id) ?? 0) !== 0) continue;
    const cycle = visit(node.id);
    if (cycle) return cycle;
  }
  return null;
}

/**
 * Check a graph for render-blocking structural errors the UI can show
 * directly: duplicate node ids and edges referencing unknown nodes.
 * Cycles are intentionally not reported here — use `findCycle` so the
 * banner can render the actual path.
 *
 * Args:
 *   graph: Raw `{nodes, edges}` payload graph.
 *
 * Returns:
 *   A human-readable error message, or null when the graph is clean.
 */
export function renderableError(graph: DagCheckGraph): string | null {
  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];

  const seen = new Set<string>();
  for (const node of nodes) {
    if (!node?.id) continue;
    if (seen.has(node.id)) {
      return `Duplicate node id: ${node.id}`;
    }
    seen.add(node.id);
  }

  for (const edge of edges) {
    if (!edge?.from || !edge?.to) continue;
    if (!seen.has(edge.from)) {
      return `Edge ${edge.from}->${edge.to} references unknown node ${edge.from}`;
    }
    if (!seen.has(edge.to)) {
      return `Edge ${edge.from}->${edge.to} references unknown node ${edge.to}`;
    }
  }
  return null;
}

/**
 * Placeholder grammar shared with the backend renderer
 * (astrbot/dashboard/services/agent_team_dag.py `_PLACEHOLDER_RE`):
 * whitespace-tolerant `{{ key }}` with alphanumeric/underscore keys.
 */
const PLACEHOLDER_RE = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;

/**
 * Find which graph node ids a node's task template references without the
 * node being directly connected to them.
 *
 * Every `{{<id>}}` in the template whose id is a graph node id but not a
 * direct predecessor of `nodeId` is reported (in order of first appearance,
 * deduplicated). `{{input}}` is the run input, never a node reference, and
 * ids that do not match any graph node are ignored. Informational only —
 * the backend auto-injects unconnected predecessor results.
 *
 * Args:
 *   nodeId: The node whose template is inspected.
 *   template: The task template text (may be empty).
 *   nodes: Graph nodes (only `id` is read).
 *   edges: Directed edges (`from` -> `to`).
 *
 * Returns:
 *   Referenced-but-unconnected graph node ids.
 */
export function unconnectedReferences(
  nodeId: string,
  template: string,
  nodes: DagCheckNode[],
  edges: DagCheckEdge[],
): string[] {
  if (!template) return [];
  const nodeIds = new Set((nodes ?? []).map((n) => n?.id).filter(Boolean) as string[]);
  const predecessors = new Set(
    (edges ?? [])
      .filter((edge) => edge && edge.from && edge.to === nodeId)
      .map((edge) => edge.from as string),
  );

  const referenced: string[] = [];
  const seen = new Set<string>();
  for (const match of template.matchAll(PLACEHOLDER_RE)) {
    const id = match[1];
    if (id === 'input' || !nodeIds.has(id) || predecessors.has(id) || seen.has(id)) continue;
    seen.add(id);
    referenced.push(id);
  }
  return referenced;
}
