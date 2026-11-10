/**
 * Parse the sanitizer markers that the backend appends to folded reasoning.
 *
 * The markers are plain lines in the model-facing text. The ChatUI turns
 * them into dedicated components instead of letting Markdown render the
 * bracket syntax as an italic link-like fragment.
 */

export type ThinkSegment =
  | { kind: "text"; text: string }
  | { kind: "period"; repeats: number; period: number }
  | { kind: "run"; repeats: number }
  | { kind: "fallback"; lines: number; chars: number };

const PERIOD_MARKER =
  /^\[context sanitizer: collapsed (\d+) x (\d+)-line repeated block\]$/;
const RUN_MARKER = /^\[context sanitizer: collapsed (\d+) x repeated line\]$/;
const FALLBACK_MARKER =
  /^\[context sanitizer: collapsed repetitive reasoning \((\d+) lines, (\d+) chars\)\]$/;

/** Return true when a line is one of the known sanitizer markers. */
export function isSanitizerMarkerLine(line: string): boolean {
  const trimmed = line.trim();
  return (
    PERIOD_MARKER.test(trimmed) ||
    RUN_MARKER.test(trimmed) ||
    FALLBACK_MARKER.test(trimmed)
  );
}

/**
 * Split a folded reasoning string into markdown text and marker segments.
 *
 * Args:
 *   text: Reasoning content, possibly containing sanitizer marker lines.
 *
 * Returns:
 *   Ordered segments; consecutive non-marker lines stay in one text segment.
 */
export function parseThinkSegments(text: string): ThinkSegment[] {
  const segments: ThinkSegment[] = [];
  let buffer: string[] = [];

  const flush = () => {
    if (!buffer.length) return;
    const joined = buffer
      .join("\n")
      .replace(/^(?:[ \t]*\n)+/, "")
      .replace(/(?:\n[ \t]*)+$/, "");
    if (joined.trim()) {
      segments.push({ kind: "text", text: joined });
    }
    buffer = [];
  };

  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();

    const period = PERIOD_MARKER.exec(trimmed);
    if (period) {
      flush();
      segments.push({
        kind: "period",
        repeats: Number(period[1]),
        period: Number(period[2]),
      });
      continue;
    }

    const run = RUN_MARKER.exec(trimmed);
    if (run) {
      flush();
      segments.push({ kind: "run", repeats: Number(run[1]) });
      continue;
    }

    const fallback = FALLBACK_MARKER.exec(trimmed);
    if (fallback) {
      flush();
      segments.push({
        kind: "fallback",
        lines: Number(fallback[1]),
        chars: Number(fallback[2]),
      });
      continue;
    }

    buffer.push(line);
  }

  flush();
  return segments;
}
