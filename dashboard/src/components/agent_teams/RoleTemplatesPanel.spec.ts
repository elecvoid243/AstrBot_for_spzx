import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { draftApi } from "@/composables/useAssetDraft";
import { mockDrafts } from "./draftTestMocks";
import RoleTemplatesPanel from "./RoleTemplatesPanel.vue";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  resources: vi.fn(),
  save: vi.fn(),
  preview: vi.fn(),
  get: vi.fn(),
  delete: vi.fn(),
}));
vi.mock("@/api/roleAssets", () => ({ roleAssetsApi: api }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
vi.mock("vuetify", () => ({ useDisplay: () => ({ mobile: false }) }));

const slotStub = { template: "<div><slot /></div>" };
const stubs = Object.fromEntries(
  [
    "v-card",
    "v-card-text",
    "v-card-actions",
    "v-alert",
    "v-chip",
    "v-expansion-panels",
    "v-expansion-panel",
    "v-expansion-panel-title",
    "v-expansion-panel-text",
    "v-card-title",
    "v-combobox",
    "v-spacer",
  ].map((name) => [name, slotStub]),
);
Object.assign(stubs, {
  "v-checkbox": {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
  },
  "v-dialog": {
    props: ["modelValue"],
    template: '<div v-if="modelValue"><slot /></div>',
  },
  "v-btn": {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  "v-text-field": {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  "v-textarea": {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  "v-select": {
    props: ["modelValue", "items", "multiple"],
    emits: ["update:modelValue"],
    template:
      '<select :value="modelValue" :multiple="multiple" @change="$emit(\'update:modelValue\', multiple ? Array.from($event.target.options).filter(option => option.selected).map(option => option.value) : $event.target.value)"><option v-for="item in items" :value="item.id ?? item.value ?? item">{{ item.name ?? item.title ?? item }}</option></select>',
  },
});

describe("reusable role configuration", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.resetAllMocks();
    mockDrafts((id, body) => api.save(id, body));
    api.list.mockResolvedValue({ roles: [] });
    api.resources.mockResolvedValue({
      profiles: [{ id: "default", name: "Default" }],
      models: [],
      personas: [],
      tools: [],
      skills: [],
      knowledge_bases: [],
    });
    api.preview.mockResolvedValue({
      ready: false,
      issues: [
        {
          path: "definition.provider_id",
          code: "MISSING",
          message: "Model is missing",
        },
      ],
      effective: { max_steps: 30 },
      sources: { max_steps: "profile:default" },
      export_content: { prompt: null },
    });
    api.save.mockResolvedValue({ role_id: "r1", version: 1 });
  });

  it("confirms deletion and refreshes the role directory", async () => {
    api.list
      .mockResolvedValueOnce({
        roles: [
          {
            role_id: "r1",
            version: 1,
            versions: [1],
            definition: { name: "Researcher" },
            installation: { profile_id: "default" },
          },
        ],
      })
      .mockResolvedValue({ roles: [] });
    api.delete.mockResolvedValue({});
    const panel = mount(RoleTemplatesPanel, { global: { stubs } });
    await flushPromises();
    await panel.get('[data-testid="delete-role"]').trigger("click");
    expect(panel.text()).toContain("deletion.impact");
    expect(api.delete).not.toHaveBeenCalled();
    await panel.get('[data-testid="confirm-asset-deletion"]').trigger("click");
    await flushPromises();
    expect(api.delete).toHaveBeenCalledWith("r1");
    expect(panel.text()).toContain("roles.empty");
    panel.unmount();
  });

  it("distinguishes unpublished and published roles with drafts", async () => {
    api.list.mockResolvedValue({
      roles: [
        {
          role_id: "published",
          version: 1,
          versions: [1],
          definition: { name: "Published" },
          installation: { profile_id: "default" },
        },
      ],
    });
    vi.mocked(draftApi.list).mockResolvedValueOnce([
      {
        asset_id: "new",
        revision: 1,
        base_version: null,
        body: {
          definition: { name: "New draft" },
          installation: { profile_id: "default" },
        },
      },
      {
        asset_id: "published",
        revision: 2,
        base_version: 1,
        body: { definition: { name: "Edited" } },
      },
    ]);
    const panel = mount(RoleTemplatesPanel, { global: { stubs } });
    await flushPromises();
    expect(panel.get('[data-testid="unpublished-draft"]').text()).toBe(
      "draft.unpublished",
    );
    expect(panel.get('[data-testid="editing-draft"]').text()).toBe(
      "draft.editing",
    );
    expect(panel.text()).toContain("roles.version 1");
    panel.unmount();
  });

  it("saves a reusable definition and shows field readiness without creating a member", async () => {
    const panel = mount(RoleTemplatesPanel, { global: { stubs } });
    await flushPromises();
    expect(panel.text()).toContain("roles.empty");
    await panel.get('[data-testid="new-role"]').trigger("click");
    await flushPromises();
    await panel.get('[data-testid="role-name"]').setValue("Researcher");
    await panel.get('[data-testid="role-prompt"]').setValue("Check evidence.");
    await panel.get('[data-testid="preview-role"]').trigger("click");
    await flushPromises();
    expect(panel.text()).toContain("definition.provider_id");
    expect(panel.text()).toContain("profile:default");
    await panel.get('[data-testid="save-role"]').trigger("click");
    await flushPromises();
    expect(api.save.mock.calls[0][0]).toBeNull();
    expect(api.save.mock.calls[0][1].definition).toMatchObject({
      name: "Researcher",
      prompt: "Check evidence.",
      provider_id: null,
    });
    expect(api.save.mock.calls[0][1].installation).toEqual({
      profile_id: "default",
    });
  });
  it("shows remote query capability and locates unsupported recovery requirements", async () => {
    api.preview.mockResolvedValue({
      ready: false,
      issues: [
        {
          path: "definition.runner_capabilities.0",
          code: "INCOMPATIBLE",
          message: "dify (workflow) does not support internal_checkpoint",
        },
      ],
      effective: {
        runner: {
          label: "dify (workflow)",
          recovery: "remote_query",
          capabilities: ["remote_query", "task_result"],
        },
      },
      sources: { runner: "profile:default" },
      export_content: { prompt: "Check evidence." },
    });
    const panel = mount(RoleTemplatesPanel, { global: { stubs } });
    await flushPromises();
    await panel.get('[data-testid="new-role"]').trigger("click");
    await flushPromises();
    await panel.get('[data-testid="preview-role"]').trigger("click");
    await flushPromises();
    expect(panel.get('[data-testid="runner-capabilities"]').text()).toContain(
      "runner.recovery.remote_query",
    );
    expect(panel.text()).toContain(
      "dify (workflow) does not support internal_checkpoint",
    );
    expect(
      panel
        .findAll("select")
        .some(
          (select) =>
            select.attributes("data-testid") === "runner-requirements",
        ),
    ).toBe(true);
    panel.unmount();
  });

  it.each([
    ["definition.name", '[data-testid="role-name"]'],
    ["definition.runner_capabilities.0", '[data-testid="runner-requirements"]'],
    ["definition.max_steps", 'input[label="roles.max_steps"]'],
  ])("focuses and reveals the control for %s", async (path, selector) => {
    api.save.mockRejectedValue({
      response: {
        data: {
          message: "Invalid role configuration",
          data: { fields: [{ path, message: "Invalid value" }] },
        },
      },
    });
    const panel = mount(RoleTemplatesPanel, {
      attachTo: document.body,
      global: { stubs },
    });
    try {
      await flushPromises();
      await panel.get('[data-testid="new-role"]').trigger("click");
      await flushPromises();
      await panel.get('[data-testid="save-role"]').trigger("click");
      await flushPromises();
      const control = panel.get(selector).element;
      const scroll = vi.fn();
      control.scrollIntoView = scroll;
      const link = panel.get<HTMLButtonElement>(".issue-link");
      link.element.focus();
      await link.trigger("click");
      await flushPromises();
      await vi.waitFor(() => expect(document.activeElement).toBe(control));
      expect(scroll).toHaveBeenCalledWith({
        block: "center",
        inline: "nearest",
      });
    } finally {
      panel.unmount();
    }
  });

  it("selects actual resources and saves a custom empty list", async () => {
    api.resources.mockResolvedValue({
      profiles: [{ id: "default", name: "Default" }],
      models: [{ id: "m", name: "Model" }],
      personas: [{ id: "p", name: "Persona" }],
      tools: [{ id: "tool", name: "Tool" }],
      skills: [{ id: "skill", name: "Skill" }],
      knowledge_bases: [{ id: "kb", name: "Knowledge" }],
    });
    const panel = mount(RoleTemplatesPanel, { global: { stubs } });
    await flushPromises();
    await panel.get('[data-testid="new-role"]').trigger("click");
    await flushPromises();
    await panel.get('[data-testid="role-name"]').setValue("Writer");
    await panel.get('[data-issue-path="definition.provider_id"]').setValue("m");
    await panel.get('[data-issue-path="definition.persona_id"]').setValue("p");
    const modes = panel
      .findAll("select")
      .filter((el) => el.attributes("aria-label")?.endsWith("roles.mode"));
    expect(modes).toHaveLength(3);
    for (const mode of modes) await mode.setValue("custom");
    const selections: Record<string, string[]> = {
      tools: ["tool"],
      skills: [],
      knowledge_bases: ["kb"],
    };
    for (const [field, value] of Object.entries(selections)) {
      const select = panel.get<HTMLSelectElement>(
        `[data-issue-path="definition.${field}"]`,
      );
      for (const option of select.element.options)
        option.selected = value.includes(option.value);
      await select.trigger("change");
    }
    await panel.get('[data-testid="save-role"]').trigger("click");
    await flushPromises();
    expect(api.save.mock.calls[0][1].definition).toMatchObject({
      provider_id: "m",
      persona_id: "p",
      tools: ["tool"],
      skills: [],
      knowledge_bases: ["kb"],
    });
    expect(api.save.mock.calls[0][1].installation).toEqual({
      profile_id: "default",
    });
    panel.unmount();
  });

  it("reopens an empty custom list without turning it into inheritance", async () => {
    api.list.mockResolvedValue({
      roles: [
        {
          role_id: "r",
          version: 1,
          versions: [1],
          definition: {
            name: "Saved",
            provider_id: "m",
            persona_id: null,
            tools: [],
            skills: null,
            knowledge_bases: null,
          },
          installation: { profile_id: "default" },
        },
      ],
    });
    const panel = mount(RoleTemplatesPanel, { global: { stubs } });
    await flushPromises();
    await panel
      .findAll("button")
      .find((button) => button.text() === "roles.edit")!
      .trigger("click");
    await flushPromises();
    expect(
      panel.get<HTMLSelectElement>(
        'select[aria-label="roles.tools: roles.mode"]',
      ).element.value,
    ).toBe("custom");
    await panel.get('[data-testid="save-role"]').trigger("click");
    await flushPromises();
    expect(api.save.mock.calls[0][1].definition).toMatchObject({
      tools: [],
      skills: null,
      knowledge_bases: null,
      persona_id: null,
    });
    expect(api.save.mock.calls[0][1].expected_version).toBe(1);
    panel.unmount();
  });

  it("waits for the expanding section before scrolling to its error", async () => {
    const panel = mount(RoleTemplatesPanel, {
      attachTo: document.body,
      global: {
        stubs: {
          ...stubs,
          "v-expansion-panel-text": {
            template: '<div class="v-expansion-panel-text"><slot /></div>',
          },
        },
      },
    });
    let finish!: () => void;
    const finished = new Promise<void>((resolve) => {
      finish = resolve;
    });
    try {
      await flushPromises();
      await panel.get('[data-testid="new-role"]').trigger("click");
      await flushPromises();
      await panel.get('[data-testid="preview-role"]').trigger("click");
      await flushPromises();
      const control = panel.get(
        'select[aria-label="roles.localModel"]',
      ).element;
      const animations = vi.fn(() => [{ finished }]);
      Object.defineProperty(
        control.closest(".v-expansion-panel-text"),
        "getAnimations",
        { value: animations },
      );
      const scroll = vi.fn();
      control.scrollIntoView = scroll;
      const link = panel.get<HTMLButtonElement>(".issue-link");
      link.element.focus();
      await link.trigger("click");
      await vi.waitFor(() => expect(animations).toHaveBeenCalled());
      expect(scroll).not.toHaveBeenCalled();
      expect(document.activeElement).toBe(link.element);
      finish();
      await vi.waitFor(() => expect(document.activeElement).toBe(control));
      expect(scroll).toHaveBeenCalledOnce();
    } finally {
      finish();
      panel.unmount();
    }
  });
});
