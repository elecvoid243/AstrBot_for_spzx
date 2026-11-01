/**
 * fileChangeTool — extraction & collection helpers for the
 * file-edit / file-write tool calls.
 *
 * Feature (2026-08-11, approved design): surface file changes on the
 * ReasoningBlock collapsed bar (per-file chips) and pin FileChangeCard
 * entries on top of the ReasoningTimeline, so users notice file edits
 * without opening the reasoning view.
 *
 * The edit-result parsers are the exact logic previously inlined in
 * ToolCallCard.vue (which now delegates here); `collectFileChanges`
 * distills the thinking-block parts into a flat, ordered list of
 * per-file change entries for the chips / pinned cards.
 *
 * Author: elecvoid243 | 2026-08-11
 */

import { findSystemNoticeIndex } from "@/utils/systemNotice";

export const FILE_EDIT_TOOL_NAME = "astrbot_file_edit_tool";
export const FILE_WRITE_TOOL_NAME = "astrbot_file_write_tool";
export const APPLY_PATCH_TOOL_NAME = "astrbot_apply_patch";

/** 2026-08-11: file-removal tool names. The suffixed form is the
 *  documented name; the bare form appears in some runtimes. */
export const FILE_REMOVE_TOOL_NAMES: ReadonlySet<string> = new Set([
    "astrbot_file_remove_tool",
    "astrbot_file_remove",
]);

/** Whether the tool name is one of the file-change tools. */
export function isFileChangeToolName(name: string): boolean {
    return (
        name === FILE_EDIT_TOOL_NAME ||
        name === FILE_WRITE_TOOL_NAME ||
        name === APPLY_PATCH_TOOL_NAME ||
        FILE_REMOVE_TOOL_NAMES.has(name)
    );
}

export interface FileEditResultParts {
    /** Result text with the [SYSTEM NOTICE] suffix stripped. */
    cleanResult: string;
    /** The stripped system-notice suffix, or null when absent. */
    notice: string | null;
    /** Extracted unified diff ( fences removed ), or the clean text. */
    diff: string;
    /** Edited file path, or "" when it cannot be determined. */
    filePath: string;
    /** Status lines preceding the Diff section. */
    summary: string;
}

/**
 * Parse an `astrbot_file_edit_tool` result into its display parts.
 * Pure-function form of the computeds previously inlined in
 * ToolCallCard.vue — behavior must stay identical.
 */
export function parseFileEditResult(raw: string): FileEditResultParts {
    const text = raw ?? "";
    const idx = findSystemNoticeIndex(text);
    const cleanResult = idx < 0 ? text : text.slice(0, idx).trim();
    const notice = idx < 0 ? null : text.slice(idx).trim();

    // The closing fence is anchored to column 0 (`^```/m`) instead of
    // matched bare: every unified-diff body line carries a `+`/`-`/space
    // prefix, so a code fence inside an edited .md file can never sit at
    // column 0. A bare ``` match would stop at the first embedded fence
    // (e.g. `+```python`) and silently truncate the diff.
    const diffMatch = cleanResult.match(
        /```diff[ \t]*\r?\n([\s\S]*?)^```[ \t]*\r?$/m,
    );
    const diff = diffMatch ? diffMatch[1] : cleanResult;

    // Success: "Edited {path}." followed by "Replaced {N} occurrence(s)...".
    // Anchoring on "Replaced" prevents the regex from stopping at a period
    // inside the path itself (e.g. the ".py" in "D:\work\test.py").
    const successMatch = cleanResult.match(/^Edited\s+(.+?)\.\s+Replaced/m);
    // Error: "Error editing file: [{path}]: ..." — the backend wraps the
    // path in brackets so Windows drive-letter colons don't confuse the
    // capture group.
    const errorMatch = cleanResult.match(/^Error editing file:\s*\[(.+?)\]:/);
    const filePath = successMatch?.[1] ?? errorMatch?.[1] ?? "";

    const statusParts: string[] = [];
    for (const line of cleanResult.split("\n")) {
        if (line.startsWith("Diff:") || line.startsWith("```")) break;
        if (line.trim()) statusParts.push(line.trim());
    }

    return {
        cleanResult,
        notice,
        diff,
        filePath,
        summary: statusParts.join("\n"),
    };
}

export interface DiffStat {
    adds: number;
    dels: number;
}

export interface ApplyPatchFileEntry {
    kind: FileChangeKind;
    filePath: string;
    renamedFrom?: string;
    diff: string;
    adds: number;
    dels: number;
    lineCount: number | null;
}

const APPLY_PATCH_HEADER_RE = /^Applied patch:\s+\d+\s+files?\s+changed/i;
const APPLY_PATCH_FILE_RE = /^(UPDATE|ADD|DELETE)\s+(.+?)(?:\s+->\s+(.+))?$/;

