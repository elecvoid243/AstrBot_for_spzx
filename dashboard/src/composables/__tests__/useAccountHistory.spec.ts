import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { effectScope, ref, type EffectScope } from "vue";

vi.mock("@/stores/auth", () => ({ useAuthStore: () => ({ role: "user" }) }));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/api/v1", () => ({
  chatApi: {
    listSessions: vi.fn(),
    listArchivedSessions: vi.fn(),
    updateSession: vi.fn(),
    deleteSession: vi.fn(),
    getSession: vi.fn(),
    getHistory: vi.fn(),
    getMarkers: vi.fn(),
    regenerateMessageUrl: vi.fn(() => "/api/v1/retry"),
  },
  configRouteApi: {},
  fileApi: { contentUrl: vi.fn(() => "") },
}));
vi.mock("@/api/http", () => ({ fetchWithAuth: vi.fn() }));

import { chatApi } from "@/api/v1";
import { fetchWithAuth } from "@/api/http";
import { messageApi } from "@/api/messages";
import { resetAccountSession } from "@/utils/accountSession";
import { useSessionUnreadStore } from "@/stores/sessionUnread";
import { useMessages, type ChatRecord } from "../useMessages";
import { useSessions, type Session } from "../useSessions";

let scope: EffectScope;
beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
  scope = effectScope();
});
afterEach(() => {
  scope.stop();
  vi.restoreAllMocks();
});

const session = { session_id: "owned", display_name: "original" } as Session;
const reply = {
  id: 2,
  content: { type: "bot", message: [{ type: "plain", text: "saved reply" }] },
} as ChatRecord;

