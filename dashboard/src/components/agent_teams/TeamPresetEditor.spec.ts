import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import { createVuetify } from "vuetify";
import {
  VBtn,
  VExpansionPanels,
  VExpansionPanel,
  VExpansionPanelTitle,
  VExpansionPanelText,
} from "vuetify/components";
import TeamPresetEditor from "./TeamPresetEditor.vue";
const resolve = vi.hoisted(() => vi.fn());
vi.mock("@/api/roleAssets", () => ({
  roleAssetsApi: { resolve, preview: vi.fn() },
}));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
it("can remove a collapsed member preset without exposing deletion on the coordinator", async () => {
  const wrapper = mount(TeamPresetEditor, {
    props: {
      preset: { id: "member", role: { name: "Member" }, overrides: {} },
      title: "Member preset 1",
      removable: true,
      roles: [],
      catalog: {},
    },
    global: {
      plugins: [
        createVuetify({
          components: {
            VBtn,
            VExpansionPanels,
            VExpansionPanel,
            VExpansionPanelTitle,
            VExpansionPanelText,
          },
        }),
      ],
      stubs: {
        RoleResourcesEditor: true,
        "v-select": true,
        "v-text-field": true,
        "v-textarea": true,
        "v-switch": true,
        "v-alert": true,
      },
    },
  });
  try {
    await flushPromises();
    const toggle = wrapper.get(".v-expansion-panel-title");
    expect(toggle.attributes("aria-expanded")).toBe("false");
    expect(wrapper.get('[data-testid="remove-preset"]').isVisible()).toBe(true);
    await wrapper.get('[data-testid="remove-preset"]').trigger("click");
    expect(wrapper.emitted("remove")).toHaveLength(1);
    expect(toggle.attributes("aria-expanded")).toBe("false");
    await wrapper.setProps({ removable: false });
    expect(wrapper.find('[data-testid="remove-preset"]').exists()).toBe(false);
  } finally {
    wrapper.unmount();
  }
});

it("keeps template overrides local and passes explicit null to the resolver", async () => {
  resolve.mockResolvedValue({
    effective: {
      runner: { type: "local" },
      provider_id: "model",
      persona_id: null,
      tools: [],
    },
    sources: {},
    issues: [],
  });
  const preset = {
    id: "reviewer",
    role_id: "writer",
    role_version: 2,
    overrides: { persona_id: null, tools: [] },
  };
  const wrapper = mount(TeamPresetEditor, {
    props: {
      preset,
      title: "Reviewer",
      roles: [
        {
          role_id: "writer",
          version: 2,
          versions: [2, 1],
          definition: { name: "Writer" },
        },
      ],
      catalog: {
        profiles: [],
        models: [],
        personas: [],
        tools: [],
        skills: [],
        knowledge_bases: [],
      },
    },
    global: {
      stubs: {
        RoleResourcesEditor: true,
        "v-card": { template: "<div><slot /></div>" },
        "v-expansion-panels": { template: "<div><slot /></div>" },
        "v-expansion-panel": { template: "<div><slot /></div>" },
        "v-expansion-panel-title": { template: "<div><slot /></div>" },
        "v-expansion-panel-text": { template: "<div><slot /></div>" },
        "v-select": true,
        "v-switch": true,
        "v-textarea": true,
        "v-text-field": true,
        "v-spacer": true,
        "v-alert": true,
        "v-btn": true,
      },
    },
  });
  await flushPromises();
  expect(resolve).toHaveBeenCalledWith("writer", 2, {
    persona_id: null,
    tools: [],
  });
  expect(wrapper.text()).toContain("graph.effectiveRole");
  expect(preset.overrides).toEqual({ persona_id: null, tools: [] });
});

it.each([
  {
    override: {},
    firstModel: "template-model",
    expected: "template-model",
    calls: 1,
  },
  {
    override: { provider_id: null },
    firstModel: null,
    expected: "team-model",
    calls: 2,
  },
])(
  "summarizes the resolved pinned model with override $override",
  async ({ override, firstModel, expected, calls }) => {
    resolve
      .mockReset()
      .mockImplementation(async (_id, _version, overrides) => ({
        definition: { name: "Pinned name" },
        effective: {
          runner: { type: "local" },
          provider_id:
            overrides.provider_id === "team-model" ? "team-model" : firstModel,
          tools: [],
        },
        sources: {},
        issues: [],
      }));
    const wrapper = mount(TeamPresetEditor, {
      props: {
        preset: {
          id: "member",
          role_id: "writer",
          role_version: 1,
          overrides: override,
        },
        title: "Member",
        defaultProvider: "team-model",
        roles: [
          {
            role_id: "writer",
            version: 2,
            versions: [2, 1],
            definition: { name: "Latest name" },
          },
        ],
        catalog: {
          profiles: [],
          models: [],
          personas: [],
          tools: [],
          skills: [],
          knowledge_bases: [],
        },
      },
      global: {
        stubs: {
          RoleResourcesEditor: true,
          "v-expansion-panels": { template: "<div><slot /></div>" },
          "v-expansion-panel": { template: "<div><slot /></div>" },
          "v-expansion-panel-title": { template: "<div><slot /></div>" },
          "v-expansion-panel-text": { template: "<div><slot /></div>" },
          "v-select": true,
          "v-switch": true,
          "v-textarea": true,
          "v-text-field": true,
          "v-alert": true,
          "v-btn": true,
        },
      },
    });
    await flushPromises();
    expect(wrapper.get('[data-testid="preset-name"]').text()).toContain(
      "Pinned name",
    );
    expect(wrapper.get('[data-testid="preset-name"]').text()).not.toContain(
      "Latest name",
    );
    expect(wrapper.get('[data-testid="preset-model"]').text()).toContain(
      expected,
    );
    expect(resolve).toHaveBeenCalledTimes(calls);
  },
);

