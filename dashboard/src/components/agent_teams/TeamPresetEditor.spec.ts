import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import TeamPresetEditor from "./TeamPresetEditor.vue";
const resolve = vi.hoisted(() => vi.fn());
vi.mock("@/api/roleAssets", () => ({
  roleAssetsApi: { resolve, preview: vi.fn() },
}));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
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
