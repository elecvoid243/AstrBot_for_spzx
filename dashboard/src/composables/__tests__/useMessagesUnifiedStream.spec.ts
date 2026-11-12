import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

vi.mock("@/api/v1", () => ({
  chatApi: {
    sendStreamUrl: () => "/api/v1/chat",
    unifiedStreamUrl: () => "/api/v1/chat/stream",
    sendMessageUrl: () => "/api/v1/chat/send",
    runSnapshotUrl: (runId: string) => `/api/v1/chat/runs/${runId}/snapshot`,
    systemStreamUrl: () => "/api/v1/chat/sessions/s1/system-stream",
    getSession: vi.fn(),
    getHistory: vi.fn(),
    getMarkers: vi.fn(),
  },
  fileApi: { contentUrl: () => "" },
}));
vi.mock("@/api/http", () => ({ fetchWithAuth: vi.fn() }));

import { chatApi } from "@/api/v1";
import { fetchWithAuth } from "@/api/http";
import { useInteractiveChoiceStore } from "@/stores/interactiveChoice";
import { useMessages, type ChatRecord } from "../useMessages";

function plainText(record: ChatRecord) {
  return (record.content.message || [])
    .filter((part) => part.type === "plain")
    .map((part) => part.text || "")
    .join("");
}

function pushSse(
  controller: ReadableStreamDefaultController<Uint8Array>,
  payload: unknown,
) {
  controller.enqueue(
    new TextEncoder().encode(`data: ${JSON.stringify(payload)}\n\n`),
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  setActivePinia(createPinia());
  vi.mocked(chatApi.getMarkers).mockResolvedValue({
    data: {
      status: "ok",
      data: { markers: [], total_messages: 0, truncated: false },
    },
  } as never);
});

