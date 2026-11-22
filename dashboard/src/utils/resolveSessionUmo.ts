/**
 * resolveSessionUmo — build the unified_msg_origin for a chat session.
 *
 * Extracted from Chat.vue (resolveCurrentUmo) on 2026-08-11 to fix the
 * stale project chip: the flat session list excludes project sessions
 * server-side (`exclude_project_sessions=True`), and the previously
 * consulted `projectSessions` only covers the CURRENTLY selected
 * project. A session under any other folder failed to resolve a umo,
 * so the session-switch watcher refreshed the spcode chip with a null
 * umo (backend falls back to the most-recently-loaded project across
 * ALL umos → stale display) and the auto-load trigger never fired.
 *
 * The lookup now also walks `projectSessionsById` — the cache backing
 * every project folder in the sidebar — so any session the user can
 * see (and click) resolves.
 *
 * Author: elecvoid243 | 2026-08-11
 */

import { buildWebchatUmoDetails } from "@/utils/chatConfigBinding";

/** Minimal session shape needed to build a umo. */
export interface UmoSessionLike {
  session_id: string;
  platform_id?: string | null;
  is_group?: number | boolean | null;
}

export interface SessionUmoSources {
  /** Flat sidebar list (excludes project sessions). */
  sessions: UmoSessionLike[];
  /** Currently selected project's session list. */
  projectSessions: UmoSessionLike[];
  /** Cache of every loaded project folder's sessions, keyed by project id. */
  projectSessionsById: Record<string, UmoSessionLike[]>;
  /** Metadata loaded independently of the sidebar, keyed by session ID. */
  sessionDetails?: Record<string, UmoSessionLike>;
}

/**
 * Resolve the full unified_msg_origin for `sessionId`, or null when the
 * session is not found in any source. Callers treat null as "unmapped
 * session" and must NOT fall back to a bare refresh (see module header).
 */
export function resolveSessionUmo(
  sessionId: string,
  sources: SessionUmoSources,
): string | null {
  if (!sessionId) return null;
  const session =
    sources.sessions.find((s) => s.session_id === sessionId) ??
    sources.projectSessions.find((s) => s.session_id === sessionId) ??
    Object.values(sources.projectSessionsById)
      .flat()
      .find((s) => s.session_id === sessionId) ??
    sources.sessionDetails?.[sessionId];
  if (!session) return null;

  const platformId = session.platform_id || "webchat";
  if (platformId === "webchat") {
    return buildWebchatUmoDetails(sessionId, Boolean(session.is_group)).umo;
  }
  // Generic fallback for non-webchat platforms: trust the platform's own
  // session_id format; message_type falls back to FriendMessage.
  const messageType = session.is_group ? "GroupMessage" : "FriendMessage";
  return `${platformId}:${messageType}:${sessionId}`;
}
