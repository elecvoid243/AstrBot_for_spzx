import { ref, computed } from "vue";
import { useRouter } from "vue-router";
import { chatApi, configRouteApi } from "@/api/v1";
import {
  buildWebchatUmoDetails,
  getStoredSelectedChatConfigId,
} from "@/utils/chatConfigBinding";

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

export function useSessions(chatboxMode: boolean = false) {
  const router = useRouter();
  const sessions = ref<Session[]>([]);
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

  const getCurrentSession = computed(() => {
    if (!currSessionId.value) return null;
    return sessions.value.find((s) => s.session_id === currSessionId.value);
  });

  async function getSessions() {
    try {
      const response = await chatApi.listSessions();
      sessions.value = response.data.data;
    } catch (err: any) {
      if (err.response?.status === 401) {
        router.push("/auth/login?redirect=/chatbox");
      }
      console.error(err);
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
        const sessionId = response.data.data.session_id;
        const platformId = response.data.data.platform_id;

        currSessionId.value = sessionId;

        if (
          selectedConfigId &&
          selectedConfigId !== "default" &&
          platformId === "webchat"
        ) {
          try {
            const umoDetails = buildWebchatUmoDetails(sessionId, false);
            await configRouteApi.upsert(umoDetails.umo, {
              config_id: selectedConfigId,
            });
          } catch (err) {
            console.error("Failed to bind config to session", err);
          }
        }

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

  async function deleteSession(sessionId: string) {
    try {
      await chatApi.deleteSession(sessionId);
      await getSessions();
      currSessionId.value = "";
      selectedSessions.value = [];
    } catch (err) {
      console.error(err);
    }
  }

  async function getArchivedSessions(params?: {
    page?: number;
    page_size?: number;
    search?: string;
  }): Promise<ArchivedSessionsPayload> {
    try {
      const response = await chatApi.listArchivedSessions(params);
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
      return { items, pagination };
    } catch (err) {
      console.error(err);
      return {
        items: [],
        pagination: {
          page: 1,
          page_size: params?.page_size ?? 20,
          total: 0,
          total_pages: 1,
        },
      };
    }
  }

  /** Archive or restore a session; refreshes both lists afterwards.
   * Returns true on success so the caller can decide about navigation /
   * project list refreshes. */
  async function setSessionArchived(
    sessionId: string,
    archived: boolean,
  ): Promise<boolean> {
    try {
      const response = archived
        ? await chatApi.archiveSession(sessionId)
        : await chatApi.unarchiveSession(sessionId);
      if (response.data?.status !== "ok") {
        console.error(
          response.data?.message || "Failed to update archive state",
        );
        return false;
      }
      await getSessions();
      await getArchivedSessions();
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
      if (response.data?.status !== "ok") {
        console.error(
          response.data?.message || "Failed to update star state",
        );
        return false;
      }
      const session = sessions.value.find((s) => s.session_id === sessionId);
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
      await chatApi.updateSession(editingSessionId.value, {
        display_name: trimmedTitle,
      });

      // 更新本地会话标题
      const session = sessions.value.find(
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
    const session = sessions.value.find((s) => s.session_id === sessionId);
    if (session) {
      session.display_name = title;
    }
  }

  return {
    sessions,
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
    newSession,
    deleteSession,
    batchDeleteSessions,
    getArchivedSessions,
    setSessionArchived,
    setSessionStarred,
    showEditTitleDialog,
    saveTitle,
    updateSessionTitle,
  };
}
