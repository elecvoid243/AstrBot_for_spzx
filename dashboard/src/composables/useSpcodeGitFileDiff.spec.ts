// Author: elecvoid243 @ 2026-10-06
// Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-frontend-design.md §3.1
//
// useSpcodeGitFileDiff: per-(umo|worktree|path|from|to) 缓存 + ETag/304 +
// in-flight abort 去重。

import { describe, it, expect, vi, beforeEach } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

vi.mock("@/composables/useSpcodeProjectStatus", () => ({
  useSpcodeProjectStatus: () => ({
    status: {
      value: { umo: "umo-test", directory: "D:/repo", loaded: true },
    },
    refresh: vi.fn(),
  }),
}));

const mockGet = vi.fn();
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
  },
}));

import { useSpcodeGitFileDiff } from "./useSpcodeGitFileDiff";

const SHA_A = "a".repeat(40);
const SHA_B = "b".repeat(40);

function okEnvelope(overrides: Record<string, unknown> = {}) {
  return {
    status: 200,
    data: {
      status: "ok",
      data: {
        success: true,
        reason: null,
        from: SHA_A,
        to: SHA_B,
        from_sha: SHA_A,
        to_sha: SHA_B,
        path: "main.py",
        status: "modified",
        old_path: null,
        is_binary: false,
        base_content: "v1\n",
        base_size: 3,
        base_truncated: false,
        patch: "@@ -1 +1 @@\n-v1\n+v2\n",
        additions: 1,
        deletions: 1,
        truncated: false,
        ...overrides,
      },
    },
    headers: { etag: 'W/"e1"' },
  };
}

function withSetup<T>(fn: () => T): T {
  let result: T;
  const Comp = defineComponent({
    setup() {
      result = fn();
      return () => h("div");
    },
  });
  mount(Comp);
  return result!;
}

describe("useSpcodeGitFileDiff", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("fetch success → ok state + parsed data", async () => {
    mockGet.mockResolvedValue(okEnvelope());
    const c = withSetup(() => useSpcodeGitFileDiff());
    await c.fetchDiff("main.py", SHA_A, SHA_B);
    expect(c.getState("main.py", SHA_A, SHA_B).kind).toBe("ok");
    const d = c.getData("main.py", SHA_A, SHA_B);
    expect(d?.baseContent).toBe("v1\n");
    expect(d?.patch).toContain("@@");
    const [endpoint, opts] = mockGet.mock.calls[0] as [
      string,
      { params: Record<string, unknown> },
    ];
    expect(endpoint).toBe("spcode/git-file-diff");
    expect(opts.params.from).toBe(SHA_A);
    expect(opts.params.to).toBe(SHA_B);
    expect(opts.params.path).toBe("main.py");
    expect(opts.params.umo).toBe("umo-test");
  });

  it("cached key → second fetch is a no-op", async () => {
    mockGet.mockResolvedValue(okEnvelope());
    const c = withSetup(() => useSpcodeGitFileDiff());
    await c.fetchDiff("main.py", SHA_A, SHA_B);
    await c.fetchDiff("main.py", SHA_A, SHA_B);
    expect(mockGet).toHaveBeenCalledTimes(1);
  });

  it("in-flight same key → single HTTP call (abort dedup)", async () => {
    let resolveFn!: (v: unknown) => void;
    mockGet.mockImplementation(
      () => new Promise((r) => (resolveFn = r)),
    );
    const c = withSetup(() => useSpcodeGitFileDiff());
    const p1 = c.fetchDiff("main.py", SHA_A, SHA_B);
    const p2 = c.fetchDiff("main.py", SHA_A, SHA_B);
    resolveFn(okEnvelope());
    await Promise.all([p1, p2]);
    expect(mockGet).toHaveBeenCalledTimes(1);
  });

  it("success=false envelope → error state with reason passthrough", async () => {
    mockGet.mockResolvedValue({
      status: 200,
      data: {
        status: "ok",
        data: { success: false, reason: "ref_not_found" },
      },
      headers: {},
    });
    const c = withSetup(() => useSpcodeGitFileDiff());
    await c.fetchDiff("main.py", SHA_A, "deadbeef");
    const s = c.getState("main.py", SHA_A, "deadbeef");
    expect(s.kind).toBe("error");
    if (s.kind === "error") expect(s.reason).toBe("ref_not_found");
  });

  it("304 without cached data → error state (no crash)", async () => {
    mockGet.mockResolvedValue({ status: 304, data: null, headers: {} });
    const c = withSetup(() => useSpcodeGitFileDiff());
    await c.fetchDiff("main.py", SHA_A, SHA_B);
    expect(c.getState("main.py", SHA_A, SHA_B).kind).toBe("error");
  });

  it("cache key includes worktree → different worktree refetches", async () => {
    mockGet.mockResolvedValue(okEnvelope());
    const { ref } = await import("vue");
    const wt = ref<string | null>("wt-1");
    const c = withSetup(() => useSpcodeGitFileDiff(wt));
    await c.fetchDiff("main.py", SHA_A, SHA_B);
    wt.value = "wt-2";
    await c.fetchDiff("main.py", SHA_A, SHA_B);
    expect(mockGet).toHaveBeenCalledTimes(2);
    expect(
      (mockGet.mock.calls[1] as [{}, { params: Record<string, unknown> }])[1]
        .params.worktree,
    ).toBe("wt-2");
  });
});
