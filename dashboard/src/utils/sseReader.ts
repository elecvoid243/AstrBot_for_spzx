// Author: elecvoid243
// Date: 2026-09-05
// Plan: docs/superpowers/plans/2026-09-05-agent-teams-refinements-3.md Task 6
// Extracted from useAgentTeamsRun.ts for reuse by useMemberRunStream.ts.
// Buffered SSE reader: splits on '\n\n', extracts 'data:' lines, JSON-parses.

/**
 * Read Server-Sent Events from a ReadableStream body.
 * 
 * @param body - The response.body ReadableStream
 * @param onEvent - Callback invoked for each parsed JSON event
 * @param signal - Optional AbortSignal to cancel the read
 */
export async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: any) => void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  
  while (true) {
    if (signal?.aborted) {
      reader.cancel();
      break;
    }
    
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
}
