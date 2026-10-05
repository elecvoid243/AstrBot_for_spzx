import { ref, type Ref } from "vue";
import { chatApi } from "@/api/v1";
import type { ShellSessionPollResult } from "@/components/chat/message_list_comps/shell_session_tools/format";

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
  /** Retained-output cap; older text is dropped behind a trim marker. */
  const MAX_RETAINED_CHARS = 256 * 1024;
  const TRIM_MARKER = "[... earlier output trimmed ...]\n";

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
        // AstrBot business errors are HTTP 200 with {status:"error"} — axios
        // resolves them, so branch on the envelope, not on rejection.
        const envelope = resp.data as {
          status?: string;
          data?: ShellSessionPollResult;
        };
        if (!envelope || envelope.status === "error" || !envelope.data) {
          // Session reaped or runtime unavailable: keep what we read and
          // freeze in place.
          sessionClosed.value = true;
          break;
        }
        const data = envelope.data;
        if (data.stdout) {
          outputText.value += data.stdout;
          // Bound the retained buffer: a chatty process must not grow the
          // window's string without limit.
          if (outputText.value.length > MAX_RETAINED_CHARS) {
            outputText.value =
              TRIM_MARKER +
              outputText.value.slice(-MAX_RETAINED_CHARS);
          }
        }
        cursor = data.cursor ?? cursor;
        if (data.status) status.value = data.status;
        if (data.session_closed) {
          sessionClosed.value = true;
          break;
        }
      } catch {
        // Network failure: same freeze-in-place behavior.
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
