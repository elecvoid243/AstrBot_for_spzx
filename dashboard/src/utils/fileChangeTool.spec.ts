// Tests for dashboard/src/utils/fileChangeTool.ts
// Feature: surface file edit/write tool calls on the ReasoningBlock
// collapsed bar (chips) and pin FileChangeCards on top of the
// ReasoningTimeline. Spec: 2026-08-11 design (approved in chat).
//
// Author: elecvoid243 | 2026-08-11

import { describe, expect, it } from "vitest";
import {
  collectFileChanges,
  computeDiffStat,
  fileBasename,
  fileChangeTone,
  isFileChangeToolName,
  parseFileChangesPayload,
  parseFileEditResult,
  stripWriteEncodingMarker,
  type FileChangeEntry,
} from "@/utils/fileChangeTool";

const EDIT_RESULT_OK = [
  "Edited F:\\proj\\a.py. Replaced 1 occurrence(s) of the target text.",
  "",
  "Diff:",
  "```diff",
  "@@ -1,3 +1,4 @@",
  " line1",
  "-old line",
  "+new line",
  "+another line",
  "```",
].join("\n");

// Regression (2026-08-28): editing a .md file puts code-fence lines
// (e.g. `+```python`) inside the diff body. The old bare ``` extraction
// regex stopped at the first embedded fence and truncated everything
// after it.
const EDIT_RESULT_MD = [
  "Edited F:\\proj\\README.md. Replaced 1 occurrence(s) of the target text.",
  "",
  "Diff:",
  "```diff",
  "--- F:\\proj\\README.md",
  "+++ F:\\proj\\README.md",
  "@@ -2,8 +2,9 @@",
  " Intro paragraph.",
  "-Example:",
  "+Example with python:",
  "+",
  " ```python",
  ' print("hello")',
  " ```",
  " ",
  " Footer text.",
  "```",
].join("\n");

const WRITE_TOOL = {
  id: "call-write-1",
  name: "astrbot_file_write_tool",
  args: { path: "F:\\proj\\b.txt", content: "a\nb\nc" },
  result: "File written successfully: F:\\proj\\b.txt",
  finished_ts: 2,
};

describe("isFileChangeToolName", () => {
  it("matches the file edit tool", () => {
    expect(isFileChangeToolName("astrbot_file_edit_tool")).toBe(true);
  });

  it("matches the file write tool", () => {
    expect(isFileChangeToolName("astrbot_file_write_tool")).toBe(true);
  });

  it("rejects unrelated tools", () => {
    expect(isFileChangeToolName("astrbot_execute_shell")).toBe(false);
    expect(isFileChangeToolName("web_search")).toBe(false);
    expect(isFileChangeToolName("")).toBe(false);
  });
});

describe("parseFileEditResult", () => {
  it("extracts path, diff and summary from a success result", () => {
    const parsed = parseFileEditResult(EDIT_RESULT_OK);
    expect(parsed.filePath).toBe("F:\\proj\\a.py");
    expect(parsed.diff).toContain("-old line");
    expect(parsed.diff).toContain("+another line");
    expect(parsed.diff).not.toContain("```");
    expect(parsed.summary).toContain("Replaced 1 occurrence(s)");
    expect(parsed.notice).toBeNull();
  });

  it("keeps the full diff for a .md file whose body contains code fences", () => {
    const parsed = parseFileEditResult(EDIT_RESULT_MD);
    expect(parsed.filePath).toBe("F:\\proj\\README.md");
    // Embedded fences (prefixed with +/-/space) must not truncate the
    // extraction — the trailing lines after the first fence survive.
    expect(parsed.diff).toContain("```python");
    expect(parsed.diff).toContain('print("hello")');
    expect(parsed.diff).toContain("Footer text.");
    // The real closing fence is consumed; no fence leaks into the diff.
    expect(parsed.diff.endsWith("Footer text.\n")).toBe(true);
    expect(parsed.summary).toContain("Replaced 1 occurrence(s)");
  });

  it("counts the full +/- stat for a fenced .md diff", () => {
    const changes = collectFileChanges([
      {
        type: "tool_call",
        tool_calls: [
          {
            id: "md1",
            name: "astrbot_file_edit_tool",
            args: { path: "F:\\proj\\README.md" },
            result: EDIT_RESULT_MD,
            finished_ts: 2,
          },
        ],
      },
    ]);
    // adds: +Example with python:, +; dels: -Example:
    expect(changes[0].diffStat).toEqual({ adds: 2, dels: 1 });
  });

  it("splits a [SYSTEM NOTICE] suffix into notice", () => {
    const raw =
      EDIT_RESULT_OK + "\n[SYSTEM NOTICE] Important: output truncated";
    const parsed = parseFileEditResult(raw);
    expect(parsed.notice).toBe("[SYSTEM NOTICE] Important: output truncated");
    expect(parsed.summary).not.toContain("SYSTEM NOTICE");
  });

  it("extracts the path from an error result with bracketed path", () => {
    const parsed = parseFileEditResult(
      "Error editing file: [D:\\work\\test.py]: old string not found",
    );
    expect(parsed.filePath).toBe("D:\\work\\test.py");
  });
});

