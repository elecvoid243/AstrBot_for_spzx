import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import WorkflowChatAccessDialog from "./WorkflowChatAccessDialog.vue";

const api = vi.hoisted(() => ({
  chatAccess: vi.fn(),
  saveChatAccess: vi.fn(),
  revokeChatAccess: vi.fn(),
}));
vi.mock("@/api/workflowAssets", () => ({ workflowAssetsApi: api }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
const slot = { template: "<div><slot /></div>" };
const stubs = {
  VDialog: slot,
  VCard: slot,
  VCardTitle: slot,
  VCardText: slot,
  VCardActions: slot,
  VAlert: slot,
  VSpacer: slot,
  VBtn: {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  VTextField: {
    props: ["modelValue", "label"],
    emits: ["update:modelValue"],
    template:
      '<input :aria-label="label" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
};

describe("explicit asset chat access", () => {
  it("opens the displayed fixed version to an exact platform caller and conversation, then revokes it", async () => {
    const grant = {
      platform_id: "qq-main",
      sender_id: "12345",
      source: "qq-main:FriendMessage:12345",
      profile_id: "research",
      version: 3,
    };
    api.chatAccess
      .mockResolvedValueOnce({ grants: [] })
      .mockResolvedValueOnce({ grants: [grant] })
      .mockResolvedValueOnce({ grants: [] });
    api.saveChatAccess.mockResolvedValue(grant);
    api.revokeChatAccess.mockResolvedValue({});
    const wrapper = mount(WorkflowChatAccessDialog, {
      props: {
        modelValue: true,
        asset: {
          asset_id: "report",
          version: 3,
          definition: { name: "Research" },
        },
      },
      global: { stubs },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Research · v3");
    expect(wrapper.text()).toContain("chatAccess.scope");
    await wrapper
      .get('input[aria-label="chatAccess.platform"]')
      .setValue("qq-main");
    await wrapper
      .get('input[aria-label="chatAccess.sender"]')
      .setValue("12345");
    await wrapper
      .get('input[aria-label="chatAccess.source"]')
      .setValue("qq-main:FriendMessage:12345");
    await wrapper
      .get('input[aria-label="chatAccess.profile"]')
      .setValue("research");
    await wrapper.get('[data-testid="save-chat-access"]').trigger("click");
    await flushPromises();
    expect(api.saveChatAccess).toHaveBeenCalledWith("report", grant);
    expect(wrapper.text()).toContain("qq-main:FriendMessage:12345");
    await wrapper.get('[data-testid="revoke-chat-access"]').trigger("click");
    await flushPromises();
    expect(api.revokeChatAccess).toHaveBeenCalledWith(
      "report",
      "qq-main",
      "12345",
    );
    expect(wrapper.text()).not.toContain("qq-main · 12345");
  });
});
