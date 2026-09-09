// Author: elecvoid243
// Date: 2026-08-28
//
// Leaf module for the spcode todo_* tool protocol: the shared tool-name
// set and the history-replay fold that rebuilds a session's latest todo
// snapshot from persisted chat records.
//
// Why a leaf: `useMessages.ts` pulls in `@/api` (Vite alias), which is
// unresolvable under bare node — the same reason `normalizeMessageParts.ts`
// was extracted (interactive-choice history roundtrip, §2.4 Amendment F).
// Keeping the replay fold here lets `useMessages.todoHistoryReplay.test.ts`
// exercise refresh persistence without spinning up Vite.

/**
 * spcode todo_* tools whose results feed the todo summary bar
 * floating menu.
 *
 * v2.2.0 split 4 standalone tools (todo_create / todo_query /
 * todo_modify / todo_clear); v2.12 further split `todo_modify` into
 * `todo_add` / `todo_update` / `todo_delete`.
 * - create / query / add / update / delete: the result `data` carries
 *   the `list` / `stats` / `attention_items` triple that refreshes the
 *   snapshot live.
 * - clear: returns null (bar disappears immediately).
 * - `todo_list` / `todo_modify` are the pre-v2.2.0 / pre-v2.12 merged
 *   tools, kept so old session histories still replay.
 */
export const TODO_TOOL_NAMES: ReadonlySet<string> = new Set([
  "todo_create",
  "todo_query",
  "todo_add",
  "todo_update",
  "todo_delete",
  "todo_clear",
  "todo_modify", // legacy (pre-v2.12)
  "todo_list", // legacy (pre-v2.2.0)
]);

/**
 * Fold todo tool results across persisted history records and return
 * the snapshot the session should display after a refresh / session
 * switch.
 *
 * Walks `records` in array order (callers pass them chronologically)
 * and applies `parse` to every `tool_call` part whose tool is in
 * `TODO_TOOL_NAMES` and whose `result` is a string. Semantics mirror
 * the live `tool_call_result` path exactly:
 * - parse returning a snapshot → becomes the latest value (last wins).
 * - parse returning `null`    → todo_clear; the value clears.
 * - parse returning `undefined` → failed / unparsable; the previous
 *   value is kept.
 * Returns `undefined` when the history contains no usable todo result
 * at all — callers keep their current snapshot in that case.
 *
 * @param records Persisted chat records (loosely typed on purpose; the
 *   fold narrows defensively, matching `parseTodoToolResult`'s style).
 * @param parse The host's todo-result parser (useMessages's
 *   `parseTodoToolResult`), injected so this module stays dependency-
 *   free and node-testable.
 */
export function replayTodoSnapshotFromHistory<TodoSnapshot>(
  records: ReadonlyArray<unknown>,
  parse: (
    toolName: string,
    resultJson: string,
  ) => TodoSnapshot | null | undefined,
): TodoSnapshot | null | undefined {
  let latest: TodoSnapshot | null | undefined;
  for (const record of records) {
    const parts = (record as { content?: { message?: unknown } } | null)
      ?.content?.message;
    if (!Array.isArray(parts)) continue;
    for (const part of parts as unknown[]) {
      if (!part || typeof part !== "object") continue;
      const toolCalls = (part as { tool_calls?: unknown }).tool_calls;
      if (
        (part as { type?: unknown }).type !== "tool_call" ||
        !Array.isArray(toolCalls)
      ) {
        continue;
      }
      for (const call of toolCalls as unknown[]) {
        if (!call || typeof call !== "object") continue;
        const name = String((call as { name?: unknown }).name ?? "");
        if (!TODO_TOOL_NAMES.has(name)) continue;
        const result = (call as { result?: unknown }).result;
        if (typeof result !== "string") continue;
        const snapshot = parse(name, result);
        if (snapshot !== undefined) latest = snapshot;
      }
    }
  }
  return latest;
}
