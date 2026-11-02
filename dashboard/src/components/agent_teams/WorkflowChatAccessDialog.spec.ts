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
    props: ["modelValue", "label", "readonly"],
    emits: ["update:modelValue"],
    template:
      '<input :aria-label="label" :readonly="readonly" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  VTextarea: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
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
    const textarea = wrapper.get("textarea");
    for (const invalid of [
      "{",
      JSON.stringify({ platform_id: "qq-main" }),
      JSON.stringify({
        ...grant,
        version: undefined,
        source: "other:GroupMessage:999",
      }),
    ]) {
      await textarea.setValue(invalid);
      expect(
        wrapper.get('[data-testid="save-chat-access"]').attributes("disabled"),
      ).toBeDefined();
      expect(wrapper.text()).toContain("chatAccess.invalidIdentity");
    }
    const { version: _version, ...caller } = grant;
    await textarea.setValue(JSON.stringify(caller));
    expect(
      wrapper
        .get('input[aria-label="chatAccess.sender"]')
        .attributes("readonly"),
    ).toBeDefined();
    await wrapper.get('[data-testid="save-chat-access"]').trigger("click");
    await flushPromises();
    expect(api.saveChatAccess).toHaveBeenCalledWith("report", grant);
    expect(wrapper.text()).toContain("qq-main:FriendMessage:12345");
    await wrapper.setProps({ modelValue: false });
    await wrapper.setProps({ modelValue: true });
    await flushPromises();
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

  it("preserves group sender independently of the UMO and retains input after a failed save", async () => {
    api.chatAccess.mockReset().mockResolvedValue({ grants: [] });
    api.saveChatAccess.mockReset().mockRejectedValue(new Error("denied"));
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
    const caller = {
      platform_id: "qq-main",
      sender_id: "12345",
      source: "qq-main:GroupMessage:88888",
      profile_id: "default",
    };
    const text = JSON.stringify(caller);
    await wrapper.get("textarea").setValue(text);
    await wrapper.get('[data-testid="save-chat-access"]').trigger("click");
    await flushPromises();
    expect(api.saveChatAccess).toHaveBeenCalledWith("report", {
      ...caller,
      version: 3,
    });
    expect((wrapper.get("textarea").element as HTMLTextAreaElement).value).toBe(
      text,
    );
  });
});
