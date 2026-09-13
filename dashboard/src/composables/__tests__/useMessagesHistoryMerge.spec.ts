// Author: elecvoid243
// Date: 2026-09-13
// Regression: windowed history (feat(chat): load chat history in paginated
// windows) vs the live-record re-append merge in loadSessionMessages.
//
// The merge treated every already-rendered record absent from the snapshot
// as a live record and re-appended it to the END of the list. Before
// windowing the snapshot covered the full history, so that held; with the
// newest-50 window it also matched the older pages loaded via
// loadOlderMessages — switching sessions (or a resume-completion reload)
// then moved earlier messages below later ones until a full page refresh.
//
// The merge must only keep genuinely live records: temp-id bubbles
// (`local-*`, `active-run-*`) and persisted records that postdate the
// snapshot fetch. Numeric ids at or below the snapshot window belong to the
// snapshot itself or to older pages the window reset drops.

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

function sessionPayload(history: unknown[], totalMessages: number, hasMore: boolean) {
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

describe("useMessages windowed history merge", () => {
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

  it("drops older pages on snapshot reload instead of re-appending them below newer messages", async () => {
    api.getSession.mockResolvedValue(
      sessionPayload([6, 7, 8, 9, 10].map(record), 10, true),
    );
    api.getHistory.mockResolvedValue(
      historyPayload([1, 2, 3, 4, 5].map(record), false),
    );
    const composable = createComposable();

    await composable.loadSessionMessages("s1");
    await composable.loadOlderMessages("s1");
    expect(loadedIds(composable.messagesBySession["s1"])).toEqual([
      "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
    ]);

    // Session switch-back / resume-completion reload: the snapshot still
    // covers ids 6..10. The older page must be dropped, not re-appended.
    await composable.loadSessionMessages("s1");
    expect(loadedIds(composable.messagesBySession["s1"])).toEqual([
      "6", "7", "8", "9", "10",
    ]);
  });

  it("keeps in-flight temp-id records across a snapshot reload", async () => {
    api.getSession.mockResolvedValue(
      sessionPayload([6, 7, 8, 9, 10].map(record), 10, true),
    );
    const composable = createComposable();

    await composable.loadSessionMessages("s1");
    composable.createLocalExchange({
      sessionId: "s1",
      messageId: "m1",
      parts: [{ type: "plain", text: "hello" }],
    });
    await composable.loadSessionMessages("s1");

    const result = loadedIds(composable.messagesBySession["s1"]);
    expect(result.slice(0, 5)).toEqual(["6", "7", "8", "9", "10"]);
    expect(result.slice(5)).toEqual(["local-user-m1", "local-bot-m1"]);
  });

  it("keeps persisted records that postdate the snapshot fetch", async () => {
    api.getSession.mockResolvedValue(
      sessionPayload([6, 7, 8, 9, 10].map(record), 10, true),
    );
    const composable = createComposable();

    await composable.loadSessionMessages("s1");
    // A bot record that received its real id via message_saved while the
    // snapshot request was already in flight — newer than everything in it.
    composable.messagesBySession["s1"].push(record(11));
    await composable.loadSessionMessages("s1");

    expect(loadedIds(composable.messagesBySession["s1"])).toEqual([
      "6", "7", "8", "9", "10", "11",
    ]);
  });
});
