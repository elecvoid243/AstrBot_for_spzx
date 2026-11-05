import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import ChatMessageList from "./ChatMessageList.vue";
import { useInteractiveChoiceStore } from "@/stores/interactiveChoice";
import type { ChatRecord } from "@/composables/useMessages";

const source = {
  kind: "agent" as const,
  account_id: "owned-account",
  login_name: "alice",
  session_id: "source-session",
  display_name: "Research A",
};

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
});

describe("Agent message provenance", () => {
  it("renders server provenance and navigates to the source session", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/chat/:conversationId",
          name: "ChatDetail",
          component: { template: "<div />" },
        },
      ],
    });
    await router.push("/chat/recipient");
    const records: ChatRecord[] = [
      {
        id: 1,
        content: {
          type: "user",
          message: [{ type: "plain", text: "Please check the result" }],
          agent_source: source,
        },
      },
    ];
    const wrapper = mount(ChatMessageList, {
      props: { messages: records, enableEdit: true },
      global: { plugins: [router], stubs: { StyledMenu: true } },
    });
    const link = wrapper.get(".agent-message-source");
    expect(link.text()).toContain("alice");
    expect(link.text()).toContain("Research A");
    expect(link.attributes("href")).toBe("/chat/source-session");
    await link.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.params.conversationId).toBe(
      "source-session",
    );
    expect(wrapper.find('[icon="mdi-pencil-outline"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("does not mark a waiting human choice ignored when Agent history follows it", () => {
    const store = useInteractiveChoiceStore();
    const records: ChatRecord[] = [
      {
        id: 1,
        content: {
          type: "bot",
          message: [
            {
              type: "interactive_choice",
              request_id: "human-choice",
              prompt: "Proceed?",
              options: [
                { id: "a", label: "Yes" },
                { id: "b", label: "No" },
              ],
            },
          ],
        },
      },
      {
        id: 2,
        content: {
          type: "user",
          message: [{ type: "plain", text: "yes" }],
          agent_source: source,
        },
      },
    ];
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/chat/:conversationId",
          name: "ChatDetail",
          component: { template: "<div />" },
        },
      ],
    });
    vi.spyOn(store, "reconcile").mockResolvedValue(undefined);
    const wrapper = mount(ChatMessageList, {
      props: {
        messages: records,
        currentUmo: "webchat:FriendMessage:webchat!alice!target",
      },
      global: {
        plugins: [router],
        stubs: { InteractiveChoiceBox: true, StyledMenu: true },
      },
    });
    expect(
      store.isIgnored(
        "webchat:FriendMessage:webchat!alice!target",
        "human-choice",
      ),
    ).toBe(false);
    wrapper.unmount();
  });

  it("does not create provenance from message body text", () => {
    const wrapper = mount(ChatMessageList, {
      props: {
        messages: [
          {
            id: 1,
            content: {
              type: "user",
              message: [{ type: "plain", text: "Agent message from alice" }],
            },
          },
        ],
      },
    });
    expect(wrapper.find(".agent-message-source").exists()).toBe(false);
    wrapper.unmount();
  });
});