/** Split a concatenated multi-file unified diff by `--- path` / `+++ path`. */
function splitPatchDiff(text: string): Map<string, string> {
    const lines = text.split("\n");
    const sections = new Map<string, string[]>();
    let current: string[] | null = null;
    for (let i = 0; i < lines.length; i++) {
        const minus = lines[i].match(/^--- (.+)$/);
        if (minus && lines[i + 1]?.startsWith("+++ ")) {
            const plusPath = lines[i + 1].slice(4).trim();
            if (plusPath === minus[1].trim()) {
                current = [lines[i], lines[i + 1]];
                sections.set(minus[1].trim(), current);
                i += 1;
                continue;
            }
        }
        current?.push(lines[i]);
    }
    return new Map([...sections].map(([path, body]) => [path, body.join("\n")]));
}

function countDiffAdds(diff: string): number {
    let count = 0;
    for (const line of (diff ?? "").split("\n")) {
        if (line.startsWith("+++")) continue;
        if (line.startsWith("+")) count++;
    }
    return count;
}

export interface ApplyPatchDisplayParts {
    /** Header line, e.g. "Applied patch: 2 files changed (+2 -1)". */
    summary: string;
    /** Concatenated unified diff (everything after the file list). */
    diff: string;
}

/**
 * Split an apply_patch success result into its header and diff body.
 *
 * Returns null for non-success results (dry run, confirmation gate, error),
 * so ToolCallCard keeps rendering those as plain text.
 */
export function parseApplyPatchDisplay(
    raw: string,
): ApplyPatchDisplayParts | null {
    const lines = (raw ?? "").split("\n");
    const headerIndex = lines.findIndex((line) =>
        APPLY_PATCH_HEADER_RE.test(line),
    );
    if (headerIndex < 0) return null;

    let cursor = headerIndex + 1;
    while (cursor < lines.length && lines[cursor].trim()) cursor++;
    const diff = lines.slice(cursor + 1).join("\n").trim();
    return { summary: lines[headerIndex], diff };
}

/**
 * Parse an `astrbot_apply_patch` result into one entry per file.
 *
 * The backend prints a header, a file list, a blank line, then the
 * concatenated unified diff. A result without the header is an error and
 * yields an empty list (callers fall back to the args parse).
 */
export function parseApplyPatchResult(raw: string): ApplyPatchFileEntry[] {
    const lines = (raw ?? "").split("\n");
    const headerIndex = lines.findIndex((line) =>
        APPLY_PATCH_HEADER_RE.test(line),
    );
    if (headerIndex < 0) return [];

    const specs: Array<{
        token: "UPDATE" | "ADD" | "DELETE";
        path: string;
        moveTo?: string;
    }> = [];
    let cursor = headerIndex + 1;
    for (; cursor < lines.length; cursor++) {
        const line = lines[cursor];
        if (!line.trim()) break;
        const match = line.match(APPLY_PATCH_FILE_RE);
        if (!match) continue;
        specs.push({
            token: match[1] as "UPDATE" | "ADD" | "DELETE",
            path: match[2].trim(),
            moveTo: match[3]?.trim(),
        });
    }

    const sections = splitPatchDiff(lines.slice(cursor + 1).join("\n"));
    return specs.map((spec) => {
        const diff = sections.get(spec.path) ?? "";
        const stat = computeDiffStat(diff);
        if (spec.moveTo) {
            return {
                kind: "write",
                filePath: spec.moveTo,
                renamedFrom: spec.path,
                diff,
                adds: stat.adds,
                dels: stat.dels,
                lineCount: null,
            };
        }
        if (spec.token === "ADD") {
            return {
                kind: "write",
                filePath: spec.path,
                diff,
                adds: stat.adds,
                dels: 0,
                lineCount: countDiffAdds(diff),
            };
        }
        if (spec.token === "DELETE") {
            return {
                kind: "remove",
                filePath: spec.path,
                diff: "",
                adds: 0,
                dels: 0,
                lineCount: null,
            };
        }
        return {
            kind: "edit",
            filePath: spec.path,
            diff,
            adds: stat.adds,
            dels: stat.dels,
            lineCount: null,
        };
    });
}

/**
 * Parse the `patch` argument to list target files while the call runs.
 *
 * Mirrors the backend's lenient grammar: `*** Add/Update/Delete File:` and
 * `*** Move to:` markers. Diffs are empty until the real result arrives.
 */
