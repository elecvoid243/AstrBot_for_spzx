import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WorkflowWorkspacePanel from "./WorkflowWorkspacePanel.vue";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  save: vi.fn(),
  start: vi.fn(),
  runs: vi.fn(),
  run: vi.fn(),
  artifact: vi.fn(),
  revise: vi.fn(),
  file: vi.fn(),
  storeFile: vi.fn(),
  events: vi.fn(),
}));
const roles = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("@/api/workflowAssets", () => ({ workflowAssetsApi: api }));
vi.mock("@/api/roleAssets", () => ({ roleAssetsApi: roles }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

const asset = {
  asset_id: "asset-1",
  version: 1,
  definition: {
    name: "Report",
    role_id: "role-1",
    role_version: 1,
    task: "Research {topic}",
    required_inputs: ["topic"],
    overrides: {},
  },
};
const run = {
  run_id: "run-1",
  status: "running",
  snapshot: {
    definition: asset.definition,
    asset_id: "asset-1",
    asset_version: 1,
    task: "Research energy",
    member: { name: "Writer", member_id: "member-1" },
    effective: { prompt: "Frozen prompt" },
  },
  state: { attempt_id: "attempt-1", reason: null },
  result: null,
};
const slotStub = { template: "<div><slot /></div>" };
const stubs: Record<string, any> = Object.fromEntries(
  [
    "v-card",
    "v-card-text",
    "v-card-title",
    "v-card-actions",
    "v-alert",
    "v-spacer",
    "v-chip",
    "v-icon",
    "v-progress-linear",
    "v-expansion-panels",
    "v-expansion-panel",
    "v-expansion-panel-text",
  ].map((name) => [name, slotStub]),
);
Object.assign(stubs, {
  "v-file-input": slotStub,
  "v-dialog": {
    props: ["modelValue"],
    template: '<div v-if="modelValue" class="dialog"><slot /></div>',
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
      '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="item in items" :value="item.role_id ?? item">{{ item.definition?.name ?? item }}</option></select>',
  },
});

describe("single-task workflow workspace", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.list.mockResolvedValue({ assets: [asset] });
    roles.list.mockResolvedValue({
      roles: [
        {
          role_id: "role-1",
          version: 1,
          versions: [1],
          definition: { name: "Writer" },
        },
      ],
    });
    api.runs.mockResolvedValue({ runs: [run] });
    api.run.mockResolvedValue(run);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("continues a cursor, ignores duplicate observations and resets to a persisted snapshot", async () => {
    vi.useFakeTimers();
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    api.run.mockResolvedValue({ ...run, cursor: 10 });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    await flushPromises();
    api.events.mockResolvedValue({
      cursor: 10,
      reset: false,
      events: [{ cursor: 10 }],
      snapshot: { ...run, cursor: 10, status: "failed" },
    });
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(api.events).toHaveBeenLastCalledWith("run-1", 10);
    expect(wrapper.find('[data-testid="run-detail"]').text()).toContain(
      "v2.states.running",
    );
    api.events.mockResolvedValue({
      cursor: 20,
      reset: true,
      events: [],
      snapshot: {
        ...run,
        cursor: 20,
        status: "completed",
        result: { text: "Saved while this page was closed" },
      },
    });
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(wrapper.find('[data-testid="result-text"]').text()).toBe(
      "Saved while this page was closed",
    );
    wrapper.unmount();
    expect(api.start).not.toHaveBeenCalled();
  });

  it("saves a pinned asset and presents required input errors before starting a run", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/");
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "assets" },
      global: { plugins: [router], stubs },
    });
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "v2.edit")!
      .trigger("click");
    api.save.mockResolvedValue(asset);
    await wrapper
      .find(".dialog textarea")
      .setValue("Research {topic} carefully");
    await wrapper
      .findAll(".dialog button")
      .find((button) => button.text() === "v2.save")!
      .trigger("click");
    await flushPromises();
    expect(api.save).toHaveBeenCalledWith("asset-1", {
      definition: { ...asset.definition, task: "Research {topic} carefully" },
      expected_version: 1,
    });
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "v2.start")!
      .trigger("click");
    api.start.mockRejectedValueOnce({
      response: {
        data: {
          message: "Invalid run inputs",
          data: {
            fields: [
              { path: "inputs.topic", message: "Provide non-empty text" },
            ],
          },
        },
      },
    });
    await wrapper
      .findAll(".dialog button")
      .find((button) => button.text() === "v2.start")!
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("inputs.topic: Provide non-empty text");
    api.start.mockResolvedValue(run);
    await wrapper.find(".dialog input").setValue("energy");
    await wrapper
      .findAll(".dialog button")
      .find((button) => button.text() === "v2.start")!
      .trigger("click");
    await flushPromises();
    expect(api.start).toHaveBeenLastCalledWith("asset-1", {
      version: 1,
      inputs: { topic: "energy" },
      source: "dashboard",
    });
    expect(wrapper.emitted("started")).toEqual([["run-1"]]);
    expect(router.currentRoute.value.query.workflow_run).toBe("run-1");
    wrapper.unmount();
  });

  it.each(["raw", "protocol"])(
    "selects a waiting task and exposes its full %s tool result without inventing thinking",
    async (kind) => {
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=graph-1");
      const toolResult = "Full source result. ".repeat(2000);
      const first = {
        ...run,
        node_id: "first",
        status: "completed",
        snapshot: {
          ...run.snapshot,
          definition: { ...asset.definition, name: "Research" },
        },
      };
      const waiting = {
        ...run,
        run_id: "task-2",
        node_id: "review",
        status: "awaiting_result",
        snapshot: {
          ...run.snapshot,
          definition: {
            ...asset.definition,
            name: "Review a very long report title",
          },
        },
        state: {
          attempt_id: "attempt-2",
          reason: "A validated review is still required",
        },
        process: [
          {
            record_id: "record-2",
            kind: "tool_result",
            task_id: "task-2",
            attempt_id: "attempt-2",
            member_id: "member-2",
            data: {
              name: "read_evidence",
              result: toolResult,
              raw_result:
                kind === "raw"
                  ? { content: [{ type: "text", text: toolResult }] }
                  : null,
            },
          },
        ],
      };
      api.run.mockResolvedValue({
        ...run,
        run_id: "graph-1",
        status: "blocked",
        snapshot: {
          ...run.snapshot,
          definition: { ...asset.definition, tasks: [] },
        },
        tasks: [first, waiting],
        progress: { total: 2, completed: 1, running: 0, waiting: 1 },
      });
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: {
          plugins: [router],
          stubs: { ...stubs, WorkflowGraphCanvas: true },
        },
      });
      await flushPromises();
      expect(wrapper.find('[data-testid="run-goal"]').text()).toBe(
        "Research energy",
      );
      await wrapper.findAll(".run-task")[1].trigger("click");
      await flushPromises();
      const record = wrapper.find('[data-testid="process-record"]');
      await record.find("summary").trigger("click");
      expect(record.text()).toContain(toolResult.trim());
      expect(record.text()).toContain("attempt-2");
      expect(record.text()).toContain("member-2");
      expect(wrapper.find('[data-testid="provider-thinking"]').exists()).toBe(
        false,
      );
      expect(wrapper.text()).toContain("A validated review is still required");
      wrapper.unmount();
    },
  );

  it.each(["run", "artifact"])(
    "keeps the selected run while its %s read spans a polling interval",
    async (phase) => {
      vi.useFakeTimers();
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      const next = {
        ...run,
        run_id: "run-2",
        cursor: 20,
        status: "completed",
        snapshot: {
          ...run.snapshot,
          definition: { ...asset.definition, name: "Second run" },
        },
        result_ref: { artifact_id: "artifact-2", version: 1 },
      };
      let release!: (value: any) => void;
      const pending = new Promise((resolve) => {
        release = resolve;
      });
      api.runs.mockResolvedValue({ runs: [{ ...run, cursor: 10 }, next] });
      api.run.mockImplementation(async (id: string) =>
        id === "run-1"
          ? { ...run, cursor: 10 }
          : phase === "run"
          ? pending
          : next,
      );
      api.artifact.mockImplementation(async () =>
        phase === "artifact"
          ? pending
          : {
              artifact_id: "artifact-2",
              version: 1,
              content: { text: "Artifact from B" },
            },
      );
      api.events.mockResolvedValue({
        cursor: 10,
        reset: false,
        events: [],
        snapshot: null,
      });
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      await flushPromises();
      await wrapper.findAll(".workflow-run-list button")[1].trigger("click");
      await flushPromises();
      await vi.advanceTimersByTimeAsync(2000);
      release(
        phase === "run"
          ? next
          : {
              artifact_id: "artifact-2",
              version: 1,
              content: { text: "Artifact from B" },
            },
      );
      await flushPromises();
      expect(router.currentRoute.value.query.workflow_run).toBe("run-2");
      expect(wrapper.find('[data-testid="run-detail"]').text()).toContain(
        "Second run",
      );
      expect(wrapper.find('[data-testid="result-text"]').text()).toBe(
        "Artifact from B",
      );
      wrapper.unmount();
    },
  );

  it("reopens the saved run, observes completion and displays the complete result after remount", async () => {
    vi.useFakeTimers();
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    await flushPromises();
    expect(wrapper.find('[data-testid="run-detail"]').text()).toContain(
      "v2.states.running",
    );
    const result = "Complete evidence. ".repeat(1000);
    api.run.mockResolvedValue({
      ...run,
      status: "completed",
      result: { text: result },
    });
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(wrapper.find('[data-testid="result-text"]').text()).toBe(
      result.trim(),
    );
    wrapper.unmount();
    const reopenedRouter = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await reopenedRouter.push("/?workflow_run=run-1");
    const reopened = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [reopenedRouter], stubs },
    });
    await flushPromises();
    expect(reopened.find('[data-testid="result-text"]').text()).toBe(
      result.trim(),
    );
    expect(reopened.find('[data-testid="run-detail"]').text()).toContain(
      "v2.states.completed",
    );
    expect(api.start).not.toHaveBeenCalled();
    expect(
      reopened.findAll("button").some((button) => button.text() === "v2.edit"),
    ).toBe(false);
    reopened.unmount();
  });

  it("shows contract errors and reads a selected immutable version in full", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    api.run.mockResolvedValue({
      ...run,
      status: "completed",
      result: { text: "First" },
      result_ref: { artifact_id: "artifact-1", version: 1 },
      artifacts: [
        { artifact_id: "artifact-1", version: 1, summary: "First" },
        { artifact_id: "artifact-1", version: 2, summary: "Second" },
      ],
      validation_errors: [
        { path: "result.approved", message: "Must be boolean" },
      ],
    });
    api.artifact.mockImplementation(async (_id: string, version: number) => ({
      artifact_id: "artifact-1",
      version,
      content: {
        text: version === 1 ? "First" : "Complete evidence. ".repeat(1000),
      },
    }));
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("result.approved: Must be boolean");
    await wrapper.find('[data-testid="artifact-version"]').setValue("2");
    await flushPromises();
    expect(wrapper.find('[data-testid="result-text"]').text()).toContain(
      "Complete evidence. ".repeat(1000).trim(),
    );
    expect(api.artifact).toHaveBeenLastCalledWith("artifact-1", 2);
    expect(api.start).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it.each([
    ["success", false],
    ["failure", false],
    ["success", true],
    ["failure", true],
  ])(
    "keeps the selected version when an earlier read ends in %s (refresh: %s)",
    async (response, refreshWhileReading) => {
      vi.useFakeTimers();
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      api.run.mockResolvedValue({
        ...run,
        status: "completed",
        result_ref: { artifact_id: "artifact-1", version: 1 },
        artifacts: [{ version: 1 }, { version: 2 }],
      });
      const first = {
        artifact_id: "artifact-1",
        version: 1,
        content: { text: "Version one" },
      };
      api.artifact.mockResolvedValue(first);
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      await flushPromises();
      let settle!: () => void;
      const delayed = new Promise((resolve, reject) => {
        settle = () =>
          response === "success"
            ? resolve({
                ...first,
                version: 2,
                content: { text: "Version two" },
              })
            : reject(new Error("Outdated read failure"));
      });
      api.artifact.mockImplementation(async (_id: string, version: number) =>
        version === 2 ? delayed : first,
      );
      const selector = wrapper.find('[data-testid="artifact-version"]');
      await selector.setValue("2");
      if (refreshWhileReading) {
        await vi.advanceTimersByTimeAsync(2000);
        await flushPromises();
        expect((selector.element as HTMLSelectElement).value).toBe("2");
      }
      await selector.setValue("1");
      await flushPromises();
      expect(wrapper.find('[data-testid="result-text"]').text()).toBe(
        "Version one",
      );
      settle();
      await flushPromises();
      expect((selector.element as HTMLSelectElement).value).toBe("1");
      expect(wrapper.find('[data-testid="result-text"]').text()).toBe(
        "Version one",
      );
      expect(wrapper.text()).not.toContain("Outdated read failure");
      wrapper.unmount();
    },
  );

  it.each(["properties", "allOf", "$ref"])(
    "attaches a selected ready file with a %s contract and creates a revision",
    async (contractForm) => {
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      const fileContract = {
        type: "object",
        properties: { text: { type: "string" }, files: { type: "array" } },
        required: ["text", "files"],
      };
      const contract =
        contractForm === "allOf"
          ? { type: "object", allOf: [fileContract] }
          : contractForm === "$ref"
          ? {
              type: "object",
              $defs: { report: fileContract },
              $ref: "#/$defs/report",
            }
          : fileContract;
      api.run.mockResolvedValue({
        ...run,
        status: "completed",
        result_ref: { artifact_id: "artifact-1", version: 1 },
        artifacts: [{ version: 1 }],
        snapshot: {
          ...run.snapshot,
          definition: {
            ...asset.definition,
            output_contract: contract,
          },
        },
      });
      api.artifact.mockResolvedValue({
        artifact_id: "artifact-1",
        version: 1,
        content: { text: "Report", files: [] },
      });
      api.storeFile.mockResolvedValue({ file_id: "ready-file" });
      api.revise.mockResolvedValue({ artifact_id: "artifact-1", version: 2 });
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: {
          plugins: [router],
          stubs: {
            ...stubs,
            "v-file-input": {
              props: { modelValue: {}, multiple: Boolean },
              emits: ["update:modelValue"],
              template:
                '<input type="file" :multiple="multiple" @change="$emit(\'update:modelValue\', multiple ? Array.from($event.target.files) : $event.target.files[0])" />',
            },
          },
        },
      });
      await flushPromises();
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "v2.revise")!
        .trigger("click");
      const file = new File(["Complete file evidence"], "evidence.txt", {
        type: "text/plain",
      });
      const input = wrapper.find('input[type="file"]');
      Object.defineProperty(input.element, "files", {
        value: [file],
        configurable: true,
      });
      await input.trigger("change");
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "v2.saveRevision")!
        .trigger("click");
      await flushPromises();
      expect(api.storeFile).toHaveBeenCalledWith("run-1", file);
      expect(api.revise).toHaveBeenCalledWith("artifact-1", {
        expected_version: 1,
        request_id: expect.any(String),
        content: { text: "Report", files: [{ file_id: "ready-file" }] },
      });
      wrapper.unmount();
    },
  );
});
