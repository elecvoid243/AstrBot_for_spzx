// Author: elecvoid243, 2026-07-30
// Parser for the "[File review comments]" block that the dashboard appends
// to outgoing user messages (see useFileComments.formatForLLM). The block is
// stored verbatim in the message history, so parsing it back into structured
// data lets the chat UI render a rich review card for BOTH freshly sent and
// historical messages — no backend change required.
//
// The grammar below mirrors formatForLLM's exact output:
//   [File review comments]            <- block marker (first line)
//   <4 prose lines for the LLM>       <- ignored for display
//   `path` line N:                    <- window (file-browser) section header
//   ````                              <- 4-backtick fence
//     <ctx line>  "  " marker " " pad(no) " │ " content
//     <anchor>    "  > " pad(no) " │ " content, followed by comment lines
//       comment line: 9 spaces + "│ Comment: " / "│ " continuation
//   ````
//   `path` (in diff hunk @@ ... @@):  <- hunk section header
//   ````
//   @@ ... @@                         <- raw hunk header line
//     <line> "  " marker " " pad(no) " │ " (+|-| ) content
//   ````
//   Comments:                         <- hunk comment list
//     - line N: text / continuation (4-space indent)

/** A single rendered line inside a review code block. */
export interface ReviewLine {
  /** 1-based line number in the (new-side) file. */
  no: number;
  /** The line's textual content (without the diff +/- prefix). */
  text: string;
  /** True when the user anchored a comment on this line (the ">" marker). */
  anchor: boolean;
  /** Diff line kind, present only for hunk sections. */
  type?: "add" | "del" | "ctx";
  /** Raw hunk header for the special "@@ ... @@" line (no `no`). */
  hunkHeader?: string;
  /** Inline comment text, present only on a window-section anchor line. */
  comment?: string;
}

/** A comment entry listed under a hunk section. */
export interface ReviewComment {
  line: number;
  text: string;
}

/** One file section inside the review block. */
export interface ReviewFile {
  path: string;
  kind: "window" | "hunk";
  /** Human label: "line N" / "lines N-M" for window, hunk header for hunk. */
  meta: string;
  /** For hunk sections, the raw "@@ ... @@" header. */
  hunkHeader?: string;
  lines: ReviewLine[];
  comments: ReviewComment[];
}

/** The parsed review block plus the user's free-form text before it. */
export interface FileReviewBlock {
  userText: string;
  files: ReviewFile[];
  /** Total number of comments across all files. */
  totalComments: number;
}