export function parseApplyPatchArgs(
    args: Record<string, unknown>,
): ApplyPatchFileEntry[] {
    const patch = typeof args.patch === "string" ? args.patch : "";
    if (!patch.trim()) return [];

    const specs: Array<{
        token: "UPDATE" | "ADD" | "DELETE";
        path: string;
        moveTo?: string;
    }> = [];
    let pendingUpdate: (typeof specs)[number] | null = null;
    for (const rawLine of patch.split("\n")) {
        const line = rawLine.trim();
        const add = line.match(/^\*\*\* Add File:\s*(.+)$/);
        if (add) {
            specs.push({ token: "ADD", path: add[1].trim() });
            pendingUpdate = null;
            continue;
        }
        const del = line.match(/^\*\*\* Delete File:\s*(.+)$/);
        if (del) {
            specs.push({ token: "DELETE", path: del[1].trim() });
            pendingUpdate = null;
            continue;
        }
        const update = line.match(/^\*\*\* Update File:\s*(.+)$/);
        if (update) {
            pendingUpdate = { token: "UPDATE", path: update[1].trim() };
            specs.push(pendingUpdate);
            continue;
        }
        const move = line.match(/^\*\*\* Move to:\s*(.+)$/);
        if (move && pendingUpdate) {
            pendingUpdate.moveTo = move[1].trim();
        }
    }

    const seen = new Set<string>();
    const entries: ApplyPatchFileEntry[] = [];
    for (const spec of specs) {
        const filePath = spec.moveTo ?? spec.path;
        if (seen.has(filePath)) continue;
        seen.add(filePath);
        const kind =
            spec.moveTo || spec.token === "ADD"
                ? "write"
                : spec.token === "DELETE"
                  ? "remove"
                  : "edit";
        entries.push({
            kind,
            filePath,
            renamedFrom: spec.moveTo ? spec.path : undefined,
            diff: "",
            adds: 0,
            dels: 0,
            lineCount: null,
        });
    }
    return entries;
}

/** Count +/- lines in a unified diff, excluding +++/--- file headers. */
export function computeDiffStat(diffText: string): DiffStat {
    let adds = 0;
    let dels = 0;
    for (const line of (diffText ?? "").split("\n")) {
        if (line.startsWith("+++") || line.startsWith("---")) continue;
        if (line.startsWith("+")) adds++;
        else if (line.startsWith("-")) dels++;
    }
    return { adds, dels };
}

export type FileChangeKind = "edit" | "write" | "remove";
export type FileChangeStatus = "running" | "done" | "error";

/** Last path segment (Windows- and POSIX-style separators). */
export function fileBasename(path: string): string {
    if (!path) return "";
    const segments = path.split(/[\\/]/);
    return segments[segments.length - 1] || path;
}

/**
 * Strip the trailing ` (encoding: xxx)` marker the write tool appends to
 * its success result (`File written successfully: <path> (encoding: utf-8)`).
 *
 * 2026-09-09: the marker is display-only — the file on disk never carries
 * it. `collectFileChanges` keeps it inside `filePath` so the card can show
 * it, but every API call built from that path (open on disk / open folder)
 * must use this stripped form.
 */
export function stripWriteEncodingMarker(path: string): string {
    return (path ?? "").replace(/\s*\(encoding:\s*[^)]*\)\s*$/i, "");
}

/**
 * Color tone for a file-change entry, used to tint FileChangeCard and
 * the ReasoningBlock chips (2026-08-11): write (new/whole-file) →
 * green, edit (modification) → yellow, remove (deletion) → red.
 */
export type FileChangeTone = "green" | "yellow" | "red";

export function fileChangeTone(
    entry: Pick<FileChangeEntry, "kind">,
): FileChangeTone {
    if (entry.kind === "write") return "green";
    if (entry.kind === "remove") return "red";
    return "yellow";
}

export interface FileChangeEntry {
    /** Tool call id, or a synthesized positional fallback. */
    callId: string;
    kind: FileChangeKind;
    filePath: string;
    status: FileChangeStatus;
    /** edit only: +/− line counts from the result diff. */
    diffStat: DiffStat | null;
    /** write only: line count of the written content (args.content). */
    lineCount: number | null;
    /** apply_patch only: this file's unified diff section. */
    diff?: string;
    /** apply_patch only: DiffPreview summary (e.g. "UPDATE src/a.py"). */
    summary?: string;
    /** apply_patch Move only: the source path. */
    renamedFrom?: string;
    /** The raw (loosely typed) tool call object for card rendering. */
    tool: Record<string, unknown>;
}

/** Args may arrive as a JSON string on the wire shape; normalize to object. */
export function normalizeToolArgs(
    args: unknown,
): Record<string, unknown> {
    if (typeof args === "string") {
        try {
            const parsed = JSON.parse(args);
            if (parsed && typeof parsed === "object") {
                return parsed as Record<string, unknown>;
            }
        } catch {
            // fall through to empty object
        }
        return {};
    }
    if (args && typeof args === "object") {
        return args as Record<string, unknown>;
    }
    return {};
}

