import { defineComponent, h } from "vue";
import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getPluginCollections: vi.fn(),
  list: vi.fn(),
  failed: vi.fn(),
  sources: vi.fn(),
  update: vi.fn(),
}));

vi.mock("@/api/v1", () => ({ pluginApi: mocks, commandApi: {} }));
vi.mock("@/stores/common", () => ({
  useCommonStore: () => ({
    getPluginCollections: mocks.getPluginCollections,
    astrbotVersion: "4.0.0",
  }),
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({ meta: { extensionTab: "market" }, hash: "", query: {} }),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { useExtensionPage } from "./useExtensionPage";

const customUrl = "http://registry.local/plugins.json";
const officialPlugin = {
  name: "official-plugin",
  repo: "https://github.com/example/plugin",
  version: "1.0.0",
  updates_enabled: true,
  install_source: { install_method: "market", registry_url: null },
};
let page: ReturnType<typeof useExtensionPage>;

beforeEach(() => {
  localStorage.setItem("selectedPluginSource", customUrl);
  mocks.list.mockResolvedValue({ data: { data: [{ ...officialPlugin }] } });
  mocks.failed.mockResolvedValue({ data: { data: {} } });
  mocks.sources.mockResolvedValue({
    data: {
      status: "ok",
      data: { sources: [{ name: "Local", url: customUrl }] },
    },
  });
  mocks.getPluginCollections.mockResolvedValue([]);
});

afterEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

it("does not query the default source for updates or source binding while a custom source is selected", async () => {
  const wrapper = mount(
    defineComponent({
      setup() {
        page = useExtensionPage("market");
        return () => h("div");
      },
    }),
  );
  await flushPromises();
  expect(mocks.getPluginCollections).toHaveBeenCalledWith(false, customUrl);
  expect(mocks.getPluginCollections).not.toHaveBeenCalledWith(false, null);
  await page.openPluginSourceBindingDialog(page.extension_data.data[0]);
  expect(mocks.getPluginCollections).not.toHaveBeenCalledWith(false, null);
  expect(
    page.sourceBindingDialog.candidates.every(
      (candidate: any) =>
        candidate.install_method !== "market" || candidate.registry_url,
    ),
  ).toBe(true);
  await page.updateExtension("official-plugin", true);
  expect(mocks.update).not.toHaveBeenCalled();
  page.removeCustomSource({ name: "Local", url: customUrl });
  expect(page.selectedSource.value).toBe(customUrl);
  expect(page.showRemoveSourceDialog.value).toBe(false);
  wrapper.unmount();
});

it("loads the default source again after explicitly selecting it", async () => {
  const wrapper = mount(
    defineComponent({
      setup() {
        page = useExtensionPage("market");
        return () => h("div");
      },
    }),
  );
  await flushPromises();
  page.selectPluginSource(null);
  await flushPromises();
  expect(mocks.getPluginCollections).toHaveBeenCalledWith(true, null);
  wrapper.unmount();
});

it("does not confirm an official-source update after switching to a custom source", async () => {
  const wrapper = mount(
    defineComponent({
      setup() {
        page = useExtensionPage("market");
        return () => h("div");
      },
    }),
  );
  await flushPromises();
  page.selectedSource.value = null;
  await page.updateExtension("official-plugin", true);
  expect(page.updateConfirmDialog.show).toBe(true);
  page.selectedSource.value = customUrl;
  await page.confirmUpdatePlugin();
  expect(mocks.update).not.toHaveBeenCalled();
  expect(page.updateConfirmDialog.show).toBe(false);
  wrapper.unmount();
});

it("checks other custom sources while skipping plugins from the default source", async () => {
  const otherUrl = "http://other.local/plugins.json";
  mocks.list.mockResolvedValue({
    data: {
      data: [
        { ...officialPlugin },
        {
          ...officialPlugin,
          name: "custom-plugin",
          install_source: {
            install_method: "market",
            registry_url: otherUrl,
            repo: officialPlugin.repo,
          },
        },
      ],
    },
  });
  mocks.getPluginCollections.mockImplementation(async (_force, url) =>
    url === otherUrl
      ? [{ name: "custom-plugin", repo: officialPlugin.repo, version: "2.0.0" }]
      : [],
  );
  const wrapper = mount(
    defineComponent({
      setup() {
        page = useExtensionPage("market");
        return () => h("div");
      },
    }),
  );
  await flushPromises();
  expect(mocks.getPluginCollections).toHaveBeenCalledWith(false, otherUrl);
  expect(mocks.getPluginCollections).not.toHaveBeenCalledWith(false, null);
  const extensions = page.extension_data.data as { has_update: boolean }[];
  expect(extensions[0].has_update).toBe(false);
  expect(extensions[1].has_update).toBe(true);
  wrapper.unmount();
});

it("discards a pending default-source response after switching to a custom source", async () => {
  localStorage.removeItem("selectedPluginSource");
  let resolveDefault!: (value: any[]) => void;
  mocks.getPluginCollections.mockImplementation((_force, url) =>
    url
      ? Promise.resolve([])
      : new Promise((resolve) => {
          resolveDefault = resolve;
        }),
  );
  const wrapper = mount(
    defineComponent({
      setup() {
        page = useExtensionPage("market");
        return () => h("div");
      },
    }),
  );
  await flushPromises();
  page.selectPluginSource(customUrl);
  await flushPromises();
  resolveDefault([{ name: "stale official plugin" }]);
  await flushPromises();
  expect(page.pluginMarketData.value).toEqual([]);
  expect(mocks.getPluginCollections).toHaveBeenCalledWith(true, customUrl);
  wrapper.unmount();
});
