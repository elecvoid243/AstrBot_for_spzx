import { computed, ref, watch, type Ref } from "vue";
import { chatApi } from "@/api/v1";
import type { SessionGoalState } from "@/api/v1";

/**
 * useSessionGoal — per-session cache of the kernel standing-goal state
 * (GET /chat/sessions/{id}/goal), rendered by the app-bar Goal button and
 * the GoalSidebar drawer.
 *
 * The goal state only changes at turn boundaries (the judge runs on
 * on_agent_done and /goal commands), and there is no push channel, so
 * callers drive refetches: session switch (watch below), after each run
 * stream ends, and after the user sends a message (covering
 * /goal set|pause|resume|clear typed in the composer).
 *
 * Cache semantics mirror latestTodoSnapshotBySession:
 * - value[sid] = state   → known goal (button shows)
 * - value[sid] = null    → known absent (/goal clear or never set)
 * - key missing          → not fetched yet (treated as null for display,
 *                          but a session switch triggers a fetch)
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

  /** Fetch and cache the goal state for one session (dedup per session). */
  async function refreshGoal(sessionId: string) {
    if (!sessionId || inflight.has(sessionId)) return;
    inflight.add(sessionId);
    try {
      const response = await chatApi.getSessionGoal(sessionId);
      const goal = response.data?.data?.goal ?? null;
      goalBySession.value = {
        ...goalBySession.value,
        [sessionId]: goal,
      };
    } catch (error) {
      // Keep the previous cached state; transient failures converge on the
      // next refresh triggered by a stream end or an outgoing message.
      console.error("Failed to load session goal:", error);
    } finally {
      inflight.delete(sessionId);
    }
  }

  /**
   * Refresh after a run ends: immediate fetch plus one trailing fetch —
   * the judge LLM call runs on on_agent_done and can finish writing the
   * new status after the run stream has already closed.
   */
  function refreshGoalAfterRun(sessionId: string) {
    void refreshGoal(sessionId);
    window.setTimeout(() => void refreshGoal(sessionId), 4000);
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

  return { currentGoal, refreshGoal, refreshGoalAfterRun };
}
