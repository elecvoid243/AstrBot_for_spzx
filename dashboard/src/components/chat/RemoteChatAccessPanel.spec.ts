import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import RemoteChatAccessPanel from "./RemoteChatAccessPanel.vue";

const api = vi.hoisted(() => ({
  access: vi.fn(),
  setExposure: vi.fn(),
  createCredential: vi.fn(),
  revokeCredential: vi.fn(),
  setEnabled: vi.fn(),
}));
vi.mock("@/api/v1", () => ({ remoteChatApi: api }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

const container = defineComponent({
  setup:
    (_, { slots }) =>
    () =>
      h("div", slots.default?.()),
});
const stubs = {
  "v-btn": defineComponent({
    props: ["disabled"],
    emits: ["click"],
    setup:
      (props, { slots, emit }) =>
      () =>
        h(
          "button",
          { disabled: props.disabled, onClick: () => emit("click") },
          slots.default?.(),
        ),
  }),
  "v-text-field": defineComponent({
    props: ["modelValue"],
    emits: ["update:modelValue"],
    setup:
      (props, { emit }) =>
      () =>
        h("input", {
          value: props.modelValue,
          onInput: (event: any) =>
            emit("update:modelValue", event.target.value),
        }),
  }),
  "v-switch": defineComponent({
    props: ["modelValue"],
    emits: ["update:modelValue"],
    setup:
      (props, { emit }) =>
      () =>
        h("input", {
          type: "checkbox",
          checked: props.modelValue,
          onChange: (event: any) =>
            emit("update:modelValue", event.target.checked),
        }),
  }),
  "v-select": container,
  "v-autocomplete": container,
  "v-alert": container,
  "v-list": container,
  "v-list-item": defineComponent({
    setup:
      (_, { slots }) =>
      () =>
        h("div", [slots.default?.(), slots.append?.()]),
  }),
  "v-list-item-title": container,
  "v-progress-linear": true,
};

beforeEach(() => {
  vi.resetAllMocks();
  api.access.mockResolvedValue({
    data: {
      data: {
        enabled: false,
        mode: "selected",
        session_ids: ["own-session"],
        sessions: [
          {
            session_id: "own-session",
            display_name: "Own session",
            project_name: null,
          },
        ],
        credentials: [
          { credential_id: "own-key", name: "Laptop", revoked: false },
        ],
      },
    },
  });
  api.createCredential.mockResolvedValue({
    data: { data: { credential_id: "new-key", key: "arc_test_once" } },
  });
});

describe("remote account access panel", () => {
  it("saves exposure and shows a new secret only until hidden", async () => {
    const wrapper = mount(RemoteChatAccessPanel, { global: { stubs } });
    await flushPromises();
    expect(wrapper.text()).toContain("remoteAccess.disabled");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "remoteAccess.save")!
      .trigger("click");
    await flushPromises();
    expect(api.setExposure).toHaveBeenCalledWith("selected", ["own-session"]);
    await wrapper.find("input").setValue("Work laptop");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "remoteAccess.create")!
      .trigger("click");
    await flushPromises();
    expect(api.createCredential).toHaveBeenCalledWith("Work laptop");
    expect(wrapper.text()).toContain("arc_test_once");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "remoteAccess.hide")!
      .trigger("click");
    expect(wrapper.text()).not.toContain("arc_test_once");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "remoteAccess.revoke")!
      .trigger("click");
    await flushPromises();
    expect(api.revokeCredential).toHaveBeenCalledWith("own-key");
    wrapper.unmount();
  });

  it("shows a failed exposure save without claiming success", async () => {
    api.setExposure.mockRejectedValue({
      response: { data: { message: "Session is unavailable" } },
    });
    const wrapper = mount(RemoteChatAccessPanel, { global: { stubs } });
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "remoteAccess.save")!
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Session is unavailable");
    expect(wrapper.text()).not.toContain("remoteAccess.saved");
  });

  it("keeps the instance switch separate from personal exposure", async () => {
    const wrapper = mount(RemoteChatAccessPanel, {
      props: { systemOnly: true },
      global: { stubs },
    });
    await flushPromises();
    await wrapper.find('input[type="checkbox"]').setValue(true);
    await flushPromises();
    expect(api.setEnabled).toHaveBeenCalledWith(true);
    expect(api.setExposure).not.toHaveBeenCalled();
    expect(wrapper.text()).not.toContain("remoteAccess.credentials");
  });
});
