// useChatDrafts.spec.ts
// Author: elecvoid243, 2026-09-13
//
// Unit tests for the per-session composer draft store. Each test re-imports
// the module (vi.resetModules) so the module-level state reloads from
// localStorage — which is exactly what happens across a page reload.

import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import {
  NEW_CHAT_DRAFT_KEY,
  useChatDrafts,
} from "@/composables/useChatDrafts";

async function freshStore() {
  vi.resetModules();
  const mod = await import("@/composables/useChatDrafts");
  return mod.useChatDrafts();
}

describe("useChatDrafts", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("stores and returns a draft per session", async () => {
    const drafts = await freshStore();
    drafts.setDraft("sess-a", "hello A");
    drafts.setDraft("sess-b", "hello B");
    expect(drafts.draftFor("sess-a")).toBe("hello A");
    expect(drafts.draftFor("sess-b")).toBe("hello B");
    expect(drafts.draftFor("sess-c")).toBe("");
  });

  it("setting empty text deletes the entry", async () => {
    const drafts = await freshStore();
    drafts.setDraft("sess-a", "hello");
    drafts.setDraft("sess-a", "");
    expect(drafts.draftFor("sess-a")).toBe("");
    expect(localStorage.getItem("chat.sessionDrafts.v1")).not.toContain(
      "sess-a",
    );
  });

  it("ignores writes with an empty session id", async () => {
    const drafts = await freshStore();
    drafts.setDraft("", "orphan");
    expect(localStorage.getItem("chat.sessionDrafts.v1")).toBeNull();
  });

  it("persists drafts across a reload", async () => {
    const drafts = await freshStore();
    drafts.setDraft("sess-a", "survives reload");

    // Fresh module instance = fresh page load reading localStorage.
    const reloaded = await freshStore();
    expect(reloaded.draftFor("sess-a")).toBe("survives reload");
  });

  it("keeps the no-session slot under NEW_CHAT_DRAFT_KEY", async () => {
    const drafts = await freshStore();
    drafts.setDraft(NEW_CHAT_DRAFT_KEY, "half-typed");
    expect(drafts.draftFor(NEW_CHAT_DRAFT_KEY)).toBe("half-typed");

    const reloaded = await freshStore();
    expect(reloaded.draftFor(NEW_CHAT_DRAFT_KEY)).toBe("half-typed");
    reloaded.clearDraft(NEW_CHAT_DRAFT_KEY);
    expect(reloaded.draftFor(NEW_CHAT_DRAFT_KEY)).toBe("");
  });

  it("drops the oldest drafts beyond MAX_DRAFTS", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const drafts = await freshStore();
    // The store's cap is 50; write 51 with distinct timestamps and check
    // the earliest session was pruned while the newest survived.
    for (let i = 0; i < 51; i++) {
      drafts.setDraft(`sess-${i}`, `text-${i}`);
      vi.setSystemTime(i + 1);
    }
    expect(drafts.draftFor("sess-0")).toBe("");
    expect(drafts.draftFor("sess-1")).toBe("text-1");
    expect(drafts.draftFor("sess-50")).toBe("text-50");
  });

  it("starts clean when localStorage holds corrupted JSON", async () => {
    localStorage.setItem("chat.sessionDrafts.v1", "{not json");
    const drafts = await freshStore();
    expect(drafts.draftFor("sess-a")).toBe("");
    // The store still works afterwards.
    drafts.setDraft("sess-a", "hello");
    expect(drafts.draftFor("sess-a")).toBe("hello");
  });
});
