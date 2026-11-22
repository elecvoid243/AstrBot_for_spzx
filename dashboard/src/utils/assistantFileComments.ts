export interface AssistantFileComment {
  title: string;
  body: string;
  path: string;
  file: string;
  start_line: number;
  end_line: number;
  priority: number | null;
  revision: string;
  directive: string;
}

export function assistantFileComments(
  parts: readonly { type: string; comments?: unknown }[],
): AssistantFileComment[] {
  return parts.flatMap((part) => {
    if (
      part.type !== "assistant_file_comments" ||
      !Array.isArray(part.comments)
    )
      return [];
    return part.comments.filter(
      (comment): comment is AssistantFileComment =>
        !!comment &&
        typeof comment.title === "string" &&
        typeof comment.body === "string" &&
        typeof comment.file === "string" &&
        typeof comment.path === "string" &&
        /^(?:[A-Za-z]:[\\/]|\/(?!\/))/.test(comment.path) &&
        typeof comment.revision === "string" &&
        /^[a-f0-9]{64}$/.test(comment.revision) &&
        typeof comment.directive === "string" &&
        Number.isSafeInteger(comment.start_line) &&
        comment.start_line > 0 &&
        Number.isSafeInteger(comment.end_line) &&
        comment.end_line >= comment.start_line &&
        (comment.priority === null ||
          (Number.isInteger(comment.priority) &&
            comment.priority >= 0 &&
            comment.priority <= 3)),
    );
  });
}

export function withoutAssistantFileDirectives(
  text: string,
  comments: readonly AssistantFileComment[],
): string {
  if (!comments.length || !text.includes("::code-comment{")) return text;
  const directives = new Set(comments.map((comment) => comment.directive));
  let fence = "";
  return text
    .split(/\r?\n/)
    .filter((line) => {
      const marker = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
      if (fence) {
        if (marker?.[1].startsWith(fence) && !marker[2].trim()) fence = "";
        return true;
      }
      if (marker && (marker[1][0] === "~" || !marker[2].includes("`"))) {
        fence = marker[1];
        return true;
      }
      return (
        !/^ {0,3}::code-comment\{/.test(line) || !directives.has(line.trim())
      );
    })
    .join("\n");
}

export function assistantCommentPathKey(path: string): string {
  return /^[A-Za-z]:[\\/]/.test(path)
    ? path.replace(/\\/g, "/").toLowerCase()
    : path;
}