/**
 * Collect file-change entries from message parts (typically the parts of
 * a thinking block), preserving their original order. Non-file tools and
 * non-tool_call parts are skipped.
 */
export function collectFileChanges(
    parts: Array<Record<string, unknown>> | undefined | null,
): FileChangeEntry[] {
    const entries: FileChangeEntry[] = [];
    if (!Array.isArray(parts)) return entries;

    parts.forEach((part, partIndex) => {
        if (!part || part.type !== "tool_call") return;
        const toolCalls = part.tool_calls;
        if (!Array.isArray(toolCalls)) return;

        toolCalls.forEach((tool, toolIndex) => {
            if (!tool || typeof tool !== "object") return;
            const call = tool as Record<string, unknown>;
            const name = String(call.name ?? "");
            if (!isFileChangeToolName(name)) return;

            const args = normalizeToolArgs(call.args ?? call.arguments);
            const result = typeof call.result === "string" ? call.result : "";
            const running = call.finished_ts == null;
            const status: FileChangeStatus = running
                ? "running"
                : /^error\b/i.test(result.trim())
                  ? "error"
                  : "done";

            const base = {
                callId: String(
                    call.id ?? `${partIndex}-${toolIndex}`,
                ),
                status,
                tool: call,
            };

            if (name === APPLY_PATCH_TOOL_NAME) {
                const finished = !running && result.trim().length > 0;
                const parsedResult = finished
                    ? parseApplyPatchResult(result)
                    : [];
                // A failed patch has no `Applied patch:` header; fall back
                // to the args parse so attempted files still show up with
                // status=error.
                const files = parsedResult.length
                    ? parsedResult
                    : parseApplyPatchArgs(args);
                for (const file of files) {
                    entries.push({
                        ...base,
                        kind: file.kind,
                        filePath: file.filePath,
                        diffStat:
                            file.kind === "edit"
                                ? { adds: file.adds, dels: file.dels }
                                : null,
                        lineCount: file.lineCount,
                        diff: file.diff,
                        summary:
                            file.kind === "edit"
                                ? `UPDATE ${file.renamedFrom ?? file.filePath}`
                                : undefined,
                        renamedFrom: file.renamedFrom,
                    });
                }
            } else if (name === FILE_EDIT_TOOL_NAME) {
                const parsed = parseFileEditResult(result);
                entries.push({
                    ...base,
                    kind: "edit",
                    filePath:
                        parsed.filePath || String(args.path ?? "") || "",
                    diffStat: result ? computeDiffStat(parsed.diff) : null,
                    lineCount: null,
                });
            } else if (FILE_REMOVE_TOOL_NAMES.has(name)) {
                entries.push({
                    ...base,
                    kind: "remove",
                    filePath: String(args.path ?? "") || "",
                    diffStat: null,
                    lineCount: null,
                });
            } else {
                const content = String(args.content ?? "");
                // 2026-08-21: prefer the backend-resolved absolute path from
                // the success result ("File written successfully: {path}");
                // args.path may be relative (the backend resolves it against
                // the workspace root, so only the result knows the real
                // location). Fall back to args.path while the call is
                // running or when no result is available.
                const writtenPath =
                    result.match(/^File written successfully:\s+(.+)$/m)?.[1] ??
                    "";
                entries.push({
                    ...base,
                    kind: "write",
                    filePath: writtenPath || String(args.path ?? "") || "",
                    diffStat: null,
                    lineCount: content ? content.split("\n").length : null,
                });
            }
        });
    });

    return entries;
}

/** One aggregated file entry from the backend end-of-turn
 * `file_changes` event (net diff vs the turn's baseline backup). */
export interface FileChangeSummaryFile {
    restore_available?: boolean;
    path: string;
    kind: "edit" | "write" | "created" | "rollback" | "remove";
    adds: number | null;
    dels: number | null;
    backup_id: string;
    sha256: string;
    runtime: string;
    diff_available: boolean;
}

/** Parse the `data` field of a `file_changes` event (object or raw JSON
 * string) into a validated file list. */
export function parseFileChangesPayload(
    data: unknown,
): FileChangeSummaryFile[] {
    let parsed: unknown = data;
    if (typeof parsed === "string") {
        try {
            parsed = JSON.parse(parsed);
        } catch {
            return [];
        }
    }
    const files = (parsed as { files?: unknown } | null)?.files;
    if (!Array.isArray(files)) return [];
    return files.filter(
        (f): f is FileChangeSummaryFile =>
            !!f &&
            typeof f === "object" &&
            typeof (f as FileChangeSummaryFile).path === "string",
    );
}
