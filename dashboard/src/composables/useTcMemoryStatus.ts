/**
 * useTcMemoryStatus.ts
 *
 * Singleton state for the astrbot_plugin_tc_memory service status,
 * fetched from the plugin's extension endpoint
 * GET /api/v1/plugins/extensions/astrbot_plugin_tc_memory/status.
 *
 * Mirrors useSpcodeCodegraphStatus.ts — a module-level ref shared by all
 * consumers (the services popover in SpcodeProjectIndicator) so they
 * observe the same data without redundant fetches.
 *
 * Author: elecvoid243
 * Created: 2026-10-02
 */

import { ref } from 'vue'
import { pluginExtensionApi } from '@/api/v1'

export interface TcMemoryStatus {
  /** Whether the plugin's status endpoint answered (plugin installed & loaded). */
  reachable: boolean
  /** "local" (embedded standalone) or "server" (remote). */
  mode: string
  /** Plugin runtime enabled (health probe passed at load). */
  enabled: boolean
  /** Gateway /health answering right now. */
  running: boolean
  endpoint: string
  version: string | null
  /** PID of the gateway child process (local mode, self-spawned only). */
  pid: number | null
  fetchedAt: number | null
}

export const EMPTY_TC_MEMORY_STATUS: TcMemoryStatus = {
  reachable: false,
  mode: 'local',
  enabled: false,
  running: false,
  endpoint: '',
  version: null,
  pid: null,
  fetchedAt: null,
}

const status = ref<TcMemoryStatus>({ ...EMPTY_TC_MEMORY_STATUS })

/**
 * Fetch the tc_memory status from the plugin endpoint and update the
 * shared singleton ref. On failure the previous state is preserved
 * except `reachable` flipping to false (soft-fail).
 */
async function fetchTcMemoryStatus(): Promise<void> {
  try {
    const res = await pluginExtensionApi.get<{
      mode: string
      enabled: boolean
      running: boolean
      endpoint: string
      version: string | null
      pid: number | null
    }>('astrbot_plugin_tc_memory/status')
    const data = res.data?.data
    if (!data) {
      return
    }
    status.value = {
      reachable: true,
      mode: data.mode ?? 'local',
      enabled: Boolean(data.enabled),
      running: Boolean(data.running),
      endpoint: data.endpoint ?? '',
      version: data.version ?? null,
      pid: data.pid ?? null,
      fetchedAt: Date.now(),
    }
  } catch (err) {
    console.warn('[useTcMemoryStatus] refresh failed:', err)
    status.value = { ...status.value, reachable: false }
  }
}

function resetTcMemoryStatus(): void {
  status.value = { ...EMPTY_TC_MEMORY_STATUS }
}

export function useTcMemoryStatus() {
  return {
    status,
    refresh: fetchTcMemoryStatus,
    reset: resetTcMemoryStatus,
  }
}
