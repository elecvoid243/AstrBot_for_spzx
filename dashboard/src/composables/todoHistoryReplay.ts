import type { TodoSnapshot, TodoList, TodoStats } from "./useMessages";
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

/** Operation a todo tool call performed. */
export type TodoAction =
  | "create"
  | "query"
  | "add"
  | "update"
  | "delete"
  | "clear";

/**
 * The merged todo tool (spcode_toolkit v2.35+): a single tool whose
 * `action` argument picks the operation, replacing the six standalone
 * names below.
 */
export const TODO_MANAGE_TOOL_NAME = "todo_manage";

const TODO_ACTIONS: ReadonlySet<string> = new Set([
  "create",
  "query",
  "add",
  "update",
  "delete",
  "clear",
]);

/** Pre-merge standalone names: the tool name *was* the action. */
const LEGACY_TODO_ACTIONS: Readonly<Record<string, TodoAction>> = {
  todo_create: "create",
  todo_query: "query",
  todo_add: "add",
  todo_update: "update",
  todo_delete: "delete",
  todo_clear: "clear",
};

/**
 * spcode todo tools whose results feed the todo summary bar.
 *
 * Timeline: v2.2.0 split `todo_list` into todo_create / todo_query /
 * todo_modify / todo_clear; v2.12 further split `todo_modify` into
 * todo_add / todo_update / todo_delete; v2.35 merged all six back into
 * `todo_manage(action=...)`.
 * - Every action except clear returns `data.list` / `data.stats` /
 *   `data.attention_items`, which refreshes the snapshot live.
 * - clear returns a result without `list`, so it must be recognised by
 *   action (see `parseTodoToolResult`).
 * - `todo_modify` / `todo_list` are the pre-v2.12 / pre-v2.2.0 merged
 *   names, kept so old session histories still replay.
 */
export const TODO_TOOL_NAMES: ReadonlySet<string> = new Set([
  TODO_MANAGE_TOOL_NAME,
  ...Object.keys(LEGACY_TODO_ACTIONS),
  "todo_modify", // legacy (pre-v2.12): action lives in args.mode
  "todo_list", // legacy (pre-v2.2.0): no action, inferred from data shape
]);

/**
 * Resolve which operation a todo tool call performed.
 *
 * Callers must not branch on the tool name alone: every name above is
 * still recognisably "a todo tool", but only the action tells clear
 * (no list in the result) from create (bumps the auto-open tick) from
 * the rest.
 *
 * @param toolName Tool name of the call (`todo_manage` or a legacy name).
 * @param args Raw tool call arguments; a JSON string is tolerated because
 *   old persisted records stored it unparsed.
 * @returns The resolved action, or null when the name is not a todo
 *   tool / the arguments do not identify one (legacy `todo_modify`
 *   without a valid `mode`, legacy `todo_list`).
 */
export function resolveTodoAction(
  toolName: string,
  args?: unknown,
): TodoAction | null {
  if (!TODO_TOOL_NAMES.has(toolName)) return null;
  let payload: any = args;
  if (typeof payload === "string") {
    try {
      payload = JSON.parse(payload);
    } catch {
      payload = null;
    }
  }
  const read = (key: string): string =>
    payload && typeof payload === "object" && typeof payload[key] === "string"
      ? payload[key].trim().toLowerCase()
      : "";

  if (toolName === TODO_MANAGE_TOOL_NAME) {
    const action = read("action");
    return TODO_ACTIONS.has(action) ? (action as TodoAction) : null;
  }
  // Legacy `todo_modify` carried add/update/delete in args.mode.
  if (toolName === "todo_modify") {
    const mode = read("mode");
    return mode === "add" || mode === "update" || mode === "delete"
      ? mode
      : null;
  }
  return LEGACY_TODO_ACTIONS[toolName] ?? null;
}

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
 * - parse returning `null`    → a clear action; the value clears.
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
    args?: unknown,
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
        // The action lives in the arguments now (merged `todo_manage`),
        // so persisted calls must hand theirs to the parser too.
        const snapshot = parse(
          name,
          result,
          (call as { args?: unknown }).args,
        );
        if (snapshot !== undefined) latest = snapshot;
      }
    }
  }
  return latest;
}

/**
 * Parse a todo tool result into the snapshot the summary bar shows.
 *
 * @param toolName Tool name of the call.
 * @param resultJson Raw result string (spcode `unwrap()` envelope).
 * @param args Tool call arguments; required to tell the merged
 *   `todo_manage` actions apart (notably clear, whose result carries no
 *   list).
 * @returns The parsed snapshot, null when the list was cleared, or
 *   undefined when the result is unusable and the previous snapshot
 *   should be kept.
 */
export function parseTodoToolResult(
  toolName: string,
  resultJson: string,
  args?: unknown,
): TodoSnapshot | null | undefined {
  if (!resultJson || typeof resultJson !== "string") return undefined;
  let parsed: any;
  try {
    parsed = JSON.parse(resultJson);
  } catch {
    return undefined;
  }
  if (!parsed || typeof parsed !== "object") return undefined;

  // Failed results leave the previous snapshot unchanged.
  if (parsed.ok === false) return undefined;

  // Successful clearing removes the displayed list. Clear is the one
  // action whose result has no `list`, so it must be recognised here by
  // action rather than by the (missing) payload shape.
  if (resolveTodoAction(toolName, args) === "clear") {
    return null;
  }

  // Accept the ordinary success envelope and legacy root-level results.
  const data =
    parsed.ok === true && parsed.data && typeof parsed.data === "object"
      ? parsed.data
      : parsed;

  const list = data?.list;
  const stats = data?.stats;
  if (!list || typeof list !== "object") return undefined;
  if (!stats || typeof stats !== "object") return undefined;

  const attentionRaw = data?.attention_items;
  const attentionItems: number[] = Array.isArray(attentionRaw)
    ? attentionRaw.filter((n) => typeof n === "number")
    : [];

  return {
    list: list as TodoList,
    stats: stats as TodoStats,
    attentionItems,
    updatedAt: Date.now(),
  };
}
