// Author: elecvoid243, 2026-09-13
// Per-session composer draft store.
//
// Chat.vue keeps a single `draft` ref for the composer; without this store
// that text leaked across sessions (switching to another session in the
// sidebar showed the previous session's unsent text). Drafts are keyed by
// session id and persisted to localStorage, so a half-typed message
// survives switching sessions, navigating to another page, and full page
// reloads.
//
// Plain (non-reactive) module state, same singleton rationale as
// usePendingFollowUps: Chat.vue's composer ref is the only render source —
// nothing reads this store from a template, so reactivity would be dead
// weight.

const STORAGE_KEY = "chat.sessionDrafts.v1";

/** Bound on stored drafts (oldest-by-update dropped first) so localStorage
 *  cannot grow without limit over months of use. */
const MAX_DRAFTS = 50;

/**
 * Draft slot for text typed while no session exists yet (the fresh
 * "/chat" landing / project compose view). Chat.vue maps an empty current
 * session id onto this key so such text is persisted too, and migrates it
 * into the session created on first send / "new chat".
 */
export const NEW_CHAT_DRAFT_KEY = "__new_chat__";

interface StoredDraft {
  text: string;
  /** Last-write time, drives pruning. */
  ts: number;
}

function loadDrafts(): Record<string, StoredDraft> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, StoredDraft>)
      : {};
  } catch {
    // Corrupted entry: start from scratch rather than crash the composer.
    return {};
  }
}

const drafts: Record<string, StoredDraft> = loadDrafts();

function persist(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(drafts));
  } catch {
    // Quota exceeded / storage disabled: drafts degrade to in-memory for
    // this page load; nothing to recover.
  }
}

/** Drop the oldest drafts beyond MAX_DRAFTS. */
function prune(): void {
  const ids = Object.keys(drafts);
  if (ids.length <= MAX_DRAFTS) return;
  ids
    .sort((a, b) => drafts[a].ts - drafts[b].ts)
    .slice(0, ids.length - MAX_DRAFTS)
    .forEach((id) => delete drafts[id]);
}

export function useChatDrafts() {
  /** Saved draft for a session ("" when it has none). */
  function draftFor(sessionId: string): string {
    return drafts[sessionId]?.text ?? "";
  }

  /** Store `text` for a session; empty text deletes the entry. Writes
   *  with an empty session id are ignored — callers that want the
   *  no-session state persisted pass NEW_CHAT_DRAFT_KEY themselves. */
  function setDraft(sessionId: string, text: string): void {
    if (!sessionId) return;
    if (text) {
      drafts[sessionId] = { text, ts: Date.now() };
      prune();
    } else {
      delete drafts[sessionId];
    }
    persist();
  }

  function clearDraft(sessionId: string): void {
    setDraft(sessionId, "");
  }

  return { draftFor, setDraft, clearDraft };
}
