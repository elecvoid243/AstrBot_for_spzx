// Tests for TodoListResult.vue — the message-list result card of the
// spcode todo tool.
//
// Focus: spcode_toolkit v2.35 merged the six standalone todo tools into
// `todo_manage(action=...)`. The card used to branch on the tool name
// alone, so every merged result fell through to the legacy data-shape
// inference (and `clear`, which returns no list, rendered as an error).
// These tests pin the action-driven branches and the legacy names that
// must keep replaying from old session history.
//
// Author: elecvoid243 | 2026-11-15

import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import TodoListResult from "./TodoListResult.vue";
import { SPCODE_TOOL_NAMES } from "./icons";

const STUBS = {
  // Icons carry no text in the app (the glyph is a pseudo element).
  "v-icon": { template: "<i class='icon-stub' />" },
  TodoListPanel: {
    name: "TodoListPanel",
    props: ["list", "stats", "attentionItems"],
    template: "<div class='panel-stub' />",
  },
};

const LIST = {
  title: "任务清单",
  items: [{ id: 1, title: "first", status: "done", notes: "" }],
};
const STATS = { total: 3, done: 1 };

function mountCard(toolName: string, data: any, args?: any) {
  return mount(TodoListResult, {
    props: { data, args, toolName },
    global: { stubs: STUBS },
  });
}

describe("TodoListResult — merged todo_manage", () => {
  it("dispatches through the spcode tool gate", () => {
    // Without this entry the whole card degrades to raw JSON.
    expect(SPCODE_TOOL_NAMES.has("todo_manage")).toBe(true);
  });

  it("renders create from args.action", () => {
    const wrapper = mountCard(
      "todo_manage",
      {
        list_title: "任务清单",
        item_count: 3,
        previous_item_count: 0,
        list: LIST,
        stats: STATS,
      },
      { action: "create", items: [{ title: "first" }] },
    );
    expect(wrapper.text()).toContain("List created: 任务清单");
    expect(wrapper.text()).toContain("3 items");
    expect(wrapper.find(".panel-stub").exists()).toBe(true);
  });

  it("renders update from args.action", () => {
    const wrapper = mountCard(
      "todo_manage",
      {
        item_ids: [2, 3],
        items: [{ id: 2, title: "second", status: "done", notes: "" }],
        list: LIST,
        stats: STATS,
      },
      { action: "update", item_ids: [2, 3], status: "done" },
    );
    expect(wrapper.text()).toContain("Items #2, #3 updated");
    expect(wrapper.find(".panel-stub").exists()).toBe(true);
  });

  it("renders clear from args.action (no list in the result)", () => {
    const wrapper = mountCard(
      "todo_manage",
      { deleted: "list", file: "todos.md" },
      { action: "clear" },
    );
    expect(wrapper.text()).toContain("List cleared");
    expect(wrapper.find(".status-row.success").exists()).toBe(true);
    expect(wrapper.find(".panel-stub").exists()).toBe(false);
  });

  it("shows the failure branch for an unusable result", () => {
    const wrapper = mountCard(
      "todo_manage",
      { ok: false, error: "action 必填" },
      { action: "nope" },
    );
    expect(wrapper.text()).toContain("action 必填");
  });
});

describe("TodoListResult — legacy standalone tools", () => {
  it("keeps rendering todo_create", () => {
    const wrapper = mountCard(
      "todo_create",
      { list_title: "old", item_count: 1, list: LIST, stats: STATS },
      { title: "old" },
    );
    expect(wrapper.text()).toContain("List created: old");
  });

  it("keeps rendering todo_clear", () => {
    const wrapper = mountCard("todo_clear", { deleted: "list" });
    expect(wrapper.text()).toContain("List cleared");
  });

  it("falls back to the data shape for legacy todo_list", () => {
    const wrapper = mountCard("todo_list", {
      list: { title: "legacy", items: [{ id: 1, title: "a", status: "pending" }] },
      stats: STATS,
    });
    expect(wrapper.text()).toContain("legacy");
    expect(wrapper.find(".panel-stub").exists()).toBe(true);
  });
});
