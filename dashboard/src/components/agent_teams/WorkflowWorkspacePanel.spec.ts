import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { draftApi } from "@/composables/useAssetDraft";
import { mockDrafts } from "./draftTestMocks";
import WorkflowWorkspacePanel from "./WorkflowWorkspacePanel.vue";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  save: vi.fn(),
  start: vi.fn(),
  readiness: vi.fn(),
  runs: vi.fn(),
  humanRequests: vi.fn(),
  history: vi.fn(),
  run: vi.fn(),
  artifact: vi.fn(),
  revise: vi.fn(),
  file: vi.fn(),
  storeFile: vi.fn(),
  events: vi.fn(),
  processPage: vi.fn(),
  runHistoryPage: vi.fn(),
  recover: vi.fn(),
  resolveTool: vi.fn(),
  control: vi.fn(),
  retry: vi.fn(),
  answer: vi.fn(),
  delete: vi.fn(),
}));
const roles = vi.hoisted(() => ({ list: vi.fn(), resources: vi.fn() }));
vi.mock("@/stores/customizer", () => ({
  useCustomizerStore: () => ({ isDark: false }),
}));
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
    required_inputs: ["topic"],
    result_task_id: "research",
    max_parallel: 1,
    tasks: [{
      id: "research",
      name: "Research",
      member: "research",
      role_id: "role-1",
      role_version: 1,
      task: "Research {topic}",
      inputs: { topic: { input: "topic" } },
    }],
    edges: [],
  },
};
const run = {
  run_id: "run-1",
  status: "running",
  snapshot: {
    definition: { ...asset.definition.tasks[0], name: asset.definition.name },
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

describe("workflow workspace", () => {
  it.each([
    ["all", true, "v2.emptyRuns"],
    ["active", false, "workbench.emptyFilter"],
    ["pending", false, "workbench.emptyFilter"],
  ] as const)(
    "keeps the %s empty state and refresh button stable during background polling",
    async (view, empty, message) => {
      vi.useFakeTimers();
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/");
      const data = {
        runs: empty ? [] : [{ ...runOverview, status: "completed" }],
      };
      api.runs.mockResolvedValue(data);
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      try {
        await flushPromises();
        await wrapper.get(`[data-testid="runs-${view}"]`).trigger("click");
        const notice = wrapper
          .findAll('[type="info"]')
          .find((item) => item.text() === message)!.element;
        const refresh = wrapper
          .findAll("button")
          .find((item) => item.text() === "page.refresh")!;
        let finish!: (value: typeof data) => void;
        api.runs.mockReturnValueOnce(
          new Promise((resolve) => {
            finish = resolve;
          }),
        );
        await vi.advanceTimersByTimeAsync(2000);
        expect(
          wrapper.findAll('[type="info"]').map((item) => item.element),
        ).toContain(notice);
        expect(refresh.attributes("disabled")).toBeUndefined();
        await vi.advanceTimersByTimeAsync(4000);
        expect(api.runs).toHaveBeenCalledTimes(2);
        finish(data);
        await flushPromises();
        expect(
          wrapper.findAll('[type="info"]').map((item) => item.element),
        ).toContain(notice);
      } finally {
        wrapper.unmount();
      }
    },
  );

  it("pauses hidden run polling in history and resumes it in the active view", async () => {
    vi.useFakeTimers();
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    api.run.mockResolvedValue({ ...run, cursor: 10 });
    api.events.mockResolvedValue({ cursor: 10, events: [], snapshot: null });
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    try {
      await flushPromises();
      await wrapper.get('[data-testid="runs-history"]').trigger("click");
      await flushPromises();
      const history = wrapper.getComponent({
        name: "UnifiedRunHistory",
      }).element;
      await vi.advanceTimersByTimeAsync(6000);
      expect(api.runs).toHaveBeenCalledTimes(1);
      expect(api.run).toHaveBeenCalledTimes(1);
      expect(api.events).not.toHaveBeenCalled();
      expect(api.history).toHaveBeenCalledTimes(1);
      expect(wrapper.getComponent({ name: "UnifiedRunHistory" }).element).toBe(
        history,
      );
      await wrapper.get('[data-testid="runs-active"]').trigger("click");
      await vi.advanceTimersByTimeAsync(2000);
      expect(api.runs).toHaveBeenCalledTimes(2);
      expect(api.events).toHaveBeenCalledWith("run-1", 10);
    } finally {
      wrapper.unmount();
    }
  });

  it("shows foreground loading for an explicit refresh without overlapping polls", async () => {
    vi.useFakeTimers();
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/");
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    try {
      await flushPromises();
      let finish!: (value: any) => void;
      api.runs.mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }),
      );
      const refresh = wrapper
        .findAll("button")
        .find((item) => item.text() === "page.refresh")!;
      await refresh.trigger("click");
      expect(refresh.attributes("disabled")).toBeDefined();
      await vi.advanceTimersByTimeAsync(4000);
      expect(api.runs).toHaveBeenCalledTimes(2);
      finish({ runs: [runOverview] });
      await flushPromises();
      expect(refresh.attributes("disabled")).toBeUndefined();
    } finally {
      wrapper.unmount();
    }
  });

  it("retains artifact read failures until the artifact can actually be loaded", async () => {
    vi.useFakeTimers();
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    api.run.mockResolvedValue({
      ...run,
      cursor: 10,
      result_ref: { artifact_id: "artifact-1", version: 1 },
    });
    api.events.mockResolvedValue({ cursor: 10, events: [], snapshot: null });
    api.artifact.mockRejectedValue(new Error("Artifact unavailable"));
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    try {
      await flushPromises();
      const notice = wrapper.get('[role="alert"]').element;
      let fail!: (error: Error) => void;
      api.artifact.mockReturnValueOnce(
        new Promise((_, reject) => {
          fail = reject;
        }),
      );
      await vi.advanceTimersByTimeAsync(2000);
      expect(wrapper.get('[role="alert"]').element).toBe(notice);
      fail(new Error("Artifact unavailable"));
      await flushPromises();
      expect(wrapper.get('[role="alert"]').element).toBe(notice);
      api.artifact.mockResolvedValue({
        artifact_id: "artifact-1",
        version: 1,
        content: { text: "Complete result" },
      });
      await vi.advanceTimersByTimeAsync(2000);
      expect(wrapper.find('[role="alert"]').exists()).toBe(false);
      expect(wrapper.get('[data-testid="result-text"]').text()).toBe(
        "Complete result",
      );
    } finally {
      wrapper.unmount();
    }
  });

  it("keeps a polling error visible until a refresh succeeds", async () => {
    vi.useFakeTimers();
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/");
    const wrapper = mount(WorkflowWorkspacePanel, {
      props: { mode: "runs" },
      global: { plugins: [router], stubs },
    });
    try {
      await flushPromises();
      api.runs.mockRejectedValueOnce(new Error("Run list unavailable"));
      await vi.advanceTimersByTimeAsync(2000);
      const notice = wrapper.get('[role="alert"]').element;
      let fail!: (error: Error) => void;
      api.runs.mockReturnValueOnce(
        new Promise((_, reject) => {
          fail = reject;
        }),
      );
      await vi.advanceTimersByTimeAsync(2000);
      expect(wrapper.get('[role="alert"]').element).toBe(notice);
      fail(new Error("Run list unavailable"));
      await flushPromises();
      expect(wrapper.get('[role="alert"]').element).toBe(notice);
      await vi.advanceTimersByTimeAsync(2000);
      expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    } finally {
      wrapper.unmount();
    }
  });

  it.each(["runs", "events", "artifact"] as const)(
    "ignores a pending %s polling failure after switching to history",
    async (phase) => {
      vi.useFakeTimers();
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/", component: slotStub }],
      });
      await router.push("/?workflow_run=run-1");
      api.run.mockResolvedValue({ ...run, cursor: 10 });
      if (phase === "artifact") {
        api.events.mockResolvedValue({
          cursor: 11,
          snapshot: {
            ...run,
            cursor: 11,
            result_ref: { artifact_id: "artifact-1", version: 1 },
          },
        });
      }
      const wrapper = mount(WorkflowWorkspacePanel, {
        props: { mode: "runs" },
        global: { plugins: [router], stubs },
      });
      try {
        await flushPromises();
        let fail!: (error: Error) => void;
        api[phase].mockReturnValueOnce(
          new Promise((_, reject) => {
            fail = reject;
          }),
        );
        await vi.advanceTimersByTimeAsync(2000);
        await wrapper.get('[data-testid="runs-history"]').trigger("click");
        await flushPromises();
        fail(new Error("Hidden run poll failed"));
        await flushPromises();
        expect(wrapper.text()).not.toContain("Hidden run poll failed");
        expect(
          wrapper.findComponent({ name: "UnifiedRunHistory" }).exists(),
        ).toBe(true);
      } finally {
        wrapper.unmount();
      }
    },
  );

  it.each(["workflows", "teams"] as const)("confirms deletion in %s", async (kind) => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/");
    const item = { ...asset, definition: kind === "teams" ? { name: "Team", kind: "autonomous", allowed_roles: [] } : asset.definition };
    api.list.mockResolvedValueOnce({ assets: [item] }).mockResolvedValue({ assets: [] });
    api.delete.mockResolvedValue({});
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "assets", assetKind: kind }, global: { plugins: [router], stubs } });
    await flushPromises();
    await wrapper.get('[data-testid="delete-asset"]').trigger("click");
    expect(wrapper.text()).toContain("deletion.impact");
    expect(api.delete).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="confirm-asset-deletion"]').trigger("click");
    await flushPromises();
    expect(api.delete).toHaveBeenCalledWith("asset-1");
    expect(wrapper.text()).not.toContain("Report · v1");
    wrapper.unmount();
  });
  it("answers a persisted request from the global inbox", async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: { template: "<div />" } }] });
    await router.push("/");
    const todo = { run_name: "Market research", task_name: "Select market", recommendation_source_name: "Forecast", todo_id: "todo-1", root_run_id: "run-1", task_run_id: "task-1", kind: "question", question: "Which region?", status: "pending", control_status: "active", constraints: { choices: [], max_length: 100 }, recommendation: {value: "Asia", reason: "Demand"}, recommendation_ref: { artifact_id: "source-1", version: 1 }, recommendation_fields: { value: "choice", reason: "reason" } };
    api.humanRequests.mockResolvedValueOnce({ requests: [todo] }).mockResolvedValue({ requests: [] });
    api.answer.mockResolvedValue(run);
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs } });
    await flushPromises();
    expect(wrapper.get('[data-testid="runs-pending"]').text()).toContain("1");
    await wrapper.get('[data-testid="runs-pending"]').trigger("click");
    expect(wrapper.get('[data-testid="human-inbox"]').text()).toContain("Demand");
    expect(wrapper.get('[data-testid="human-origin"]').text()).toContain("Market research");
    expect(wrapper.get('[data-testid="human-origin"]').text()).toContain("Select market");
    expect(wrapper.get('[data-testid="human-inbox"]').text()).toContain("Forecast");
    await wrapper.get('[data-testid="human-answer"]').setValue("Europe");
    await wrapper.get('[data-testid="human-submit"]').trigger("click");
    await flushPromises();
    expect(api.answer).toHaveBeenCalledWith("run-1", "todo-1", expect.objectContaining({ action: "answer", answer: "Europe" }));
    expect(wrapper.get('[data-testid="human-inbox"]').text()).not.toContain("Which region?");
    wrapper.unmount();
  });

  it("opens the exact recommendation task from the inbox in the workbench", async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/");
    const todo = { todo_id: "todo-1", root_run_id: "run-1", task_run_id: "question", kind: "question", question: "Choose a region", status: "pending", constraints: { choices: [], max_length: 100 }, recommendation_ref: { artifact_id: "source-1", version: 1 } };
    api.humanRequests.mockResolvedValue({ requests: [todo] });
    api.run.mockResolvedValue({ ...run, tasks: [
      { ...run, run_id: "question" },
      { ...run, run_id: "recommendation-task", result_ref: todo.recommendation_ref },
    ] });
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs: {
      ...stubs,
      WorkflowRunProcess: { props: ["run"], template: '<div data-testid="selected-process">{{ run.run_id }}</div>' },
    } } });
    try {
      await flushPromises();
      await wrapper.get('[data-testid="runs-pending"]').trigger("click");
      await wrapper.get('[data-testid="human-inbox"]').findAll("button").find((button) => button.text() === "human.openProcess")!.trigger("click");
      await flushPromises();
      expect(wrapper.find('[data-testid="human-inbox"]').exists()).toBe(false);
      expect(wrapper.get('[data-testid="runs-all"]').attributes("aria-pressed")).toBe("true");
      expect(wrapper.get('[data-testid="selected-process"]').text()).toBe("recommendation-task");
    } finally {
      wrapper.unmount();
    }
  });

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
  it("labels unpublished drafts and published assets with edits", async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/");
    vi.mocked(draftApi.list).mockResolvedValueOnce([
      { asset_id: "asset-1", revision: 2, base_version: 1, body: { definition: { name: "Edited" } } },
      { asset_id: "new-flow", revision: 1, base_version: null, body: { definition: { name: "Unpublished" } } },
    ]).mockResolvedValueOnce([
      { asset_id: "new-team", revision: 1, base_version: null, body: { definition: { kind: "autonomous", name: "Team draft" } } },
    ]);
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "assets" }, global: { plugins: [router], stubs } });
    await flushPromises();
    const draftCards = wrapper.findAll(".at-draft-card");
    expect(draftCards.map((card) => card.text())).toEqual([
      expect.stringContaining("Unpublished"),
      expect.stringContaining("Team draft"),
    ]);
    expect(draftCards.every((card) => card.text().includes("draft.unpublished"))).toBe(true);
    expect(wrapper.get('[data-testid="editing-draft"]').text()).toBe("draft.editing");
    expect(wrapper.findAll(".at-asset-card").find((card) => card.text().includes("Report"))?.text()).toContain("v1");
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
    expect(wrapper.text()).not.toContain("Report");
    await wrapper.setProps({ assetKind: "workflows" });
    expect(wrapper.text()).toContain("Report");
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
        workflow: { asset_id: "report", version: 1 },
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
  it("opens the selected static team call in its own scope", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: slotStub }],
    });
    await router.push("/?workflow_run=run-1");
    const call = {
      ...run,
      run_id: "team-call",
      snapshot: {
        ...run.snapshot,
        engine: "team_call_v2",
        definition: { name: "Fixed team" },
      },
      state: { ...run.state, child_scope_run_id: "team-scope" },
    };
    const coordinator = {
      ...run,
      run_id: "team-coordinator",
      snapshot: { ...run.snapshot, definition: { name: "Team coordinator" } },
    };
    api.run.mockResolvedValue({
      ...run,
      tasks: [call],
      scopes: [{ run_id: "team-scope", tasks: [coordinator] }],
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
        .vm.$emit("select", call.run_id);
      await flushPromises();
      await wrapper.get('[data-testid="open-child-workflow"]').trigger("click");
      await flushPromises();
      expect(wrapper.get('[data-testid="run-detail"]').text()).toContain(
        "Team coordinator",
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
          await wrapper.get('[data-testid="human-preview"]').trigger("click");
          await flushPromises();
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
        } else {
          await wrapper.get('[data-testid="human-preview"]').trigger("click");
          await flushPromises();
          await wrapper
            .get('[data-testid="human-feedback"]')
            .setValue("Second report needs verified evidence.");
        }
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
          10,
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
    vi.restoreAllMocks();
    vi.resetAllMocks();
    api.humanRequests.mockResolvedValue({ requests: [] });
    mockDrafts((id, body) => api.save(id, body));
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

  it("routes duplicate nested and loop observations to their own run", async () => {
    vi.useFakeTimers();
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=run-1");
    const child = { ...run, run_id: "child", node_id: "same", process: [], snapshot: { ...run.snapshot, definition: { name: "Child" } } };
    const round = { ...run, run_id: "round", node_id: "same", process: [], snapshot: { ...run.snapshot, definition: { name: "Round" } } };
    api.run.mockResolvedValue({ ...run, cursor: 10, tasks: [run], scopes: [{ run_id: "scope", tasks: [child] }], loops: [{ iterations: [{ tasks: [round] }] }] });
    const record = (id: string, text: string) => ({ record_id: id, run_id: id, attempt_id: "attempt-1", kind: "model_response", data: { text } });
    api.events.mockResolvedValue({ cursor: 11, reset: false, records: [record("child", "Child output"), record("round", "Round output")], snapshot: null });
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs: { ...stubs, WorkflowRunProgress: true } } });
    await flushPromises();
    await vi.advanceTimersByTimeAsync(4000);
    wrapper.findComponent({ name: "WorkflowRunProgress" }).vm.$emit("select", "child");
    await flushPromises();
    expect(wrapper.text()).toContain("Child output");
    expect(wrapper.text()).not.toContain("Round output");
    wrapper.findComponent({ name: "WorkflowRunProgress" }).vm.$emit("select", "round");
    await flushPromises();
    expect(wrapper.text()).toContain("Round output");
    expect(wrapper.text()).not.toContain("Child output");
    expect(wrapper.findAll('[data-testid="process-record"]')).toHaveLength(1);
    wrapper.unmount();
  });

  it("pages older observations and keeps them when a state snapshot arrives", async () => {
    vi.useFakeTimers();
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=run-1");
    const current = { record_id: "current", cursor: 10, run_id: "run-1", attempt_id: "attempt-1", kind: "model_response", data: { text: "Current" } };
    const old = { record_id: "old", cursor: 3, run_id: "run-1", attempt_id: "attempt-1", kind: "model_response", data: { text: "Older" } };
    api.run.mockResolvedValue({ ...run, cursor: 10, process: [current] });
    api.processPage.mockResolvedValue({ records: [old], next_before: null });
    api.events.mockResolvedValue({ cursor: 11, reset: true, records: [], snapshot: { ...run, cursor: 11, process: [current] } });
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs } });
    await flushPromises();
    const button = wrapper.findAll("button").find((element) => element.text() === "progress.loadOlder")!;
    await button.trigger("click");
    await flushPromises();
    expect(api.processPage).toHaveBeenCalledWith("run-1", "run-1", 10);
    expect(wrapper.text()).toContain("Older");
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(api.events).toHaveBeenCalledWith("run-1", 10);
    expect(wrapper.text()).toContain("Older");
    expect(wrapper.text()).toContain("Current");
    wrapper.unmount();
  });

  it("keeps the latest scroll intent when an older page finishes late", async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=run-1");
    api.run.mockResolvedValue({ ...run, cursor: 10, process: [] });
    let finish!: (page: any) => void;
    api.processPage.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs } });
    await flushPromises();
    const pane = wrapper.get(".workbench-process");
    Object.defineProperty(pane.element, "scrollHeight", { get: () => wrapper.text().includes("Older page") ? 1100 : 1000 });
    pane.element.scrollTop = 100;
    await wrapper.findAll("button").find((button) => button.text() === "progress.loadOlder")!.trigger("click");
    pane.element.scrollTop = 250;
    finish({records:[{record_id:"old",cursor:5,run_id:"run-1",attempt_id:"attempt-1",kind:"model_response",data:{text:"Older page"}}],next_before:null});
    await flushPromises();
    expect(pane.element.scrollTop).toBe(350);
    wrapper.unmount();
  });

  it("pages scoped message and event history without duplicating entries", async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=run-1");
    api.run.mockResolvedValue({ ...run, cursor: 100, event_history_before: 50, message_history_before: 60, attempt_history_before: 70, messages: [], events: [] });
    api.runHistoryPage.mockImplementation(async (_root: string, _task: string, kind: string) => kind === "events"
      ? { items: [{ cursor: 10, kind: "task.changed", data: { event: "Older event" } }], next_before: null }
      : kind === "attempts"
      ? { items: [{ cursor: 11, attempt_id: "older-attempt", status: "failed", state: { reason: "Original failure" }, snapshot: { inputs: {} } }], next_before: null }
      : { items: [{ cursor: 12, message_id: "older-message", sender_member_id: "one", recipient_member_id: "two", text: "Older message", artifact_refs: [] }], next_before: null });
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text() === "progress.loadMessages")!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Older message");
    expect(api.runHistoryPage).toHaveBeenCalledWith("run-1", "run-1", "messages", "60");
    await wrapper.findAll("button").find((button) => button.text() === "progress.loadEvents")!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Older event");
    expect(api.runHistoryPage).toHaveBeenCalledWith("run-1", "run-1", "events", "50");
    await wrapper.findAll("button").find((button) => button.text() === "progress.loadAttempts")!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Original failure");
    expect(api.runHistoryPage).toHaveBeenCalledWith("run-1", "run-1", "attempts", "70");
    wrapper.unmount();
  });

  it("keeps the applied event cursor when refreshing newer task state", async () => {
    vi.useFakeTimers();
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=run-1");
    const record = { record_id:"old-delivery", run_id:"run-1", cursor:11, attempt_id:"attempt-1", kind:"model_response", data:{text:"Read before refresh"} };
    api.run.mockResolvedValueOnce({ ...run, cursor:10 }).mockResolvedValue({ ...run, cursor:200, status:"completed", process:[] });
    api.events.mockResolvedValueOnce({ cursor:12, reset:false, events:[{cursor:11,kind:"process.model_response"},{cursor:12,kind:"task.completed"}], records:[record], snapshot:null })
      .mockResolvedValue({ cursor:12, reset:false, events:[], records:[], snapshot:null });
    const wrapper = mount(WorkflowWorkspacePanel, { props:{mode:"runs"}, global:{plugins:[router],stubs} });
    await flushPromises();
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(wrapper.text()).toContain("Read before refresh");
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(api.events).toHaveBeenLastCalledWith("run-1",12);
    wrapper.unmount();
  });

  it("keeps following the newest output and final response beyond 512 observations", async () => {
    vi.useFakeTimers();
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=run-1");
    const start = { record_id: "start", cursor: 10, run_id: "run-1", attempt_id: "attempt-1", kind: "model_delivery", data: { receipt_id: "delivery", status: "started", streaming: true } };
    const end = { record_id: "end", cursor: 611, run_id: "run-1", attempt_id: "attempt-1", kind: "model_delivery", data: { receipt_id: "delivery", status: "completed", text: "FINAL COMPLETION" } };
    const response = { record_id: "response", cursor: 612, run_id: "run-1", attempt_id: "attempt-1", kind: "model_response", data: { receipt_id: "delivery", text: "FINAL COMPLETION" } };
    api.run.mockResolvedValueOnce({ ...run, cursor: 10, process: [start] }).mockResolvedValue({ ...run, cursor: 613, status: "completed", process: [end, response] });
    let sequence = 0;
    api.events.mockImplementation(async (_id: string, after: number) => {
      if (sequence < 600) {
        const records = Array.from({ length: 100 }, () => {
          const current = sequence++;
          return { record_id: `delta-${current}`, cursor: 11 + current, run_id: "run-1", attempt_id: "attempt-1", kind: "model_delta", data: { receipt_id: "delivery", sequence: current, text: `CHUNK[${String(current).padStart(3, "0")}]` } };
        });
        return { cursor: records[records.length - 1].cursor, reset: false, records, messages: [], events: records.map((record) => ({ cursor: record.cursor, run_id: record.run_id, kind: "process.model_delta" })), snapshot: null };
      }
      if (sequence++ === 600) return { cursor: 613, reset: false, records: [end, response], messages: [], events: [{ cursor: 613, run_id: "run-1", kind: "run.completed" }], snapshot: null };
      return { cursor: after, reset: false, records: [], messages: [], events: [], snapshot: null };
    });
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs } });
    await flushPromises();
    for (let index = 0; index < 6; index++) { await vi.advanceTimersByTimeAsync(2000); await flushPromises(); }
    expect(wrapper.text()).toContain("CHUNK599");
    expect(wrapper.text()).not.toContain("CHUNK000");
    expect(wrapper.text()).toContain("progress.olderHistoryHidden");
    expect(wrapper.text()).not.toContain("progress.reloadLatest");
    expect(wrapper.getComponent({ name: "WorkflowRunProcess" }).props("run").process.length).toBeLessThanOrEqual(512);
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(wrapper.text()).toContain("FINAL COMPLETION");
    expect(wrapper.text()).toContain("progress.deliveryStates.completed");
    expect(wrapper.getComponent({ name: "WorkflowRunProcess" }).props("run").process.length).toBeLessThanOrEqual(512);
    wrapper.unmount();
  });

  it("preserves a historical window and scroll position until return to latest", async () => {
    vi.useFakeTimers();
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=run-1");
    const delta = (sequence: number, cursor: number) => ({ record_id: `delta-${sequence}`, cursor, run_id: "run-1", attempt_id: "attempt-1", kind: "model_delta", data: { receipt_id: "delivery", sequence, text: `CHUNK[${sequence}]` } });
    const initial = Array.from({ length: 512 }, (_, index) => delta(100 + index, 200 + index));
    const end = { record_id: "end", cursor: 1002, run_id: "run-1", attempt_id: "attempt-1", kind: "model_delivery", data: { receipt_id: "delivery", status: "completed", text: "FINISHED WHILE READING" } };
    const response = { record_id: "response", cursor: 1003, run_id: "run-1", attempt_id: "attempt-1", kind: "model_response", data: { receipt_id: "delivery", text: "FINISHED WHILE READING" } };
    api.run.mockResolvedValueOnce({ ...run, cursor: 1000, process: initial }).mockResolvedValue({ ...run, cursor: 1004, status: "completed", process: [end, response] });
    api.processPage.mockImplementation(async (_root: string, _task: string, before: number) => before === 200
      ? { records: Array.from({ length: 20 }, (_, index) => delta(80 + index, 180 + index)), next_before: "180" }
      : { records: [...initial.slice(-126), end, response], next_before: "586" });
    api.runHistoryPage.mockResolvedValue({ items: [], next_before: null });
    api.events.mockResolvedValueOnce({ cursor: 1004, reset: false, records: [delta(612, 1001), end, response], messages: [], events: [{ cursor: 1004, run_id: "run-1", kind: "run.completed" }], snapshot: null })
      .mockImplementation(async (_id: string, after: number) => ({ cursor: after, reset: false, records: [], messages: [], events: [], snapshot: null }));
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs } });
    await flushPromises();
    const pane = wrapper.get(".workbench-process");
    Object.defineProperties(pane.element, { scrollHeight: { value: 3000 }, clientHeight: { value: 400 } });
    pane.element.scrollTop = 250;
    await pane.trigger("scroll");
    await wrapper.findAll("button").find((button) => button.text() === "progress.loadOlder")!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("CHUNK80");
    expect(pane.element.scrollTop).toBe(250);
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(wrapper.text()).toContain("CHUNK80");
    expect(wrapper.text()).not.toContain("FINISHED WHILE READING");
    expect(wrapper.text()).toContain("progress.newActivity");
    expect(wrapper.text()).toContain("progress.historyWindow");
    expect(pane.element.scrollTop).toBe(250);
    await wrapper.findAll("button").find((button) => button.text() === "progress.reloadLatest")!.trigger("click");
    await flushPromises();
    expect(api.processPage).toHaveBeenLastCalledWith("run-1", "run-1", 1005);
    expect(wrapper.text()).toContain("FINISHED WHILE READING");
    expect(wrapper.text()).not.toContain("progress.newActivity");
    expect(pane.element.scrollTop).toBe(3000);
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(api.events).toHaveBeenLastCalledWith("run-1", 1004);
    wrapper.unmount();
  });

  it("keeps events arriving while returning to the latest window", async () => {
    vi.useFakeTimers();
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=run-1");
    const delta = (id: number, cursor: number, text: string) => ({ record_id: `delta-${id}`, cursor, run_id: "run-1", attempt_id: "attempt-1", kind: "model_delta", data: { receipt_id: "delivery", sequence: id, text } });
    const initial = Array.from({ length: 512 }, (_, index) => delta(index, index + 1, `OLD-${index};`));
    const first = delta(512, 1001, "BEFORE RETURN;");
    const second = delta(513, 1002, "DURING RETURN;");
    api.run.mockResolvedValue({ ...run, cursor: 1000, process: initial });
    let finish!: (page: any) => void;
    api.processPage.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    api.runHistoryPage.mockResolvedValue({ items: [], next_before: null });
    api.events.mockResolvedValueOnce({ cursor: 1001, reset: false, records: [first], messages: [], events: [{ cursor: 1001, run_id: "run-1", kind: "process.model_delta" }], snapshot: null })
      .mockResolvedValueOnce({ cursor: 1002, reset: false, records: [second], messages: [], events: [{ cursor: 1002, run_id: "run-1", kind: "process.model_delta" }], snapshot: null })
      .mockImplementation(async (_id: string, after: number) => ({ cursor: after, reset: false, records: [], messages: [], events: [], snapshot: null }));
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs } });
    await flushPromises();
    const pane = wrapper.get(".workbench-process");
    Object.defineProperties(pane.element, { scrollHeight: { value: 3000 }, clientHeight: { value: 400 } });
    pane.element.scrollTop = 250;
    await pane.trigger("scroll");
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text() === "progress.reloadLatest")!.trigger("click");
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(wrapper.text()).not.toContain("DURING RETURN");
    finish({ records: [...initial.slice(-127), first], next_before: "385" });
    await flushPromises();
    expect(wrapper.text()).toContain("BEFORE RETURN");
    expect(wrapper.text()).toContain("DURING RETURN");
    expect(wrapper.text()).not.toContain("progress.newActivity");
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(api.events).toHaveBeenLastCalledWith("run-1", 1002);
    wrapper.unmount();
  });

  it("resumes following when a user scrolls back to the live edge without a gap", async () => {
    vi.useFakeTimers();
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=run-1");
    api.run.mockResolvedValue({ ...run, cursor: 10, process: [] });
    api.events.mockImplementation(async (_id: string, after: number) => ({ cursor: after, reset: false, records: [], messages: [], events: [], snapshot: null }));
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs } });
    await flushPromises();
    const pane = wrapper.get(".workbench-process");
    Object.defineProperties(pane.element, { scrollHeight: { value: 1000 }, clientHeight: { value: 200 } });
    pane.element.scrollTop = 100;
    await pane.trigger("scroll");
    expect(wrapper.text()).toContain("progress.reloadLatest");
    pane.element.scrollTop = 800;
    await pane.trigger("scroll");
    expect(wrapper.text()).not.toContain("progress.reloadLatest");
    api.events.mockResolvedValueOnce({ cursor: 11, reset: false, records: [{ record_id: "new", cursor: 11, run_id: "run-1", attempt_id: "attempt-1", kind: "model_response", data: { text: "NEW LIVE RESPONSE" } }], messages: [], events: [{ cursor: 11, run_id: "run-1", kind: "process.model_response" }], snapshot: null });
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(wrapper.text()).toContain("NEW LIVE RESPONSE");
    expect(pane.element.scrollTop).toBe(1000);
    wrapper.unmount();
  });

  it("retains the newest messages and events through repeated state snapshots", async () => {
    vi.useFakeTimers();
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=run-1");
    api.run.mockImplementation(async () => ({ ...run, cursor: 10, process: [], messages: [], events: [] }));
    let sequence = 0;
    api.events.mockImplementation(async (_id: string, after: number) => {
      if (sequence >= 600) return { cursor: after, reset: false, records: [], messages: [], events: [], snapshot: null };
      const messages = Array.from({ length: 100 }, () => {
        const current = sequence++;
        return { message_id: `message-${current}`, cursor: 11 + current, sender_member_id: "sender", recipient_member_id: "member-1", sender_task_run_id: "sender-task", sender_attempt_id: "sender-attempt", scope_id: "scope", text: `MESSAGE-${String(current).padStart(3, "0")}`, artifact_refs: [] };
      });
      return { cursor: messages[messages.length - 1].cursor, reset: false, records: [], messages, events: messages.map((message) => ({ cursor: message.cursor, run_id: "run-1", kind: "member.message_sent", data: { message_id: message.message_id } })), snapshot: null };
    });
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs } });
    await flushPromises();
    for (let index = 0; index < 6; index++) { await vi.advanceTimersByTimeAsync(2000); await flushPromises(); }
    expect(wrapper.text()).toContain("MESSAGE-599");
    expect(wrapper.text()).not.toContain("MESSAGE-000");
    const displayed = wrapper.getComponent({ name: "WorkflowRunProcess" }).props("run");
    expect(displayed.messages).toHaveLength(512);
    expect(displayed.events).toHaveLength(512);
    expect(displayed.messages[511].message_id).toBe("message-599");
    expect(displayed.events[511].cursor).toBe(610);
    expect(wrapper.text()).toContain("progress.olderHistoryHidden");
    expect(wrapper.text()).not.toContain("progress.reloadLatest");
    wrapper.unmount();
  });

  it("keeps the newest observations for an unselected concurrent task", async () => {
    vi.useFakeTimers();
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=root");
    const first = { ...run, run_id: "first", node_id: "first", process: [], snapshot: { ...run.snapshot, definition: { name: "First" } } };
    const second = { ...run, run_id: "second", node_id: "second", process: [], snapshot: { ...run.snapshot, definition: { name: "Second" } } };
    api.run.mockResolvedValue({ ...run, run_id: "root", cursor: 10, tasks: [first, second], scopes: [], loops: [] });
    let sequence = 0;
    api.events.mockImplementation(async (_id: string, after: number) => {
      if (sequence >= 600) return { cursor: after, reset: false, records: [], messages: [], events: [], snapshot: null };
      const records = Array.from({ length: 100 }, () => {
        const current = sequence++;
        return { record_id: `other-${current}`, cursor: 11 + current, run_id: "second", attempt_id: "attempt-1", kind: "model_delta", data: { receipt_id: "other-delivery", sequence: current, text: `OTHER[${String(current).padStart(3, "0")}]` } };
      });
      return { cursor: records[records.length - 1].cursor, reset: false, records, messages: [], events: records.map((record) => ({ cursor: record.cursor, run_id: record.run_id, kind: "process.model_delta" })), snapshot: null };
    });
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs: { ...stubs, WorkflowRunProgress: true } } });
    await flushPromises();
    for (let index = 0; index < 6; index++) { await vi.advanceTimersByTimeAsync(2000); await flushPromises(); }
    wrapper.findComponent({ name: "WorkflowRunProgress" }).vm.$emit("select", "second");
    await flushPromises();
    expect(wrapper.text()).toContain("OTHER599");
    expect(wrapper.text()).not.toContain("OTHER000");
    expect(wrapper.getComponent({ name: "WorkflowRunProcess" }).props("run").process.length).toBeLessThanOrEqual(512);
    wrapper.unmount();
  });

  it("restores a task's latest window when switching away from truncated history", async () => {
    vi.useFakeTimers();
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=root");
    const delta = (sequence: number, cursor: number, text: string) => ({ record_id: `a-${sequence}`, cursor, run_id: "first", attempt_id: "attempt-1", kind: "model_delta", data: { receipt_id: "first-delivery", sequence, text } });
    const initial = Array.from({ length: 512 }, (_, index) => delta(100 + index, 200 + index, `OLD-${index};`));
    const first = { ...run, run_id: "first", node_id: "first", process: initial, snapshot: { ...run.snapshot, definition: { name: "First" } } };
    const second = { ...run, run_id: "second", node_id: "second", process: [], snapshot: { ...run.snapshot, definition: { name: "Second" } } };
    api.run.mockResolvedValue({ ...run, run_id: "root", cursor: 1000, tasks: [first, second], scopes: [], loops: [] });
    const firstNew = delta(612, 1001, "FIRST-NEW;");
    const secondNew = delta(613, 1002, "SECOND-NEW;");
    let finishRestore!: (page: any) => void;
    api.processPage.mockImplementation(async (_root: string, _task: string, before: number) => before === 200
      ? { records: Array.from({ length: 20 }, (_, index) => delta(80 + index, 180 + index, `EARLIER-${index};`)), next_before: "180" }
      : new Promise((resolve) => { finishRestore = resolve; }));
    api.runHistoryPage.mockResolvedValue({ items: [], next_before: null });
    const batch = (record: any) => ({ cursor: record.cursor, reset: false, records: [record], messages: [], events: [{ cursor: record.cursor, run_id: record.run_id, kind: "process.model_delta" }], snapshot: null });
    api.events.mockResolvedValueOnce(batch(firstNew)).mockResolvedValueOnce(batch(secondNew))
      .mockImplementation(async (_id: string, after: number) => ({ cursor: after, reset: false, records: [], messages: [], events: [], snapshot: null }));
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs: { ...stubs, WorkflowRunProgress: true } } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text() === "progress.loadOlder")!.trigger("click");
    await flushPromises();
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(wrapper.text()).toContain("progress.newActivity");
    wrapper.findComponent({ name: "WorkflowRunProgress" }).vm.$emit("select", "second");
    await flushPromises();
    expect(api.processPage).toHaveBeenCalledWith("root", "first", 1002);
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    finishRestore({ records: [...initial.slice(-126), firstNew], next_before: "586" });
    await flushPromises();
    wrapper.findComponent({ name: "WorkflowRunProgress" }).vm.$emit("select", "first");
    await flushPromises();
    expect(wrapper.text()).toContain("FIRST-NEW");
    expect(wrapper.text()).toContain("SECOND-NEW");
    expect(wrapper.text()).not.toContain("progress.newActivity");
    expect(wrapper.getComponent({ name: "WorkflowRunProcess" }).props("run").process.length).toBeLessThanOrEqual(512);
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(api.events).toHaveBeenLastCalledWith("root", 1002);
    wrapper.unmount();
  });

  it("merges incremental records without replacing the reader or forcing scroll", async () => {
    vi.useFakeTimers();
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
    await router.push("/?workflow_run=run-1");
    const start = { record_id: "start", run_id: "run-1", attempt_id: "attempt-1", kind: "model_delivery", data: { receipt_id: "delivery", invocation_id: "invocation", status: "started" } };
    const delta = { record_id: "delta", run_id: "run-1", attempt_id: "attempt-1", kind: "model_delta", data: { receipt_id: "delivery", sequence: 0, text: "First", reasoning_content: "" } };
    api.run.mockResolvedValue({ ...run, cursor: 10, process: [start] });
    api.events.mockResolvedValue({ cursor: 11, reset: false, events: [{ cursor: 11 }], records: [delta], snapshot: null });
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs } });
    await flushPromises();
    const reader = wrapper.get('[data-testid="model-delivery"]').element;
    const pane = wrapper.get(".workbench-process");
    Object.defineProperties(pane.element, { scrollHeight: { value: 1000 }, clientHeight: { value: 200 } });
    pane.element.scrollTop = 100;
    await pane.trigger("scroll");
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(wrapper.get('[data-testid="model-delivery"]').element).toBe(reader);
    expect(wrapper.findAll('[data-testid="model-delivery"]')).toHaveLength(1);
    expect(pane.element.scrollTop).toBe(100);
    expect(api.run).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(wrapper.findAll('[data-testid="model-delivery"]')).toHaveLength(1);
    expect(api.events).toHaveBeenLastCalledWith("run-1", 11);
    wrapper.unmount();
  });

  it.each([["image/png", "ImagePreview"], ["application/pdf", "PdfPreview"]])(
    "uses the chat preview for stored %s artifacts", async (mediaType, componentName) => {
      const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: slotStub }] });
      await router.push("/?workflow_run=run-1");
      api.run.mockResolvedValue({ ...run, cursor: 1, result_ref: { artifact_id: "artifact", version: 1 } });
      api.artifact.mockResolvedValue({ artifact_id: "artifact", version: 1, content: { text: "Report", files: [{file_id: "file", filename: "report"}] } });
      const blob = new Blob(["sample"], { type: mediaType });
      api.file.mockResolvedValue({ data: blob, headers: {} });
      const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: "runs" }, global: { plugins: [router], stubs: { ...stubs,
        ImagePreview: { props: ["blob", "filename"], template: '<div data-testid="image-preview">{{ filename }}</div>' },
        PdfPreview: { props: ["blob"], template: '<div data-testid="pdf-preview" />' },
      } } });
      await flushPromises();
      const button = wrapper.findAll("button").find(item => item.text() === "v2.previewFile");
      expect(button).toBeDefined();
      await button!.trigger("click");
      await flushPromises();
      expect(api.file).toHaveBeenCalledWith("artifact", 1, "file", false);
      expect(wrapper.findComponent({ name: componentName }).exists() || wrapper.find(componentName === "ImagePreview" ? '[data-testid="image-preview"]' : '[data-testid="pdf-preview"]').exists()).toBe(true);
      wrapper.unmount();
    },
  );

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

  it("corrects an input error and starts the saved inline workflow version", async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: slotStub }] });
    await router.push('/');
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
    api.list.mockResolvedValue({ assets: [saved] });
    const wrapper = mount(WorkflowWorkspacePanel, { props: { mode: 'assets' }, global: { plugins: [router], stubs } });
    await flushPromises();
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
