import { authApi, statsApi } from '@/api/v1'
import { getDesktopRuntimeInfo } from '@/utils/desktopRuntime'
import { resetAccountSession } from '@/utils/accountSession'

type WaitingForRestartRef = {
  check: (initialStartTime?: number | null) => void | Promise<void>
  stop?: () => void
}

async function triggerWaiting(
  waitingRef?: WaitingForRestartRef | null,
  initialStartTime?: number | null
) {
  if (!waitingRef) return
  await waitingRef.check(initialStartTime)
}

async function fetchCurrentStartTime(): Promise<number | null> {
  try {
    const response = await statsApi.startTime()
    const rawStartTime = response?.data?.data?.start_time
    const numericStartTime = Number(rawStartTime)
    return Number.isFinite(numericStartTime) ? numericStartTime : null
  } catch (_error) {
    return null
  }
}

export async function restartAstrBot(
  waitingRef?: WaitingForRestartRef | null,
  afterRestore = false
): Promise<void> {
  const { bridge: desktopBridge, hasDesktopRestartCapability, isDesktopRuntime } =
    await getDesktopRuntimeInfo()

  if (afterRestore) {
    // Send the authenticated restart before clearing all identity and Chat state.
    if (desktopBridge && hasDesktopRestartCapability && isDesktopRuntime) {
      const result = await desktopBridge.restartBackend(localStorage.getItem('token'))
      if (!result.ok) throw new Error(result.reason || 'Failed to restart backend.')
    } else {
      await statsApi.restart()
    }
    resetAccountSession()
    sessionStorage.removeItem('astrbot-full-restore')
    // Readiness is public; a retired JWT must never be a restart polling credential.
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, 1000))
      try {
        const response = await authApi.setupStatus()
        if (response.status === 200) {
          window.location.hash = '/auth/login'
          window.location.reload()
          return
        }
      } catch { /* The backend is stopping or still in restore maintenance. */ }
    }
    window.location.hash = '/auth/login'
    return
  }
  if (desktopBridge && hasDesktopRestartCapability && isDesktopRuntime) {
    const authToken = localStorage.getItem('token')
    const initialStartTime = await fetchCurrentStartTime()
    try {
      const restartPromise = desktopBridge.restartBackend(authToken)
      await triggerWaiting(waitingRef, initialStartTime)
      const result = await restartPromise
      if (!result.ok) {
        waitingRef?.stop?.()
        throw new Error(result.reason || 'Failed to restart backend.')
      }
    } catch (error) {
      waitingRef?.stop?.()
      throw error
    }
    return
  }

  await statsApi.restart()
  await triggerWaiting(waitingRef)
}
