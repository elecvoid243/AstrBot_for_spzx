// Author: elecvoid243 @ 2026-10-06
// Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-frontend-design.md §4.2
import { describe, it, expect } from "vitest";
import {
  alignOverlay,
  GAP_FOLD_THRESHOLD,
} from "@/utils/diffOverlayAlign";

function makePatch(oldStart: number, oldCount: number, newStart: number, newCount: number, body: string[]): string {
  return `@@ -${oldStart},${oldCount} +${newStart},${newCount} @@\n${body.join("\n")}`;
}

describe("alignOverlay", () => {
  it("single hunk: context/del/add kinds and baseLineno are correct", () => {
    const base = "l1\nl2\nl3\n";
    const patch = makePatch(1, 3, 1, 4, [" l1", "-l2", "+l2a", "+l2b", " l3"]);
    const r = alignOverlay(base, patch);
    expect(r.ok).toBe(true);
    expect(r.lines).toEqual([
      { kind: "context", baseLineno: 1, text: "l1" },
      { kind: "del", baseLineno: 2, text: "l2" },
      { kind: "add", baseLineno: null, text: "l2a" },
      { kind: "add", baseLineno: null, text: "l2b" },
      { kind: "context", baseLineno: 3, text: "l3" },
    ]);
    expect(r.gaps).toEqual([]);
  });

  it("two distant hunks produce a gap with hidden count", () => {
    const base = Array.from({ length: 30 }, (_, i) => `l${i + 1}`).join("\n");
    // hunk1 覆盖 l1..l3,hunk2 覆盖 l28..l30 → 间隙 l4..l27 = 24 行
    const patch = [
      makePatch(1, 3, 1, 3, [" l1", "-l2", "+L2", " l3"]),
      makePatch(28, 3, 28, 3, [" l28", "-l29", "+L29", " l30"]),
    ].join("\n");
    const r = alignOverlay(base, patch);
    expect(r.ok).toBe(true);
    expect(r.gaps).toEqual([{ afterBaseLineno: 3, hiddenCount: 24 }]);
    // 全部基准行仍在 lines 中(gap 只是折叠提示)
    expect(
      r.lines.filter((x) => x.kind !== "add").length,
    ).toBe(30);
  });

  it("hunks closer than threshold → no gap", () => {
    const base = Array.from({ length: 10 }, (_, i) => `l${i + 1}`).join("\n");
    const patch = [
      makePatch(1, 2, 1, 2, ["-l1", "+L1", " l2"]),
      makePatch(3, 2, 3, 2, ["-l3", "+L3", " l4"]),
    ].join("\n");
    const r = alignOverlay(base, patch);
    expect(r.ok).toBe(true);
    expect(r.gaps).toEqual([]);
    // l2(基准行2)与 l3 之间无折叠
    expect(r.lines.map((x) => x.text)).toContain("l2");
  });

  it("leading and trailing regions fold when large", () => {
    const base = Array.from({ length: 30 }, (_, i) => `l${i + 1}`).join("\n");
    // 仅中间 hunk:l13..l15 → 头部 l1..l12、尾部 l16..l30
    const patch = makePatch(13, 3, 13, 3, [" l13", "-l14", "+L14", " l15"]);
    const r = alignOverlay(base, patch);
    expect(r.ok).toBe(true);
    expect(r.gaps).toEqual([
      { afterBaseLineno: 0, hiddenCount: 12 },
      { afterBaseLineno: 15, hiddenCount: 15 },
    ]);
  });

  it("context text mismatch → ok=false (caller degrades to patch view)", () => {
    const base = "l1\nl2\nl3\n";
    const patch = makePatch(1, 3, 1, 3, [" l1", " WRONG", " l3"]);
    const r = alignOverlay(base, patch);
    expect(r.ok).toBe(false);
  });

  it("hunk claims lines beyond base length → ok=false", () => {
    const base = "l1\nl2\n";
    const patch = makePatch(1, 5, 1, 5, [" l1", " l2", " l3", " l4", " l5"]);
    const r = alignOverlay(base, patch);
    expect(r.ok).toBe(false);
  });

  it("empty patch (unchanged) → ok, all context, no gaps", () => {
    const r = alignOverlay("l1\nl2\n", "");
    expect(r.ok).toBe(true);
    expect(r.lines).toEqual([
      { kind: "context", baseLineno: 1, text: "l1" },
      { kind: "context", baseLineno: 2, text: "l2" },
    ]);
    expect(r.gaps).toEqual([]);
  });

  it("trailing newline does not produce a phantom empty line", () => {
    const base = "l1\nl2\n";
    const patch = makePatch(1, 2, 1, 2, [" l1", " l2"]);
    const r = alignOverlay(base, patch);
    expect(r.ok).toBe(true);
    expect(r.lines.length).toBe(2);
  });

  it("GAP_FOLD_THRESHOLD is 8", () => {
    expect(GAP_FOLD_THRESHOLD).toBe(8);
  });
});
