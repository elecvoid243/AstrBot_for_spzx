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
  VTextarea: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  VBtn: {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  VSelect: {
    props: ["modelValue", "items"],
    emits: ["update:modelValue"],
    template:
      '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="item in items" :value="item.id">{{ item.name }}</option></select>',
  },
  RouterLink: slot,
};

describe("workflow package review", () => {
  it("discards a stale preview after the user replaces the package", async () => {
    let resolve!: (value: any) => void;
    api.previewImport.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const wrapper = mount(WorkflowPackageDialog, {
      props: { modelValue: true },
      global: { stubs },
    });
    await wrapper.find("textarea").setValue('{"asset_id":"first"}');
    await wrapper.get('[data-testid="preview-package"]').trigger("click");
    await wrapper.find("textarea").setValue('{"asset_id":"second"}');
    resolve({
      package: { definition: { name: "Old package" }, version: 1 },
      roles: [],
      ready: true,
      issues: [],
    });
    await flushPromises();
    expect(wrapper.text()).not.toContain("Old package");
    expect(wrapper.find('[data-testid="confirm-package"]').exists()).toBe(
      false,
    );
  });

  it("requires review of ambiguity and confirms one installation with the displayed choices", async () => {
    const portable = {
      definition: { name: "Imported workflow" },
      asset_id: "source",
      version: 1,
    };
    const role = {
      key: "writer:1",
      name: "Writer",
      prompt: "Actual prompt",
      prompt_source: "role",
      installation: { profile_id: "local", bindings: {} },
      dependencies: [
        {
          slot: "model",
          kind: "models",
          capabilities: [],
          status: "ambiguous",
          selected: null,
          candidates: [{ id: "model-b", name: "Local model" }],
        },
      ],
    };
    api.previewImport.mockResolvedValueOnce({
      package: portable,
      roles: [role],
      ready: false,
      issues: [{ path: "writer.model", message: "Choose model" }],
    });
    api.previewImport.mockResolvedValueOnce({
      package: portable,
      roles: [
        {
          ...role,
          installation: { profile_id: "local", bindings: { model: "model-b" } },
          dependencies: [
            { ...role.dependencies[0], status: "matched", selected: "model-b" },
          ],
        },
      ],
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
    expect(wrapper.text()).toContain("Actual prompt");
    expect(
      wrapper.get('[data-testid="confirm-package"]').attributes("disabled"),
    ).toBeDefined();
    await wrapper.find("select").setValue("model-b");
    await flushPromises();
    await wrapper.get('[data-testid="confirm-package"]').trigger("click");
    await flushPromises();
    expect(api.importPackage).toHaveBeenCalledWith({
      package: portable,
      installations: {
        "writer:1": { profile_id: "local", bindings: { model: "model-b" } },
      },
      save_pending: false,
    });
    expect(wrapper.emitted("installed")).toHaveLength(1);
  });
});
