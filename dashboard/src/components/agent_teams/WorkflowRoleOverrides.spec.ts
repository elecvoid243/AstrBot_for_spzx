import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";

const api = vi.hoisted(() => ({ resolve: vi.fn(), save: vi.fn() }));
vi.mock("@/api/roleAssets", () => ({ roleAssetsApi: api }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

const stubs = {
  TeamsFlowCanvas: {
    name: "TeamsFlowCanvas",
    emits: ["select-node"],
    template: "<div />",
  },
  WorkflowLoopPanel: true,
  "v-icon": true,
  "v-chip": true,
  "v-spacer": true,
  "v-btn": { template: "<button><slot /></button>" },
  "v-alert": { template: "<div><slot /></div>" },
  "v-text-field": true,
  "v-textarea": {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: `<textarea :value="modelValue" @input="$emit('update:modelValue', $event.target.value)" />`,
  },
  "v-switch": {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: `<input type="checkbox" :checked="modelValue" @change="$emit('update:modelValue', $event.target.checked)" />`,
  },
  "v-select": {
    props: ["modelValue", "items", "multiple"],
    emits: ["update:modelValue"],
    template: `<select :value="modelValue" @change="$emit('update:modelValue', $event.target.value)"><option v-for="item in items" :value="item.value ?? item.id ?? item">{{ item.title ?? item.name ?? item }}</option></select>`,
  },
  "v-expansion-panels": { template: "<div><slot /></div>" },
  "v-expansion-panel": { template: "<div><slot /></div>" },
  "v-expansion-panel-title": { template: "<div><slot /></div>" },
  "v-expansion-panel-text": { template: "<div><slot /></div>" },
};

const result = (prompt: string, provider: string | null = "model-a") => ({
  effective: {
    runner: { type: "local" },
    prompt,
    provider_id: provider,
    persona_id: null,
    tools: [],
    skills: [],
    knowledge_bases: [],
  },
  sources: {
    prompt: "role",
    provider_id: "role",
    persona_id: "task",
    tools: "task",
    skills: "role",
    knowledge_bases: "role",
  },
  issues: [],
});

beforeEach(() => {
  vi.clearAllMocks();
  api.resolve.mockImplementation(async (_role, version, overrides) =>
    result(`v${version}`, overrides.provider_id ?? null),
  );
});

describe("role template inspector", () => {
  it("previews current edits, ignores late versions, and preserves overrides on upgrade and undo", async () => {
    let finishOld!: (value: unknown) => void;
    api.resolve.mockImplementation((_role, version, overrides) => {
      if (version === 1)
        return new Promise((resolve) => {
          finishOld = resolve;
        });
      return Promise.resolve(
        result("new template", overrides.provider_id ?? null),
      );
    });
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition: {
          name: "Flow",
          default_provider_id: "model-a",
          tasks: [
            {
              id: "a",
              name: "Agent",
              member: "a",
              task: "Task",
              role_id: "r",
              role_version: 1,
              overrides: { persona_id: null, tools: [] },
            },
          ],
          edges: [],
        },
        roles: [
          {
            role_id: "r",
            version: 2,
            versions: [1, 2],
            definition: { name: "Role" },
          },
        ],
      },
      global: { stubs },
    });
    try {
      wrapper
        .getComponent({ name: "TeamsFlowCanvas" })
        .vm.$emit("select-node", "a");
      await flushPromises();
      await wrapper
        .findAll("button")
        .find((b) => b.text() === "graph.upgradeRole")!
        .trigger("click");
      await flushPromises();
      expect(
        wrapper.get('[data-testid="role-preview-prompt"]').text(),
      ).toContain("new template");
      expect(
        wrapper.get('[data-testid="role-preview-provider_id"]').text(),
      ).toContain("model-a");
      expect(
        wrapper.get('[data-testid="role-preview-provider_id"]').text(),
      ).toContain("graph.workflowDefault");
      finishOld(result("stale"));
      await flushPromises();
      expect(
        wrapper.get('[data-testid="role-preview-prompt"]').text(),
      ).not.toContain("stale");
      expect(
        (wrapper.emitted("update:definition")!.at(-1)![0] as any).tasks[0]
          .overrides,
      ).toEqual({ persona_id: null, tools: [] });
      await wrapper.get('[data-testid="override-prompt"]').setValue(true);
      await wrapper
        .get('[data-testid="override-prompt-value"]')
        .setValue("Local");
      expect(
        (wrapper.emitted("update:definition")!.at(-1)![0] as any).tasks[0]
          .overrides.prompt,
      ).toBe("Local");
      await wrapper.get('[data-testid="override-prompt"]').setValue(false);
      expect(
        (wrapper.emitted("update:definition")!.at(-1)![0] as any).tasks[0]
          .overrides,
      ).not.toHaveProperty("prompt");
      await wrapper
        .findAll("button")
        .find((b) => b.text() === "graph.undo")!
        .trigger("click");
      expect(
        (wrapper.emitted("update:definition")!.at(-1)![0] as any).tasks[0]
          .overrides.prompt,
      ).toBe("Local");
    } finally {
      wrapper.unmount();
    }
  });

  it("saves only the direct role and leaves the node intact on success and failure", async () => {
    api.save
      .mockResolvedValueOnce({ role_id: "new", version: 1 })
      .mockRejectedValueOnce(new Error("denied"));
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition: {
          name: "Flow",
          default_provider_id: "graph-model",
          tasks: [
            {
              id: "a",
              name: "Agent",
              member: "a",
              task: "Private task",
              inputs: {},
              role: { name: "Agent", prompt: "Responsibility" },
              installation: { profile_id: "default" },
            },
          ],
          edges: [],
        },
      },
      global: { stubs },
    });
    try {
      const save = () =>
        wrapper
          .findAll("button")
          .find((b) => b.text() === "graph.saveRole")!
          .trigger("click");
      await save();
      await flushPromises();
      expect(api.save).toHaveBeenCalledWith(null, {
        definition: { name: "Agent", prompt: "Responsibility" },
        installation: { profile_id: "default" },
      });
      expect(wrapper.emitted("roleSaved")).toHaveLength(1);
      await save();
      await flushPromises();
      expect(wrapper.text()).toContain("denied");
      expect(
        (
          wrapper.get('[data-testid="inline-responsibility"]')
            .element as HTMLTextAreaElement
        ).value,
      ).toBe("Responsibility");
    } finally {
      wrapper.unmount();
    }
  });

  it("distinguishes inherited fields from explicit clears and reports role errors", async () => {
    api.resolve.mockRejectedValue({
      response: {
        data: {
          message: "Role not found",
          data: { fields: [{ path: "role_version", message: "Unavailable" }] },
        },
      },
    });
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition: {
          name: "Flow",
          tasks: [
            {
              id: "a",
              name: "Agent",
              member: "a",
              task: "Task",
              role_id: "missing",
              role_version: 1,
              overrides: {},
            },
          ],
          edges: [],
        },
      },
      global: { stubs },
    });
    try {
      wrapper
        .getComponent({ name: "TeamsFlowCanvas" })
        .vm.$emit("select-node", "a");
      await flushPromises();
      expect(
        wrapper.get('[data-testid="role-preview-error"]').text(),
      ).toContain("Role not found");
      expect(
        wrapper.get('[data-testid="role-preview-error"]').text(),
      ).toContain("role_version: Unavailable");
      await wrapper.get('[data-testid="override-persona_id"]').setValue(true);
      await wrapper.get('[data-testid="override-provider_id"]').setValue(true);
      for (const field of ["tools", "skills", "knowledge_bases"])
        await wrapper
          .get(`select[aria-label="roles.${field}: roles.mode"]`)
          .setValue("custom");
      let saved: any = wrapper.emitted("update:definition")!.at(-1)![0];
      expect(saved.tasks[0].overrides).toEqual({
        persona_id: null,
        provider_id: null,
        tools: [],
        skills: [],
        knowledge_bases: [],
      });
      await wrapper.get('[data-testid="override-persona_id"]').setValue(false);
      await wrapper
        .get('select[aria-label="roles.tools: roles.mode"]')
        .setValue("inherit");
      saved = wrapper.emitted("update:definition")!.at(-1)![0];
      expect(saved.tasks[0].overrides).not.toHaveProperty("persona_id");
      expect(saved.tasks[0].overrides.tools).toBeNull();
      expect(saved.tasks[0].overrides.skills).toEqual([]);
    } finally {
      wrapper.unmount();
    }
  });
});

