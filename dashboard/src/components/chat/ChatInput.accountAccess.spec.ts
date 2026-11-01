import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { createVuetify } from "vuetify";
import { mount } from "@vue/test-utils";
import {
  chatApi,
  commandApi,
  configProfileApi,
  configRouteApi,
} from "@/api/v1";
vi.mock("@/stores/auth", () => ({ useAuthStore: () => ({ role: "user" }) }));
import ChatInput from "./ChatInput.vue";

const Button = defineComponent({
  setup(_, { attrs, slots }) {
    return () => h("button", attrs, slots.default?.());
  },
});
const Tooltip = defineComponent({
  setup(_, { slots }) {
    return () =>
      h("span", slots.activator?.({ props: {} }) || slots.default?.());
  },
});

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
});

describe("shared ChatInput for ordinary accounts", () => {
  it("sends text through the existing composer and does not initialize management APIs", async () => {
    const profileList = vi.spyOn(configProfileApi, "list");
    const routes = vi.spyOn(configRouteApi, "list");
    const settings = vi
      .spyOn(chatApi, "getUiSettings")
      .mockResolvedValue({
        data: { status: "ok", data: { can_edit: false } },
      } as never);
    vi.spyOn(chatApi, "listModels").mockResolvedValue({
      data: { status: "ok", data: [] },
    } as never);
    vi.spyOn(chatApi, "listConfigs").mockResolvedValue({
      data: { status: "ok", data: { info_list: [] } },
    } as never);
    const commands = vi.spyOn(commandApi, "list");
    const wrapper = mount(ChatInput, {
      props: {
        prompt: "Private plain text",
        stagedImagesUrl: [],
        stagedAudioUrl: "",
        disabled: false,
        enableStreaming: true,
        isRecording: false,
        isRunning: false,
      },
      global: {
        plugins: [createVuetify()],
        stubs: {
          VBtn: Button,
          VTooltip: Tooltip,
          VTextarea: true,
          VProgressLinear: true,
          VListItemTitle: true,
          VListItem: true,
          VProgressCircular: true,
          VOverlay: true,
          ProjectLoadDialog: true,
          ThinkingEffortLevelsDialog: true,
          QuickMessagesDialog: true,
          CommentsPreviewDialog: true,
        },
      },
    });
    await wrapper.get('button[aria-label="发送"]').trigger("click");
    expect(wrapper.emitted("send")).toEqual([[[]]]);
    expect(wrapper.find(".input-area__status-row").exists()).toBe(false);
    expect(wrapper.find(".composer-toolbar__left").exists()).toBe(true);
    expect(profileList).not.toHaveBeenCalled();
    expect(routes).not.toHaveBeenCalled();
    expect(settings).toHaveBeenCalledTimes(1);
    expect(commands).not.toHaveBeenCalled();
    wrapper.unmount();
    vi.restoreAllMocks();
  });
});
