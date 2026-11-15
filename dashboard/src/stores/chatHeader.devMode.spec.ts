// 2026-11-16 (elecvoid243): the header chip and the Chat.vue inspector share
// one store contract. Pin the open/turn/close/reset transitions so a chip
// click and a message marker open the same panel with the right anchor.

import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useChatHeaderStore } from "@/stores/chatHeader";

describe("chatHeader developer-mode bridge", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("stores visibility and opens at a turn", () => {
    const store = useChatHeaderStore();
    store.SET_DEV_MODE_STATE({ enabled: true, canEdit: true });
    store.OPEN_DEV_MODE("turn-1");

    expect(store.devModeEnabled).toBe(true);
    expect(store.devModeCanEdit).toBe(true);
    expect(store.devModeOpen).toBe(true);
    expect(store.devModeInitialTurnId).toBe("turn-1");
  });

  it("clears the turn anchor on close and hides when not editable", () => {
    const store = useChatHeaderStore();
    store.OPEN_DEV_MODE("turn-1");
    store.SET_DEV_MODE_OPEN(false);

    expect(store.devModeOpen).toBe(false);
    expect(store.devModeInitialTurnId).toBeNull();

    store.OPEN_DEV_MODE();
    store.SET_DEV_MODE_STATE({ enabled: false, canEdit: false });
    expect(store.devModeOpen).toBe(false);
    expect(store.devModeCanEdit).toBe(false);
  });

  it("reset clears the developer-mode state with the rest of the context", () => {
    const store = useChatHeaderStore();
    store.SET_DEV_MODE_STATE({ enabled: true, canEdit: true });
    store.OPEN_DEV_MODE("turn-1");
    store.CLEAR_CONTEXT();

    expect(store.devModeOpen).toBe(false);
    expect(store.devModeEnabled).toBe(false);
    expect(store.devModeCanEdit).toBe(false);
    expect(store.devModeInitialTurnId).toBeNull();
  });
});
