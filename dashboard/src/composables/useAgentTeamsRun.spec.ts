// Unit tests for the agent-teams run composable: SSE attach + fold into the
// reactive run state (heartbeat/unknown events tolerated), snapshot seeding,
// startRun wiring, reconnect capping and the abort lifecycle of closeRun.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuthMock = vi.hoisted(() => vi.fn());

vi.mock("@/api/http", () => ({ fetchWithAuth: fetchWithAuthMock }));

const apiMocks = vi.hoisted(() => ({
  listActiveRuns: vi.fn(),
  startRun: vi.fn(),
  pauseRun: vi.fn(),
  resumeRun: vi.fn(),
  stopRun: vi.fn(),
  retryNode: vi.fn(),
  skipNode: vi.fn(),
  runStreamUrl: (runId: string) => `/api/v1/agent_teams/runs/${runId}/stream`,
}));

vi.mock("@/api/v1", () => ({ agentTeamsApi: apiMocks }));

const toastMocks = vi.hoisted(() => ({
  toast: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));

vi.mock("@/utils/toast", () => ({ useToast: () => toastMocks }));

import { computed } from "vue";
import { useAgentTeamsRun } from "./useAgentTeamsRun";

const ok = (data: unknown) =>
  Promise.resolve({ data: { status: "ok", message: null, data } });
const fail = (message: string) =>
  Promise.resolve({ data: { status: "error", message, data: null } });

const encoder = new TextEncoder();

function frame(payload: unknown): Uint8Array {
  return encoder.encode(`data: ${JSON.stringify(payload)}\n\n`);
}

function sseResponse(frames: Uint8Array[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of frames) controller.enqueue(chunk);
      controller.close();
    },
  });
  // Minimal Response-like object: the composable only reads ok/headers/body.
  return {
    ok: true,
    headers: new Headers({ "content-type": "text/event-stream" }),
    body,
  } as unknown as Response;
}

// SSE response whose body stays open: frames can be pumped while an observer
// (computed/component) is already attached, to assert live reactivity.
function controlledSse(): {
  response: Response;
  push: (payload: unknown) => void;
  close: () => void;
} {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: {
      ok: true,
      headers: new Headers({ "content-type": "text/event-stream" }),
      body,
    } as unknown as Response,
    push: (payload) => controller.enqueue(frame(payload)),
    close: () => controller.close(),
  };
}

// A stream that never emits and never closes: pins the connection open so
// abort/close behavior can be asserted deterministically.
function hangingResponse(): Response {
  return {
    ok: true,
    headers: new Headers({ "content-type": "text/event-stream" }),
    body: new ReadableStream<Uint8Array>({ start() {} }),
  } as unknown as Response;
}

// Let the fire-and-forget attach loop drain its microtasks (stream reads).
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  fetchWithAuthMock.mockReset();
  for (const mock of [
    apiMocks.listActiveRuns,
    apiMocks.startRun,
    apiMocks.pauseRun,
    apiMocks.resumeRun,
    apiMocks.stopRun,
    apiMocks.retryNode,
    apiMocks.skipNode,
  ]) {
    mock.mockReset();
  }
  for (const mock of Object.values(toastMocks)) mock.mockReset();
});

afterEach(() => {
  useAgentTeamsRun().closeRun();
  vi.useRealTimers();
});

