// Shared recent-project-path history for the two project path entry
// points: ProjectLoadDialog (spcode `/project load`) and ProjectDialog
// (sidebar project create/edit). Both persist under the same
// localStorage key so a path picked in either dialog shows up as
// recent in the other.

import { computed, ref } from "vue";
import {
  accountSessionRevision,
  assertCurrentAccount,
} from "@/utils/accountSession";

const HISTORY_KEY = "chatui.spcode.projectPathHistory";
const HISTORY_CAP = 10;
// The load dialog shows the full stored history (2026-11-07: 5 → 10).
const RECENT_DROPDOWN_COUNT = 10;

function historyKey(): string {
  const owner =
    localStorage.getItem("account_id") ||
    localStorage.getItem("resource_owner");
  return owner ? `${HISTORY_KEY}:${owner}` : HISTORY_KEY;
}

/**
 * Read the recent-project-path history from `localStorage`.
 *
 * Defensive against malformed entries (non-strings, non-arrays, JSON
 * parse errors) — returns an empty list on any failure rather than
 * throwing into a dialog render path.
 */
function getPathHistory(): string[] {
  try {
    const raw =
      localStorage.getItem(historyKey()) ||
      (localStorage.getItem("role") === "user"
        ? null
        : localStorage.getItem(HISTORY_KEY));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((p): p is string => typeof p === "string");
  } catch {
    return [];
  }
}

// Module-level singleton: every dialog instance shares one in-memory
// mirror, matching the shared localStorage key on the backend of it.
const pathHistory = ref<string[]>(getPathHistory());
window.addEventListener?.("astrbot:account-changed", () => {
  pathHistory.value = [];
});

/**
 * Shared recent-path state for project path pickers.
 *
 * Returns:
 *   recentPaths: Up to `RECENT_DROPDOWN_COUNT` most recent paths
 *     (newest first), for dropdown rendering.
 *   addToPathHistory: Push a path to the front, dedup by exact match,
 *     cap at HISTORY_CAP; mirrors to localStorage.
 *   removeFromPathHistory: Drop one path; no-op if absent; mirrors to
 *     localStorage.
 */
export function useProjectPathHistory() {
  const revision = accountSessionRevision.value;
  pathHistory.value = getPathHistory();
  const recentPaths = computed<string[]>(() =>
    pathHistory.value.slice(0, RECENT_DROPDOWN_COUNT),
  );

  /**
   * Push `path` to the front of the history, deduping by exact match
   * and capping total length. Updates both the reactive ref and the
   * localStorage mirror. Empty/whitespace-only paths are ignored.
   *
   * Args:
   *   path: Absolute project path to record.
   */
  function addToPathHistory(path: string): void {
    assertCurrentAccount(revision);
    const trimmed = path.trim();
    if (!trimmed) return;
    const deduped = [
      trimmed,
      ...pathHistory.value.filter((p) => p !== trimmed),
    ].slice(0, HISTORY_CAP);
    pathHistory.value = deduped;
    try {
      localStorage.setItem(historyKey(), JSON.stringify(deduped));
    } catch {
      // localStorage write failure (quota, private mode, etc.); silent.
    }
  }

  /**
   * Remove `path` from the history. Updates both the reactive ref and
   * the localStorage mirror. No-op if `path` is not currently in
   * history.
   *
   * Args:
   *   path: Exact path entry to remove.
   */
  function removeFromPathHistory(path: string): void {
    assertCurrentAccount(revision);
    const filtered = pathHistory.value.filter((p) => p !== path);
    if (filtered.length === pathHistory.value.length) return; // no-op
    pathHistory.value = filtered;
    try {
      localStorage.setItem(historyKey(), JSON.stringify(filtered));
    } catch {
      // localStorage write failure; silent.
    }
  }

  return { recentPaths, addToPathHistory, removeFromPathHistory };
}
