import { describe, expect, it } from "vitest";
import { parseFileReviewComments } from "@/utils/parseFileReviewComments";

// Line builders that mirror useFileComments.formatForLLM exactly, so the
// sample below is byte-for-byte the shape the backend stores in history.
//   renderWindow:    `  ${marker} ${pad(no)} │ ${content}`
//   renderHunkGroup: `  ${marker} ${pad(no)} │ ${prefix}${content}`
//   window comment:  9 spaces + `│ Comment: ` / `│ ` continuation
//   hunk comments:   `  - line N: text` / 4-space continuation
const pad = (n: number) => String(n).padStart(4);
const winLine = (no: number, content: string, anchor = false) =>
  `  ${anchor ? ">" : " "} ${pad(no)} │ ${content}`;
const hunkLine = (no: number, prefix: " " | "+" | "-", content: string, anchor = false) =>
  `  ${anchor ? ">" : " "} ${pad(no)} │ ${prefix}${content}`;
const commentLine = (text: string, first = false) =>
  `         │ ${first ? `Comment: ${text}` : text}`;

const SAMPLE = [
  "帮我看看这两处改动有没有问题",
  "",
  "[File review comments]",
  "Each entry shows the line content (and 3 lines of context above/below)",
  "that was current when the comment was written. Use the line content",
  "as a fingerprint to locate the line in the current file — line numbers",
  "may have drifted if the file was edited since the comment.",
  "",
  "`src/agent/runner.py` line 41:",
  "````",
  winLine(39, "def accumulate(values):"),
  winLine(40, "    total = 0"),
  winLine(41, "    for v in values:", true),
  commentLine("这里可以直接用 sum(values)，", true),
  commentLine("更简洁也更快"),
  winLine(42, "        total += v"),
  winLine(43, "    return total"),
  "````",
  "",
  "`src/utils/path.py` (in diff hunk @@ -10,6 +10,8 @@):",
  "````",
  "@@ -10,6 +10,8 @@",
  hunkLine(10, " ", "import os"),
  hunkLine(11, " ", "import sys"),
  hunkLine(12, "+", "import pathlib", true),
  hunkLine(13, "+", "import shutil"),
  hunkLine(14, " ", ""),
  "````",
  "Comments:",
  "  - line 12: pathlib 没有被用到，删掉这行",
  "  - line 13: shutil 是这次新加的吗？",
  "    看起来和本次改动无关",
].join("\n");