describe("computeDiffStat", () => {
  it("counts +/- lines and excludes +++/--- headers", () => {
    const stat = computeDiffStat(
      ["--- a/x.py", "+++ b/x.py", "@@ -1 +1 @@", "-a", "+b", "+c", " ctx"].join(
        "\n",
      ),
    );
    expect(stat).toEqual({ adds: 2, dels: 1 });
  });

  it("returns zero counts for an empty diff", () => {
    expect(computeDiffStat("")).toEqual({ adds: 0, dels: 0 });
  });
});

describe("fileBasename", () => {
  it("returns the last segment for Windows and POSIX paths", () => {
    expect(fileBasename("F:\\proj\\src\\a.py")).toBe("a.py");
    expect(fileBasename("/home/user/b.txt")).toBe("b.txt");
  });

  it("returns the input for bare names and empty strings", () => {
    expect(fileBasename("a.py")).toBe("a.py");
    expect(fileBasename("")).toBe("");
  });
});

// 2026-09-09: the write tool appends " (encoding: xxx)" to its success
// result; the marker is display-only and must be stripped before any
// path is handed to an API (open on disk / open folder / language detect).
describe("stripWriteEncodingMarker", () => {
  it("strips the trailing encoding marker", () => {
    expect(
      stripWriteEncodingMarker("F:\\proj\\notes.md (encoding: utf-8)"),
    ).toBe("F:\\proj\\notes.md");
    expect(stripWriteEncodingMarker("F:\\proj\\a.txt (encoding: cp936)")).toBe(
      "F:\\proj\\a.txt",
    );
  });

  it("strips the alias encodings the backend normalizes to", () => {
    expect(stripWriteEncodingMarker("C:/p/a.txt (encoding: utf-8-sig)")).toBe(
      "C:/p/a.txt",
    );
    expect(stripWriteEncodingMarker("C:/p/a.txt (encoding: ansi)")).toBe(
      "C:/p/a.txt",
    );
  });

  it("leaves a clean path untouched", () => {
    expect(stripWriteEncodingMarker("F:\\proj\\b.txt")).toBe("F:\\proj\\b.txt");
    expect(stripWriteEncodingMarker("")).toBe("");
  });

  it("only strips a trailing marker, not an inner one", () => {
    expect(
      stripWriteEncodingMarker("F:\\proj\\a (encoding: x)\\b.txt"),
    ).toBe("F:\\proj\\a (encoding: x)\\b.txt");
  });
});

describe("fileChangeTone", () => {
  const base: FileChangeEntry = {
    callId: "x",
    kind: "edit",
    filePath: "a.py",
    status: "done",
    diffStat: null,
    lineCount: null,
    tool: {},
  };

  it("maps write → green (new/whole-file write)", () => {
    expect(fileChangeTone({ ...base, kind: "write" })).toBe("green");
  });

  it("maps edit → yellow (modification)", () => {
    expect(fileChangeTone({ ...base, kind: "edit" })).toBe("yellow");
  });

  it("maps remove → red (deletion)", () => {
    expect(fileChangeTone({ ...base, kind: "remove" })).toBe("red");
  });
});

