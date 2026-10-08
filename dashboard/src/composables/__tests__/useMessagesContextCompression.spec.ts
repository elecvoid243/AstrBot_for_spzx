// Author: elecvoid243
// Date: 2026-10-08
//
// Live-only context compression notice on the primary chat run stream: the
// payload must route to `onContextCompression` and must never render as a
// message record (the backend accumulator already refuses to persist it, and
// the wire JSON must not leak into the live bubble either).

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

vi.mock("@/api/v1", () => ({
  chatApi: {
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

import { fetchWithAuth } from "@/api/http";
import { useMessages } from "../useMessages";

const fetchMock = vi.mocked(fetchWithAuth);

function sseResponse(payloads: unknown[]) {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const p of payloads) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(p)}\n\n`));
      }
      controller.close();
    },
  });
  return {
    ok: true,
    headers: {
      get: (name: string) =>
        name.toLowerCase() === "content-type" ? "text/event-stream" : null,
    },
    body,
  } as unknown as Response;
}

const NOTICE_PAYLOAD = {
  type: "plain",
  chain_type: "context_compression",
  data: JSON.stringify({
    strategy: "llm_compress",
    tokens_before: 123456,
    tokens_after: 45123,
  }),
  streaming: false,
  message_id: "m1",
};

describe("useMessages context_compression dispatch", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("routes the notice to onContextCompression without rendering a message", async () => {
    fetchMock
      .mockResolvedValueOnce(sseResponse([NOTICE_PAYLOAD]))
      // Second reconnect attempt: non-SSE response stops the retry loop.
      .mockResolvedValue({
        ok: false,
        headers: { get: () => null },
      } as unknown as Response);

    const currentSessionId = ref("s1");
    const onContextCompression = vi.fn();
    const composable = useMessages({
      currentSessionId,
      onContextCompression,
    });

    await vi.waitFor(() =>
      expect(onContextCompression).toHaveBeenCalledWith("s1", {
        strategy: "llm_compress",
        tokensBefore: 123456,
        tokensAfter: 45123,
      }),
    );

    // The payload must not surface as a bot record, and no part may carry
    // the raw JSON.
    const records = composable.messagesBySession["s1"] ?? [];
    expect(records).toEqual([]);
  });

  it("ignores a malformed notice instead of inventing numbers", async () => {
    fetchMock
      .mockResolvedValueOnce(
        sseResponse([{ ...NOTICE_PAYLOAD, data: "not-json" }]),
      )
      .mockResolvedValue({
        ok: false,
        headers: { get: () => null },
      } as unknown as Response);

    const currentSessionId = ref("s1");
    const onContextCompression = vi.fn();
    useMessages({ currentSessionId, onContextCompression });

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(onContextCompression).not.toHaveBeenCalled();
  });

  it("routes the notice on the primary run stream without rendering it", async () => {
    fetchMock
      // System stream: the synthetic-run registration attaches the page to
      // the run's own stream (the primary processing path).
      .mockResolvedValueOnce(
        sseResponse([
          {
            type: "run_started",
            synthetic: true,
            message_id: "run-1",
            data: { run_id: "run-1" },
          },
        ]),
      )
      // The run stream carries the compression notice.
      .mockResolvedValueOnce(sseResponse([NOTICE_PAYLOAD]))
      .mockResolvedValue({
        ok: false,
        headers: { get: () => null },
      } as unknown as Response);

    const currentSessionId = ref("s1");
    const onContextCompression = vi.fn();
    const composable = useMessages({
      currentSessionId,
      onContextCompression,
    });

    await vi.waitFor(() =>
      expect(onContextCompression).toHaveBeenCalledWith("s1", {
        strategy: "llm_compress",
        tokensBefore: 123456,
        tokensAfter: 45123,
      }),
    );

    // The live bubble must stay free of the notice's wire JSON.
    const records = composable.messagesBySession["s1"] ?? [];
    const parts = records.flatMap((record) => record.content?.message ?? []);
    expect(parts).toEqual([]);

    // Telemetry must not end the "replying" state: the answer has not
    // arrived yet, so clearing the loading flag would freeze the bubble on
    // an empty record until the first delta.
    expect(records[0]?.content?.isLoading).toBe(true);
  });
});
