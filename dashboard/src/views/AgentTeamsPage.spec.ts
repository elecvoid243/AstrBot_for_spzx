// The workbench exposes versioned assets and their runs.
import { mount } from "@vue/test-utils";
import { defineComponent, h, inject, nextTick, provide } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentTeamsPage from "./AgentTeamsPage.vue";

const mocks = vi.hoisted(() => ({ route: null as any }));
vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual<typeof import("vue")>("vue");
  mocks.route = reactive({ query: {} });
  return {
    useRoute: () => mocks.route,
    useRouter: () => ({
      replace: async ({ query }: { query: Record<string, unknown> }) => {
        mocks.route.query = query;
      },
    }),
  };
});
const TABS_KEY = Symbol("tabs-stub");
const stubs = {
  WorkflowWorkspacePanel: {
    name: "WorkspaceStub",
    props: ["mode", "assetKind"],
    emits: ["started", "installed"],
    template: '<div class="workflow-workspace-stub" :mode="mode" />',
  },
  RoleTemplatesPanel: { template: '<div class="role-templates-panel-stub" />' },
  "v-container": { template: "<div><slot /></div>" },
  "v-chip": { template: '<span class="chip-stub"><slot /></span>' },
  "v-btn": {
    props: {
      icon: { type: String, default: undefined },
      disabled: { type: Boolean, default: false },
      loading: { type: Boolean, default: false },
    },
    emits: ["click"],
    template:
      '<button type="button" :disabled="disabled || loading" @click="$emit(\'click\', $event)"><i v-if="icon" class="mdi" :class="icon" /><slot /></button>',
  },
  "v-tabs": defineComponent({
    props: { modelValue: { type: String, default: "" } },
    emits: ["update:modelValue"],
    setup(props, { slots, emit }) {
      provide(TABS_KEY, (value: string) => emit("update:modelValue", value));
      return () => h("div", { class: "tabs-stub" }, slots.default?.());
    },
  }),
  "v-tab": defineComponent({
    props: { value: { type: String, default: "" } },
    setup(props, { slots }) {
      const select = inject<((value: string) => void) | null>(TABS_KEY, null);
      return () =>
        h(
          "button",
          {
            type: "button",
            class: "tab-stub",
            onClick: () => select?.(props.value),
          },
          slots.default?.(),
        );
    },
  }),
};
function mountPage() {
  return mount(AgentTeamsPage, { global: { stubs } });
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.route.query = {};
});

describe("AgentTeamsPage", () => {
  it("opens versioned assets without legacy migration controls", () => {
    const wrapper = mountPage();
    expect(wrapper.find(".workbench-legacy").exists()).toBe(false);
    expect(wrapper.find(".workflow-workspace-stub").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("迁移旧定义");
    expect(wrapper.find(".dashboard-title").text()).toBe("Agent 团队");
    // The workbench is not feature-complete yet, so the header must say so.
    expect(wrapper.find(".at-page-heading .chip-stub").text()).toBe("未完成");
  });
  it("opens an incoming chat run link while the assets panel is already mounted", async () => {
    const wrapper = mountPage();
    expect(wrapper.get(".workflow-workspace-stub").attributes("mode")).toBe(
      "assets",
    );
    mocks.route.query = { workflow_run: "chat-run" };
    await nextTick();
    expect(wrapper.get(".workflow-workspace-stub").attributes("mode")).toBe(
      "runs",
    );
    await wrapper
      .findAll(".workbench-navigation button")
      .find((button) => button.text() === "资产")!
      .trigger("click");
    mocks.route.query = { workflow_run: "chat-run" };
    await nextTick();
    expect(wrapper.get(".workflow-workspace-stub").attributes("mode")).toBe(
      "runs",
    );
  });
  it("opens reusable role templates independently of a team selection", async () => {
    const wrapper = mountPage();
    await wrapper
      .findAll(".tab-stub")
      .find((tab) => tab.text() === "角色模板")!
      .trigger("click");
    expect(wrapper.find(".role-templates-panel-stub").exists()).toBe(true);
    expect(wrapper.find(".agent-teams-panel-editor").exists()).toBe(false);
  });

  it("keeps the asset panel mounted without scheduling legacy polling", async () => {
    vi.useFakeTimers();
    const schedule = vi.spyOn(globalThis, "setInterval");
    const wrapper = mountPage();
    try {
      await wrapper
        .findAll(".tab-stub")
        .find((tab) => tab.text() === "团队")!
        .trigger("click");
      const workspace = wrapper.getComponent({ name: "WorkspaceStub" }).vm;
      await vi.advanceTimersByTimeAsync(9000);
      expect(wrapper.getComponent({ name: "WorkspaceStub" }).vm).toBe(
        workspace,
      );
      expect(schedule).not.toHaveBeenCalled();
    } finally {
      wrapper.unmount();
      schedule.mockRestore();
      vi.useRealTimers();
    }
  });
});
