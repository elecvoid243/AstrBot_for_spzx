// Author: elecvoid243
// Date: 2026-10-08
//
// Leaf module: the agent runner's context compression notice, as consumed by
// the chat page.
//
// The notice is live-only telemetry: the runner emits it once per compression
// and the dashboard accumulator deliberately drops it (see
// `BotMessageAccumulator.add_plain`), so the chat page renders it as a
// transient toast instead of a message part. Keeping the wire parsing and the
// message composition here leaves the SSE consumers as thin wiring and keeps
// this logic unit-testable without the API layer.

export interface ContextCompressionNotice {
  /** Strategy that produced the result, e.g. `llm_compress`. */
  strategy: string;
  tokensBefore: number;
  tokensAfter: number;
}

/**
 * Parse the SSE `data` field of a `context_compression` payload.
 *
 * @param data Raw payload: the JSON string off the wire, or an already
 *   decoded object (the run snapshot path hands one over).
 * @returns The notice, or null when the payload cannot be trusted — showing a
 *   bogus count is worse than showing nothing.
 */
export function parseContextCompressionNotice(
  data: unknown,
): ContextCompressionNotice | null {
  let raw: unknown = data;
  if (typeof raw === "string") {
    try {
      raw = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!raw || typeof raw !== "object") return null;

  const record = raw as Record<string, unknown>;
  const tokensBefore = Number(record.tokens_before);
  const tokensAfter = Number(record.tokens_after);
  if (!Number.isFinite(tokensBefore) || !Number.isFinite(tokensAfter)) {
    return null;
  }

  return {
    strategy: typeof record.strategy === "string" ? record.strategy : "",
    tokensBefore,
    tokensAfter,
  };
}

/**
 * Identity of one notice, used to suppress duplicates of the same event.
 *
 * @param messageId Message the notice belongs to.
 * @param notice Parsed notice.
 * @returns A key that is equal only for the same turn and token delta.
 */
export function contextCompressionNoticeKey(
  messageId: string,
  notice: ContextCompressionNotice,
): string {
  return `${messageId}:${notice.tokensBefore}:${notice.tokensAfter}`;
}

/**
 * Compact token count for the toast: 1234 → "1.2k", 1234567 → "1.2M".
 *
 * @param tokens Token count.
 * @returns Short human-readable count.
 */
export function formatTokenCount(tokens: number): string {
  if (!Number.isFinite(tokens) || tokens < 0) return "?";
  if (tokens < 1000) return String(Math.round(tokens));
  if (tokens < 1_000_000) {
    return `${trimTrailingZero(tokens / 1000)}k`;
  }
  return `${trimTrailingZero(tokens / 1_000_000)}M`;
}

function trimTrailingZero(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}

/**
 * Build the toast the chat page shows for a compression.
 *
 * The wording depends on the strategy: an LLM summary keeps the conversation
 * (compressed), while a truncation drops history, so the two must not read the
 * same. Unknown (custom) strategies fall back to the generic wording.
 *
 * @param notice Parsed notice.
 * @param translate Module-scoped translation function.
 * @returns Message text and display duration.
 */
export function contextCompressionToast(
  notice: ContextCompressionNotice,
  translate: (key: string, params?: Record<string, string | number>) => string,
): { message: string; timeout: number } {
  const keyByStrategy: Record<string, string> = {
    llm_compress: "llm",
    truncate_by_turns: "truncate",
    truncate_by_halving: "halving",
  };
  const key = keyByStrategy[notice.strategy] ?? "generic";

  return {
    message: translate(`contextCompression.${key}`, {
      from: formatTokenCount(notice.tokensBefore),
      to: formatTokenCount(notice.tokensAfter),
    }),
    // Longer than the 3s default: the message carries two numbers to read.
    timeout: 4000,
  };
}
