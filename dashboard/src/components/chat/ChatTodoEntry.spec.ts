// Source contract for the todo summary entry (author: elecvoid243, 2026-11-07).
//
// The summary used to be a draggable floating pill inside the message panel.
// It now lives in the app bar next to the goal entry, so the toolbar is the
// single home for "what is the agent working on" affordances. The snapshot
// crosses the Chat.vue -> VerticalHeader boundary through the chatHeader store,
// mirroring the existing goalBadge push pattern. These assertions pin that
// split: the message panel must not render its own copy again, and the header
// must stay the only consumer of the shared state.
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

function readSource(filename: string): string {
  return readFileSync(fileURLToPath(new URL(filename, import.meta.url)), "utf8");
}

describe("todo summary entry lives in the app bar", () => {
  const chatSource = readSource("./Chat.vue");
  const headerSource = readSource(
    "../../layouts/full/vertical-header/VerticalHeader.vue",
  );
  const storeSource = readSource("../../stores/chatHeader.ts");

  it("removes the floating pill and its drag/placement machinery from Chat.vue", () => {
    expect(chatSource).not.toContain("todo-summary-bar");
    expect(chatSource).not.toContain("todo-summary-menu");
    expect(chatSource).not.toContain("todoBarPos");
    expect(chatSource).not.toMatch(/function placeTodoMenu/);
  });

  it("pushes the active session's snapshot into the header store", () => {
    expect(chatSource).toMatch(
      /watch\(\s*currentTodoSnapshot,\s*\(snap\) => chatHeader\.SET_TODO_SNAPSHOT\(snap\)/,
    );
  });

  it("renders the entry next to the goal button as a menu activator", () => {
    expect(headerSource).toMatch(
      /class="chat-action-btn goal-sidebar-trigger"[\s\S]*?<v-menu[\s\S]*?v-model="todoMenuOpen"/,
    );
    expect(headerSource).toMatch(
      /class="chat-action-btn todo-summary-trigger"/,
    );
    expect(headerSource).toContain('tm("todo.summary")');
    // The list expands in a menu, never as a pill over the message flow.
    expect(headerSource).toMatch(/<TodoListPanel[\s\S]*?collapsible/);
  });

  it("keeps the shared state and a null-snapshot close rule in the store", () => {
    expect(storeSource).toMatch(/todoSnapshot:\s*null as TodoSnapshot \| null/);
    expect(storeSource).toMatch(/todoMenuOpen:\s*false/);
    expect(storeSource).toMatch(/SET_TODO_MENU_OPEN\(open: boolean\)/);
    expect(storeSource).toMatch(/if \(!snapshot\) this\.todoMenuOpen = false;/);
  });
});