describe("collectFileChanges", () => {
  it("collects a finished edit call with path and diff stat", () => {
    const parts = [
      {
        type: "tool_call",
        tool_calls: [
          {
            id: "c1",
            name: "astrbot_file_edit_tool",
            args: { path: "F:\\proj\\a.py" },
            result: EDIT_RESULT_OK,
            finished_ts: 2,
          },
        ],
      },
    ];
    const changes = collectFileChanges(parts);
    expect(changes).toHaveLength(1);
    expect(changes[0]).toMatchObject({
      callId: "c1",
      kind: "edit",
      filePath: "F:\\proj\\a.py",
      status: "done",
      diffStat: { adds: 2, dels: 1 },
    });
  });

  it("collects a write call with path from args and line count from content", () => {
    const changes = collectFileChanges([
      { type: "tool_call", tool_calls: [WRITE_TOOL] },
    ]);
    expect(changes).toHaveLength(1);
    expect(changes[0]).toMatchObject({
      callId: "call-write-1",
      kind: "write",
      filePath: "F:\\proj\\b.txt",
      status: "done",
      lineCount: 3,
    });
  });

  // 2026-08-21: the write result carries the backend-resolved absolute
  // path, which wins over a (possibly relative) args.path.
  it("prefers the result path over a relative args path for writes", () => {
    const relative = {
      ...WRITE_TOOL,
      args: { path: "notes\\b.txt", content: "x" },
      result: "File written successfully: F:\\workspaces\\default\\notes\\b.txt",
    };
    const changes = collectFileChanges([
      { type: "tool_call", tool_calls: [relative] },
    ]);
    expect(changes[0].filePath).toBe(
      "F:\\workspaces\\default\\notes\\b.txt",
    );
  });

  it("falls back to the args path while a write call is still running", () => {
    const running = { ...WRITE_TOOL, result: undefined, finished_ts: undefined };
    const changes = collectFileChanges([
      { type: "tool_call", tool_calls: [running] },
    ]);
    expect(changes[0].filePath).toBe("F:\\proj\\b.txt");
  });

  it("marks calls without finished_ts as running", () => {
    const running = { ...WRITE_TOOL, finished_ts: undefined, result: undefined };
    const changes = collectFileChanges([
      { type: "tool_call", tool_calls: [running] },
    ]);
    expect(changes[0].status).toBe("running");
  });

  it("marks results starting with Error as error status", () => {
    const failed = {
      ...WRITE_TOOL,
      result: "Error writing file: permission denied",
    };
    const changes = collectFileChanges([
      { type: "tool_call", tool_calls: [failed] },
    ]);
    expect(changes[0].status).toBe("error");
  });

  it("parses stringified args (pre-normalization wire shape)", () => {
    const stringArgs = {
      ...WRITE_TOOL,
      args: JSON.stringify({ path: "F:\\proj\\b.txt", content: "x" }),
    };
    const changes = collectFileChanges([
      { type: "tool_call", tool_calls: [stringArgs] },
    ]);
    expect(changes[0].filePath).toBe("F:\\proj\\b.txt");
  });

  it("skips non-file tools and keeps original order", () => {
    const parts = [
      { type: "think", think: "hmm" },
      {
        type: "tool_call",
        tool_calls: [
          { id: "s1", name: "astrbot_execute_shell", finished_ts: 1 },
          WRITE_TOOL,
        ],
      },
    ];
    const changes = collectFileChanges(parts);
    expect(changes).toHaveLength(1);
    expect(changes[0].callId).toBe("call-write-1");
  });

  it("synthesizes a stable callId fallback when id is missing", () => {
    const noId = { ...WRITE_TOOL, id: undefined };
    const changes = collectFileChanges([
      { type: "tool_call", tool_calls: [noId] },
    ]);
    expect(changes[0].callId).toBeTruthy();
  });

  it("collects a remove call with the path from args", () => {
    const removeTool = {
      id: "r1",
      name: "astrbot_file_remove_tool",
      args: { path: "F:\\proj\\old.txt" },
      result: "File removed: F:\\proj\\old.txt",
      finished_ts: 2,
    };
    const changes = collectFileChanges([
      { type: "tool_call", tool_calls: [removeTool] },
    ]);
    expect(changes).toHaveLength(1);
    expect(changes[0]).toMatchObject({
      callId: "r1",
      kind: "remove",
      filePath: "F:\\proj\\old.txt",
      status: "done",
      diffStat: null,
      lineCount: null,
    });
  });

  it("also recognizes the bare astrbot_file_remove name", () => {
    const removeTool = {
      id: "r2",
      name: "astrbot_file_remove",
      args: { path: "/tmp/x.txt" },
      result: "ok",
      finished_ts: 1,
    };
    const changes = collectFileChanges([
      { type: "tool_call", tool_calls: [removeTool] },
    ]);
    expect(changes[0].kind).toBe("remove");
  });

  it("marks a failed remove as error status", () => {
    const failed = {
      id: "r3",
      name: "astrbot_file_remove_tool",
      args: { path: "/tmp/x.txt" },
      result: "Error removing file: not found",
      finished_ts: 1,
    };
    const changes = collectFileChanges([
      { type: "tool_call", tool_calls: [failed] },
    ]);
    expect(changes[0].status).toBe("error");
  });
});

describe("parseFileChangesPayload", () => {
  it("parses object payloads and returns files", () => {
    const files = parseFileChangesPayload({
      files: [{ path: "/x/a.py", kind: "edit", adds: 3, dels: 1 }],
    });
    expect(files).toHaveLength(1);
    expect(files[0].path).toBe("/x/a.py");
  });

  it("parses JSON string payloads (raw chain_type form)", () => {
    const files = parseFileChangesPayload(
      JSON.stringify({ files: [{ path: "/x/b.py", kind: "created" }] }),
    );
    expect(files[0].kind).toBe("created");
  });

  it("drops entries without a path and non-list payloads", () => {
    expect(parseFileChangesPayload({ files: [{ kind: "edit" }] })).toEqual([]);
    expect(parseFileChangesPayload("garbage")).toEqual([]);
    expect(parseFileChangesPayload(null)).toEqual([]);
  });
});
