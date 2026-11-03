import { describe, expect, it, vi } from "vitest";
import { shallowMount } from "@vue/test-utils";
import { ref } from "vue";
import { createPinia, setActivePinia } from "pinia";
import FileBrowserFilePreview from "./FileBrowserFilePreview.vue";

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
