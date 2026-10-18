// Author: elecvoid243
// Date: 2026-10-06
// Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-frontend-design.md §4.2
//
// 把 unified diff 的 hunk 序列对齐到基准文件全文,产出「全文件 + diff 叠加」
// 渲染所需的行模型。纯函数,无 Vue 依赖。

import { parseUnifiedDiff } from "@/utils/diffHunkPatch";

export interface OverlayLine {
  kind: "context" | "del" | "add";
  /** context/del 为基准行号(1-based);add 为 null */
  baseLineno: number | null;
  /**
   * 变更后文件的 new-side 行号(1-based); context/add 有值, del 为 null。
   * 叠加视图的评论锚点: context/add 用 new 侧, del 用 old 侧。
   */
  newLineno: number | null;
  text: string;
}

export interface OverlayGap {
  /** 折叠起点:基准行号 N 之后(0 = 文件开头之前) */
  afterBaseLineno: number;
  hiddenCount: number;
}

export interface OverlayResult {
  /** false = 对齐失败(hunk 与基准行不匹配),调用方降级为纯 patch 渲染 */
  ok: boolean;
  lines: OverlayLine[];
  gaps: OverlayGap[];
}

/** 相邻 hunk 基准侧间距超过该值才折叠(行) */
export const GAP_FOLD_THRESHOLD = 8;

const HUNK_HEADER_RE = /^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@/;

export function alignOverlay(baseContent: string, patch: string): OverlayResult {
  // 尾部换行产生的伪空行不属于文件行序列
  const baseLines = baseContent.split("\n");
  if (baseLines.length > 0 && baseLines[baseLines.length - 1] === "") {
    baseLines.pop();
  }

  if (!patch.trim()) {
    return {
      ok: true,
      lines: baseLines.map((text, i) => ({
        kind: "context" as const,
        baseLineno: i + 1,
        newLineno: i + 1,
        text,
      })),
      gaps: [],
    };
  }

  const hunks = parseUnifiedDiff(patch, Infinity);
  if (hunks.length === 0) return { ok: false, lines: [], gaps: [] };

  // ── 1. 校验 + 归一化每个 hunk 的覆盖范围 ──
  interface HunkSeg {
    start: number; // 首个 ctx/del 基准行号
    end: number; // 末个 ctx/del 基准行号
    lines: OverlayLine[];
    /** 该 hunk 结束后,基准行号 → new 行号的累计偏移 */
    newOffsetAfter: number;
  }
  const segs: HunkSeg[] = [];
  for (const h of hunks) {
    const m = h.header.match(HUNK_HEADER_RE);
    if (!m) return { ok: false, lines: [], gaps: [] };
    const oldStart = parseInt(m[1], 10);
    const oldCount = m[2] ? parseInt(m[2], 10) : 1;
    const newStart = parseInt(m[3], 10);
    const newCount = m[4] ? parseInt(m[4], 10) : 1;
    let cursor = oldStart; // 基准侧起点
    const segLines: OverlayLine[] = [];
    let first = -1;
    let last = -1;
    for (const l of h.lines) {
      if (l.type === "add") {
        segLines.push({
          kind: "add",
          baseLineno: null,
          newLineno: l.newNo ? Number(l.newNo) : null,
          text: l.content,
        });
        continue;
      }
      // ctx / del 都消费一个基准行
      if (l.type === "header-file") continue;
      const text = l.content;
      // marker 必须先于越界检查吞掉:EOF 无换行时 cursor 已越过
      // baseLines.length,先查越界会把合法输入误判为对齐失败
      if (text === "\\ No newline at end of file") continue; // 不占基准行
      if (cursor > baseLines.length) return { ok: false, lines: [], gaps: [] };
      if (baseLines[cursor - 1] !== text) {
        return { ok: false, lines: [], gaps: [] };
      }
      segLines.push({
        kind: l.type === "del" ? "del" : "context",
        baseLineno: cursor,
        newLineno: l.type === "del" || !l.newNo ? null : Number(l.newNo),
        text,
      });
      if (first < 0) first = cursor;
      last = cursor;
      cursor++;
    }
    if (first < 0) return { ok: false, lines: [], gaps: [] }; // 纯 add 空 hunk,防御
    segs.push({
      start: first,
      end: last,
      lines: segLines,
      newOffsetAfter: newStart + newCount - (oldStart + oldCount),
    });
  }

  // ── 2. 组装:头间隙 + hunk 序列 + 尾间隙 ──
  const lines: OverlayLine[] = [];
  const gaps: OverlayGap[] = [];
  let covered = 0; // 已输出的最后一个基准行号

  function pushGap(after: number, hidden: number): void {
    if (hidden > GAP_FOLD_THRESHOLD) {
      gaps.push({ afterBaseLineno: after, hiddenCount: hidden });
    }
  }
  function pushContextRange(
    fromLine: number,
    toLine: number,
    newOffset: number,
  ): void {
    for (let i = fromLine; i <= toLine; i++) {
      lines.push({
        kind: "context",
        baseLineno: i,
        newLineno: i + newOffset,
        text: baseLines[i - 1],
      });
    }
  }

  let newOffset = 0;
  for (const seg of segs) {
    if (seg.start <= covered) return { ok: false, lines: [], gaps: [] }; // 重叠/乱序
    const gapCount = seg.start - covered - 1;
    pushGap(covered, gapCount);
    pushContextRange(covered + 1, seg.start - 1, newOffset);
    lines.push(...seg.lines);
    covered = seg.end;
    newOffset = seg.newOffsetAfter;
  }
  const tailCount = baseLines.length - covered;
  pushGap(covered, tailCount);
  pushContextRange(covered + 1, baseLines.length, newOffset);

  return { ok: true, lines, gaps };
}
