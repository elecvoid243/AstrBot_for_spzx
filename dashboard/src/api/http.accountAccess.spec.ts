import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => { vi.restoreAllMocks(); vi.resetModules(); localStorage.clear(); });

describe("fetch account invalidation", () => {
  it("clears identity through the same logout path when a protected SSE request returns 401", async () => {
    const nativeFetch = vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    const { fetchWithAuth, setupHttpClient } = await import("./http");
    const { accountSessionRevision } = await import("@/utils/accountSession");
    localStorage.setItem("token", "old-account");
    localStorage.setItem("account_id", "account-a");
    const revision = accountSessionRevision.value;
    setupHttpClient();
    await fetchWithAuth("/api/v1/chat", { method: "POST" });
    expect(nativeFetch).toHaveBeenCalled();
    expect(localStorage.getItem("token")).toBeNull();
    expect(accountSessionRevision.value).toBe(revision + 1);
  });
});
