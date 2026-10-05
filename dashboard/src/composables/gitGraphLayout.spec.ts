// Author: elecvoid243 @ 2026-10-05
// Spec: docs/superpowers/specs/2026-10-05-git-log-graph-and-branch-tree-design.md §4.3
// 纯函数：把 topo 序的 commit DAG 摊成每行的 lane 渲染指令。

import { describe, expect, it } from "vitest";
import { gutterWidth, layoutGraph, type GraphCommit } from "./gitGraphLayout";

const c = (sha: string, parents: string[] = []): GraphCommit => ({ sha, parents });

describe("gutterWidth", () => {
  it("costs nothing for a single lane", () => {
    expect(gutterWidth(0)).toBe(0);
    expect(gutterWidth(1)).toBe(0);
    expect(gutterWidth(2)).toBe(34); // GUTTER_PAD*2 + 2*LANE_W
    expect(gutterWidth(4)).toBe(60);
  });
});

describe("layoutGraph", () => {
  it("keeps a linear history on lane 0 and marks the out-of-window parent", () => {
    const { rows, lanes } = layoutGraph([
      c("c3", ["c2"]),
      c("c2", ["c1"]),
      c("c1", ["gone"]),
    ]);

    expect(lanes).toBe(1);
    expect(rows.map((r) => r.lane)).toEqual([0, 0, 0]);
    expect(
      rows.every((r) => r.ins.length === 0 && r.passIn.length === 0 && r.outs.length === 0),
    ).toBe(true);
    expect(rows[0]).toMatchObject({ cont: true, dangling: false });
    expect(rows[2]).toMatchObject({ cont: true, dangling: true });
  });

  it("forks the second parent of a merge commit into a new lane", () => {
    const { rows, lanes } = layoutGraph([
      c("m", ["a", "b"]),
      c("a", ["a0"]),
      c("b", ["a0"]),
      c("a0", []),
    ]);

    expect(lanes).toBe(2);
    expect(rows[0]).toMatchObject({ lane: 0, outs: [1] });
    expect(rows[1]).toMatchObject({ lane: 0, passIn: [1] });
    expect(rows[2]).toMatchObject({ lane: 1, ins: [] });
  });

  it("converges two lanes at the shared parent's row, not at the child's", () => {
    const { rows, lanes } = layoutGraph([
      c("m", ["a", "b"]),
      c("a", ["p"]),
      c("b", ["p"]),
      c("p", []),
    ]);

    // b 行仍与 a 行并行指向 p（本列不提前释放）
    expect(rows[2]).toMatchObject({ lane: 1, cont: true, outs: [] });
    // 收敛发生在 p 所在的节点行：另一列从上方弯进 lane 0
    expect(rows[3]).toMatchObject({ lane: 0, ins: [1] });
    expect(lanes).toBe(2);
  });

  it("draws a fork edge when a second parent already owns a lane", () => {
    const { rows, lanes } = layoutGraph([
      c("m1", ["a", "p"]),
      c("a", ["m2"]),
      c("m2", ["b", "p"]),
      c("b", ["c"]),
      c("p", []),
      c("c", []),
    ]);

    // m2 的第二父 p 已在 lane 1 上：只画 fork 边，不新开列
    expect(rows[2]).toMatchObject({ lane: 0, outs: [1] });
    // p 的列在 b/c 行仍然贯穿（说明那条 lane 没被释放）
    expect(rows[3].passIn).toContain(1);
    expect(lanes).toBe(2);
  });

  it("reuses a freed lane instead of growing the lane count", () => {
    const { rows, lanes } = layoutGraph([
      c("m1", ["a", "b"]),
      c("a", ["p"]),
      c("b", ["p"]),
      c("p", ["c", "d"]),
      c("c", []),
      c("d", []),
    ]);

    // p 行先因 ins 释放 lane 1，再把第二父 d 分配到刚空出的 lane 1
    expect(rows[3]).toMatchObject({ lane: 0, ins: [1], outs: [1] });
    expect(rows[4].lane).toBe(0);
    expect(rows[5].lane).toBe(1);
    expect(lanes).toBe(2);
  });
});
