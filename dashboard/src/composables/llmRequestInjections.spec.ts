// Author: elecvoid243
// Date: 2026-10-08
//
// Wire contract of the LLM request injection trace: the payload a plugin
// context injection produces on `on_llm_request`, parsed off the stream /
// history and formatted for the ChatMessageList gutter panel.

import { describe, expect, it } from "vitest";

import {
  injectionChangeLabel,
  parseLlmRequestInjections,
} from "./llmRequestInjections";

const tm = (key: string, params?: Record<string, string | number>) =>
  params ? `${key}|${params.delta}` : key;

const ITEM = {
  plugin: "tc_memory",
  handler: "decorate_llm_req",
  changes: [
    { field: "system_prompt", delta: 412, lossy: false, preview: "## 长期记忆" },
  ],
};

describe("parseLlmRequestInjections", () => {
  it("parses a JSON string payload", () => {
    expect(
      parseLlmRequestInjections(JSON.stringify({ items: [ITEM] }))?.items[0]
        .plugin,
    ).toBe("tc_memory");
  });

  it("accepts an already decoded object", () => {
    expect(parseLlmRequestInjections({ items: [ITEM] })?.items.length).toBe(1);
  });

  it("rejects malformed JSON and payloads without items", () => {
    expect(parseLlmRequestInjections("not-json")).toBeNull();
    expect(parseLlmRequestInjections({})).toBeNull();
    expect(parseLlmRequestInjections({ items: [] })).toBeNull();
    expect(parseLlmRequestInjections(null)).toBeNull();
  });
});

describe("injectionChangeLabel", () => {
  it("labels an append with sign and unit", () => {
    expect(injectionChangeLabel(ITEM.changes[0], tm)).toBe(
      "llmRequestInjections.units.chars|+412",
    );
  });

  it("labels a lossy shrink with the trimmed suffix", () => {
    expect(
      injectionChangeLabel(
        { field: "system_prompt", delta: -1200, lossy: true, preview: "" },
        tm,
      ),
    ).toBe(
      "llmRequestInjections.units.chars|-1.2k · llmRequestInjections.lossySuffix",
    );
  });

  it("labels a context append with the items unit", () => {
    expect(
      injectionChangeLabel(
        { field: "contexts", delta: 2, lossy: false, preview: "" },
        tm,
      ),
    ).toBe("llmRequestInjections.units.items|+2");
  });

  it("labels an equal-length rewrite", () => {
    expect(
      injectionChangeLabel(
        { field: "system_prompt", delta: 0, lossy: false, preview: "" },
        tm,
      ),
    ).toBe("llmRequestInjections.rewritten");
  });
});
