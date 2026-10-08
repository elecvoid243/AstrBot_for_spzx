// Author: elecvoid243, 2026-10-08

/**
 * Click-to-send phrases for the ChatUI input row, persisted in
 * `cmd_config.json` (`chatui.quick_messages`) next to the thinking-effort
 * presets.
 *
 * Items carry content only — no title. The menu renders one truncated line
 * per item and the native `title` attribute reveals the full text on hover,
 * so a long template stays readable without widening the popup.
 *
 * The backend (`astrbot/dashboard/services/chatui_settings.py`) owns
 * validation; these helpers only keep the UI resilient while the payload
 * loads. An empty list is a legitimate state (the user deleted them all).
 */

/** Upper bounds mirror the backend so the editor can disable inputs early. */
export const MAX_QUICK_MESSAGES = 24;
export const MAX_QUICK_MESSAGE_LENGTH = 2000;

export interface QuickMessage {
  id: string;
  content: string;
}

/** Id for a phrase the user is adding; stable within one browser session. */
export function createQuickMessageId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return `qm-${uuid}`;
  return `qm-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function normalizeQuickMessages(raw: unknown): QuickMessage[] {
  const data = (raw ?? {}) as { items?: unknown };
  if (!Array.isArray(data.items)) return [];
  const items: QuickMessage[] = [];
  const seen = new Set<string>();
  for (const entry of data.items) {
    const item = entry as Partial<QuickMessage> | null;
    if (!item || typeof item.id !== "string" || typeof item.content !== "string") {
      continue;
    }
    const id = item.id.trim();
    const content = item.content.trim();
    if (!id || !content || seen.has(id)) continue;
    seen.add(id);
    items.push({ id, content });
    if (items.length >= MAX_QUICK_MESSAGES) break;
  }
  return items;
}
