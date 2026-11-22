//
// Sidebar session loading: the first page is 50 rows, scroll-to-edge
// appends the next page, a refresh keeps the rows already on screen, and
// the archive list is only fetched after the user opened the dialog.

import { effectScope, type EffectScope } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/api/v1", () => ({
  chatApi: {
    listSessions: vi.fn(),
    listArchivedSessions: vi.fn(),
    archiveSession: vi.fn(),
    unarchiveSession: vi.fn(),
    updateSession: vi.fn(),
    deleteSession: vi.fn(),
    createSession: vi.fn(),
    setSessionConfig: vi.fn(),
    batchDeleteSessions: vi.fn(),
  },
}));

import { chatApi } from "@/api/v1";
import { useSessions, type Session } from "../useSessions";

let scope: EffectScope;

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(console, "error").mockImplementation(() => {});
  scope = effectScope();
});

afterEach(() => {
  scope.stop();
  vi.restoreAllMocks();
});

function rows(count: number, offset = 0): Session[] {
  return Array.from({ length: count }, (_, index) => ({
    session_id: `s${offset + index}`,
    display_name: `会话 ${offset + index}`,
    updated_at: "2026-11-20T00:00:00Z",
    platform_id: "webchat",
    creator: "alice",
    is_group: 0,
    created_at: "2026-11-20T00:00:00Z",
    branch_source: null,
    branches: [],
  }));
}

function sessionsOk(items: Session[], nextCursor: string | null = null, expired = false) {
  return { data: { status: "ok", data: { items, next_cursor: nextCursor, expired } } } as never;
}

function archivedOk(items: unknown[] = []) {
  return {
    data: {
      status: "ok",
      data: {
        items,
        pagination: { page: 1, page_size: 20, total: items.length, total_pages: 1 },
      },
    },
  } as never;
}

