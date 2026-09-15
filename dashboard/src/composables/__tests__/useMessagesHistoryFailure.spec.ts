// Author: elecvoid243
// Date: 2026-09-15
// Windowed history: failure handling and the opt-in window contract.
//
// "Load older messages" used to swallow errors into console.error, so a broken
// page was re-requested on every scroll event (the top-of-list trigger fires
// per scroll) and the user had no way to retry. A failed page must now pause
// the automatic path until the user retries explicitly.
//
// The same suite pins the client half of the opt-in window: the ChatUI asks
// for HISTORY_PAGE_SIZE records, because the server default stays at the legacy
// full page for callers that do not page (v1 dashboard query, archived
// previews, third-party clients).

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

vi.mock("@/api/v1", () => ({
  chatApi: {
    getSession: vi.fn(),
    getHistory: vi.fn(),
    getMarkers: vi.fn(),
  },
  fileApi: {
    contentUrl: vi.fn(() => ""),
  },
}));
vi.mock("@/api/http", () => ({
  fetchWithAuth: vi.fn(),
}));

import { chatApi } from "@/api/v1";
import { useMessages } from "../useMessages";

function record(id: number) {
  return {
    id,
    content: {
      type: "bot",
      message: [{ type: "plain", text: `m${id}` }],
    },
  };
}

function sessionPayload(
  history: unknown[],
  totalMessages: number,
  hasMore: boolean,
) {
  return {
    data: {
      data: {
        history,
        threads: [],
        total_messages: totalMessages,
        has_more: hasMore,
        active_runs: [],
      },
    },
  };
}

function historyPayload(history: unknown[], hasMore: boolean) {
  return { data: { data: { history, threads: [], has_more: hasMore } } };
}

function loadedIds(messages: Array<{ id?: unknown }> | undefined) {
  return (messages || []).map((m) => String(m.id));
}

describe("useMessages windowed history failures", () => {
  const currentSessionId = ref("s1");
  let api: {
    getSession: ReturnType<typeof vi.fn>;
    getHistory: ReturnType<typeof vi.fn>;
    getMarkers: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    api = vi.mocked(chatApi, true) as never;
    api.getMarkers.mockResolvedValue({
      data: { data: { markers: [], total_messages: 0 } },
    });
  });

  function createComposable() {
    return useMessages({ currentSessionId });
  }

  it("asks for the newest window explicitly so the server default can stay legacy", async () => {
    api.getSession.mockResolvedValue(
      sessionPayload([6, 7, 8, 9, 10].map(record), 10, true),
    );
    const composable = createComposable();

    await composable.loadSessionMessages("s1");

    expect(api.getSession).toHaveBeenCalledWith("s1", { limit: 50 });
    expect(composable.historyPagingBySession["s1"].hasMore).toBe(true);
    expect(composable.historyPagingBySession["s1"].error).toBeNull();
  });

  it("pauses automatic loading after a failure and resumes on an explicit retry", async () => {
    api.getSession.mockResolvedValue(
      sessionPayload([6, 7, 8, 9, 10].map(record), 10, true),
    );
    const composable = createComposable();
    await composable.loadSessionMessages("s1");

    api.getHistory.mockRejectedValueOnce(new Error("boom"));
    await composable.loadOlderMessages("s1");

    const paging = composable.historyPagingBySession["s1"];
    expect(paging.error).toBe("boom");
    expect(api.getHistory).toHaveBeenCalledTimes(1);

    // Automatic path (scroll-to-top): stays paused instead of hammering the
    // failed request on every scroll event.
    await composable.loadOlderMessages("s1");
    expect(api.getHistory).toHaveBeenCalledTimes(1);

    // Explicit retry from the button clears the error and loads the page.
    api.getHistory.mockResolvedValueOnce(
      historyPayload([1, 2, 3, 4, 5].map(record), false),
    );
    await composable.loadOlderMessages("s1", { retry: true });

    expect(api.getHistory).toHaveBeenCalledTimes(2);
    expect(paging.error).toBeNull();
    expect(loadedIds(composable.messagesBySession["s1"])).toEqual([
      "1",
      "2",
      "3",
      "4",
      "5",
      "6",
      "7",
      "8",
      "9",
      "10",
    ]);
  });

  it("keeps the already loaded window intact when a page fails", async () => {
    api.getSession.mockResolvedValue(
      sessionPayload([6, 7, 8, 9, 10].map(record), 10, true),
    );
    const composable = createComposable();
    await composable.loadSessionMessages("s1");

    api.getHistory.mockRejectedValueOnce(new Error("network down"));
    await composable.loadOlderMessages("s1");

    expect(loadedIds(composable.messagesBySession["s1"])).toEqual([
      "6",
      "7",
      "8",
      "9",
      "10",
    ]);
    // The cursor still points at the oldest loaded record, so a retry
    // re-requests the same page instead of skipping it.
    expect(composable.historyPagingBySession["s1"].oldestLoadedId).toBe(6);
    expect(composable.historyOffsetBySession["s1"]).toBe(5);
  });
});
