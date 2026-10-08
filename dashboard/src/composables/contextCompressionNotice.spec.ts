// Author: elecvoid243
// Date: 2026-10-08
//
// Leaf-module tests for the live-only context compression notice: parsing of
// the runner's wire payload, the duplicate-suppression identity, and the chip
// the chat page renders next to the token-usage ring.

import { describe, expect, it } from "vitest";

import {
  contextCompressionChip,
  contextCompressionHistoryLine,
  contextCompressionNoticeKey,
  formatTokenCount,
  parseContextCompressionNotice,
} from "./contextCompressionNotice";

const REPORT = {
  strategy: "llm_compress",
  tokens_before: 123456,
  tokens_after: 45123,
};

describe("parseContextCompressionNotice", () => {
  it("parses the JSON string carried by the SSE data field", () => {
    expect(parseContextCompressionNotice(JSON.stringify(REPORT))).toEqual({
      strategy: "llm_compress",
      tokensBefore: 123456,
      tokensAfter: 45123,
    });
  });

  it("parses an already-decoded object", () => {
    expect(parseContextCompressionNotice(REPORT)?.tokensAfter).toBe(45123);
  });

  it("rejects malformed payloads instead of showing a bogus notice", () => {
    expect(parseContextCompressionNotice("not-json")).toBeNull();
    expect(parseContextCompressionNotice(null)).toBeNull();
    expect(
      parseContextCompressionNotice({ strategy: "llm_compress" }),
    ).toBeNull();
  });
});

describe("formatTokenCount", () => {
  it("keeps small counts exact and compacts large ones", () => {
    expect(formatTokenCount(0)).toBe("0");
    expect(formatTokenCount(999)).toBe("999");
    expect(formatTokenCount(1000)).toBe("1k");
    expect(formatTokenCount(123456)).toBe("123.5k");
    expect(formatTokenCount(1_234_567)).toBe("1.2M");
  });
});

describe("contextCompressionNoticeKey", () => {
  it("identifies one notice by turn and token delta", () => {
    const notice = parseContextCompressionNotice(REPORT)!;

    expect(contextCompressionNoticeKey("m1", notice)).toBe(
      contextCompressionNoticeKey("m1", notice),
    );
    expect(contextCompressionNoticeKey("m1", notice)).not.toBe(
      contextCompressionNoticeKey("m2", notice),
    );
    expect(contextCompressionNoticeKey("m1", notice)).not.toBe(
      contextCompressionNoticeKey("m1", { ...notice, tokensAfter: 1 }),
    );
  });
});

describe("contextCompressionChip", () => {
  const translate = (key: string, params?: Record<string, string | number>) =>
    params ? `${key}|${params.from}|${params.to}` : key;

  it("labels the chip with the token delta and describes a summary neutrally", () => {
    const chip = contextCompressionChip(
      { strategy: "llm_compress", tokensBefore: 123456, tokensAfter: 45123 },
      translate,
    );

    expect(chip.label).toBe("123.5k → 45.1k");
    expect(chip.lossy).toBe(false);
    expect(chip.icon).toBe("mdi-arrow-collapse-vertical");
    expect(chip.description).toBe("contextCompression.llm|123.5k|45.1k");
  });

  it("gives truncation its own icon shape, so the loss never rests on colour alone", () => {
    for (const strategy of ["truncate_by_turns", "truncate_by_halving"]) {
      const chip = contextCompressionChip(
        { strategy, tokensBefore: 123456, tokensAfter: 45123 },
        translate,
      );

      expect(chip.lossy).toBe(true);
      expect(chip.icon).toBe("mdi-content-cut");
    }
  });

  it("treats a custom compressor as a non-lossy summary with generic wording", () => {
    const chip = contextCompressionChip(
      { strategy: "plugin_summarizer", tokensBefore: 1000, tokensAfter: 500 },
      translate,
    );

    expect(chip.lossy).toBe(false);
    expect(chip.icon).toBe("mdi-arrow-collapse-vertical");
    expect(chip.description).toBe("contextCompression.generic|1k|500");
  });

  it("names the halving fallback as its own event", () => {
    const chip = contextCompressionChip(
      { strategy: "truncate_by_halving", tokensBefore: 1000, tokensAfter: 500 },
      translate,
    );

    expect(chip.description).toBe("contextCompression.halving|1k|500");
  });
});

describe("contextCompressionHistoryLine", () => {
  const translate = (key: string, params?: Record<string, string | number>) =>
    key.startsWith("contextCompression.strategy.")
      ? `≈${key}`
      : `contextCompression.lastCompression|${params?.from}|${params?.to}|${params?.strategy}`;

  it("names the strategy so the ring stays self-explanatory after the chip fades", () => {
    expect(
      contextCompressionHistoryLine(
        { strategy: "llm_compress", tokensBefore: 123456, tokensAfter: 45123 },
        translate,
      ),
    ).toBe(
      "contextCompression.lastCompression|123.5k|45.1k|≈contextCompression.strategy.llm",
    );
  });

  it("falls back to the generic strategy word for custom compressors", () => {
    expect(
      contextCompressionHistoryLine(
        { strategy: "plugin_summarizer", tokensBefore: 1000, tokensAfter: 500 },
        translate,
      ),
    ).toBe(
      "contextCompression.lastCompression|1k|500|≈contextCompression.strategy.generic",
    );
  });
});
