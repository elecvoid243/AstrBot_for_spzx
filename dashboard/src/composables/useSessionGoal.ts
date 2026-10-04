import { computed, ref, watch, type Ref } from "vue";
import { chatApi } from "@/api/v1";
import type { SessionGoalState } from "@/api/v1";

/**
 * useSessionGoal — per-session cache of the kernel standing-goal state,
 * rendered by the app-bar Goal button and the GoalSidebar drawer.
 *
 * State flows push-first: the backend emits `goal_state_changed` over the
 * webchat system stream on every mutation, and useMessages dispatches it to
 * applyPushedGoal. The GET endpoint is only the cold-start path (entering a
 * session whose state has never been fetched).
 *
 * Cache semantics:
 * - value[sid] = state   → known goal (button shows)
 * - value[sid] = null    → known absent
 * - key missing          → not fetched yet; session switch triggers a fetch
 */
export function useSessionGoal(currentSessionId: Ref<string | undefined>) {
  const goalBySession = ref<Record<string, SessionGoalState | null>>({});
  const inflight = new Set<string>();

  /** Goal of the active session, or null when absent/unknown. */
  const currentGoal = computed<SessionGoalState | null>(() => {
    const sid = currentSessionId.value;
    if (!sid) return null;
    return goalBySession.value[sid] ?? null;
  });

  /** Apply a pushed goal_state_changed payload (authoritative). */
  function applyPushedGoal(sessionId: string, goal: SessionGoalState | null) {
    if (!sessionId) return;
    goalBySession.value = {
      ...goalBySession.value,
      [sessionId]: goal,
    };
  }

  /** Fetch and cache the goal state for one session (dedup per session). */
  async function refreshGoal(sessionId: string) {
    if (!sessionId || inflight.has(sessionId)) return;
    inflight.add(sessionId);
    try {
      const response = await chatApi.getSessionGoal(sessionId);
      applyPushedGoal(sessionId, response.data?.data?.goal ?? null);
    } catch (error) {
      // Keep the previous cached state; the next push or session switch
      // re-converges the cache.
      console.error("Failed to load session goal:", error);
    } finally {
      inflight.delete(sessionId);
    }
  }

  // Session switch: fetch when entering a session with no cached state.
  watch(
    currentSessionId,
    (sid) => {
      if (sid && !(sid in goalBySession.value)) {
        void refreshGoal(sid);
      }
    },
    { immediate: true },
  );

  return { currentGoal, applyPushedGoal, refreshGoal };
}
