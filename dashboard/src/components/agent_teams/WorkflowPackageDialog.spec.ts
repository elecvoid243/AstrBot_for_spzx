import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import WorkflowPackageDialog from "./WorkflowPackageDialog.vue";
const api = vi.hoisted(() => ({
  previewImport: vi.fn(),
  importPackage: vi.fn(),
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
  VFileInput: slot,
  RouterLink: slot,
  VTextarea: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: `<textarea :value="modelValue" @input="$emit('update:modelValue', $event.target.value)" />`,
  },
  VBtn: {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  VSelect: {
    props: ["modelValue", "items"],
    emits: ["update:modelValue"],
    template: `<select :value="modelValue" @change="$emit('update:modelValue', $event.target.value)"><option value="">Choose</option><option v-for="item in items" :value="item.id" :disabled="item.disabled">{{ item.name }}</option></select>`,
  },
};
describe("workflow package review", () => {
  it("requires explicit choices even for matching IDs and clears them when content changes", async () => {
    const portable = {
      asset_id: "root",
      version: 1,
      definition: { name: "Report" },
    };
    const requirement = {
      kind: "models",
      source_id: "same",
      display_name: "Source",
      selected: null,
      candidates: [
        { id: "same", name: "Local model", disabled: false, reasons: [] },
        {
          id: "bad",
          name: "Disabled",
          disabled: true,
          reasons: ["Resource is disabled"],
        },
      ],
    };
    api.previewImport.mockResolvedValueOnce({
      package: portable,
      requirements: [requirement],
      assets: [],
      effective: [],
      ready: false,
      issues: [],
    });
    api.previewImport.mockResolvedValueOnce({
      package: portable,
      requirements: [{ ...requirement, selected: "same" }],
      assets: [],
      effective: [],
      ready: true,
      issues: [],
    });
    api.importPackage.mockResolvedValueOnce({ existing: false });
    const wrapper = mount(WorkflowPackageDialog, {
      props: { modelValue: true },
      global: { stubs },
    });
    await wrapper.find("textarea").setValue(JSON.stringify(portable));
    await wrapper.get('[data-testid="preview-package"]').trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Resource is disabled");
    expect(
      wrapper.get('[data-testid="confirm-package"]').attributes("disabled"),
    ).toBeDefined();
    await wrapper.get("select").setValue("same");
    await flushPromises();
    const selections = [
      { kind: "models", source_id: "same", target_id: "same" },
    ];
    expect(api.previewImport).toHaveBeenLastCalledWith({
      package: portable,
      selections,
    });
    await wrapper.get('[data-testid="confirm-package"]').trigger("click");
    await flushPromises();
    expect(api.importPackage).toHaveBeenCalledWith({
      package: portable,
      selections,
    });
    expect(wrapper.emitted("installed")).toEqual([["workflows"]]);
    await wrapper.find("textarea").setValue('{"asset_id":"other"}');
    api.previewImport.mockResolvedValueOnce({
      package: { asset_id: "other", version: 1, definition: { name: "Other" } },
      requirements: [],
      assets: [],
      effective: [],
      ready: true,
      issues: [],
    });
    await wrapper.get('[data-testid="preview-package"]').trigger("click");
    await flushPromises();
    expect(api.previewImport).toHaveBeenLastCalledWith({
      package: { asset_id: "other" },
      selections: [],
    });
    wrapper.unmount();
  });
  it("discards an outdated preview when the package text is replaced", async () => {
    let resolve!: (value: any) => void;
    api.previewImport.mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const wrapper = mount(WorkflowPackageDialog, {
      props: { modelValue: true },
      global: { stubs },
    });
    await wrapper.find("textarea").setValue('{"asset_id":"old"}');
    await wrapper.get('[data-testid="preview-package"]').trigger("click");
    await wrapper.find("textarea").setValue('{"asset_id":"new"}');
    resolve({
      package: {
        asset_id: "old",
        version: 1,
        definition: { name: "Outdated" },
      },
      requirements: [],
      assets: [],
      effective: [],
      ready: true,
      issues: [],
    });
    await flushPromises();
    expect(wrapper.text()).not.toContain("Outdated");
    expect(wrapper.find('[data-testid="confirm-package"]').exists()).toBe(
      false,
    );
    wrapper.unmount();
  });
});
