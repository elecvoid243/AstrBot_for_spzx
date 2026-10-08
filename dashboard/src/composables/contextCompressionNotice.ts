// Author: elecvoid243
// Date: 2026-10-08
//
// Leaf module: the agent runner's context compression notice, as consumed by
// the chat page.
//
// The notice is live-only telemetry: the runner emits it once per compression
// and the dashboard accumulator deliberately drops it (see
// `BotMessageAccumulator.add_plain`), so the chat page renders it as a chip
// next to the composer's token-usage ring instead of a message part. Keeping
// the wire parsing and the chip composition here leaves the SSE consumers as
// thin wiring and keeps this logic unit-testable without the API layer.

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

export interface ContextCompressionEntry {
  /** Identity of the event, from {@link contextCompressionNoticeKey}. */
  key: string;
  notice: ContextCompressionNotice;
}

/** Chip shown next to the composer's token-usage ring while the notice is live. */
export interface ContextCompressionChip {
  /** Token delta only: "123.5k → 45.1k". */
  label: string;
  /** MDI icon; its shape carries the strategy, not just the colour. */
  icon: string;
  /** True when history was dropped, false when it was merely summarized. */
  lossy: boolean;
  /** Full sentence, used as the aria-label and the hover tooltip. */
  description: string;
}

export type CompressionTranslate = (
  key: string,
  params?: Record<string, string | number>,
) => string;

/** Strategies that lose history outright. */
const LOSSY_STRATEGIES = new Set(["truncate_by_turns", "truncate_by_halving"]);

/** Wire strategy → i18n key suffix. Unknown (custom) compressors fall back. */
const STRATEGY_KEYS: Record<string, string> = {
  llm_compress: "llm",
  truncate_by_turns: "truncate",
  truncate_by_halving: "halving",
};

function strategyKey(strategy: string): string {
  return STRATEGY_KEYS[strategy] ?? "generic";
}

/**
 * Build the composer chip for a compression.
 *
 * The sentence depends on the strategy: an LLM summary keeps the conversation
 * (compressed), while a truncation drops history, so the two must not read the
 * same. The icon differs by shape as well as colour so the loss survives
 * greyscale and colour blindness.
 *
 * @param notice Parsed notice.
 * @param translate Module-scoped translation function.
 * @returns Chip label, icon, loss flag and the full description.
 */
export function contextCompressionChip(
  notice: ContextCompressionNotice,
  translate: CompressionTranslate,
): ContextCompressionChip {
  const lossy = LOSSY_STRATEGIES.has(notice.strategy);

  return {
    label: `${formatTokenCount(notice.tokensBefore)} → ${formatTokenCount(notice.tokensAfter)}`,
    icon: lossy ? "mdi-content-cut" : "mdi-arrow-collapse-vertical",
    lossy,
    description: translate(`contextCompression.${strategyKey(notice.strategy)}`, {
      from: formatTokenCount(notice.tokensBefore),
      to: formatTokenCount(notice.tokensAfter),
    }),
  };
}

/**
 * Build the ring tooltip's "last compression" line.
 *
 * The chip is transient, so the numbers must stay reachable from the control
 * that explains them: hovering the token ring is how a user later answers
 * "did it forget what we discussed?".
 *
 * @param notice Parsed notice.
 * @param translate Module-scoped translation function.
 * @returns One-line summary of the last compression.
 */
export function contextCompressionHistoryLine(
  notice: ContextCompressionNotice,
  translate: CompressionTranslate,
): string {
  return translate("contextCompression.lastCompression", {
    from: formatTokenCount(notice.tokensBefore),
    to: formatTokenCount(notice.tokensAfter),
    strategy: translate(
      `contextCompression.strategy.${strategyKey(notice.strategy)}`,
    ),
  });
}
