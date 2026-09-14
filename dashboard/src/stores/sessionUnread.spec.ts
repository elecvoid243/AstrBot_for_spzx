// Author: elecvoid243
// Date: 2026-09-14
//
// Unit tests for the session-unread store backing the sidebar context
// menu's "mark as unread" highlight.

import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { useSessionUnreadStore } from "./sessionUnread";

describe("sessionUnread store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("marks sessions unread and dedups duplicates", () => {
    const store = useSessionUnreadStore();
    store.markUnread("s1");
    store.markUnread("s1");
    store.markUnread("s2");

    expect(store.sessionIds).toEqual(["s1", "s2"]);
    expect(store.count).toBe(2);
    expect(store.isUnread("s1")).toBe(true);
    expect(store.isUnread("s3")).toBe(false);
  });

  it("ignores empty session ids", () => {
    const store = useSessionUnreadStore();
    store.markUnread("");

    expect(store.sessionIds).toEqual([]);
  });

  it("marks a single session read and keeps the others", () => {
    const store = useSessionUnreadStore();
    store.markUnread("s1");
    store.markUnread("s2");

    store.markRead("s1");

    expect(store.isUnread("s1")).toBe(false);
    expect(store.isUnread("s2")).toBe(true);
    expect(store.count).toBe(1);
  });

  it("markRead is a no-op for sessions never marked", () => {
    const store = useSessionUnreadStore();
    store.markUnread("s1");

    store.markRead("s2");

    expect(store.sessionIds).toEqual(["s1"]);
  });
});
