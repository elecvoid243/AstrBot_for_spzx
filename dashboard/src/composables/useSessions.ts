import {
  accountSessionRevision,
  assertCurrentAccount,
} from "@/utils/accountSession";
import { ref, computed, watch, toValue, type MaybeRef } from "vue";
import type { RunConversationSummary } from "@/api/generated/openapi-v1";
import { useRouter } from "vue-router";
import { chatApi } from "@/api/v1";
import { sessionRowKey } from "@/utils/runConversation";
import { getStoredSelectedChatConfigId } from "@/utils/chatConfigBinding";

export interface Session {
  session_id: string;
  display_name: string | null;
  updated_at: string;
  platform_id: string;
  creator: string;
  is_group: number;
  created_at: string;
  /** Present when this session is a branch: points to the source session. */
  branch_source: { session_id: string; message_id: number } | null;
  /** Direct child sessions branched from this session. */
  branches: Array<{ session_id: string; display_name: string | null }>;
  /** 2026-09-18 (elecvoid243): user star from the sidebar context menu,
   * persisted on the server (platform_sessions.starred). Rendered as a
   * leading marker so a starred conversation is quick to find. */
  starred?: boolean;
  session_type?: "workflow_run";
  run_conversation?: RunConversationSummary;
}

/** Archived session: same shape as Session plus project membership, so the
 * archive section can label project members and restore into the right
 * project. */
export interface ArchivedSession extends Session {
  project_id: string | null;
  project_title: string | null;
  project_emoji: string | null;
}

