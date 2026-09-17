// Author: elecvoid243, 2026-09-01
// ZCode-style follow-up message queue for the chat input.
//
// When an agent run is active and the user sends a message, the message
// is held locally as a "pending follow-up" rendered above the chat input
// instead of being dispatched immediately. Dispatch is explicit
// (2026-09-17): the card's "send" action flushes it as a normal message
// now — the backend captures it into the running turn and injects it at
// the end of that turn's next tool call — while "force interrupt" stops
// the run first and resends. Items still queued when the run ends are
// flushed by Chat.vue, in which case they simply start new runs. The
// earlier auto-flush on every tool-call boundary was dropped: it made
// the card flash by unread whenever the model called a tool right after
// the user typed.
//
// In-memory only (per browser tab): items not yet flushed are lost on
// refresh, same as ZCode's queued messages.

import { reactive } from "vue";

export interface PendingFollowUp {
  /** UUID, stable across edits. */
  id: string;
  /** Session the message queues for. */
  sessionId: string;
  /** Composed outgoing text (user text + comment/reference blocks). */
  text: string;
  createdAt: string;
}

const pendingBySession = reactive<Record<string, PendingFollowUp[]>>({});

export function usePendingFollowUps() {
  function itemsFor(sessionId: string | null | undefined): PendingFollowUp[] {
    return (sessionId && pendingBySession[sessionId]) || [];
  }

  function enqueue(sessionId: string, text: string): PendingFollowUp {
    const item: PendingFollowUp = {
      id: crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`,
      sessionId,
      text,
      createdAt: new Date().toISOString(),
    };
    pendingBySession[sessionId] = pendingBySession[sessionId] || [];
    pendingBySession[sessionId].push(item);
    return item;
  }

  function updateText(sessionId: string, id: string, text: string): void {
    const item = itemsFor(sessionId).find((entry) => entry.id === id);
    const trimmed = text.trim();
    if (!item || !trimmed) return;
    item.text = trimmed;
  }

  function remove(sessionId: string, id: string): void {
    const items = pendingBySession[sessionId];
    if (!items) return;
    const index = items.findIndex((entry) => entry.id === id);
    if (index >= 0) items.splice(index, 1);
  }

  /** Atomically drain the queue (used by flush triggers). Returns the
      removed items so a failed flush could still inspect them. */
  function takeAll(sessionId: string): PendingFollowUp[] {
    const items = pendingBySession[sessionId] || [];
    delete pendingBySession[sessionId];
    return items;
  }

  return {
    pendingBySession,
    itemsFor,
    enqueue,
    updateText,
    remove,
    takeAll,
  };
}
