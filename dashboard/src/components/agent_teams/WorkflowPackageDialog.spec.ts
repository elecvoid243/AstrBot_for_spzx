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
  it.each(["teams", "workflows"] as const)(
    "returns the %s directory after installing a package",
    async (directory) => {
      const portable = {
        asset_id: "entry",
        version: 1,
        definition: {
          name: "Imported entry",
          ...(directory === "teams" ? { kind: "autonomous" } : {}),
        },
      };
      api.previewImport.mockResolvedValueOnce({
        package: portable,
        roles: [],
        assets: [],
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
      await wrapper.get('[data-testid="confirm-package"]').trigger("click");
      await flushPromises();
      expect(wrapper.emitted("installed")).toEqual([[directory]]);
      wrapper.unmount();
    },
  );
  it("keeps reviewed slots with local choices and resets them for edited content", async () => {
    const raw = {
      asset_id: "source",
      version: 1,
      definition: {
        name: "Custom slots",
        overrides: { tools: ["custom-search-slot"] },
      },
    };
    const normalized = {
      ...raw,
      definition: { name: "Custom slots", overrides: { tools: ["tools_1"] } },
    };
    const role = {
      key: "writer:1",
      name: "Writer",
      prompt: "Read evidence",
      prompt_source: "role",
      installation: { profile_id: "local", bindings: {} },
      dependencies: [
        {
          slot: "tools_1",
          kind: "tools",
          capabilities: [],
          status: "confirmation_required",
          selected: null,
          candidates: [{ id: "search-b", name: "Reviewed local search" }],
        },
      ],
    };
    api.previewImport.mockResolvedValueOnce({
      package: normalized,
      roles: [role],
      ready: false,
      issues: [],
      assets: [],
    });
    api.previewImport.mockResolvedValueOnce({
      package: normalized,
      roles: [
        {
          ...role,
          installation: {
            profile_id: "local",
            bindings: { tools_1: "search-b" },
          },
        },
      ],
      ready: true,
      issues: [],
      assets: [],
    });
    const wrapper = mount(WorkflowPackageDialog, {
      props: { modelValue: true },
      global: { stubs },
    });
    await wrapper.find("textarea").setValue(JSON.stringify(raw));
    await wrapper.get('[data-testid="preview-package"]').trigger("click");
    await flushPromises();
    await wrapper.find("select").setValue("search-b");
    await flushPromises();
    expect(api.previewImport).toHaveBeenLastCalledWith({
      package: normalized,
      installations: {
        "writer:1": { profile_id: "local", bindings: { tools_1: "search-b" } },
      },
    });
    const edited = { ...raw, asset_id: "edited" };
    api.previewImport.mockResolvedValueOnce({
      package: edited,
      roles: [],
      assets: [],
      ready: true,
      issues: [],
    });
    await wrapper.find("textarea").setValue(JSON.stringify(edited));
    await wrapper.get('[data-testid="preview-package"]').trigger("click");
    await flushPromises();
    expect(api.previewImport).toHaveBeenLastCalledWith({
      package: edited,
      installations: {},
    });
  });
  it("shows each exact nested version and the reuse or upgrade action before confirmation", async () => {
    api.previewImport.mockResolvedValueOnce({
      package: { asset_id: "root", version: 2, definition: { name: "Report" } },
      assets: [
        { asset_id: "root", version: 2, name: "Report", action: "new_version" },
        {
          asset_id: "leaf",
          version: 7,
          name: "Pinned evidence",
          action: "reuse",
        },
      ],
      roles: [],
      ready: true,
      issues: [],
    });
    const wrapper = mount(WorkflowPackageDialog, {
      props: { modelValue: true },
      global: { stubs },
    });
    await wrapper.find("textarea").setValue('{"asset_id":"root"}');
    await wrapper.get('[data-testid="preview-package"]').trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Pinned evidence · v7");
    expect(wrapper.text()).toContain("package.actions.new_version");
    expect(wrapper.text()).toContain("package.actions.reuse");
  });
  it("shows typed HTTP format validation fields without offering installation", async () => {
    api.previewImport.mockRejectedValueOnce({
      response: {
        data: {
          detail: [
            {
              loc: ["body", "package", "format_version"],
              msg: "Package format_version must be an integer",
            },
          ],
        },
      },
    });
    const wrapper = mount(WorkflowPackageDialog, {
      props: { modelValue: true },
      global: { stubs },
    });
    await wrapper.find("textarea").setValue('{"format_version":true}');
    await wrapper.get('[data-testid="preview-package"]').trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("package.format_version");
    expect(wrapper.text()).toContain("must be an integer");
    expect(wrapper.find('[data-testid="confirm-package"]').exists()).toBe(
      false,
    );
  });

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
          logical_slots: ["financial-reasoning"],
          description: "Analyze company filings",
          input_schema: { type: "object", required: ["company"] },
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
    expect(wrapper.text()).toContain("financial-reasoning");
    expect(wrapper.text()).toContain("Analyze company filings");
    expect(wrapper.text()).toContain('"company"');
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
