import { afterEach, describe, expect, it, vi } from "vitest";
import MockAdapter from "axios-mock-adapter";

afterEach(() => {
  vi.restoreAllMocks();
  vi.resetModules();
  localStorage.clear();
});

describe("fetch account invalidation", () => {
  it("clears revoked identity and pending account state after an Axios 401", async () => {
    const { httpClient, setupHttpClient } = await import("./http");
    const { accountSessionSignal, accountSessionRevision } = await import(
      "@/utils/accountSession"
    );
    const adapter = new MockAdapter(httpClient);
    try {
      localStorage.setItem("token", "revoked-credential");
      localStorage.setItem("account_id", "account-a");
      localStorage.setItem("role", "user");
      sessionStorage.setItem("chat-draft", "old private draft");
      const signal = accountSessionSignal();
      const revision = accountSessionRevision.value;
      setupHttpClient();
      adapter
        .onGet("/api/v1/auth/session")
        .reply(401, { message: "Invalid token" });
      await expect(
        httpClient.get("/api/v1/auth/session"),
      ).rejects.toBeDefined();
      expect(localStorage.getItem("account_id")).toBeNull();
      expect(localStorage.getItem("role")).toBeNull();
      expect(sessionStorage.getItem("chat-draft")).toBeNull();
      expect(signal.aborted).toBe(true);
      expect(accountSessionRevision.value).toBe(revision + 1);
      expect(window.location.hash).toBe("#/auth/login");
    } finally {
      adapter.restore();
    }
  });
  it("clears identity through the same logout path when a protected SSE request returns 401", async () => {
    const nativeFetch = vi
      .spyOn(window, "fetch")
      .mockResolvedValue(new Response("{}", { status: 401 }));
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
