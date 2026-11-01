import { afterEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useCommonStore } from "./common";
import { fetchWithAuth } from "@/api/http";
import { resetAccountSession } from "@/utils/accountSession";

vi.mock("@/api/http", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/api/v1", () => ({ logApi: { liveUrl: () => "/api/v1/logs/live" }, pluginApi: {}, statsApi: {} }));
afterEach(() => { vi.clearAllMocks(); vi.useRealTimers(); localStorage.clear(); });

describe("operational log stream during full restore reauthentication", () => {
  it("retires the stream, clears its cache and never reconnects under another identity", async () => {
    vi.useFakeTimers(); setActivePinia(createPinia());
    localStorage.setItem("token", "old-admin"); localStorage.setItem("role", "admin");
    let fail!: (reason: unknown) => void;
    vi.mocked(fetchWithAuth).mockImplementationOnce(() => new Promise((_, reject) => { fail = reject; }));
    const store = useCommonStore();
    store.log_cache.push({ data: "old operational secret" } as never);
    await store.createEventSource();
    const controller = store.eventSource as unknown as AbortController;
    resetAccountSession();
    localStorage.setItem("token", "ordinary"); localStorage.setItem("role", "user");
    fail(new DOMException("Aborted", "AbortError"));
    await vi.runAllTimersAsync();
    expect(controller.signal.aborted).toBe(true);
    expect(store.log_cache).toEqual([]);
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);
    await store.createEventSource();
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);
  });
});
