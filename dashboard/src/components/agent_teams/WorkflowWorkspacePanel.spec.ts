import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WorkflowWorkspacePanel from "./WorkflowWorkspacePanel.vue";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  save: vi.fn(),
  start: vi.fn(),
  readiness: vi.fn(),
  runs: vi.fn(),
  history: vi.fn(),
  run: vi.fn(),
  artifact: vi.fn(),
  revise: vi.fn(),
  file: vi.fn(),
  storeFile: vi.fn(),
  events: vi.fn(),
  recover: vi.fn(),
  resolveTool: vi.fn(),
  control: vi.fn(),
  retry: vi.fn(),
  answer: vi.fn(),
}));
const roles = vi.hoisted(() => ({ list: vi.fn(), resources: vi.fn() }));
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
const runOverview = {
  run_id: "run-1",
  status: "running",
  snapshot: {
    definition: { name: "Report" },
    asset_id: "asset-1",
    asset_version: 1,
    task: "Research energy",
  },
  state: { attempt_id: "attempt-1", reason: null },
  progress: { total: 1, completed: 0, running: 1, waiting: 0 },
  artifacts: [],
  result_ref: null,
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
      '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="item in items" :value="item.value ?? item.role_id ?? item">{{ item.title ?? item.definition?.name ?? item }}</option></select>',
  },
});

