// Author: elecvoid243
// Date: 2026-09-05
// Plan: docs/superpowers/plans/2026-09-05-agent-teams-refinements-3.md Task 6
// Composable for attaching to a member's run stream (for MemberTranscriptDialog).
// Handles interactive_choice, interactive_choice_resolved, and user_message_saved.

import { ref, shallowRef, type Ref } from 'vue';
import { fetchWithAuth } from '@/api/http';
import { chatApi } from '@/api/v1';
import { readSseStream } from '@/utils/sseReader';
import {
  applyInteractiveChoiceSse,
  applyInteractiveChoiceResolved,
  type BotMessageLike,
} from './dispatchInteractiveChoice';

interface UserBubble {
  id?: string;
  text: string;
}

export interface MemberRunStreamState {
  record: BotMessageLike;
  userBubbles: Ref<UserBubble[]>;
  detach: () => void;
}

/**
 * Attach to a run's stream to hydrate a member's transcript dialog.
 * 
 * @param umo - Session ID for the member
 * @param sessionId - Session ID (same as umo for consistency)
 * @param runId - Run ID to resume
 * @returns Stream state with bot record, user bubbles, and detach function
 */
export function useMemberRunStream(): {
  attach: (umo: string, sessionId: string, runId: string) => MemberRunStreamState;
} {
  return {
    attach(umo: string, sessionId: string, runId: string): MemberRunStreamState {
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
                applyInteractiveChoiceSse(umo, record, payload);
              } else if (type === 'interactive_choice_resolved') {
                applyInteractiveChoiceResolved(umo, payload);
              } else if (type === 'user_message_saved') {
                const data = payload.data as any;
                if (data && typeof data.text === 'string') {
                  userBubbles.value.push({
                    id: data.id,
                    text: data.text,
                  });
                }
              }
              // Ignore other events
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
