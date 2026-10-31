// Author: elecvoid243 @ 2026-10-22
// Wiring test for the sixth workbench tab: it must be absent until first
// activation, then stay mounted (v-show) so switching tabs does not
// reload the previewed page.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: getMock, post: postMock },
}));

import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import GitDiffSidebar from "./GitDiffSidebar.vue";

const VIEW_MODE_STORAGE_KEY = "astrbot.spcode.gitDiffSidebar.viewMode";

function okEnvelope(inner: unknown) {
  return { data: { status: "ok", data: inner } };
}

const WebBrowserViewStub = defineComponent({
  name: "WebBrowserView",
  props: { projectRoot: { type: String, default: null } },
  render: () => h("div", { class: "web-browser-stub" }),
});

const STUBS = {
  FileBrowserView: true,
  DocumentManager: true,
  GitDiffBodyContent: true,
  GitCommitBar: true,
  GitLogView: true,
  GitStatsPanel: true,
  GitIgnoreEditor: true,
  GitConflictPanel: true,
  GitPullDialog: true,
  GitPushDialog: true,
  GitRemoteUrlDialog: true,
  GitStashDialog: true,
  GitMergeDialog: true,
  GitCherryPickDialog: true,
  GitSquashDialog: true,
  GitChangelogDialog: true,
  GitCommitDialog: true,
  GitCommitAmendDialog: true,
  WorktreeCreateDialog: true,
  LockReasonDialogBody: true,
  BranchSwitchConfirmDialog: true,
  BranchDeleteConfirmDialog: true,
  WebBrowserView: WebBrowserViewStub,
  AgentBrowserView: true,
  TerminalView: true,
};

async function mountSidebar() {
  const wrapper = mount(GitDiffSidebar, {
    props: { modelValue: true },
    global: { stubs: STUBS },
  });
  await flushPromises();
  return wrapper;
}

function tabButtons(wrapper: Awaited<ReturnType<typeof mountSidebar>>) {
  return wrapper.findAll(".git-diff-sidebar-view-tab");
}

/** happy-dom's checkVisibility ignores inline display, so assert v-show directly. */
function isHidden(element: Element): boolean {
  return (element.getAttribute("style") ?? "").includes("display: none");
}

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.removeItem(VIEW_MODE_STORAGE_KEY);
  localStorage.removeItem("astrbot.agentBrowser.mode");
  getMock.mockReset();
  postMock.mockReset();
  getMock.mockImplementation(async (path: string) => {
    if (path === "spcode/git-repo-check") {
      return okEnvelope({
        is_git_repo: true,
        git_available: true,
        directory: "F:/github/testproj",
        reason: null,
        stderr: "",
        elapsed_ms: 1,
      });
    }
    if (path === "spcode/git-worktrees") {
      return okEnvelope({ worktrees: [] });
    }
    return okEnvelope({});
  });
  const status = useSpcodeProjectStatus();
  status.status.value = {
    loaded: true,
    directory: "F:/github/testproj",
    loadedAt: 1,
    umo: "session:test",
    allLoadedCount: 1,
    fetchedAt: 1,
    bootId: null,
  };
});

describe("GitDiffSidebar web preview tab", () => {
  it("retains each project view and the mounted iframe while navigating", async () => {
    const wrapper = await mountSidebar();
    await tabButtons(wrapper)[5].trigger("click");
    const preview = wrapper.findComponent(WebBrowserViewStub).vm;
    for (const [index, name] of [
      "FileBrowserView",
      "DocumentManager",
      "GitDiffBodyContent",
      "GitLogView",
      "TerminalView",
    ].entries()) {
      await tabButtons(wrapper)[index].trigger("click");
      await flushPromises();
      expect(wrapper.findComponent({ name }).exists()).toBe(true);
      expect(wrapper.findComponent(WebBrowserViewStub).vm).toBe(preview);
      expect(isHidden(wrapper.find(".web-browser-stub").element)).toBe(true);
    }
    wrapper.unmount();
  });

  it("opens the Agent browser without a project and keeps it open on project unload", async () => {
    const status = useSpcodeProjectStatus();
    status.status.value = {
      ...status.status.value,
      loaded: false,
      directory: null,
    };
    const wrapper = await mountSidebar();
    await wrapper.setProps({ chatId: "chat-1" });
    expect(tabButtons(wrapper)).toHaveLength(1);
    expect(wrapper.findComponent({ name: "AgentBrowserView" }).exists()).toBe(
      true,
    );
    status.status.value = {
      ...status.status.value,
      loaded: true,
      directory: "F:/repo",
    };
    await flushPromises();
    status.status.value = {
      ...status.status.value,
      loaded: false,
      directory: null,
    };
    await flushPromises();
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    expect(
      wrapper.findComponent({ name: "AgentBrowserView" }).props("chatId"),
    ).toBe("chat-1");
    wrapper.unmount();
  });

  it("renders six tabs and mounts the preview lazily", async () => {
    const wrapper = await mountSidebar();
    const tabs = tabButtons(wrapper);
    expect(tabs).toHaveLength(6);
    expect(tabs[5].text()).toContain("Web 预览");
    expect(wrapper.find(".web-browser-stub").exists()).toBe(false);

    await tabs[5].trigger("click");
    await flushPromises();
    const preview = wrapper.find(".web-browser-stub");
    expect(preview.exists()).toBe(true);
    expect(isHidden(preview.element)).toBe(false);
    expect(wrapper.findComponent(WebBrowserViewStub).props("projectRoot")).toBe(
      "F:/github/testproj",
    );
    wrapper.unmount();
  });

  it("keeps the preview mounted but hidden after switching away", async () => {
    const wrapper = await mountSidebar();
    await tabButtons(wrapper)[5].trigger("click");
    await flushPromises();

    await tabButtons(wrapper)[0].trigger("click");
    await flushPromises();
    const preview = wrapper.find(".web-browser-stub");
    expect(preview.exists()).toBe(true);
    expect(isHidden(preview.element)).toBe(true);
    wrapper.unmount();
  });
});
