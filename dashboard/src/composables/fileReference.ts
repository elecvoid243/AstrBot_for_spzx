import type { InjectionKey } from "vue";

/** A host path and its optional one-based, closed source-line range. */
export interface FileReferenceTarget {
  path: string;
  startLine: number | null;
  endLine: number | null;
}

export interface FileReferenceNavigation extends FileReferenceTarget {
  requestId: number;
}

export const OPEN_FILE_REFERENCE: InjectionKey<
  (target: FileReferenceTarget) => void
> = Symbol("openFileReference");

/** Parse an explicit Markdown destination; never resolve it against a project. */
export function parseFileReference(href: string): FileReferenceTarget | null {
  const match = /^(.*?)(?:#L([1-9]\d*)(?:-L([1-9]\d*))?)?$/.exec(href);
  if (!match) return null;
  let path: string;
  try {
    // Decode exactly once, after separating the fragment: %23 belongs to a filename.
    path = decodeURIComponent(match[1]);
  } catch {
    return null;
  }
  if (!/^(?:[A-Za-z]:[\\/]|\/(?!\/))/.test(path)) return null;
  if (/[\x00-\x1f]/.test(path) || /[?#]/.test(match[1])) return null;
  const startLine = match[2] ? Number(match[2]) : null;
  const endLine = match[3] ? Number(match[3]) : startLine;
  if (
    startLine !== null &&
    (!Number.isSafeInteger(startLine) ||
      !Number.isSafeInteger(endLine) ||
      endLine! < startLine)
  )
    return null;
  return { path, startLine, endLine };
}
