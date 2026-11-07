import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";
import { createPinia, setActivePinia } from "pinia";
import type { FileBrowserFetchState } from "@/composables/useSpcodeFileBrowser";

const { get, confirmLeave } = vi.hoisted(() => ({
  get: vi.fn(),
  confirmLeave: vi.fn(),
}));
vi.mock("@/api/v1", () => ({ pluginExtensionApi: { get } }));

import FileBrowserView from "./FileBrowserView.vue";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { useSpcodeGitLog } from "@/composables/useSpcodeGitLog";
import { useSpcodeGitShow } from "@/composables/useSpcodeGitShow";

enableAutoUnmount(afterEach);

/** Build a resolved host directory snapshot for navigation tests.
 *
 * Args:
 *   path: Absolute path returned by the directory service.
 *
 * Returns:
 *   A successful directory state with no entries.
 */
function directory(path: string): FileBrowserFetchState {
  return {
    kind: "directory",
    snapshot: {
      meta: {
        path,
        entryCount: 0,
        truncated: false,
        maxEntries: 1000,
        reason: null,
        elapsedMs: 1,
        fetchedAt: 1,
      },
      entries: [],
    },
  };
}

/** Mount the real browser with lightweight preview and toolbar controls.
 *
 * Returns:
 *   The mounted file browser with real navigation and data composables.
 */
