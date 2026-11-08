// @vitest-environment happy-dom
import { config, flushPromises, shallowMount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import StartupUpdateDialogs from "./StartupUpdateDialogs.vue";

for (const name of [
  "v-dialog", "v-card", "v-card-title", "v-card-text", "v-card-actions",
  "v-icon", "v-chip", "v-progress-linear", "v-alert", "v-spacer", "v-btn", "v-checkbox",
]) config.global.stubs[name] = true;

const mocks = vi.hoisted(() => ({
  check: vi.fn(),
  list: vi.fn(),
  update: vi.fn(),
  market: vi.fn(),
  start: vi.fn(),
}));
vi.mock("@/api/v1", () => ({
  updatesApi: { check: mocks.check },
  pluginApi: { list: mocks.list, update: mocks.update },
}));
vi.mock("@/stores/common", () => ({
  useCommonStore: () => ({
    fetchStartTime: mocks.start,
    getPluginCollections: mocks.market,
  }),
}));
vi.mock("@/stores/auth", () => ({ useAuthStore: () => ({ role: "admin" }) }));
vi.mock("@/i18n/composables", () => ({
  useI18n: () => ({ locale: ref("zh-CN") }),
}));
vi.mock("@/utils/desktopRuntime", () => ({
  getDesktopRuntimeInfo: async () => ({ isDesktopRuntime: false }),
}));
vi.mock("markstream-vue", () => ({ MarkdownRender: { template: "<div />" } }));

describe("startup update dialogs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mocks.start.mockResolvedValue(123);
    mocks.check.mockResolvedValue({
      data: {
        status: "success",
        data: { has_new_version: true },
        message: "\nChanges\n\nVersion: v2.0.0 | Published at: today",
      },
    });
    mocks.list.mockResolvedValue({
      data: {
        status: "ok",
        data: ["one", "two"].map((name) => ({
          name,
          marketplace_name: name,
          version: "1.0.0",
          updates_enabled: true,
          install_source: {
            install_method: "market",
            registry_url: "https://registry.test",
            market_plugin_id: `author/${name}`,
          },
        })),
      },
    });
    mocks.market.mockResolvedValue(
      ["one", "two"].map((name) => ({
        name,
        market_plugin_id: `author/${name}`,
        version: "2.0.0",
      })),
    );
  });

  it("shows core first and updates plugins sequentially, preserving failures", async () => {
    let finishFirst: (value: unknown) => void = () => {};
    mocks.update
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockResolvedValueOnce({ data: { status: "error", message: "failed" } });
    const wrapper = shallowMount(StartupUpdateDialogs);
    await flushPromises();
    const vm = wrapper.vm as any;
    expect(vm.coreOpen).toBe(true);
    expect(vm.pluginsOpen).toBe(false);
    expect(vm.release.version).toBe("v2.0.0");
    vm.closeCore();
    expect(vm.pluginsOpen).toBe(true);
    const updating = vm.updatePlugins();
    expect(mocks.update).toHaveBeenCalledTimes(1);
    finishFirst({ data: { status: "ok" } });
    await updating;
    expect(mocks.update).toHaveBeenCalledTimes(2);
    expect(vm.plugins[0].updated).toBe(true);
    expect(vm.plugins[1].error).toBe("failed");
    expect(vm.selected).toEqual(["two"]);
    wrapper.unmount();
  });

  it("updates only selected plugins and prompts once per boot", async () => {
    mocks.check.mockResolvedValue({
      data: { status: "success", data: { has_new_version: false } },
    });
    mocks.update.mockResolvedValue({ data: { status: "ok" } });
    const wrapper = shallowMount(StartupUpdateDialogs);
    await flushPromises();
    const vm = wrapper.vm as any;
    expect(vm.pluginsOpen).toBe(true);
    vm.selected = ["two"];
    await vm.updatePlugins();
    expect(mocks.update).toHaveBeenCalledTimes(1);
    expect(mocks.update).toHaveBeenCalledWith("two", expect.any(Object));
    wrapper.unmount();
    const next = shallowMount(StartupUpdateDialogs);
    await flushPromises();
    expect((next.vm as any).pluginsOpen).toBe(false);
    expect(mocks.check).toHaveBeenCalledTimes(1);
    next.unmount();
  });
});
