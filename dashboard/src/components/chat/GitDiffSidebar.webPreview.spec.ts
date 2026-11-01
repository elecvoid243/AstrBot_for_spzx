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

vi.mock("vuetify", async (importOriginal) => {
  const actual = await importOriginal<typeof import("vuetify")>();
  const { ref } = await import("vue");
  return { ...actual, useDisplay: () => ({ width: ref(1600) }) };
});

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
  FileBrowserFilePreview: true,
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

const MenuStub = defineComponent({
  setup(_props, { slots }) {
    return () =>
      h("div", [slots.activator?.({ props: {} }), slots.default?.()]);
  },
});

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
  it("switches browser modes from the Web Preview tab's attached menu", async () => {
    const wrapper = mount(GitDiffSidebar, {
      props: { modelValue: true, chatId: "chat-menu" },
      global: { stubs: { ...STUBS, "v-menu": MenuStub } },
    });
    await flushPromises();
    expect(wrapper.find(".browser-mode-switch").exists()).toBe(false);
    expect(
      wrapper
        .get(".git-diff-sidebar-browser-tab .browser-mode-trigger")
        .attributes("aria-label"),
    ).toBe("切换浏览模式");
    await wrapper.get(".browser-mode-agent").trigger("click");
    await flushPromises();
    expect(wrapper.findComponent({ name: "AgentBrowserView" }).exists()).toBe(
      true,
    );
    await tabButtons(wrapper)[0].trigger("click");
    await wrapper.get(".browser-mode-preview").trigger("click");
    await flushPromises();
    expect(localStorage.getItem("astrbot.agentBrowser.mode")).toBe("preview");
    expect(localStorage.getItem(VIEW_MODE_STORAGE_KEY)).toBe("browser");
    expect(wrapper.findComponent(WebBrowserViewStub).exists()).toBe(true);
    wrapper.unmount();
  });

  it("selects the Agent browser before automatic opening even when Files was persisted", async () => {
    localStorage.setItem(VIEW_MODE_STORAGE_KEY, "files");
    localStorage.setItem("astrbot.agentBrowser.mode", "preview");
    const wrapper = mount(GitDiffSidebar, {
      props: { modelValue: false, chatId: "chat-auto" },
      global: { stubs: STUBS },
    });
    await flushPromises();
    expect(await wrapper.vm.prepareAgentBrowser()).toBe(true);
    await wrapper.setProps({ modelValue: true });
    await flushPromises();
    const browser = wrapper.findComponent({ name: "AgentBrowserView" });
    expect(browser.exists()).toBe(true);
    expect(browser.props()).toMatchObject({
      chatId: "chat-auto",
      visible: true,
    });
    expect(localStorage.getItem(VIEW_MODE_STORAGE_KEY)).toBe("browser");
    wrapper.unmount();
  });

  it("does not replace an open Files view for an automatic browser request", async () => {
    const wrapper = await mountSidebar();
    expect(await wrapper.vm.prepareAgentBrowser()).toBe(false);
    expect(wrapper.findComponent({ name: "FileBrowserView" }).exists()).toBe(
      true,
    );
    expect(wrapper.findComponent({ name: "AgentBrowserView" }).exists()).toBe(
      false,
    );
    wrapper.unmount();
  });

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
    expect(tabButtons(wrapper)).toHaveLength(2);
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

  it("keeps a host reference independent of project and worktree status refreshes", async () => {
    const target = {
      path: "G:/outside/source.py",
      startLine: 2,
      endLine: 4,
      requestId: 1,
    };
    const wrapper = mount(GitDiffSidebar, {
      props: { modelValue: true, fileReference: target },
      global: { stubs: STUBS },
    });
    await flushPromises();
    expect(
      wrapper.findComponent({ name: "FileBrowserView" }).props("hostReference"),
    ).toEqual(target);
    const status = useSpcodeProjectStatus();
    status.setUnloaded();
    await flushPromises();
    status.setLoaded(
      status.status.value.umo ?? "reference-test",
      "F:/another-project",
      1,
    );
    await flushPromises();
    expect(
      wrapper.findComponent({ name: "FileBrowserView" }).props("hostReference"),
    ).toEqual(target);
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    wrapper.unmount();
  });

  it("uses the citation lifecycle for manual host directories and files without changing project context", async () => {
    const wrapper = await mountSidebar();
    const project = useSpcodeProjectStatus().status.value.directory;
    wrapper
      .findComponent({ name: "FileBrowserView" })
      .vm.$emit("open-path", "C:/outside");
    await flushPromises();
    const files = wrapper.findComponent({ name: "FileBrowserView" });
    expect(files.props("hostReference")).toMatchObject({
      path: "C:/outside",
      startLine: null,
      requestId: 1,
    });
    files.vm.$emit("navigate", {
      dirPath: "G:/other",
      previewPath: "G:/other/file.py",
    });
    await flushPromises();
    expect(files.props("hostReference")).toMatchObject({
      path: "G:/other/file.py",
      requestId: 2,
    });
    expect(useSpcodeProjectStatus().status.value.directory).toBe(project);
    useSpcodeProjectStatus().setUnloaded();
    await flushPromises();
    expect(files.props("hostReference").path).toBe("G:/other/file.py");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    wrapper.unmount();
  });

  it("provides the existing host folder picker from Files without loading a project", async () => {
    useSpcodeProjectStatus().setUnloaded();
    const wrapper = await mountSidebar();
    await wrapper.setProps({ chatId: "host-chat" });
    await tabButtons(wrapper)[0].trigger("click");
    const picker = wrapper.findComponent({ name: "ProjectDirectoryBrowser" });
    expect(picker.props("modelValue")).toBe(true);
    picker.vm.$emit("select", "C:/temporary");
    await flushPromises();
    expect(
      wrapper.findComponent({ name: "FileBrowserView" }).props("hostReference")
        .path,
    ).toBe("C:/temporary");
    expect(postMock).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it.each(["Files tab", "Git diff open file"])(
    "leaves the citation through the public %s entry",
    async (entry) => {
      getMock.mockImplementation(
        async (path: string, config?: { params?: { path?: string } }) => {
          if (path === "spcode/git-repo-check") {
            return okEnvelope({
              is_git_repo: true,
              git_available: true,
              directory: "F:/github/testproj",
              reason: null,
            });
          }
          if (path === "spcode/git-worktrees")
            return okEnvelope({ worktrees: [] });
          if (path === "spcode/git-diff")
            return okEnvelope({
              loaded: true,
              directory: "F:/github/testproj",
              umo: "session:test",
              scope: "unstaged",
              files_changed: [
                {
                  path: "project.txt",
                  status: "M",
                  additions: 1,
                  deletions: 1,
                },
              ],
              elapsed_ms: 1,
              reason: null,
            });
          if (path === "spcode/file-browser") {
            const target = config?.params?.path ?? "";
            if (target.endsWith(".txt"))
              return okEnvelope({
                type: "file",
                path: target,
                name: "project.txt",
                is_binary: false,
                content: target.startsWith("G:")
                  ? "EXTERNAL CITATION"
                  : "PROJECT FILE CONTENT",
              });
            return okEnvelope({
              type: "directory",
              path: target,
              entries: [
                {
                  path: "F:/github/testproj/project.txt",
                  name: "project.txt",
                  type: "file",
                  is_symlink: false,
                },
              ],
            });
          }
          return okEnvelope({});
        },
      );
      const wrapper = mount(GitDiffSidebar, {
        props: {
          modelValue: true,
          fileReference: {
            path: "G:/outside/reference.txt",
            startLine: null,
            endLine: null,
            requestId: 1,
          },
        },
        global: {
          stubs: {
            ...STUBS,
            FileBrowserFilePreview: false,
            FileBrowserView: false,
            GitDiffBodyContent: false,
            "v-menu": MenuStub,
            DocumentHistoryPanel: true,
            CodeCheckFormatBar: true,
            RecentFilesBlock: true,
          },
        },
      });
      await vi.waitFor(() =>
        expect(wrapper.find(".code-content").text()).toContain(
          "EXTERNAL CITATION",
        ),
      );
      if (entry === "Files tab") {
        await tabButtons(wrapper)[0].trigger("click");
        await flushPromises();
        await vi.waitFor(() =>
          expect(wrapper.find(".file-tree-list").text()).toContain(
            "project.txt",
          ),
        );
        await wrapper.get(".file-browser-search-trigger").trigger("click");
        await wrapper.setProps({
          fileReference: {
            path: "G:/outside/reference.txt",
            startLine: null,
            endLine: null,
            requestId: 99,
          },
        });
        await vi.waitFor(() =>
          expect(wrapper.find(".code-content").text()).toContain(
            "EXTERNAL CITATION",
          ),
        );
        expect(wrapper.find(".file-browser-search-input").exists()).toBe(false);
        await tabButtons(wrapper)[0].trigger("click");
        await flushPromises();
      } else {
        await tabButtons(wrapper)[2].trigger("click");
        await flushPromises();
        await wrapper.get(".git-diff-file-open").trigger("click");
        const openItem = wrapper
          .findAll("v-list-item")
          .find((item) => item.text() === "在文件浏览器中打开");
        expect(openItem).toBeDefined();
        await openItem!.trigger("click");
        await vi.waitFor(() =>
          expect(wrapper.find(".code-content").text()).toContain(
            "PROJECT FILE CONTENT",
          ),
        );
      }
      expect(wrapper.text()).not.toContain("EXTERNAL CITATION");
      expect(wrapper.find(".file-browser-view").exists()).toBe(true);
      wrapper.unmount();
    },
  );
});

