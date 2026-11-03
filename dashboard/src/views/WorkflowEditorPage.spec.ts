import { flushPromises, mount } from "@vue/test-utils";
import { RouterView, createMemoryHistory, createRouter } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { draftApi } from "@/composables/useAssetDraft";
import { mockDrafts } from "@/components/agent_teams/draftTestMocks";
import WorkflowEditorPage from "./WorkflowEditorPage.vue";

const api = vi.hoisted(() => ({ list: vi.fn(), save: vi.fn() }));
const roles = vi.hoisted(() => ({ list: vi.fn(), resources: vi.fn() }));
vi.mock("@/api/workflowAssets", () => ({ workflowAssetsApi: api }));
vi.mock("@/api/roleAssets", () => ({ roleAssetsApi: roles }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

const slot = { template: "<div><slot /></div>" };
const stubs = {
  "v-btn": {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  "v-text-field": {
    props: ["modelValue", "disabled"],
    emits: ["update:modelValue"],
    template:
      '<input :disabled="disabled" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  "v-select": slot,
  "v-alert": slot,
  "v-progress-circular": slot,
  RunLimitsForm: true,
  WorkflowGraphCanvas: {
    name: "WorkflowGraphCanvas",
    props: ["definition", "issues"],
    emits: ["update:definition", "select-task", "validation"],
    template: '<div data-testid="graph-editor">{{ definition.name }}</div>',
  },
  TrialRunDialog: {
    name: "TrialRunDialog",
    props: ["modelValue", "assetId", "revision", "initialNode"],
    emits: ["started"],
    template:
      '<div v-if="modelValue" data-testid="trial"><button @click="$emit(\'started\', \'trial-run\')">Start trial</button></div>',
  },
};
const mounted: ReturnType<typeof mount>[] = [];
async function open(path = "/agent-teams/workflows/new", realCanvas = false) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/agent-teams",
        component: { template: '<div data-testid="asset-list" />' },
      },
      {
        path: "/agent-teams/workflows/:assetId",
        name: "AgentWorkflowEditor",
        component: WorkflowEditorPage,
      },
    ],
  });
  await router.push("/agent-teams");
  await router.push(path);
  const wrapper = mount(RouterView, {
    global: {
      plugins: [router],
      stubs: realCanvas
        ? {
            ...stubs,
            WorkflowGraphCanvas: false,
            TeamsFlowCanvas: true,
            WorkflowLoopPanel: true,
            RoleResourcesEditor: true,
            "v-icon": true,
            "v-chip": true,
            "v-spacer": true,
            "v-textarea": true,
            "v-switch": true,
            "v-expansion-panels": true,
          }
        : stubs,
    },
  });
  mounted.push(wrapper);
  await flushPromises();
  return { wrapper, router };
}

beforeEach(() => {
  vi.resetAllMocks();
  mockDrafts(api.save);
  api.list.mockResolvedValue({ assets: [] });
  api.save.mockResolvedValue({ asset_id: "draft-1", version: 1 });
  roles.list.mockResolvedValue({ roles: [] });
  roles.resources.mockResolvedValue({
    models: [],
    profiles: [],
    personas: [],
    tools: [],
    skills: [],
    knowledge_bases: [],
  });
});
afterEach(() => {
  mounted.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.restoreAllMocks();
});

