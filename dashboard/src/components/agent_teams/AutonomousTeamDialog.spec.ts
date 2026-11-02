import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import AutonomousTeamDialog from "./AutonomousTeamDialog.vue";

const save = vi.hoisted(() => vi.fn());
vi.mock("@/api/workflowAssets", () => ({
  workflowAssetsApi: { save, list: vi.fn().mockResolvedValue({ assets: [] }) },
}));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

it("saves pinned coordinator and allowed templates with explicit team limits", async () => {
  save.mockResolvedValue({ asset_id: "team" });
  const wrapper = mount(AutonomousTeamDialog, {
    props: {
      modelValue: true,
      asset: null,
      catalog: {
        profiles: [],
        models: [],
        personas: [],
        tools: [],
        skills: [],
        knowledge_bases: [],
      },
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
        TeamPresetEditor: true,
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
    .findAll("button")
    .find((button) => button.text() === "autonomous.addPreset")!
    .trigger("click");
  await flushPromises();
  await wrapper.get('[data-testid="team-save"]').trigger("click");
  await flushPromises();
  expect(save).toHaveBeenCalledWith(null, {
    definition: expect.objectContaining({
      kind: "autonomous",
      name: "Research team",
      coordinator: expect.objectContaining({
        id: "coordinator",
        role: { name: "Coordinator" },
      }),
      allowed_roles: [
        expect.objectContaining({
          id: expect.any(String),
          role: { name: "Member" },
        }),
      ],
      required_inputs: ["goal"],
      max_parallel: 4,
      max_members: 8,
    }),
    expected_version: undefined,
  });
  expect(wrapper.emitted("saved")).toHaveLength(1);
});

it.each(["success", "failure"])(
  "ignores a late save %s from a previous asset editor",
  async (outcome) => {
    let finish!: (value: any) => void;
    let fail!: (reason: Error) => void;
    save.mockReset().mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          finish = resolve;
          fail = reject;
        }),
    );
    const asset = {
      asset_id: "team-a",
      version: 1,
      definition: {
        kind: "autonomous",
        name: "Team A",
        coordinator: {
          id: "coordinator",
          role_id: "writer",
          role_version: 1,
          overrides: {},
        },
        allowed_roles: [
          { id: "writer", role_id: "writer", role_version: 1, overrides: {} },
        ],
        required_inputs: ["goal"],
      },
    };
    const wrapper = mount(AutonomousTeamDialog, {
      props: {
        modelValue: true,
        asset,
        catalog: {
          profiles: [],
          models: [],
          personas: [],
          tools: [],
          skills: [],
          knowledge_bases: [],
        },
        roles: [
          {
            role_id: "writer",
            version: 1,
            versions: [1],
            definition: { name: "Writer" },
          },
        ],
      },
      global: {
        stubs: {
          ...Object.fromEntries(
            [
              "v-dialog",
              "v-card",
              "v-card-text",
              "v-card-actions",
              "v-alert",
            ].map((name) => [name, { template: "<div><slot /></div>" }]),
          ),
          AllowedWorkflowsPanel: true,
          TeamPresetEditor: true,
          "v-spacer": true,
          "v-select": true,
          "v-textarea": true,
          "v-text-field": {
            props: ["modelValue"],
            template: '<input :value="modelValue" />',
          },
          "v-btn": {
            props: ["disabled", "loading"],
            template:
              '<button :disabled="disabled || loading"><slot /></button>',
          },
        },
      },
    });
    await wrapper.get('[data-testid="team-save"]').trigger("click");
    const replacement = {
      ...asset,
      asset_id: "team-b",
      definition: { ...asset.definition, name: "Team B" },
    };
    await wrapper.setProps({ asset: replacement });
    if (outcome === "success") finish({ asset_id: "team-a", version: 2 });
    else fail(new Error("Old save failed"));
    await flushPromises();
    expect(
      wrapper.get<HTMLInputElement>('[data-testid="team-name"]').element.value,
    ).toBe("Team B");
    expect(wrapper.emitted("saved")).toBeUndefined();
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    expect(wrapper.text()).not.toContain("Old save failed");
    expect(
      wrapper.get('[data-testid="team-save"]').attributes("disabled"),
    ).toBeUndefined();
    wrapper.unmount();
  },
);
