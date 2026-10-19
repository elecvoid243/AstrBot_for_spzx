// The formal workbench exposes versioned assets and original read-only history.
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, inject, nextTick, provide } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentTeamsSidebar from "@/components/agent_teams/AgentTeamsSidebar.vue";
import AgentTeamsPage from "./AgentTeamsPage.vue";

const mocks = vi.hoisted(() => ({ loadTeams: vi.fn(), route: null as any }));
vi.mock("@/composables/useAgentTeams", async () => {
  const { ref } = await vi.importActual<typeof import("vue")>("vue");
  return {
    useAgentTeams: () => ({ teams: ref(TEAMS), loadTeams: mocks.loadTeams }),
  };
});
vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual<typeof import("vue")>("vue");
  mocks.route = reactive({ query: {} });
  return {
    useRoute: () => mocks.route,
    useRouter: () => ({
      replace: async ({ query }: { query: Record<string, unknown> }) => {
        mocks.route.query = query;
      },
    }),
  };
});
const TEAMS = [
  {
    team_id: "t1",
    name: "Alpha 团队",
    members: [
      { member_id: "m1", name: "Alice" },
      { member_id: "m2", name: "Bob" },
    ],
    coordinator_member_id: "m1",
  },
  {
    team_id: "t2",
    name: "Beta 团队",
    members: [{ member_id: "m3", name: "Carol" }],
  },
];
const TABS_KEY = Symbol("tabs-stub");
const stubs = {
  WorkflowWorkspacePanel: {
    name: "WorkspaceStub",
    props: ["mode", "launchDisabled", "assetKind", "launchAsset"],
    emits: ["locateAsset", "started", "installed"],
    template: '<div class="workflow-workspace-stub" :mode="mode" />',
  },
  LegacyRunCutover: {
    name: "CutoverStub",
    props: ["teams"],
    emits: ["ready"],
    template: '<div class="cutover-stub" />',
  },
  LegacyMigrationDialog: {
    name: "MigrationStub",
    props: ["teams"],
    emits: ["migrated"],
    template: '<div class="migration-stub" />',
  },
  RoleTemplatesPanel: { template: '<div class="role-templates-panel-stub" />' },
  "v-container": { template: "<div><slot /></div>" },
  "v-btn": {
    props: {
      icon: { type: String, default: undefined },
      disabled: { type: Boolean, default: false },
      loading: { type: Boolean, default: false },
    },
    emits: ["click"],
    template:
      '<button type="button" :disabled="disabled || loading" @click="$emit(\'click\', $event)"><i v-if="icon" class="mdi" :class="icon" /><slot /></button>',
  },
  "v-tabs": defineComponent({
    props: { modelValue: { type: String, default: "" } },
    emits: ["update:modelValue"],
    setup(props, { slots, emit }) {
      provide(TABS_KEY, (value: string) => emit("update:modelValue", value));
      return () => h("div", { class: "tabs-stub" }, slots.default?.());
    },
  }),
  "v-tab": defineComponent({
    props: { value: { type: String, default: "" } },
    setup(props, { slots }) {
      const select = inject<((value: string) => void) | null>(TABS_KEY, null);
      return () =>
        h(
          "button",
          {
            type: "button",
            class: "tab-stub",
            onClick: () => select?.(props.value),
          },
          slots.default?.(),
        );
    },
  }),
};
function mountPage() {
  return mount(AgentTeamsPage, { global: { stubs } });
}
function mountSidebar(props: Record<string, unknown> = {}) {
  return mount(AgentTeamsSidebar, {
    props: {
      teams: TEAMS,
      selectedTeamId: "t1",
      selectedTeam: TEAMS[0],
      ...props,
    },
    global: { stubs },
  });
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.route.query = {};
});

