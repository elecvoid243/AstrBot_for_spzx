// Author: elecvoid243, 2026-08-13
// Regression test for the combined comments+references rendering bug.
//
// Message layout on send is [userText][comments block][references block]
// (Chat.vue concat). parseFileReviewComments only returns the text
// BEFORE the comments marker, which can never contain the trailing
// references block — so the references parser must receive the FULL
// text. Source-contract style (same as ChatTodoSummaryBar.spec.ts; no
// component-mount harness exists for this file).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

function readSiblingSource(filename: string): string {
  return readFileSync(fileURLToPath(new URL(filename, import.meta.url)), "utf8");
}

describe("UserPlainMessagePart combined comments + references", () => {
  it("parses references from the full message text, not the comments userText", () => {
    const source = readSiblingSource("./UserPlainMessagePart.vue");
    expect(source).toContain("parseFileReferences(props.text)");
    // The broken wiring fed review.userText (text BEFORE the comments
    // marker) into the references parser — that can never contain the
    // trailing references block, so the card never rendered.
    expect(source).not.toContain(
      "parseFileReferences(review.value ? review.value.userText : props.text)",
    );
  });

  it("prefers the comments userText for display when both blocks exist", () => {
    const source = readSiblingSource("./UserPlainMessagePart.vue");
    // Without this, the references parser's userText (which still
    // contains the raw comments block) would be re-rendered as plain
    // text above the comments card.
    expect(source).toContain("if (review.value) return review.value.userText;");
  });

  // 2026-10-01 skill-request block: the "[Requested skills]" marker sits
  // LAST in the send-time concat, so the parser must scan the full text
  // and the display prefix must fall through comments → references →
  // skills (the earliest present block holds the clean userText).
  it("parses skill requests from the full message text", () => {
    const source = readSiblingSource("./UserPlainMessagePart.vue");
    expect(source).toContain("parseSkillRequests(props.text)");
  });

  it("falls back to the references/skills userText only when no earlier block exists", () => {
    const source = readSiblingSource("./UserPlainMessagePart.vue");
    expect(source).toContain(
      "if (references.value) return references.value.userText;",
    );
    expect(source).toContain("return skillRequest.value?.userText ?? \"\";");
  });
});
