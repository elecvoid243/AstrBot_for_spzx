// Author: elecvoid243
// Date: 2026-10-08
//
// LLM request injection trace dispatch: the primary run stream must attach
// the payload to the bot record's content (the gutter marker's source), and
// the system stream must drop it so the wire JSON never renders as chat text.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

vi.mock("@/api/v1", () => ({
  chatApi: {
    sendStreamUrl: vi.fn(() => "/api/v1/chat"),
    getSession: vi.fn(),
    getHistory: vi.fn(),
    getMarkers: vi.fn(),
    systemStreamUrl: vi.fn(() => "/api/v1/chat/sessions/s1/system-stream"),
    resumeRunStreamUrl: vi.fn(() => "/api/v1/chat/runs/run-1/stream"),
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

const NON_SSE = {
  ok: false,
  headers: { get: () => null },
} as unknown as Response;

const ITEM = {
  plugin: "tc_memory",
  handler: "decorate_llm_req",
  changes: [
    { field: "system_prompt", delta: 412, lossy: false, preview: "## 长期记忆" },
  ],
};

const TRACE_PAYLOAD = {
  type: "llm_request_injections",
  data: { items: [ITEM] },
  message_id: "m1",
};

function recordText(parts: Array<{ type?: string; text?: string }> | undefined) {
  return (parts || [])
    .filter((part) => part.type === "plain")
    .map((part) => part.text || "")
    .join("");
}

describe("useMessages llm_request_injections dispatch", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(chatApi.getSession).mockResolvedValue({
      data: {
        data: {
          history: [],
          threads: [],
          total_messages: 0,
          has_more: false,
          active_runs: [],
        },
      },
    } as never);
    vi.mocked(chatApi.getMarkers).mockResolvedValue({
      data: { data: { markers: [], total_messages: 0 } },
    } as never);
  });

  it("attaches the trace to the bot record on the primary stream", async () => {
    vi.mocked(fetchWithAuth).mockImplementation(async (url: unknown) =>
      String(url).includes("system-stream")
        ? NON_SSE
        : sseResponse(
            sseFrame(TRACE_PAYLOAD) +
              sseFrame({
                type: "end",
                data: "",
                streaming: false,
                message_id: "m1",
              }),
          ),
    );

    const composable = useMessages({ currentSessionId: ref("s1") });
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

    await vi.waitFor(() =>
      expect(botRecord.content.llmRequestInjections?.items[0].plugin).toBe(
        "tc_memory",
      ),
    );
    // The wire JSON must never become a text part.
    expect(recordText(botRecord.content.message)).not.toContain("tc_memory");
  });

  it("drops the trace on the system stream instead of rendering JSON", async () => {
    let systemCalls = 0;
    vi.mocked(fetchWithAuth).mockImplementation(async (url: unknown) => {
      if (!String(url).includes("system-stream")) return NON_SSE;
      systemCalls += 1;
      return systemCalls === 1
        ? sseResponse(
            // Real wire shape: `type: "plain"` plus the chain_type stamp
            // (webchat_event._send's Json branch), which is exactly the
            // payload the system leaf would render as text without the drop.
            sseFrame({
              type: "plain",
              chain_type: "llm_request_injections",
              data: JSON.stringify({ items: [ITEM] }),
              streaming: false,
              message_id: "m2",
            }) +
              sseFrame({
                type: "plain",
                data: "visible text",
                streaming: false,
                message_id: "m3",
              }),
          )
        : NON_SSE;
    });

    const composable = useMessages({ currentSessionId: ref("s1") });

    await vi.waitFor(() => {
      const records = composable.messagesBySession["s1"] ?? [];
      expect(
        records.some((r) => recordText(r.content?.message).includes("visible text")),
      ).toBe(true);
    });

    for (const record of composable.messagesBySession["s1"] ?? []) {
      expect(recordText(record.content?.message)).not.toContain("tc_memory");
    }
  });
});
