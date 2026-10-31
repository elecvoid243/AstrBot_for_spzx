/**
 * astrbot_shell_session 工具结果的类型与解析。
 *
 * 后端 (astrbot/core/tools/computer_tools/shell.py → LocalShellComponent)
 * 按 action 返回三种 JSON 形态:
 *
 * - poll / interrupt / terminate:
 *   `{session_id, pid, status, stdout, stderr, exit_code, cursor,
 *     has_more, session_closed}`
 * - write / write_line:
 *   `{session_id, pid, status: "running", written_chars}`
 * - list:
 *   `{sessions: [{session_id, pid, status, exit_code, started_at,
 *     sandboxed, unread_output_bytes}]}`
 *
 * 解析直接复用 @/utils/shellToolResult 的 parseShellToolResult:它对
 * stdout 内任意花括号做字符串感知追踪,并能切分后端溢出截断与
 * [SYSTEM NOTICE] 后缀,避免在此重复一套脆弱的正则。
 *
 * Author: elecvoid243 | 2026-08-11
 */

import { parseShellToolResult } from "@/utils/shellToolResult";
import type { ShellSessionAction } from "./icons";

/** poll / interrupt / terminate 的结果(增量输出 + 会话状态)。 */
export interface ShellSessionPollResult {
    session_id: string;
    pid: number;
    status: string;
    stdout: string;
    stderr: string;
    exit_code: number | null;
    cursor: number;
    has_more: boolean;
    session_closed: boolean;
}

/** write / write_line 的结果。 */
export interface ShellSessionWriteResult {
    session_id: string;
    pid: number;
    status: string;
    written_chars: number;
}

/** list 结果中的单个会话摘要。 */
export interface ShellSessionListItem {
    command?: string;
    session_id: string;
    pid: number;
    status: string;
    exit_code: number | null;
    started_at: number | null;
    sandboxed: boolean;
    unread_output_bytes: number;
}

export interface ParsedShellSessionResult {
    /** 解析出的 JSON 对象;截断/非 JSON(纯文本错误)时为 null。 */
    json: Record<string, unknown> | null;
    /** 尾部系统通知([SYSTEM NOTICE] 等),无则为 null。 */
    extra: string | null;
}

/**
 * 解析 shell_session 工具返回的原始文本。
 * 返回值的 json 为 null 时调用方应降级为纯文本渲染。
 */
export function parseShellSessionResult(raw: string): ParsedShellSessionResult {
    return parseShellToolResult(raw ?? "");
}

/**
 * 推断结果对应的 action。优先使用工具调用参数;缺失时按 JSON 形态推断:
 * 含 sessions 数组 → list;含 written_chars → write;其余按 poll 形态处理
 * (poll / interrupt / terminate 结构相同)。
 */
export function inferShellSessionAction(
    args: Record<string, unknown> | undefined,
    json: Record<string, unknown> | null,
): ShellSessionAction {
    const fromArgs = args?.action;
    if (typeof fromArgs === "string" && fromArgs) {
        return fromArgs as ShellSessionAction;
    }
    if (json) {
        if (Array.isArray(json.sessions)) return "list";
        if (typeof json.written_chars === "number") return "write";
    }
    return "poll";
}

/** 把字节数格式化为紧凑文本(如 "512 B" / "1.2 KB")。 */
export function formatBytes(n: number | null | undefined): string {
    if (n === null || n === undefined || !Number.isFinite(n) || n < 0) {
        return "—";
    }
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
