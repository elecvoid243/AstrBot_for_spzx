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
    "v-expansion-panels",
    "v-expansion-panel",
    "v-expansion-panel-text",
  ].map((name) => [name, slotStub]),
);
Object.assign(stubs, {
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
});
