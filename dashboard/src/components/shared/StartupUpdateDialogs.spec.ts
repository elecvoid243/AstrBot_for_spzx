// @vitest-environment happy-dom
import { config, shallowMount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import StartupUpdateDialogs from "./StartupUpdateDialogs.vue";

for (const name of [
  "v-dialog",
  "v-card",
  "v-card-title",
  "v-card-text",
  "v-card-actions",
  "v-icon",
  "v-chip",
  "v-progress-linear",
  "v-alert",
  "v-spacer",
  "v-btn",
  "v-checkbox",
])
  config.global.stubs[name] = true;

const mocks = vi.hoisted(() => ({
  check: vi.fn(),
  list: vi.fn(),
  update: vi.fn(),
  market: vi.fn(),
  start: vi.fn(),
  activeRuns: vi.fn(),
}));
vi.mock("@/api/v1", () => ({
  updatesApi: { check: mocks.check },
  pluginApi: { list: mocks.list, update: mocks.update },
  chatApi: { activeRuns: mocks.activeRuns },
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
    vi.useFakeTimers();
    localStorage.clear();
    mocks.start.mockResolvedValue(123);
    mocks.activeRuns.mockResolvedValue({
      data: { status: "ok", data: { running: false } },
    });
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

  afterEach(() => {
    vi.useRealTimers();
  });

  async function settle() {
    await vi.advanceTimersByTimeAsync(0);
  }

  async function passIdle() {
    await vi.advanceTimersByTimeAsync(30_000);
  }

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
    await settle();
    await passIdle();
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

  it("updates only selected plugins and remembers an explicit dismissal", async () => {
    mocks.check.mockResolvedValue({
      data: { status: "success", data: { has_new_version: false } },
    });
    mocks.update.mockResolvedValue({ data: { status: "ok" } });
    const wrapper = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    const vm = wrapper.vm as any;
    expect(vm.pluginsOpen).toBe(true);
    vm.selected = ["two"];
    await vm.updatePlugins();
    expect(mocks.update).toHaveBeenCalledTimes(1);
    expect(mocks.update).toHaveBeenCalledWith("two", expect.any(Object));
    wrapper.unmount();
    const next = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    expect((next.vm as any).pluginsOpen).toBe(true);
    (next.vm as any).closePlugins();
    next.unmount();
    const dismissed = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    expect((dismissed.vm as any).pluginsOpen).toBe(false);
    expect(mocks.check).toHaveBeenCalledTimes(3);
    dismissed.unmount();
    mocks.market.mockResolvedValue([
      { name: "one", market_plugin_id: "author/one", version: "3.0.0" },
      { name: "two", market_plugin_id: "author/two", version: "2.0.0" },
    ]);
    const newer = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    expect((newer.vm as any).plugins.map((item: any) => item.name)).toEqual([
      "one",
    ]);
    expect((newer.vm as any).pluginsOpen).toBe(true);
    newer.unmount();
  });

  it("remembers the closed core version independently from plugin updates", async () => {
    const first = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    (first.vm as any).closeCore();
    expect((first.vm as any).pluginsOpen).toBe(true);
    first.unmount();
    const next = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    expect((next.vm as any).coreOpen).toBe(false);
    expect((next.vm as any).pluginsOpen).toBe(true);
    next.unmount();
    mocks.check.mockResolvedValue({
      data: {
        status: "success",
        data: { has_new_version: true },
        message: "\nChanges\n\nVersion: v3.0.0 | Published at: today",
      },
    });
    const newer = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    expect((newer.vm as any).coreOpen).toBe(true);
    newer.unmount();
  });

  it("prompts for a newer release on a later dashboard entry in the same runtime", async () => {
    mocks.check.mockResolvedValue({
      data: { status: "success", data: { has_new_version: false } },
    });
    mocks.list.mockResolvedValue({ data: { status: "ok", data: [] } });
    const first = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    expect((first.vm as any).coreOpen).toBe(false);
    first.unmount();
    mocks.check.mockResolvedValue({
      data: {
        status: "success",
        data: { has_new_version: true },
        message: "\nChanges\n\nVersion: v2.0.0 | Published at: today",
      },
    });
    const next = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    expect((next.vm as any).coreOpen).toBe(true);
    next.unmount();
  });

  it("prompts for a dismissed version again after a backend restart", async () => {
    mocks.list.mockResolvedValue({ data: { status: "ok", data: [] } });
    const first = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    (first.vm as any).closeCore();
    expect((first.vm as any).coreOpen).toBe(false);
    first.unmount();

    const sameRuntime = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    expect((sameRuntime.vm as any).coreOpen).toBe(false);
    sameRuntime.unmount();

    mocks.start.mockResolvedValue(456);
    const restarted = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    expect((restarted.vm as any).coreOpen).toBe(true);
    restarted.unmount();
  });

  it("picks up a newer release in the same runtime after the current one was dismissed", async () => {
    mocks.list.mockResolvedValue({ data: { status: "ok", data: [] } });
    const wrapper = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    const vm = wrapper.vm as any;
    expect(vm.coreOpen).toBe(true);
    vm.closeCore();
    mocks.check.mockResolvedValue({
      data: {
        status: "success",
        data: { has_new_version: true },
        message: "\nChanges\n\nVersion: v3.0.0 | Published at: today",
      },
    });

    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(vm.coreOpen).toBe(true);
    expect(vm.release.version).toBe("v3.0.0");
    wrapper.unmount();
  });

  it("waits for the user to be idle before prompting", async () => {
    mocks.list.mockResolvedValue({ data: { status: "ok", data: [] } });
    const wrapper = shallowMount(StartupUpdateDialogs);
    await settle();
    const vm = wrapper.vm as any;
    expect(vm.coreOpen).toBe(false);

    await vi.advanceTimersByTimeAsync(29_000);
    expect(vm.coreOpen).toBe(false);

    window.dispatchEvent(new Event("pointerdown"));
    await vi.advanceTimersByTimeAsync(29_000);
    expect(vm.coreOpen).toBe(false);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(vm.coreOpen).toBe(true);
    wrapper.unmount();
  });

  it("holds the prompt while a chat run is active", async () => {
    mocks.list.mockResolvedValue({ data: { status: "ok", data: [] } });
    mocks.activeRuns.mockResolvedValue({
      data: { status: "ok", data: { running: true } },
    });
    const wrapper = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    const vm = wrapper.vm as any;
    expect(vm.coreOpen).toBe(false);

    mocks.activeRuns.mockResolvedValue({
      data: { status: "ok", data: { running: false } },
    });
    await vi.advanceTimersByTimeAsync(15_000);
    expect(vm.coreOpen).toBe(true);
    wrapper.unmount();
  });

  it("does not reopen a dismissed prompt when the tab regains focus", async () => {
    mocks.list.mockResolvedValue({ data: { status: "ok", data: [] } });
    const wrapper = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();
    const vm = wrapper.vm as any;
    expect(vm.coreOpen).toBe(true);
    vm.closeCore();

    document.dispatchEvent(new Event("visibilitychange"));
    await settle();

    expect(vm.coreOpen).toBe(false);
    wrapper.unmount();
  });

  it("keeps both dialogs open on an outside click", async () => {
    mocks.list.mockResolvedValue({ data: { status: "ok", data: [] } });
    const wrapper = shallowMount(StartupUpdateDialogs);
    await settle();
    await passIdle();

    const dialogs = wrapper.findAll("v-dialog-stub");
    expect(dialogs).toHaveLength(2);
    for (const dialog of dialogs) {
      // A bare boolean prop renders as an empty attribute; false renders "false".
      expect(dialog.attributes("persistent")).not.toBe("false");
    }
    wrapper.unmount();
  });
});