it("keeps explicit unlimited and removes the override when returning to the template", async () => {
  const { default: HumanWaitPolicyEditor } = await import(
    "./HumanWaitPolicyEditor.vue"
  );
  const wrapper = mount(WorkflowGraphCanvas, {
    props: {
      definition: {
        name: "Flow",
        tasks: [
          {
            id: "a",
            name: "Agent",
            member: "a",
            task: "Task",
            role_id: "r",
            role_version: 1,
            overrides: {},
          },
        ],
        edges: [],
      },
      roles: [{ role_id: "r", version: 1, definition: { name: "Role" } }],
    },
    global: { stubs },
  });
  wrapper
    .getComponent({ name: "TeamsFlowCanvas" })
    .vm.$emit("select-node", "a");
  await flushPromises();
  const editor = wrapper.getComponent(HumanWaitPolicyEditor);
  editor.vm.$emit("update:modelValue", null);
  expect(
    (wrapper.emitted("update:definition")!.at(-1)![0] as any).tasks[0]
      .overrides,
  ).toHaveProperty("human_wait", null);
  editor.vm.$emit("update:modelValue", undefined);
  expect(
    (wrapper.emitted("update:definition")!.at(-1)![0] as any).tasks[0]
      .overrides,
  ).not.toHaveProperty("human_wait");
  wrapper.unmount();
});
