// src/composables/useFileAccessMode.ts
//
// Singleton state for the per-session file access mode chip
// (full / readonly / workspace): module-level ref + explicit mutation
// methods; soft-fail refresh.
//
// Besides the mode itself the state carries the workspace-mode
// whitelist (fixed implicit roots + per-session custom roots) that the
// chip's whitelist dialog edits.

import { ref } from 'vue'
import {
  fileAccessApi,
  type FileAccessFixedRoot,
  type FileAccessModeValue,
} from '@/api/v1'

export type FileAccessMode = FileAccessModeValue

export interface FileAccessState {
  mode: FileAccessMode | null
  defaultMode: FileAccessMode | null
  fixedRoots: FileAccessFixedRoot[]
  customRoots: string[]
}

const state = ref<FileAccessState>({
  mode: null,
  defaultMode: null,
  fixedRoots: [],
  customRoots: [],
})

export function useFileAccessMode() {
  /**
   * Pull the authoritative mode + whitelist for a session from the core
   * endpoint. Soft-fails: keeps the last known state on errors.
   */
  async function refresh(umo: string): Promise<void> {
    try {
      const res = await fileAccessApi.get(umo)
      const data = res.data?.data
      if (!data) return
      state.value = {
        mode: data.mode,
        defaultMode: data.default_mode,
        fixedRoots: data.fixed_roots ?? [],
        customRoots: data.custom_roots ?? [],
      }
    } catch (err) {
      console.warn('[useFileAccessMode] refresh failed:', err)
    }
  }

  /**
   * Authoritatively set the mode via POST. Returns false on failure so
   * the caller can resync or fall back.
   */
  async function setMode(umo: string, mode: FileAccessMode): Promise<boolean> {
    try {
      const res = await fileAccessApi.set(umo, mode)
      const data = res.data?.data
      if (!data) return false
      state.value = {
        ...state.value,
        mode: data.mode,
        defaultMode: data.default_mode,
      }
      return true
    } catch (err) {
      console.warn('[useFileAccessMode] setMode failed:', err)
      return false
    }
  }

  /**
   * Replace the session's custom whitelist roots via POST. On success
   * the shared state adopts the normalized list from the response.
   */
  async function setRoots(umo: string, roots: string[]): Promise<boolean> {
    try {
      const res = await fileAccessApi.setRoots(umo, roots)
      const data = res.data?.data
      if (!data) return false
      state.value = { ...state.value, customRoots: data.custom_roots ?? [] }
      return true
    } catch (err) {
      console.warn('[useFileAccessMode] setRoots failed:', err)
      return false
    }
  }

  /** Optimistic local flip used right after a chip click. */
  function setOptimistic(mode: FileAccessMode): void {
    state.value = { ...state.value, mode }
  }

  /** Wipe to the unknown state (logout / no active session). */
  function reset(): void {
    state.value = {
      mode: null,
      defaultMode: null,
      fixedRoots: [],
      customRoots: [],
    }
  }

  return { status: state, refresh, setMode, setRoots, setOptimistic, reset }
}
