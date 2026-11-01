// Author: elecvoid243
// Date: 2026-08-13
//
// Browser "attention" effects for the `ask_user_choice` reminder (Plan A):
// document.title flash, the Notification API, and the Badging API. The
// module owns the browser-side state (flash timer, visibility listener)
// and is intentionally i18n-free — a Vue component supplies localized
// strings and the "is this the currently viewed session" flag.

import { useInteractiveChoiceAttentionStore } from "@/stores/interactiveChoiceAttention";
import { useRunFinishedAttentionStore } from "@/stores/runFinishedAttention";

interface BadgingApi {
  setAppBadge?: (count: number) => Promise<void>;
  clearAppBadge?: () => Promise<void>;
}

let titleFlashTimer: number | null = null;
let originalTitle = "";
let noticeFlashTimer: number | null = null;
let noticeOriginalTitle = "";
let visibilityBound = false;

function startTitleFlash(attentionTitle: string): void {
  if (typeof document === "undefined" || titleFlashTimer !== null) return;
  // A pending choice outranks the bounded run-finished flash — reclaim
  // the title from it before starting the unbounded choice flash.
  stopNoticeFlash();
  originalTitle = document.title;
  let on = false;
  titleFlashTimer = window.setInterval(() => {
    on = !on;
    document.title = on ? attentionTitle : originalTitle;
  }, 800);
}

function stopTitleFlash(): void {
  if (typeof document === "undefined") return;
  if (titleFlashTimer !== null) {
    window.clearInterval(titleFlashTimer);
    titleFlashTimer = null;
  }
  if (originalTitle) {
    document.title = originalTitle;
    originalTitle = "";
  }
}

/**
 * Bounded title flash for informational run-finished notices: alternates
 * the title a fixed number of times, then restores it. Deliberately
 * self-terminating — unlike a pending choice, a finished run needs no
 * user action, so nagging forever would be noise.
 */
function startNoticeFlash(noticeTitle: string, flashes = 3): void {
  if (typeof document === "undefined") return;
  // The pending-choice flash is more urgent — never clobber it.
  if (titleFlashTimer !== null) return;
  stopNoticeFlash();
  noticeOriginalTitle = document.title;
  let on = false;
  let toggles = 0;
  noticeFlashTimer = window.setInterval(() => {
    on = !on;
    toggles += 1;
    document.title = on ? noticeTitle : noticeOriginalTitle;
    if (toggles >= flashes * 2) stopNoticeFlash();
  }, 800);
}

function stopNoticeFlash(): void {
  if (typeof document === "undefined") return;
  if (noticeFlashTimer !== null) {
    window.clearInterval(noticeFlashTimer);
    noticeFlashTimer = null;
  }
  if (noticeOriginalTitle) {
    document.title = noticeOriginalTitle;
    noticeOriginalTitle = "";
  }
}

function syncBadge(count: number): void {
  if (typeof navigator === "undefined") return;
  const nav = navigator as unknown as BadgingApi;
  if (typeof nav.setAppBadge !== "function") return;
  if (count > 0) {
    nav.setAppBadge(count).catch(() => {});
  } else if (typeof nav.clearAppBadge === "function") {
    nav.clearAppBadge().catch(() => {});
  }
}

function bindVisibilityOnce(): void {
  if (visibilityBound || typeof document === "undefined") return;
  visibilityBound = true;
  document.addEventListener("visibilitychange", () => {
    // The tab is visible again — a flashing title is pointless and
    // distracting. Keep the badge + sidebar highlight so the pending
    // choice is still discoverable.
    if (!document.hidden) {
      stopTitleFlash();
      stopNoticeFlash();
    }
  });
}

export interface ChoiceReminderText {
  title: string;
  body: string;
}

/**
 * Signal that a session now has a pending choice the user should act on.
 *
 * The session is always added to the attention store (sidebar highlight),
 * so the pending choice stays visible in the conversation list no matter
 * which session is open. The OS-level effects (title flash, notification,
 * badge) are only raised when the user is NOT already looking at that
 * session in a visible tab — the inline InteractiveChoiceBox is the
 * reminder there.
 */
export function markChoiceAttention(
  sessionId: string,
  text: ChoiceReminderText,
  isCurrentSession: boolean,
): void {
  const store = useInteractiveChoiceAttentionStore();
  store.add(sessionId);

  bindVisibilityOnce();

  const isHidden = typeof document !== "undefined" && document.hidden;
  if (isCurrentSession && !isHidden) return;

  if (
    typeof Notification !== "undefined" &&
    Notification.permission === "granted"
  ) {
    try {
      new Notification(text.title, { body: text.body, tag: "astrbot-choice" });
    } catch {
      // Some browsers throw despite a granted permission; the other
      // signals still fire, so swallowing is safe.
    }
  }

  startTitleFlash(text.title);
  syncBadge(store.count);
}

/** Clear a session's attention; resets the browser signals when empty. */
export function clearChoiceAttention(sessionId: string): void {
  const store = useInteractiveChoiceAttentionStore();
  store.clear(sessionId);
  if (store.count === 0) {
    stopTitleFlash();
    syncBadge(0);
  }
}

/**
 * Signal that a session's LLM run finished while the user was elsewhere.
 *
 * When the user is already viewing the session, nothing is recorded at
 * all — the arriving output itself is the reminder, and a marker on the
 * row they are looking at would be pure noise (unlike a pending choice,
 * which must stay discoverable until answered). Otherwise the session
 * gets a steady sidebar dot and the browser title flashes a bounded
 * number of times; no OS notification/badge is raised.
 */
export function markRunFinishedAttention(
  sessionId: string,
  noticeTitle: string,
  isCurrentSession: boolean,
): void {
  if (isCurrentSession) return;

  const store = useRunFinishedAttentionStore();
  store.add(sessionId);

  bindVisibilityOnce();
  startNoticeFlash(noticeTitle);
}

// Account changes also stop module-held browser attention signals.
window.addEventListener?.("astrbot:account-changed", () => {
  stopTitleFlash();
  stopNoticeFlash();
  syncBadge(0);
});
