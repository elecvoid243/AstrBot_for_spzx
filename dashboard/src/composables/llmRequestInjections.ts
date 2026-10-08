// Author: elecvoid243
// Date: 2026-10-08
//
// Leaf parsing/formatting for the LLM request injection trace: the wire
// payload a plugin context injection produces on `on_llm_request`, rendered
// by the ChatMessageList gutter marker. Kept out of the component so both the
// dispatch tests and the component specs can pin the wire contract.

import { formatTokenCount } from "./contextCompressionNotice";

export interface LlmRequestInjectionChange {
  field: string;
  delta: number;
  lossy: boolean;
  preview: string;
  /** Complete injected text (kernel-capped, ellipsis-marked when cut); the
   * panel reveals it in place. Empty when there is nothing to expand. */
  full?: string;
}

export interface LlmRequestInjectionItem {
  plugin: string;
  handler: string;
  changes: LlmRequestInjectionChange[];
}

export interface LlmRequestInjections {
  items: LlmRequestInjectionItem[];
}

/** One change's i18n unit key, by the request field it touched. */
const FIELD_UNITS: Record<string, string> = {
  system_prompt: "units.chars",
  contexts: "units.items",
  extra_user_content_parts: "units.items",
  func_tool: "units.tools",
};

/**
 * Parse the `llm_request_injections` payload off the stream or out of
 * history.
 *
 * @param data Raw payload: the JSON string off the wire, or an already
 *   decoded object (history reloads hand one over).
 * @returns The payload, or null when it cannot be trusted — a bogus marker
 *   is worse than no marker.
 */
export function parseLlmRequestInjections(
  data: unknown,
): LlmRequestInjections | null {
  let candidate: unknown = data;
  if (typeof candidate === "string") {
    try {
      candidate = JSON.parse(candidate);
    } catch {
      return null;
    }
  }
  if (!candidate || typeof candidate !== "object") return null;
  const items = (candidate as { items?: unknown }).items;
  if (!Array.isArray(items) || items.length === 0) return null;
  return { items: items as LlmRequestInjectionItem[] };
}

/**
 * One change's label: signed magnitude plus unit; lossy trims get a suffix.
 *
 * @param change One change record from an injection item.
 * @param translate Module-scoped translation function.
 * @returns E.g. "+412 字符", "-1.2k 字符 · 已截断", "已改写".
 */
export function injectionChangeLabel(
  change: LlmRequestInjectionChange,
  translate: (key: string, params?: Record<string, string | number>) => string,
): string {
  if (change.delta === 0) return translate("llmRequestInjections.rewritten");
  const unit = FIELD_UNITS[change.field] ?? "units.items";
  const magnitude = formatTokenCount(Math.abs(change.delta));
  const signed = `${change.delta > 0 ? "+" : "-"}${magnitude}`;
  const label = translate(`llmRequestInjections.${unit}`, { delta: signed });
  return change.lossy
    ? `${label} · ${translate("llmRequestInjections.lossySuffix")}`
    : label;
}
