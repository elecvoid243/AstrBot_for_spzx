import { describe, expect, it } from "vitest";
import { parseSpcodeGitLog } from "@/composables/parseSpcodeGitWorkflow";

function envelope(overrides: Record<string, unknown> = {}) {
  return {
    status: "ok",
    data: {
      loaded: true,
      umo: "u",
      worktree: "w",
      directory: "d",
      ref: "HEAD",
      count: 1,
      has_more: false,
      truncated: false,
      max_bytes: 1024,
      resolved_ref: null,
      commits: [
        {
          sha: "a".repeat(40),
          sha_short: "aaaaaaa",
          author: { name: "n", email: "e" },
          committer: { name: "n", email: "e" },
          date: "2026-09-08T00:00:00+08:00",
          subject: "s",
          body: null,
          parents: [],
          shortstat: { files: 1, additions: 1, deletions: 0 },
          tags: ["v1.0.0", "latest"],
        },
      ],
      ...overrides,
    },
  };
}

describe("parseSpcodeGitLog", () => {
  it("maps tags and resolved_ref", () => {
    const parsed = parseSpcodeGitLog(envelope({ resolved_ref: "b".repeat(40) }));
    if (parsed.kind !== "ok") throw new Error("expected ok");
    expect(parsed.snapshot.commits[0].tags).toEqual(["v1.0.0", "latest"]);
    expect(parsed.snapshot.resolvedRef).toBe("b".repeat(40));
  });

  it("defaults tags to [] and resolvedRef to '' when backend is older", () => {
    const raw = envelope();
    // 模拟旧插件:删除新字段
    const commit = (raw.data.commits as Record<string, unknown>[])[0];
    delete commit.tags;
    delete (raw.data as Record<string, unknown>).resolved_ref;
    const parsed = parseSpcodeGitLog(raw);
    if (parsed.kind !== "ok") throw new Error("expected ok");
    expect(parsed.snapshot.commits[0].tags).toEqual([]);
    expect(parsed.snapshot.resolvedRef).toBe("");
  });
});
