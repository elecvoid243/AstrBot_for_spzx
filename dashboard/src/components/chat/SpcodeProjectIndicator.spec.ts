// SpcodeProjectIndicator.spec.ts
// Author: elecvoid243 @ 2026-08-06
// Updated: 2026-08-15 — services popover (codegraph / vivado status
// integrated from the removed SpcodeCodegraphChip / SpcodeVivadoStatusChip).
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, nextTick } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: vi.fn(), post: vi.fn() },
}));

import { useSpcodeOperationProgress } from "@/composables/useSpcodeOperationProgress";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { useSpcodeCodegraphStatus } from "@/composables/useSpcodeCodegraphStatus";
import { useSpcodeVivadoStatus } from "@/composables/useSpcodeVivadoStatus";
import SpcodeProjectIndicator from "./SpcodeProjectIndicator.vue";
import { pluginExtensionApi } from "@/api/v1";

// Minimal stubs: v-tooltip renders its activator slot directly; v-menu
// forwards an onClick to its activator so tests can toggle the popover.
const tooltipStub = defineComponent({
  template: `<div><slot name="activator" :props="{}" /><slot /></div>`,
});
const menuStub = defineComponent({
  props: { modelValue: { type: Boolean, default: false } },
  template: `<div class="v-menu-stub"><slot name="activator" :props="{ onClick: () => $emit('update:modelValue', true) }" /><slot v-if="modelValue" /></div>`,
});
const stubs = {
  "v-tooltip": tooltipStub,
  "v-menu": menuStub,
  "v-card": { template: "<div><slot /></div>" },
  "v-card-text": { template: "<div><slot /></div>" },
  "v-icon": { template: "<i><slot /></i>" },
  // Stub <Transition> so leave-animation frames can't delay element removal
  // in fake-timer tests (Vue's nextFrame uses real rAF).
  transition: { template: "<div><slot /></div>" },
};

function setProgress(
  status: "idle" | "running" | "done" | "failed",
  operation:
    | "project_load"
    | "project_unload"
    | "codegraph_set"
    | "codegraph_init"
    | null,
  extra: Partial<{ currentStep: string; messages: string[]; reason: string }> = {},
) {
  const { progress } = useSpcodeOperationProgress();
  progress.value = {
    status,
    operation,
    currentStep: extra.currentStep ?? "",
    messages: extra.messages ?? [],
    reason: extra.reason ?? null,
  };
}

/** Put both MCP services into a healthy state so popover rows show labels. */
function setServicesHealthy() {
  useSpcodeCodegraphStatus().status.value = {
    enabled: true,
    mcpRunning: true,
    activeProject: "F:/proj",
    fetchedAt: 1,
  };
  useSpcodeVivadoStatus().status.value = {
    overall: "ok",
    enabled: true,
    mcpRunning: true,
    vivadoPath: "C:/vivado/bin/vivado",
    installMissing: false,
    degraded: false,
    sessions: [{ id: "s1", state: "idle" }],
    fetchedAt: 1,
    message: "Vivado 运行中 · 1 会话",
  };
}

