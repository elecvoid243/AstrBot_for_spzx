import { ref } from "vue";
import { clearChatDrafts } from "../composables/useChatDrafts";

export const accountSessionRevision = ref(0);
let sessionController = new AbortController();

export function accountSessionSignal(): AbortSignal {
  return sessionController.signal;
}

/** Invalidate old requests, mounted chat state and module-held text drafts. */
export function resetAccountSession(clearIdentity = true): void {
  sessionController.abort();
  sessionController = new AbortController();
  accountSessionRevision.value += 1;
  clearChatDrafts();
  const sessionKeys = Array.from({ length: sessionStorage.length }, (_, i) =>
    sessionStorage.key(i),
  ).filter((key): key is string => key !== null);
  for (const key of sessionKeys) {
    if (/chat|lastBotRoute/i.test(key)) sessionStorage.removeItem(key);
  }
  for (const key of Object.keys(localStorage)) {
    if (key.startsWith("remote-chat-draft:")) localStorage.removeItem(key);
  }
  if (clearIdentity) {
    for (const key of [
      "user",
      "token",
      "resource_owner",
      "account_id",
      "role",
      "change_pwd_hint",
      "md5_pwd_hint",
      "password_upgrade_required",
    ]) {
      localStorage.removeItem(key);
    }
  }
  window.dispatchEvent(new Event("astrbot:account-changed"));
}

export function assertCurrentAccount(revision: number): void {
  if (revision !== accountSessionRevision.value) {
    throw new DOMException("Account changed", "AbortError");
  }
}

export function expireAccountSession(): void {
  // Public login-page requests can fail too; avoid repeatedly remounting the
  // form after the identity has already been cleared.
  if (localStorage.getItem("token") || localStorage.getItem("account_id"))
    resetAccountSession();
  window.location.hash = "/auth/login";
}

// A logout/login in another tab must invalidate this tab's old requests too.
window.addEventListener?.("storage", (event) => {
  if (event.key === "token" || event.key === "account_id") {
    resetAccountSession(false);
    window.location.reload();
  }
});
