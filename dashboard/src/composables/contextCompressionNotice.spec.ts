// Author: elecvoid243
// Date: 2026-10-08
//
// Leaf-module tests for the live-only context compression notice: parsing of
// the runner's wire payload, the duplicate-suppression identity, and the
// i18n message the chat page shows as a transient toast.

import { describe, expect, it } from "vitest";

import {
  contextCompressionNoticeKey,
  contextCompressionToast,
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

describe("contextCompressionToast", () => {
  const translate = (key: string, params?: Record<string, string | number>) =>
    params ? `${key}|${params.from}|${params.to}` : key;

  it("names the strategy that produced the truncation", () => {
    const toast = contextCompressionToast(
      { strategy: "truncate_by_turns", tokensBefore: 123456, tokensAfter: 45123 },
      translate,
    );

    expect(toast.message).toBe("contextCompression.truncate|123.5k|45.1k");
    expect(toast.timeout).toBe(4000);
  });

  it("falls back to the generic wording for custom strategies", () => {
    const toast = contextCompressionToast(
      { strategy: "plugin_summarizer", tokensBefore: 1000, tokensAfter: 500 },
      translate,
    );

    expect(toast.message).toBe("contextCompression.generic|1k|500");
  });

  it("distinguishes the halving fallback from the configured strategy", () => {
    const toast = contextCompressionToast(
      { strategy: "truncate_by_halving", tokensBefore: 1000, tokensAfter: 500 },
      translate,
    );

    expect(toast.message).toBe("contextCompression.halving|1k|500");
  });
});