describe("SpcodeProjectIndicator progress states", () => {
  afterEach(() => {
    useSpcodeOperationProgress().clear();
    useSpcodeProjectStatus().reset();
    useSpcodeCodegraphStatus().reset();
    useSpcodeVivadoStatus().reset();
  });

  it("shows a generic loading label (no live step) while loading and suppresses click", async () => {
    setProgress("running", "project_load", {
      currentStep: "⏳ [2/3] codegraph init",
    });
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    // Live yield steps are deliberately hidden (the codegraph bubble owns
    // those details); the chip shows one generic loading label instead.
    expect(wrapper.text()).toContain("正在加载项目");
    expect(wrapper.text()).not.toContain("⏳ [2/3] codegraph init");
    await wrapper.find(".sp-status-badge").trigger("click");
    expect(wrapper.emitted("open-load-dialog")).toBeUndefined();
  });

  it("shows failed state with detail popover button", async () => {
    setProgress("failed", "project_load", {
      messages: ["⏳ [1/3] init", "❌ path unsafe"],
      reason: "path_unsafe",
    });
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    expect(wrapper.text()).toContain("加载失败");
    expect(wrapper.find(".sp-chip-details-btn").exists()).toBe(true);
    // failed state still allows opening the dialog (retry)
    await wrapper.find(".sp-status-badge").trigger("click");
    expect(wrapper.emitted("open-load-dialog")).toHaveLength(1);
  });

  it("non-project operations do not hijack the chip", () => {
    setProgress("running", "codegraph_set", { currentStep: "🔄 restart" });
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    expect(wrapper.text()).not.toContain("🔄 restart");
    expect(wrapper.find(".sp-chip-details-btn").exists()).toBe(false);
  });

  it("idle progress falls back to the unloaded badge", () => {
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    expect(wrapper.text()).toContain("未加载项目");
    expect(wrapper.find(".sp-chip-details-btn").exists()).toBe(false);
  });

  it("shows only the basename of a loaded project path", () => {
    useSpcodeProjectStatus().setLoaded(
      "webchat:FriendMessage:webchat!astrbot!cid-indicator",
      "F:/project/python/pycharm/testproj1",
    );
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    // Visible badge: label + basename only.
    const badgeText = wrapper.find(".sp-status-badge").text();
    expect(badgeText).toContain("已加载项目");
    expect(badgeText).toContain("testproj1");
    expect(badgeText).not.toContain("F:/project");
    // Hover tooltip still exposes the full path.
    expect(wrapper.text()).toContain("F:/project/python/pycharm/testproj1");
  });

  it("strips trailing separators before taking the basename", () => {
    useSpcodeProjectStatus().setLoaded(
      "webchat:FriendMessage:webchat!astrbot!cid-indicator",
      "C:\\proj\\demo\\",
    );
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    const badgeText = wrapper.find(".sp-status-badge").text();
    expect(badgeText).toContain("demo");
    expect(badgeText).not.toContain("C:\\proj");
  });
});

describe("SpcodeProjectIndicator services popover", () => {
  afterEach(() => {
    useSpcodeOperationProgress().clear();
    useSpcodeProjectStatus().reset();
    useSpcodeCodegraphStatus().reset();
    useSpcodeVivadoStatus().reset();
  });

  it("renders the services side button next to the project chip", () => {
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    expect(wrapper.find(".sp-chip-services-btn").exists()).toBe(true);
    // Icon must be one of the project's mdi subset glyphs (mdi-server-network).
    expect(wrapper.find(".sp-chip-services-btn").text()).toContain(
      "mdi-server-network",
    );
  });

  it("opens the popover and shows codegraph + vivado status rows", async () => {
    setServicesHealthy();
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    expect(wrapper.findAll(".sp-svc-row").length).toBe(0);
    await wrapper.find(".sp-chip-services-btn").trigger("click");
    expect(wrapper.findAll(".sp-svc-row").length).toBe(2);
    expect(wrapper.text()).toContain("Codegraph 已加载");
    expect(wrapper.text()).toContain("Vivado 已就绪");
    // The codegraph path detail is labelled as the *default* project so
    // users don't mistake it for the only directory codegraph works on.
    const cgDetail = wrapper.find(".sp-svc-row__detail");
    expect(cgDetail.text()).toContain("F:/proj");
    expect(cgDetail.text()).toContain("默认");
  });

  it("treats a running MCP as loaded even without a default project", async () => {
    useSpcodeCodegraphStatus().status.value = {
      enabled: true,
      mcpRunning: true,
      activeProject: "",
      fetchedAt: 1,
    };
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    await wrapper.find(".sp-chip-services-btn").trigger("click");

    expect(wrapper.text()).toContain("Codegraph 已加载");
    expect(wrapper.text()).not.toContain("Codegraph 未加载");
    // The detail line still tells the user no default directory is set.
    expect(wrapper.find(".sp-svc-row__detail").text()).toContain(
      "未设置默认项目",
    );
    // Success dot, not the neutral one.
    expect(wrapper.find(".sp-svc-row__dot").classes()).toContain(
      "sp-svc-row__dot--success",
    );
  });

  it("codegraph row falls back to the not-running label when MCP is down", async () => {
    useSpcodeCodegraphStatus().reset();
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    await wrapper.find(".sp-chip-services-btn").trigger("click");
    expect(wrapper.text()).toContain("Codegraph 未启动");
  });

  it("manage button emits open-codegraph-dialog and closes the popover", async () => {
    setServicesHealthy();
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    await wrapper.find(".sp-chip-services-btn").trigger("click");
    expect(wrapper.findAll(".sp-svc-row").length).toBe(2);
    await wrapper.find(".sp-svc-row__action").trigger("click");
    expect(wrapper.emitted("open-codegraph-dialog")).toHaveLength(1);
    await nextTick();
    // Popover closed after delegating to the codegraph dialog.
    expect(wrapper.findAll(".sp-svc-row").length).toBe(0);
  });
});

