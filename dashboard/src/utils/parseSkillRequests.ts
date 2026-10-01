// Author: elecvoid243, 2026-10-01
// Parser for the "[Requested skills]" block that Chat.vue appends to
// outgoing user messages when one-shot skill nudges were queued
// (astrbot_plugin_skill_guide). Same rationale as parseFileReferences.ts:
// the block is stored verbatim in the platform message history, so
// parsing it back into structured data lets the chat UI render a skill
// card for BOTH freshly sent and historical messages — no backend
// change required.
//
// Grammar (byte-for-byte mirrors the send-time block in Chat.vue):
//   [Requested skills]     <- block marker (first line)
//   <1 prose line for LLM> <- ignored for display
//   - `<skill name>`       <- one line per queued skill

/** The parsed skill-request block plus the text before it. */
export interface SkillRequestBlock {
  /** Everything before the marker, trimmed. */
  userText: string;
  /** Skill names in send order. */
  skills: string[];
}

const BLOCK_MARKER = "[Requested skills]";
const ENTRY = /^- `(.+)`\s*$/;

/**
 * Parse a user message that may end with a "[Requested skills]" block.
 *
 * Returns `null` when the marker is absent or no entry line could be
 * parsed, so callers fall back to rendering the raw text unchanged.
 *
 * @param raw - The full plain-text content of a user message part.
 * @returns The structured block, or `null` if not present/parseable.
 */
export function parseSkillRequests(raw: string): SkillRequestBlock | null {
  if (!raw) return null;
  const lines = raw.split("\n");
  const start = lines.findIndex((l) => l.trim() === BLOCK_MARKER);
  if (start < 0) return null;

  const userText = lines.slice(0, start).join("\n").trim();
  const skills: string[] = [];
  for (let i = start + 1; i < lines.length; i++) {
    const m = lines[i].match(ENTRY);
    if (m) skills.push(m[1]);
  }
  if (skills.length === 0) return null;
  return { userText, skills };
}
