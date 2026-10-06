// Author: elecvoid243 @ 2026-10-06
// Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-frontend-design.md §2
//
// 双选模型:第一次点选 = 基准(from),第二次 = 目标(to);
// 不做时间序纠正;working 伪行不参与。

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { ref } from "vue";
import DocumentHistoryPanel from "./DocumentHistoryPanel.vue";
import type { UseSpcodeGitLog } from "@/composables/useSpcodeGitLog";

const SHA_A = "a".repeat(40);
const SHA_B = "b".repeat(40);
const SHA_C = "c".repeat(40);

function makeGitLog() {
  return {
    state: ref({
      kind: "ok" as const,
      snapshot: {
        commits: [
          { sha: SHA_A, subject: "commit A", author: "x" },
          { sha: SHA_B, subject: "commit B", author: "y" },
          { sha: SHA_C, subject: "commit C", author: "z" },
        ],
      },
    }),
    refresh: vi.fn(),
  } as unknown as UseSpcodeGitLog;
}

function mountPanel(rangeBase: string | null = null) {
  return mount(DocumentHistoryPanel, {
    props: {
      gitLog: makeGitLog(),
      fileRelative: "main.py",
      currentRevision: null,
      isLoading: false,
      rangeBase,
    },
  });
}

describe("DocumentHistoryPanel range compare", () => {
  it("emits set-range-base on base button click", async () => {
    const w = mountPanel();
    const rows = w.findAll(".document-history-panel__row");
    // rows[0] = working 伪行;rows[1] = commit A
    const baseBtn = rows[1].find('[data-testid="range-base-btn"]');
    expect(baseBtn.exists()).toBe(true);
    await baseBtn.trigger("click");
    expect(w.emitted("set-range-base")).toEqual([[SHA_A]]);
    // 尚未选目标 → 不 emit compare-range
    expect(w.emitted("compare-range")).toBeUndefined();
  });

  it("base row shows badge, compare button emits compare-range(base, target)", async () => {
    const w = mountPanel(SHA_A);
    const rows = w.findAll(".document-history-panel__row");
    // 基准行显示徽章
    expect(rows[1].find('[data-testid="range-base-badge"]').exists()).toBe(true);
    // 非基准行的比较按钮语义切换
    const cmpBtn = rows[2].find('[data-testid="compare-btn"]');
    await cmpBtn.trigger("click");
    expect(w.emitted("compare-range")).toEqual([[SHA_A, SHA_B]]);
    expect(w.emitted("compare-current")).toBeUndefined();
  });

  it("clicking base button on the base row clears the base", async () => {
    const w = mountPanel(SHA_A);
    const rows = w.findAll(".document-history-panel__row");
    await rows[1].find('[data-testid="range-base-btn"]').trigger("click");
    expect(w.emitted("set-range-base")).toEqual([[null]]);
  });

  it("without a base, compare button keeps compare-current behavior", async () => {
    const w = mountPanel();
    const rows = w.findAll(".document-history-panel__row");
    await rows[2].find('[data-testid="compare-btn"]').trigger("click");
    expect(w.emitted("compare-current")).toEqual([[SHA_B]]);
    expect(w.emitted("compare-range")).toBeUndefined();
  });

  it("working pseudo row has no base/compare-range controls", () => {
    const w = mountPanel(SHA_A);
    const working = w.findAll(".document-history-panel__row")[0];
    expect(working.find('[data-testid="range-base-btn"]').exists()).toBe(false);
    expect(working.find('[data-testid="compare-btn"]').exists()).toBe(false);
  });
});
