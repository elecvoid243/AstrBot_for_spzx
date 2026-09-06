// Specs for the pure workflow-graph validators in dagCheck.ts (Task 7).
//
// These are the client-side mirrors of the backend DAG rules
// (astrbot/dashboard/services/agent_team_dag.py): a workflow graph must be
// acyclic, every edge must reference known node ids and node ids must be
// unique. The functions stay pure (no Vue imports) so they can be reused by
// the editor banner, the save gate and future monitor UI.
import { describe, expect, it } from 'vitest';
import { findCycle, renderableError, unconnectedReferences } from './dagCheck';

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

describe('unconnectedReferences', () => {
  const nodes = [N('n1'), N('n2'), N('n3')];

  it('reports graph nodes referenced but not directly connected to the node', () => {
    const edges = [{ from: 'n1', to: 'n3' }];
    // n1 is a direct predecessor of n3; n2 is not.
    expect(unconnectedReferences('n3', 'do {{n2}} after {{n1}}', nodes, edges)).toEqual(['n2']);
  });

  it('treats every direct predecessor edge direction as connected', () => {
    const edges = [{ from: 'n2', to: 'n1' }];
    expect(unconnectedReferences('n1', 'use {{n2}}', nodes, edges)).toEqual([]);
  });

  it('never counts the {{input}} placeholder', () => {
    expect(unconnectedReferences('n1', 'x {{input}} y', nodes, [])).toEqual([]);
  });

  it('ignores ids that are not graph nodes and malformed placeholders', () => {
    expect(unconnectedReferences('n1', 'see {{n9}} and {n2} and {{}}', nodes, [])).toEqual([]);
  });

  it('reports a self-reference (a node is not its own predecessor)', () => {
    expect(unconnectedReferences('n1', 'self {{n1}}', nodes, [])).toEqual(['n1']);
  });

  it('tolerates whitespace and dedupes repeated references', () => {
    expect(unconnectedReferences('n1', '{{ n2 }} plus {{n2}}', nodes, [])).toEqual(['n2']);
  });

  it('returns references in order of first appearance', () => {
    expect(unconnectedReferences('n1', '{{n3}} then {{n2}} then {{n3}}', nodes, [])).toEqual([
      'n3',
      'n2',
    ]);
  });

  it('returns nothing for blank templates and malformed edges', () => {
    expect(unconnectedReferences('n1', '', nodes, [])).toEqual([]);
    expect(
      unconnectedReferences('n1', '{{n2}}', nodes, [{ from: '', to: 'n1' } as any]),
    ).toEqual(['n2']);
  });
});
