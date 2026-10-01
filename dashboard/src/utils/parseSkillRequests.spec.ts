// Author: elecvoid243, 2026-10-01
// Sample blocks mirror the send-time "[Requested skills]" block in
// Chat.vue byte-for-byte, so the fixtures below are exactly the shape
// the backend stores in history.
import { describe, expect, it } from "vitest";
import { parseSkillRequests } from "@/utils/parseSkillRequests";

const BLOCK = [
  "[Requested skills]",
  "The user explicitly queued the following skill(s) for this request.",
  "- `brainstorming`",
  "- `pdf`",
].join("\n");

describe("parseSkillRequests", () => {
  it("returns null when the marker is absent", () => {
    expect(parseSkillRequests("just a normal message")).toBeNull();
  });

  it("returns null for empty input", () => {
    expect(parseSkillRequests("")).toBeNull();
  });

  it("returns null when the block has no entries", () => {
    expect(parseSkillRequests("[Requested skills]\nsome prose")).toBeNull();
  });

  it("parses a block-only message", () => {
    const r = parseSkillRequests(BLOCK);
    expect(r).not.toBeNull();
    expect(r!.userText).toBe("");
    expect(r!.skills).toEqual(["brainstorming", "pdf"]);
  });

  it("splits user text from the block", () => {
    const r = parseSkillRequests(`please use these\n\n${BLOCK}`);
    expect(r!.userText).toBe("please use these");
    expect(r!.skills).toHaveLength(2);
  });

  it("parses skills when comments and references blocks precede them", () => {
    // Combined message layout mirrors the send-time concat in Chat.vue:
    // [userText][comments block][references block][skills block]. The
    // skills marker sits LAST, so a parser that only looked at another
    // parser's userText would miss it — this locks in that
    // parseSkillRequests scans the FULL text.
    const commentsBlock = [
      "[File review comments]",
      "`F:\\a\\main.py` line 25:",
      "````",
      "  >   25 │     x = 1",
      "         │ Comment: hello",
      "````",
    ].join("\n");
    const referencesBlock = [
      "[Referenced files]",
      "The user referenced the following file(s) by absolute path.",
      "- `/home/user/notes.md`",
    ].join("\n");
    const r = parseSkillRequests(
      `do the thing\n\n${commentsBlock}\n\n${referencesBlock}\n\n${BLOCK}`,
    );
    expect(r).not.toBeNull();
    expect(r!.skills).toEqual(["brainstorming", "pdf"]);
  });
});
