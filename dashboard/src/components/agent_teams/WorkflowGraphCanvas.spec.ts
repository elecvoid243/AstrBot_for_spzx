import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { reactive } from "vue";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";
import RoleResourcesEditor from "./RoleResourcesEditor.vue";
import { VueFlow } from "@vue-flow/core";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

describe("canvas asset editing", () => {
  it("upgrades a pinned template without discarding explicit overrides", async () => {
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition: {
          name: "Report",
          tasks: [
            {
              id: "writer",
              name: "Writer",
              member: "writer",
              role_id: "role",
              role_version: 1,
              task: "Write",
              overrides: { persona_id: null, tools: [] },
            },
          ],
          edges: [],
        },
        roles: [
          {
            role_id: "role",
            version: 2,
            versions: [1, 2],
            definition: { name: "Writer" },
          },
        ],
      },
      global: {
        stubs: {
          TeamsFlowCanvas: {
            name: "TeamsFlowCanvas",
            emits: ["select-node"],
            template: "<div />",
          },
          WorkflowLoopPanel: true,
          "v-icon": true,
          "v-chip": true,
          "v-btn": { template: "<button><slot /></button>" },
          "v-alert": { template: "<div><slot /></div>" },
          "v-select": true,
          "v-text-field": true,
          "v-textarea": true,
          "v-switch": true,
          "v-spacer": true,
          "v-expansion-panels": true,
        },
      },
    });
    try {
      wrapper
        .getComponent({ name: "TeamsFlowCanvas" })
        .vm.$emit("select-node", "writer");
      await flushPromises();
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "graph.upgradeRole")!
        .trigger("click");
      const saved: any = wrapper.emitted("update:definition")!.at(-1)![0];
      expect(saved.tasks[0].role_version).toBe(2);
      expect(saved.tasks[0].overrides).toEqual({ persona_id: null, tools: [] });
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "graph.undo")!
        .trigger("click");
      const undone: any = wrapper.emitted("update:definition")!.at(-1)![0];
      expect(undone.tasks[0].role_version).toBe(1);
      expect(undone.tasks[0].overrides).toEqual({
        persona_id: null,
        tools: [],
      });
    } finally {
      wrapper.unmount();
    }
  });
  it("edits inline responsibility and resources, undoes, redoes and reopens after refresh", async () => {
    const definition = {
      name: "Report",
      default_provider_id: "model-a",
      required_inputs: ["topic"],
      edges: [],
      tasks: [
        {
          id: "agent",
          name: "Writer",
          member: "writer",
          task: "Write {topic}",
          inputs: { topic: { input: "topic" } },
          role: { name: "Writer", prompt: "Original" },
          installation: { profile_id: "default" },
        },
      ],
    };
    const catalog = {
      profiles: [],
      models: [],
      personas: [],
      tools: [{ id: "search", name: "Search" }],
      skills: [],
      knowledge_bases: [],
    };
    const options = {
      global: {
        stubs: {
          TeamsFlowCanvas: {
            name: "TeamsFlowCanvas",
            props: ["nodes", "edges"],
            emits: ["select-node"],
            template: "<div />",
          },
          WorkflowLoopPanel: true,
          "v-icon": true,
          "v-chip": true,
          "v-btn": { template: "<button><slot /></button>" },
          "v-text-field": true,
          "v-spacer": true,
          "v-alert": true,
          "v-switch": true,
          "v-expansion-panels": { template: "<div><slot /></div>" },
          "v-expansion-panel": { template: "<div><slot /></div>" },
          "v-expansion-panel-title": { template: "<div><slot /></div>" },
          "v-expansion-panel-text": { template: "<div><slot /></div>" },
          "v-textarea": {
            props: ["modelValue"],
            emits: ["update:modelValue"],
            template: `<textarea :value="modelValue" @input="$emit('update:modelValue', $event.target.value)" />`,
          },
          "v-select": {
            props: ["modelValue", "items", "multiple"],
            emits: ["update:modelValue"],
            template: `<select :multiple="multiple" :value="modelValue" @change="$emit('update:modelValue', multiple ? Array.from($event.target.options).filter(o => o.selected).map(o => o.value) : $event.target.value)"><option v-for="item in items" :value="item.id ?? item.value ?? item">{{ item.name ?? item.title ?? item }}</option></select>`,
          },
        },
      },
    };
    const wrapper = mount(WorkflowGraphCanvas, {
      ...options,
      props: { definition, catalog },
    });
    const canvas = wrapper.findComponent({ name: "TeamsFlowCanvas" });
    expect((canvas.props("nodes") as any[]).map((node) => node.id)).toEqual([
      "__input",
      "__output",
      "agent",
    ]);
    expect(
      (canvas.props("edges") as any[]).map((edge) => [
        edge.source,
        edge.target,
      ]),
    ).toEqual([
      ["__input", "agent"],
      ["agent", "__output"],
    ]);
    await wrapper
      .get('[data-testid="inline-responsibility"]')
      .setValue("Revised responsibility");
    await wrapper
      .get('select[aria-label="roles.tools: roles.mode"]')
      .setValue("custom");
    await wrapper
      .get('[data-issue-path="definition.tools"]')
      .setValue(["search"]);
    const saved: any = wrapper.emitted("update:definition")!.at(-1)![0];
    expect(saved.tasks[0].role).toMatchObject({
      prompt: "Revised responsibility",
      tools: ["search"],
    });
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "graph.undo")!
      .trigger("click");
    expect(
      (wrapper.emitted("update:definition")!.at(-1)![0] as any).tasks[0].role
        .tools,
    ).toEqual([]);
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "graph.redo")!
      .trigger("click");
    expect(
      (wrapper.emitted("update:definition")!.at(-1)![0] as any).tasks[0].role
        .tools,
    ).toEqual(["search"]);
    await wrapper.setProps({ roles: [], definition: saved });
    expect(
      (
        wrapper.get('[data-testid="inline-responsibility"]')
          .element as HTMLTextAreaElement
      ).value,
    ).toBe("Revised responsibility");
    wrapper.unmount();
    const reopened = mount(WorkflowGraphCanvas, {
      ...options,
      props: { definition: saved, catalog },
    });
    expect(
      (
        reopened.get('[data-testid="inline-responsibility"]')
          .element as HTMLTextAreaElement
      ).value,
    ).toBe("Revised responsibility");
    expect(reopened.find('[data-issue-path="definition.tools"]').exists()).toBe(
      true,
    );
    expect(
      (reopened.getComponent(RoleResourcesEditor).props("definition") as any)
        .tools,
    ).toEqual(["search"]);
    reopened
      .findComponent({ name: "TeamsFlowCanvas" })
      .vm.$emit("select-node", "__output");
    await reopened.vm.$nextTick();
    expect(reopened.text()).toContain("graph.outputHint");
    reopened.unmount();
  });
  it("adds a pinned team call with declared input and output contracts", async () => {
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition: {
          name: "Parent",
          required_inputs: ["goal"],
          tasks: [],
          edges: [],
        },
        teams: [
          {
            asset_id: "research",
            version: 2,
            versions: [2, 1],
            definition: {
              kind: "autonomous",
              name: "Research team",
              required_inputs: ["goal"],
              coordinator: { role_id: "coordinator", role_version: 1 },
              allowed_roles: [{ role_id: "writer", role_version: 1 }],
              output_contract: {
                type: "object",
                properties: { approved: { type: "boolean" } },
                required: ["approved"],
              },
            },
          },
        ],
      },
      global: {
        stubs: {
          VueFlow: true,
          Background: true,
          Controls: true,
          MiniMap: true,
          "v-icon": true,
          "v-chip": true,
          "v-btn": { template: "<button><slot /></button>" },
          "v-text-field": true,
          "v-select": true,
          "v-textarea": true,
          "v-spacer": true,
          "v-alert": true,
          "v-switch": true,
        },
      },
    });
    try {
      await wrapper.get('[data-testid="add-team-call"]').trigger("click");
      const saved: any = wrapper.emitted("update:definition")?.at(-1)?.[0];
      expect(saved.tasks[0].team).toEqual({ asset_id: "research", version: 2 });
      expect(saved.tasks[0].inputs).toEqual({ goal: { input: "goal" } });
      expect(saved.tasks[0].output_contract.required).toEqual(["approved"]);
      expect(saved.tasks[0].member).toBeUndefined();
      expect(wrapper.find('[data-testid="team-call-version"]').exists()).toBe(
        true,
      );
      expect(wrapper.get('[data-testid="team-call-preview"]').text()).toContain(
        "Research team",
      );
      expect(
        wrapper.get('[data-testid="team-call-preview"]').attributes("open"),
      ).toBeUndefined();
      await wrapper
        .get('[data-testid="team-call-preview"] summary')
        .trigger("click");
      expect(wrapper.text()).toContain("writer");
    } finally {
      wrapper.unmount();
    }
  });
  it("authors human question and approval nodes without requiring a role", async () => {
    const wrapper = mount(WorkflowGraphCanvas, {
      props: { definition: { name: "Human graph", tasks: [], edges: [] } },
      global: {
        stubs: {
          VueFlow: true,
          Background: true,
          Controls: true,
          MiniMap: true,
          "v-icon": true,
          "v-chip": true,
          "v-btn": { template: "<button><slot /></button>" },
          "v-text-field": true,
          "v-select": true,
          "v-textarea": true,
          "v-spacer": true,
          "v-alert": true,
          "v-switch": true,
        },
      },
    });
    try {
      await wrapper.get('[data-testid="add-human-question"]').trigger("click");
      let saved: any = wrapper.emitted("update:definition")?.at(-1)?.[0];
      expect(saved.tasks[0].human.kind).toBe("question");
      expect(saved.tasks[0].role_id).toBeUndefined();
      const field = wrapper.getComponent({ name: "v-textarea" });
      field.vm.$emit("update:modelValue", "Choose a region");
      await flushPromises();
      saved = wrapper.emitted("update:definition")?.at(-1)?.[0];
      expect(saved.tasks[0].human.question).toBe("Choose a region");
      await wrapper.get('[data-testid="add-human-approval"]').trigger("click");
      saved = wrapper.emitted("update:definition")?.at(-1)?.[0];
      expect(saved.tasks[1].human.kind).toBe("approval");
      expect(wrapper.find('[data-testid="human-artifact-task"]').exists()).toBe(
        true,
      );
    } finally {
      wrapper.unmount();
    }
  });
  it.each(["click", "drop"])(
    "adds unique tasks by %s when randomUUID is unavailable",
    async (action) => {
      vi.stubGlobal("crypto", {
        getRandomValues: crypto.getRandomValues.bind(crypto),
        randomUUID: undefined,
      });
      vi.spyOn(Date, "now").mockReturnValue(1720000000000);
      vi.spyOn(Math, "random").mockReturnValue(0.5);
      const wrapper = mount(WorkflowGraphCanvas, {
        props: {
          definition: { name: "HTTP workflow", tasks: [], edges: [] },
          roles: [
            { role_id: "writer", version: 2, definition: { name: "Writer" } },
          ],
        },
        global: {
          stubs: {
            VueFlow: true,
            Background: true,
            Controls: true,
            MiniMap: true,
            "v-icon": true,
            "v-chip": true,
            "v-btn": { template: "<button><slot /></button>" },
            "v-text-field": true,
            "v-select": true,
            "v-textarea": true,
            "v-spacer": true,
            "v-alert": true,
            "v-switch": true,
          },
        },
      });
      try {
        for (let count = 1; count <= 2; count++) {
          if (action === "click") {
            await wrapper.get(".graph-role").trigger("click");
          } else {
            await wrapper.get(".teams-flow-canvas").trigger("drop", {
              clientX: 50,
              clientY: 70,
              dataTransfer: { getData: () => "writer" },
            });
          }
          const saved: any = wrapper.emitted("update:definition")?.at(-1)?.[0];
          expect(saved?.tasks).toHaveLength(count);
        }
        const saved: any = wrapper.emitted("update:definition")?.at(-1)?.[0];
        expect(saved.tasks[0].id).not.toBe(saved.tasks[1].id);
        expect(
          saved.tasks.map((task: any) => [task.role_id, task.role_version]),
        ).toEqual([
          ["writer", 2],
          ["writer", 2],
        ]);
        expect(saved.tasks[1].position).toEqual(
          action === "drop" ? { x: 50, y: 70 } : { x: 300, y: 0 },
        );
      } finally {
        wrapper.unmount();
        vi.restoreAllMocks();
        vi.unstubAllGlobals();
      }
    },
  );
  it("undoes and redoes a complete drag gesture", async () => {
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition: {
          name: "Report",
          tasks: [
            {
              id: "writer",
              name: "Writer",
              member: "writer",
              role_id: "role",
              role_version: 1,
              task: "Write",
              position: { x: 0, y: 0 },
            },
          ],
          edges: [],
        },
      },
      global: {
        stubs: {
          VueFlow: true,
          Background: true,
          Controls: true,
          MiniMap: true,
          "v-icon": true,
          "v-chip": true,
          "v-btn": { template: "<button><slot /></button>" },
          "v-text-field": true,
          "v-select": true,
          "v-textarea": true,
          "v-spacer": true,
          "v-alert": true,
          "v-switch": true,
        },
      },
    });
    const flow = wrapper.findComponent(VueFlow);
    for (let x = 1; x <= 5; x++)
      flow.vm.$emit("nodesChange", [
        {
          type: "position",
          id: "writer",
          position: { x, y: 0 },
          dragging: true,
        },
      ]);
    flow.vm.$emit("nodeDragStop", {
      node: { id: "writer", position: { x: 5, y: 0 } },
      nodes: [{ id: "writer", position: { x: 5, y: 0 } }],
    });
    await flushPromises();
    const undo = wrapper
      .findAll("button")
      .find((button) => button.text() === "graph.undo")!;
    await undo.trigger("click");
    let events: any[] = wrapper.emitted("update:definition")!;
    expect(events[events.length - 1][0].tasks[0].position).toEqual({
      x: 0,
      y: 0,
    });
    const redo = wrapper
      .findAll("button")
      .find((button) => button.text() === "graph.redo")!;
    await redo.trigger("click");
    events = wrapper.emitted("update:definition")!;
    expect(events[events.length - 1][0].tasks[0].position).toEqual({
      x: 5,
      y: 0,
    });
    wrapper.unmount();
  });
  it("keeps declared workflow inputs and name when a role is added after metadata editing", async () => {
    const definition = reactive({
      name: "",
      tasks: [],
      edges: [],
      required_inputs: [] as string[],
    });
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition,
        roles: [
          { role_id: "writer", version: 2, definition: { name: "Writer" } },
        ],
      },
      global: {
        stubs: {
          TeamsFlowCanvas: true,
          "v-icon": true,
          "v-chip": true,
          "v-btn": { template: "<button><slot /></button>" },
          "v-text-field": true,
          "v-select": true,
          "v-textarea": true,
          "v-spacer": true,
          "v-alert": true,
          "v-switch": true,
        },
      },
    });
    definition.name = "Evidence report";
    definition.required_inputs = ["topic"];
    await wrapper.setProps({ definition });
    await flushPromises();
    await wrapper.get(".graph-role").trigger("click");
    const saved: any = wrapper.emitted("update:definition")?.at(-1)?.[0];
    expect(saved.name).toBe("Evidence report");
    expect(saved.required_inputs).toEqual(["topic"]);
    expect(saved.tasks[0].role_version).toBe(2);
    wrapper.unmount();
  });
});
