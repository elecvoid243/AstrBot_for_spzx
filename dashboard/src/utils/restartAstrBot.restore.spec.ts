import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { restartAstrBot } from "./restartAstrBot";
import { authApi, statsApi } from "@/api/v1";
import { getDesktopRuntimeInfo } from "./desktopRuntime";

vi.mock("@/api/v1", () => ({ authApi: { setupStatus: vi.fn() }, statsApi: { restart: vi.fn(), startTime: vi.fn() } }));
vi.mock("./desktopRuntime", () => ({ getDesktopRuntimeInfo: vi.fn() }));
beforeEach(() => {
  vi.useFakeTimers();
  localStorage.setItem("token", "fresh-recovery-login");
  sessionStorage.setItem("astrbot-full-restore", "restore-job");
  vi.spyOn(window.location, "reload").mockImplementation(() => undefined);
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear(); });

describe("full restore restart", () => {
  it.each([false, true])("uses the existing restart path, then public readiness and fresh login (desktop=%s)", async (desktop) => {
    const bridge = { restartBackend: vi.fn().mockResolvedValue({ ok: true }) };
    vi.mocked(getDesktopRuntimeInfo).mockResolvedValue({ bridge: bridge as any, hasDesktopRestartCapability: desktop, isDesktopRuntime: desktop, hasDesktopRuntimeProbe: desktop });
    vi.mocked(statsApi.restart).mockImplementation(async () => {
      expect(localStorage.getItem("token")).toBe("fresh-recovery-login");
      return {} as any;
    });
    vi.mocked(authApi.setupStatus).mockRejectedValueOnce(new Error("restarting")).mockResolvedValue({ status: 200 } as any);
    const pending = restartAstrBot(null, true);
    await vi.runAllTimersAsync();
    await pending;
    expect(localStorage.getItem("token")).toBeNull();
    expect(sessionStorage.getItem("astrbot-full-restore")).toBeNull();
    expect(statsApi.startTime).not.toHaveBeenCalled();
    expect(authApi.setupStatus).toHaveBeenCalledTimes(2);
    expect(window.location.hash).toBe("#/auth/login");
    if (desktop) expect(bridge.restartBackend).toHaveBeenCalledWith("fresh-recovery-login");
  });
  it("keeps the administrator authenticated when a restart fails so they can retry", async () => {
    vi.mocked(getDesktopRuntimeInfo).mockResolvedValue({ bridge: undefined, hasDesktopRestartCapability: false, isDesktopRuntime: false, hasDesktopRuntimeProbe: false });
    vi.mocked(statsApi.restart).mockRejectedValueOnce(new Error("restart failed"));
    await expect(restartAstrBot(null, true)).rejects.toThrow("restart failed");
    expect(localStorage.getItem("token")).toBe("fresh-recovery-login");
    expect(sessionStorage.getItem("astrbot-full-restore")).toBe("restore-job");
  });
});
