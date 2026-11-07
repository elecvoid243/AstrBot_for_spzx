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
    expect(link.get(".agent-message-source-title").text()).toBe("Research A");
    expect(link.get(".agent-message-source-meta").text()).toContain("alice");
    expect(link.attributes("aria-label")).toBe(
      "Agent 消息来源 · alice · Research A",
    );
    expect(link.attributes("title")).toBe(link.attributes("aria-label"));
    expect(
      link.get(".agent-message-source-icon").attributes("aria-hidden"),
    ).toBe("true");
    expect(
      link.get(".agent-message-source-arrow").attributes("aria-hidden"),
    ).toBe("true");
    expect(link.attributes("href")).toBe("/chat/source-session");
    await link.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.params.conversationId).toBe(
      "source-session",
    );
    expect(wrapper.find('[icon="mdi-pencil-outline"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it.each([
    { name: "an empty title", displayName: "", loginName: "alice" },
    { name: "blank title and login", displayName: "   ", loginName: "" },
    {
      name: "long title and login",
      displayName:
        "Release verification across all supported platforms ".repeat(5),
      loginName: "astrbot-release-verification-account-".repeat(5),
    },
  ])(
    "keeps full source identity accessible for $name",
    ({ displayName, loginName }) => {
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
      const wrapper = mount(ChatMessageList, {
        props: {
          messages: [
            {
              id: 1,
              content: {
                type: "user",
                message: [{ type: "plain", text: "Verification result" }],
                agent_source: {
                  ...source,
                  display_name: displayName,
                  login_name: loginName,
                },
              },
            },
          ],
        },
        global: { plugins: [router], stubs: { StyledMenu: true } },
      });
      const title = displayName.trim() || source.session_id;
      const account = loginName || source.account_id;
      const link = wrapper.get("a.agent-message-source");
      expect(link.get(".agent-message-source-title").text()).toBe(title);
      expect(link.get(".agent-message-source-account").text()).toBe(account);
      expect(link.attributes("aria-label")).toBe(
        `Agent 消息来源 · ${account} · ${title}`,
      );
      expect(link.attributes("title")).toBe(link.attributes("aria-label"));
      expect(link.attributes("href")).toBe("/chat/source-session");
      wrapper.unmount();
    },
  );

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
