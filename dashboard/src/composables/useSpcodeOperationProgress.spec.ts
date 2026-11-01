// useSpcodeOperationProgress.spec.ts
// Author: elecvoid243 @ 2026-08-06
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: vi.fn() },
}));

import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeOperationProgress } from "./useSpcodeOperationProgress";

function mockGet(status: string, extra: Record<string, unknown> = {}) {
  (pluginExtensionApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({
    data: { data: { status, ...extra } },
  });
}

/** Flush nested microtasks that fake timers don't drain in one pass. */
async function flushMicrotasks(times = 5): Promise<void> {
  for (let i = 0; i < times; i++) await Promise.resolve();
}

describe("useSpcodeOperationProgress", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useSpcodeOperationProgress().clear();
  });
  afterEach(() => {
    // Stop polling BEFORE restoring real timers so no scheduled pollOnce
    // fires after the mock is torn down (would cause unhandled rejections).
    useSpcodeOperationProgress().stopPolling();
    vi.useRealTimers();
  });

  it("starts in idle", () => {
    const { progress } = useSpcodeOperationProgress();
    expect(progress.value.status).toBe("idle");
    expect(progress.value.operation).toBeNull();
  });

  it("drops an old response and stops polling when the account changes", async () => {
    let finish!: (value: unknown) => void;
    const get = pluginExtensionApi.get as ReturnType<typeof vi.fn>;
    get.mockReset();
    get.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const { progress, startPolling } = useSpcodeOperationProgress();
    startPolling("account-a-chat");
    window.dispatchEvent(new Event("astrbot:account-changed"));
    finish({ data: { data: { status: "running", messages: ["Private result"] } } });
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(1000);
    expect(progress.value.status).toBe("idle");
    expect(progress.value.messages).toEqual([]);
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("polls and mirrors running progress", async () => {
    mockGet("running", {
      operation: "project_load",
      current_step: "⏳ [1/3] init",
      messages: ["⏳ [1/3] init"],
    });
    const { progress, startPolling } = useSpcodeOperationProgress();
    startPolling("u1");
    await vi.advanceTimersByTimeAsync(0); // first immediate poll
    await flushMicrotasks();
    expect(progress.value.status).toBe("running");
    expect(progress.value.currentStep).toBe("⏳ [1/3] init");
  });

  it("stops polling on terminal state", async () => {
    mockGet("done", { operation: "project_load" });
    const { progress, startPolling } = useSpcodeOperationProgress();
    startPolling("u1");
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    expect(progress.value.status).toBe("done");
    const calls = (pluginExtensionApi.get as ReturnType<typeof vi.fn>).mock
      .calls.length;
    await vi.advanceTimersByTimeAsync(2000);
    await flushMicrotasks();
    expect(
      (pluginExtensionApi.get as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBe(calls); // no more polls after terminal
  });

  it("keeps failed terminal state for the chip popover", async () => {
    mockGet("failed", {
      operation: "project_load",
      reason: "path_unsafe",
      messages: ["❌ path unsafe"],
    });
    const { progress, startPolling } = useSpcodeOperationProgress();
    startPolling("u1");
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    expect(progress.value.status).toBe("failed");
    expect(progress.value.reason).toBe("path_unsafe");
    expect(progress.value.messages).toEqual(["❌ path unsafe"]);
  });

  it("clear resets to idle", async () => {
    mockGet("failed", { operation: "project_load" });
    const { progress, startPolling, clear } = useSpcodeOperationProgress();
    startPolling("u1");
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    clear();
    expect(progress.value.status).toBe("idle");
  });

  it("isProjectOpRunning locks only running project load/unload", async () => {
    const { progress, isProjectOpRunning, clear } =
      useSpcodeOperationProgress();

    const set = (
      status: "idle" | "running" | "done" | "failed",
      operation: "project_load" | "project_unload" | "codegraph_set" | null,
    ) => {
      progress.value = {
        status,
        operation,
        currentStep: "",
        messages: [],
        reason: null,
      };
    };

    set("running", "project_load");
    expect(isProjectOpRunning.value).toBe(true);
    set("running", "project_unload");
    expect(isProjectOpRunning.value).toBe(true);
    // codegraph_set does not touch the system prompt -> no lock
    set("running", "codegraph_set");
    expect(isProjectOpRunning.value).toBe(false);
    // terminal states unlock (failed = no prompt change happened)
    set("done", "project_load");
    expect(isProjectOpRunning.value).toBe(false);
    set("failed", "project_load");
    expect(isProjectOpRunning.value).toBe(false);
    clear();
    expect(isProjectOpRunning.value).toBe(false);
  });
});
