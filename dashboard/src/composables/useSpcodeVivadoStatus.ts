/**
 * useSpcodeVivadoStatus.ts
 *
 * Singleton state for the spcode vivado MCP server status, fetched
 * from the backend's GET /spcode/vivado-status endpoint.
 *
 * Mirrors the pattern used by useSpcodeCodegraphStatus.ts — a module-level
 * ref shared across consumers (the project indicator's services popover,
 * refresh watcher, etc.) so they all read the same value.
 *
 * Author: elecvoid243
 * Last-Modified: 2026-07-23
 */

import { ref } from 'vue'
import { pluginExtensionApi } from '@/api/v1'

// ── Types ──────────────────────────────────────────────────────────────

export interface VivadoSession {
  id: string
  state: 'idle' | 'busy' | 'error'
}

export type VivadoOverallStatus =
  | 'ok'
  | 'degraded'
  | 'disabled'
  | 'not_installed'
  | 'not_running'
  | 'toolchain_missing'

export interface VivadoStatus {
  /** Derived overall status. */
  overall: VivadoOverallStatus
  /** Whether vivado-mcp is enabled in plugin config. */
  enabled: boolean
  /** Whether the vivado MCP server is currently running. */
  mcpRunning: boolean
  /** Detected Vivado executable path (empty = not found). */
  vivadoPath: string
  /** Whether vivado-mcp Python package is missing. */
  installMissing: boolean
  /** Whether sessions data read failed. */
  degraded: boolean
  /** Active session list. */
  sessions: VivadoSession[]
  /** Local timestamp in ms of the last successful refresh. */
  fetchedAt: number | null
  /** Human-readable status message (for tooltip/label). */
  message: string
}

export const EMPTY_VIVADO_STATUS: VivadoStatus = {
  overall: 'disabled',
  enabled: false,
  mcpRunning: false,
  vivadoPath: '',
  installMissing: false,
  degraded: false,
  sessions: [],
  fetchedAt: null,
  message: 'vivado-mcp 未启用',
}

// ── Inference ──────────────────────────────────────────────────────────

/**
 * Derive the frontend status from the raw API response.
 * Mirrors the `inferStatus` function from the vivado-status API spec.
 */
function inferVivadoStatus(data: {
  enabled: boolean
  mcp_running: boolean
  vivado_path: string
  install_missing: boolean
  degraded: boolean
  sessions: { id: string; state: string }[]
}): Pick<VivadoStatus, 'overall' | 'message'> {
  if (!data.enabled) {
    return { overall: 'disabled', message: 'vivado-mcp 集成未启用' }
  }
  if (data.install_missing) {
    return {
      overall: 'not_installed',
      message: 'vivado-mcp 包未安装，请执行 pip install vivado-mcp',
    }
  }
  if (!data.mcp_running) {
    return {
      overall: 'not_running',
      message: 'vivado MCP 服务未运行（启动中或启动失败）',
    }
  }
  if (!data.vivado_path) {
    // MCP 服务能跑起来说明包已装 (install_missing 已先短路),
    // 但 vivado_path 还是空 → find_vivado_executable 三层 fallback
    // (配置 / VIVADO_PATH env / vivado_mcp 默认路径) 全失败
    // → Vivado 工具链本身未在用户机器上。提示要装 Vivado IDE。
    return {
      overall: 'toolchain_missing',
      message:
        '未找到 Vivado 工具链。请安装 Vivado IDE 并配置 VIVADO_PATH 环境变量（或在 spcode 插件配置中指定 vivado.executable）',
    }
  }
  if (data.degraded) {
    return {
      overall: 'degraded',
      message: 'vivado 会话数据暂时不可用',
    }
  }
  const count = data.sessions.length
  const active = data.sessions.filter((s) => s.state === 'busy').length
  return {
    overall: 'ok',
    message:
      active > 0
        ? `Vivado 运行中 · ${count} 会话 (${active} 活跃)`
        : `Vivado 运行中 · ${count} 会话`,
  }
}

// ── Module-level shared state ────────────────────────────────────────────

const status = ref<VivadoStatus>({ ...EMPTY_VIVADO_STATUS })

/**
 * Fetch the vivado MCP status from the spcode plugin's HTTP endpoint
 * and update the shared singleton ref. On failure the previous state is
 * preserved (soft-fail).
 */
async function fetchVivadoStatus(): Promise<void> {
  try {
    const res = await pluginExtensionApi.get<{
      enabled: boolean
      mcp_running: boolean
      vivado_path: string
      install_missing: boolean
      degraded: boolean
      sessions: { id: string; state: string }[]
    }>('spcode/vivado-status')
    const data = res.data?.data
    if (!data) {
      return
    }
    const derived = inferVivadoStatus(data)
    status.value = {
      enabled: Boolean(data.enabled),
      mcpRunning: Boolean(data.mcp_running),
      vivadoPath: data.vivado_path ?? '',
      installMissing: Boolean(data.install_missing),
      degraded: Boolean(data.degraded),
      sessions: (data.sessions ?? []).map((s) => ({
        id: s.id,
        state: s.state as VivadoSession['state'],
      })),
      fetchedAt: Date.now(),
      ...derived,
    }
  } catch (err) {
    // Soft-fail: keep the last known state. A transient network blip
    // should not clear the chip's current display.
    console.warn('[useSpcodeVivadoStatus] refresh failed:', err)
  }
}

/**
 * Reset the singleton status to the empty state.
 */
function resetVivadoStatus(): void {
  status.value = { ...EMPTY_VIVADO_STATUS }
}

/**
 * Singleton composable for the vivado MCP server status.
 *
 * Returns:
 *   - status:  the shared ref (reactive VivadoStatus)
 *   - refresh: trigger an explicit re-fetch from the backend
 *   - reset:   wipe to the empty state (e.g. on logout)
 */
export function useSpcodeVivadoStatus() {
  return {
    status,
    refresh: fetchVivadoStatus,
    reset: resetVivadoStatus,
  }
}

window.addEventListener?.("astrbot:account-changed", () => { resetVivadoStatus(); });
