// 2026-11-16 (elecvoid243): the bot message exposes a compact DEV marker
// only while live snapshot metadata is attached. History reloads do not
// carry the field, so no marker should appear for a plain bot record.

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

const TEXT = (text: string) => ({ type: "plain", text });

function botRecord(id: string, snapshots?: unknown[]): ChatRecord {
  return {
    id,
    created_at: "2026-11-16T00:00:00Z",
    content: {
      type: "bot",
      message: [TEXT("final answer")],
      llmRequestSnapshots: snapshots as never,
    } as ChatContent,
  };
}

function passthrough(name: string) {
  return defineComponent({
    name,
    setup: (_props, { slots }) => () => h("div", { class: name }, slots.default?.()),
  });
}

const tooltipStub = defineComponent({
  name: "v-tooltip",
  setup: (_props, { slots }) => () =>
    h("div", { class: "v-tooltip" }, [
      slots.activator?.({ props: {} }),
      slots.default?.(),
    ]),
});

const vuetifyStubs = {
  ...Object.fromEntries(
    [
      "v-avatar",
      "v-btn",
      "v-card",
      "v-list-item",
      "v-list-item-title",
      "v-menu",
      "v-overlay",
      "v-progress-circular",
      "v-icon",
    ].map((tag) => [tag, passthrough(tag)]),
  ),
  "v-tooltip": tooltipStub,
};

function mountList(record: ChatRecord) {
  return mount(ChatMessageList, {
    props: { messages: [record] },
    global: { components: vuetifyStubs },
  });
}

describe("ChatMessageList developer-mode marker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders no marker without live snapshots", () => {
    const wrapper = mountList(botRecord("b1"));
    expect(wrapper.find(".dev-mode-marker").exists()).toBe(false);
  });

  it("renders the snapshot count and emits the message on click", async () => {
    const record = botRecord("b1", [
      { snapshot_id: "s1", turn_id: "t1" },
      { snapshot_id: "s2", turn_id: "t1" },
    ]);
    const wrapper = mountList(record);
    const marker = wrapper.find(".dev-mode-marker");
    expect(marker.exists()).toBe(true);
    expect(marker.text()).toContain("DEV");
    expect(marker.text()).toContain("2");

    await marker.trigger("click");
    expect(wrapper.emitted("openDevSnapshots")?.[0]?.[0]).toMatchObject({
      id: "b1",
    });
  });
});
