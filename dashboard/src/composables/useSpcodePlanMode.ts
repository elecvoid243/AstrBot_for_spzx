// src/composables/useSpcodePlanMode.ts
//
// Singleton state for the spcode plan/build mode chip. Mirrors the
// pattern of `useSpcodeProjectStatus.ts`:
//
//   - one module-level ref so every consumer (the chip, the
//     +menu entry, the chat-stream parser) reads the same value
//     without coordinating fetches;
//   - explicit mutation methods (`refresh` / `setActive` / `reset`)
//     so the only way to write the ref is through a documented API;
//   - a soft-fail `refresh` that never throws to callers.
//
// The chip itself is mounted in `ChatInput.vue` next to
// `SpcodeProjectIndicator` and `GitDiffChip`; this composable feeds
// it the current per-umo state.
//
// Author: elecvoid243
// Last-Modified: 2026-06-19

import { ref } from 'vue'
import { pluginExtensionApi } from '@/api/v1'
import {
  EMPTY_PLAN_MODE_STATUS,
  type SpcodePlanModeStatus,
} from './parseSpcodePlanMode'

// Re-export the type so existing consumers of this module keep
// importing it from a single place.
export type { SpcodePlanModeStatus } from './parseSpcodePlanMode'

const status = ref<SpcodePlanModeStatus>({ ...EMPTY_PLAN_MODE_STATUS })

/**
 * Composable returning the spcode plan/build mode singleton.
 *
 * The dashboard only ever writes to the ref through one of three
 * explicit methods:
 *
 *   - `refresh(umo?)` — pull the authoritative state from the
 *     plugin's HTTP endpoint. Pass `umo` to query a specific session;
 *     omit it to receive the default build state for the whole
 *     plugin (we intentionally do NOT fall back to "most recent
 *     plan-mode session" because plan/build is strictly per-session
 *     and silently inheriting another session's mode would be
 *     confusing).
 *   - `setActive(value)` — optimistic local flip, used right after
 *     a `/plan` or `/build` command is dispatched so the chip
 *     updates before the round-trip completes.
 *   - `reset()` — wipe the ref to the empty state (e.g. on logout
 *     or when the active session becomes null).
 */
export function useSpcodePlanMode() {
  /**
   * Query the spcode plugin via its registered web API and update
   * the shared status ref.
   *
   * The endpoint accepts an optional `umo` query param. When the
   * dashboard does not know its umo, it can omit it and receive
   * the default build state — the chip should treat that as
   * "unknown" rather than "build", which is what the
   * `SpcodePlanModeChip`'s v-if gate is for.
   */
  async function refresh(umo?: string | null): Promise<void> {
    try {
      const res = await pluginExtensionApi.get<{
        active: boolean
        umo: string | null
        all_active_count: number
      }>('spcode/plan-mode', {
        params: umo ? { umo } : {},
      })
      const data = res.data?.data
      if (!data) {
        // Soft-fail: keep the last known state.
        return
      }
      status.value = {
        active: Boolean(data.active),
        umo: data.umo ?? null,
        allActiveCount:
          typeof data.all_active_count === 'number'
            ? data.all_active_count
            : 0,
        fetchedAt: Date.now(),
      }
    } catch (err) {
      // Network or auth error: keep previous state, do not throw.
      console.warn('[useSpcodePlanMode] refresh failed:', err)
    }
  }

  /**
   * Optimistically set the current session's plan mode flag.
   *
   * Used right after the dashboard dispatches a `/plan` or `/build`
   * command so the chip flips immediately rather than waiting for
   * the bot to respond and the next refresh tick to fire.
   *
   * The all-active count is bumped/decremented in lockstep so the
   * tooltip ("N sessions in plan mode") stays consistent during the
   * optimistic window. The authoritative refresh fires from
   * `Chat.vue`'s `currSessionId` watcher once the response arrives,
   * which corrects any drift.
   */
  function setActive(active: boolean): void {
    const wasActive = status.value.active
    if (wasActive === active) {
      // No-op: avoid mutating allActiveCount on a redundant call.
      return
    }
    const delta = active ? 1 : -1
    status.value = {
      ...status.value,
      active,
      allActiveCount: Math.max(0, status.value.allActiveCount + delta),
      fetchedAt: Date.now(),
    }
  }

  /**
   * Authoritatively set the plan mode for a session via the plugin's
   * ``POST /spcode/plan-mode`` endpoint (v2.22.0+).
   *
   * This is the chip's primary toggle path: unlike dispatching a
   * ``/plan`` / ``/build`` chat command it leaves no message in the
   * conversation history. On success the shared status ref adopts the
   * state from the response envelope (the backend is the source of
   * truth). On any failure — network error, or an older plugin that
   * does not register the POST route — the prior state is kept and
   * ``false`` is returned so the caller can fall back to the chat
   * command path.
   *
   * Args:
   *   umo: The full unified_msg_origin of the session to switch.
   *   active: ``true`` → plan mode, ``false`` → build mode.
   *
   * Returns:
   *   ``true`` when the backend confirmed the new state.
   */
  async function setPlanMode(umo: string, active: boolean): Promise<boolean> {
    try {
      const res = await pluginExtensionApi.post<{
        active: boolean
        umo: string | null
        all_active_count: number
        changed: boolean
      }>('spcode/plan-mode', { umo, active })
      const data = res.data?.data
      if (!data) {
        return false
      }
      status.value = {
        active: Boolean(data.active),
        umo: data.umo ?? umo,
        allActiveCount:
          typeof data.all_active_count === 'number'
            ? data.all_active_count
            : 0,
        fetchedAt: Date.now(),
      }
      return true
    } catch (err) {
      // Soft-fail: keep previous state, let the caller fall back to
      // the /plan or /build chat command.
      console.warn('[useSpcodePlanMode] setPlanMode failed:', err)
      return false
    }
  }

  /**
   * Reset the status to the empty state (e.g. on logout or session
   * switch to a session that has never been seen).
   */
  function reset(): void {
    status.value = { ...EMPTY_PLAN_MODE_STATUS }
  }

  return {
    status,
    refresh,
    setActive,
    setPlanMode,
    reset,
  }
}

window.addEventListener?.("astrbot:account-changed", () => { useSpcodePlanMode().reset(); });
