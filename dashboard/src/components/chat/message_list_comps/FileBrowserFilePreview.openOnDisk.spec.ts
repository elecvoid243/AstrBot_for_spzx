import { describe, expect, it, vi } from "vitest";
import { shallowMount } from "@vue/test-utils";
import { ref } from "vue";
import { createPinia, setActivePinia } from "pinia";
import FileBrowserFilePreview from "./FileBrowserFilePreview.vue";
import FileBrowserCodeView from "./FileBrowserCodeView.vue";
import { ASSISTANT_FILE_COMMENTS } from "@/composables/fileReference";
import type { FileBrowserFetchState } from "@/composables/useSpcodeFileBrowser";

const { openOnDisk } = vi.hoisted(() => ({ openOnDisk: vi.fn() }));
vi.mock("@/composables/useOpenOnDisk", () => ({
  useOpenOnDisk: () => ({ opening: ref(false), openOnDisk }),
}));
vi.mock("vuetify", async (importOriginal) => ({
  ...(await importOriginal<typeof import("vuetify")>()),
  useDisplay: () => ({ width: ref(1600) }),
}));

describe("file preview open on disk", () => {
  it("opens the resolved host reference in the host's default application", async () => {
    setActivePinia(createPinia());
    const wrapper = shallowMount(FileBrowserFilePreview, {
      props: {
        isDark: false,
        hostReference: {
          path: "G:/outside/file.py",
          startLine: null,
          endLine: null,
          requestId: 1,
        },
        state: {
          kind: "file",
          snapshot: {
            meta: {
              path: "G:/outside/file.py",
              name: "file.py",
              size: 4,
              mtime: null,
              maxBytes: 1024,
              encoding: "utf-8",
              isBinary: false,
              reason: null,
              elapsedMs: 1,
              fetchedAt: 1,
            },
            content: "test",
          },
        },
      },
      global: {
        stubs: { "v-btn": { template: "<button><slot /></button>" } },
      },
    });
    const button = wrapper
      .findAll("button")
      .find((item) => item.text() === "在磁盘中打开");
    expect(button).toBeDefined();
    await button!.trigger("click");
    expect(openOnDisk).toHaveBeenCalledWith("G:/outside/file.py", "file.py");
    wrapper.unmount();
  });
});

it("shows the selected commentary only for the matching source revision", async () => {
  setActivePinia(createPinia());
  const comment = {
    title: "Finding", body: "Explanation", file: "file.py", path: "G:/repo/file.py",
    start_line: 2, end_line: 2, priority: 2, revision: "a".repeat(64), directive: "review",
  };
  const state: FileBrowserFetchState = {
    kind: "file",
    snapshot: {
      meta: {
        path: "G:\\repo\\file.py", name: "file.py", size: 13, mtime: null,
        maxBytes: 1024, encoding: "utf-8", isBinary: false, reason: null,
        elapsedMs: 1, fetchedAt: 1, revision: comment.revision,
      },
      content: "first\nsecond\n",
    },
  };
  const comments = ref([comment]);
  const wrapper = shallowMount(FileBrowserFilePreview, {
    props: {
      isDark: false, state, scrollToLine: 2, scrollToEndLine: 2,
      hostReference: {
        path: comment.path, startLine: 2, endLine: 2, requestId: 1, assistantComment: comment,
      },
    },
    global: { provide: { [ASSISTANT_FILE_COMMENTS as symbol]: comments } },
  });
  const code = wrapper.getComponent(FileBrowserCodeView);
  expect(code.props("assistantComments")).toEqual([comment]);
  expect(code.props("focusedAssistantComment")).toEqual(comment);
  expect(code.props("scrollToLine")).toBe(2);
  await wrapper.setProps({ state: {
    ...state,
    snapshot: { ...state.snapshot, meta: { ...state.snapshot.meta, revision: "b".repeat(64) } },
  } });
  expect(code.props("assistantComments")).toEqual([]);
  expect(code.props("scrollToLine")).toBeNull();
  expect(wrapper.get('[role="status"]').text()).toContain("原位置");
  await wrapper.setProps({ state, hostReference: null, scrollToLine: null });
  expect(code.props("assistantComments")).toEqual([comment]);
  comments.value = [];
  await wrapper.vm.$nextTick();
  expect(code.props("assistantComments")).toEqual([]);
  wrapper.unmount();
});
