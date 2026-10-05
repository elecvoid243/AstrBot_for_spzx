// Author: elecvoid243 @ 2026-10-05
// Spec: docs/superpowers/specs/2026-10-05-git-log-graph-and-branch-tree-design.md §4.2
// DOM 契约测试：竖线是元素（展开行时靠 top/bottom 拉伸），曲线与节点是 SVG。

import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import GitLogGraphGutter from "./GitLogGraphGutter.vue";
import {
  BAND,
  GUTTER_LEFT,
  NODE_CY,
  STROKE_W,
  gutterWidth,
  type GraphRow,
} from "@/composables/gitGraphLayout";

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

  it("positions itself against the row rather than sitting in flow", () => {
    const w = mount(GitLogGraphGutter, { props: { row: row(), lanes: 2 } });
    const style = w.attributes("style") ?? "";

    // gutter 的孩子全是绝对定位:流内元素的高度由内容决定,会塌成 0,
    // 于是 top:0;bottom:0 的竖线高度为 0,真机上只剩孤立的点和短弧。
    // 定位契约由组件自己声明,jsdom 才守得住它。
    expect(style).toContain("position: absolute");
    expect(style).toContain("top: 0px");
    // 下沿多出 1px:跨过 .git-log-item 的 border-bottom,否则每条 lane
    // 在每个行边界都会缺 1px(截图里表现为间断的细口)。
    expect(style).toContain("bottom: -1px");
    expect(style).toContain(`left: ${GUTTER_LEFT}px`);
  });

  it("continues a fork line down to the row's bottom", () => {
    const w = mount(GitLogGraphGutter, {
      props: { row: row({ lane: 0, outs: [1], cont: true }), lanes: 2 },
    });

    // 弧线只画到曲线层底(BAND);剩下的必须由竖线接到行底 —— 否则真机上
    // 每个分叉都会留 24~26px 断口(2026-10-06 实测截图:26px)。
    const stub = w.find('[data-seg="out"][data-lane="1"]');
    expect(stub.exists()).toBe(true);
    expect(stub.attributes("style")).toContain(`top: ${BAND}px`);
  });

  it("does not double-draw a fork target that already passes through", () => {
    const w = mount(GitLogGraphGutter, {
      props: {
        row: row({ lane: 0, outs: [1], passIn: [1], cont: true }),
        lanes: 2,
      },
    });

    // passIn 的竖线已经覆盖整行,再补一段是冗余绘制
    expect(w.find('[data-seg="out"][data-lane="1"]').exists()).toBe(false);
  });

  it("draws curves with the same weight as the verticals, from one constant", () => {
    const w = mount(GitLogGraphGutter, {
      props: { row: row({ ins: [1], cont: true }), lanes: 2 },
    });

    const style = w.attributes("style") ?? "";
    expect(style).toContain(`--spcode-graph-sw: ${STROKE_W}px`);
    expect(w.find("path").attributes("stroke-width")).toBe(String(STROKE_W));
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
