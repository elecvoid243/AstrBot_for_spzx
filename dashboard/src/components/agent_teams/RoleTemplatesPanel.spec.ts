import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RoleTemplatesPanel from "./RoleTemplatesPanel.vue";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  resources: vi.fn(),
  save: vi.fn(),
  preview: vi.fn(),
  get: vi.fn(),
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
    props: ["modelValue", "items"],
    emits: ["update:modelValue"],
    template:
      '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="item in items" :value="item.id ?? item.value ?? item">{{ item.name ?? item.title ?? item }}</option></select>',
  },
});

describe("reusable role configuration", () => {
  it("requires review before resolving a migrated role in a new version", async () => {
    api.list.mockResolvedValue({
      roles: [
        {
          role_id: "migrated",
          version: 1,
          versions: [1],
          definition: {
            name: "Migrated writer",
            prompt: "Write evidence.",
            model_slot: "model",
            migration: {
              issues: [
                {
                  path: "runner_config.legacy_exit",
                  code: "UNMAPPABLE",
                  message: "Review previous exit behavior",
                },
              ],
              source: { execution: { legacy_exit: true } },
            },
          },
          installation: { profile_id: "default", bindings: { model: "model" } },
        },
      ],
    });
    const panel = mount(RoleTemplatesPanel, { global: { stubs } });
    await flushPromises();
    await panel
      .findAll("button")
      .find((button) => button.text() === "roles.edit")!
      .trigger("click");
    expect(panel.text()).toContain("Review previous exit behavior");
    expect(
      panel.get('[data-testid="save-role"]').attributes("disabled"),
    ).toBeDefined();
    await panel.get('input[type="checkbox"]').setValue(true);
    await panel.get('[data-testid="save-role"]').trigger("click");
    await flushPromises();
    expect(api.save).toHaveBeenCalledTimes(1);
    expect(api.save.mock.calls[0][0]).toBe("migrated");
    expect(api.save.mock.calls[0][1].expected_version).toBe(1);
    expect(api.save.mock.calls[0][1].definition).not.toHaveProperty(
      "migration",
    );
    panel.unmount();
  });
  beforeEach(() => {
    vi.resetAllMocks();
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
          path: "installation.bindings.model",
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

  it("saves a reusable definition and shows field readiness without creating a member", async () => {
    const panel = mount(RoleTemplatesPanel, { global: { stubs } });
    await flushPromises();
    expect(panel.text()).toContain("roles.empty");
    await panel.get('[data-testid="new-role"]').trigger("click");
    await panel.get('[data-testid="role-name"]').setValue("Researcher");
    await panel.get('[data-testid="role-prompt"]').setValue("Check evidence.");
    await panel.get('[data-testid="preview-role"]').trigger("click");
    await flushPromises();
    expect(panel.text()).toContain("installation.bindings.model");
    expect(panel.text()).toContain("profile:default");
    await panel.get('[data-testid="save-role"]').trigger("click");
    await flushPromises();
    expect(api.save.mock.calls[0][0]).toBeNull();
    expect(api.save.mock.calls[0][1].definition).toMatchObject({
      name: "Researcher",
      prompt: "Check evidence.",
      model_slot: "model",
    });
    expect(api.save.mock.calls[0][1].installation).toEqual({
      profile_id: "default",
      bindings: {},
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

  it("locates the exact resource binding when logical slots share a dotted prefix", async () => {
    api.list.mockResolvedValue({
      roles: [
        {
          role_id: "writer",
          version: 1,
          versions: [1],
          definition: {
            name: "Writer",
            prompt: "Write evidence.",
            model_slot: "shared",
            tools: ["shared.tool"],
          },
          installation: { profile_id: "default", bindings: {} },
        },
      ],
    });
    api.save.mockRejectedValue({
      response: {
        data: {
          message: "Invalid role configuration",
          data: {
            fields: [
              {
                path: "installation.bindings.shared.tool",
                message: "Missing tool",
              },
            ],
          },
        },
      },
    });
    const panel = mount(RoleTemplatesPanel, {
      attachTo: document.body,
      global: { stubs },
    });
    try {
      await flushPromises();
      await panel
        .findAll("button")
        .find((button) => button.text() === "roles.edit")!
        .trigger("click");
      await panel.get('[data-testid="save-role"]').trigger("click");
      await flushPromises();
      const control = panel.get(
        'select[aria-label="roles.binding: shared.tool"]',
      ).element;
      const scroll = vi.fn();
      control.scrollIntoView = scroll;
      const link = panel.get<HTMLButtonElement>(".issue-link");
      link.element.focus();
      await link.trigger("click");
      await flushPromises();
      await vi.waitFor(() => expect(document.activeElement).toBe(control));
      expect(scroll).toHaveBeenCalledOnce();
    } finally {
      panel.unmount();
    }
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
