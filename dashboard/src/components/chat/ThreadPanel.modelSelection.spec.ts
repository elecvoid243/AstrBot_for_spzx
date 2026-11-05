import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, ref } from "vue";
import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createVuetify } from "vuetify";
import { chatApi, skillGuideApi } from "@/api/v1";
import * as http from "@/api/http";
import { useProviderModelSelection } from "@/composables/useProviderModelSelection";
import ProviderModelMenu from "./ProviderModelMenu.vue";
import ChatInput from "./ChatInput.vue";
import ThreadPanel from "./ThreadPanel.vue";

vi.mock("@/stores/auth", () => ({ useAuthStore: () => ({ role: "user" }) }));

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("side-chat model selection", () => {
  it("shares a local model between the side header and real composer without saving to the ordinary session", async () => {
    vi.spyOn(skillGuideApi, "active").mockResolvedValue({
      data: { status: "ok", data: { skills: [] } },
    } as never);
    vi.spyOn(skillGuideApi, "inventory").mockResolvedValue({
      data: { status: "ok", data: { skills: [] } },
    } as never);
    const send = vi.spyOn(http, "fetchWithAuth").mockResolvedValue({
      ok: true,
      body: new ReadableStream({
        start(controller) {
          controller.close();
        },
      }),
    } as Response);
    const original = {
      selected_provider: "main-provider",
      selected_model: "main-model",
      thinking_effort: "xhigh",
      effort_preset: null,
    };
    vi.spyOn(chatApi, "getSessionSelection").mockResolvedValue({
      data: { status: "ok", data: original },
    } as never);
    const save = vi
      .spyOn(chatApi, "setSessionSelection")
      .mockImplementation(
        async (_id, selection) =>
          ({ data: { status: "ok", data: selection } }) as never,
      );
    vi.spyOn(chatApi, "getThread").mockResolvedValue({
      data: { data: { history: [] } },
    } as never);
    vi.spyOn(chatApi, "getUiSettings").mockResolvedValue({
      data: { status: "ok", data: { can_edit: false } },
    } as never);
    vi.spyOn(chatApi, "listConfigs").mockResolvedValue({
      data: {
        status: "ok",
        data: { info_list: [{ id: "default", name: "Default" }] },
      },
    } as never);
    vi.spyOn(chatApi, "getSessionConfig").mockResolvedValue({
      data: { status: "ok", data: { config_id: "default" } },
    } as never);
    let ordinary!: ReturnType<typeof useProviderModelSelection>;
    const Host = defineComponent({
      setup() {
        ordinary = useProviderModelSelection(ref("ordinary-main"));
        return () =>
          h("div", [
            h(ProviderModelMenu, { variant: "header", class: "main-menu" }),
            h(ThreadPanel, {
              modelValue: true,
              isDark: false,
              thread: {
                thread_id: "side-thread",
                parent_session_id: "ordinary-main",
                parent_message_id: 0,
                base_checkpoint_id: "",
                selected_text: "",
                created_at: "",
                updated_at: "",
              },
            }),
          ]);
      },
    });
    const wrapper = shallowMount(Host, {
      global: {
        plugins: [createVuetify()],
        stubs: {
          ThreadPanel: false,
          ChatInput: false,
          ProviderModelMenu: false,
          VBtn: true,
          VTextarea: true,
          VProgressLinear: true,
          VListItem: true,
          VListItemTitle: true,
          VProgressCircular: true,
          VOverlay: true,
        },
      },
    });
    try {
      await flushPromises();
      await ordinary.prepareSend();
      const side = wrapper.getComponent(ThreadPanel);
      const menu = side
        .getComponent(ProviderModelMenu)
        .getComponent({ name: "ProviderSelectMenu" });
      menu.vm.$emit("select", {
        id: "side-provider",
        model: "side-thread-model",
      });
      await flushPromises();
      expect(save).not.toHaveBeenCalled();
      expect(ordinary.selectedModelName.value).toBe("main-model");
      expect(side.getComponent(ChatInput).vm.getCurrentSelection()).toEqual({
        providerId: "side-provider",
        modelName: "side-thread-model",
      });
      const mainMenu = wrapper
        .findAllComponents(ProviderModelMenu)[0]
        .getComponent({ name: "ProviderSelectMenu" });
      mainMenu.vm.$emit("select", { id: "main-new", model: "main-new-model" });
      await flushPromises();
      await ordinary.prepareSend();
      expect(save).toHaveBeenCalledTimes(1);
      expect(save).toHaveBeenCalledWith("ordinary-main", {
        ...original,
        selected_provider: "main-new",
        selected_model: "main-new-model",
      });
      side
        .getComponent(ChatInput)
        .vm.$emit("send-command", "Use the side model");
      await flushPromises();
      expect(send).toHaveBeenCalledTimes(1);
      expect(JSON.parse(String(send.mock.calls[0][1]?.body))).toMatchObject({
        selected_provider: "side-provider",
        selected_model: "side-thread-model",
      });
      expect(save).toHaveBeenCalledTimes(1);
      expect(menu.props("fallbackModel")).toBe("side-thread-model");
      expect(side.getComponent(ChatInput).vm.getCurrentSelection()).toEqual({
        providerId: "side-provider",
        modelName: "side-thread-model",
      });
    } finally {
      wrapper.unmount();
    }
  });
});
