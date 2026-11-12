import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

vi.mock("@/api/v1", () => ({
  chatApi: {
    sendStreamUrl: () => "/api/v1/chat",
    unifiedStreamUrl: () => "/api/v1/chat/stream",
    sendMessageUrl: () => "/api/v1/chat/send",
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
  });

  it("settles the optimistic bubble when the send POST is rejected", async () => {
    localStorage.setItem("chat.streamMode", "unified");
    vi.mocked(fetchWithAuth).mockResolvedValue(
      new Response(
        JSON.stringify({ status: "error", message: "rejected" }),
        {
          status: 400,
          headers: { "content-type": "application/json" },
        },
      ),
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
});
