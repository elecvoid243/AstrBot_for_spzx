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
import { useMessages } from "../useMessages";

describe("live tool-call notifications", () => {
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
