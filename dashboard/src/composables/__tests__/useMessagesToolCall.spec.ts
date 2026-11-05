import { describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

vi.mock("@/api/v1", () => ({
  chatApi: {
    sendStreamUrl: () => "/api/v1/chat",
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

describe("live tool-call notifications", () => {
  it.each([1, 2])(
    "preserves a pending Python tool across %i follow-ups and its result",
    async (followupCount) => {
      setActivePinia(createPinia());
      let controller!: ReadableStreamDefaultController<Uint8Array>;
      vi.mocked(fetchWithAuth).mockImplementation(async (url) =>
        String(url).includes("system-stream")
          ? new Response(null, { status: 403 })
          : new Response(
              new ReadableStream({
                start(value) {
                  controller = value;
                },
              }),
              { headers: { "content-type": "text/event-stream" } },
            ),
      );
      const onStreamEnd = vi.fn();
      const onToolCall = vi.fn();
      const messages = useMessages({
        currentSessionId: ref("s1"),
        onStreamEnd,
        onToolCall,
      });
      const calls = (record: ChatRecord) =>
        record.content.message.flatMap((part) => part.tool_calls || []);
      const tool = {
        id: "python-1",
        name: "astrbot_execute_python",
        args: { code: "import time; time.sleep(20)" },
        ts: 1,
      };
      const parts = [{ type: "plain", text: "Run a Python program." }];
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
      await vi.waitFor(() => expect(controller).toBeDefined());
      const push = (payload: unknown) =>
        controller.enqueue(
          new TextEncoder().encode(`data: ${JSON.stringify(payload)}\n\n`),
        );
      try {
        push({ type: "run_started", data: { run_id: "r1" } });
        push({
          type: "plain",
          chain_type: "reasoning",
          data: "Run the first program.",
        });
        push({
          type: "plain",
          chain_type: "tool_call",
          data: JSON.stringify(tool),
        });
        await vi.waitFor(() =>
          expect(calls(exchange.botRecord)).toEqual([tool]),
        );
        for (let index = 0; index < followupCount; index++) {
          const user = messages.createLocalExchange({
            sessionId: "s1",
            messageId: `followup-${index}`,
            parts: [{ type: "plain", text: "Print Hello world next." }],
          }).userRecord;
          push({
            type: "message_segment",
            data: {
              previous: {
                id: 2 + index * 2,
                content: {
                  type: "bot",
                  message:
                    index === 0
                      ? [{ type: "think", think: "Run the first program." }]
                      : [],
                },
              },
              user: { ...user, id: 3 + index * 2 },
              client_message_id: `followup-${index}`,
              segment_id: `segment-${index}`,
              content: {
                type: "bot",
                message: [{ type: "tool_call", tool_calls: [tool] }],
              },
            },
          });
          await vi.waitFor(() =>
            expect(exchange.botRecord.id).toBe(
              `active-segment-segment-${index}`,
            ),
          );
          expect(calls(exchange.botRecord)).toEqual([tool]);
          expect(exchange.botRecord.content.isLoading).toBe(false);
          expect(messages.activeMessages.value.flatMap(calls)).toEqual([tool]);
        }
        push({
          type: "plain",
          chain_type: "tool_call_result",
          data: JSON.stringify({ id: tool.id, result: "slept", ts: 2 }),
        });
        const second = {
          ...tool,
          id: "python-2",
          args: { code: "print('Hello world')" },
          ts: 3,
        };
        push({
          type: "plain",
          chain_type: "tool_call",
          data: JSON.stringify(second),
        });
        push({
          type: "plain",
          chain_type: "tool_call_result",
          data: JSON.stringify({ id: second.id, result: "Hello world", ts: 4 }),
        });
        push({ type: "end" });
        controller.close();
        await vi.waitFor(() => expect(onStreamEnd).toHaveBeenCalledWith("s1"));
        expect(messages.activeMessages.value.flatMap(calls)).toEqual([
          { ...tool, result: "slept", finished_ts: 2 },
          { ...second, result: "Hello world", finished_ts: 4 },
        ]);
        expect(onToolCall).toHaveBeenCalledTimes(2);
      } finally {
        messages.cleanupConnections();
      }
    },
  );

  it("reports the live run identity without firing for history, plain text or results", async () => {
    setActivePinia(createPinia());
    const tool = {
      id: "t1",
      name: "browser_navigate",
      args: { url: "http://localhost" },
    };
    vi.mocked(chatApi.getSession).mockResolvedValue({
      data: {
        data: {
          history: [
            {
              id: 1,
              content: {
                type: "bot",
                message: [{ type: "tool_call", tool_calls: [tool] }],
              },
            },
          ],
          threads: [],
          total_messages: 1,
          has_more: false,
          active_runs: [],
        },
      },
    } as never);
    vi.mocked(chatApi.getMarkers).mockResolvedValue({
      data: { data: { markers: [] } },
    } as never);
    const frames = [
      { type: "run_started", data: { run_id: "run-live" } },
      { type: "plain", data: "browser_navigate is mentioned here" },
      { type: "plain", chain_type: "tool_call", data: JSON.stringify(tool) },
      {
        type: "plain",
        chain_type: "tool_call_result",
        data: JSON.stringify({ id: "t1", result: "ok" }),
      },
      { type: "end", data: "", streaming: false },
    ]
      .map((payload) => `data: ${JSON.stringify(payload)}\n\n`)
      .join("");
    vi.mocked(fetchWithAuth).mockImplementation(async (url) =>
      String(url).includes("system-stream")
        ? new Response(null, { status: 403 })
        : new Response(frames, {
            headers: { "content-type": "text/event-stream" },
          }),
    );
    const onToolCall = vi.fn();
    const messages = useMessages({ currentSessionId: ref("s1"), onToolCall });
    await messages.loadSessionMessages("s1");
    expect(onToolCall).not.toHaveBeenCalled();
    const parts = [{ type: "plain", text: "browse" }];
    const exchange = messages.createLocalExchange({
      sessionId: "s1",
      messageId: "m-live",
      parts,
    });
    messages.sendMessageStream({
      sessionId: "s1",
      messageId: "m-live",
      parts,
      transport: "sse",
      ...exchange,
    });
    await vi.waitFor(() =>
      expect(onToolCall).toHaveBeenCalledWith("s1", tool, "run-live"),
    );
    await vi.waitFor(() => expect(messages.sending.value).toBe(false));
    expect(onToolCall).toHaveBeenCalledOnce();
  });
});

describe("Agent message resume provenance", () => {
  it("restores the accepted Agent input before its reply and keeps a single copy", async () => {
    setActivePinia(createPinia());
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(fetchWithAuth).mockImplementation(async (url) =>
      String(url).includes("system-stream")
        ? new Response(null, { status: 403 })
        : new Response(
            new ReadableStream({
              start(value) {
                controller = value;
              },
            }),
            { headers: { "content-type": "text/event-stream" } },
          ),
    );
    const messages = useMessages({ currentSessionId: ref("s1") });
    const parts = [{ type: "plain", text: "human initial" }];
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
    await vi.waitFor(() => expect(controller).toBeDefined());
    const source = {
      kind: "agent",
      account_id: "owner",
      login_name: "alice",
      session_id: "source",
      display_name: "Research",
    };
    const input = {
      id: 10,
      content: {
        type: "user",
        message: [{ type: "plain", text: "Agent reply" }],
        agent_source: source,
      },
    };
    const snapshot = {
      type: "run_snapshot",
      data: {
        run_id: "r1",
        status: "running",
        input_message: input,
        content: { type: "bot", message: [] },
      },
    };
    const push = (value: unknown) =>
      controller.enqueue(
        new TextEncoder().encode(`data: ${JSON.stringify(value)}\n\n`),
      );
    push(snapshot);
    push(snapshot);
    await vi.waitFor(() =>
      expect(
        messages.messagesBySession.s1.filter((r) => r.id === 10),
      ).toHaveLength(1),
    );
    const records = messages.messagesBySession.s1;
    expect(records.find((r) => r.id === 10)?.content.agent_source).toEqual(
      source,
    );
    expect(records.findIndex((r) => r.id === 10)).toBeLessThan(
      records.indexOf(exchange.botRecord),
    );
    push({ type: "end" });
    controller.close();
  });
});
