// Author: elecvoid243
// Date: 2026-09-05
// Plan: docs/superpowers/plans/2026-09-05-agent-teams-refinements-3.md Task 6

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useMemberRunStream } from './useMemberRunStream';
import * as httpModule from '@/api/http';

vi.mock('@/api/http');
const fetchWithAuthMock = vi.mocked(httpModule.fetchWithAuth);

const encoder = new TextEncoder();

function frame(payload: unknown): Uint8Array {
  return encoder.encode(`data: ${JSON.stringify(payload)}\n\n`);
}

function sseResponse(frames: Uint8Array[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of frames) controller.enqueue(chunk);
      controller.close();
    },
  });
  return {
    ok: true,
    headers: new Headers({ 'content-type': 'text/event-stream' }),
    body,
  } as unknown as Response;
}

describe('useMemberRunStream', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('hydrates bot record with interactive_choice events', async () => {
    fetchWithAuthMock.mockResolvedValue(
      sseResponse([
        frame({
          type: 'interactive_choice',
          data: {
            request_id: 'choice-1',
            spec: {
              type: 'interactive_choice',
              request_id: 'choice-1',
              prompt: 'Pick one',
              options: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }],
            },
          },
        }),
      ]),
    );

    const stream = useMemberRunStream();
    const state = stream.attach('umo1', 'run1');

    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(state.record.content.message).toHaveLength(1);
    expect(state.record.content.message[0]).toMatchObject({
      type: 'interactive_choice',
      request_id: 'choice-1',
      prompt: 'Pick one',
    });
  });

  it('collects user_message_saved into userBubbles', async () => {
    fetchWithAuthMock.mockResolvedValue(
      sseResponse([
        frame({
          type: 'user_message_saved',
          data: { id: 'msg1', text: 'Hello' },
        }),
        frame({
          type: 'user_message_saved',
          data: { id: 'msg2', text: 'World' },
        }),
      ]),
    );

    const stream = useMemberRunStream();
    const state = stream.attach('umo1', 'run1');

    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(state.userBubbles.value).toHaveLength(2);
    expect(state.userBubbles.value[0]).toEqual({ id: 'msg1', text: 'Hello' });
    expect(state.userBubbles.value[1]).toEqual({ id: 'msg2', text: 'World' });
  });

  it('detach aborts the stream', async () => {
    const abortSpy = vi.fn();
    fetchWithAuthMock.mockImplementation(async (url, init) => {
      init?.signal?.addEventListener('abort', abortSpy);
      return sseResponse([]);
    });

    const stream = useMemberRunStream();
    const state = stream.attach('umo1', 'run1');

    await new Promise((resolve) => setTimeout(resolve, 10));
    state.detach();
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(abortSpy).toHaveBeenCalled();
  });

  it('ignores unknown event types', async () => {
    fetchWithAuthMock.mockResolvedValue(
      sseResponse([
        frame({ type: 'message', direction: 'sent', text: 'task' }),
        frame({ type: 'unknown_event', data: 'junk' }),
      ]),
    );

    const stream = useMemberRunStream();
    const state = stream.attach('umo1', 'run1');

    await new Promise((resolve) => setTimeout(resolve, 50));

    // No errors, no hydration from unknown types
    expect(state.record.content.message).toHaveLength(0);
    expect(state.userBubbles.value).toHaveLength(0);
  });
});
