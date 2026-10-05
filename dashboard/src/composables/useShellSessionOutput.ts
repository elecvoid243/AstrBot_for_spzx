import { ref, type Ref } from "vue";
import { chatApi } from "@/api/v1";

/**
 * useShellSessionOutput — peek-polling loop for one floating shell session
 * output window.
 *
 * The backend peek endpoint is non-destructive (never advances the agent's
 * cursor), so this composable keeps its OWN cursor starting at 0 and
 * long-polls (`yieldTimeMs`) for incremental output. The loop ends when the
 * session closes, when the session vanishes (reaped by an agent poll — the
 * window keeps showing what it already read), or on explicit stop().
 *
 * Author: elecvoid243 | 2026-10-05
 */
export function useShellSessionOutput(
  sessionId: string,
  shellSessionId: string,
) {
  const outputText: Ref<string> = ref("");
  const status: Ref<string> = ref("running");
  const sessionClosed: Ref<boolean> = ref(false);
  /** UI auto-scroll toggle; the loop itself does not read it. */
  const follow: Ref<boolean> = ref(true);

  let cursor = 0;
  let stopped = false;

  /**
   * Run the peek loop until the session closes, errors, or stop() is
   * called. Resolves when the loop ends; safe to await in tests.
   */
  async function start(): Promise<void> {
    stopped = false;
    while (!stopped && !sessionClosed.value) {
      try {
        const resp = await chatApi.getShellSessionOutput(
          sessionId,
          shellSessionId,
          { cursor, yieldTimeMs: 2000 },
        );
        const data = resp.data?.data;
        if (!data) break;
        if (data.stdout) outputText.value += data.stdout;
        cursor = data.cursor ?? cursor;
        if (data.status) status.value = data.status;
        if (data.session_closed) {
          sessionClosed.value = true;
          break;
        }
      } catch {
        // Session reaped or network failure: the window keeps the output it
        // already has and freezes in place.
        sessionClosed.value = true;
        break;
      }
    }
  }

  /** Stop after the in-flight request settles (long-poll grace). */
  function stop(): void {
    stopped = true;
  }

  return { outputText, status, sessionClosed, follow, start, stop };
}
