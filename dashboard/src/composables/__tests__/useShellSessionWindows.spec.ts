// Window-state actions for floating shell session output windows.
// Multiple windows coexist; reopening a session focuses (topmost = last).
import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { useChatHeaderStore } from "@/stores/chatHeader";

describe("chatHeader shell session windows", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("opens once, focuses on re-open, closes, and clears with context", () => {
    const store = useChatHeaderStore();

    store.SET_SHELL_WINDOW_OPEN("sh_a", true);
    store.SET_SHELL_WINDOW_OPEN("sh_b", true);
    expect(store.openShellWindows).toEqual(["sh_a", "sh_b"]);

    // Reopening an open window raises it instead of duplicating.
    store.SET_SHELL_WINDOW_OPEN("sh_a", true);
    expect(store.openShellWindows).toEqual(["sh_b", "sh_a"]);

    store.FOCUS_SHELL_WINDOW("sh_b");
    expect(store.openShellWindows).toEqual(["sh_a", "sh_b"]);

    store.SET_SHELL_WINDOW_OPEN("sh_b", false);
    expect(store.openShellWindows).toEqual(["sh_a"]);

    store.CLEAR_CONTEXT();
    expect(store.openShellWindows).toEqual([]);
  });
});