describe("single-task workflow workspace", () => {
  it("finds active, waiting and historical runs without editing their definitions", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/");
    api.runs.mockResolvedValue({
      runs: [
        runOverview,
        {
          ...runOverview,
          run_id: "blocked-run",
          status: "blocked",
          snapshot: {
            ...runOverview.snapshot,
            definition: { name: "Missing model" },
          },
        },
        {
          ...runOverview,
          run_id: "done-run",
          status: "completed",
          snapshot: {
            ...runOverview.snapshot,
            definition: { name: "Historical report" },
          },
        },
        {
          ...runOverview,
          run_id: "waiting-run",
          todos: [{ status: "pending" }],
          snapshot: {
            ...runOverview.snapshot,
            definition: { name: "Waiting for approval" },
          },
        },
      ],
    });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    await flushPromises();
    await wrapper.get('[data-testid="runs-active"]').trigger("click");
    expect(wrapper.get(".workflow-run-list").text()).toContain("Report");
    expect(wrapper.get(".workflow-run-list").text()).not.toContain(
      "Historical report",
    );
    await wrapper.get('[data-testid="runs-pending"]').trigger("click");
    expect(wrapper.get(".workflow-run-list").text()).toContain("Missing model");
    expect(wrapper.get(".workflow-run-list").text()).toContain(
      "Waiting for approval",
    );
    expect(wrapper.get(".workflow-run-list").text()).not.toContain(
      "Historical report",
    );
    await wrapper.get('[data-testid="runs-history"]').trigger("click");
    await flushPromises();
    expect(wrapper.get(".workflow-run-list").text()).toContain(
      "Historical report",
    );
    expect(wrapper.get(".workflow-run-list").text()).not.toContain(
      "Missing model",
    );
    await wrapper.get(".workflow-run-list button").trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
      "workbench.snapshotHint",
    );
    expect(
      wrapper.get('[data-testid="run-detail"]').find("textarea").exists(),
    ).toBe(false);
    wrapper.unmount();
  });
  it("keeps teams and workflows in their own asset directories", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/");
    api.list.mockResolvedValue({
      assets: [
        asset,
        {
          ...asset,
          asset_id: "team-1",
          definition: {
            name: "Research team",
            kind: "autonomous",
            allowed_roles: [],
          },
        },
      ],
    });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "assets", assetKind: "teams" },
      global: { plugins: [router], stubs },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Research team");
    expect(wrapper.text()).not.toContain("Report · v1");
    await wrapper.setProps({ assetKind: "workflows" });
    expect(wrapper.text()).toContain("Report · v1");
    expect(wrapper.text()).not.toContain("Research team");
    wrapper.unmount();
  });
  it("navigates from a nested coordinator through its call to the child task", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    const coordinator = {
      ...run,
      run_id: "nested-coordinator",
      snapshot: {
        ...run.snapshot,
        coordinator: true,
        definition: { name: "Nested coordinator" },
      },
      state: { ...run.state, task_id: "coordinator-task" },
    };
    const call = {
      ...run,
      run_id: "nested-call",
      snapshot: {
        ...run.snapshot,
        engine: "workflow_call_v2",
        workflow: { asset_id: "report", version: 1, result_task_id: "result" },
        created_by_task_id: "coordinator-task",
        definition: { name: "Pinned report call" },
      },
      state: { ...run.state, child_scope_run_id: "child-workflow" },
    };
    const child = {
      ...run,
      run_id: "child-result",
      snapshot: {
        ...run.snapshot,
        definition: { name: "Independent report writer" },
      },
    };
    api.run.mockResolvedValue({
      ...run,
      tasks: [run],
      scopes: [
        { run_id: "nested-team", tasks: [coordinator, call] },
        { run_id: "child-workflow", tasks: [child] },
      ],
    });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: {
        plugins: [router],
        stubs: { ...stubs, WorkflowRunProgress: true },
      },
    });
    try {
      await flushPromises();
      wrapper
        .findComponent({ name: "WorkflowRunProgress" })
        .vm.$emit("select", coordinator.run_id);
      await flushPromises();
      expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
        "Nested coordinator",
      );
      await wrapper
        .get('[data-testid="coordinator-workflow-call"]')
        .trigger("click");
      await flushPromises();
      expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
        "Pinned report call",
      );
      await wrapper.get('[data-testid="open-child-workflow"]').trigger("click");
      await flushPromises();
      expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
        "Independent report writer",
      );
    } finally {
      wrapper.unmount();
    }
  });
  it("opens an earlier iteration's complete result without replacing the current round", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    const first = {
      ...run,
      run_id: "first-round",
      node_id: "write",
      status: "completed",
      snapshot: { ...run.snapshot, iteration: 1 },
      result: { text: "Original reviewed content" },
      result_ref: { artifact_id: "first-round", version: 1 },
    };
    const second = {
      ...first,
      run_id: "second-round",
      snapshot: { ...run.snapshot, iteration: 2 },
      result: { text: "Revised approved content" },
      result_ref: { artifact_id: "second-round", version: 1 },
    };
    api.artifact.mockImplementation(async (id: string) => ({
      artifact_id: id,
      version: 1,
      content: id === "first-round" ? first.result : second.result,
    }));
    api.run.mockResolvedValue({
      ...run,
      status: "completed",
      snapshot: {
        ...run.snapshot,
        definition: {
          name: "Revision graph",
          tasks: [
            {
              id: "write",
              name: "Writer",
              member: "writer",
              role_id: "role-1",
              role_version: 1,
              task: "Write",
            },
          ],
          edges: [],
          loops: [],
        },
      },
      tasks: [second],
      loops: [
        {
          loop_id: "revision",
          status: "passed",
          iterations: [
            {
              iteration: 1,
              status: "rejected",
              tasks: [first],
              task_runs: { write: "first-round" },
            },
            {
              iteration: 2,
              status: "passed",
              tasks: [second],
              task_runs: { write: "second-round" },
            },
          ],
        },
      ],
    });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    try {
      await flushPromises();
      await wrapper
        .get('[data-testid="iteration-task-first-round"]')
        .trigger("click");
      await flushPromises();
      expect(wrapper.get('[data-testid="result-text"]').text()).toContain(
        "Original reviewed content",
      );
      await wrapper
        .get('[data-testid="iteration-task-second-round"]')
        .trigger("click");
      await flushPromises();
      expect(wrapper.get('[data-testid="result-text"]').text()).toContain(
        "Revised approved content",
      );
    } finally {
      wrapper.unmount();
    }
  });
  it("allows answering another run while an earlier answer is pending", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    const firstTodo = {
      todo_id: "q1",
      task_run_id: "q1",
      kind: "question",
      question: "First question",
      status: "pending",
      constraints: { choices: [], max_length: 100 },
    };
    const secondTodo = {
      ...firstTodo,
      todo_id: "q2",
      task_run_id: "q2",
      question: "Second question",
    };
    const first = { ...run, todos: [firstTodo] };
    const second = {
      ...run,
      run_id: "run-2",
      todos: [secondTodo],
      snapshot: {
        ...run.snapshot,
        definition: { ...asset.definition, name: "Second report" },
      },
    };
    api.run.mockImplementation(async (id: string) =>
      id === "run-1" ? first : second,
    );
    api.runs.mockResolvedValue({
      runs: [runOverview, { ...runOverview, run_id: "run-2" }],
    });
    let finishFirst!: (value: any) => void;
    api.answer.mockReturnValueOnce(
      new Promise((resolve) => {
        finishFirst = resolve;
      }),
    );
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    try {
      await flushPromises();
      await wrapper.get('[data-testid="human-answer"]').setValue("Asia");
      await wrapper.get('[data-testid="human-submit"]').trigger("click");
      expect(api.answer).toHaveBeenCalledTimes(1);
      await wrapper
        .findAll("button")
        .filter((button) => button.text() === "v2.details")[1]
        .trigger("click");
      await flushPromises();
      expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
        "Second question",
      );
      await wrapper.get('[data-testid="human-answer"]').setValue("Europe");
      api.answer.mockResolvedValueOnce({
        ...second,
        todos: [
          {
            ...secondTodo,
            status: "completed",
            response: { answer: "Europe" },
          },
        ],
      });
      expect(
        wrapper.get('[data-testid="human-submit"]').attributes("disabled"),
      ).toBeUndefined();
      await wrapper.get('[data-testid="human-submit"]').trigger("click");
      await flushPromises();
      expect(api.answer).toHaveBeenLastCalledWith(
        "run-2",
        "q2",
        expect.objectContaining({ answer: "Europe" }),
      );
    } finally {
      finishFirst({ ...first, todos: [{ ...firstTodo, status: "completed" }] });
      await flushPromises();
      wrapper.unmount();
    }
  });
  it.each(
    ["answer", "control", "resolve", "recover"].flatMap((operation) =>
      ["success", "failure"].flatMap((oldOutcome) =>
        ["success", "failure"].map((outcome) => ({
          operation,
          oldOutcome,
          outcome,
        })),
      ),
    ),
  )(
    "isolates human operations across runs ($operation / old $oldOutcome / current $outcome)",
    async ({ operation, oldOutcome, outcome }) => {
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      const todo = {
        todo_id: "q1",
        task_run_id: "q1",
        kind: "question",
        question: "First question",
        status: "pending",
        constraints: { choices: [], max_length: 100 },
      };
      const task = {
        ...run,
        run_id: "child-1",
        node_id: "report",
        status: "interrupted",
        recovery: { available: true, boundary: "response" },
        tool_calls: [
          {
            record_id: "call-1",
            name: "publish",
            status: "unknown",
            args: {},
            recovery: {},
            available_actions: ["retry"],
          },
        ],
      };
      const first = {
        ...run,
        control: { status: "active", revision: 0 },
        todos: [todo],
        tasks: [task],
      };
      const action =
        operation === "control"
          ? "approve"
          : operation === "resolve"
          ? "reject"
          : "answer";
      const secondTodo = {
        ...todo,
        todo_id: "q2",
        task_run_id: "q2",
        question: "Second question",
        kind: action === "answer" ? "question" : "approval",
        artifact_ref:
          action === "answer" ? null : { artifact_id: "report-2", version: 1 },
      };
      const second = {
        ...run,
        run_id: "run-2",
        todos: [secondTodo],
        control: { status: "active", revision: 0 },
        snapshot: {
          ...run.snapshot,
          definition: { ...asset.definition, name: "Second report" },
        },
      };
      api.run.mockImplementation(async (id) =>
        id === "run-1" ? first : second,
      );
      api.runs.mockResolvedValue({
        runs: [runOverview, { ...runOverview, run_id: "run-2" }],
      });
      api.artifact.mockResolvedValue({
        artifact_id: "report-2",
        version: 1,
        content: { text: "Complete approval content" },
        files: [],
      });
      let finishOld!: (value: any) => void;
      let failOld!: (error: Error) => void;
      const oldApi =
        operation === "answer"
          ? api.answer
          : operation === "control"
          ? api.control
          : operation === "resolve"
          ? api.resolveTool
          : api.recover;
      oldApi.mockReturnValueOnce(
        new Promise((resolve, reject) => {
          finishOld = resolve;
          failOld = reject;
        }),
      );
      let finishCurrent!: (value: any) => void;
      let failCurrent!: (error: Error) => void;
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      try {
        await flushPromises();
        if (operation === "answer") {
          await wrapper.get('[data-testid="human-answer"]').setValue("Asia");
          await wrapper.get('[data-testid="human-submit"]').trigger("click");
        } else if (operation === "control") {
          await wrapper.get('[data-testid="pause-run"]').trigger("click");
        } else if (operation === "recover") {
          await wrapper.get('[data-testid="recover-attempt"]').trigger("click");
        } else {
          const form = wrapper.get('[data-testid="tool-resolution"]');
          await form.get("select").setValue("retry");
          await form
            .get('[data-testid="resolution-evidence"]')
            .setValue("Owner verified the first operation.");
          await form.get("button").trigger("click");
        }
        expect(oldApi).toHaveBeenCalledTimes(1);
        const firstCallCount = api.answer.mock.calls.length;
        await wrapper
          .findAll("button")
          .filter((button) => button.text() === "v2.details")[1]
          .trigger("click");
        await flushPromises();
        if (action === "answer") {
          await wrapper.get('[data-testid="human-answer"]').setValue("Europe");
        } else if (action === "approve") {
          await wrapper.get('[data-testid="human-preview"]').trigger("click");
          await flushPromises();
        } else {
          await wrapper
            .get('[data-testid="human-feedback"]')
            .setValue("Second report needs verified evidence.");
        }
        const button = action === "reject" ? "human-reject" : "human-submit";
        expect(
          wrapper.get(`[data-testid="${button}"]`).attributes("disabled"),
        ).toBeUndefined();
        api.answer.mockReturnValueOnce(
          new Promise((resolve, reject) => {
            finishCurrent = resolve;
            failCurrent = reject;
          }),
        );
        await wrapper.get(`[data-testid="${button}"]`).trigger("click");
        await wrapper.get(`[data-testid="${button}"]`).trigger("click");
        expect(api.answer).toHaveBeenCalledTimes(firstCallCount + 1);
        expect(api.answer).toHaveBeenLastCalledWith(
          "run-2",
          "q2",
          expect.objectContaining({ action }),
        );
        await wrapper
          .findAll("button")
          .filter((button) => button.text() === "v2.details")[0]
          .trigger("click");
        await flushPromises();
        await wrapper.get('[data-testid="human-answer"]').setValue("Asia");
        expect(
          wrapper.get('[data-testid="human-submit"]').attributes("disabled"),
        ).toBeDefined();
        await wrapper.get('[data-testid="human-submit"]').trigger("click");
        expect(api.answer).toHaveBeenCalledTimes(firstCallCount + 1);
        expect(
          wrapper.get('[data-testid="pause-run"]').attributes("disabled"),
        ).toBeDefined();
        await wrapper
          .findAll("button")
          .filter((button) => button.text() === "v2.details")[1]
          .trigger("click");
        await flushPromises();
        if (action === "answer")
          await wrapper.get('[data-testid="human-answer"]').setValue("Europe");
        else if (action === "approve") {
          await wrapper.get('[data-testid="human-preview"]').trigger("click");
          await flushPromises();
        } else
          await wrapper
            .get('[data-testid="human-feedback"]')
            .setValue("Second report needs verified evidence.");
        if (outcome === "failure") {
          failCurrent(new Error("Current response failed"));
          await flushPromises();
          expect(wrapper.text()).toContain("Current response failed");
        }
        if (oldOutcome === "success")
          finishOld({ ...task, status: "completed" });
        else failOld(new Error("Old operation failed"));
        await flushPromises();
        expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
          "Second question",
        );
        expect(wrapper.text()).not.toContain("Old operation failed");
        if (outcome === "success") {
          expect(
            wrapper.get(`[data-testid="${button}"]`).attributes("disabled"),
          ).toBeDefined();
          expect(
            wrapper
              .findAll("button")
              .find((item) => item.text() === "page.refresh")!
              .attributes("disabled"),
          ).toBeDefined();
          finishCurrent({
            ...second,
            todos: [
              { ...secondTodo, status: "answered", response: { action } },
            ],
          });
          await flushPromises();
        } else {
          expect(wrapper.text()).toContain("Current response failed");
          expect(
            wrapper.get(`[data-testid="${button}"]`).attributes("disabled"),
          ).toBeUndefined();
          api.answer.mockResolvedValueOnce({
            ...second,
            todos: [
              { ...secondTodo, status: "answered", response: { action } },
            ],
          });
          await wrapper.get(`[data-testid="${button}"]`).trigger("click");
          await flushPromises();
          expect(wrapper.text()).not.toContain("Current response failed");
        }
        expect(
          wrapper.get('[data-testid="pause-run"]').attributes("disabled"),
        ).toBeUndefined();
      } finally {
        finishOld(task);
        finishCurrent?.(second);
        await flushPromises();
        wrapper.unmount();
      }
    },
  );
  it("releases answer busy state so the next request and controls can run", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    const todo = {
      todo_id: "q",
      task_run_id: "q",
      kind: "question",
      question: "Which region?",
      status: "pending",
      constraints: { choices: [], max_length: 100 },
    };
    api.run.mockResolvedValue({
      ...run,
      todos: [todo],
      control: { status: "active", revision: 0 },
    });
    api.answer.mockResolvedValue({
      ...run,
      todos: [{ ...todo, status: "completed", response: { answer: "Asia" } }],
      control: { status: "active", revision: 0 },
    });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    try {
      await flushPromises();
      await wrapper.get('[data-testid="human-answer"]').setValue("Asia");
      await wrapper.get('[data-testid="human-submit"]').trigger("click");
      await flushPromises();
      expect(
        wrapper.get('[data-testid="pause-run"]').attributes("disabled"),
      ).toBeUndefined();
    } finally {
      wrapper.unmount();
    }
  });
  it.each(["success", "failure"])(
    "fences a late human answer after switching runs (%s)",
    async (outcome) => {
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      const todo = {
        todo_id: "q",
        task_run_id: "q",
        kind: "question",
        question: "Which region?",
        status: "pending",
        constraints: { choices: [], max_length: 100 },
      };
      api.run.mockImplementation(async (id: string) =>
        id === "run-1"
          ? { ...run, todos: [todo] }
          : {
              ...run,
              run_id: "run-2",
              snapshot: {
                ...run.snapshot,
                definition: { ...asset.definition, name: "Second report" },
              },
            },
      );
      api.runs.mockResolvedValue({
        runs: [
          runOverview,
          {
            ...runOverview,
            run_id: "run-2",
            snapshot: {
              ...runOverview.snapshot,
              definition: { name: "Second report" },
            },
          },
        ],
      });
      let finish!: (value: any) => void;
      let fail!: (err: Error) => void;
      api.answer.mockReturnValue(
        new Promise((resolve, reject) => {
          finish = resolve;
          fail = reject;
        }),
      );
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      try {
        await flushPromises();
        await wrapper.get('[data-testid="human-answer"]').setValue("Asia");
        await wrapper.get('[data-testid="human-submit"]').trigger("click");
        await wrapper.get('[data-testid="human-submit"]').trigger("click");
        expect(api.answer).toHaveBeenCalledTimes(1);
        await wrapper
          .findAll("button")
          .filter((button) => button.text() === "v2.details")[1]
          .trigger("click");
        await flushPromises();
        if (outcome === "success")
          finish({ ...run, todos: [{ ...todo, status: "completed" }] });
        else fail(new Error("Old answer failed"));
        await flushPromises();
        expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
          "Second report",
        );
        expect(wrapper.text()).not.toContain("Old answer failed");
      } finally {
        wrapper.unmount();
      }
    },
  );
  it.each([
    ["runs", "success"],
    ["events", "success"],
    ["runs", "failure"],
    ["events", "failure"],
  ])(
    "keeps polling after control supersedes %s (%s)",
    async (phase, outcome) => {
      vi.useFakeTimers();
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      api.run.mockResolvedValue({
        ...run,
        cursor: 10,
        control: { status: "active", revision: 0 },
      });
      api.events.mockResolvedValue({
        cursor: 10,
        reset: false,
        events: [],
        snapshot: null,
      });
      let finishRead!: (value: any) => void;
      let finish!: (value: any) => void;
      let fail!: (error: Error) => void;
      const read = new Promise((resolve) => {
        finishRead = resolve;
      });
      api.control.mockReturnValue(
        new Promise((resolve, reject) => {
          finish = resolve;
          fail = reject;
        }),
      );
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      try {
        await flushPromises();
        api[phase as "runs" | "events"].mockReturnValueOnce(read);
        await vi.advanceTimersByTimeAsync(2000);
        await wrapper.get('[data-testid="pause-run"]').trigger("click");
        await wrapper.get('[data-testid="pause-run"]').trigger("click");
        expect(api.control).toHaveBeenCalledTimes(1);
        finishRead(
          phase === "runs"
            ? { runs: [runOverview] }
            : { cursor: 10, reset: false, events: [], snapshot: null },
        );
        await flushPromises();
        if (outcome === "success")
          finish({
            ...run,
            cursor: 11,
            control: { status: "paused", revision: 1 },
          });
        else fail(new Error("Control storage unavailable"));
        await flushPromises();
        const previous = api.events.mock.calls.length;
        await vi.advanceTimersByTimeAsync(4000);
        await flushPromises();
        expect(api.events.mock.calls.length).toBeGreaterThan(previous);
      } finally {
        wrapper.unmount();
      }
    },
  );

  it.each(["success", "failure"])(
    "ignores an old control %s after selecting another run",
    async (outcome) => {
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      const second = {
        ...run,
        run_id: "run-2",
        snapshot: {
          ...run.snapshot,
          definition: { ...asset.definition, name: "Second report" },
        },
      };
      api.runs.mockResolvedValue({
        runs: [runOverview, { ...runOverview, run_id: "run-2" }],
      });
      api.run.mockImplementation((id) =>
        Promise.resolve(id === "run-1" ? run : second),
      );
      let finish!: (value: any) => void;
      let fail!: (error: Error) => void;
      api.control.mockReturnValueOnce(
        new Promise((resolve, reject) => {
          finish = resolve;
          fail = reject;
        }),
      );
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      await flushPromises();
      await wrapper.get('[data-testid="pause-run"]').trigger("click");
      await wrapper
        .findAll("button")
        .filter((button) => button.text() === "v2.details")[1]
        .trigger("click");
      await flushPromises();
      expect(
        wrapper.get('[data-testid="pause-run"]').attributes("disabled"),
      ).toBeUndefined();
      if (outcome === "success")
        finish({ ...run, control: { status: "paused", revision: 1 } });
      else fail(new Error("Old control failed"));
      await flushPromises();
      expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
        "Second report",
      );
      expect(wrapper.text()).not.toContain("Old control failed");
      wrapper.unmount();
    },
  );

  it("explicitly retries original inputs on HTTP deployments and blocks unknown tools", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    const stopped = {
      ...run,
      status: "failed",
      control: { status: "active", revision: 0 },
      snapshot: {
        ...run.snapshot,
        input_artifacts: [{ artifact_id: "source", version: 1 }],
      },
    };
    api.run.mockResolvedValue(stopped);
    api.retry.mockRejectedValue(new Error("Temporary retry failure"));
    vi.stubGlobal("crypto", {});
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    try {
      await flushPromises();
      for (let count = 0; count < 2; count++) {
        await wrapper.get('[data-testid="retry-task"]').trigger("click");
        expect(wrapper.get(".dialog").text()).toContain('"version": 1');
        await wrapper
          .get('[data-testid="confirm-run-control"]')
          .trigger("click");
        await flushPromises();
      }
      expect(api.retry.mock.calls[0]).toEqual(api.retry.mock.calls[1]);
      expect(api.retry).toHaveBeenCalledWith("run-1", {
        attempt_id: "attempt-1",
        input_policy: "original",
        request_id: expect.any(String),
      });
      api.run.mockResolvedValue({
        ...stopped,
        tool_calls: [{ attempt_id: "attempt-1", status: "unknown" }],
      });
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "v2.details")!
        .trigger("click");
      await flushPromises();
      expect(
        wrapper.get('[data-testid="retry-task"]').attributes("disabled"),
      ).toBeDefined();
      expect(wrapper.get('[data-testid="run-controls"]').text()).toContain(
        "controls.resolveFirst",
      );
    } finally {
      wrapper.unmount();
      vi.unstubAllGlobals();
    }
  });
  it("persists a whole-run pause and continues the original attempt", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    api.run.mockResolvedValue({
      ...run,
      control: { status: "active", revision: 0 },
    });
    api.control.mockResolvedValueOnce({
      ...run,
      control: { status: "paused", revision: 1 },
    });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    await flushPromises();
    await wrapper.get('[data-testid="pause-run"]').trigger("click");
    await flushPromises();
    expect(api.control).toHaveBeenCalledWith(
      "run-1",
      expect.objectContaining({
        action: "pause",
        request_id: expect.any(String),
        expected_revision: 0,
      }),
    );
    expect(wrapper.get('[data-testid="run-controls"]').text()).toContain(
      "controls.inFlight",
    );
    api.control.mockResolvedValueOnce({
      ...run,
      control: { status: "active", revision: 2 },
    });
    await wrapper.get('[data-testid="continue-run"]').trigger("click");
    await flushPromises();
    expect(api.control).toHaveBeenLastCalledWith(
      "run-1",
      expect.objectContaining({ action: "continue", expected_revision: 1 }),
    );
    wrapper.unmount();
  });
  it.each(["success", "failure"])(
    "keeps recovery and resolution owned by their selected task after an older %s",
    async (outcome) => {
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      const stopped = {
        ...run,
        status: "blocked",
        cursor: 10,
        recovery: { available: true, boundary: "tools_started" },
        tool_calls: [
          {
            record_id: "call-one",
            call_id: "external-one",
            name: "publish",
            status: "unknown",
            recovery: {},
            available_actions: ["retry"],
          },
        ],
      };
      const second = {
        ...stopped,
        run_id: "run-2",
        snapshot: {
          ...run.snapshot,
          definition: { ...asset.definition, name: "Second report" },
        },
        tool_calls: [{ ...stopped.tool_calls[0], record_id: "call-two" }],
      };
      api.runs.mockResolvedValue({
        runs: [runOverview, { ...runOverview, run_id: "run-2" }],
      });
      api.run.mockImplementation(async (id) =>
        id === "run-1" ? stopped : second,
      );
      let finishFirst!: (value: any) => void;
      let failFirst!: (error: Error) => void;
      let finishSecond!: (value: any) => void;
      const firstRecovery = new Promise((resolve, reject) => {
        finishFirst = resolve;
        failFirst = reject;
      });
      const secondRecovery = new Promise((resolve) => {
        finishSecond = resolve;
      });
      api.recover.mockImplementation((id) =>
        id === "run-1" ? firstRecovery : secondRecovery,
      );
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      try {
        await flushPromises();
        await wrapper.get('[data-testid="recover-attempt"]').trigger("click");
        let form = wrapper.get('[data-testid="tool-resolution"]');
        await form.get("select").setValue("retry");
        await form
          .get('[data-testid="resolution-evidence"]')
          .setValue("Owner chose a retry.");
        expect(form.get("button").attributes("disabled")).toBeDefined();
        await form.get("button").trigger("click");
        expect(api.resolveTool).not.toHaveBeenCalled();
        await wrapper
          .findAll("button")
          .filter((button) => button.text() === "v2.details")[1]
          .trigger("click");
        await flushPromises();
        await wrapper.get('[data-testid="recover-attempt"]').trigger("click");
        expect(api.recover).toHaveBeenCalledTimes(2);
        if (outcome === "success")
          finishFirst({ ...stopped, status: "completed" });
        else failFirst(new Error("Old recovery failed"));
        await flushPromises();
        form = wrapper.get('[data-testid="tool-resolution"]');
        await form.get("select").setValue("retry");
        await form
          .get('[data-testid="resolution-evidence"]')
          .setValue("Owner chose the second retry.");
        expect(form.get("button").attributes("disabled")).toBeDefined();
        await form.get("button").trigger("click");
        expect(api.resolveTool).not.toHaveBeenCalled();
        expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
          "Second report",
        );
        expect(wrapper.text()).not.toContain("Old recovery failed");
        finishSecond(second);
        await flushPromises();
        form = wrapper.get('[data-testid="tool-resolution"]');
        await form.get("select").setValue("retry");
        await form
          .get('[data-testid="resolution-evidence"]')
          .setValue("Owner chose the second retry.");
        expect(form.get("button").attributes("disabled")).toBeUndefined();
        api.resolveTool.mockResolvedValueOnce({
          ...second,
          status: "running",
          tool_calls: [],
        });
        await form.get("button").trigger("click");
        await flushPromises();
        expect(api.resolveTool).toHaveBeenCalledTimes(1);
      } finally {
        wrapper.unmount();
      }
    },
  );
  it.each([
    ["runs", "success"],
    ["events", "success"],
    ["runs", "failure"],
    ["events", "failure"],
  ])(
    "resumes polling after resolution supersedes a pending %s read (%s)",
    async (phase, outcome) => {
      vi.useFakeTimers();
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      const stopped = {
        ...run,
        cursor: 10,
        status: "blocked",
        tool_calls: [
          {
            record_id: "unknown-one",
            call_id: "call-one",
            name: "publish",
            status: "unknown",
            recovery: {},
            available_actions: ["retry"],
          },
        ],
      };
      api.run.mockResolvedValue(stopped);
      api.events.mockResolvedValue({
        cursor: 10,
        reset: false,
        events: [],
        snapshot: null,
      });
      let finishRead!: (value: any) => void;
      const pendingRead = new Promise((resolve) => {
        finishRead = resolve;
      });
      let finishResolution!: (value: any) => void;
      let failResolution!: (error: Error) => void;
      api.resolveTool.mockReturnValue(
        new Promise((resolve, reject) => {
          finishResolution = resolve;
          failResolution = reject;
        }),
      );
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      try {
        await flushPromises();
        api[phase as "runs" | "events"].mockReturnValueOnce(pendingRead);
        await vi.advanceTimersByTimeAsync(2000);
        const form = wrapper.get('[data-testid="tool-resolution"]');
        await form.get("select").setValue("retry");
        await form
          .get('[data-testid="resolution-evidence"]')
          .setValue("Owner confirmed a new execution is required.");
        await form.get("button").trigger("click");
        await flushPromises();
        expect(api.resolveTool).toHaveBeenCalledTimes(1);
        finishRead(
          phase === "runs"
            ? { runs: [runOverview] }
            : { cursor: 10, reset: false, events: [], snapshot: null },
        );
        await flushPromises();
        if (outcome === "success") finishResolution({ ...run, cursor: 11 });
        else failResolution(new Error("Resolution temporarily unavailable"));
        await flushPromises();
        const readsBefore = api.events.mock.calls.length;
        api.events.mockResolvedValue({
          cursor: 12,
          reset: false,
          events: [{ cursor: 12 }],
          snapshot: {
            ...run,
            cursor: 12,
            status: "completed",
            result: { text: "Resolved final evidence" },
          },
        });
        await vi.advanceTimersByTimeAsync(4000);
        await flushPromises();
        expect(api.events.mock.calls.length).toBeGreaterThan(readsBefore);
        expect(wrapper.get('[data-testid="result-text"]').text()).toBe(
          "Resolved final evidence",
        );
      } finally {
        wrapper.unmount();
      }
    },
  );
  it.each(["success", "failure"])(
    "keeps resolution loading and a late %s response with its original run",
    async (outcome) => {
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      const stopped = {
        ...run,
        status: "blocked",
        tool_calls: [
          {
            record_id: "call-one",
            call_id: "external-one",
            name: "publish",
            status: "unknown",
            recovery: {},
            available_actions: ["retry"],
          },
        ],
      };
      const second = {
        ...stopped,
        run_id: "run-2",
        snapshot: {
          ...run.snapshot,
          definition: { ...asset.definition, name: "Second report" },
        },
        tool_calls: [{ ...stopped.tool_calls[0], record_id: "call-two" }],
      };
      api.runs.mockResolvedValue({
        runs: [runOverview, { ...runOverview, run_id: "run-2" }],
      });
      api.run.mockImplementation((id) =>
        Promise.resolve(id === "run-1" ? stopped : second),
      );
      let finish!: (value: any) => void;
      let fail!: (error: Error) => void;
      api.resolveTool.mockReturnValueOnce(
        new Promise((resolve, reject) => {
          finish = resolve;
          fail = reject;
        }),
      );
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      await flushPromises();
      let form = wrapper.get('[data-testid="tool-resolution"]');
      await form.get("select").setValue("retry");
      await form
        .get('[data-testid="resolution-evidence"]')
        .setValue("Owner explicitly requested retry.");
      await form.get("button").trigger("click");
      await form.get("button").trigger("click");
      expect(api.resolveTool).toHaveBeenCalledTimes(1);
      await wrapper
        .findAll("button")
        .filter((button) => button.text() === "v2.details")[1]
        .trigger("click");
      await flushPromises();
      form = wrapper.get('[data-testid="tool-resolution"]');
      await form.get("select").setValue("retry");
      await form
        .get('[data-testid="resolution-evidence"]')
        .setValue("Retry the second operation.");
      expect(form.get("button").attributes("disabled")).toBeUndefined();
      api.resolveTool.mockResolvedValueOnce({
        ...second,
        status: "running",
        tool_calls: [],
      });
      await form.get("button").trigger("click");
      await flushPromises();
      if (outcome === "success") finish({ ...stopped, status: "completed" });
      else fail(new Error("Old resolution failed"));
      await flushPromises();
      expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
        "Second report",
      );
      expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
        "v2.states.running",
      );
      expect(wrapper.text()).not.toContain("Old resolution failed");
      wrapper.unmount();
    },
  );
  it.each(["confirm_result", "reconcile"])(
    "shows a saved paused %s decision and continues the original attempt",
    async (action) => {
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      const stopped = {
        ...run,
        status: "interrupted",
        control: { status: "paused", revision: 1, root_run_id: "run-1" },
        cursor: 1,
        tool_calls: [
          {
            record_id: "original-call",
            call_id: "external-call",
            name: "publish",
            status: "unknown",
            attempt_id: "attempt-1",
            args: { title: "Original report" },
            recovery: {},
            available_actions: ["confirm_result", "reconcile"],
          },
        ],
      };
      const waiting = {
        ...stopped,
        cursor: 2,
        state: { ...stopped.state, resolution_pending: true },
        tool_calls: [
          {
            ...stopped.tool_calls[0],
            status: action === "reconcile" ? "unknown" : "result_saved",
            recovery: {
              decisions: [
                { request_id: "saved-decision", request: { action } },
              ],
            },
            available_actions: [],
          },
        ],
      };
      api.run.mockResolvedValue(stopped);
      api.resolveTool.mockResolvedValueOnce(waiting);
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      await flushPromises();
      const form = wrapper.get('[data-testid="tool-resolution"]');
      await form.get("select").setValue(action);
      await form
        .get('[data-testid="resolution-evidence"]')
        .setValue("Verified remote receipt");
      if (action === "confirm_result") {
        await form
          .get('[data-testid="resolution-result"]')
          .setValue("Saved original result");
      }
      await form.get("button").trigger("click");
      await flushPromises();
      expect(wrapper.get('[data-testid="resolution-saved"]').text()).toContain(
        "resolution.savedWaiting",
      );
      expect(wrapper.text()).toContain("saved-decision");
      expect(wrapper.find('[data-testid="tool-resolution"]').exists()).toBe(
        false,
      );
      api.control.mockResolvedValueOnce({
        ...waiting,
        status: "completed",
        control: { status: "active", revision: 2, root_run_id: "run-1" },
        state: { ...waiting.state, resolution_pending: false },
        cursor: 3,
        tool_calls: [{ ...waiting.tool_calls[0], status: "applied" }],
      });
      await wrapper.get('[data-testid="continue-run"]').trigger("click");
      await flushPromises();
      expect(api.control).toHaveBeenCalledWith(
        "run-1",
        expect.objectContaining({ action: "continue" }),
      );
      expect(api.resolveTool).toHaveBeenCalledTimes(1);
      expect(api.recover).not.toHaveBeenCalled();
      expect(wrapper.find('[data-testid="resolution-saved"]').exists()).toBe(
        false,
      );
      expect(wrapper.text()).toContain("attempt-1");
      wrapper.unmount();
    },
  );
  it("requires evidence and an explicit retry, then preserves the original call association", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    const stopped = {
      ...run,
      status: "blocked",
      cursor: 1,
      tool_calls: [
        {
          record_id: "original-call",
          call_id: "external-call",
          name: "publish",
          status: "unknown",
          attempt_id: "attempt-1",
          args: { title: "Original report" },
          recovery: { queries: [{ evidence: "Remote receipt unavailable" }] },
          available_actions: ["retry", "confirm_result", "confirm_failure"],
        },
      ],
    };
    api.run.mockResolvedValue(stopped);
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Remote receipt unavailable");
    expect(wrapper.text()).toContain("Original report");
    const form = wrapper.get('[data-testid="tool-resolution"]');
    expect(form.get("button").attributes("disabled")).toBeDefined();
    await form.get("select").setValue("retry");
    await form
      .get('[data-testid="resolution-evidence"]')
      .setValue(
        "Owner checked remote logs and explicitly chose a new execution.",
      );
    api.resolveTool.mockResolvedValue({
      ...stopped,
      status: "running",
      cursor: 2,
      tool_calls: [
        {
          ...stopped.tool_calls[0],
          recovery: { replacement_record_id: "replacement-call" },
          available_actions: [],
        },
      ],
    });
    await form.get("button").trigger("click");
    await flushPromises();
    expect(api.resolveTool).toHaveBeenCalledWith(
      "run-1",
      "original-call",
      expect.objectContaining({
        action: "retry",
        evidence:
          "Owner checked remote logs and explicitly chose a new execution.",
        request_id: expect.any(String),
      }),
    );
    expect(wrapper.text()).toContain("replacement-call");
    expect(wrapper.find('[data-testid="tool-resolution"]').exists()).toBe(
      false,
    );
    wrapper.unmount();
  });
  it.each([
    ["runs", "success"],
    ["events", "success"],
    ["runs", "failure"],
    ["events", "failure"],
  ])(
    "continues polling after recovery %s read is superseded with %s",
    async (phase, outcome) => {
      vi.useFakeTimers();
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      api.run.mockResolvedValue({
        ...run,
        cursor: 10,
        status: "interrupted",
        recovery: { available: true, boundary: "response" },
      });
      let finishRead!: (value: any) => void;
      const pendingRead = new Promise((resolve) => {
        finishRead = resolve;
      });
      let finishRecovery!: (value: any) => void;
      let failRecovery!: (error: Error) => void;
      api.recover.mockReturnValue(
        new Promise((resolve, reject) => {
          finishRecovery = resolve;
          failRecovery = reject;
        }),
      );
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      try {
        await flushPromises();
        api[phase as "runs" | "events"].mockReturnValueOnce(pendingRead);
        await vi.advanceTimersByTimeAsync(2000);
        await wrapper.get('[data-testid="recover-attempt"]').trigger("click");
        await flushPromises();
        expect(api.recover).toHaveBeenCalledWith("run-1");
        finishRead(
          phase === "runs"
            ? { runs: [runOverview] }
            : {
                cursor: 10,
                reset: false,
                events: [],
                snapshot: { ...run, status: "failed", cursor: 10 },
              },
        );
        await flushPromises();
        const readsBeforeRecovery = api.events.mock.calls.length;
        await vi.advanceTimersByTimeAsync(2000);
        expect(api.events).toHaveBeenCalledTimes(readsBeforeRecovery);
        if (outcome === "success") finishRecovery({ ...run, cursor: 11 });
        else failRecovery(new Error("Recovery unavailable"));
        await flushPromises();
        if (outcome === "failure")
          expect(wrapper.text()).toContain("Recovery unavailable");
        api.events.mockResolvedValue({
          cursor: 12,
          reset: false,
          events: [{ cursor: 12 }],
          snapshot: {
            ...run,
            cursor: 12,
            status: "completed",
            result: { text: "Recovered result" },
          },
        });
        await vi.advanceTimersByTimeAsync(2000);
        await flushPromises();
        expect(api.events).toHaveBeenCalledWith(
          "run-1",
          outcome === "success" ? 11 : 10,
        );
        expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
          "v2.states.completed",
        );
        expect(wrapper.get('[data-testid="result-text"]').text()).toBe(
          "Recovered result",
        );
      } finally {
        wrapper.unmount();
      }
    },
  );

  it("keeps a new run read owned while an older recovery finishes", async () => {
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
      snapshot: {
        ...run.snapshot,
        definition: { ...asset.definition, name: "Second run" },
      },
    };
    api.runs.mockResolvedValue({ runs: [runOverview, next] });
    let finishRun!: (value: any) => void;
    api.run.mockImplementation(async (id: string) =>
      id === "run-1"
        ? {
            ...run,
            cursor: 10,
            status: "interrupted",
            recovery: { available: true, boundary: "response" },
          }
        : new Promise((resolve) => {
            finishRun = resolve;
          }),
    );
    let finishRecovery!: (value: any) => void;
    api.recover.mockReturnValue(
      new Promise((resolve) => {
        finishRecovery = resolve;
      }),
    );
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    try {
      await flushPromises();
      await wrapper.get('[data-testid="recover-attempt"]').trigger("click");
      await wrapper.findAll(".workflow-run-list button")[1].trigger("click");
      await flushPromises();
      finishRecovery({ ...run, cursor: 11 });
      await flushPromises();
      await wrapper.get('[data-testid="recover-attempt"]').trigger("click");
      expect(api.recover).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(2000);
      expect(api.events).not.toHaveBeenCalled();
      finishRun(next);
      await flushPromises();
      api.events.mockResolvedValue({
        cursor: 21,
        reset: false,
        events: [{ cursor: 21 }],
        snapshot: {
          ...next,
          cursor: 21,
          status: "completed",
          result: { text: "Second completed result" },
        },
      });
      await vi.advanceTimersByTimeAsync(2000);
      expect(api.events).toHaveBeenCalledWith("run-2", 20);
      expect(router.currentRoute.value.query.workflow_run).toBe("run-2");
      expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
        "Second run",
      );
      expect(wrapper.get('[data-testid="result-text"]').text()).toBe(
        "Second completed result",
      );
    } finally {
      wrapper.unmount();
    }
  });

  it("continues the selected original attempt and explains a started-tool block", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    api.run.mockResolvedValue({
      ...run,
      status: "interrupted",
      recovery: { available: true, boundary: "response" },
    });
    api.recover.mockResolvedValue({ ...run });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    await flushPromises();
    expect(wrapper.find('[data-testid="recovery-info"]').text()).toContain(
      "recovery.sameAttempt",
    );
    await wrapper.find('[data-testid="recover-attempt"]').trigger("click");
    await flushPromises();
    expect(api.recover).toHaveBeenCalledWith("run-1");
    expect(api.start).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="run-detail"]').text()).toContain(
      "attempt-1",
    );
    wrapper.unmount();
    api.run.mockResolvedValue({
      ...run,
      status: "interrupted",
      recovery: {
        available: false,
        boundary: "tools_started",
        reason: "Result requires confirmation",
      },
    });
    const blockedRouter = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await blockedRouter.push("/?workflow_run=run-1");
    const blocked = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [blockedRouter], stubs },
    });
    await flushPromises();
    expect(blocked.find('[data-testid="recover-attempt"]').exists()).toBe(
      false,
    );
    expect(blocked.find('[data-testid="recovery-info"]').text()).toContain(
      "Result requires confirmation",
    );
    blocked.unmount();
  });
  beforeEach(() => {
    vi.resetAllMocks();
    api.readiness.mockResolvedValue({ ready: true, issues: [], roles: [] });
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
    api.runs.mockResolvedValue({ runs: [runOverview] });
    api.history.mockResolvedValue({
      records: [
        {
          source: { kind: "v2_run", id: "done-run" },
          engine: "v2",
          title: "Historical report",
          status: "completed",
        },
      ],
      next_offset: null,
    });
    api.run.mockResolvedValue(run);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows original remote thoughts as observations tied to their attempt", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    api.run.mockResolvedValue({
      ...run,
      status: "awaiting_result",
      process: [
        {
          record_id: "remote-record-1",
          kind: "remote_event",
          task_id: "task-1",
          attempt_id: "attempt-1",
          member_id: "member-1",
          data: {
            provider: "dify",
            event: {
              event: "agent_thought",
              id: "thought-original",
              thought: "Original provider reasoning.",
              tool: "evidence_lookup",
              tool_input: '{"query":"original evidence"}',
              observation: "Original tool observation.",
            },
          },
        },
      ],
    });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    await flushPromises();
    const record = wrapper.find('[data-testid="process-record"]');
    expect(record.find("summary").text()).toContain("progress.remoteEvent");
    expect(record.find("summary").text()).toContain("dify · agent_thought");
    expect(record.text()).toContain("attempt-1");
    const event = record.find('[data-testid="remote-event"]');
    expect(event.text()).toContain("thought-original");
    expect(event.text()).toContain("Original provider reasoning.");
    expect(event.text()).toContain("evidence_lookup");
    expect(event.text()).toContain("Original tool observation.");
    expect(wrapper.find('[data-testid="durable-tool-call"]').exists()).toBe(
      false,
    );
    wrapper.unmount();
  });

  it("keeps selected complete process records while polling overview-only history", async () => {
    vi.useFakeTimers();
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    const completeText = "Complete evidence. ".repeat(2000);
    api.run.mockResolvedValue({
      ...run,
      cursor: 10,
      process: [
        {
          record_id: "record-1",
          kind: "tool_result",
          task_id: "task-1",
          attempt_id: "attempt-1",
          member_id: "member-1",
          data: { name: "search", raw_result: { content: completeText } },
        },
      ],
    });
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
    expect(wrapper.find('[data-testid="process-record"]').text()).toContain(
      completeText,
    );
    await vi.advanceTimersByTimeAsync(4000);
    await flushPromises();
    expect(api.events).toHaveBeenLastCalledWith("run-1", 10);
    expect(api.run).toHaveBeenCalledTimes(1);
    expect(wrapper.find('[data-testid="process-record"]').text()).toContain(
      completeText,
    );
    wrapper.unmount();
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

  it("blocks a launch when local executor readiness is incompatible", async () => {
    api.readiness.mockResolvedValue({
      ready: false,
      roles: [],
      issues: [
        {
          path: "definition.runner_capabilities.0",
          code: "INCOMPATIBLE",
          message: "dify (workflow) does not support internal_checkpoint",
        },
      ],
    });
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
      .find((button) => button.text() === "v2.start")!
      .trigger("click");
    await flushPromises();
    expect(
      wrapper.get('[data-testid="runner-readiness-blocked"]').text(),
    ).toContain("definition.runner_capabilities.0");
    expect(wrapper.text()).toContain(
      "dify (workflow) does not support internal_checkpoint",
    );
    const launch = wrapper
      .findAll(".dialog button")
      .find((button) => button.text() === "v2.start")!;
    expect(launch.attributes("disabled")).toBeDefined();
    await launch.trigger("click");
    expect(api.start).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("saves the inline graph, corrects an input error and starts its fixed version", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/");
    roles.resources.mockResolvedValue({
      profiles: [{ id: "default" }],
      models: [{ id: "m", name: "Model" }],
      personas: [],
      tools: [],
      skills: [],
      knowledge_bases: [],
    });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "assets" },
      global: {
        plugins: [router],
        stubs: {
          ...stubs,
          WorkflowGraphCanvas: {
            props: ["definition"],
            template: '<div data-testid="graph-editor" />',
          },
        },
      },
    });
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "graph.create")!
      .trigger("click");
    expect(wrapper.find('[data-testid="graph-editor"]').exists()).toBe(true);
    await wrapper.find(".dialog input").setValue("My workflow");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "page.refresh")!
      .trigger("click");
    await flushPromises();
    expect(
      (wrapper.find(".dialog input").element as HTMLInputElement).value,
    ).toBe("My workflow");
    const saved = {
      asset_id: "inline-1",
      version: 3,
      definition: {
        name: "My workflow",
        required_inputs: ["input"],
        max_parallel: 4,
        tasks: [
          {
            id: "agent",
            name: "Agent",
            member: "agent",
            task: "{input}",
            role: { name: "Agent" },
            installation: { profile_id: "default" },
            inputs: { input: { input: "input" } },
          },
        ],
        edges: [],
      },
    };
    api.save.mockImplementation(async () => {
      api.list.mockResolvedValue({ assets: [saved] });
      return saved;
    });
    await wrapper
      .findAll(".dialog button")
      .find((button) => button.text() === "v2.save")!
      .trigger("click");
    await flushPromises();
    expect(api.save.mock.calls[0][1].definition.name).toBe("My workflow");
    expect(api.save.mock.calls[0][1].definition.tasks[0]).toMatchObject({
      role: { name: "Agent" },
      installation: { profile_id: "default" },
      inputs: { input: { input: "input" } },
      task: "{input}",
    });
    expect(
      api.save.mock.calls[0][1].definition.tasks[0].role_id,
    ).toBeUndefined();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "v2.start")!
      .trigger("click");
    await flushPromises();
    api.start.mockRejectedValueOnce({
      response: {
        data: {
          message: "Invalid run inputs",
          data: {
            fields: [
              { path: "inputs.input", message: "Provide non-empty text" },
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
    expect(wrapper.text()).toContain("inputs.input: Provide non-empty text");
    api.start.mockResolvedValue({ run_id: "inline-run" });
    await wrapper.find(".dialog input").setValue("energy");
    await wrapper
      .findAll(".dialog button")
      .find((button) => button.text() === "v2.start")!
      .trigger("click");
    await flushPromises();
    expect(api.start).toHaveBeenLastCalledWith("inline-1", {
      version: 3,
      inputs: { input: "energy" },
      source: "dashboard",
    });
    expect(wrapper.emitted("started")).toEqual([["inline-run"]]);
    expect(router.currentRoute.value.query.workflow_run).toBe("inline-run");
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

  it("keeps polling the newly selected run when an older list read finishes late", async () => {
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
      snapshot: {
        ...run.snapshot,
        definition: { ...asset.definition, name: "Second run" },
      },
    };
    const list = { runs: [{ ...run, cursor: 10 }, next] };
    api.runs.mockResolvedValue(list);
    let finishRun!: (value: any) => void;
    let finishList!: (value: any) => void;
    const pendingRun = new Promise((resolve) => {
      finishRun = resolve;
    });
    const pendingList = new Promise((resolve) => {
      finishList = resolve;
    });
    api.run.mockImplementation(async (id: string) =>
      id === "run-1" ? { ...run, cursor: 10 } : pendingRun,
    );
    api.events.mockResolvedValue({
      cursor: 20,
      reset: false,
      events: [],
      snapshot: null,
    });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    await flushPromises();
    api.runs.mockReturnValueOnce(pendingList);
    await vi.advanceTimersByTimeAsync(2000);
    await wrapper.findAll(".workflow-run-list button")[1].trigger("click");
    finishList(list);
    await flushPromises();
    finishRun(next);
    await flushPromises();
    expect(router.currentRoute.value.query.workflow_run).toBe("run-2");
    expect(wrapper.find('[data-testid="run-detail"]').text()).toContain(
      "Second run",
    );
    await vi.advanceTimersByTimeAsync(2000);
    expect(api.events).toHaveBeenLastCalledWith("run-2", 20);
    wrapper.unmount();
  });

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
