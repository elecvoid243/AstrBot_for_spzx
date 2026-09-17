// Tests for the message-search jump reveal (author: elecvoid243, 2026-09-17).
//
// Once an agent turn finishes, ChatMessageList collapses the work trail it
// produced before the final reply (thinking / tool calls / intermediate
// output) behind a "worked for ..." capsule. A message-search jump only
// scrolled the matched row into view, so a keyword that matched inside that
// hidden trail left the user on the right message with nothing to see — the
// reported bug this spec pins.
//
// Chat.vue now hands the target's ABSOLUTE history index down as
// `revealWorkIndex`, and the list opens the matching capsule:
//   1. without a reveal the capsule stays collapsed (unchanged default)
//   2. the reveal expands the target message's capsule
//   3. the reveal resolves against absolute indices, not window-local ones
//   4. a reveal that arrives before its row is loaded is adopted on arrival
//   5. clearing the reveal keeps the capsule open, leaves it manually
//      collapsible, and re-arms a later reveal of the same index

import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import ChatMessageList from "@/components/chat/ChatMessageList.vue";
import type { ChatContent, ChatRecord } from "@/composables/useMessages";

// The list mirrors pending interactive choices into Pinia on mount; the
// reveal under test is independent of that store surface.
const storeMock = vi.hoisted(() => ({
  activeChoices: {} as Record<string, Record<string, unknown>>,
  hydrate: vi.fn(),
  injectOrphans: () => 0,
  isIgnored: () => false,
  markIgnored: vi.fn(),
  addChoice: vi.fn(),
  submitChoice: vi.fn(),
  cancelChoice: vi.fn(),
}));

vi.mock("@/stores/interactiveChoice", () => ({
  useInteractiveChoiceStore: () => storeMock,
}));
vi.mock("@/stores/interactiveChoiceAttention", () => ({
  useInteractiveChoiceAttentionStore: () => ({ clearByUmo: vi.fn() }),
}));

const THINK = { type: "think", think: "pondering the keyword" };
const TOOL_CALL = {
  type: "tool_call",
  tool_calls: [
    {
      id: "t1",
      name: "astrbot_file_read_tool",
      args: { path: "a.py" },
      result: "ok",
      finished_ts: 2,
    },
  ],
};
const TEXT = (text: string) => ({ type: "plain", text });

function userRecord(id: string | number): ChatRecord {
  return {
    id,
    content: { type: "user", message: [TEXT("find it")] } as ChatContent,
  };
}

/** A bot turn whose work trail (think + tool call) precedes the final reply. */
function workedBotRecord(id: string | number): ChatRecord {
  return {
    id,
    content: {
      type: "bot",
      message: [THINK, TOOL_CALL, TEXT("final answer")],
    } as ChatContent,
  };
}

/** Passthrough stand-in so a Vuetify tag resolves without pulling Vuetify in. */
function passthrough(name: string) {
  return defineComponent({
    name,
    setup: (_props, { slots }) => () => h("div", { class: name }, slots.default?.()),
  });
}

// The capsule is plain markup plus these Vuetify wrappers; the setup file
// stubs v-icon / v-tooltip the same way.
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

function mountList(props: Record<string, unknown> = {}) {
  return mount(ChatMessageList, {
    props: { messages: [userRecord("u1"), workedBotRecord("b1")], ...props },
    global: { components: vuetifyStubs },
  });
}

/** `aria-expanded` is the capsule's own view of the collapsed state. */
function capsuleExpanded(wrapper: ReturnType<typeof mountList>) {
  const pill = wrapper.find(".agent-work-pill");
  expect(pill.exists()).toBe(true);
  return pill.attributes("aria-expanded") === "true";
}

describe("ChatMessageList agent-work reveal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("keeps the capsule collapsed without a reveal", () => {
    expect(capsuleExpanded(mountList())).toBe(false);
  });

  it("opens the capsule of the revealed message", () => {
    expect(capsuleExpanded(mountList({ revealWorkIndex: 1 }))).toBe(true);
  });

  it("resolves the reveal against absolute history indices", () => {
    // Only the bot turn is loaded and it sits at absolute index 5.
    const windowed = mountList({
      messages: [workedBotRecord("b1")],
      historyOffset: 5,
      revealWorkIndex: 5,
    });
    expect(capsuleExpanded(windowed)).toBe(true);
    // The same window-local index must not reveal anything.
    expect(capsuleExpanded(mountList({ revealWorkIndex: 0 }))).toBe(false);
  });

  it("adopts a reveal that lands before its row is loaded", async () => {
    const wrapper = mountList({
      messages: [userRecord("u1")],
      revealWorkIndex: 1,
    });
    expect(wrapper.find(".agent-work-pill").exists()).toBe(false);

    // The jump pages the older history in: the row now exists and must be
    // expanded without a second reveal request.
    await wrapper.setProps({ messages: [userRecord("u1"), workedBotRecord("b1")] });
    expect(capsuleExpanded(wrapper)).toBe(true);
  });

  it("stays expanded after the reveal is cleared, and can be collapsed", async () => {
    const wrapper = mountList({ revealWorkIndex: 1 });
    expect(capsuleExpanded(wrapper)).toBe(true);

    // The parent clears the consumed index once the landing is done; the
    // capsule is local state by then and must not snap shut.
    await wrapper.setProps({ revealWorkIndex: null });
    expect(capsuleExpanded(wrapper)).toBe(true);

    // It toggles like any other expansion.
    await wrapper.find(".agent-work-pill").trigger("click");
    expect(capsuleExpanded(wrapper)).toBe(false);

    // Re-searching the same hit reveals it again.
    await wrapper.setProps({ revealWorkIndex: 1 });
    expect(capsuleExpanded(wrapper)).toBe(true);
  });
});
