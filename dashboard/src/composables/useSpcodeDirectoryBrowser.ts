// dashboard/src/composables/useSpcodeDirectoryBrowser.ts
//
// Directory-browser state/actions for the spcode "打开文件浏览器" picker
// (ProjectDirectoryBrowser.vue). The browser lists ONLY directories and
// never synthesizes paths: every path it emits (entry.path, currentPath,
// breadcrumb segment.path) round-trips from the backend file-browser /
// home-directory endpoints, or is derived by pure string-splitting of a
// backend-returned absolute path (spec 4.3 — 前端永不拼接路径).
//
// Author: elecvoid243, 2026-08-15
// Spec: D:\AstrbotWorkSpace\spcode-目录选择器设计方案.md §4.2 / §4.3

import { computed, ref, toValue, type MaybeRef } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import {
  FileBrowserParseError,
  parseSpcodeFileBrowser,
  type SpcodeFileBrowserEntry,
  type SpcodeFileBrowserRawResponse,
} from "@/composables/parseSpcodeFileBrowser";

/** One clickable breadcrumb segment (root first). */
export interface DirectorySegment {
  /** Display label (e.g. "Users" or "C:/") */
  label: string;
  /** Absolute path this segment represents — used to re-list on click. */
  path: string;
}

/** Normalized absolute path with leading/trailing separators stripped to
 *  the OS separator detected in the input (keeps backend fidelity). */
function stripTrailingSeparators(p: string): string {
  return p.replace(/[\\/]+$/, "");
}

/** Parent of an absolute path via pure string-splitting; null at a root. */
export function parentOf(p: string): string | null {
  const trimmed = stripTrailingSeparators(p);
  // Windows drive root ("C:" after stripping trailing sep) and POSIX root
  // ("") have no parent.
  if (trimmed === "" || /^[A-Za-z]:$/.test(trimmed)) return null;
  const idx = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  if (idx < 0) return null;
  const parent = trimmed.slice(0, idx);
  // POSIX root returns "/" (the stripped form of "/home" → "" handled above
  // by the empty check only for the root itself; here we re-add it).
  if (parent === "") return "/";
  // Windows drive root: bare drive letter → re-append the separator so the
  // returned path can be passed straight back to file-browser (a bare "C:"
  // would be resolved as the drive's current dir, not the root).
  if (/^[A-Za-z]:$/.test(parent)) {
    return p.includes("\\") ? `${parent}\\` : `${parent}/`;
  }
  return parent;
}

/** True when `p` is a filesystem root (POSIX "/" or a Windows drive root). */
function isRootPath(p: string): boolean {
  const trimmed = p.trim();
  if (trimmed === "/") return true;
  return /^[A-Za-z]:[\\/]?$/.test(trimmed);
}

/** True when `p` is a Windows drive root (e.g. "C:\" / "C:/"). */
function isWindowsDriveRoot(p: string): boolean {
  return /^[A-Za-z]:[\\/]?$/.test(p.trim());
}

/**
 * Build breadcrumb segments for an absolute path, root first. Preserves the
 * input's OS separator for reconstructed segment paths.
 */
export function buildBreadcrumbs(p: string): DirectorySegment[] {
  const norm = p.replace(/\\/g, "/");
  const isWin = /^[A-Za-z]:/.test(norm);
  const segs = norm.split("/").filter(Boolean);
  if (!segs.length) return norm === "/" ? [{ label: "/", path: "/" }] : [];
  const sep = p.includes("\\") ? "\\" : "/";
  if (isWin) {
    const drive = segs[0];
    const root = `${drive}${sep}`;
    const result: DirectorySegment[] = [{ label: root, path: root }];
    let acc = root;
    for (const s of segs.slice(1)) {
      acc = `${acc}${s}${sep}`;
      result.push({ label: s, path: acc });
    }
    return result;
  }
  const result: DirectorySegment[] = [{ label: "/", path: "/" }];
  let acc = "";
  for (const s of segs) {
    acc = `${acc}/${s}`;
    result.push({ label: s, path: acc });
  }
  return result;
}

/**
 * Reactive directory-browser controller. Instantiate per browser dialog;
 * each instance owns its own navigation state.
 */
