import { beforeEach, describe, expect, it, vi } from "vitest";
import { AxiosError } from "axios";
import { useChatDrafts, NEW_CHAT_DRAFT_KEY } from "@/composables/useChatDrafts";
import {
  accountSessionRevision,
  accountSessionSignal,
  assertCurrentAccount,
  resetAccountSession,
  expireAccountSession,
} from "./accountSession";
import { httpClient, setupHttpClient } from "@/api/http";

setupHttpClient();
beforeEach(() => {
  resetAccountSession();
  localStorage.clear();
  sessionStorage.clear();
});

describe("account session changes", () => {
  it("clears mounted-session identity and module-held drafts and aborts old work", () => {
    const drafts = useChatDrafts();
    drafts.setDraft(NEW_CHAT_DRAFT_KEY, "private text");
    localStorage.setItem("token", "old-credential");
    localStorage.setItem("resource_owner", "owner-a");
    sessionStorage.setItem("astrbot:lastChatSessionId", "private-session");
    const oldSignal = accountSessionSignal();
    const revision = accountSessionRevision.value;
    resetAccountSession();
    expect(oldSignal.aborted).toBe(true);
    expect(drafts.draftFor(NEW_CHAT_DRAFT_KEY)).toBe("");
    expect(localStorage.getItem("resource_owner")).toBeNull();
    expect(sessionStorage.getItem("astrbot:lastChatSessionId")).toBeNull();
    expect(() => assertCurrentAccount(revision)).toThrow("Account changed");
  });

  it("does not remount an already logged-out login form on repeated public 401s", () => {
    const revision = accountSessionRevision.value;
    expireAccountSession();
    expireAccountSession();
    expect(accountSessionRevision.value).toBe(revision);
  });

  it("drops a delayed successful Axios response after switching accounts", async () => {
    let complete!: (value: any) => void;
    const pending = httpClient.get("/api/v1/chat/sessions", {
      adapter: (config) =>
        new Promise((resolve) => {
          complete = (data) =>
            resolve({
              data,
              config,
              status: 200,
              statusText: "OK",
              headers: {},
            });
        }),
    });
    await vi.waitFor(() => expect(typeof complete).toBe("function"));
    resetAccountSession();
    localStorage.setItem("token", "new-user");
    complete({ private: "old-user" });
    await expect(pending).rejects.toBeDefined();
    expect(localStorage.getItem("token")).toBe("new-user");
  });

  it("does not let an old 401 sign out the new account", async () => {
    let rejectOld!: () => void;
    const pending = httpClient.get("/api/v1/chat/sessions", {
      adapter: (config) =>
        new Promise((_resolve, reject) => {
          rejectOld = () =>
            reject(
              new AxiosError("expired", "ERR_BAD_RESPONSE", config, null, {
                status: 401,
                data: {},
                config,
                headers: {},
                statusText: "Unauthorized",
              }),
            );
        }),
    });
    await vi.waitFor(() => expect(typeof rejectOld).toBe("function"));
    resetAccountSession();
    localStorage.setItem("token", "new-user");
    rejectOld();
    await expect(pending).rejects.toBeDefined();
    expect(localStorage.getItem("token")).toBe("new-user");
  });
});
