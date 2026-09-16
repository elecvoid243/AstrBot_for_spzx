import {
  computed,
  reactive,
  ref,
  toValue,
  type ComputedRef,
  type MaybeRefOrGetter,
} from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { EMPTY_STATUS, type SpcodeProjectStatus } from "./parseSpcodeStatus";

// Re-export the type so existing consumers of this module keep working.
export type { SpcodeProjectStatus } from "./parseSpcodeStatus";

const status = ref<SpcodeProjectStatus>({ ...EMPTY_STATUS });

// 2026-09-16 (elecvoid243): per-umo entries. The shared `status` ref is a
// mirror of the ACTIVE session's entry, so a refresh for another session
// can no longer overwrite what the sidebar/chip display. Kept unpinned by
// default (= legacy "last writer wins") so standalone callers and specs
// that never call setActiveUmo() behave exactly as before.
const entries = reactive(new Map<string, SpcodeProjectStatus>());
let activeUmo: string | null = null;
let pinned = false;

/** Whether a write for `umo` may update the shared ref. */
function shouldMirror(umo: string): boolean {
  return !pinned || umo === activeUmo;
}

// 2026-09-01 (elecvoid243): in-flight refresh dedup, keyed by umo.
// The session-switch watcher and the spcode auto-load fast path may
// both call refresh(umo) for the same session at the same tick; sharing
// the promise avoids a duplicate GET and keeps the boot id coherent.
const inflightRefresh = new Map<string, Promise<SpcodeProjectStatus>>();

/**
 * Shared state holder for the spcode "currently loaded project" chip.
 *
 * The composable wraps the plugin's HTTP API and exposes a single
 * module-level `status` ref so every consumer (indicator chip, dialog
 * refresh, chat-stream parser) sees the same value.
 *
 * The dashboard only ever writes to the ref through one of three
 * explicit methods:
 *   - `refresh(umo?)` — pull the authoritative state from the spcode
 *     plugin's HTTP endpoint.
 *   - `setLoaded` / `setUnloaded` — optimistic local flips, used right
 *     after a `/project load` or `/project unload` command is
 *     dispatched so the chip updates before the round-trip completes.
 *   - `reset()` — wipe the ref to the empty state (e.g. on logout or
 *     when the active session becomes null).
 *
 * There is intentionally no chat-stream parser here: the dashboard
 * never inspects bot message text for status signals. All chat-driven
 * updates arrive via the `refresh()` call in `Chat.vue`'s
 * `currSessionId` watcher (and the `showSpcodeIndicator` watcher in
 * `ChatInput.vue`), keeping a single source of truth on the backend.
 */