describe("parseFileReviewComments", () => {
  it("returns null when the marker is absent", () => {
    expect(parseFileReviewComments("just a plain message")).toBeNull();
    expect(parseFileReviewComments("")).toBeNull();
  });

  it("splits the user's free text from the review block", () => {
    const block = parseFileReviewComments(SAMPLE);
    expect(block).not.toBeNull();
    expect(block!.userText).toBe("帮我看看这两处改动有没有问题");
  });

  it("parses both a window and a hunk section", () => {
    const block = parseFileReviewComments(SAMPLE)!;
    expect(block.files).toHaveLength(2);
    expect(block.files[0].kind).toBe("window");
    expect(block.files[0].path).toBe("src/agent/runner.py");
    expect(block.files[1].kind).toBe("hunk");
    expect(block.files[1].path).toBe("src/utils/path.py");
    expect(block.files[1].hunkHeader).toBe("@@ -10,6 +10,8 @@");
  });

  it("parses a review block stored with CRLF line endings", () => {
    // Regression (2026-10-18): the message store on Windows keeps the
    // appended block as CRLF. The old split("\n") left "\r" on every line,
    // and the hunk code regexes dropped all content lines — the review card
    // rendered its header and comments but an empty diff.
    const block = parseFileReviewComments(SAMPLE.replace(/\n/g, "\r\n"));
    expect(block).not.toBeNull();
    expect(block!.files).toHaveLength(2);
    expect(block!.files[0].lines).toHaveLength(5);
    expect(block!.files[0].lines.find((l) => l.anchor)?.comment).toContain(
      "sum(values)",
    );
    expect(block!.files[1].lines).toHaveLength(6); // header + 5 rows
    expect(block!.files[1].lines.find((l) => l.no === 12)?.type).toBe("add");
    expect(block!.files[1].comments).toHaveLength(2);
    // LF and CRLF must produce the same model — the normalisation only
    // removes the line-ending \r, it never changes line structure.
    expect(block).toEqual(parseFileReviewComments(SAMPLE));
  });

  it("marks the anchored line and captures the inline window comment", () => {
    const win = parseFileReviewComments(SAMPLE)!.files[0];
    const anchor = win.lines.find((l) => l.anchor);
    expect(anchor?.no).toBe(41);
    expect(anchor?.comment).toBe("这里可以直接用 sum(values)，\n更简洁也更快");
    // The comment is also surfaced in the uniform `comments` list.
    expect(win.comments).toHaveLength(1);
    expect(win.comments[0].line).toBe(41);
  });

  it("classifies hunk line types and the hunk header line", () => {
    const hunk = parseFileReviewComments(SAMPLE)!.files[1];
    const header = hunk.lines.find((l) => l.hunkHeader);
    expect(header?.hunkHeader).toBe("@@ -10,6 +10,8 @@");
    const add = hunk.lines.find((l) => l.no === 12);
    expect(add?.type).toBe("add");
    expect(add?.anchor).toBe(true);
    expect(add?.text).toBe("import pathlib");
    const ctx = hunk.lines.find((l) => l.no === 10);
    expect(ctx?.type).toBe("ctx");
    expect(ctx?.text).toBe("import os");
  });

  it("parses the trailing hunk comment list with continuations", () => {
    const hunk = parseFileReviewComments(SAMPLE)!.files[1];
    expect(hunk.comments).toHaveLength(2);
    expect(hunk.comments[0]).toEqual({ line: 12, text: "pathlib 没有被用到，删掉这行" });
    expect(hunk.comments[1].line).toBe(13);
    expect(hunk.comments[1].text).toBe("shutil 是这次新加的吗？\n看起来和本次改动无关");
  });

  it("counts total comments across sections", () => {
    expect(parseFileReviewComments(SAMPLE)!.totalComments).toBe(3);
  });

  it("returns null when the marker is present but no sections parse", () => {
    const bare = "[File review comments]\nsome prose only";
    expect(parseFileReviewComments(bare)).toBeNull();
  });
});

// Regression (2026-10-18): formatForLLM pads line numbers to width 4, so a
// single-digit number is preceded by four spaces (1 separator + 3 padding).
// The old `{0,3}` regex silently dropped those lines, leaving the quoted
// diff empty for comments on lines 1-9.
describe("single-digit line numbers", () => {
  it("parses a hunk whose commented line is a single digit", () => {
    const block = [
      "[File review comments]",
      "`src/small.py` (in diff hunk @@ -1,2 +1,2 @@):",
      "````",
      "@@ -1,2 +1,2 @@",
      hunkLine(1, " ", "alpha"),
      hunkLine(2, "+", "ALPHA", true),
      "````",
      "Comments:",
      "  - line 2: single digit",
    ].join("\n");

    const parsed = parseFileReviewComments(block);
    expect(parsed).not.toBeNull();
    const hunk = parsed!.files[0];
    expect(hunk.lines.filter((l) => !l.hunkHeader)).toHaveLength(2);
    expect(hunk.lines.find((l) => l.no === 2)?.anchor).toBe(true);
    expect(hunk.comments).toEqual([{ line: 2, text: "single digit" }]);
  });

  it("parses a window whose commented line is a single digit", () => {
    const block = [
      "[File review comments]",
      "`src/small.py` line 3:",
      "````",
      winLine(2, "beta"),
      winLine(3, "gamma", true),
      commentLine("single digit window", true),
      "````",
    ].join("\n");

    const parsed = parseFileReviewComments(block);
    expect(parsed).not.toBeNull();
    const lines = parsed!.files[0].lines;
    expect(lines).toHaveLength(2);
    expect(lines[1].anchor).toBe(true);
    expect(lines[1].comment).toBe("single digit window");
  });
});