describe("explicit project return preserves platform path semantics", () => {
  it.each([
    ["/tmp/A", "/tmp/a", false],
    ["F:/Repo", "f:/repo", true],
  ])(
    "returns from %s to %s through the public Files tab",
    async (before, after, same) => {
      const status = useSpcodeProjectStatus();
      status.status.value = {
        ...status.status.value,
        loaded: true,
        directory: before,
      };
      const wrapper = await mountSidebar();
      const files = wrapper.findComponent({ name: "FileBrowserView" });
      files.vm.$emit("navigate", {
        dirPath: before,
        previewPath: before + "/same.py",
      });
      await flushPromises();
      expect(files.props("previewPath")).toBe(before + "/same.py");
      status.status.value = { ...status.status.value, directory: after };
      await flushPromises();
      await tabButtons(wrapper)[0].trigger("click");
      await flushPromises();
      expect(files.props("previewPath")).toBe(
        same ? before + "/same.py" : null,
      );
      expect(files.props("currentPath")).toBe(same ? before : after);
      wrapper.unmount();
    },
  );
});

describe("host citation retry", () => {
  it.each([
    [40, 40],
    [55, 58],
  ])(
    "keeps source lines %i-%i when Retry recovers a failed read",
    async (startLine, endLine) => {
      let available = false;
      const path = "G:/outside/source.txt";
      getMock.mockImplementation(
        async (route: string, config?: { params?: { path?: string } }) => {
          if (route !== "spcode/file-browser") return okEnvelope({});
          if (config?.params?.path !== path)
            return okEnvelope({
              type: "directory",
              path: config?.params?.path,
              entries: [],
            });
          return available
            ? okEnvelope({
                type: "file",
                path,
                name: "source.txt",
                is_binary: false,
                content: Array.from(
                  { length: 80 },
                  (_, index) => `source line ${index + 1}`,
                ).join("\n"),
              })
            : {
                data: {
                  status: "error",
                  message: "spcode is disabled",
                  data: null,
                },
              };
        },
      );
      const wrapper = mount(GitDiffSidebar, {
        props: {
          modelValue: true,
          fileReference: { path, startLine, endLine, requestId: 1 },
        },
        global: {
          stubs: {
            ...STUBS,
            FileBrowserView: false,
            FileBrowserFilePreview: false,
            DocumentHistoryPanel: true,
            CodeCheckFormatBar: true,
            RecentFilesBlock: true,
            "v-btn": { template: "<button><slot /></button>" },
          },
        },
      });
      await vi.waitFor(() =>
        expect(wrapper.get(".preview-error-detail").text()).toContain(
          "请安装或启用 spcode",
        ),
      );
      available = true;
      await wrapper
        .get(".file-browser-pane-right .preview-center button")
        .trigger("click");
      await vi.waitFor(() =>
        expect(wrapper.findAll(".line-referenced")).toHaveLength(
          endLine - startLine + 1,
        ),
      );
      expect(wrapper.get(".code-content").text()).toContain("source line 80");
      expect(wrapper.findAll(".line-referenced")[0].text()).toContain(
        `source line ${startLine}`,
      );
      expect(
        wrapper
          .findComponent({ name: "FileBrowserView" })
          .props("hostReference"),
      ).toMatchObject({ path, startLine, endLine });
      const files = wrapper.findComponent({ name: "FileBrowserView" });
      files.vm.$emit("open-path", path);
      await flushPromises();
      expect(files.props("hostReference")).toMatchObject({
        path,
        startLine: null,
        endLine: null,
      });
      wrapper.unmount();
    },
  );
});
