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

function sessionsOk(items: Session[]) {
  return { data: { status: "ok", data: items } } as never;
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
    vi.mocked(chatApi.listSessions).mockResolvedValue(sessionsOk(rows(50)));
    const state = scope.run(() => useSessions())!;

    await state.getSessions();

    expect(chatApi.listSessions).toHaveBeenCalledWith({
      page: 1,
      page_size: 50,
    });
    expect(state.sessions.value).toHaveLength(50);
    expect(state.sessionsHasMore.value).toBe(true);
  });

  it("appends the next page and stops on a short page", async () => {
    vi.mocked(chatApi.listSessions)
      .mockResolvedValueOnce(sessionsOk(rows(50)))
      .mockResolvedValueOnce(sessionsOk(rows(20, 50)));
    const state = scope.run(() => useSessions())!;

    await state.getSessions();
    await state.loadMoreSessions();

    expect(chatApi.listSessions).toHaveBeenLastCalledWith({
      page: 2,
      page_size: 50,
    });
    expect(state.sessions.value).toHaveLength(70);
    expect(state.sessionsHasMore.value).toBe(false);
  });

  it("refreshes every loaded row in one request", async () => {
    vi.mocked(chatApi.listSessions)
      .mockResolvedValueOnce(sessionsOk(rows(50)))
      .mockResolvedValueOnce(sessionsOk(rows(20, 50)))
      .mockResolvedValueOnce(sessionsOk(rows(70)));
    const state = scope.run(() => useSessions())!;

    await state.getSessions();
    await state.loadMoreSessions();
    await state.getSessions();

    expect(chatApi.listSessions).toHaveBeenLastCalledWith({
      page: 1,
      page_size: 70,
    });
    expect(state.sessions.value).toHaveLength(70);
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
