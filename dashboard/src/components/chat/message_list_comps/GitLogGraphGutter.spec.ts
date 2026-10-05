// Author: elecvoid243 @ 2026-10-05
// Spec: docs/superpowers/specs/2026-10-05-git-log-graph-and-branch-tree-design.md §4.2
// DOM 契约测试：竖线是元素（展开行时靠 top/bottom 拉伸），曲线与节点是 SVG。

import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import GitLogGraphGutter from "./GitLogGraphGutter.vue";
import { NODE_CY, gutterWidth, type GraphRow } from "@/composables/gitGraphLayout";

const row = (over: Partial<GraphRow> = {}): GraphRow => ({
  lane: 0,
  ins: [],
  passIn: [],
  outs: [],
  cont: false,
  dangling: false,
  ...over,
});

describe("GitLogGraphGutter", () => {
  it("renders one vertical per pass-through lane and takes its width from gutterWidth", () => {
    const w = mount(GitLogGraphGutter, {
      props: { row: row({ passIn: [1, 2] }), lanes: 3 },
    });

    expect(
      w.findAll('[data-seg="pass"]').map((v) => v.attributes("data-lane")),
    ).toEqual(["1", "2"]);
    expect(w.attributes("style")).toContain(`width: ${gutterWidth(3)}px`);
  });

  it("draws the node at NODE_CY and only renders the lower half when it continues", () => {
    const w = mount(GitLogGraphGutter, {
      props: { row: row({ cont: true }), lanes: 2 },
    });

    expect(w.find("circle").attributes("cy")).toBe(String(NODE_CY));
    expect(w.find('[data-seg="node-down"]').exists()).toBe(true);

    const stopped = mount(GitLogGraphGutter, {
      props: { row: row({ cont: false }), lanes: 2 },
    });
    expect(stopped.find('[data-seg="node-down"]').exists()).toBe(false);
  });

  it("marks an out-of-window parent so the lower half can fade", () => {
    const w = mount(GitLogGraphGutter, {
      props: { row: row({ cont: true, dangling: true }), lanes: 2 },
    });

    expect(w.find('[data-seg="node-down"]').attributes("data-dangling")).toBe(
      "true",
    );
  });

  it("draws one path per in/out edge and a head ring when asked", () => {
    const w = mount(GitLogGraphGutter, {
      props: { row: row({ ins: [1], outs: [2], cont: true }), lanes: 3 },
    });

    expect(w.findAll("path")).toHaveLength(2);

    const head = mount(GitLogGraphGutter, {
      props: { row: row({ cont: true }), lanes: 2, isHead: true },
    });
    // 衬底圆 + 外环 + 内点：外环内不能漏出贯穿的竖线
    expect(head.findAll("circle")).toHaveLength(3);
  });
});
