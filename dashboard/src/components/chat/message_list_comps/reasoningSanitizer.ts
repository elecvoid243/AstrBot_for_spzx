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
 * Drop the kept first occurrence that precedes a fold marker.
 *
 * The backend keeps one occurrence as an anchor for the model, but the
 * ChatUI should not display any of the folded block. The marker's period
 * tells us how many trailing lines belong to that kept occurrence.
 *
 * Args:
 *   segments: Segments parsed so far.
 *   count: Number of trailing lines to hide.
 */
function trimKeptBlock(segments: ThinkSegment[], count: number): void {
  if (count <= 0) return;
  const last = segments[segments.length - 1];
  if (!last || last.kind !== "text") return;
  const lines = last.text.split("\n");
  if (lines.length < count) return;
  const remaining = lines.slice(0, -count);
  while (remaining.length && !remaining[remaining.length - 1].trim()) {
    remaining.pop();
  }
  if (remaining.length) {
    last.text = remaining.join("\n");
  } else {
    segments.pop();
  }
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
      trimKeptBlock(segments, Number(period[2]));
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
      trimKeptBlock(segments, 1);
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

/**
 * Return the last text segment, excluding all marker and folded-block content.
 *
 * Args:
 *   text: Reasoning content, possibly containing sanitizer marker lines.
 *
 * Returns:
 *   The text after the last marker, or the whole text when there is none.
 */
export function lastTextSegment(text: string): string {
  const segments = parseThinkSegments(text);
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const segment = segments[index];
    if (segment.kind === "text") return segment.text;
  }
  return "";
}