describe("unified chat stream", () => {
  it("sends a plain POST and renders the run from the unified stream", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(fetchWithAuth).mockImplementation(async (url) => {
      const target = String(url);
      if (target.includes("/chat/stream")) {
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      if (target.includes("/chat/send")) {
        return new Response(
          JSON.stringify({
            status: "ok",
            data: { run_id: "m1", input_message_id: 42 },
          }),
          { headers: { "content-type": "application/json" } },
        );
      }
      return new Response(null, { status: 404 });
    });
    const onStreamEnd = vi.fn();
    const messages = useMessages({
      currentSessionId: ref("s1"),
      onStreamEnd,
    });
    messages.messagesBySession["s1"] = [];
    messages.startUnifiedStream();
    await vi.waitFor(() => expect(streamController).toBeDefined());
    pushSse(streamController, { type: "stream_ready", epoch: "server-epoch" });

    const parts = [{ type: "plain" as const, text: "hello" }];
    const exchange = messages.createLocalExchange({
      sessionId: "s1",
      messageId: "m1",
      parts,
    });
    messages.sendMessageStream({
      sessionId: "s1",
      messageId: "m1",
      parts,
      transport: "sse",
      ...exchange,
    });
    await vi.waitFor(() =>
      expect(fetchWithAuth).toHaveBeenCalledWith(
        "/api/v1/chat/send",
        expect.anything(),
      ),
    );
    expect(messages.isSessionRunning("s1")).toBe(true);

    pushSse(streamController, {
      type: "run_started",
      session_id: "s1",
      seq: 1,
      message_id: "m1",
      data: {
        run_id: "m1",
        input_message: {
          id: 42,
          created_at: "2026-11-12T00:00:00Z",
        },
      },
    });
    pushSse(streamController, {
      type: "plain",
      session_id: "s1",
      seq: 2,
      message_id: "m1",
      data: "world",
    });
    pushSse(streamController, {
      type: "end",
      session_id: "s1",
      seq: 3,
      message_id: "m1",
      data: { status: "completed" },
    });

    await vi.waitFor(() => expect(onStreamEnd).toHaveBeenCalledWith("s1"));
    expect(plainText(exchange.botRecord)).toContain("world");
    expect(exchange.botRecord.content.isLoading).toBe(false);
    expect(exchange.userRecord.id).toBe(42);
    expect(messages.isSessionRunning("s1")).toBe(false);
    streamController.close();
    messages.cleanupConnections();
  });

  it("settles the optimistic bubble when the send POST is rejected", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    vi.mocked(fetchWithAuth).mockResolvedValue(
      new Response(JSON.stringify({ status: "error", message: "rejected" }), {
        status: 400,
        headers: { "content-type": "application/json" },
      }),
    );
    const onStreamEnd = vi.fn();
    const onError = vi.fn();
    const messages = useMessages({
      currentSessionId: ref("s1"),
      onStreamEnd,
      onError,
    });
    const parts = [{ type: "plain" as const, text: "hello" }];
    const exchange = messages.createLocalExchange({
      sessionId: "s1",
      messageId: "m1",
      parts,
    });
    messages.sendMessageStream({
      sessionId: "s1",
      messageId: "m1",
      parts,
      transport: "sse",
      ...exchange,
    });

    await vi.waitFor(() => expect(onStreamEnd).toHaveBeenCalledWith("s1"));
    expect(plainText(exchange.botRecord)).toContain("rejected");
    expect(exchange.botRecord.content.isLoading).toBe(false);
    expect(onError).toHaveBeenCalledWith("rejected", "s1");
    expect(messages.isSessionRunning("s1")).toBe(false);
  });

  it("dispatches background runs by session and materializes only loaded sessions", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(fetchWithAuth).mockImplementation(async (url) => {
      if (String(url).includes("/chat/stream")) {
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      return new Response(null, { status: 404 });
    });
    const onStreamEnd = vi.fn();
    const messages = useMessages({
      currentSessionId: ref("s1"),
      onStreamEnd,
    });
    messages.messagesBySession["s1"] = [];
    messages.startUnifiedStream();
    await vi.waitFor(() => expect(streamController).toBeDefined());
    pushSse(streamController, { type: "stream_ready", epoch: "e" });

    messages.messagesBySession["s2"] = [];
    const started = {
      type: "run_started",
      session_id: "s2",
      seq: 1,
      message_id: "m2",
      data: {
        run_id: "m2",
        input_message: {
          id: 52,
          content: {
            type: "user",
            message: [{ type: "plain", text: "background" }],
          },
        },
      },
    };
    pushSse(streamController, started);
    pushSse(streamController, started);
    await vi.waitFor(() => expect(messages.isSessionRunning("s2")).toBe(true));
    await vi.waitFor(() =>
      expect(
        messages.messagesBySession["s2"].map((record) => String(record.id)),
      ).toEqual(["52", "local-bot-m2"]),
    );

    pushSse(streamController, {
      type: "plain",
      session_id: "s2",
      seq: 2,
      message_id: "m2",
      data: "background reply",
    });
    await vi.waitFor(() =>
      expect(plainText(messages.messagesBySession["s2"][1])).toContain(
        "background reply",
      ),
    );
    pushSse(streamController, {
      type: "end",
      session_id: "s2",
      seq: 3,
      message_id: "m2",
      data: { status: "completed" },
    });
    await vi.waitFor(() => expect(messages.isSessionRunning("s2")).toBe(false));
    expect(onStreamEnd).toHaveBeenCalledWith("s2");

    pushSse(streamController, {
      type: "run_started",
      session_id: "s3",
      seq: 1,
      message_id: "m3",
      data: { run_id: "m3" },
    });
    await vi.waitFor(() => expect(messages.isSessionRunning("s3")).toBe(true));
    expect(messages.messagesBySession["s3"]).toBeUndefined();
    pushSse(streamController, {
      type: "plain",
      session_id: "s3",
      seq: 2,
      message_id: "m3",
      data: "ignored token",
    });
    pushSse(streamController, {
      type: "end",
      session_id: "s3",
      seq: 3,
      message_id: "m3",
      data: { status: "completed" },
    });
    await vi.waitFor(() => expect(messages.isSessionRunning("s3")).toBe(false));
    expect(messages.messagesBySession["s3"]).toBeUndefined();
    expect(onStreamEnd).toHaveBeenCalledWith("s3");
    streamController.close();
    messages.cleanupConnections();
  });

  it("reconnects with the last cursors and applies replayed frames", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    const controllers: ReadableStreamDefaultController<Uint8Array>[] = [];
    const bodies: any[] = [];
    vi.mocked(fetchWithAuth).mockImplementation(async (url, init) => {
      if (String(url).includes("/chat/stream")) {
        bodies.push(
          JSON.parse(String((init as RequestInit | undefined)?.body || "{}")),
        );
        return new Response(
          new ReadableStream({
            start(controller) {
              controllers.push(controller);
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      return new Response(null, { status: 404 });
    });
    const messages = useMessages({ currentSessionId: ref("s1") });
    messages.messagesBySession["s1"] = [];
    messages.startUnifiedStream();
    await vi.waitFor(() => expect(controllers).toHaveLength(1));
    pushSse(controllers[0], { type: "stream_ready", epoch: "e1" });
    pushSse(controllers[0], {
      type: "run_started",
      session_id: "s1",
      seq: 1,
      message_id: "m1",
      data: { run_id: "m1" },
    });
    pushSse(controllers[0], {
      type: "plain",
      session_id: "s1",
      seq: 2,
      message_id: "m1",
      data: "hello",
    });
    await vi.waitFor(() =>
      expect(plainText(messages.messagesBySession["s1"][0])).toContain("hello"),
    );

    controllers[0].close();
    await vi.waitFor(() => expect(controllers).toHaveLength(2), {
      timeout: 3000,
    });
    expect(bodies[1]).toEqual({ epoch: "e1", cursors: { s1: 2 } });

    pushSse(controllers[1], { type: "stream_ready", epoch: "e1" });
    pushSse(controllers[1], {
      type: "plain",
      session_id: "s1",
      seq: 3,
      message_id: "m1",
      data: " world",
    });
    pushSse(controllers[1], {
      type: "end",
      session_id: "s1",
      seq: 4,
      message_id: "m1",
      data: { status: "completed" },
    });
    await vi.waitFor(() =>
      expect(plainText(messages.messagesBySession["s1"][0])).toContain(
        "hello world",
      ),
    );
    expect(messages.isSessionRunning("s1")).toBe(false);
    messages.cleanupConnections();
  });

  it("reconnects when the server asks for a resync", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    const controllers: ReadableStreamDefaultController<Uint8Array>[] = [];
    const bodies: any[] = [];
    vi.mocked(fetchWithAuth).mockImplementation(async (url, init) => {
      if (String(url).includes("/chat/stream")) {
        bodies.push(
          JSON.parse(String((init as RequestInit | undefined)?.body || "{}")),
        );
        return new Response(
          new ReadableStream({
            start(controller) {
              controllers.push(controller);
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      return new Response(null, { status: 404 });
    });
    const messages = useMessages({ currentSessionId: ref("s1") });
    messages.messagesBySession["s1"] = [];
    messages.startUnifiedStream();
    await vi.waitFor(() => expect(controllers).toHaveLength(1));
    pushSse(controllers[0], { type: "stream_ready", epoch: "e1" });
    pushSse(controllers[0], {
      type: "resync_required",
      session_id: "s1",
      from_seq: 5,
    });
    await vi.waitFor(() => expect(controllers).toHaveLength(2), {
      timeout: 2000,
    });
    expect(bodies[1]).toEqual({ epoch: "e1", cursors: {} });
    messages.cleanupConnections();
  });

  it("applies goal and shell state from the reconnect snapshot", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(fetchWithAuth).mockImplementation(async (url) => {
      if (String(url).includes("/chat/stream")) {
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      return new Response(null, { status: 404 });
    });
    const onGoal = vi.fn();
    const onShell = vi.fn();
    const messages = useMessages({
      currentSessionId: ref("s1"),
      onGoalStateChanged: onGoal,
      onShellSessionsChanged: onShell,
    });
    messages.messagesBySession["s1"] = [];
    messages.startUnifiedStream();
    await vi.waitFor(() => expect(streamController).toBeDefined());
    pushSse(streamController, { type: "stream_ready", epoch: "e1" });
    pushSse(streamController, {
      type: "session_state",
      session_id: "s1",
      seq: 1,
      data: { goal: { text: "goal" }, sessions: [{ id: "shell" }] },
    });
    await vi.waitFor(() =>
      expect(onGoal).toHaveBeenCalledWith("s1", { text: "goal" }),
    );
    expect(onShell).toHaveBeenCalledWith("s1", [{ id: "shell" }]);
    messages.cleanupConnections();
  });

  it("fetches a large run_snapshot ref before rendering", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(fetchWithAuth).mockImplementation(async (url) => {
      const target = String(url);
      if (target.includes("/chat/stream")) {
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      if (target.includes("/chat/runs/run-snap/snapshot")) {
        return new Response(
          JSON.stringify({
            status: "ok",
            data: {
              content: {
                type: "bot",
                message: [{ type: "plain", text: "full snapshot" }],
              },
            },
          }),
          { headers: { "content-type": "application/json" } },
        );
      }
      return new Response(null, { status: 404 });
    });
    const onStreamEnd = vi.fn();
    const messages = useMessages({
      currentSessionId: ref("s1"),
      onStreamEnd,
    });
    messages.messagesBySession["s1"] = [];
    messages.startUnifiedStream();
    await vi.waitFor(() => expect(streamController).toBeDefined());
    pushSse(streamController, { type: "stream_ready", epoch: "e1" });
    pushSse(streamController, {
      type: "run_started",
      session_id: "s1",
      seq: 1,
      message_id: "run-snap",
      data: { run_id: "run-snap" },
    });
    pushSse(streamController, {
      type: "run_snapshot",
      session_id: "s1",
      seq: 2,
      message_id: "run-snap",
      data: {
        content: {
          snapshot_ref: { run_id: "run-snap", bytes: 70000 },
        },
      },
    });
    await vi.waitFor(() =>
      expect(fetchWithAuth).toHaveBeenCalledWith(
        "/api/v1/chat/runs/run-snap/snapshot",
        expect.anything(),
      ),
    );
    await vi.waitFor(() =>
      expect(
        plainText(messages.messagesBySession["s1"].at(-1) as ChatRecord),
      ).toContain("full snapshot"),
    );
    pushSse(streamController, {
      type: "end",
      session_id: "s1",
      seq: 3,
      message_id: "run-snap",
      data: { status: "completed" },
    });
    await vi.waitFor(() => expect(onStreamEnd).toHaveBeenCalledWith("s1"));
    streamController.close();
    messages.cleanupConnections();
  });

  it("fetches a large complete text_ref before settling the reply", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(fetchWithAuth).mockImplementation(async (url) => {
      const target = String(url);
      if (target.includes("/chat/stream")) {
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      if (target.includes("/chat/runs/m1/snapshot")) {
        return new Response(
          JSON.stringify({
            status: "ok",
            data: {
              content: {
                type: "bot",
                message: [{ type: "plain", text: "final text" }],
              },
            },
          }),
          { headers: { "content-type": "application/json" } },
        );
      }
      return new Response(null, { status: 404 });
    });
    const onStreamEnd = vi.fn();
    const messages = useMessages({
      currentSessionId: ref("s1"),
      onStreamEnd,
    });
    messages.messagesBySession["s1"] = [];
    messages.startUnifiedStream();
    await vi.waitFor(() => expect(streamController).toBeDefined());
    pushSse(streamController, { type: "stream_ready", epoch: "e1" });
    pushSse(streamController, {
      type: "run_started",
      session_id: "s1",
      seq: 1,
      message_id: "m1",
      data: { run_id: "m1" },
    });
    pushSse(streamController, {
      type: "complete",
      session_id: "s1",
      seq: 2,
      message_id: "m1",
      data: {
        text_ref: { run_id: "m1", message_id: "m1", bytes: 70000 },
      },
    });
    await vi.waitFor(() =>
      expect(
        plainText(messages.messagesBySession["s1"].at(-1) as ChatRecord),
      ).toContain("final text"),
    );
    pushSse(streamController, {
      type: "end",
      session_id: "s1",
      seq: 3,
      message_id: "m1",
      data: { status: "completed" },
    });
    await vi.waitFor(() => expect(onStreamEnd).toHaveBeenCalledWith("s1"));
    streamController.close();
    messages.cleanupConnections();
  });

  it("stores a choice for a session that is not loaded and notifies the page", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(fetchWithAuth).mockImplementation(async (url) => {
      if (String(url).includes("/chat/stream")) {
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      return new Response(null, { status: 404 });
    });
    const onInteractiveChoice = vi.fn();
    const messages = useMessages({
      currentSessionId: ref("s1"),
      onInteractiveChoice,
    });
    messages.startUnifiedStream();
    await vi.waitFor(() => expect(streamController).toBeDefined());
    pushSse(streamController, { type: "stream_ready", epoch: "e" });

    pushSse(streamController, {
      type: "plain",
      chain_type: "interactive_choice",
      session_id: "s9",
      seq: 1,
      message_id: "m9",
      data: JSON.stringify({
        request_id: "req-1",
        spec: {
          type: "interactive_choice",
          prompt: "pick one",
          options: [
            { id: "a", label: "A" },
            { id: "b", label: "B" },
          ],
        },
        umo: "webchat:FriendMessage:webchat!u!s9",
      }),
    });

    await vi.waitFor(() =>
      expect(onInteractiveChoice).toHaveBeenCalledWith("s9"),
    );
    expect(
      useInteractiveChoiceStore().activeChoices["s9"]?.["req-1"],
    ).toBeTruthy();
    expect(messages.messagesBySession["s9"]).toBeUndefined();
    streamController.close();
    messages.cleanupConnections();
  });

  it("ignores a persisted websocket transport in unified mode", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    const socket = vi
      .spyOn(globalThis, "WebSocket")
      .mockImplementation(() => {
        throw new Error("unified mode must not open a websocket");
      });
    vi.mocked(fetchWithAuth).mockImplementation(async (url) => {
      if (String(url).includes("/chat/send")) {
        return new Response(
          JSON.stringify({
            status: "ok",
            data: { run_id: "m1", input_message_id: 1 },
          }),
          { headers: { "content-type": "application/json" } },
        );
      }
      return new Response(null, { status: 404 });
    });
    const messages = useMessages({ currentSessionId: ref("s1") });
    messages.messagesBySession["s1"] = [];
    const parts = [{ type: "plain" as const, text: "hello" }];
    const exchange = messages.createLocalExchange({
      sessionId: "s1",
      messageId: "m1",
      parts,
    });
    messages.sendMessageStream({
      sessionId: "s1",
      messageId: "m1",
      parts,
      transport: "websocket",
      ...exchange,
    });

    await vi.waitFor(() =>
      expect(fetchWithAuth).toHaveBeenCalledWith(
        "/api/v1/chat/send",
        expect.anything(),
      ),
    );
    expect(socket).not.toHaveBeenCalled();
    socket.mockRestore();
    messages.cleanupConnections();
  });

  it("regenerates through the unified endpoint and reuses the optimistic record", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(fetchWithAuth).mockImplementation(async (url) => {
      const target = String(url);
      if (target.includes("/chat/stream")) {
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      if (target.includes("/chat/send")) {
        return new Response(JSON.stringify({ status: "ok", data: {} }), {
          headers: { "content-type": "application/json" },
        });
      }
      return new Response(null, { status: 404 });
    });
    const onStreamEnd = vi.fn();
    const messages = useMessages({
      currentSessionId: ref("s1"),
      onStreamEnd,
    });
    const botRecord: ChatRecord = {
      id: 5,
      created_at: "2026-11-12T00:00:00Z",
      content: {
        type: "bot",
        message: [{ type: "plain", text: "old answer" }],
      },
    };
    messages.messagesBySession["s1"] = [botRecord];
    messages.startUnifiedStream();
    await vi.waitFor(() => expect(streamController).toBeDefined());
    pushSse(streamController, { type: "stream_ready", epoch: "e" });

    await messages.regenerateMessage("s1", botRecord, "", "", true, undefined, "default");

    const sendCall = vi
      .mocked(fetchWithAuth)
      .mock.calls.find(([url]) => String(url).includes("/chat/send"));
    expect(sendCall).toBeTruthy();
    const body = JSON.parse(String(sendCall?.[1]?.body));
    expect(body.mode).toBe("regenerate");
    expect(body.session_id).toBe("s1");
    expect(body.message_id).toBe(5);
    expect(body.run_id).toBeTruthy();
    expect(messages.isSessionRunning("s1")).toBe(true);

    pushSse(streamController, {
      type: "run_started",
      session_id: "s1",
      seq: 1,
      message_id: body.run_id,
      data: { run_id: body.run_id },
    });
    pushSse(streamController, {
      type: "plain",
      session_id: "s1",
      seq: 2,
      message_id: body.run_id,
      data: "new answer",
    });
    pushSse(streamController, {
      type: "end",
      session_id: "s1",
      seq: 3,
      message_id: body.run_id,
      data: { status: "completed" },
    });

    await vi.waitFor(() => expect(onStreamEnd).toHaveBeenCalledWith("s1"));
    expect(plainText(botRecord)).toContain("new answer");
    expect(messages.messagesBySession["s1"]).toHaveLength(1);
    expect(messages.isSessionRunning("s1")).toBe(false);
    streamController.close();
    messages.cleanupConnections();
  });

  it("continues an edited message over the unified endpoint", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(fetchWithAuth).mockImplementation(async (url) => {
      const target = String(url);
      if (target.includes("/chat/stream")) {
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      if (target.includes("/chat/send")) {
        return new Response(JSON.stringify({ status: "ok", data: {} }), {
          headers: { "content-type": "application/json" },
        });
      }
      return new Response(null, { status: 404 });
    });
    const messages = useMessages({ currentSessionId: ref("s1") });
    const sourceRecord: ChatRecord = {
      id: 3,
      created_at: "2026-11-12T00:00:00Z",
      llm_checkpoint_id: "ckpt-1",
      content: {
        type: "user",
        message: [{ type: "plain", text: "edited question" }],
      },
    };
    messages.messagesBySession["s1"] = [sourceRecord];
    messages.startUnifiedStream();
    await vi.waitFor(() => expect(streamController).toBeDefined());
    pushSse(streamController, { type: "stream_ready", epoch: "e" });

    messages.continueEditedMessage({
      sessionId: "s1",
      sourceRecord,
      configId: "default",
    });

    await vi.waitFor(() =>
      expect(
        vi
          .mocked(fetchWithAuth)
          .mock.calls.some(([url]) => String(url).includes("/chat/send")),
      ).toBe(true),
    );
    const sendCall = vi
      .mocked(fetchWithAuth)
      .mock.calls.find(([url]) => String(url).includes("/chat/send"));
    const body = JSON.parse(String(sendCall?.[1]?.body));
    expect(body.mode).toBe("send");
    expect(body._skip_user_history).toBe(true);
    expect(body._llm_checkpoint_id).toBe("ckpt-1");
    expect(body.content).toEqual([{ type: "plain", text: "edited question" }]);
    expect(
      vi
        .mocked(fetchWithAuth)
        .mock.calls.some(([url]) => String(url) === "/api/v1/chat"),
    ).toBe(false);
    expect(messages.isSessionRunning("s1")).toBe(true);
    streamController.close();
    messages.cleanupConnections();
  });
});
