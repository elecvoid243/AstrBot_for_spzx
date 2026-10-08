// Author: elecvoid243
// Date: 2026-10-08
//
// LLM request injection trail on a bot turn: a silent rail marker that
// expands into a panel listing plugin / handler / field deltas. The panel
// stays collapsed until clicked, and a message without injections renders no
// marker at all.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import ChatMessageList from "@/components/chat/ChatMessageList.vue";
import type { ChatContent, ChatRecord } from "@/composables/useMessages";

// Same store surface as ChatMessageList.revealWork.spec.ts.
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

function userRecord(id: string | number): ChatRecord {
  return {
    id,
    content: { type: "user", message: [TEXT("hi")] } as ChatContent,
  };
}

function botRecord(id: string | number, injections?: unknown): ChatRecord {
  return {
    id,
    content: {
      type: "bot",
      message: [TEXT("final answer")],
      llmRequestInjections: injections,
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

// The tooltip must render its activator slot content, or the marker button
// never reaches the DOM in tests.
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

const INJECTIONS = {
  items: [
    {
      plugin: "tc_memory",
      handler: "decorate_llm_req",
      changes: [
        {
          field: "system_prompt",
          delta: 412,
          lossy: false,
          preview: "## 长期记忆",
        },
        { field: "system_prompt", delta: -1200, lossy: true, preview: "" },
      ],
    },
  ],
};

function mountList(record: ChatRecord) {
  return mount(ChatMessageList, {
    props: { messages: [userRecord("u1"), record] },
    global: { components: vuetifyStubs },
  });
}

describe("ChatMessageList injection trail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders no marker for a message without injections", () => {
    const wrapper = mountList(botRecord("b1"));
    expect(wrapper.find(".injection-marker").exists()).toBe(false);
  });

  it("renders the rail marker and expands the panel on click", async () => {
    const wrapper = mountList(botRecord("b1", INJECTIONS));

    const marker = wrapper.find(".injection-marker");
    expect(marker.exists()).toBe(true);
    // Collapsed until clicked.
    expect(wrapper.find(".injection-panel").exists()).toBe(false);

    await marker.trigger("click");

    expect(wrapper.find(".injection-panel").exists()).toBe(true);
  });

  it("lists plugin, handler and marks the lossy change", async () => {
    const wrapper = mountList(botRecord("b1", INJECTIONS));
    await wrapper.find(".injection-marker").trigger("click");

    const panel = wrapper.find(".injection-panel");
    expect(panel.text()).toContain("tc_memory");
    expect(panel.text()).toContain("decorate_llm_req");
    expect(panel.text()).toContain("+412 字符");
    expect(panel.text()).toContain("已截断");
    expect(panel.html()).toContain("## 长期记忆");
  });
});
