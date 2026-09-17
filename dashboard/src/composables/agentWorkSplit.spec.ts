// Tests for splitAgentWork — the helper behind the collapsed "worked for
// ..." pill in the chat message lists. While the agent is working, all
// blocks render live; once the final reply exists, everything else
// (thinking / tool-call blocks, intermediate outputs, resolved interactive
// choices — including work that trails the reply) collapses into an
// expandable group and only the final reply stays visible.
//
// Block model (see messageBlocks/isThinkingPart): `think` AND `tool_call`
// parts group into "thinking" blocks (the "thought N times, used M tools"
// tag); everything else forms "content" blocks. The final reply is the
// trailing content region after the last thinking block, cut down to its
// own reply parts.
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

  it("collapses a resolved interactive choice into the work group", () => {
    const split = splitAgentWork(
      content([
        THINK,
        { type: "interactive_choice", request_id: "r1", options: [] },
        THINK,
        TEXT("final answer"),
      ]),
    );
    // A choice inside the work group is resolved history (a pending choice
    // always trails the message) — it collapses like any other work and the
    // expanded pill renders it via the normal choice-box path.
    expect(split).not.toBeNull();
    const workTypes = split!.workBlocks.flatMap((block) =>
      block.parts.map((part) => part.type),
    );
    expect(workTypes).toContain("interactive_choice");
    expect(split!.finalBlocks.flatMap((block) => block.parts)).toEqual([
      TEXT("final answer"),
    ]);
  });

  it("folds an interactive choice that trails the reply text", () => {
    const split = splitAgentWork(
      content([
        THINK,
        { type: "interactive_choice", request_id: "r1", options: [] },
        TEXT("please pick one"),
      ]),
    );
    // The box sits in the reply block: the agent asked after writing, so the
    // text trails the choice and the box is resolved history once the pill
    // shows (a pending choice keeps the message live, pill included).
    expect(split).not.toBeNull();
    const finalTypes = split!.finalBlocks.flatMap((block) =>
      block.parts.map((part) => part.type),
    );
    expect(finalTypes).toEqual(["plain"]);
    expect(split!.finalBlocks[0].parts).toEqual([TEXT("please pick one")]);
  });

  it("keeps only the reply text when a choice follows it", () => {
    const split = splitAgentWork(
      content([
        THINK,
        TEXT("plan is written"),
        { type: "interactive_choice", request_id: "r1", options: [] },
      ]),
    );
    // Answering the box continued the run, so both the box and what came
    // before it are work; the message opens on the text the box followed.
    expect(split).not.toBeNull();
    expect(split!.finalBlocks.flatMap((block) => block.parts)).toEqual([
      TEXT("plan is written"),
    ]);
  });

  it("prefers text streamed after the last choice", () => {
    const split = splitAgentWork(
      content([
        THINK,
        TEXT("asking now"),
        { type: "interactive_choice", request_id: "r1", options: [] },
        TEXT("done with A"),
      ]),
    );
    expect(split).not.toBeNull();
    expect(split!.finalBlocks.flatMap((block) => block.parts)).toEqual([
      TEXT("done with A"),
    ]);
  });

  it("collapses a tool call that trails the reply instead of leaking it", () => {
    // Shape a paused turn saves: the agent called ask_user_choice together
    // with a sibling tool, the choice event flushed the record, and the
    // sibling call was still waiting for its result (empty card below the
    // box, previously rendered outside the capsule).
    const pendingSibling = {
      type: "tool_call",
      tool_calls: [{ id: "call-read", name: "astrbot_read_tool", args: {} }],
    };
    const split = splitAgentWork(
      content([
        THINK,
        TEXT("plan is written"),
        { type: "interactive_choice", request_id: "r1", options: [] },
        pendingSibling,
      ]),
    );
    expect(split).not.toBeNull();
    expect(split!.finalBlocks.flatMap((block) => block.parts)).toEqual([
      TEXT("plan is written"),
    ]);
    expect(
      split!.workBlocks.flatMap((block) => block.parts.map((part) => part.type)),
    ).toContain("tool_call");
  });
});
