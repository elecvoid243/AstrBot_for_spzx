// dashboard/src/composables/useSpcodeOperationProgress.ts
//
// Singleton polling driver for the spcode silent-operation progress
// endpoint (GET /spcode/operation-progress). Any silent POST (project
// load / unload / codegraph set) calls startPolling(umo) before firing;
// the chips read `progress` to render their loading / failed states.
//
// Author: elecvoid243 @ 2026-08-06

import { computed, ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";

export type OperationKind =
  | "project_load"
  | "project_unload"
  | "codegraph_set"
  | "codegraph_init";
export type OperationStatus = "idle" | "running" | "done" | "failed";

export interface OperationProgress {
  status: OperationStatus;
  operation: OperationKind | null;
  currentStep: string;
  messages: string[];
  reason: string | null;
}

const IDLE: OperationProgress = {
  status: "idle",
  operation: null,
  currentStep: "",
  messages: [],
  reason: null,
};

const progress = ref<OperationProgress>({ ...IDLE });

/**
 * Send-lock gate (2026-08-07): true while a project load/unload is in
 * flight. These two operations rewrite the system prompt (AGENTS.md
 * injection), so sending mid-load would break prompt-cache continuity
 * between turns. codegraph_set only restarts the MCP server and does
 * NOT lock sending. Failed/timeout states deliberately do not lock:
 * a failed load means no prompt change happened, so sending is safe.
 */
const isProjectOpRunning = computed(() => {
  const p = progress.value;
  return (
    p.status === "running" &&
    (p.operation === "project_load" || p.operation === "project_unload")
  );
});

const POLL_INTERVAL_MS = 500;
const POLL_TIMEOUT_MS = 200_000; // codegraph MCP restart can take 180 s
let timer: ReturnType<typeof setTimeout> | null = null;
let deadline = 0;

function stopTimer(): void {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
}

function scheduleNext(umo: string): void {
  timer = setTimeout(() => void pollOnce(umo), POLL_INTERVAL_MS);
}

async function pollOnce(umo: string): Promise<void> {
  let terminal = false;
  try {
    const res = await pluginExtensionApi.get<{
      status: OperationStatus;
      operation?: OperationKind;
      current_step?: string;
      messages?: string[];
      reason?: string | null;
    }>("spcode/operation-progress", { params: { umo } });
    const data = res.data?.data;
    if (data && data.status !== "idle") {
      progress.value = {
        status: data.status,
        operation: data.operation ?? null,
        currentStep: data.current_step ?? "",
        messages: data.messages ?? [],
        reason: data.reason ?? null,
      };
      terminal = data.status === "done" || data.status === "failed";
    }
  } catch {
    // Network hiccup: keep polling until the deadline; the POST's own
    // error handling covers real failures.
  }
  if (!terminal && Date.now() > deadline) {
    progress.value = { ...progress.value, status: "failed", reason: "network_timeout" };
    terminal = true;
  }
  if (terminal) {
    stopTimer();
    return;
  }
  scheduleNext(umo);
}

export function useSpcodeOperationProgress() {
  /**
   * Reset to running and poll every 500 ms until a terminal state.
   *
   * Args:
   *   umo: Session umo the silent operation was dispatched for.
   *   timeoutMs: Polling deadline (default 200 s). codegraph init can run
   *     for minutes (300 s + 180 s --force retry), so callers pass a
   *     larger budget there.
   */
  function startPolling(
    umo: string,
    timeoutMs: number = POLL_TIMEOUT_MS,
  ): void {
    stopTimer();
    progress.value = { ...IDLE, status: "running" };
    deadline = Date.now() + timeoutMs;
    void pollOnce(umo); // immediate first poll; chain continues via scheduleNext
  }

  function stopPolling(): void {
    stopTimer();
  }

  /** Stop polling and reset to idle (session switch / manual dismiss). */
  function clear(): void {
    stopPolling();
    progress.value = { ...IDLE };
  }

  return { progress, isProjectOpRunning, startPolling, stopPolling, clear };
}
