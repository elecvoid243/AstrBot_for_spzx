// Tests for splitAgentWork — the helper behind the collapsed "worked for
// ..." pill in the chat message lists. While the agent is working, all
// blocks render live; once the final reply exists, everything produced
// before it (thinking / tool-call blocks, intermediate outputs) collapses
// into an expandable group and only the final reply stays visible.
//
// Block model (see messageBlocks/isThinkingPart): `think` AND `tool_call`
// parts group into "thinking" blocks (the "thought N times, used M tools"
// tag); everything else forms "content" blocks. The final reply is the
// trailing content region after the last thinking block.
//
// Author: elecvoid243 | 2026-09-11

import { describe, it, expect } from "vitest";
import { splitAgentWork } from "@/composables/useMessages";
import type { ChatContent } from "@/composables/useMessages";

function content(message: unknown[]): ChatContent {
  return { type: "bot", message } as ChatContent;
}

const THINK = { type: "think", think: "pondering" };
const TOOL_CALL = {
  type: "tool_call",
  tool_calls: [
    {
      id: "t1",
      name: "astrbot_file_read_tool",
      args: { path: "a.py" },
      result: "ok",
      finished_ts: 2,
    },
  ],
};
const TEXT = (text: string) => ({ type: "plain", text });

describe("splitAgentWork", () => {
  it("returns null for a plain text reply without any work", () => {
    expect(splitAgentWork(content([TEXT("hello")]))).toBeNull();
  });

  it("returns null when the turn ended without a final reply", () => {
    expect(splitAgentWork(content([THINK, TOOL_CALL]))).toBeNull();
  });

  it("splits the thinking/tool-call block from the final reply", () => {
    const split = splitAgentWork(
      content([THINK, TOOL_CALL, THINK, TEXT("final answer")]),
    );
    expect(split).not.toBeNull();
    // think + tool_call merge into a single thinking block (the activity tag).
    expect(split!.workBlocks).toHaveLength(1);
    expect(split!.workBlocks[0].kind).toBe("thinking");
    expect(split!.finalBlocks).toHaveLength(1);
    expect(split!.finalBlocks[0].parts[0]).toEqual(TEXT("final answer"));
  });

  it("keeps intermediate outputs before the final reply in the work group", () => {
    const split = splitAgentWork(
      content([THINK, TEXT("intermediate"), TOOL_CALL, TEXT("done")]),
    );
    expect(split).not.toBeNull();
    const workTexts = split!.workBlocks.flatMap((block) =>
      block.parts.map((part) => part.text),
    );
    expect(workTexts).toContain("intermediate");
    const finalTexts = split!.finalBlocks.flatMap((block) =>
      block.parts.map((part) => part.text),
    );
    expect(finalTexts).toEqual(["done"]);
  });

  it("treats all text after the last thinking block as the final reply", () => {
    const split = splitAgentWork(
      content([TEXT("step one"), TOOL_CALL, TEXT("step two"), TEXT("final")]),
    );
    expect(split).not.toBeNull();
    // "step two" trails the last tool call with no thinking in between —
    // it is part of the final reply region and stays visible.
    const finalTexts = split!.finalBlocks.flatMap((block) =>
      block.parts.map((part) => part.text),
    );
    expect(finalTexts).toEqual(["step two", "final"]);
    expect(split!.workBlocks).toHaveLength(2);
  });

  it("returns null when an interactive choice sits in the work group", () => {
    const split = splitAgentWork(
      content([
        THINK,
        { type: "interactive_choice", request_id: "r1", options: [] },
        THINK,
        TEXT("final answer"),
      ]),
    );
    // Interactive choices require user input — they must never hide behind
    // the collapsed work group.
    expect(split).toBeNull();
  });

  it("keeps an interactive choice in the final region visible", () => {
    const split = splitAgentWork(
      content([
        THINK,
        { type: "interactive_choice", request_id: "r1", options: [] },
        TEXT("please pick one"),
      ]),
    );
    // The choice trails the last thinking block, so it lands in the final
    // region and stays rendered — the split is allowed.
    expect(split).not.toBeNull();
    const finalTypes = split!.finalBlocks.flatMap((block) =>
      block.parts.map((part) => part.type),
    );
    expect(finalTypes).toContain("interactive_choice");
  });
});
