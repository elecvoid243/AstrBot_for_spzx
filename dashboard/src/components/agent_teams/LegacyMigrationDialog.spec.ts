import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LegacyMigrationDialog from "./LegacyMigrationDialog.vue";

const api = vi.hoisted(() => ({ preview: vi.fn(), confirm: vi.fn() }));
vi.mock("@/api/legacyMigration", () => ({ legacyMigrationApi: api }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
const slot = { template: "<div><slot /></div>" };
const stubs = {
  VCard: slot,
  VCardTitle: slot,
  VCardText: slot,
  VCardActions: slot,
  VAlert: slot,
  VSpacer: slot,
  VDialog: {
    props: ["modelValue"],
    template: '<div v-if="modelValue"><slot /></div>',
  },
  VBtn: {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  VSelect: {
    props: ["modelValue", "items"],
    emits: ["update:modelValue"],
    template:
      '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="item in items" :value="item.team_id">{{item.name}}</option></select>',
  },
  VCheckbox: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
  },
};
const proposal = {
  preview_token: "reviewed-token",
  ready: false,
  active_runs: [{ run_id: "old-active", status: "running" }],
  roles: [
    {
      role_id: "role",
      definition: { name: "Evidence writer" },
      effective: { provider_id: "missing-model" },
      sources: {},
      installation: {},
    },
  ],
  assets: [
    {
      source_id: "old",
      asset_id: "new",
      kind: "workflow",
      definition: { name: "Old report" },
    },
  ],
  mapping: { workflows: { old: { asset_id: "new", version: 1 } } },
  issues: [
    {
      path: "model",
      code: "MISSING",
      message: "Choose the original compatible model",
    },
  ],
};

describe("legacy definition migration", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("requires explicit pending review, confirms the displayed token once and opens migrated assets", async () => {
    api.preview.mockResolvedValue(proposal);
    api.confirm.mockResolvedValue({
      existing: false,
      mapping: proposal.mapping,
    });
    const wrapper = mount(LegacyMigrationDialog, {
      props: { teams: [{ team_id: "a", name: "Legacy team" }] },
      global: { stubs },
    });
    await wrapper.get('[data-testid="open-migration"]').trigger("click");
    await wrapper.get("select").setValue("a");
    await wrapper.get('[data-testid="preview-migration"]').trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("old-active");
    expect(wrapper.text()).toContain("Choose the original compatible model");
    expect(
      wrapper.get('[data-testid="confirm-migration"]').attributes("disabled"),
    ).toBeDefined();
    await wrapper.get('input[type="checkbox"]').setValue(true);
    await wrapper.get('[data-testid="confirm-migration"]').trigger("click");
    await flushPromises();
    expect(api.confirm).toHaveBeenCalledTimes(1);
    expect(api.confirm).toHaveBeenCalledWith("a", {
      preview_token: "reviewed-token",
      save_pending: true,
    });
    expect(wrapper.find('[data-testid="confirm-migration"]').exists()).toBe(
      false,
    );
    await wrapper.get('[data-testid="open-migrated-assets"]').trigger("click");
    expect(wrapper.emitted("migrated")).toEqual([[]]);
    wrapper.unmount();
  });

  it("does not confirm a late preview for a different selected team", async () => {
    let release: (value: unknown) => void = () => {};
    api.preview.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    const wrapper = mount(LegacyMigrationDialog, {
      props: {
        teams: [
          { team_id: "a", name: "A" },
          { team_id: "b", name: "B" },
        ],
      },
      global: { stubs },
    });
    await wrapper.get('[data-testid="open-migration"]').trigger("click");
    await wrapper.get("select").setValue("a");
    await wrapper.get('[data-testid="preview-migration"]').trigger("click");
    await wrapper.get("select").setValue("b");
    release(proposal);
    await flushPromises();
    expect(wrapper.find('[data-testid="migration-readiness"]').exists()).toBe(
      false,
    );
    expect(
      wrapper.get('[data-testid="confirm-migration"]').attributes("disabled"),
    ).toBeDefined();
    expect(api.confirm).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
