// Specs for the pure workflow-graph validators in dagCheck.ts (Task 7).
//
// These are the client-side mirrors of the backend DAG rules
// (astrbot/dashboard/services/agent_team_dag.py): a workflow graph must be
// acyclic, every edge must reference known node ids and node ids must be
// unique. The functions stay pure (no Vue imports) so they can be reused by
// the editor banner, the save gate and future monitor UI.
import { describe, expect, it } from 'vitest';
import { findCycle, renderableError } from './dagCheck';

const N = (id: string) => ({ id });

describe('findCycle', () => {
  it('returns null for an acyclic diamond graph', () => {
    const nodes = [N('a'), N('b'), N('c'), N('d')];
    const edges = [
      { from: 'a', to: 'b' },
      { from: 'a', to: 'c' },
      { from: 'b', to: 'd' },
      { from: 'c', to: 'd' },
    ];
    expect(findCycle(nodes, edges)).toBeNull();
  });

  it('returns the cycle path (closed with the start node) for a 3-cycle', () => {
    const nodes = [N('a'), N('b'), N('c')];
    const edges = [
      { from: 'a', to: 'b' },
      { from: 'b', to: 'c' },
      { from: 'c', to: 'a' },
    ];
    expect(findCycle(nodes, edges)).toEqual(['a', 'b', 'c', 'a']);
  });

  it('returns a self-loop as a one-node cycle', () => {
    const nodes = [N('a'), N('b')];
    const edges = [{ from: 'a', to: 'a' }];
    expect(findCycle(nodes, edges)).toEqual(['a', 'a']);
  });

  it('finds a cycle inside a later disconnected component', () => {
    const nodes = [N('a'), N('b'), N('x'), N('y')];
    const edges = [
      { from: 'a', to: 'b' },
      { from: 'x', to: 'y' },
      { from: 'y', to: 'x' },
    ];
    expect(findCycle(nodes, edges)).toEqual(['x', 'y', 'x']);
  });

  it('returns null for empty graphs and ignores edges without both endpoints', () => {
    expect(findCycle([], [])).toBeNull();
    expect(findCycle([N('a')], [])).toBeNull();
    // Defensive: malformed edges must not crash the DFS.
    expect(
      findCycle([N('a')], [{ from: '', to: 'a' } as any]),
    ).toBeNull();
  });
});

describe('renderableError', () => {
  it('returns null for a clean graph', () => {
    const graph = {
      nodes: [N('n1'), N('n2')],
      edges: [{ from: 'n1', to: 'n2' }],
    };
    expect(renderableError(graph)).toBeNull();
  });

  it('reports the first duplicate node id', () => {
    const graph = {
      nodes: [N('n1'), N('n2'), N('n1')],
      edges: [],
    };
    expect(renderableError(graph)).toContain('n1');
  });

  it('reports a dangling edge target', () => {
    const graph = {
      nodes: [N('n1')],
      edges: [{ from: 'n1', to: 'n9' }],
    };
    expect(renderableError(graph)).toContain('n9');
  });

  it('reports a dangling edge source', () => {
    const graph = {
      nodes: [N('n1')],
      edges: [{ from: 'n8', to: 'n1' }],
    };
    expect(renderableError(graph)).toContain('n8');
  });

  it('returns null for empty or malformed graphs', () => {
    expect(renderableError({ nodes: [], edges: [] })).toBeNull();
    expect(renderableError({ nodes: undefined as any, edges: undefined as any })).toBeNull();
  });
});
