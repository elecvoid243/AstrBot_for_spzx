import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import WorkflowVersionDialog from "./WorkflowVersionDialog.vue";

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/api/workflowAssets", () => ({ workflowAssetsApi: api }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

it("compares task order independently and holds both versions during restore", async () => {
  const first = [{ id: "a", name: "A" }, { id: "b", name: "B" }];
  api.get.mockImplementation((assetId: string, version?: number) => Promise.resolve({
    asset_id: assetId,
    version: version ?? 2,
    versions: [2, 1],
    definition: { name: "Report", tasks: version === 1 ? first : [...first].reverse() },
  }));
  let finish = () => {};
  const restoreVersion = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
  const slot = { template: "<div><slot /></div>" };
  const wrapper = mount(WorkflowVersionDialog, {
    props: { assetId: "report", blocked: false, restoreVersion },
    global: {
      stubs: {
        VDialog: slot, VCard: slot, VCardText: slot, VCardTitle: slot,
        VCardActions: slot, VAlert: slot, VSpacer: true, VProgressLinear: true,
        "v-select": { name: "VersionSelect", props: { disabled: Boolean }, template: '<select :disabled="disabled" />' },
        VBtn: { template: "<button><slot /></button>" },
      },
    },
  });
  try {
    await flushPromises();
    expect(wrapper.findAll("summary").map((item) => item.text())).toEqual(["reuse.taskOrder"]);
    expect(wrapper.findAll("pre").map((item) => JSON.parse(item.text()))).toEqual([
      ["a", "b"], ["b", "a"],
    ]);
    const restore = () => wrapper.findAll("button").find((item) => item.text() === "reuse.restore")!;
    await restore().trigger("click");
    await restore().trigger("click");
    expect(restoreVersion).toHaveBeenCalledWith(1, 2);
    expect(wrapper.findAllComponents({ name: "VersionSelect" }).map((item) => item.props("disabled"))).toEqual([true, true]);
    finish();
    await flushPromises();
    expect(wrapper.emitted("close")).toHaveLength(1);
  } finally {
    wrapper.unmount();
  }
});