function mountBrowser() {
  return mount(FileBrowserView, {
    props: {
      currentPath: "/repo",
      previewPath: null,
      rootPath: "/active-worktree",
      gitLog: useSpcodeGitLog(null, false),
      gitShow: useSpcodeGitShow(),
    },
    global: {
      stubs: {
        "v-btn": {
          props: ["disabled", "icon"],
          template:
            '<button :disabled="disabled" :data-icon="icon"><slot /></button>',
        },
        FileBrowserFilePreview: defineComponent({
          setup(_, { expose }) {
            expose({ confirmLeaveEditing: confirmLeave });
          },
          template: "<div />",
        }),
        FileTreeList: true,
        SearchPanel: true,
        DocumentHistoryPanel: true,
        RecentFilesBlock: true,
      },
    },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
  confirmLeave.mockReset().mockResolvedValue(true);
  get.mockReset().mockImplementation(async (_route, options) => ({
    data: {
      status: "ok",
      data: {
        type: "directory",
        path: options.params.path,
        name: "directory",
        size: 0,
        mtime: null,
        is_symlink: false,
        entries: [],
        entry_count: 0,
        truncated: false,
        max_entries: 1000,
        elapsed_ms: 1,
      },
    },
  }));
  useSpcodeProjectStatus().status.value = {
    loaded: true,
    directory: "/repo",
    loadedAt: 1,
    umo: "session:test",
    allLoadedCount: 1,
    fetchedAt: 1,
    bootId: null,
    agentsMd: false,
  };
});

describe("FileBrowserView directory shortcuts", () => {
  it("goes back through visited directories without bouncing between them", async () => {
    const wrapper = mountBrowser();
    await flushPromises();
    expect(
      wrapper.get('[data-icon="mdi-arrow-left"]').attributes(),
    ).toHaveProperty("disabled");
    await wrapper.setProps({ currentPath: "/repo/src" });
    await flushPromises();
    await wrapper.setProps({ currentPath: "/repo/src/deep" });
    await flushPromises();
    await wrapper.get('[data-icon="mdi-arrow-left"]').trigger("click");
    expect(wrapper.emitted("navigate")?.at(-1)).toEqual([
      { dirPath: "/repo/src", previewPath: null },
    ]);
    await wrapper.setProps({ currentPath: "/repo/src" });
    await flushPromises();
    await wrapper.get('[data-icon="mdi-arrow-left"]').trigger("click");
    expect(wrapper.emitted("navigate")?.at(-1)).toEqual([
      { dirPath: "/repo", previewPath: null },
    ]);
    await wrapper.setProps({ currentPath: "/repo" });
    await flushPromises();
    expect(
      wrapper.get('[data-icon="mdi-arrow-left"]').attributes(),
    ).toHaveProperty("disabled");
  });

  it("returns to the loaded project independently of the active worktree", async () => {
    const wrapper = mountBrowser();
    await flushPromises();
    await wrapper.setProps({ currentPath: "/outside" });
    await flushPromises();
    await wrapper.get('[data-icon="mdi-folder-home-outline"]').trigger("click");
    expect(wrapper.emitted("navigate")?.at(-1)).toEqual([
      { dirPath: "/repo", previewPath: null },
    ]);
    await wrapper.setProps({ currentPath: "/repo" });
    await flushPromises();
    await wrapper.get('[data-icon="mdi-arrow-left"]').trigger("click");
    expect(wrapper.emitted("navigate")?.at(-1)).toEqual([
      { dirPath: "/outside", previewPath: null },
    ]);

    const status = useSpcodeProjectStatus();
    status.status.value = {
      ...status.status.value,
      loaded: false,
      directory: null,
    };
    await flushPromises();
    expect(
      wrapper.get('[data-icon="mdi-folder-home-outline"]').attributes(),
    ).toHaveProperty("disabled");
    expect(
      wrapper.get('[data-icon="mdi-arrow-left"]').attributes(),
    ).not.toHaveProperty("disabled");
  });

  it("preserves drafts and back history when leaving is cancelled", async () => {
    const wrapper = mountBrowser();
    await flushPromises();
    await wrapper.setProps({
      currentPath: "/repo/src",
      previewPath: "/repo/src/file.txt",
    });
    await flushPromises();
    confirmLeave.mockResolvedValue(false);
    await wrapper.get('[data-icon="mdi-arrow-left"]').trigger("click");
    await wrapper.get('[data-icon="mdi-folder-home-outline"]').trigger("click");
    expect(confirmLeave).toHaveBeenCalledTimes(2);
    expect(wrapper.emitted("navigate")).toBeUndefined();
    confirmLeave.mockResolvedValue(true);
    await wrapper.get('[data-icon="mdi-arrow-left"]').trigger("click");
    await flushPromises();
    expect(wrapper.emitted("navigate")?.at(-1)).toEqual([
      { dirPath: "/repo", previewPath: null },
    ]);
  });

  it("ignores file changes in the same directory and temporary host loading paths", async () => {
    const wrapper = mountBrowser();
    await flushPromises();
    await wrapper.setProps({ previewPath: "/repo/first.txt" });
    await flushPromises();
    await wrapper.setProps({ previewPath: "/repo/second.txt" });
    await flushPromises();
    expect(
      wrapper.get('[data-icon="mdi-arrow-left"]').attributes(),
    ).toHaveProperty("disabled");

    await wrapper.setProps({
      currentPath: "C:/outside",
      previewPath: null,
      hostReference: {
        path: "C:/outside/child",
        requestId: 1,
        startLine: null,
        endLine: null,
      },
      hostState: { kind: "loading" },
    });
    await flushPromises();
    await wrapper.setProps({
      currentPath: "C:/outside/child",
      hostState: directory("C:/outside/child"),
    });
    await flushPromises();
    await wrapper.get('[data-icon="mdi-arrow-left"]').trigger("click");
    expect(wrapper.emitted("navigate")?.at(-1)).toEqual([
      { dirPath: "/repo", previewPath: null },
    ]);
    await wrapper.setProps({ currentPath: "/repo", hostReference: null });
    await flushPromises();
    expect(
      wrapper.get('[data-icon="mdi-arrow-left"]').attributes(),
    ).toHaveProperty("disabled");
  });

  it("does not add failed directory loads to back history", async () => {
    const wrapper = mountBrowser();
    await flushPromises();
    get.mockRejectedValueOnce(new Error("Missing directory"));
    await wrapper.setProps({ currentPath: "/missing" });
    await flushPromises();
    await wrapper.setProps({ currentPath: "/repo/next" });
    await flushPromises();
    await wrapper.get('[data-icon="mdi-arrow-left"]').trigger("click");
    expect(wrapper.emitted("navigate")?.at(-1)).toEqual([
      { dirPath: "/repo", previewPath: null },
    ]);
  });

  it("consumes Windows back history when the service changes path casing", async () => {
    const wrapper = mountBrowser();
    await wrapper.setProps({
      currentPath: "C:/Repo",
      hostReference: {
        path: "C:/Repo",
        requestId: 1,
        startLine: null,
        endLine: null,
      },
      hostState: directory("C:/Repo"),
    });
    await flushPromises();
    await wrapper.setProps({
      currentPath: "C:/Repo/src",
      hostState: directory("C:/Repo/src"),
    });
    await flushPromises();
    await wrapper.get('[data-icon="mdi-arrow-left"]').trigger("click");
    expect(wrapper.emitted("navigate")?.at(-1)).toEqual([
      { dirPath: "C:/Repo", previewPath: null },
    ]);
    await wrapper.setProps({
      currentPath: "c:/repo",
      hostState: directory("c:/repo"),
    });
    await flushPromises();
    expect(
      wrapper.get('[data-icon="mdi-arrow-left"]').attributes(),
    ).toHaveProperty("disabled");
  });
});
