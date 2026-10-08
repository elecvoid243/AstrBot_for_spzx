// Author: elecvoid243
// Date: 2026-10-08
// Terminal state of a chat placeholder.
//
// Regression for: send → stop → edit the user message → resend, and the page
// sits on "思考中..." until a hard refresh while the server is healthy.
//
// Mechanism: `isLoading` is ONLY cleared by a stream payload
// (`markMessageStarted`), and the bubble renders `v-if="isLoading"` INSTEAD of
// its content (ChatMessageList). A run that delivered no event at all therefore
// leaves a permanent spinner, which happens through several shapes:
//   * a rejected send answers HTTP 200 + application/json (both the v1 and the
//     legacy handler return `JSONResponse(error(...))`), so `readSseStream`
//     parsed zero frames and never threw;
//   * a 200 answer without a body fell into the same silent hole;
//   * run teardown drops events still buffered for a subscriber, and the
//     subscriber attaches lazily — the run can be over before it attaches, so
//     the stream closes after heartbeats only.
//
// Note for future tests: under happy-dom `Object.prototype.toString.call(new
// AbortController())` is "[object Object]", so Vue proxies it and the stream
// bookkeeping's own `abort` identity check (`activeConnections[id]?.abort ===
// abort`) never matches. Assertions about a *finished* stream must therefore not
// rely on `isSessionRunning`; `cleanupConnections()` (which deletes by key) is
// the shape that does clean up.
//
// These tests pin the guarantees of the fix:
//   1. a rejected send surfaces its reason instead of spinning forever,
//   2. an unfulfilled run drops its empty placeholder and re-syncs history,
//   3. a normal run is untouched (no extra history fetch, bubble kept),
//   4. an aborted request never touches the data,
//   5. the edit-resend placeholder is written through a reactive handle.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref, watchEffect } from "vue";

vi.mock("@/api/v1", () => ({
  chatApi: {
    sendStreamUrl: vi.fn(() => "/api/v1/chat"),
    getSession: vi.fn(),
    getHistory: vi.fn(),
    getMarkers: vi.fn(),
    updateMessage: vi.fn(),
    stopSession: vi.fn(),
  },
  fileApi: {
    contentUrl: vi.fn(() => ""),
  },
}));

vi.mock("@/api/http", () => ({
  fetchWithAuth: vi.fn(),
}));

import { chatApi } from "@/api/v1";
import { fetchWithAuth } from "@/api/http";
import { useMessages } from "../useMessages";

