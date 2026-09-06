// Author: elecvoid243
// Date: 2026-09-05
// Plan: docs/superpowers/plans/2026-09-05-agent-teams-refinements-3.md Task 6
// Extracted from useAgentTeamsRun.ts so useMemberRunStream can reuse the same
// framing rules. Dependency-free leaf: no vue / @/api imports.

/**
 * Read one SSE body, invoking onEvent with every JSON-parsed `data:` payload.
 *
 * Framing follows the backend's SSE writer: events are separated by a blank
 * line, each event's `data:` lines are joined, and events with empty data are
 * skipped so `: heartbeat` comment lines are ignored. Malformed JSON is logged
 * and dropped rather than aborting the stream.
 *
 * Args:
 *   body: The fetch response body stream.
 *   onEvent: Callback invoked with each JSON-parsed payload.
 *   signal: Optional abort signal; the reader is cancelled when it fires.
 */
export async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: any) => void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  // Abort mid-read too: `reader.read()` only settles on the next chunk, so a
  // pre-loop check alone would keep a hanging stream alive until it closes.
  const onAbort = () => {
    void reader.cancel().catch(() => {});
  };
  signal?.addEventListener('abort', onAbort, { once: true });

  try {
    while (true) {
      if (signal?.aborted) break;
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop() || '';
      for (const event of events) {
        const data = event
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n');
        if (!data) continue;
        try {
          onEvent(JSON.parse(data));
        } catch (error) {
          console.error('Failed to parse SSE payload:', error, data);
        }
      }
    }
  } finally {
    signal?.removeEventListener('abort', onAbort);
  }
}