describe("useAgentTeamsRun SSE fold", () => {
  it("folds framed events into the run state, skipping heartbeats and unknown payloads", async () => {
    apiMocks.listActiveRuns.mockResolvedValue(ok({ runs: [] }));
    fetchWithAuthMock.mockReturnValue(
      sseResponse([
        encoder.encode(": heartbeat\n\n"),
        frame({ type: "unknown_kind" }),
        // Multi-line data: lines are joined before JSON.parse.
        encoder.encode(
          'data: {"type":\ndata: "round", "n": 1, "max": 3}\n\n',
        ),
        frame({ type: "message", direction: "sent", member_id: "m1", text: "task A" }),
        frame({ type: "message", direction: "stream", member_id: "m1", text: "Hel" }),
        frame({ type: "message", direction: "stream", member_id: "m1", text: "lo" }),
        frame({
          type: "message",
          direction: "reply",
          member_id: "m1",
          text: "Hello world",
          parts: [{ type: "text" }],
        }),
        frame({
          type: "dag_progress",
          done: 1,
          running: 0,
          pending: 0,
          skipped: 0,
          failed: 0,
          total: 1,
        }),
        frame({ type: "stopped", reason: "all done" }),
      ]),
    );

    const run = useAgentTeamsRun();
    await run.openRun("run-1");
    await flush();

    expect(fetchWithAuthMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchWithAuthMock.mock.calls[0];
    expect(url).toBe("/api/v1/agent_teams/runs/run-1/stream");
    expect(init.headers.Accept).toBe("text/event-stream");
    expect(init.signal).toBeInstanceOf(AbortSignal);

    const state = run.runState.value!;
    expect(state.runId).toBe("run-1");
    expect(state.windows.m1).toMatchObject({
      sent: "task A",
      streamText: "Hello world",
      streaming: false,
    });
    expect(state.windows.m1.parts).toEqual([{ type: "text" }]);
    // Joined multi-line data + progress mirror.
    expect(state.round).toEqual({ n: 1, max: 3 });
    expect(state.progress).toEqual({
      done: 1,
      running: 0,
      pending: 0,
      skipped: 0,
      failed: 0,
      total: 1,
    });
    expect(state.stoppedReason).toBe("all done");
    expect(state.status).toBe("stopped");
  });

  it("streams deltas through the deep-reactive state into child-computed reads", async () => {
    // Regression: with a shallowRef + triggerRef host the folds re-rendered
    // only the monitor's own template — child props (window objects) compared
    // Object.is-equal, so AgentWindow's `blocks` computed never recomputed.
    // This mirrors that read path: the window object is captured once (the
    // prop handoff) and a computed tracks its nested fields through the same
    // reactive proxy the component would read.
    apiMocks.listActiveRuns.mockResolvedValue(ok({ runs: [] }));
    const stream = controlledSse();
    fetchWithAuthMock.mockReturnValue(stream.response);

    const run = useAgentTeamsRun();
    await run.openRun("run-1");
    await flush();

    stream.push({ type: "message", direction: "sent", member_id: "m1", text: "task A" });
    await flush();

    // Prop handoff: the parent passes the window object by reference; the
    // child computed tracks only its nested fields from here on.
    const win = run.runState.value?.windows.m1 ?? null;
    expect(win).not.toBeNull();
    const view = computed(() => ({
      sent: win?.sent ?? null,
      streamText: win?.streamText ?? "",
      streaming: win?.streaming ?? false,
      parts: win?.parts ?? [],
    }));
    expect(view.value).toEqual({
      sent: "task A",
      streamText: "",
      streaming: false,
      parts: [],
    });

    stream.push({ type: "message", direction: "stream", member_id: "m1", text: "Hel" });
    await flush();
    stream.push({ type: "message", direction: "stream", member_id: "m1", text: "lo" });
    await flush();
    expect(view.value.streamText).toBe("Hello");
    expect(view.value.streaming).toBe(true);

    stream.push({
      type: "message",
      direction: "reply",
      member_id: "m1",
      text: "Hello world",
      parts: [{ type: "text" }],
    });
    await flush();
    expect(view.value).toEqual({
      sent: "task A",
      streamText: "Hello world",
      streaming: false,
      parts: [{ type: "text" }],
    });
    stream.close();
  });

  it("openRun seeds state from the active-run snapshot when present", async () => {    apiMocks.listActiveRuns.mockResolvedValue(
      ok({
        runs: [
          {
            run_id: "run-9",
            status: "paused",
            node_states: { n1: { status: "done" } },
            graph: { nodes: [] },
          },
        ],
      }),
    );
    // Never-ending stream: no SSE event can overwrite the seeded state.
    fetchWithAuthMock.mockImplementation(() => hangingResponse());

    const run = useAgentTeamsRun();
    await run.openRun("run-9");

    expect(apiMocks.listActiveRuns).toHaveBeenCalledTimes(1);
    expect(run.runState.value?.status).toBe("paused");
    expect(run.runState.value?.nodeStates.n1).toEqual({ status: "done" });
  });

  it("prefers a caller-provided seed (camelCase) over the active-run lookup", async () => {
    apiMocks.listActiveRuns.mockResolvedValue(
      ok({ runs: [{ run_id: "run-9", status: "running" }] }),
    );
    fetchWithAuthMock.mockImplementation(() => hangingResponse());

    const run = useAgentTeamsRun();
    const state = await run.openRun("run-9", {
      status: "completed",
      nodeStates: { n1: { status: "failed", error: "boom" } },
    });

    expect(state?.status).toBe("completed");
    expect(state?.nodeStates.n1).toEqual({ status: "failed", error: "boom" });
  });

  it("falls back to a bare state when the run is not in the active list", async () => {
    apiMocks.listActiveRuns.mockResolvedValue(
      ok({ runs: [{ run_id: "other", status: "running" }] }),
    );
    fetchWithAuthMock.mockImplementation(() => hangingResponse());

    const run = useAgentTeamsRun();
    await run.openRun("run-missing");

    expect(run.runState.value?.runId).toBe("run-missing");
    expect(run.runState.value?.status).toBe("running");
    expect(run.runState.value?.nodeStates).toEqual({});
  });

  it("loadActiveRuns populates monitors", async () => {
    apiMocks.listActiveRuns.mockResolvedValue(
      ok({ runs: [{ run_id: "r1", status: "running" }] }),
    );
    const run = useAgentTeamsRun();
    const monitors = await run.loadActiveRuns();
    expect(monitors).toEqual([{ run_id: "r1", status: "running" }]);
    expect(run.monitors.value).toEqual([{ run_id: "r1", status: "running" }]);
    expect(fetchWithAuthMock).not.toHaveBeenCalled();
  });
});

describe("useAgentTeamsRun lifecycle", () => {
  it("startRun seeds the state from the snapshot and attaches the stream", async () => {
    const snapshot = {
      run_id: "run-x",
      status: "running",
      node_states: { a: { status: "pending" } },
    };
    apiMocks.startRun.mockResolvedValue(ok(snapshot));
    fetchWithAuthMock.mockImplementation(() => hangingResponse());

    const run = useAgentTeamsRun();
    const result = await run.startRun("team-1", {
      mode: "dag",
      input: "do it",
      workflow_id: "wf-1",
    });

    expect(apiMocks.startRun).toHaveBeenCalledWith("team-1", {
      mode: "dag",
      input: "do it",
      workflow_id: "wf-1",
    });
    expect(result).toEqual(snapshot);
    expect(run.runState.value?.runId).toBe("run-x");
    expect(run.runState.value?.nodeStates.a).toEqual({ status: "pending" });
    expect(fetchWithAuthMock).toHaveBeenCalledWith(
      "/api/v1/agent_teams/runs/run-x/stream",
      expect.objectContaining({ headers: { Accept: "text/event-stream" } }),
    );
  });

  it("startRun toasts error envelopes and attaches nothing", async () => {
    apiMocks.startRun.mockResolvedValue(fail("team already has an active run"));
    const run = useAgentTeamsRun();

    expect(
      await run.startRun("team-1", { mode: "dag", input: "x" }),
    ).toBeNull();
    expect(toastMocks.error).toHaveBeenCalledWith(
      "team already has an active run",
    );
    expect(run.runState.value).toBeNull();
    expect(fetchWithAuthMock).not.toHaveBeenCalled();
  });

  it("startRun toasts the backend envelope message when axios throws (409)", async () => {
    // Axios rejects non-2xx with the response still attached; the envelope
    // message must win over axios's generic error message.
    apiMocks.startRun.mockRejectedValue({
      message: "Request failed with status code 409",
      response: {
        status: 409,
        data: { status: "error", message: "团队已有运行中的任务" },
      },
    });
    const run = useAgentTeamsRun();

    expect(
      await run.startRun("team-1", { mode: "dag", input: "x" }),
    ).toBeNull();
    expect(toastMocks.error).toHaveBeenCalledWith("团队已有运行中的任务");
  });

  it("closeRun aborts the SSE fetch signal and clears the state", async () => {
    apiMocks.listActiveRuns.mockResolvedValue(ok({ runs: [] }));
    fetchWithAuthMock.mockImplementation(() => hangingResponse());

    const run = useAgentTeamsRun();
    await run.openRun("run-a");
    const signal = fetchWithAuthMock.mock.calls[0][1].signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    run.closeRun();
    expect(signal.aborted).toBe(true);
    expect(run.runState.value).toBeNull();
  });

  it("opening another run aborts the previous attachment", async () => {
    apiMocks.listActiveRuns.mockResolvedValue(ok({ runs: [] }));
    fetchWithAuthMock.mockImplementation(() => hangingResponse());

    const run = useAgentTeamsRun();
    await run.openRun("run-a");
    const first = fetchWithAuthMock.mock.calls[0][1].signal as AbortSignal;
    await run.openRun("run-b");
    const second = fetchWithAuthMock.mock.calls[1][1].signal as AbortSignal;

    expect(first.aborted).toBe(true);
    expect(second.aborted).toBe(false);
    expect(run.runState.value?.runId).toBe("run-b");
  });

  it("reconnect reattaches the current run", async () => {
    apiMocks.listActiveRuns.mockResolvedValue(ok({ runs: [] }));
    // Fresh stream per call: a Response body can only be read once.
    fetchWithAuthMock.mockImplementation(() => hangingResponse());

    const run = useAgentTeamsRun();
    await run.openRun("run-a");
    fetchWithAuthMock.mockClear();

    run.reconnect();
    expect(fetchWithAuthMock).toHaveBeenCalledWith(
      "/api/v1/agent_teams/runs/run-a/stream",
      expect.anything(),
    );
  });

  it("reconnects up to 5 attempts with 1s backoff when the stream ends abnormally", async () => {
    vi.useFakeTimers();
    apiMocks.listActiveRuns.mockResolvedValue(ok({ runs: [] }));
    // Stream ends without a stopped event: the loop treats it as a drop.
    fetchWithAuthMock.mockImplementation(() =>
      sseResponse([
        frame({ type: "message", direction: "sent", member_id: "m1", text: "t" }),
      ]),
    );

    const run = useAgentTeamsRun();
    await run.openRun("run-c");
    expect(fetchWithAuthMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1100);
    expect(fetchWithAuthMock).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(10000);
    expect(fetchWithAuthMock).toHaveBeenCalledTimes(5);
  });

  it("does not reconnect once the run reached a terminal status", async () => {
    apiMocks.listActiveRuns.mockResolvedValue(ok({ runs: [] }));
    fetchWithAuthMock.mockReturnValue(
      sseResponse([frame({ type: "stopped", reason: "done" })]),
    );

    const run = useAgentTeamsRun();
    await run.openRun("run-d");
    await flush();

    expect(run.runState.value?.status).toBe("stopped");
    expect(fetchWithAuthMock).toHaveBeenCalledTimes(1);
    await flush();
    expect(fetchWithAuthMock).toHaveBeenCalledTimes(1);
  });
});

describe("useAgentTeamsRun controls", () => {
  it("pause/resume/stop and node actions target the current run", async () => {
    apiMocks.listActiveRuns.mockResolvedValue(ok({ runs: [] }));
    fetchWithAuthMock.mockImplementation(() => hangingResponse());
    apiMocks.pauseRun.mockResolvedValue(ok({ message: "paused" }));
    apiMocks.resumeRun.mockResolvedValue(ok({ message: "resumed" }));
    apiMocks.stopRun.mockResolvedValue(ok({ message: "stopping" }));
    apiMocks.retryNode.mockResolvedValue(ok({}));
    apiMocks.skipNode.mockResolvedValue(ok({}));

    const run = useAgentTeamsRun();
    await run.openRun("run-a");

    await run.pause("run-a");
    expect(apiMocks.pauseRun).toHaveBeenCalledWith("run-a");
    expect(run.runState.value?.status).toBe("paused");

    await run.resumeRun("run-a");
    expect(apiMocks.resumeRun).toHaveBeenCalledWith("run-a");
    expect(run.runState.value?.status).toBe("running");

    await run.stop("run-a");
    expect(apiMocks.stopRun).toHaveBeenCalledWith("run-a");
    expect(run.runState.value?.status).toBe("stopping");

    await run.retryNode("n1");
    expect(apiMocks.retryNode).toHaveBeenCalledWith("run-a", "n1");

    await run.skipNode("n1");
    expect(apiMocks.skipNode).toHaveBeenCalledWith("run-a", "n1");
  });

  it("node actions without an attached run do not call the API", async () => {
    const run = useAgentTeamsRun();
    expect(await run.retryNode("n1")).toBeNull();
    expect(await run.skipNode("n1")).toBeNull();
    expect(apiMocks.retryNode).not.toHaveBeenCalled();
    expect(apiMocks.skipNode).not.toHaveBeenCalled();
  });

  it("control error envelopes toast", async () => {
    apiMocks.listActiveRuns.mockResolvedValue(ok({ runs: [] }));
    fetchWithAuthMock.mockImplementation(() => hangingResponse());
    apiMocks.stopRun.mockResolvedValue(fail("run already stopped"));

    const run = useAgentTeamsRun();
    await run.openRun("run-a");
    await run.stop("run-a");

    expect(toastMocks.error).toHaveBeenCalledWith("run already stopped");
  });
});
