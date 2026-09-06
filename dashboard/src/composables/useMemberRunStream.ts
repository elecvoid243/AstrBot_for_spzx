// Author: elecvoid243
// Date: 2026-09-05
// Plan: docs/superpowers/plans/2026-09-05-agent-teams-refinements-3.md Task 6
// Attaches one member's chat run stream so the transcript dialog gets the same
// fidelity as the chat page: `ask_user_choice` boxes hydrate through the shared
// dispatcher + Pinia store, and follow-up turns appear as user bubbles. Only
// those payload types are handled; Teams' own run events stay on the reducer.

import { ref, type Ref } from 'vue';
import { fetchWithAuth } from '@/api/http';
import { chatApi } from '@/api/v1';
import { readSseStream } from '@/utils/sseReader';
import {
  applyInteractiveChoiceSse,
  applyInteractiveChoiceResolved,
  type BotMessageLike,
} from './dispatchInteractiveChoice';

/** One user turn echoed back by the run stream (`user_message_saved`). */
interface UserBubble {
  id?: string;
  text: string;
}

export interface MemberRunStreamState {
  /** Bot record the choice dispatcher pushes interactive_choice parts into. */
  record: BotMessageLike;
  userBubbles: Ref<UserBubble[]>;
  detach: () => void;
}

/**
 * Attach one member's chat run stream for the transcript dialog.
 *
 * The stream is the member session's own run stream, so choice boxes and
 * follow-up echoes arrive exactly as they do on the chat page. Only
 * `interactive_choice`, `interactive_choice_resolved` and `user_message_saved`
 * are handled; every other payload belongs to the Teams reducer and is ignored.
 * No reconnect is attempted — the dialog re-attaches when it reopens.
 *
 * Returns:
 *   An object whose `attach(umo, runId)` starts one stream and returns the bot
 *   record, the user-bubble list, and a `detach` that aborts it.
 */
export function useMemberRunStream(): {
  attach: (umo: string, runId: string) => MemberRunStreamState;
} {
  return {
    /**
     * Start streaming one run for the member owning `umo`.
     *
     * Args:
     *   umo: Member session umo; scopes the interactive-choice store writes.
     *   runId: Chat run to resume (the member turn's run id).
     */
    attach(umo: string, runId: string): MemberRunStreamState {
      const userBubbles = ref<UserBubble[]>([]);
      const record: BotMessageLike = {
        content: {
          message: [],
          isLoading: false,
        },
      };

      const abort = new AbortController();

      void (async () => {
        try {
          const response = await fetchWithAuth(chatApi.resumeRunStreamUrl(runId), {
            headers: { Accept: 'text/event-stream' },
            signal: abort.signal,
          });

          if (!response.ok || !response.body) return;

          await readSseStream(
            response.body,
            (payload) => {
              if (!payload || typeof payload !== 'object') return;
              const type = payload.type;
              if (type === 'interactive_choice') {
                // The dispatcher pushes the part and mirrors it into the store,
                // so InteractiveChoiceBox submits through the chat-page path.
                applyInteractiveChoiceSse(umo, record, payload);
              } else if (type === 'interactive_choice_resolved') {
                applyInteractiveChoiceResolved(umo, payload);
              } else if (type === 'user_message_saved') {
                const data = payload.data as { id?: string; text?: unknown } | undefined;
                if (data && typeof data.text === 'string') {
                  userBubbles.value.push({ id: data.id, text: data.text });
                }
              }
            },
            abort.signal,
          );
        } catch (error) {
          if (!abort.signal.aborted) {
            console.error('Member run stream failed:', error);
          }
        }
      })();

      return {
        record,
        userBubbles,
        detach: () => abort.abort(),
      };
    },
  };
}
