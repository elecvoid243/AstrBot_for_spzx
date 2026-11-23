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
const LINE_BREAK = /\r\n?|\n/;

type MarkerSegment = Exclude<ThinkSegment, { kind: "text" }>;

/** Parse one marker line, or return null when it is ordinary text. */
function parseMarkerLine(line: string): MarkerSegment | null {
  const trimmed = line.trim();

  const period = PERIOD_MARKER.exec(trimmed);
  if (period) {
    return {
      kind: "period",
      repeats: Number(period[1]),
      period: Number(period[2]),
    };
  }

  const run = RUN_MARKER.exec(trimmed);
  if (run) {
    return { kind: "run", repeats: Number(run[1]) };
  }

  const fallback = FALLBACK_MARKER.exec(trimmed);
  if (fallback) {
    return {
      kind: "fallback",
      lines: Number(fallback[1]),
      chars: Number(fallback[2]),
    };
  }

  return null;
}

/** Return true when a line is one of the known sanitizer markers. */
export function isSanitizerMarkerLine(line: string): boolean {
  return parseMarkerLine(line) !== null;
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

  for (const line of text.split(LINE_BREAK)) {
    const marker = parseMarkerLine(line);
    if (marker) {
      const keptLines =
        marker.kind === "period"
          ? marker.period
          : marker.kind === "run"
          ? 1
          : 0;
      // Count blank lines in the kept block before trimming display whitespace.
      if (buffer.length >= keptLines) buffer.length -= keptLines;
      flush();
      segments.push(marker);
      continue;
    }

    buffer.push(line);
  }

  flush();
  return segments;
}

/**
 * Rebuild the original view with each marker at its original position.
 *
 * The sanitized text is the original with repeated blocks removed, so its
 * lines are an ordered subsequence of the original. Walking both line lists
 * lets us insert each marker after the first occurrence it replaced, instead
 * of collecting all markers at the top of the original text.
 *
 * Args:
 *   sanitized: Folded reasoning text with marker lines.
 *   original: Raw reasoning text without markers.
 *
 * Returns:
 *   Original text segments interleaved with marker segments.
 */
export function buildOriginalSegments(
  sanitized: string,
  original: string,
): ThinkSegment[] {
  const sanitizedLines = sanitized.split(LINE_BREAK);
  const originalLines = original.split(LINE_BREAK);
  const insertions: { position: number; marker: MarkerSegment }[] = [];
  let originalIndex = 0;

  for (const line of sanitizedLines) {
    const marker = parseMarkerLine(line);
    if (marker) {
      insertions.push({ position: originalIndex, marker });
      continue;
    }
    const trimmed = line.trim();
    if (!trimmed) continue;
    let found = -1;
    for (let index = originalIndex; index < originalLines.length; index += 1) {
      if (originalLines[index].trim() === trimmed) {
        found = index;
        break;
      }
    }
    if (found === -1) continue;
    originalIndex = found + 1;
  }

  const segments: ThinkSegment[] = [];
  let cursor = 0;
  for (const insertion of insertions) {
    const position = Math.min(insertion.position, originalLines.length);
    if (position > cursor) {
      segments.push({
        kind: "text",
        text: originalLines.slice(cursor, position).join("\n"),
      });
    }
    segments.push(insertion.marker);
    cursor = position;
  }
  if (cursor < originalLines.length) {
    segments.push({
      kind: "text",
      text: originalLines.slice(cursor).join("\n"),
    });
  }
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