export function useSpcodeDirectoryBrowser(hostRead: MaybeRef<boolean> = false) {
  const currentPath = ref("");
  /** Only `directory` entries are kept (files/symlinks are not selectable). */
  const entries = ref<SpcodeFileBrowserEntry[]>([]);
  /** Available drive roots (Windows) / root dirs (POSIX) for the "This PC" view. */
  const drives = ref<string[]>([]);
  const loading = ref(false);
  /** Backend reason string (path_not_found / permission_denied / unknown). */
  const error = ref<string | null>(null);
  const truncated = ref(false);
  let generation = 0;

  const breadcrumbs = computed<DirectorySegment[]>(() =>
    currentPath.value ? buildBreadcrumbs(currentPath.value) : [],
  );

  const isAtRoot = computed(() => isRootPath(currentPath.value));

  /** True in the "This PC" view: no folder is being browsed, drives are shown. */
  const computerMode = computed(() => currentPath.value === "");

  /**
   * Whether the "up" action is available: never in This-PC mode; at a
   * Windows drive root it moves up to the drive list; at POSIX "/" there
   * is no parent so it stays disabled.
   */
  const canGoUp = computed(() => {
    if (computerMode.value) return false;
    if (isAtRoot.value) return isWindowsDriveRoot(currentPath.value);
    return true;
  });

  /** List the single-level directory entries of `path` (dirs only). */
  async function list(path: string): Promise<void> {
    const request = ++generation;
    loading.value = true;
    error.value = null;
    try {
      const resp = await pluginExtensionApi.get<unknown>(
        "spcode/file-browser",
        { params: { path, ...(toValue(hostRead) ? { scope: "host" } : {}) } },
      );
      if (request !== generation) return;
      const envelope = resp.data as
        | {
            status?: string;
            data?: SpcodeFileBrowserRawResponse;
          }
        | undefined;
      const data = envelope?.data;
      if (!data || (toValue(hostRead) && envelope?.status === "error")) {
        error.value = toValue(hostRead)
          ? "spcode_unavailable"
          : "path_not_found";
        return;
      }
      const snap = parseSpcodeFileBrowser(data);
      if (snap.kind !== "directory") {
        // Navigating to a non-directory (should not happen in a dir picker);
        // fall back to the parent of the requested path so the dialog stays
        // usable instead of showing a broken list.
        const parent = parentOf(path);
        if (parent && parent !== path) return list(parent);
        error.value = "path_not_found";
        return;
      }
      currentPath.value = snap.snapshot.meta.path;
      entries.value = snap.snapshot.entries.filter(
        (e) => e.type === "directory",
      );
      truncated.value = snap.snapshot.meta.truncated;
    } catch (err) {
      if (request !== generation) return;
      error.value =
        err instanceof FileBrowserParseError
          ? err.reason
          : toValue(hostRead)
          ? "spcode_unavailable"
          : "unknown";
    } finally {
      if (request === generation) loading.value = false;
    }
  }

  /** Fetch the available drives/roots for the "This PC" view. */
  async function loadDrives(): Promise<void> {
    const request = ++generation;
    loading.value = true;
    error.value = null;
    try {
      const resp = await pluginExtensionApi.get<unknown>("spcode/drives");
      if (request !== generation) return;
      const list = (resp.data as { data?: { drives?: string[] } } | undefined)
        ?.data?.drives;
      // 响应里没有 drives 数组(如插件未重载、路由未注册)一律视为错误,
      // 让错误条暴露出来,而不是静默显示空目录。
      if (!Array.isArray(list)) {
        error.value = toValue(hostRead) ? "spcode_unavailable" : "unknown";
        drives.value = [];
        return;
      }
      drives.value = list;
      currentPath.value = "";
      entries.value = [];
      truncated.value = false;
    } catch {
      if (request !== generation) return;
      error.value = toValue(hostRead) ? "spcode_unavailable" : "unknown";
      drives.value = [];
    } finally {
      if (request === generation) loading.value = false;
    }
  }

  /** Open the "This PC" (drive list) view. */
  async function openComputer(): Promise<void> {
    await loadDrives();
  }

  /** Jump to the host account home directory (start position). */
  async function goHome(): Promise<void> {
    const request = ++generation;
    loading.value = true;
    error.value = null;
    try {
      const resp = await pluginExtensionApi.get<unknown>(
        "spcode/home-directory",
      );
      if (request !== generation) return;
      const home = (resp.data as { data?: { home?: string } } | undefined)?.data
        ?.home;
      if (!home) {
        error.value = toValue(hostRead)
          ? "spcode_unavailable"
          : "path_not_found";
        loading.value = false;
        return;
      }
      await list(home);
    } catch {
      if (request !== generation) return;
      error.value = toValue(hostRead) ? "spcode_unavailable" : "unknown";
      loading.value = false;
    }
  }

  /** Navigate to the parent directory; at a Windows drive root, to the
   *  "This PC" drive list. No-op in This-PC mode / at POSIX "/". */
  async function goUp(): Promise<void> {
    if (computerMode.value) return;
    if (isAtRoot.value) {
      if (isWindowsDriveRoot(currentPath.value)) await openComputer();
      return;
    }
    const parent = parentOf(currentPath.value);
    if (parent) await list(parent);
  }

  /** Navigate into a listed directory entry. */
  async function openEntry(entry: SpcodeFileBrowserEntry): Promise<void> {
    if (entry.type === "directory") await list(entry.path);
  }

  /** Navigate to a breadcrumb segment. */
  async function openSegment(segment: DirectorySegment): Promise<void> {
    await list(segment.path);
  }

  /** Invalidate picker requests when the dialog closes or unmounts. */
  function cancel(): void {
    generation++;
    loading.value = false;
  }

  return {
    cancel,
    currentPath,
    entries,
    drives,
    loading,
    error,
    truncated,
    breadcrumbs,
    isAtRoot,
    computerMode,
    canGoUp,
    list,
    goHome,
    goUp,
    loadDrives,
    openComputer,
    openEntry,
    openSegment,
  };
}
