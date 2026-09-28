// Capsule vs a still-answerable ask_user_choice box (author: elecvoid243,
// 2026-09-28).
//
// The collapsed capsule renders only the reply region, and that region drops
// its `interactive_choice` parts — the box is "review history" once the run
// continued past it. A box that is still waiting for an answer must therefore
// keep its whole message out of the collapse.
//
// Tab liveness (`is-streaming`) is not a safe gate for that: a page reload
// drops the run's SSE connection (or the run is gone entirely — the server
// restarted, the answer came from another tab, reconcile has not caught up),
// and the pill then appeared while the box was still waiting, hiding it
// behind the capsule until the user happened to expand it. The reported bug.
//
// The gate reads the same state machine `InteractiveChoiceBox` renders from:
// a submitted / cancelled / ignored box is terminal and may fold.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import ChatMessageList from "@/components/chat/ChatMessageList.vue";
import type { ChatContent, ChatRecord } from "@/composables/useMessages";

const storeMock = vi.hoisted(() => ({
  activeChoices: {} as Record<string, Record<string, unknown>>,
  hydrate: vi.fn(),
  injectOrphans: () => 0,
  isIgnored: () => false,
  isCancelled: vi.fn(() => false),
  getSubmissionState: vi.fn(() => undefined as unknown),
  markIgnored: vi.fn(),
  addChoice: vi.fn(),
  submitChoice: vi.fn(),
  cancelChoice: vi.fn(),
  reconcile: vi.fn(),
}));

vi.mock("@/stores/interactiveChoice", () => ({
  useInteractiveChoiceStore: () => storeMock,
}));
vi.mock("@/stores/interactiveChoiceAttention", () => ({
  useInteractiveChoiceAttentionStore: () => ({ clearByUmo: vi.fn() }),
}));

const THINK = { type: "think", think: "pondering" };
const TEXT = (text: string) => ({ type: "plain", text });
const CHOICE = {
  type: "interactive_choice",
  request_id: "bc6bb8b4-8fa5-42dd-889c-86e3c6f3815a",
  prompt: "方案已给出，请确认实施范围",
  options: [
    { id: "a", label: "A" },
    { id: "b", label: "B" },
  ],
};

/** A finished turn whose reply text carries a trailing choice box. */
function botRecordWithChoice(id: number): ChatRecord {
  return {
    id,
    content: {
      type: "bot",
      message: [THINK, TEXT("实测结论很关键，方案可以落地了。"), CHOICE],
    } as ChatContent,
  };
}

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

/** Post-refresh mount: history only, no live run attached to the tab. */
function mountList() {
  return mount(ChatMessageList, {
    props: {
      messages: [botRecordWithChoice(8)],
      currentUmo: "webchat:FriendMessage:webchat!u!c",
      isStreaming: false,
    },
    global: { components: vuetifyStubs },
  });
}

describe("ChatMessageList capsule vs a pending choice box", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    storeMock.isCancelled.mockReturnValue(false);
    storeMock.getSubmissionState.mockReturnValue(undefined);
  });

  it("keeps the box on screen while it still waits for an answer", () => {
    const wrapper = mountList();
    expect(wrapper.find(".agent-work-pill").exists()).toBe(false);
    expect(wrapper.find(".interactive-choice-box").exists()).toBe(true);
  });

  it("collapses the box once it was answered, keeping it reviewable", async () => {
    storeMock.getSubmissionState.mockReturnValue({
      kind: "option",
      optionId: "a",
    });
    const wrapper = mountList();
    expect(wrapper.find(".agent-work-pill").exists()).toBe(true);
    expect(wrapper.find(".interactive-choice-box").exists()).toBe(false);
    // The capsule is the review path the collapse design promises: expanding
    // it renders the box again through the normal choice-box component.
    await wrapper.find(".agent-work-pill").trigger("click");
    expect(wrapper.find(".interactive-choice-box").exists()).toBe(true);
  });

  it("collapses the box once the server cancelled it", () => {
    storeMock.isCancelled.mockReturnValue(true);
    const wrapper = mountList();
    expect(wrapper.find(".agent-work-pill").exists()).toBe(true);
  });
});