describe("useSessions sidebar pagination", () => {
  it("loads the first 50 rows and marks the list as having more", async () => {
    vi.mocked(chatApi.listSessions).mockResolvedValue(sessionsOk(rows(50), "snapshot:50"));
    const state = scope.run(() => useSessions())!;

    await state.getSessions();

    expect(chatApi.listSessions).toHaveBeenCalledWith({
      cursor: "start",
      page_size: 50,
    });
    expect(state.sessions.value).toHaveLength(50);
    expect(state.sessionsHasMore.value).toBe(true);
  });

  it("appends the next page and stops on a short page", async () => {
    vi.mocked(chatApi.listSessions)
      .mockResolvedValueOnce(sessionsOk(rows(50), "snapshot:50"))
      .mockResolvedValueOnce(sessionsOk(rows(20, 50)));
    const state = scope.run(() => useSessions())!;

    await state.getSessions();
    await state.loadMoreSessions();

    expect(chatApi.listSessions).toHaveBeenLastCalledWith({
      cursor: "snapshot:50",
      page_size: 50,
    });
    expect(state.sessions.value).toHaveLength(70);
    expect(state.sessionsHasMore.value).toBe(false);
  });

  it("requests run conversations across Dashboard pages while ChatBox keeps ordinary sessions", async () => {
    const legacy = { ...rows(1)[0], session_id: "workflow-run-example" };
    const run: Session = {
      ...legacy,
      session_type: "workflow_run",
      run_conversation: { run_id: "example", asset_id: "asset", asset_version: 1, status: "running" },
    };
    vi.mocked(chatApi.listSessions)
      .mockResolvedValueOnce(sessionsOk([legacy, ...rows(49, 1)], "snapshot:50"))
      .mockResolvedValueOnce(sessionsOk([run]))
      .mockResolvedValueOnce(sessionsOk([legacy]));
    const dashboard = scope.run(() => useSessions(false, true))!;
    const chatbox = scope.run(() => useSessions(true))!;

    await dashboard.getSessions();
    await dashboard.loadMoreSessions();
    await chatbox.getSessions();

    expect(vi.mocked(chatApi.listSessions).mock.calls.map(([params]) => params)).toEqual([
      { cursor: "start", page_size: 50, include_run_conversations: true },
      { cursor: "snapshot:50", page_size: 50, include_run_conversations: true },
      { cursor: "start", page_size: 50 },
    ]);
    expect(dashboard.sessions.value.at(-1)).toEqual(run);
    expect(dashboard.ordinarySessions.value[0]).toEqual(legacy);
    expect(chatbox.sessions.value).toEqual([legacy]);
  });

  it("refreshes the loaded rows in batches within the API limit", async () => {
    vi.mocked(chatApi.listSessions)
      .mockResolvedValueOnce(sessionsOk(rows(200), "fresh:200"))
      .mockResolvedValueOnce(sessionsOk(rows(50, 200), "fresh:250"))
      .mockResolvedValueOnce(sessionsOk(rows(12, 250)));
    const state = scope.run(() => useSessions())!;
    state.sessions.value = rows(250);

    await state.getSessions();
    expect(chatApi.listSessions).toHaveBeenNthCalledWith(1, { cursor: "start", page_size: 200 });
    expect(chatApi.listSessions).toHaveBeenNthCalledWith(2, { cursor: "fresh:200", page_size: 50 });
    expect(state.sessions.value).toHaveLength(250);
    await state.loadMoreSessions();
    expect(chatApi.listSessions).toHaveBeenLastCalledWith({ cursor: "fresh:250", page_size: 50 });
    expect(state.sessions.value).toHaveLength(262);
  });

  it("refreshes after an in-flight append without losing the new rows", async () => {
    let finishAppend!: (response: ReturnType<typeof sessionsOk>) => void;
    vi.mocked(chatApi.listSessions)
      .mockResolvedValueOnce(sessionsOk(rows(50), "snapshot:50"))
      .mockReturnValueOnce(new Promise((resolve) => { finishAppend = resolve; }))
      .mockResolvedValueOnce(sessionsOk(rows(100)));
    const state = scope.run(() => useSessions())!;
    await state.getSessions();
    const append = state.loadMoreSessions();
    await Promise.resolve();
    const refresh = state.getSessions();
    expect(chatApi.listSessions).toHaveBeenCalledTimes(2);
    finishAppend(sessionsOk(rows(50, 50)));
    await Promise.all([append, refresh]);
    expect(chatApi.listSessions).toHaveBeenLastCalledWith({ cursor: "start", page_size: 100 });
    expect(state.sessions.value).toHaveLength(100);
  });

  it("restarts an expired snapshot and preserves the loaded depth", async () => {
    vi.mocked(chatApi.listSessions)
      .mockResolvedValueOnce(sessionsOk(rows(50), "expired:50"))
      .mockResolvedValueOnce(sessionsOk([], null, true))
      .mockResolvedValueOnce(sessionsOk(rows(80, 10)));
    const state = scope.run(() => useSessions())!;
    await state.getSessions();
    await state.loadMoreSessions();
    expect(chatApi.listSessions).toHaveBeenLastCalledWith({ cursor: "start", page_size: 100 });
    expect(state.sessions.value).toHaveLength(80);
    expect(state.sessions.value[0].session_id).toBe("s10");
  });
});

describe("useSessions archive laziness", () => {
  it("does not query archived sessions until the dialog loads them", async () => {
    vi.mocked(chatApi.listSessions).mockResolvedValue(sessionsOk([]));
    vi.mocked(chatApi.archiveSession).mockResolvedValue(sessionsOk([]));
    vi.mocked(chatApi.listArchivedSessions).mockResolvedValue(archivedOk());
    const state = scope.run(() => useSessions())!;

    await state.setSessionArchived("s1", true);
    await state.refreshArchivedIfLoaded();
    expect(chatApi.listArchivedSessions).not.toHaveBeenCalled();

    // Opening the archive dialog fetches it once; later mutations refresh it.
    await state.getArchivedSessions();
    expect(chatApi.listArchivedSessions).toHaveBeenCalledTimes(1);

    vi.mocked(chatApi.unarchiveSession).mockResolvedValue(sessionsOk([]));
    await state.setSessionArchived("s1", false);
    expect(chatApi.listArchivedSessions).toHaveBeenCalledTimes(2);
  });
});