it("shows an actionable error and retries a failed preview", async () => {
  resolve
    .mockReset()
    .mockRejectedValueOnce(new Error("network"))
    .mockResolvedValueOnce({
      definition: { name: "Writer" },
      effective: { runner: { type: "local" }, provider_id: "model", tools: [] },
      sources: {},
      issues: [],
    });
  const wrapper = mount(TeamPresetEditor, {
    props: {
      preset: {
        id: "writer",
        role_id: "writer",
        role_version: 1,
        overrides: {},
      },
      title: "Member",
      roles: [],
      catalog: {
        profiles: [],
        models: [],
        personas: [],
        tools: [],
        skills: [],
        knowledge_bases: [],
      },
    },
    global: {
      stubs: {
        RoleResourcesEditor: true,
        "v-expansion-panels": { template: "<div><slot /></div>" },
        "v-expansion-panel": { template: "<div><slot /></div>" },
        "v-expansion-panel-title": { template: "<div><slot /></div>" },
        "v-expansion-panel-text": { template: "<div><slot /></div>" },
        "v-alert": { template: "<div><slot /></div>" },
        "v-btn": { template: "<button><slot /></button>" },
        "v-select": true,
        "v-switch": true,
        "v-textarea": true,
        "v-text-field": true,
      },
    },
  });
  await flushPromises();
  expect(wrapper.get('[data-testid="preset-model"]').text()).toContain(
    "roles.previewFailed",
  );
  expect(wrapper.get('[data-testid="preset-preview-error"]').text()).toContain(
    "roles.previewFailed",
  );
  await wrapper.get('[data-testid="preset-preview-retry"]').trigger("click");
  await flushPromises();
  expect(wrapper.find('[data-testid="preset-preview-error"]').exists()).toBe(
    false,
  );
  expect(wrapper.get('[data-testid="preset-model"]').text()).toContain("model");
  expect(resolve).toHaveBeenCalledTimes(2);
});

it("ignores a stale failure after a newer preview succeeds", async () => {
  let rejectOld!: (reason: Error) => void;
  resolve
    .mockReset()
    .mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectOld = reject;
        }),
    )
    .mockResolvedValueOnce({
      definition: { name: "New version" },
      effective: {
        runner: { type: "local" },
        provider_id: "new-model",
        tools: [],
      },
      sources: {},
      issues: [],
    });
  const wrapper = mount(TeamPresetEditor, {
    props: {
      preset: {
        id: "writer",
        role_id: "writer",
        role_version: 1,
        overrides: {},
      },
      title: "Member",
      roles: [],
      catalog: {
        profiles: [],
        models: [],
        personas: [],
        tools: [],
        skills: [],
        knowledge_bases: [],
      },
    },
    global: {
      stubs: {
        RoleResourcesEditor: true,
        "v-expansion-panels": { template: "<div><slot /></div>" },
        "v-expansion-panel": { template: "<div><slot /></div>" },
        "v-expansion-panel-title": { template: "<div><slot /></div>" },
        "v-expansion-panel-text": { template: "<div><slot /></div>" },
        "v-select": true,
        "v-switch": true,
        "v-textarea": true,
        "v-text-field": true,
        "v-alert": true,
        "v-btn": true,
      },
    },
  });
  await wrapper.setProps({
    preset: { id: "writer", role_id: "writer", role_version: 2, overrides: {} },
  });
  await flushPromises();
  rejectOld(new Error("old failure"));
  await flushPromises();
  expect(wrapper.get('[data-testid="preset-name"]').text()).toContain(
    "New version",
  );
  expect(wrapper.get('[data-testid="preset-model"]').text()).toContain(
    "new-model",
  );
  expect(wrapper.find('[data-testid="preset-preview-error"]').exists()).toBe(
    false,
  );
});
