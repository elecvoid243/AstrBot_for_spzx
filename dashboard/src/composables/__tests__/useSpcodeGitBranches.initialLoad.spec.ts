// Author: elecvoid243 @ 2026-10-07
//
// Regression tests for "当前分支显示为 detached HEAD，必须手点刷新才加载出来".
//
// Root cause (see useSpcodeGitBranches.ts's RETRY_BACKOFF_MS comment):
//   1. The dashboard mounts GitDiffSidebar unconditionally, so the first
//      git-branches request fires ~500ms after page load — before the
//      project is registered. The backend answers with a failure envelope
//      `{ success: false, reason: "no_project_loaded" }`, which owns no
//      `branches` field. Feeding it to parseSpcodeGitBranches used to
//      produce an "ok but empty" snapshot → `current: null` → the label
//      fell back to the i18n string "detached HEAD".
//   2. Project load only flips `directory` (the umo is already resolved),
//      so the umo-only watcher never refired; the 30s branch poller is
//      also never started when the sidebar was opened before the load.
//      Net effect: no automatic refetch channel at all.
//
// These tests pin the two channels added by the fix: the failure envelope
// is surfaced as `error` (never as an empty snapshot), and a bounded
// backoff retry + a `directory` watcher land the snapshot on their own.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, nextTick, ref, type Ref } from "vue";
import { mount } from "@vue/test-utils";

interface SessionStatus {
  umo: string | null;
  directory: string | null;
  loaded: boolean;
}

// vi.hoisted so the mock factory (hoisted above the imports) can write
// into it. The status MUST be a real ref: the SUT watches
// `session.directory`, and only a reactive source can flip it.
const holder = vi.hoisted(() => ({ status: null as unknown }));

vi.mock("../useSpcodeProjectStatus", async () => {
  const { ref: vueRef } = await import("vue");
  const status = vueRef<SessionStatus>({
    umo: null,
    directory: null,
    loaded: false,
  });
  holder.status = status;
  return {
    useSpcodeProjectStatus: () => ({ status, refresh: vi.fn() }),
  };
});

const mockGet = vi.fn();
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
  },
}));

import { useSpcodeGitBranches } from "../useSpcodeGitBranches";

function statusRef(): Ref<SessionStatus> {
  const s = holder.status as Ref<SessionStatus> | null;
  if (!s) throw new Error("session status ref not initialised");
  return s;
}

const BRANCHES_OK = {
  success: true,
  branches: [
    {
      name: "main",
      sha: "abc1234",
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
  elapsed_ms: 3,
};

/** Axios response carrying a plugin envelope. */
function okEnvelope(inner: Record<string, unknown>) {
  return {
    status: 200,
    data: { status: "ok", data: inner },
    headers: { etag: 'W/"abc"' },
  };
}

/** The backend's "project not registered yet" answer (no `branches` key). */
function failureEnvelope(reason: string) {
  return {
    status: 200,
    data: {
      status: "ok",
      data: { success: false, reason, stderr: "", elapsed_ms: 1 },
    },
    headers: {},
  };
}

const mounted: Array<{ unmount: () => void }> = [];

function withSetup<T>(fn: () => T): T {
  let result: T;
  const Comp = defineComponent({
    setup() {
      result = fn();
      return () => h("div");
    },
  });
  const wrapper = mount(Comp);
  mounted.push(wrapper);
  return result!;
}

describe("useSpcodeGitBranches — project-load race", () => {
  beforeEach(() => {
    mockGet.mockReset();
    // Established session by default; the mount-time gap test (no umo yet)
    // opts out explicitly.
    statusRef().value = { umo: "umo-test", directory: "D:/repo", loaded: true };
  });

  afterEach(() => {
    for (const w of mounted.splice(0)) w.unmount();
    vi.useRealTimers();
  });

  it("surfaces a success:false envelope as an error, not as an empty branch list", async () => {
    vi.useFakeTimers();
    mockGet.mockResolvedValue(failureEnvelope("no_project_loaded"));
    const { state, refresh, dispose } = withSetup(() => useSpcodeGitBranches());

    await refresh();

    expect(state.value.kind).toBe("error");
    if (state.value.kind === "error") {
      expect(state.value.reason).toBe("no_project_loaded");
    }
    dispose();
  });

  it("retries a not-ready failure with backoff and lands the snapshot once the project registers", async () => {
    vi.useFakeTimers();
    mockGet
      .mockResolvedValueOnce(failureEnvelope("no_project_loaded"))
      .mockResolvedValueOnce(okEnvelope(BRANCHES_OK));
    const { state, refresh, dispose } = withSetup(() => useSpcodeGitBranches());

    await refresh();
    expect(state.value.kind).toBe("error");

    // First retry sits at the head of the backoff table: 500ms.
    await vi.advanceTimersByTimeAsync(499);
    expect(mockGet).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(mockGet).toHaveBeenCalledTimes(2);

    expect(state.value.kind).toBe("ok");
    if (state.value.kind === "ok") {
      expect(state.value.snapshot.current).toBe("main");
    }

    // A successful fetch must tear the retry chain down (no request per
    // backoff slot forever).
    await vi.advanceTimersByTimeAsync(30_000);
    expect(mockGet).toHaveBeenCalledTimes(2);
    dispose();
  });

  it("does not retry failures that re-fetching cannot fix", async () => {
    vi.useFakeTimers();
    mockGet.mockResolvedValue(failureEnvelope("feature_disabled"));
    const { state, refresh, dispose } = withSetup(() => useSpcodeGitBranches());

    await refresh();
    expect(state.value.kind).toBe("error");

    await vi.advanceTimersByTimeAsync(30_000);
    expect(mockGet).toHaveBeenCalledTimes(1);
    dispose();
  });

  it("refetches when only the directory changes (project load keeps the same umo)", async () => {
    vi.useFakeTimers();
    statusRef().value = { umo: "umo-test", directory: "D:/repo", loaded: true };
    mockGet.mockResolvedValue(okEnvelope(BRANCHES_OK));
    const { refresh, dispose } = withSetup(() => useSpcodeGitBranches());

    await refresh();
    expect(mockGet).toHaveBeenCalledTimes(1);

    // /project load finishes: same umo, new directory. Before the fix
    // nothing refetched here.
    statusRef().value = { umo: "umo-test", directory: "D:/repo2", loaded: true };
    await nextTick();
    await vi.advanceTimersByTimeAsync(500);

    expect(mockGet).toHaveBeenCalledTimes(2);
    const secondCall = mockGet.mock.calls[1][1] as { params: { umo: string } };
    expect(secondCall.params.umo).toBe("umo-test");
    dispose();
  });

  it("recovers from the mount-time gap where the session umo is not resolved yet", async () => {
    vi.useFakeTimers();
    statusRef().value = { umo: null, directory: null, loaded: false };
    mockGet.mockResolvedValue(okEnvelope(BRANCHES_OK));
    const { state, refresh, dispose } = withSetup(() => useSpcodeGitBranches());

    // Mount time: no session yet → local short-circuit, no request.
    await refresh();
    expect(mockGet).not.toHaveBeenCalled();
    expect(state.value.kind).toBe("error");

    // Session established; the umo watcher defers ~500ms, both triggers
    // coalesce into a single fetch.
    statusRef().value = {
      umo: "umo-test",
      directory: "D:/repo",
      loaded: true,
    };
    await nextTick();
    await vi.advanceTimersByTimeAsync(500);

    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(state.value.kind).toBe("ok");
    dispose();
  });
});
