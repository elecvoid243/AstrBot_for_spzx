import { computed, ref, watch, type ComputedRef, type Ref } from "vue";
import { chatApi } from "@/api/v1";
import type { ShellSessionListItem } from "@/components/chat/message_list_comps/shell_session_tools/format";

/**
 * useShellSessions — per-session cache of managed shell sessions, rendered
 * by the app-bar ShellSessionIndicator.
 *
 * State flows push-first: the backend emits `shell_sessions_changed` over
 * the webchat system stream on every lifecycle transition (create / exit /
 * removal), and useMessages dispatches it to applyPushedShellSessions. The
 * GET endpoint is only the cold-start path (entering a session whose state
 * has never been fetched).
 *
 * Cache semantics:
 * - value[sid] = list  → known sessions (possibly empty)
 * - key missing        → not fetched yet; session switch triggers a fetch
 *
 * Author: elecvoid243 | 2026-10-05
 */
export function useShellSessions(currentSessionId: Ref<string | undefined>) {
  const sessionsBySession = ref<Record<string, ShellSessionListItem[]>>({});
  const inflight = new Set<string>();

  /** Sessions of the active session; empty when absent/unknown. */
  const currentSessions: ComputedRef<ShellSessionListItem[]> = computed(() => {
    const sid = currentSessionId.value;
    if (!sid) return [];
    return sessionsBySession.value[sid] ?? [];
  });

  const runningCount = computed(
    () => currentSessions.value.filter((s) => s.status === "running").length,
  );
  const finishedCount = computed(
    () => currentSessions.value.length - runningCount.value,
  );

  /** Apply a pushed shell_sessions_changed payload (authoritative snapshot). */
  function applyPushedShellSessions(
    sessionId: string,
    sessions: ShellSessionListItem[],
  ) {
    if (!sessionId) return;
    sessionsBySession.value = {
      ...sessionsBySession.value,
      [sessionId]: sessions,
    };
  }

  /** Fetch and cache the session list for one session (dedup per session). */
  async function refreshShellSessions(sessionId: string) {
    if (!sessionId || inflight.has(sessionId)) return;
    inflight.add(sessionId);
    try {
      const response = await chatApi.getSessionShellSessions(sessionId);
      applyPushedShellSessions(sessionId, response.data?.data?.sessions ?? []);
    } catch (error) {
      // Keep the previous cached state; the next push or session switch
      // re-converges the cache.
      console.error("Failed to load shell sessions:", error);
    } finally {
      inflight.delete(sessionId);
    }
  }

  // Session switch: fetch when entering a session with no cached state.
  watch(
    currentSessionId,
    (sid) => {
      if (sid && !(sid in sessionsBySession.value)) {
        void refreshShellSessions(sid);
      }
    },
    { immediate: true },
  );

  return {
    currentSessions,
    runningCount,
    finishedCount,
    applyPushedShellSessions,
    refreshShellSessions,
  };
}