describe("AgentTeamsPage", () => {
  it("retires the previous execution workspace while retaining definition migration", () => {
    const wrapper = mountPage();
    expect(wrapper.find(".workbench-legacy").exists()).toBe(false);
    expect(wrapper.find(".workflow-workspace-stub").exists()).toBe(true);
    expect(wrapper.find(".migration-stub").exists()).toBe(true);
    expect(wrapper.find(".dashboard-title").text()).toBe("Agent 团队");
  });
  it("opens an incoming chat run link while the assets panel is already mounted", async () => {
    const wrapper = mountPage();
    expect(wrapper.get(".workflow-workspace-stub").attributes("mode")).toBe(
      "assets",
    );
    mocks.route.query = { workflow_run: "chat-run" };
    await nextTick();
    expect(wrapper.get(".workflow-workspace-stub").attributes("mode")).toBe(
      "runs",
    );
    await wrapper
      .findAll(".workbench-navigation button")
      .find((button) => button.text() === "资产")!
      .trigger("click");
    mocks.route.query = { workflow_run: "chat-run" };
    await nextTick();
    expect(wrapper.get(".workflow-workspace-stub").attributes("mode")).toBe(
      "runs",
    );
  });
  it("opens reusable role templates independently of a team selection", async () => {
    const wrapper = mountPage();
    await wrapper
      .findAll(".tab-stub")
      .find((tab) => tab.text() === "角色模板")!
      .trigger("click");
    expect(wrapper.find(".role-templates-panel-stub").exists()).toBe(true);
    expect(wrapper.find(".agent-teams-panel-editor").exists()).toBe(false);
  });
  it("blocks asset launches until the original owned runs are handled", async () => {
    const wrapper = mountPage();
    expect(
      wrapper.getComponent({ name: "WorkspaceStub" }).props("launchDisabled"),
    ).toBe(true);
    wrapper.getComponent({ name: "CutoverStub" }).vm.$emit("ready", true);
    await nextTick();
    expect(
      wrapper.getComponent({ name: "WorkspaceStub" }).props("launchDisabled"),
    ).toBe(false);
    wrapper.getComponent({ name: "CutoverStub" }).vm.$emit("ready", false);
    await nextTick();
    expect(
      wrapper.getComponent({ name: "WorkspaceStub" }).props("launchDisabled"),
    ).toBe(true);
  });
  it("locates a migrated definition from history as a separate new asset", async () => {
    const wrapper = mountPage();
    mocks.route.query = { workflow_run: "current-run" };
    await nextTick();
    const asset = { asset_id: "migrated-team", version: 2, kind: "autonomous" };
    wrapper
      .getComponent({ name: "WorkspaceStub" })
      .vm.$emit("locateAsset", asset);
    await nextTick();
    const workspace = wrapper.getComponent({ name: "WorkspaceStub" });
    expect(workspace.props("mode")).toBe("assets");
    expect(workspace.props("assetKind")).toBe("teams");
    expect(workspace.props("launchAsset")).toEqual(asset);
  });
  it("opens migrated assets without exposing the original member editor", async () => {
    const wrapper = mountPage();
    wrapper.getComponent({ name: "MigrationStub" }).vm.$emit("migrated");
    await flushPromises();
    expect(
      wrapper.getComponent({ name: "WorkspaceStub" }).props("assetKind"),
    ).toBe("teams");
    expect(wrapper.find(".agent-teams-panel-members").exists()).toBe(false);
    expect(mocks.loadTeams).toHaveBeenCalledTimes(1);
  });
});
describe("AgentTeamsSidebar", () => {
  it("emits create when the create button is clicked", async () => {
    const wrapper = mountSidebar();
    const createBtn = wrapper
      .findAll("button")
      .find((btn) => btn.text() === "新建团队");
    expect(createBtn).toBeTruthy();
    await createBtn!.trigger("click");
    expect(wrapper.emitted("create")).toHaveLength(1);
  });

  it("renders team rows with names, member counts and the selected highlight", () => {
    const wrapper = mountSidebar();
    const rows = wrapper.findAll(".team-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("Alpha 团队");
    expect(rows[0].text()).toContain("2");
    expect(rows[1].text()).toContain("Beta 团队");
    expect(rows[1].text()).toContain("1");
    expect(rows[0].classes()).toContain("is-selected");
    expect(rows[1].classes()).not.toContain("is-selected");
  });

  it("emits select with the team id when a row is clicked", async () => {
    const wrapper = mountSidebar();
    const rows = wrapper.findAll(".team-row");
    await rows[1].trigger("click");
    expect(wrapper.emitted("select")).toEqual([["t2"]]);
  });

  it("renders the edit pencil only on the selected team row and emits edit without select", async () => {
    const wrapper = mountSidebar();
    const rows = wrapper.findAll(".team-row");
    expect(rows[0].find(".team-edit").exists()).toBe(true);
    expect(rows[1].find(".team-edit").exists()).toBe(false);
    expect(rows[0].find(".team-edit").attributes("aria-label")).toBe(
      "编辑团队",
    );

    await rows[0].find(".team-edit").trigger("click");
    expect(wrapper.emitted("edit")).toHaveLength(1);
    // The pencil must not double as a row selection (.stop).
    expect(wrapper.emitted("select")).toBeUndefined();
  });

  it("marks the coordinator member and emits removeMember from the remove button", async () => {
    const wrapper = mountSidebar();

    const coordinatorRow = wrapper.find(".member-row.is-coordinator");
    expect(coordinatorRow.text()).toContain("Alice");
    expect(coordinatorRow.text()).toContain("协调者");
    expect(coordinatorRow.find(".mdi-crown").exists()).toBe(true);
    expect(wrapper.text()).toContain("Bob");

    const removeButtons = wrapper.findAll('button[aria-label="移除"]');
    expect(removeButtons).toHaveLength(2);
    await removeButtons[1].trigger("click");
    expect(wrapper.emitted("removeMember")).toEqual([["m2"]]);
  });

  it("hides the member list when no team is selected", () => {
    const wrapper = mountSidebar({ selectedTeamId: null, selectedTeam: null });
    expect(wrapper.find(".member-row").exists()).toBe(false);
  });

  it("shows the empty hint when there are no teams", () => {
    const wrapper = mountSidebar({
      teams: [],
      selectedTeamId: null,
      selectedTeam: null,
    });
    expect(wrapper.text()).toContain("暂无团队");
  });
});