export interface ArchivedPagination {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ArchivedSessionsPayload {
  items: ArchivedSession[];
  pagination: ArchivedPagination;
}

/** Sidebar page size: initial load and each scroll-to-edge increment. */
const SESSION_PAGE_SIZE = 50;
const MAX_SESSION_PAGE_SIZE = 200;

export function useSessions(
  chatboxMode = false,
  includeRunConversations: MaybeRef<boolean> = false,
) {
  const router = useRouter();
  const accountRevision = accountSessionRevision.value;
  const sessions = ref<Session[]>([]);
  const sessionCursor = ref<string | null>(null);
  const sessionsHasMore = computed(() => sessionCursor.value !== null);
  const sessionsLoadingMore = ref(false);
  let sessionListQueue = Promise.resolve();
  // Set once the archive dialog has fetched its first page; archive
  // refreshes stay dormant until then so opening ChatUI never queries it.
  const archivedLoaded = ref(false);
  const archivedSessions = ref<ArchivedSession[]>([]);
  const archivedPagination = ref<ArchivedPagination>({
    page: 1,
    page_size: 20,
    total: 0,
    total_pages: 1,
  });
  const selectedSessions = ref<string[]>([]);
  const currSessionId = ref("");
  const pendingSessionId = ref<string | null>(null);
  // 编辑标题相关
  const editTitleDialog = ref(false);
  const editingTitle = ref("");
  const editingSessionId = ref("");

  const ordinarySessions = computed(() =>
    sessions.value.filter((session) => session.session_type !== "workflow_run"),
  );
  const getCurrentSession = computed(() => {
    if (!currSessionId.value) return null;
    return ordinarySessions.value.find((s) => s.session_id === currSessionId.value);
  });

  /** Serialize list updates so a refresh preserves rows that just finished loading. */
  function getSessions({ append = false }: { append?: boolean } = {}) {
    sessionListQueue = sessionListQueue
      .then(async () => {
        assertCurrentAccount(accountRevision);
        let replace = !append;
        let cursor = append ? sessionCursor.value : "start";
        let target = append
          ? SESSION_PAGE_SIZE
          : Math.max(SESSION_PAGE_SIZE, sessions.value.length);
        const items: Session[] = [];

        while (cursor && items.length < target) {
          const response = await chatApi.listSessions({
            cursor,
            ...(toValue(includeRunConversations) ? { include_run_conversations: true } : {}),
            page_size: Math.min(MAX_SESSION_PAGE_SIZE, target - items.length),
          });
          assertCurrentAccount(accountRevision);
          if (response.data?.status !== "ok") {
            throw new Error(response.data?.message || "Sessions are unavailable");
          }
          const page = response.data.data;
          if (!Array.isArray(page?.items) || page.next_cursor === cursor) {
            throw new Error("Invalid session list page");
          }
          if (page.expired) {
            if (replace) throw new Error("Session list expired during refresh");
            replace = true;
            cursor = "start";
            target = sessions.value.length + SESSION_PAGE_SIZE;
            items.length = 0;
            continue;
          }
          items.push(...page.items);
          cursor = page.next_cursor ?? null;
        }
        if (replace) {
          sessions.value = items;
        } else {
          const known = new Set(sessions.value.map(sessionRowKey));
          sessions.value = [
            ...sessions.value,
            ...items.filter((s) => !known.has(sessionRowKey(s))),
          ];
        }
        sessionCursor.value = cursor;
      })
      .catch((err) => {
        if (err.response?.status === 401) {
          void router.push("/auth/login?redirect=/chatbox");
        }
        console.error(err);
      });
    return sessionListQueue;
  }

  /** Fetch the next sidebar page; no-op while one is in flight or at the end. */
  async function loadMoreSessions() {
    if (sessionsLoadingMore.value || !sessionsHasMore.value) return;
    sessionsLoadingMore.value = true;
    try {
      await getSessions({ append: true });
    } finally {
      sessionsLoadingMore.value = false;
    }
  }

  // In-flight creation promise. When "new chat" is clicked and the user
  // immediately sends a message (or double-clicks), both callers would
  // otherwise race and create two empty sessions on the server.
  let sessionCreationPromise: Promise<string> | null = null;

  async function newSession() {
    if (sessionCreationPromise) {
      return sessionCreationPromise;
    }

    sessionCreationPromise = (async () => {
      try {
        const selectedConfigId = getStoredSelectedChatConfigId();
        const response = await chatApi.createSession();
        assertCurrentAccount(accountRevision);
        if (response.data?.status !== "ok")
          throw new Error(response.data?.message || "Failed to create session");
        const sessionId = response.data.data.session_id;
        const platformId = response.data.data.platform_id;

        if (platformId === "webchat") {
          const binding = await chatApi.setSessionConfig(sessionId, selectedConfigId);
          if (binding.data.status !== "ok") throw new Error(binding.data.message || "Configuration is unavailable");
        }
        // Publish only after the selected configuration is ready for first send.
        currSessionId.value = sessionId;

        // 更新 URL
        const basePath = chatboxMode ? "/chatbox" : "/chat";
        router.push(`${basePath}/${sessionId}`);

        // 确保新创建的会话被选中高亮
        selectedSessions.value = [sessionId];

        return sessionId;
      } catch (err) {
        console.error(err);
        throw err;
      } finally {
        sessionCreationPromise = null;
      }
    })();

    return sessionCreationPromise;
  }

  async function deleteSession(sessionId: string): Promise<boolean> {
    try {
      const response = await chatApi.deleteSession(sessionId);
      assertCurrentAccount(accountRevision);
      if (response.data?.status !== "ok")
        throw new Error(response.data?.message || "Failed to delete session");
      if (currSessionId.value === sessionId) {
        currSessionId.value = "";
        selectedSessions.value = [];
      }
      await getSessions();
      return true;
    } catch (err) {
      console.error(err);
      return false;
    }
  }

  async function getArchivedSessions(params?: {
    page?: number;
    page_size?: number;
    search?: string;
  }): Promise<ArchivedSessionsPayload> {
    try {
      const response = await chatApi.listArchivedSessions(
        toValue(includeRunConversations)
          ? { ...params, include_run_conversations: true }
          : params,
      );
      assertCurrentAccount(accountRevision);
      if (response.data?.status !== "ok")
        throw new Error(response.data?.message || "Archive is unavailable");
      const data = response.data.data || {};
      const items: ArchivedSession[] = Array.isArray(data.items)
        ? data.items
        : [];
      const pagination: ArchivedPagination = data.pagination || {
        page: 1,
        page_size: params?.page_size ?? 20,
        total: 0,
        total_pages: 1,
      };
      archivedSessions.value = items;
      archivedPagination.value = pagination;
      archivedLoaded.value = true;
      return { items, pagination };
    } catch (err) {
      console.error(err);
      throw err;
    }
  }

  /** Refresh the archived page only if it has been loaded before.
   *
   * Opening ChatUI must not query the archive; mutations that touch
   * archive state refresh it only after the user opened the dialog once.
   */
  async function refreshArchivedIfLoaded() {
    if (!archivedLoaded.value) return;
    await getArchivedSessions().catch(() => undefined);
  }

  /** Archive or restore a session; refreshes both lists afterwards.
   * Returns true on success so the caller can decide about navigation /
   * project list refreshes. */
  async function setSessionArchived(
    sessionId: string,
    archived: boolean,
    sessionType?: "workflow_run",
  ): Promise<boolean> {
    try {
      const response = sessionType === "workflow_run"
        ? await chatApi.setRunConversationArchived(sessionId, archived)
        : archived
        ? await chatApi.archiveSession(sessionId)
        : await chatApi.unarchiveSession(sessionId);
      assertCurrentAccount(accountRevision);
      if (response.data?.status !== "ok") {
        console.error(
          response.data?.message || "Failed to update archive state",
        );
        return false;
      }
      const refreshes: Promise<unknown>[] = [getSessions()];
      if (archivedLoaded.value) refreshes.push(getArchivedSessions());
      await Promise.allSettled(refreshes);
      return true;
    } catch (err) {
      console.error(err);
      return false;
    }
  }

  /** 2026-09-18 (elecvoid243): star / unstar a session (persisted on the
   * server). Unlike archiving this only flips a marker, so the local list
   * entry is patched in place — refetching would risk a sidebar flicker and
   * a reorder for a purely visual change. Returns true on success. */
  async function setSessionStarred(
    sessionId: string,
    starred: boolean,
  ): Promise<boolean> {
    try {
      const response = await chatApi.updateSession(sessionId, { starred });
      assertCurrentAccount(accountRevision);
      if (response.data?.status !== "ok") {
        console.error(response.data?.message || "Failed to update star state");
        return false;
      }
      const session = ordinarySessions.value.find((s) => s.session_id === sessionId);
      if (session) session.starred = starred;
      return true;
    } catch (err) {
      console.error(err);
      return false;
    }
  }

  interface BatchDeleteFailedItem {
    session_id: string;
    reason: string;
  }

  interface BatchDeleteResult {
    deleted_count: number;
    failed_count: number;
    failed_items: BatchDeleteFailedItem[];
    currentSessionDeleted: boolean;
  }

  function isBatchDeleteResponseData(data: unknown): data is {
    deleted_count: number;
    failed_count: number;
    failed_items: BatchDeleteFailedItem[];
  } {
    if (!data || typeof data !== "object") {
      return false;
    }
    const payload = data as Record<string, unknown>;
    return (
      typeof payload.deleted_count === "number" &&
      typeof payload.failed_count === "number" &&
      Array.isArray(payload.failed_items)
    );
  }

  async function batchDeleteSessions(
    sessionIds: string[],
  ): Promise<BatchDeleteResult> {
    try {
      const currentSessionId = currSessionId.value;
      const response = await chatApi.batchDeleteSessions({
        session_ids: sessionIds,
      });
      assertCurrentAccount(accountRevision);
      if (response.data?.status !== "ok") {
        throw new Error(
          response.data?.message || "Failed to batch delete sessions",
        );
      }

      const data = response.data?.data;
      if (!isBatchDeleteResponseData(data)) {
        throw new Error("Invalid batch delete response payload");
      }

      const failedItems = data.failed_items;
      const failedSessionIds = new Set(
        failedItems.map((item) => item.session_id),
      );
      const currentSessionDeleted = Boolean(
        currentSessionId &&
          sessionIds.includes(currentSessionId) &&
          !failedSessionIds.has(currentSessionId),
      );

      if (currentSessionDeleted) {
        currSessionId.value = "";
        selectedSessions.value = [];
      }
      await getSessions();

      return {
        deleted_count: data.deleted_count,
        failed_count: data.failed_count,
        failed_items: failedItems,
        currentSessionDeleted,
      };
    } catch (err) {
      console.error(err);
      throw err;
    }
  }

  function showEditTitleDialog(sessionId: string, title: string) {
    editingSessionId.value = sessionId;
    editingTitle.value = title || "";
    editTitleDialog.value = true;
  }

  async function saveTitle() {
    if (!editingSessionId.value) return;

    const trimmedTitle = editingTitle.value.trim();
    try {
      const response = await chatApi.updateSession(editingSessionId.value, {
        display_name: trimmedTitle,
      });
      assertCurrentAccount(accountRevision);
      if (response.data?.status !== "ok")
        throw new Error(response.data?.message || "Failed to rename session");

      // 更新本地会话标题
      const session = ordinarySessions.value.find(
        (s) => s.session_id === editingSessionId.value,
      );
      if (session) {
        session.display_name = trimmedTitle;
      }
      editTitleDialog.value = false;
    } catch (err) {
      console.error("重命名会话失败:", err);
    }
  }

  function updateSessionTitle(sessionId: string, title: string) {
    const session = ordinarySessions.value.find((s) => s.session_id === sessionId);
    if (session) {
      session.display_name = title;
    }
  }

  watch(
    accountSessionRevision,
    () => {
      sessions.value = [];
      sessionCursor.value = null;
      archivedLoaded.value = false;
      archivedSessions.value = [];
      selectedSessions.value = [];
      currSessionId.value = "";
      pendingSessionId.value = null;
      editTitleDialog.value = false;
      editingTitle.value = "";
      editingSessionId.value = "";
    },
    { flush: "sync" },
  );

  return {
    sessions,
    ordinarySessions,
    archivedSessions,
    archivedPagination,
    selectedSessions,
    currSessionId,
    pendingSessionId,
    editTitleDialog,
    editingTitle,
    editingSessionId,
    getCurrentSession,
    getSessions,
    sessionsHasMore,
    sessionsLoadingMore,
    loadMoreSessions,
    newSession,
    deleteSession,
    batchDeleteSessions,
    getArchivedSessions,
    refreshArchivedIfLoaded,
    setSessionArchived,
    setSessionStarred,
    showEditTitleDialog,
    saveTitle,
    updateSessionTitle,
  };
}
