// Author: elecvoid243 @ 2026-10-06
// Parser tests for GET /spcode/git-file-diff (range-compare overlay view).
// Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-frontend-design.md
import { describe, expect, it } from "vitest";
import { parseSpcodeGitFileDiff } from "@/composables/parseSpcodeGitFileDiff";

function envelope(overrides: Record<string, unknown> = {}) {
  return {
    status: "ok",
    data: {
      success: true,
      reason: null,
      from: "a".repeat(40),
      to: "b".repeat(40),
      from_sha: "a".repeat(40),
      to_sha: "b".repeat(40),
      path: "main.py",
      status: "modified",
      old_path: null,
      is_binary: false,
      base_content: "line1\nline2\n",
      base_size: 12,
      base_truncated: false,
      patch: "@@ -1 +1 @@\n-line1\n+lineX\n",
      additions: 1,
      deletions: 1,
      truncated: false,
      ...overrides,
    },
  };
}

describe("parseSpcodeGitFileDiff", () => {
  it("maps modified envelope to camelCase data", () => {
    const d = parseSpcodeGitFileDiff(envelope());
    expect(d).not.toBeNull();
    expect(d!.fromSha).toBe("a".repeat(40));
    expect(d!.toSha).toBe("b".repeat(40));
    expect(d!.status).toBe("modified");
    expect(d!.oldPath).toBeNull();
    expect(d!.baseContent).toBe("line1\nline2\n");
    expect(d!.baseSize).toBe(12);
    expect(d!.baseTruncated).toBe(false);
    expect(d!.patch).toContain("@@");
    expect(d!.additions).toBe(1);
    expect(d!.deletions).toBe(1);
  });

  it("added: empty base content passes through", () => {
    const d = parseSpcodeGitFileDiff(
      envelope({ status: "added", base_content: "", base_size: 0, deletions: 0 }),
    );
    expect(d!.status).toBe("added");
    expect(d!.baseContent).toBe("");
  });

  it("renamed: old_path maps to oldPath", () => {
    const d = parseSpcodeGitFileDiff(
      envelope({ status: "renamed", old_path: "a.py", path: "b.py" }),
    );
    expect(d!.status).toBe("renamed");
    expect(d!.oldPath).toBe("a.py");
    expect(d!.path).toBe("b.py");
  });

  it("binary: null patch / null counts pass through", () => {
    const d = parseSpcodeGitFileDiff(
      envelope({
        is_binary: true,
        patch: null,
        base_content: "",
        additions: null,
        deletions: null,
      }),
    );
    expect(d!.isBinary).toBe(true);
    expect(d!.patch).toBeNull();
    expect(d!.additions).toBeNull();
    expect(d!.deletions).toBeNull();
  });

  it("returns null when success=false", () => {
    expect(
      parseSpcodeGitFileDiff(envelope({ success: false, reason: "ref_not_found" })),
    ).toBeNull();
  });

  it("returns null for non-object / missing data / missing from", () => {
    expect(parseSpcodeGitFileDiff(null)).toBeNull();
    expect(parseSpcodeGitFileDiff({ status: "ok" })).toBeNull();
    expect(
      parseSpcodeGitFileDiff({ status: "ok", data: { success: true } }),
    ).toBeNull();
  });

  it("returns null for unknown status value", () => {
    expect(parseSpcodeGitFileDiff(envelope({ status: "weird" }))).toBeNull();
  });
});