describe("SpcodeProjectIndicator status bubble", () => {
  afterEach(() => {
    useSpcodeOperationProgress().clear();
    useSpcodeProjectStatus().reset();
    useSpcodeCodegraphStatus().reset();
    useSpcodeVivadoStatus().reset();
    vi.useRealTimers();
  });

  /**
   * Mount the indicator with the watch baseline established: the first
   * observed snapshot is swallowed (no bubble) so pre-existing state on
   * page load never pops.
   */
  async function mountWithBaseline() {
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    setProgress("idle", null); // replace the progress object → first watch tick
    await nextTick();
    return wrapper;
  }

  it("does not pop a bubble for pre-existing state on mount", async () => {
    // codegraph already connected before mount → no change → no bubble.
    useSpcodeCodegraphStatus().status.value = {
      enabled: true,
      mcpRunning: true,
      activeProject: "F:/proj",
      fetchedAt: 1,
    };
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(false);
  });

  it("pops a bubble on codegraph connect and hides it after 3s", async () => {
    vi.useFakeTimers();
    const wrapper = await mountWithBaseline();
    // codegraph goes online → bubble "Codegraph 已连接"
    useSpcodeCodegraphStatus().status.value = {
      enabled: true,
      mcpRunning: true,
      activeProject: "F:/proj",
      fetchedAt: 2,
    };
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(true);
    expect(wrapper.find(".sp-bubble").text()).toContain("Codegraph 已连接");
    // still visible just before the 3s mark
    vi.advanceTimersByTime(2999);
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(true);
    // hidden after 3s
    vi.advanceTimersByTime(1);
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(false);
  });

  it("resets the 3s timer when state changes while the bubble is visible", async () => {
    vi.useFakeTimers();
    const wrapper = await mountWithBaseline();
    useSpcodeCodegraphStatus().status.value = {
      enabled: true,
      mcpRunning: true,
      activeProject: "F:/proj",
      fetchedAt: 2,
    };
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(true);
    // 2s later: bubble still visible (user example: init at t0, done at t2)
    vi.advanceTimersByTime(2000);
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(true);
    // State updates again (project switch) while visible → timer resets.
    useSpcodeCodegraphStatus().status.value = {
      enabled: true,
      mcpRunning: true,
      activeProject: "F:/other",
      fetchedAt: 3,
    };
    await nextTick();
    // 2.9s after the reset: still visible (the OLD timer would have expired
    // at t0+3s, i.e. 1s after this point).
    vi.advanceTimersByTime(2999);
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(true);
    vi.advanceTimersByTime(1);
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(false);
  });

  it("dismisses the bubble immediately via the close button", async () => {
    vi.useFakeTimers();
    const wrapper = await mountWithBaseline();
    useSpcodeCodegraphStatus().status.value = {
      enabled: true,
      mcpRunning: true,
      activeProject: "F:/proj",
      fetchedAt: 2,
    };
    await nextTick();
    expect(wrapper.find(".sp-bubble__close").exists()).toBe(true);
    await wrapper.find(".sp-bubble__close").trigger("click");
    expect(wrapper.find(".sp-bubble").exists()).toBe(false);
    // And it stays hidden past the old expiry point.
    vi.advanceTimersByTime(5000);
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(false);
  });

  it("shows an initializing bubble while project_load runs a codegraph step", async () => {
    vi.useFakeTimers();
    const wrapper = await mountWithBaseline();
    setProgress("running", "project_load", {
      currentStep: "⏳ [2/3] codegraph init",
    });
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(true);
    expect(wrapper.find(".sp-bubble").text()).toContain("正在初始化 codegraph");
  });

  it("shows a restarting bubble while codegraph_set runs", async () => {
    vi.useFakeTimers();
    const wrapper = await mountWithBaseline();
    setProgress("running", "codegraph_set", { currentStep: "🔄 restart" });
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(true);
    expect(wrapper.find(".sp-bubble").text()).toContain("正在重启 codegraph");
  });

  it("shows an indexing bubble while codegraph_init runs", async () => {
    vi.useFakeTimers();
    const wrapper = await mountWithBaseline();
    setProgress("running", "codegraph_init", {
      currentStep: "⏳ 正在 初始化 codegraph 项目 F:/proj...",
    });
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(true);
    expect(wrapper.find(".sp-bubble").text()).toContain(
      "正在初始化 codegraph 索引",
    );
  });

  it("shows a disconnected bubble when the MCP goes down outside an operation", async () => {
    vi.useFakeTimers();
    const wrapper = await mountWithBaseline();
    // baseline: connected
    useSpcodeCodegraphStatus().status.value = {
      enabled: true,
      mcpRunning: true,
      activeProject: "F:/proj",
      fetchedAt: 2,
    };
    await nextTick();
    expect(wrapper.find(".sp-bubble").exists()).toBe(true);
    expect(wrapper.find(".sp-bubble").text()).toContain("Codegraph 已连接");
    // MCP stops (no operation running) → disconnected bubble
    useSpcodeCodegraphStatus().status.value = {
      enabled: true,
      mcpRunning: false,
      activeProject: "",
      fetchedAt: 3,
    };
    await nextTick();
    expect(wrapper.find(".sp-bubble").text()).toContain("Codegraph 已断开");
  });
});