describe("account-scoped history operations", () => {
  it("keeps title, active session and selection when the server rejects a change", async () => {
    const state = scope.run(() => useSessions())!;
    state.sessions.value = [{ ...session }];
    state.currSessionId.value = "owned";
    state.selectedSessions.value = ["owned"];
    state.showEditTitleDialog("owned", "replacement");
    vi.mocked(chatApi.updateSession).mockResolvedValue({
      data: { status: "error", message: "denied" },
    } as never);
    vi.mocked(chatApi.deleteSession).mockResolvedValue({
      data: { status: "error", message: "denied" },
    } as never);
    await state.saveTitle();
    expect(state.sessions.value[0].display_name).toBe("original");
    expect(state.editTitleDialog.value).toBe(true);
    expect(await state.deleteSession("owned")).toBe(false);
    expect(state.currSessionId.value).toBe("owned");
    expect(state.selectedSessions.value).toEqual(["owned"]);
  });

  it("clears selections and ignores delayed lists and archived pages on account switch", async () => {
    const state = scope.run(() => useSessions())!;
    state.sessions.value = [{ ...session }];
    state.selectedSessions.value = ["owned"];
    let finishList!: (data: any) => void;
    let finishArchive!: (data: any) => void;
    vi.mocked(chatApi.listSessions).mockReturnValue(
      new Promise((resolve) => {
        finishList = resolve;
      }) as never,
    );
    vi.mocked(chatApi.listArchivedSessions).mockReturnValue(
      new Promise((resolve) => {
        finishArchive = resolve;
      }) as never,
    );
    const pending = Promise.allSettled([
      state.getSessions(),
      state.getArchivedSessions(),
    ]);
    resetAccountSession();
    finishList({ data: { status: "ok", data: [session] } });
    finishArchive({ data: { status: "ok", data: { items: [session] } } });
    await pending;
    expect(state.sessions.value).toEqual([]);
    expect(state.archivedSessions.value).toEqual([]);
    expect(state.selectedSessions.value).toEqual([]);
  });

  it("keeps the last archive page when the server returns an error envelope", async () => {
    const state = scope.run(() => useSessions())!;
    state.archivedSessions.value = [
      {
        ...session,
        project_id: null,
        project_title: null,
        project_emoji: null,
      },
    ];
    vi.mocked(chatApi.listArchivedSessions).mockResolvedValue({
      data: { status: "error", message: "archive unavailable" },
    } as never);
    await expect(state.getArchivedSessions()).rejects.toThrow(
      "archive unavailable",
    );
    expect(state.archivedSessions.value[0].session_id).toBe("owned");
  });

  it("ignores delayed history and markers after account switch", async () => {
    const state = scope.run(() =>
      useMessages({ currentSessionId: ref(""), accountRole: "user" }),
    )!;
    let finish!: (data: any) => void;
    vi.mocked(chatApi.getMarkers).mockResolvedValue({
      data: {
        status: "ok",
        data: { markers: [{ id: 1, snippet: "private" }] },
      },
    } as never);
    vi.mocked(chatApi.getSession).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }) as never,
    );
    const pending = state.loadSessionMessages("owned", false);
    resetAccountSession();
    finish({ data: { status: "ok", data: { history: [reply], threads: [] } } });
    await pending;
    expect(state.messagesBySession).toEqual({});
    expect(state.sessionMarkersBySession).toEqual({});
    expect(state.historyPagingBySession).toEqual({});
  });

  it("drops an in-flight marker response and older page when the account changes", async () => {
    const state = scope.run(() =>
      useMessages({ currentSessionId: ref(""), accountRole: "user" }),
    )!;
    let finishMarkers!: (data: any) => void;
    let finishOlder!: (data: any) => void;
    vi.mocked(chatApi.getSession).mockResolvedValue({
      data: {
        status: "ok",
        data: {
          history: [reply],
          threads: [],
          total_messages: 2,
          has_more: true,
        },
      },
    } as never);
    vi.mocked(chatApi.getMarkers).mockReturnValue(
      new Promise((resolve) => {
        finishMarkers = resolve;
      }) as never,
    );
    await state.loadSessionMessages("owned", false);
    vi.mocked(chatApi.getHistory).mockReturnValue(
      new Promise((resolve) => {
        finishOlder = resolve;
      }) as never,
    );
    const pending = state.loadOlderMessages("owned");
    resetAccountSession();
    finishMarkers({
      data: {
        status: "ok",
        data: { markers: [{ id: 1, snippet: "private marker" }] },
      },
    });
    finishOlder({
      data: {
        status: "ok",
        data: { history: [reply], threads: [], has_more: false },
      },
    });
    await pending;
    expect(state.messagesBySession).toEqual({});
    expect(state.sessionMarkersBySession).toEqual({});
    expect(state.historyPagingBySession).toEqual({});
  });

  it("rejects a search whose JSON finishes after switching accounts", async () => {
    let finish!: (data: any) => void;
    vi.mocked(fetchWithAuth).mockResolvedValue({
      ok: true,
      json: () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    } as never);
    const pending = messageApi.searchMessages("private");
    await vi.waitFor(() => expect(finish).toBeTypeOf("function"));
    resetAccountSession();
    finish({
      status: "ok",
      data: { results: [{ session_id: "old-account" }] },
    });
    await expect(pending).rejects.toThrow("Account changed");
  });

  it("preserves the saved reply when regeneration returns an error envelope", async () => {
    const onError = vi.fn();
    const state = scope.run(() =>
      useMessages({ currentSessionId: ref(""), accountRole: "user", onError }),
    )!;
    const original = structuredClone(reply);
    vi.mocked(fetchWithAuth).mockResolvedValue(
      new Response(JSON.stringify({ status: "error", message: "denied" }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    await state.regenerateMessage(
      "owned",
      original,
      "configured-model",
      "model-name",
      true,
      "low",
      "research",
    );
    expect(original).toEqual(reply);
    expect(onError).toHaveBeenCalled();
    expect(
      JSON.parse(vi.mocked(fetchWithAuth).mock.calls[0][1]!.body as string),
    ).toEqual({
      config_id: "research",
      thinking_effort: "low",
      selected_provider: "configured-model",
      selected_model: "model-name",
    });
  });

  it("does not retain unread targets between accounts", () => {
    const unread = useSessionUnreadStore();
    unread.markUnread("private");
    resetAccountSession();
    expect(unread.count).toBe(0);
    expect(unread.isUnread("private")).toBe(false);
    unread.markUnread("new-account");
    expect(unread.sessionIds).toEqual(["new-account"]);
  });
});
