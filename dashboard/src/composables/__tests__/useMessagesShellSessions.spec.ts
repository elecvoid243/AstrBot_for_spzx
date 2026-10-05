// Author: elecvoid243
// Date: 2026-10-05
// System-stream dispatch for `shell_sessions_changed`: the payload is a
// session-scoped authoritative snapshot (like goal_state_changed), so it
// must route to the onShellSessionsChanged callback and never render as a
// message record.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

vi.mock("@/api/v1", () => ({
  chatApi: {
    getSession: vi.fn(),
    getHistory: vi.fn(),
    getMarkers: vi.fn(),
    systemStreamUrl: vi.fn(() => "/api/v1/chat/sessions/s1/system-stream"),
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

const SESSION_ITEM = {
  session_id: "sh_1",
  pid: 100,
  status: "running",
  exit_code: null,
  started_at: 1759656000.0,
  sandboxed: false,
  unread_output_bytes: 42,
};

describe("useMessages shell_sessions_changed dispatch", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("routes the snapshot to onShellSessionsChanged without rendering a message", async () => {
    fetchMock
      .mockResolvedValueOnce(
        sseResponse([
          {
            type: "shell_sessions_changed",
            data: { sessions: [SESSION_ITEM] },
          },
        ]),
      )
      // Second reconnect attempt: non-SSE response stops the retry loop.
      .mockResolvedValue({
        ok: false,
        headers: { get: () => null },
      } as unknown as Response);

    const currentSessionId = ref("s1");
    const onShellSessionsChanged = vi.fn();
    const composable = useMessages({
      currentSessionId,
      onShellSessionsChanged,
    });

    await vi.waitFor(() =>
      expect(onShellSessionsChanged).toHaveBeenCalledWith("s1", [SESSION_ITEM]),
    );

    // The payload must not surface as a bot record in the message list.
    expect(composable.messagesBySession["s1"] ?? []).toEqual([]);
  });
});