describe("SpcodeProjectIndicator worktree activation overlay", () => {
  afterEach(() => {
    useSpcodeOperationProgress().clear();
    useSpcodeProjectStatus().reset();
    useSpcodeCodegraphStatus().reset();
    useSpcodeVivadoStatus().reset();
    vi.mocked(pluginExtensionApi.get).mockReset();
    vi.mocked(pluginExtensionApi.post).mockReset();
  });

  function setProjectLoaded() {
    useSpcodeProjectStatus().status.value = {
      loaded: true,
      directory: "F:/proj",
      loadedAt: 1,
      umo: "webchat-1",
      allLoadedCount: 1,
      fetchedAt: 1,
      bootId: null,
    };
  }

  function mockWorktreesFetch(active: string | null = null) {
    vi.mocked(pluginExtensionApi.get).mockResolvedValue({
      data: {
        status: "ok",
        data: {
          loaded: true,
          directory: "F:/proj",
          umo: "webchat-1",
          worktrees: [
            {
              path: "F:/proj",
              head_sha: "aaaaaaa",
              branch: "main",
              is_main: true,
              prunable: false,
              locked: null,
            },
            {
              path: "F:/proj/.worktrees/feat",
              head_sha: "bbbbbbb",
              branch: "feat",
              is_main: false,
              prunable: false,
              locked: null,
            },
          ],
          active_worktree: active,
          reason: null,
          stderr: "",
          elapsed_ms: 5,
        },
      },
    } as any);
  }

  function mockActivatePost(active: string | null) {
    vi.mocked(pluginExtensionApi.post).mockResolvedValue({
      data: {
        status: "ok",
        data: {
          success: true,
          loaded: true,
          directory: "F:/proj",
          umo: "webchat-1",
          worktree: active ?? "",
          active_worktree: active,
          worktrees: [],
          reason: null,
          stderr: "",
          elapsed_ms: 3,
        },
      },
    } as any);
  }

  it("hides the overlay button when no project is loaded", () => {
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    expect(wrapper.find(".sp-chip-wt-btn").exists()).toBe(false);
  });

  it("shows the overlay button on the chip and lists worktrees in the menu", async () => {
    mockWorktreesFetch();
    setProjectLoaded();
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    expect(wrapper.find(".sp-chip-wt-btn").exists()).toBe(true);
    // Menu content only renders once opened (v-menu stub gates on modelValue).
    expect(wrapper.findAll(".sp-wt-row").length).toBe(0);
    await wrapper.find(".sp-chip-wt-btn").trigger("click");
    await flushPromises();
    // "not specified" option + 2 worktrees
    expect(wrapper.findAll(".sp-wt-row").length).toBe(3);
    expect(wrapper.text()).toContain("main");
    expect(wrapper.text()).toContain("feat");
  });

  it("marks the active worktree with a check and the active button state", async () => {
    mockWorktreesFetch("F:/proj/.worktrees/feat");
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    // Project status arrives AFTER mount (Chat.vue refresh) — that change
    // is what drives the composable's directory watcher to first fetch.
    setProjectLoaded();
    await flushPromises();
    expect(wrapper.find(".sp-chip-wt-btn--active").exists()).toBe(true);
    await wrapper.find(".sp-chip-wt-btn").trigger("click");
    await flushPromises();
    const selected = wrapper.findAll(".sp-wt-row--selected");
    expect(selected.length).toBe(1);
    expect(selected[0].text()).toContain("feat");
    expect(selected[0].find(".sp-wt-row__check").exists()).toBe(true);
  });

  it("selecting a worktree POSTs activate (umo in body) and pops a bubble", async () => {
    mockWorktreesFetch();
    setProjectLoaded();
    mockActivatePost("F:/proj/.worktrees/feat");
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    await wrapper.find(".sp-chip-wt-btn").trigger("click");
    await flushPromises();
    const featRow = wrapper
      .findAll(".sp-wt-row")
      .find((r) => r.text().includes("feat"))!;
    await featRow.trigger("click");
    await flushPromises();
    expect(pluginExtensionApi.post).toHaveBeenCalledWith(
      "spcode/worktree-activate",
      expect.objectContaining({
        path: "F:/proj/.worktrees/feat",
        umo: "webchat-1",
      }),
      expect.anything(),
    );
    expect(wrapper.find(".sp-bubble").exists()).toBe(true);
    expect(wrapper.find(".sp-bubble").text()).toContain("feat");
    // Menu closed after a successful selection.
    expect(wrapper.findAll(".sp-wt-row").length).toBe(0);
  });

  it("the not-specified option deactivates (path null)", async () => {
    mockWorktreesFetch("F:/proj/.worktrees/feat");
    setProjectLoaded();
    mockActivatePost(null);
    const wrapper = mount(SpcodeProjectIndicator, { global: { stubs } });
    await wrapper.find(".sp-chip-wt-btn").trigger("click");
    await flushPromises();
    await wrapper.findAll(".sp-wt-row")[0].trigger("click");
    await flushPromises();
    expect(pluginExtensionApi.post).toHaveBeenCalledWith(
      "spcode/worktree-activate",
      expect.objectContaining({ path: null, umo: "webchat-1" }),
      expect.anything(),
    );
    expect(wrapper.find(".sp-bubble").exists()).toBe(true);
  });
});