const BLOCK_MARKER = "[File review comments]";
const FENCE = /^`{4}\s*$/;
const WIN_HEADER = /^`(.+?)` lines? (\d+)(?:-(\d+))?:\s*$/;
const HUNK_HEADER = /^`(.+?)` \(in diff hunk (.+)\):\s*$/;
// "  " + marker + up to 4 spaces (padStart(4) + separator) + number + " │ "
const WIN_CODE = /^ {2}([> ]) {0,4}(\d+) │ (.*)$/;
// same shape but the first content char is the diff prefix (+/-/space)
const HUNK_CODE = /^ {2}([> ]) {0,4}(\d+) │ ([+\- ])(.*)$/;
// 9 spaces + "│ " comment gutter (aligned with the code lines' "│")
const COMMENT_START = /^\s{9}│ Comment: (.*)$/;
const COMMENT_CONT = /^\s{9}│ (.*)$/;
const HUNK_COMMENT_START = /^ {2}- line (\d+): (.*)$/;
const HUNK_COMMENT_CONT = /^ {4}(.*)$/;
const HUNK_HDR_LINE = /^@@.*@@$/;

/**
 * Parse a user message that may end with a "[File review comments]" block.
 *
 * Returns `null` when the marker is absent or no file section could be
 * parsed, so callers can fall back to rendering the raw text unchanged.
 *
 * @param raw - The full plain-text content of a user message part.
 * @returns The structured review block, or `null` if not present/parseable.
 */
export function parseFileReviewComments(raw: string): FileReviewBlock | null {
  if (!raw) return null;
  // 2026-10-18 (elecvoid243): the block is appended to the outgoing message
  // and stored with the platform's line endings. On Windows that is CRLF;
  // splitting on "\n" left a trailing "\r" on every line, and the hunk code
  // regexes (which end in `$` and use `.` for the content) silently dropped
  // every line, leaving an empty quoted diff in the review card. Normalise
  // CRLF / lone CR before parsing.
  const lines = raw.replace(/\r\n?/g, "\n").split("\n");
  const start = lines.findIndex((l) => l.trim() === BLOCK_MARKER);
  if (start < 0) return null;

  const userText = lines.slice(0, start).join("\n").trim();

  const files: ReviewFile[] = [];
  let current: ReviewFile | null = null;
  let inFence = false;
  // Tracks the last anchored line so window comment lines can attach to it.
  let lastAnchor: ReviewLine | null = null;
  // After a hunk fence closes, trailing "Comments:" list entries are expected.
  let expectHunkComments = false;
  let lastHunkComment: ReviewComment | null = null;

  for (let i = start + 1; i < lines.length; i++) {
    const line = lines[i];

    if (FENCE.test(line)) {
      if (inFence) {
        inFence = false;
        if (current && current.kind === "hunk") expectHunkComments = true;
      } else {
        inFence = true;
      }
      continue;
    }

    if (inFence && current) {
      if (current.kind === "window") {
        const cs = line.match(COMMENT_START);
        if (cs) {
          if (lastAnchor) {
            lastAnchor.comment = lastAnchor.comment
              ? `${lastAnchor.comment}\n${cs[1]}`
              : cs[1];
          }
          continue;
        }
        const cc = line.match(COMMENT_CONT);
        if (cc) {
          if (lastAnchor && lastAnchor.comment) {
            lastAnchor.comment += `\n${cc[1]}`;
          }
          continue;
        }
        const code = line.match(WIN_CODE);
        if (code) {
          const entry: ReviewLine = {
            no: Number(code[2]),
            text: code[3],
            anchor: code[1] === ">",
          };
          if (entry.anchor) lastAnchor = entry;
          current.lines.push(entry);
        }
      } else {
        if (HUNK_HDR_LINE.test(line)) {
          current.lines.push({ no: 0, text: "", anchor: false, hunkHeader: line });
          continue;
        }
        const code = line.match(HUNK_CODE);
        if (code) {
          const prefix = code[3];
          current.lines.push({
            no: Number(code[2]),
            text: code[4],
            anchor: code[1] === ">",
            type: prefix === "+" ? "add" : prefix === "-" ? "del" : "ctx",
          });
        }
      }
      continue;
    }

    const win = line.match(WIN_HEADER);
    if (win) {
      current = {
        path: win[1],
        kind: "window",
        meta: win[3] ? `${win[2]}-${win[3]}` : win[2],
        lines: [],
        comments: [],
      };
      files.push(current);
      lastAnchor = null;
      expectHunkComments = false;
      continue;
    }

    const hunk = line.match(HUNK_HEADER);
    if (hunk) {
      current = {
        path: hunk[1],
        kind: "hunk",
        meta: hunk[2],
        hunkHeader: hunk[2],
        lines: [],
        comments: [],
      };
      files.push(current);
      lastAnchor = null;
      expectHunkComments = false;
      continue;
    }

    if (expectHunkComments && current) {
      const trimmed = line.trim();
      if (trimmed === "Comments:" || trimmed === "") continue;
      const hs = line.match(HUNK_COMMENT_START);
      if (hs) {
        lastHunkComment = { line: Number(hs[1]), text: hs[2] };
        current.comments.push(lastHunkComment);
        continue;
      }
      const hc = line.match(HUNK_COMMENT_CONT);
      if (hc && lastHunkComment) {
        lastHunkComment.text += `\n${hc[1]}`;
        continue;
      }
      expectHunkComments = false;
    }
    // Prose / blank lines outside any section are intentionally skipped.
  }

  // Window comments live inline on anchor lines; surface them in `comments`
  // too so callers can count/iterate uniformly across both section kinds.
  let totalComments = 0;
  for (const file of files) {
    if (file.kind === "window") {
      for (const l of file.lines) {
        if (l.comment) file.comments.push({ line: l.no, text: l.comment });
      }
    }
    totalComments += file.comments.length;
  }

  if (files.length === 0) return null;
  return { userText, files, totalComments };
}
