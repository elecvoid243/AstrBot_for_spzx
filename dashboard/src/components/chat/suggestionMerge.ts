// src/components/chat/suggestionMerge.ts
//
// Pure ranking/merge for the composer's "/" palette: enabled bot commands
// plus the skill pseudo-commands backed by astrbot_plugin_skill_guide.
//
// Kept as a standalone module (instead of inlining in ChatInput.vue)
// because the ranking rules are non-trivial and unit-tested in
// suggestionMerge.spec.ts.
//
// Author: elecvoid243, 2026-09-08

import type { SuggestionCommand } from "./CommandSuggestion.vue";

/** Max skills appended after the commands when the query is empty. */
export const DEFAULT_MAX_SKILLS_WHEN_EMPTY = 8;

/** Remove the first matching wake prefix (e.g. "/") from `text`. */
export function stripWakePrefix(text: string, wakePrefixes: string[]): string {
  for (const prefix of wakePrefixes) {
    if (prefix && text.startsWith(prefix)) {
      return text.slice(prefix.length);
    }
  }
  return text;
}

/** Lowercased, prefix-stripped, trimmed form used for matching. */
export function normalizeCommandSearchText(
  text: string,
  wakePrefixes: string[],
): string {
  return stripWakePrefix(text.trim(), wakePrefixes).toLowerCase();
}

/**
 * Remove the leading wake-prefixed trigger token from the composer text,
 * keeping whatever the user typed after it.
 *
 * The token is the palette QUERY (e.g. "/wr" while picking
 * "writing-plans"), so it must be removed regardless of whether it equals
 * the selected name. Text without a wake prefix is returned unchanged.
 *
 * Args:
 *   text: Raw composer text.
 *   wakePrefixes: Configured wake prefixes (e.g. ["/"]).
 *
 * Returns:
 *   The remaining text ("/" alone yields ""; no prefix yields `text`).
 */
export function stripLeadingTriggerToken(
  text: string,
  wakePrefixes: string[],
): string {
  const trimmed = text.trimStart();
  const stripped = stripWakePrefix(trimmed, wakePrefixes);
  if (stripped === trimmed) return text;
  const match = /^([^\s]+)(\s+)?/.exec(stripped);
  return match ? stripped.slice(match[0].length) : "";
}

export interface MergeSuggestionsInput {
  /** Enabled bot commands, already display-prefixed (e.g. "/help"). */
  commands: SuggestionCommand[];
  /** Skill pseudo-commands, already display-prefixed. */
  skills: SuggestionCommand[];
  /** Raw prompt text; callers gate on the wake prefix before calling. */
  text: string;
  wakePrefixes: string[];
  maxSkillsWhenEmpty?: number;
}

/** Reserved (system/plugin) commands first, stable within a bucket. */
function sortReservedFirst(commands: SuggestionCommand[]): SuggestionCommand[] {
  return [...commands].sort((a, b) => Number(b.reserved) - Number(a.reserved));
}

function byCommandName(a: SuggestionCommand, b: SuggestionCommand): number {
  return a.effective_command.localeCompare(b.effective_command);
}

/**
 * Merge commands + skills for the composer's suggestion panel.
 *
 * Rules:
 * - Empty query: every command (reserved first) then up to
 *   `maxSkillsWhenEmpty` skills, alphabetical.
 * - Non-empty query: matches whose command name starts with the query
 *   rank above substring/metadata matches; inside each bucket commands
 *   keep their insertion order, so a skill never outranks an
 *   equally-matching command.
 *
 * Args:
 *   input: The candidate lists, raw prompt text and wake prefixes.
 *
 * Returns:
 *   The ranked suggestion list for the panel.
 */
export function mergeSuggestions(
  input: MergeSuggestionsInput,
): SuggestionCommand[] {
  const { commands, skills, text, wakePrefixes } = input;
  const maxSkills = input.maxSkillsWhenEmpty ?? DEFAULT_MAX_SKILLS_WHEN_EMPTY;
  const query = normalizeCommandSearchText(text, wakePrefixes);

  if (!query) {
    return [
      ...sortReservedFirst(commands),
      ...[...skills].sort(byCommandName).slice(0, maxSkills),
    ];
  }

  const startsWithMatches: SuggestionCommand[] = [];
  const containsMatches: SuggestionCommand[] = [];

  for (const command of [...commands, ...skills]) {
    const commandText = normalizeCommandSearchText(
      command.effective_command,
      wakePrefixes,
    );
    const metadataText = [
      command.plugin_display_name || "",
      command.description || "",
    ].map((value) => normalizeCommandSearchText(value, wakePrefixes));

    if (commandText.startsWith(query)) {
      startsWithMatches.push(command);
    } else if (
      commandText.includes(query) ||
      metadataText.some((value) => value.includes(query))
    ) {
      containsMatches.push(command);
    }
  }

  return [
    ...sortReservedFirst(startsWithMatches),
    ...sortReservedFirst(containsMatches),
  ];
}
