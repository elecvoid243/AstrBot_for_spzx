// Author: elecvoid243
// Date: 2026-10-17
// The rail mark is the only "this turn is alive" signal now that the avatar
// spinner is gone. Pin the three states the list maps onto it: waiting before
// the first token, slowed while text streams on the last row, and invisible
// once the turn settles (the rail slot stays so the message never shifts).

import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import ChatMessageList from "@/components/chat/ChatMessageList.vue";
import ToolCallCard from "@/components/chat/message_list_comps/ToolCallCard.vue";
import type { ChatContent, ChatRecord } from "@/composables/useMessages";

// The list mirrors pending interactive choices into Pinia on mount; this
// spec only cares about the generation mark.
vi.mock("@/stores/interactiveChoice", () => ({
  useInteractiveChoiceStore: () => ({
    activeChoices: {},
    hydrate: vi.fn(),
    injectOrphans: () => 0,
    isIgnored: () => false,
    markIgnored: vi.fn(),
    addChoice: vi.fn(),
    submitChoice: vi.fn(),
    cancelChoice: vi.fn(),
  }),
}));
vi.mock("@/stores/interactiveChoiceAttention", () => ({
  useInteractiveChoiceAttentionStore: () => ({ clearByUmo: vi.fn() }),
}));

/** Passthrough stand-in so Vuetify tags resolve without pulling Vuetify in. */
function passthrough(name: string) {
  return defineComponent({
    name,
    setup: (_props, { slots }) => () => h("div", { class: name }, slots.default?.()),
  });
}

const vuetifyStubs = Object.fromEntries(
  [
    "v-avatar",
    "v-btn",
    "v-card",
    "v-list-item",
    "v-list-item-title",
    "v-menu",
    "v-overlay",
    "v-progress-circular",
  ].map((tag) => [tag, passthrough(tag)]),
);

function botRecord(
  id: string,
  content: Partial<ChatContent> = {},
): ChatRecord {
  return {
    id,
    content: { type: "bot", message: [], ...content } as ChatContent,
  };
}

function mountList(messages: ChatRecord[], isStreaming = false) {
  return mount(ChatMessageList, {
    props: { messages, isStreaming },
    global: { components: vuetifyStubs },
  });
}

function railMark(wrapper: ReturnType<typeof mountList>) {
  return wrapper.find(".message-rail .generation-indicator");
}

describe("ChatMessageList generation mark", () => {
  it("only enables tool timers for the live reply, including after history recovery", async () => {
    const messages = ["old", "live"].map((id) => botRecord(id, {
      message: [{ type: "tool_call", tool_calls: [{ id, name: "astrbot_shell_session", ts: 1 }] }],
    }));
    const wrapper = mountList(messages);
    await wrapper.setProps({ variant: "thread" });
    for (const pill of wrapper.findAll(".agent-work-pill")) await pill.trigger("click");
    for (const header of wrapper.findAll(".reasoning-header")) await header.trigger("click");
    const cards = wrapper.findAllComponents(ToolCallCard);
    expect(cards).toHaveLength(2);
    expect(cards.map((card) => card.props("isActive"))).toEqual([false, false]);
    await wrapper.setProps({ isStreaming: true });
    expect(cards.map((card) => card.props("isActive"))).toEqual([false, true]);
    await wrapper.setProps({ messages: [
      ...messages,
      { id: "follow-up", content: { type: "user", message: [{ type: "plain", text: "continue" }] } },
    ] });
    expect(wrapper.findAllComponents(ToolCallCard).map((card) => card.props("isActive")))
      .toEqual([false, true]);
    await wrapper.setProps({ isStreaming: false });
    expect(cards.map((card) => card.props("isActive"))).toEqual([false, false]);
    wrapper.unmount();
  });
  it("waits (fast orbit) before the first token", () => {
    const wrapper = mountList([botRecord("b1", { isLoading: true })], true);
    const mark = railMark(wrapper);

    expect(mark.exists()).toBe(true);
    expect(mark.classes()).not.toContain("is-slow");
    expect(mark.classes()).not.toContain("is-off");
    // The label next to it reuses the existing text sweep.
    expect(wrapper.find(".loading-message .thinking-indicator").exists()).toBe(
      true,
    );
  });

  it("slows down once text is streaming on the last row", () => {
    const wrapper = mountList(
      [
        botRecord("b1", {
          message: [{ type: "plain", text: "streaming answer" }],
        }),
      ],
      true,
    );

    expect(railMark(wrapper).classes()).toContain("is-slow");
    expect(wrapper.find(".loading-message").exists()).toBe(false);
  });

  it("keeps the mark in place but invisible after the turn settles", () => {
    const wrapper = mountList([
      botRecord("b1", { message: [{ type: "plain", text: "done" }] }),
    ]);

    // Still rendered so the fade-out can play and the rail width never jumps.
    expect(railMark(wrapper).exists()).toBe(true);
    expect(railMark(wrapper).classes()).toContain("is-off");
  });
});
