// Author: elecvoid243 @ 2026-09-12
// Parser tests for POST /spcode/git-tag-create (commit-dialog tag flow).
import { describe, expect, it } from "vitest";
import { parseSpcodeGitTagCreate } from "@/composables/parseSpcodeGitWorkflow";

function envelope(overrides: Record<string, unknown> = {}) {
  return {
    status: "ok",
    data: {
      created: true,
      tag: "v1.0.0",
      rev: "HEAD",
      sha: "c".repeat(40),
      umo: "u",
      worktree: "w",
      directory: "d",
      reason: null,
      stderr: "",
      elapsed_ms: 3,
      ...overrides,
    },
  };
}

describe("parseSpcodeGitTagCreate", () => {
  it("maps snake_case fields to the snapshot", () => {
    const parsed = parseSpcodeGitTagCreate(envelope());
    expect(parsed.kind).toBe("ok");
    if (parsed.kind !== "ok") return;
    expect(parsed.snapshot.success).toBe(true);
    expect(parsed.snapshot.created).toBe(true);
    expect(parsed.snapshot.tag).toBe("v1.0.0");
    expect(parsed.snapshot.rev).toBe("HEAD");
    expect(parsed.snapshot.sha).toBe("c".repeat(40));
    expect(parsed.snapshot.elapsedMs).toBe(3);
  });

  it("passes failure reason + stderr through with safe fallbacks", () => {
    const parsed = parseSpcodeGitTagCreate(
      envelope({
        created: false,
        reason: "tag_already_exists",
        stderr: "fatal: tag 'v1.0.0' already exists",
        sha: null,
        rev: null,
      }),
    );
    expect(parsed.kind).toBe("ok");
    if (parsed.kind !== "ok") return;
    expect(parsed.snapshot.success).toBe(false);
    expect(parsed.snapshot.created).toBe(false);
    expect(parsed.snapshot.reason).toBe("tag_already_exists");
    expect(parsed.snapshot.stderr).toContain("already exists");
    expect(parsed.snapshot.sha).toBe("");
    expect(parsed.snapshot.rev).toBe("");
  });
});
