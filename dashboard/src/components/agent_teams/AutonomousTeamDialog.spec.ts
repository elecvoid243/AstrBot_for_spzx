import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import AutonomousTeamDialog from "./AutonomousTeamDialog.vue";

const save = vi.hoisted(() => vi.fn());
vi.mock("@/api/workflowAssets", () => ({ workflowAssetsApi: { save } }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

it("saves pinned coordinator and allowed templates with explicit team limits", async () => {
  save.mockResolvedValue({ asset_id: "team" });
  const wrapper = mount(AutonomousTeamDialog, {
    props: {
      modelValue: true,
      asset: null,
      roles: [
        {
          role_id: "coordinator",
          version: 2,
          versions: [2, 1],
          definition: { name: "Coordinator" },
        },
        {
          role_id: "writer",
          version: 3,
          versions: [3, 2, 1],
          definition: { name: "Writer" },
        },
      ],
    },
    global: {
      stubs: {
        "v-dialog": { template: "<div><slot /></div>" },
        "v-card": { template: "<div><slot /></div>" },
        "v-card-text": { template: "<div><slot /></div>" },
        "v-card-actions": { template: "<div><slot /></div>" },
        "v-alert": { template: "<div><slot /></div>" },
        "v-spacer": true,
        "v-text-field": {
          props: ["modelValue"],
          emits: ["update:modelValue"],
          template:
            '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
        },
        "v-textarea": true,
        "v-select": {
          props: ["modelValue", "items"],
          emits: ["update:modelValue"],
          template: "<select />",
        },
        "v-btn": {
          props: ["disabled", "loading"],
          template: '<button :disabled="disabled || loading"><slot /></button>',
        },
      },
    },
  });
  await wrapper.get('[data-testid="team-name"]').setValue("Research team");
  await wrapper
    .getComponent<any>('[data-testid="team-coordinator"]')
    .vm.$emit("update:modelValue", "coordinator");
  await wrapper
    .getComponent<any>('[data-testid="team-roles"]')
    .vm.$emit("update:modelValue", ["writer"]);
  await flushPromises();
  await wrapper.get('[data-testid="team-save"]').trigger("click");
  await flushPromises();
  expect(save).toHaveBeenCalledWith(null, {
    definition: expect.objectContaining({
      kind: "autonomous",
      name: "Research team",
      coordinator: { role_id: "coordinator", role_version: 2, overrides: {} },
      allowed_roles: [{ role_id: "writer", role_version: 3, overrides: {} }],
      required_inputs: ["goal"],
      max_parallel: 4,
      max_members: 8,
    }),
    expected_version: undefined,
  });
  expect(wrapper.emitted("saved")).toHaveLength(1);
});