function sseResponse(frames: string) {
  return new Response(frames, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function sseFrame(payload: unknown) {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

/** A business rejection: HTTP 200 with a JSON body (see module header). */
function jsonRejection(message: string) {
  return new Response(JSON.stringify({ status: "error", message }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function sessionPayload(history: unknown[]) {
  return {
    data: {
      data: {
        history,
        threads: [],
        total_messages: history.length,
        has_more: false,
        active_runs: [],
      },
    },
  };
}

function record(id: number, type: "user" | "bot", text: string) {
  return { id, content: { type, message: [{ type: "plain", text }] } };
}

function recordText(parts: Array<{ type?: string; text?: string }> | undefined) {
  return (parts || [])
    .filter((part) => part.type === "plain")
    .map((part) => part.text || "")
    .join("");
}

describe("useMessages placeholder terminal state", () => {
  const currentSessionId = ref("s1");
  let api: {
    getSession: ReturnType<typeof vi.fn>;
    getHistory: ReturnType<typeof vi.fn>;
    getMarkers: ReturnType<typeof vi.fn>;
  };
  const onStreamEnd = vi.fn();

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    api = vi.mocked(chatApi, true) as never;
    api.getMarkers.mockResolvedValue({
      data: { data: { markers: [], total_messages: 0 } },
    });
  });

  function createComposable() {
    return useMessages({ currentSessionId, onStreamEnd });
  }

  /** Send one plain message through the SSE transport and return its bubble. */
  function sendMessage(composable: ReturnType<typeof createComposable>) {
    const parts = [{ type: "plain" as const, text: "hi" }];
    const { userRecord, botRecord } = composable.createLocalExchange({
      sessionId: "s1",
      messageId: "m1",
      parts,
    });
    composable.sendMessageStream({
      sessionId: "s1",
      messageId: "m1",
      parts,
      transport: "sse",
      userRecord,
      botRecord,
    });
    return { userRecord, botRecord };
  }

  it("surfaces a JSON rejection instead of spinning forever", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      jsonRejection("Message content is empty (reply only is not allowed)"),
    );
    const composable = createComposable();

    const { botRecord } = sendMessage(composable);

    await vi.waitFor(() => expect(botRecord.content.isLoading).toBe(false));
    expect(recordText(botRecord.content.message)).toContain(
      "Message content is empty (reply only is not allowed)",
    );
    // Nothing was persisted, so no history re-sync is needed.
    expect(api.getSession).not.toHaveBeenCalled();
    // The explicit reason is the user-facing feedback: keep the bubble.
    expect(composable.activeMessages.value).toContain(botRecord);
    expect(onStreamEnd).toHaveBeenCalledWith("s1");
  });

  it("surfaces a bodyless 200 answer instead of spinning forever", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      new Response(null, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );
    const composable = createComposable();

    const { botRecord } = sendMessage(composable);

    await vi.waitFor(() => expect(botRecord.content.isLoading).toBe(false));
    expect(recordText(botRecord.content.message)).toContain(
      "SSE connection failed: 200",
    );
    expect(api.getSession).not.toHaveBeenCalled();
  });

  it("drops an unfulfilled placeholder and re-syncs the session history", async () => {
    // The stream stays open (heartbeats only) and then closes without a single
    // event — the shape a lost fan-out leaves behind.
    vi.mocked(fetchWithAuth).mockResolvedValue(
      sseResponse(": heartbeat\n\n: heartbeat\n\n"),
    );
    api.getSession.mockResolvedValue(
      sessionPayload([
        record(1, "user", "hi"),
        record(2, "bot", "reply persisted server-side"),
      ]),
    );
    const composable = createComposable();

    const { botRecord } = sendMessage(composable);

    await vi.waitFor(() => expect(api.getSession).toHaveBeenCalledTimes(1));
    // The reply the server persisted replaces the placeholder the fan-out lost.
    expect(composable.activeMessages.value.map((m) => String(m.id))).toEqual([
      "1",
      "2",
    ]);
    expect(composable.activeMessages.value).not.toContain(botRecord);
    expect(onStreamEnd).toHaveBeenCalledWith("s1");
  });

  it("leaves a normal run alone (no placeholder drop, no extra history fetch)", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      sseResponse(
        sseFrame({ type: "plain", data: "hello", streaming: true }) +
          sseFrame({ type: "end", data: "" }),
      ),
    );
    const composable = createComposable();

    const { botRecord } = sendMessage(composable);

    await vi.waitFor(() => expect(botRecord.content.isLoading).toBe(false));
    expect(recordText(botRecord.content.message)).toBe("hello");
    expect(composable.activeMessages.value).toContain(botRecord);
    expect(api.getSession).not.toHaveBeenCalled();
  });

  it("never settles data for an aborted request", async () => {
    vi.mocked(fetchWithAuth).mockImplementation(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    const composable = createComposable();

    const { botRecord } = sendMessage(composable);
    composable.cleanupConnections();

    await vi.waitFor(() => expect(composable.isSessionRunning("s1")).toBe(false));
    expect(botRecord.content.isLoading).toBe(true);
    expect(composable.activeMessages.value).toContain(botRecord);
    expect(api.getSession).not.toHaveBeenCalled();
  });

  it("streams the edit-resend reply through a reactive handle", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      sseResponse(
        sseFrame({ type: "plain", data: "edited reply", streaming: true }) +
          sseFrame({ type: "end", data: "" }),
      ),
    );
    const composable = createComposable();

    composable.continueEditedMessage({
      sessionId: "s1",
      sourceRecord: {
        id: 1,
        content: { type: "user", message: [{ type: "plain", text: "hi" }] },
        llm_checkpoint_id: "cp-1",
      },
    });

    const records = composable.activeMessages.value;
    const placeholder = records[records.length - 1];
    // Count how often an observer of the bubble's rendered state re-runs: a
    // raw (non-reactive) placeholder handed to the stream keeps this at 1, so
    // the bubble would show its first frame ("思考中...") until some unrelated
    // reactive change forced a re-render.
    let renders = 0;
    watchEffect(() => {
      renders += 1;
      void composable.messageParts(placeholder).length;
      void placeholder.content.isLoading;
    });

    await vi.waitFor(() => expect(renders).toBeGreaterThan(1));
    expect(recordText(composable.messageParts(placeholder) as never)).toBe(
      "edited reply",
    );
  });
});
