// Author: elecvoid243
// Date: 2026-09-05
// Plan: docs/superpowers/plans/2026-09-05-agent-teams-refinements-3.md Task 6
// Simple spec to verify sseReader.ts works correctly.

import { describe, it, expect } from 'vitest';
import { readSseStream } from './sseReader';

describe('sseReader', () => {
  it('parses SSE events from a stream', async () => {
    const events: any[] = [];
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"type":"test","value":1}\n\n'));
        controller.enqueue(encoder.encode('data: {"type":"test","value":2}\n\n'));
        controller.close();
      },
    });

    await readSseStream(stream, (event) => events.push(event));
    expect(events).toHaveLength(2);
    expect(events[0]).toEqual({ type: 'test', value: 1 });
    expect(events[1]).toEqual({ type: 'test', value: 2 });
  });

  it('skips empty and comment lines', async () => {
    const events: any[] = [];
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(': heartbeat\n\n'));
        controller.enqueue(encoder.encode('data: {"type":"ok"}\n\n'));
        controller.close();
      },
    });

    await readSseStream(stream, (event) => events.push(event));
    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({ type: 'ok' });
  });
});