describe("workflow editor navigation", () => {
  it("keeps canvas validation stable across editor updates and publishes", async () => {
    const { wrapper, router } = await open("/agent-teams/workflows/new", true);
    const canvas = wrapper.getComponent({ name: "WorkflowGraphCanvas" });
    const validations = canvas.emitted("validation")?.length ?? 0;
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "editor.settings")!
      .trigger("click");
    expect(canvas.emitted("validation")).toHaveLength(validations);
    await wrapper
      .get('[data-testid="workflow-name"]')
      .setValue("Stable canvas");
    await flushPromises();
    expect(canvas.emitted("validation")).toHaveLength(validations + 2);
    await wrapper.get('[data-testid="publish-workflow"]').trigger("click");
    await flushPromises();
    expect(api.save).toHaveBeenCalledTimes(1);
    expect(router.currentRoute.value.path).toBe("/agent-teams");
  });

  it("assigns a draft URL and restores that draft after reopening without creating another", async () => {
    const { wrapper, router } = await open();
    expect(router.currentRoute.value.path).toBe(
      "/agent-teams/workflows/draft-1",
    );
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
    await wrapper
      .get('[data-testid="workflow-name"]')
      .setValue("Research workflow");
    await flushPromises();
    wrapper.unmount();
    const reopened = await open("/agent-teams/workflows/draft-1");
    expect(
      (reopened.wrapper.get("input").element as HTMLInputElement).value,
    ).toBe("Research workflow");
    expect(draftApi.create).toHaveBeenCalledTimes(1);
    expect(reopened.wrapper.get('[data-testid="graph-editor"]').text()).toBe(
      "Research workflow",
    );
  });

  it("waits for an in-flight save before browser Back can leave the editor", async () => {
    const { wrapper, router } = await open();
    const original = vi.mocked(draftApi.update).getMockImplementation()!;
    let finish!: () => void;
    vi.mocked(draftApi.update).mockImplementationOnce(
      (...args) =>
        new Promise((resolve) => {
          finish = () => {
            void original(...args).then(resolve);
          };
        }),
    );
    await wrapper
      .get('[data-testid="workflow-name"]')
      .setValue("Saved on exit");
    await flushPromises();
    router.back();
    await flushPromises();
    expect(wrapper.find('[data-testid="graph-editor"]').exists()).toBe(true);
    finish();
    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/agent-teams");
    expect(
      (await draftApi.get("workflow", "draft-1")).body.definition.name,
    ).toBe("Saved on exit");
  });

  it.each([409, 500])(
    "keeps an unsaved draft visible on navigation after a %s failure",
    async (status) => {
      const { wrapper, router } = await open();
      vi.mocked(draftApi.update).mockRejectedValueOnce({
        response: { status },
      });
      await wrapper
        .get('[data-testid="workflow-name"]')
        .setValue("Do not lose this");
      await flushPromises();
      await router.push("/agent-teams");
      expect(router.currentRoute.value.path).toBe(
        "/agent-teams/workflows/draft-1",
      );
      expect(wrapper.text()).toContain("editor.saveBeforeLeave");
      expect((wrapper.get("input").element as HTMLInputElement).value).toBe(
        "Do not lose this",
      );
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "draft.discardClose")!
        .trigger("click");
      await flushPromises();
      expect(router.currentRoute.value.path).toBe("/agent-teams");
    },
  );

  it("publishes the edited inline agent and returns to the workflow list", async () => {
    const { wrapper, router } = await open();
    await wrapper.get('[data-testid="workflow-name"]').setValue("My workflow");
    await wrapper.get('[data-testid="publish-workflow"]').trigger("click");
    await flushPromises();
    expect(api.save).toHaveBeenCalledWith(
      null,
      expect.objectContaining({
        definition: expect.objectContaining({
          name: "My workflow",
          tasks: [
            expect.objectContaining({
              role: { name: "Agent" },
              installation: { profile_id: "default" },
              task: "{input}",
            }),
          ],
        }),
      }),
    );
    expect(router.currentRoute.value.path).toBe("/agent-teams");
  });

  it("surfaces publish validation in the canvas and allows a corrected retry", async () => {
    const { wrapper, router } = await open();
    await wrapper.get('[data-testid="workflow-name"]').setValue("My workflow");
    api.save.mockRejectedValueOnce({
      response: {
        data: {
          message: "Invalid task",
          data: {
            fields: [
              {
                path: "body.definition.GraphDefinition.tasks.0.task",
                message: "Required task",
              },
            ],
          },
        },
      },
    });
    await wrapper.get('[data-testid="publish-workflow"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.name).toBe("AgentWorkflowEditor");
    expect(wrapper.text()).toContain("Invalid task");
    expect(
      wrapper.getComponent({ name: "WorkflowGraphCanvas" }).props("issues"),
    ).toEqual([{ path: "definition.tasks.0.task", message: "Required task" }]);
    await wrapper.get('[data-testid="publish-workflow"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/agent-teams");
  });

  it("tests the current saved draft revision and opens the started run", async () => {
    const { wrapper, router } = await open();
    await wrapper
      .get('[data-testid="workflow-name"]')
      .setValue("Trial workflow");
    wrapper
      .getComponent({ name: "WorkflowGraphCanvas" })
      .vm.$emit("select-task", "agent");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "trial.title")!
      .trigger("click");
    await flushPromises();
    const trial = wrapper.getComponent({ name: "TrialRunDialog" });
    expect(trial.props()).toMatchObject({
      assetId: "draft-1",
      revision: 2,
      initialNode: "agent",
    });
    await wrapper.get('[data-testid="trial"] button').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.workflow_run).toBe("trial-run");
  });

  it("does not turn an unknown asset URL into a new draft", async () => {
    const { wrapper, router } = await open("/agent-teams/workflows/missing");
    expect(wrapper.text()).toContain("editor.notFound");
    expect(draftApi.create).not.toHaveBeenCalled();
    await router.push("/agent-teams");
    expect(router.currentRoute.value.path).toBe("/agent-teams");
  });
});