export function useSpcodeProjectStatus() {
  /**
   * Query the spcode plugin via its registered web API and update the
   * shared status ref. Pass `umo` to look up a specific session.
   *
   * Bug fix (2026-08-15, elecvoid243): a missing/empty `umo` now resets
   * the chip to the empty state instead of hitting the backend's
   * "most-recently-loaded project across ALL umos" fallback. That
   * fallback returns a value for whatever session happened to load
   * last — a fixed, unrelated directory the chip would display as the
   * CURRENT session's project. This is exactly the window right after
   * "new chat" / a project title is clicked (no session exists yet, so
   * there is no umo to address the request at). Every caller passes
   * the resolved umo of the active session; when there is none the
   * correct display is the empty state.
   */
  async function refresh(umo?: string | null): Promise<SpcodeProjectStatus> {
    if (!umo) {
      status.value = { ...EMPTY_STATUS };
      return status.value;
    }
    // dedup: multiple callers in the same tick share one network request
    const existing = inflightRefresh.get(umo);
    if (existing) return existing;
    const promise = (async (): Promise<SpcodeProjectStatus> => {
      try {
        const res = await pluginExtensionApi.get<{
          loaded: boolean;
          directory: string | null;
          loaded_at: number | null;
          umo: string | null;
          all_loaded_count: number;
          boot_id?: string | null;
        }>("spcode/project-status", {
          params: { umo },
        });
        const data = res.data?.data;
        if (!data) {
          // Soft-fail: keep the last known state for this umo.
          return entries.get(umo) ?? { ...EMPTY_STATUS };
        }
        const next: SpcodeProjectStatus = {
          loaded: Boolean(data.loaded),
          directory: data.directory ?? null,
          loadedAt: typeof data.loaded_at === "number" ? data.loaded_at : null,
          umo: data.umo ?? null,
          allLoadedCount:
            typeof data.all_loaded_count === "number"
              ? data.all_loaded_count
              : 0,
          fetchedAt: Date.now(),
          // 2026-09-01: backend boot id, drives dirty-tag invalidation.
          bootId: data.boot_id ?? null,
        };
        entries.set(umo, next);
        // Only the ACTIVE session may drive the shared ref; a response that
        // arrives after a session switch is cached (for that session's own
        // consumers) but must not overwrite the chip/sidebar display.
        if (shouldMirror(umo)) status.value = { ...next };
        return next;
      } catch (err) {
        // Network or auth error: keep previous state, do not throw to callers.
        console.warn("[useSpcodeProjectStatus] refresh failed:", err);
        return entries.get(umo) ?? { ...EMPTY_STATUS };
      }
    })();
    inflightRefresh.set(umo, promise);
    try {
      return await promise;
    } finally {
      inflightRefresh.delete(umo);
    }
  }

  /**
   * Optimistically mark `umo` as having a project loaded. Writes the umo
   * and the directory together so the pair can never mix sessions
   * (2026-09-16, elecvoid243).
   */
  function setLoaded(
    umo: string,
    directory: string,
    loadedAt: number = Date.now() / 1000,
  ) {
    const base = entries.get(umo) ?? status.value;
    const next: SpcodeProjectStatus = {
      ...base,
      loaded: true,
      directory,
      loadedAt,
      umo,
      fetchedAt: Date.now(),
    };
    entries.set(umo, next);
    if (shouldMirror(umo)) status.value = next;
  }

  /** Optimistically mark `umo` (default: the displayed session) as unloaded. */
  function setUnloaded(umo?: string | null) {
    const target = umo ?? (pinned ? activeUmo : status.value.umo);
    const base = target ? (entries.get(target) ?? status.value) : status.value;
    const next: SpcodeProjectStatus = {
      ...EMPTY_STATUS,
      umo: target,
      allLoadedCount: Math.max(0, base.allLoadedCount - 1),
      fetchedAt: Date.now(),
      // backend identity is session-independent: keep it (only refresh updates)
      bootId: base.bootId,
    };
    if (target) entries.set(target, next);
    if (!target || shouldMirror(target)) status.value = next;
  }

  /**
   * Pin the shared ref to `umo`'s entry. Called by Chat.vue on every
   * session switch — after this, only that session's writes may change
   * the shared ref.
   */
  function setActiveUmo(umo: string | null): void {
    pinned = true;
    activeUmo = umo;
    status.value = umo
      ? { ...(entries.get(umo) ?? EMPTY_STATUS) }
      : { ...EMPTY_STATUS };
  }

  /** Reactive read-only accessor for a specific session's status. */
  function statusFor(
    umo: MaybeRefOrGetter<string | null>,
  ): ComputedRef<SpcodeProjectStatus> {
    return computed(
      () =>
        entries.get(toValue(umo) ?? "") ??
        (EMPTY_STATUS as SpcodeProjectStatus),
    );
  }

  /**
   * Reset the status to the empty state (e.g. on logout or session switch).
   * Unpins as well, so callers that never re-pin fall back to the legacy
   * "last writer wins" mirroring.
   */
  function reset() {
    pinned = false;
    activeUmo = null;
    status.value = {
      ...EMPTY_STATUS,
      // keep bootId so the dirty tag stays valid across session switches;
      // the backend identity only changes when refresh() observes a new one.
      bootId: status.value.bootId,
    };
  }

  return {
    status,
    refresh,
    setLoaded,
    setUnloaded,
    reset,
    setActiveUmo,
    statusFor,
  };
}
