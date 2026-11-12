import { describe, expect, it } from "vitest";
import {
  appendReasoningPart,
  extractReasoningText,
  type ChatRecord,
} from "@/composables/useMessages";

function record(): ChatRecord {
  return {
    id: "b1",
    content: { type: "bot", message: [], reasoning: "" },
  };
}

describe("appendReasoningPart", () => {
  it("appends a delta without re-extracting the whole reasoning", () => {
    const message = record();
    appendReasoningPart(message, "a");
    appendReasoningPart(message, "b");
    expect(message.content.reasoning).toBe("ab");
    expect(extractReasoningText(message.content.message)).toBe("ab");
  });

  it("keeps the cached reasoning in sync across a tool boundary", () => {
    const message = record();
    appendReasoningPart(message, "first");
    message.content.message.push({
      type: "tool_call",
      tool_calls: [{ id: "t1" }],
    });
    appendReasoningPart(message, "second");
    expect(message.content.reasoning).toBe("firstsecond");
    expect(extractReasoningText(message.content.message)).toBe("firstsecond");
  });

  it("falls back to extraction when the cached value is stale", () => {
    const message = record();
    message.content.message.push({ type: "think", think: "existing" });
    message.content.reasoning = "";
    appendReasoningPart(message, "!");
    expect(message.content.reasoning).toBe("existing!");
  });
});
