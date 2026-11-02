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
      result_task_id: "agent",
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
  it("selects upstream fields and an explicit output, preserving edits and undo", async () => {
    const definition = {
      name: "Fields",
      result_task_id: "first",
      required_inputs: [],
      tasks: [
        {
          id: "first",
          name: "First",
          member: "one",
          task: "Produce",
          role: { name: "One" },
          installation: { profile_id: "default" },
          output_contract: {
            type: "object",
            properties: {
              answer: { type: "string" },
              count: { type: "integer" },
            },
            required: ["answer", "count"],
          },
        },
        {
          id: "second",
          name: "Second",
          member: "two",
          task: "Use {value}",
          role: { name: "Two" },
          installation: { profile_id: "default" },
          inputs: { value: { task_id: "first", field: "answer" } },
        },
      ],
      edges: [{ source: "first", target: "second" }],
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
          "v-textarea": true,
          "v-spacer": true,
          "v-alert": true,
          "v-switch": true,
          "v-select": {
            name: "VSelect",
            props: ["modelValue", "items"],
            emits: ["update:modelValue"],
            template: "<div />",
          },
          "v-expansion-panels": { template: "<div><slot /></div>" },
          "v-expansion-panel": { template: "<div><slot /></div>" },
          "v-expansion-panel-title": { template: "<div><slot /></div>" },
          "v-expansion-panel-text": { template: "<div><slot /></div>" },
        },
      },
    };
    const wrapper = mount(WorkflowGraphCanvas, {
      ...options,
      props: { definition },
    });
    const canvas = wrapper.findComponent({ name: "TeamsFlowCanvas" });
    canvas.vm.$emit("select-node", "second");
    await wrapper.vm.$nextTick();
    const field = wrapper.getComponent('[data-testid="input-field"]') as any;
    expect(field.props("items")).toEqual([
      { title: "answer · string", value: "answer" },
      { title: "count · integer", value: "count" },
    ]);
    field.vm.$emit("update:modelValue", "count");
    await wrapper.vm.$nextTick();
    let saved: any = wrapper.emitted("update:definition")!.at(-1)![0];
    expect(saved.tasks[1].inputs.value.field).toBe("count");
    canvas.vm.$emit("select-node", "__output");
    await wrapper.vm.$nextTick();
    (wrapper.getComponent('[data-testid="result-task"]') as any).vm.$emit(
      "update:modelValue",
      "second",
    );
    await wrapper.vm.$nextTick();
    saved = wrapper.emitted("update:definition")!.at(-1)![0];
    expect(saved.result_task_id).toBe("second");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "graph.undo")!
      .trigger("click");
    expect(
      (wrapper.emitted("update:definition")!.at(-1)![0] as any).result_task_id,
    ).toBe("first");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "graph.redo")!
      .trigger("click");
    expect(
      (wrapper.emitted("update:definition")!.at(-1)![0] as any).result_task_id,
    ).toBe("second");
    wrapper.unmount();
    const reopened = mount(WorkflowGraphCanvas, {
      ...options,
      props: { definition: saved },
    });
    reopened
      .findComponent({ name: "TeamsFlowCanvas" })
      .vm.$emit("select-node", "second");
    await reopened.vm.$nextTick();
    expect(
      (reopened.getComponent('[data-testid="input-field"]') as any).props(
        "modelValue",
      ),
    ).toBe("count");
    await reopened.setProps({
      issues: [
        { path: "definition.result_task_id", message: "Missing result" },
      ],
    });
    expect(reopened.find('[data-testid="result-task"]').exists()).toBe(true);
    reopened.unmount();
  });
  it("builds a structured output with required typed fields", async () => {
    const definition = {
      name: "Fields",
      result_task_id: "one",
      tasks: [
        {
          id: "one",
          name: "One",
          member: "one",
          role: { name: "One" },
          installation: { profile_id: "default" },
          task: "Work",
        },
      ],
      edges: [],
    };
    const wrapper = mount(WorkflowGraphCanvas, {
      props: { definition },
      global: {
        stubs: {
          TeamsFlowCanvas: true,
          WorkflowLoopPanel: true,
          "v-icon": true,
          "v-chip": true,
          "v-btn": { template: "<button><slot /></button>" },
          "v-text-field": true,
          "v-textarea": true,
          "v-spacer": true,
          "v-alert": true,
          "v-switch": {
            name: "VSwitch",
            props: ["modelValue"],
            emits: ["update:modelValue"],
            template: "<div />",
          },
          "v-select": {
            name: "VSelect",
            props: ["modelValue", "items"],
            emits: ["update:modelValue"],
            template: "<div />",
          },
          "v-expansion-panels": { template: "<div><slot /></div>" },
          "v-expansion-panel": { template: "<div><slot /></div>" },
          "v-expansion-panel-title": { template: "<div><slot /></div>" },
          "v-expansion-panel-text": { template: "<div><slot /></div>" },
        },
      },
    });
    (wrapper.getComponent('[data-testid="structured-output"]') as any).vm.$emit(
      "update:modelValue",
      true,
    );
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "graph.addField")!
      .trigger("click");
    (wrapper.getComponent('[data-testid="output-field-type"]') as any).vm.$emit(
      "update:modelValue",
      "boolean",
    );
    await wrapper.vm.$nextTick();
    const saved: any = wrapper.emitted("update:definition")!.at(-1)![0];
    expect(saved.tasks[0].output_contract).toEqual({
      type: "object",
      properties: { field1: { type: "boolean" } },
      required: ["field1"],
      additionalProperties: false,
    });
    wrapper.unmount();
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
  it("adds a fixed workflow call using its public result contract", async () => {
    const contract = {
      type: "object",
      properties: { proof: { type: "string" } },
      required: ["proof"],
    };
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition: {
          name: "Parent",
          required_inputs: ["topic"],
          tasks: [],
          edges: [],
        },
        workflows: [
          {
            asset_id: "child",
            version: 2,
            versions: [2, 1],
            definition: {
              name: "Child",
              required_inputs: ["topic"],
              result_task_id: "public",
              tasks: [
                { id: "private", output_contract: null },
                { id: "public", output_contract: contract },
              ],
            },
          },
        ],
      },
      global: {
        stubs: {
          TeamsFlowCanvas: true,
          WorkflowLoopPanel: true,
          "v-icon": true,
          "v-chip": true,
          "v-btn": { template: "<button><slot /></button>" },
          "v-text-field": true,
          "v-select": true,
          "v-textarea": true,
          "v-spacer": true,
          "v-alert": true,
          "v-switch": true,
          "v-expansion-panels": true,
        },
      },
    });
    try {
      await wrapper.get('[data-testid="add-workflow-call"]').trigger("click");
      const saved: any = wrapper.emitted("update:definition")?.at(-1)?.[0];
      expect(saved.tasks[0].workflow).toEqual({
        asset_id: "child",
        version: 2,
      });
      expect(saved.tasks[0].inputs).toEqual({ topic: { input: "topic" } });
      expect(saved.tasks[0].output_contract).toEqual(contract);
      expect(
        wrapper.find('[data-testid="workflow-call-version"]').exists(),
      ).toBe(true);
      expect(
        wrapper.get('[data-testid="child-output-contract"]').text(),
      ).toContain("proof");
      expect(
        wrapper.get('[data-testid="workflow-call-preview"]').text(),
      ).toContain("Child");
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
  it("authors condition exits and an ALL join with undo, redo and reopen", async () => {
    const stubs = {
      TeamsFlowCanvas: {
        name: "TeamsFlowCanvas",
        props: ["nodes", "edges"],
        emits: ["selectNode", "selectEdge", "connect"],
        template: "<div />",
      },
      WorkflowLoopPanel: true,
      "v-icon": true,
      "v-chip": true,
      "v-btn": { template: "<button><slot /></button>" },
      "v-select": true,
      "v-text-field": true,
      "v-textarea": true,
      "v-spacer": true,
      "v-alert": true,
      "v-switch": true,
      "v-expansion-panels": true,
    };
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition: {
          name: "Branch",
          required_inputs: ["choice"],
          tasks: [],
          edges: [],
        },
      },
      global: { stubs },
    });
    try {
      await wrapper.get('[data-testid="add-condition"]').trigger("click");
      let saved: any = wrapper.emitted("update:definition")!.at(-1)![0];
      const condition = saved.tasks[0].id;
      expect(saved.tasks[0].control).toEqual({
        kind: "condition",
        operator: "eq",
        value: "",
      });
      expect(saved.tasks[0].inputs.value.input).toBe("choice");
      (wrapper.getComponent('[data-testid="control-value"]') as any).vm.$emit(
        "update:modelValue",
        "yes",
      );
      await wrapper.get('[data-testid="add-join"]').trigger("click");
      saved = wrapper.emitted("update:definition")!.at(-1)![0];
      const join = saved.tasks[1].id;
      expect(saved.tasks[1].control).toEqual({ kind: "join" });
      const canvas = wrapper.getComponent({ name: "TeamsFlowCanvas" });
      canvas.vm.$emit("connect", {
        from: condition,
        to: join,
        sourceHandle: "matched-false",
      });
      await flushPromises();
      saved = wrapper.emitted("update:definition")!.at(-1)![0];
      expect(saved.tasks[0].control.value).toBe("yes");
      expect(saved.edges[0].condition).toEqual({
        field: "matched",
        operator: "eq",
        value: false,
      });
      expect(
        (canvas.props("nodes") as any[]).find((node) => node.id === condition)
          .data.controlKind,
      ).toBe("condition");
      expect((canvas.props("edges") as any[])[0].sourceHandle).toBe(
        "matched-false",
      );
      canvas.vm.$emit("selectEdge", `${condition}:${join}`);
      await flushPromises();
      (wrapper.getComponent('[data-testid="edge-exit"]') as any).vm.$emit(
        "update:modelValue",
        true,
      );
      await flushPromises();
      expect(
        (wrapper.emitted("update:definition")!.at(-1)![0] as any).edges[0]
          .condition.value,
      ).toBe(true);
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "graph.undo")!
        .trigger("click");
      expect(
        (wrapper.emitted("update:definition")!.at(-1)![0] as any).edges[0]
          .condition.value,
      ).toBe(false);
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "graph.redo")!
        .trigger("click");
      saved = wrapper.emitted("update:definition")!.at(-1)![0];
      expect(saved.edges[0].condition.value).toBe(true);
      const reopened = mount(WorkflowGraphCanvas, {
        props: { definition: saved },
        global: { stubs },
      });
      try {
        const reopenedCanvas = reopened.getComponent({
          name: "TeamsFlowCanvas",
        });
        expect((reopenedCanvas.props("edges") as any[])[0].sourceHandle).toBe(
          "matched-true",
        );
        reopenedCanvas.vm.$emit("selectNode", join);
        await flushPromises();
        await reopened
          .findAll("button")
          .find((button) => button.text() === "graph.removeTask")!
          .trigger("click");
        const deleted: any = reopened.emitted("update:definition")!.at(-1)![0];
        expect(deleted.tasks).toHaveLength(1);
        expect(deleted.edges).toHaveLength(0);
      } finally {
        reopened.unmount();
      }
    } finally {
      wrapper.unmount();
    }
  });
  it("connects named approval and return outlets to explicit conditions", async () => {
    const wrapper = mount(WorkflowGraphCanvas, {
      props: { definition: { name: "Review", tasks: [
        { id: "approval", name: "Approval", human: { kind: "approval", question: "Approve?", artifact_task_id: "writer" } },
        { id: "next", name: "Next", member: "writer", role_id: "role", role_version: 1, task: "Deliver" },
      ], edges: [] } },
      global: { stubs: {
        TeamsFlowCanvas: { name: "TeamsFlowCanvas", props: ["nodes", "edges"], emits: ["connect"], template: "<div />" },
        WorkflowLoopPanel: true, "v-btn": true, "v-icon": true, "v-chip": true, "v-select": true, "v-text-field": true, "v-textarea": true, "v-spacer": true, "v-alert": true, "v-switch": true, "v-expansion-panels": true,
      } },
    });
    try {
      const canvas = wrapper.getComponent({ name: "TeamsFlowCanvas" });
      canvas.vm.$emit("connect", { from: "approval", to: "next", sourceHandle: "matched-false" });
      await flushPromises();
      const saved: any = wrapper.emitted("update:definition")!.at(-1)![0];
      expect(saved.edges[0].condition).toEqual({ field: "approved", operator: "eq", value: false });
      expect((canvas.props("nodes") as any[]).find((node) => node.id === "approval").data.approval).toBe(true);
      expect((canvas.props("edges") as any[])[0].label).toBe("human.returnForRevision");
    } finally { wrapper.unmount(); }
  });
});
