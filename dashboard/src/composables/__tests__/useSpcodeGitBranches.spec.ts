import { describe, it, expect, vi, beforeEach } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

// Mock the spcode project status composable BEFORE importing the SUT.
vi.mock("../useSpcodeProjectStatus", () => ({
  useSpcodeProjectStatus: () => ({
    status: {
      value: { umo: "umo-test", directory: "D:/repo", loaded: true },
    },
    refresh: vi.fn(),
  }),
}));

const mockGet = vi.fn();
const mockPost = vi.fn();
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

import { useSpcodeGitBranches } from "../useSpcodeGitBranches";
import type { SpcodeGitBranchesRawResponse } from "../parseSpcodeGitBranches";

function okEnvelope(data: Partial<SpcodeGitBranchesRawResponse>) {
  return {
    status: 200,
    data: { status: "ok", data: { ...data } },
    headers: { etag: 'W/"abc"' },
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

describe("useSpcodeGitBranches — shell", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  it("refresh() transitions loading → ok and stores snapshot", async () => {
    mockGet.mockResolvedValueOnce(
      okEnvelope({
        loaded: true,
        directory: "D:/repo",
        umo: "umo-test",
        branches: [
          {
            name: "main",
            sha: "a",
            upstream: "",
            upstream_track: "",
            current: true,
            remote: false,
          },
        ],
        total: 1,
        current: "main",
        detached: false,
        reason: null,
        stderr: "",
        elapsed_ms: 10,
      }),
    );
    const { state, refresh } = withSetup(() => useSpcodeGitBranches());
    expect(state.value.kind).toBe("idle");
    await refresh();
    expect(state.value.kind).toBe("ok");
    if (state.value.kind === "ok") {
      expect(state.value.snapshot.current).toBe("main");
      expect(state.value.snapshot.branches).toHaveLength(1);
    }
  });

  it("refresh() with 304 replays previous snapshot with notModified", async () => {
    const ok200 = okEnvelope({
      loaded: true,
      directory: "D:/repo",
      umo: "umo-test",
      branches: [
        {
          name: "main",
          sha: "a",
          upstream: "",
          upstream_track: "",
          current: true,
          remote: false,
        },
      ],
      total: 1,
      current: "main",
      detached: false,
      reason: null,
      stderr: "",
      elapsed_ms: 10,
    });
    mockGet.mockResolvedValueOnce(ok200);
    const { state, refresh } = withSetup(() => useSpcodeGitBranches());
    await refresh();
    expect(state.value.kind).toBe("ok");

    // Second call returns 304
    mockGet.mockResolvedValueOnce({ status: 304, data: null, headers: {} });
    await refresh();
    if (state.value.kind === "ok") {
      expect(state.value.notModified).toBe(true);
      expect(state.value.snapshot.current).toBe("main");
    }
  });

  it("refresh() sets error state with previousSnapshot on failure", async () => {
    mockGet.mockRejectedValueOnce(new Error("network"));
    const { state, refresh } = withSetup(() => useSpcodeGitBranches());
    await refresh();
    expect(state.value.kind).toBe("error");
    if (state.value.kind === "error") {
      expect(state.value.reason).toBe("network");
    }
  });

  it("startPolling is idempotent; stopPolling clears the timer", async () => {
    vi.useFakeTimers();
    mockGet.mockResolvedValue(
      okEnvelope({
        loaded: true,
        directory: "D:/repo",
        umo: "umo-test",
        branches: [],
        total: 0,
        current: null,
        detached: false,
        reason: null,
        stderr: "",
        elapsed_ms: 0,
      }),
    );
    const { startPolling, stopPolling } = withSetup(() =>
      useSpcodeGitBranches(),
    );
    startPolling(1000);
    startPolling(1000); // second call should be no-op
    expect(mockGet).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(mockGet).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(mockGet).toHaveBeenCalledTimes(2);
    stopPolling();
    await vi.advanceTimersByTimeAsync(5000);
    expect(mockGet).toHaveBeenCalledTimes(2); // still 2 — no more calls
    vi.useRealTimers();
  });

  it("dispose() clears timer and prevents future state updates", async () => {
    vi.useFakeTimers();
    mockGet.mockResolvedValue(
      okEnvelope({
        loaded: true,
        directory: "D:/repo",
        umo: "umo-test",
        branches: [],
        total: 0,
        current: null,
        detached: false,
        reason: null,
        stderr: "",
        elapsed_ms: 0,
      }),
    );
    const { startPolling, dispose } = withSetup(() =>
      useSpcodeGitBranches(),
    );
    startPolling(1000);
    await vi.advanceTimersByTimeAsync(1000);
    expect(mockGet).toHaveBeenCalledTimes(1);
    dispose();
    await vi.advanceTimersByTimeAsync(5000);
    expect(mockGet).toHaveBeenCalledTimes(1); // no more calls after dispose
    vi.useRealTimers();
  });

  // ── refreshDelayed (2026-08-13): initial / project-switch fetch ────
  it("refreshDelayed() defers the fetch by ~500ms", async () => {
    vi.useFakeTimers();
    mockGet.mockResolvedValue(
      okEnvelope({
        loaded: true,
        directory: "D:/repo",
        umo: "umo-test",
        branches: [],
        total: 0,
        current: null,
        detached: false,
        reason: null,
        stderr: "",
        elapsed_ms: 0,
      }),
    );
    const { refreshDelayed } = withSetup(() => useSpcodeGitBranches());
    refreshDelayed();
    expect(mockGet).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(499);
    expect(mockGet).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(mockGet).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("refreshDelayed() coalesces rapid calls into a single fetch", async () => {
    vi.useFakeTimers();
    mockGet.mockResolvedValue(
      okEnvelope({
        loaded: true,
        directory: "D:/repo",
        umo: "umo-test",
        branches: [],
        total: 0,
        current: null,
        detached: false,
        reason: null,
        stderr: "",
        elapsed_ms: 0,
      }),
    );
    const { refreshDelayed } = withSetup(() => useSpcodeGitBranches());
    refreshDelayed();
    refreshDelayed(); // second call cancels the first pending timer
    await vi.advanceTimersByTimeAsync(500);
    expect(mockGet).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("dispose() clears a pending refreshDelayed timer", async () => {
    vi.useFakeTimers();
    mockGet.mockResolvedValue(
      okEnvelope({
        loaded: true,
        directory: "D:/repo",
        umo: "umo-test",
        branches: [],
        total: 0,
        current: null,
        detached: false,
        reason: null,
        stderr: "",
        elapsed_ms: 0,
      }),
    );
    const { refreshDelayed, dispose } = withSetup(() =>
      useSpcodeGitBranches(),
    );
    refreshDelayed();
    dispose();
    await vi.advanceTimersByTimeAsync(1000);
    expect(mockGet).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  // ── Mutation tests ──────────────────────────────────────
  it("switch() success: state updated with refreshed snapshot", async () => {
    mockGet.mockResolvedValueOnce(
      okEnvelope({
        loaded: true,
        directory: "D:/repo",
        umo: "umo-test",
        branches: [
          {
            name: "main",
            sha: "a",
            upstream: "",
            upstream_track: "",
            current: true,
            remote: false,
          },
        ],
        total: 1,
        current: "main",
        detached: false,
        reason: null,
        stderr: "",
        elapsed_ms: 0,
      }),
    );
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: {
        status: "ok",
        data: {
          loaded: true,
          directory: "D:/repo",
          umo: "umo-test",
          switched: true,
          name: "feat/x",
          previous: "main",
          created: false,
          force: false,
          detach: false,
          branches: [
            {
              name: "main",
              sha: "a",
              upstream: "",
              upstream_track: "",
              current: false,
              remote: false,
            },
            {
              name: "feat/x",
              sha: "b",
              upstream: "",
              upstream_track: "",
              current: true,
              remote: false,
            },
          ],
          total: 2,
          current: "feat/x",
          detached: false,
          reason: null,
          stderr: "",
          elapsed_ms: 0,
        },
      },
    });
    const { state, refresh, switch: doSwitch } = withSetup(() =>
      useSpcodeGitBranches(),
    );
    await refresh();
    const r = await doSwitch({ name: "feat/x" });
    expect(r.ok).toBe(true);
    if (state.value.kind === "ok") {
      expect(state.value.snapshot.current).toBe("feat/x");
    }
  });

  it("switch() keeps the previous tag list when the mutation response omits tags", async () => {
    mockGet.mockResolvedValueOnce(
      okEnvelope({
        loaded: true,
        directory: "D:/repo",
        umo: "umo-test",
        branches: [
          {
            name: "main",
            sha: "a",
            upstream: "",
            upstream_track: "",
            current: true,
            remote: false,
          },
        ],
        tags: [{ name: "v1.0.0", sha: "a".repeat(40), annotated: false }],
        total: 1,
        current: "main",
        detached: false,
        reason: null,
        stderr: "",
        elapsed_ms: 0,
      }),
    );
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: {
        status: "ok",
        data: {
          loaded: true,
          directory: "D:/repo",
          umo: "umo-test",
          switched: true,
          name: "feat/x",
          previous: "main",
          created: false,
          force: false,
          detach: false,
          branches: [
            {
              name: "main",
              sha: "a",
              upstream: "",
              upstream_track: "",
              current: false,
              remote: false,
            },
            {
              name: "feat/x",
              sha: "b",
              upstream: "",
              upstream_track: "",
              current: true,
              remote: false,
            },
          ],
          total: 2,
          current: "feat/x",
          detached: false,
          reason: null,
          stderr: "",
          elapsed_ms: 0,
        },
      },
    });
    const { state, refresh, switch: doSwitch } = withSetup(() =>
      useSpcodeGitBranches(),
    );
    await refresh();
    if (state.value.kind === "ok") {
      expect(state.value.snapshot.tags).toEqual([
        { name: "v1.0.0", sha: "a".repeat(40), annotated: false },
      ]);
    }
    const r = await doSwitch({ name: "feat/x" });
    expect(r.ok).toBe(true);
    if (state.value.kind === "ok") {
      expect(state.value.snapshot.current).toBe("feat/x");
      expect(state.value.snapshot.tags).toEqual([
        { name: "v1.0.0", sha: "a".repeat(40), annotated: false },
      ]);
    }
  });

  it("switch() failure: returns ok=false with reason and stderr", async () => {
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: {
        status: "ok",
        data: {
          loaded: true,
          directory: "D:/repo",
          umo: "umo-test",
          reason: "worktree_dirty",
          stderr: "working tree has uncommitted changes",
          elapsed_ms: 0,
          branches: [],
        },
      },
    });
    const { switch: doSwitch } = withSetup(() => useSpcodeGitBranches());
    const r = await doSwitch({ name: "feat/x" });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.reason).toBe("worktree_dirty");
      expect(r.stderr).toContain("uncommitted");
    }
  });

  it("create() success: snapshot includes new branch", async () => {
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: {
        status: "ok",
        data: {
          loaded: true,
          directory: "D:/repo",
          umo: "umo-test",
          created: true,
          name: "feat/new",
          start_point: "HEAD",
          force: false,
          sha: "newSha",
          branches: [
            {
              name: "main",
              sha: "a",
              upstream: "",
              upstream_track: "",
              current: true,
              remote: false,
            },
            {
              name: "feat/new",
              sha: "newSha",
              upstream: "",
              upstream_track: "",
              current: false,
              remote: false,
            },
          ],
          total: 2,
          current: "main",
          detached: false,
          reason: null,
          stderr: "",
          elapsed_ms: 0,
        },
      },
    });
    const { state, create: doCreate } = withSetup(() =>
      useSpcodeGitBranches(),
    );
    const r = await doCreate({ name: "feat/new" });
    expect(r.ok).toBe(true);
    if (state.value.kind === "ok") {
      expect(state.value.snapshot.branches.map((b) => b.name)).toContain(
        "feat/new",
      );
    }
  });

  it("create() failure on branch_exists: returns ok=false with reason", async () => {
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: {
        status: "ok",
        data: {
          loaded: true,
          directory: "D:/repo",
          umo: "umo-test",
          reason: "branch_exists",
          stderr: "already exists",
          elapsed_ms: 0,
          branches: [],
        },
      },
    });
    const { create: doCreate } = withSetup(() => useSpcodeGitBranches());
    const r = await doCreate({ name: "feat/dup" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("branch_exists");
  });

  it("delete() success: branch removed from snapshot", async () => {
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: {
        status: "ok",
        data: {
          loaded: true,
          directory: "D:/repo",
          umo: "umo-test",
          deleted: true,
          name: "feat/old",
          force: false,
          was_current: false,
          branches: [
            {
              name: "main",
              sha: "a",
              upstream: "",
              upstream_track: "",
              current: true,
              remote: false,
            },
          ],
          total: 1,
          current: "main",
          detached: false,
          reason: null,
          stderr: "",
          elapsed_ms: 0,
        },
      },
    });
    const { state, delete: doDelete } = withSetup(() =>
      useSpcodeGitBranches(),
    );
    const r = await doDelete({ name: "feat/old" });
    expect(r.ok).toBe(true);
    if (state.value.kind === "ok") {
      expect(state.value.snapshot.branches.map((b) => b.name)).not.toContain(
        "feat/old",
      );
    }
  });

  it("delete() failure on branch_is_current: returns ok=false", async () => {
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: {
        status: "ok",
        data: {
          loaded: true,
          directory: "D:/repo",
          umo: "umo-test",
          reason: "branch_is_current",
          stderr: "cannot delete current",
          elapsed_ms: 0,
          branches: [],
        },
      },
    });
    const { delete: doDelete } = withSetup(() => useSpcodeGitBranches());
    const r = await doDelete({ name: "main" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("branch_is_current");
  });
});
