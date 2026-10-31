// dashboard/src/components/chat/ProjectDirectoryBrowser.spec.ts
// Author: elecvoid243, 2026-08-15
//
// Component tests for the spcode in-app directory browser: renders only
// directories, double-click enters, confirm emits a backend path, and
// empty / error / truncated states render.

import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { defineComponent, nextTick } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getMock = vi.fn();
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    get: (...args: unknown[]) => getMock(...args),
    post: vi.fn(),
  },
}));

import ProjectDirectoryBrowser from "./ProjectDirectoryBrowser.vue";
import { initI18n } from "@/i18n/composables";

const stubs = {
  "v-dialog": defineComponent({
    props: { modelValue: { type: Boolean, default: false } },
    template: `<div v-if="modelValue"><slot /></div>`,
  }),
  "v-card": { template: "<div><slot /></div>" },
  "v-card-title": { template: "<div><slot /></div>" },
  "v-card-text": { template: "<div><slot /></div>" },
  "v-card-actions": { template: "<div><slot /></div>" },
  "v-spacer": { template: "<span />" },
  "v-btn": defineComponent({
    props: ["disabled", "title"],
    emits: ["click"],
    template: `<button :disabled="disabled" :title="title" @click="$emit('click')"><slot /><slot name="prepend" /></button>`,
  }),
  "v-icon": { template: "<i><slot /></i>" },
  "v-alert": { template: '<div data-testid="alert"><slot /></div>' },
  "v-progress-circular": { template: '<span data-testid="loading" />' },
  "v-text-field": defineComponent({
    props: ["modelValue", "placeholder", "error"],
    emits: ["update:modelValue"],
    template: `<input :value="modelValue" :placeholder="placeholder" @input="$emit('update:modelValue', $event.target.value)" />`,
  }),
  "v-list": { template: "<div><slot /></div>" },
  "v-list-item": defineComponent({
    emits: ["click", "dblclick"],
    template: `<div data-testid="dir-item" @click="$emit('click')" @dblclick="$emit('dblclick')"><slot name="prepend" /><slot /></div>`,
  }),
  "v-list-item-title": { template: "<span><slot /></span>" },
};

function dirEntry(name: string, path: string): Record<string, unknown> {
  return {
    path,
    name,
    type: "directory",
    size: null,
    mtime: 0,
    is_symlink: false,
  };
}
function fileEntry(name: string, path: string): Record<string, unknown> {
  return { path, name, type: "file", size: 3, mtime: 0, is_symlink: false };
}

function mockDirListing(entries: Record<string, unknown>[]) {
  getMock.mockImplementation(
    (endpoint: string, config?: { params?: { path?: string } }) => {
      if (endpoint === "spcode/home-directory") {
        return Promise.resolve({ data: { data: { home: "/home/user" } } });
      }
      if (endpoint === "spcode/drives") {
        return Promise.resolve({
          data: { data: { drives: ["C:\\", "D:\\"] } },
        });
      }
      const path = config?.params?.path ?? "";
      if (path === "/home/user") {
        return Promise.resolve({
          data: { data: { type: "directory", path, entries } },
        });
      }
      // Nested listing (double-click target etc.)
      return Promise.resolve({
        data: {
          data: {
            type: "directory",
            path,
            entries: [dirEntry("leaf", `${path}/leaf`)],
          },
        },
      });
    },
  );
}

async function flush(): Promise<void> {
  await flushPromises();
  await nextTick();
}

function mountBrowser(): VueWrapper {
  return mount(ProjectDirectoryBrowser, {
    props: { modelValue: true },
    global: { stubs },
  });
}

beforeEach(async () => {
  getMock.mockReset();
  await initI18n("zh-CN");
});

describe("ProjectDirectoryBrowser", () => {
  it("renders only directories (files are filtered out)", async () => {
    mockDirListing([
      dirEntry("src", "/home/user/src"),
      dirEntry("docs", "/home/user/docs"),
      fileEntry("notes.txt", "/home/user/notes.txt"),
    ]);
    const wrapper = mountBrowser();
    await flush();
    expect(wrapper.text()).toContain("src");
    expect(wrapper.text()).toContain("docs");
    expect(wrapper.text()).not.toContain("notes.txt");
  });

  it("double-click enters a directory and re-lists it", async () => {
    mockDirListing([dirEntry("src", "/home/user/src")]);
    const wrapper = mountBrowser();
    await flush();

    await wrapper.find('[data-testid="dir-item"]').trigger("dblclick");
    await flush();

    // The nested listing for "/home/user/src" returned ["leaf"].
    expect(wrapper.text()).toContain("leaf");
    // A file-browser call for the entered path was issued.
    expect(getMock).toHaveBeenCalledWith("spcode/file-browser", {
      params: { path: "/home/user/src" },
    });
  });

  it("confirm emits the browsed folder when nothing is selected", async () => {
    mockDirListing([dirEntry("src", "/home/user/src")]);
    const wrapper = mountBrowser();
    await flush();
    await wrapper.find('[data-testid="dir-confirm"]').trigger("click");
    expect(wrapper.emitted("select")).toEqual([["/home/user"]]);
  });

  it("confirm emits a clicked sub-directory path", async () => {
    mockDirListing([dirEntry("src", "/home/user/src")]);
    const wrapper = mountBrowser();
    await flush();
    await wrapper.find('[data-testid="dir-item"]').trigger("click");
    await wrapper.find('[data-testid="dir-confirm"]').trigger("click");
    expect(wrapper.emitted("select")).toEqual([["/home/user/src"]]);
  });

  it("shows the empty state for an empty folder", async () => {
    mockDirListing([]);
    const wrapper = mountBrowser();
    await flush();
    expect(wrapper.text()).toContain("此文件夹为空");
  });

  it("shows a mapped error message on backend failure", async () => {
    getMock.mockImplementation(
      (endpoint: string, config?: { params?: { path?: string } }) => {
        if (endpoint === "spcode/home-directory") {
          return Promise.resolve({ data: { data: { home: "/home/user" } } });
        }
        const path = config?.params?.path ?? "";
        return Promise.resolve({
          data: { data: { type: null, path, reason: "path_not_found" } },
        });
      },
    );
    const wrapper = mountBrowser();
    await flush();
    expect(wrapper.text()).toContain("路径不存在或已被删除");
  });

  it("navigates up via the up button", async () => {
    getMock.mockImplementation(
      (endpoint: string, config?: { params?: { path?: string } }) => {
        if (endpoint === "spcode/home-directory") {
          return Promise.resolve({ data: { data: { home: "/home/user" } } });
        }
        const path = config?.params?.path ?? "";
        if (path === "/home/user") {
          return Promise.resolve({
            data: {
              data: {
                type: "directory",
                path,
                entries: [dirEntry("sub", "/home/user/sub")],
              },
            },
          });
        }
        return Promise.resolve({
          data: {
            data: {
              type: "directory",
              path,
              entries: [dirEntry("leaf", `${path}/leaf`)],
            },
          },
        });
      },
    );
    const wrapper = mountBrowser();
    await flush();
    // Enter "/home/user/sub".
    await wrapper.find('[data-testid="dir-item"]').trigger("dblclick");
    await flush();
    expect(wrapper.text()).toContain("leaf");
    // Up → back to "/home/user".
    await wrapper.find('[data-testid="dir-up"]').trigger("click");
    await flush();
    expect(wrapper.text()).toContain("sub");
  });

  it("disables the up button at the POSIX root (no parent)", async () => {
    getMock.mockImplementation(
      (endpoint: string, config?: { params?: { path?: string } }) => {
        if (endpoint === "spcode/home-directory") {
          return Promise.resolve({ data: { data: { home: "/" } } });
        }
        const path = config?.params?.path ?? "";
        return Promise.resolve({
          data: {
            data: { type: "directory", path, entries: [dirEntry("a", "/a")] },
          },
        });
      },
    );
    const wrapper = mountBrowser();
    await flush();
    const up = wrapper.find('[data-testid="dir-up"]');
    expect(up.attributes("disabled")).toBeDefined();
  });

  it("at a Windows drive root, up opens the This PC drive list", async () => {
    getMock.mockImplementation(
      (endpoint: string, config?: { params?: { path?: string } }) => {
        if (endpoint === "spcode/home-directory") {
          return Promise.resolve({ data: { data: { home: "C:\\" } } });
        }
        if (endpoint === "spcode/drives") {
          return Promise.resolve({
            data: { data: { drives: ["C:\\", "D:\\"] } },
          });
        }
        const path = config?.params?.path ?? "";
        return Promise.resolve({
          data: {
            data: {
              type: "directory",
              path,
              entries: [dirEntry("a", "C:\\a")],
            },
          },
        });
      },
    );
    const wrapper = mountBrowser();
    await flush();
    // At "C:\" the up button is available (moves to This PC).
    const up = wrapper.find('[data-testid="dir-up"]');
    expect(up.attributes("disabled")).toBeUndefined();
    await up.trigger("click");
    await flush();
    // This-PC view: drive list rendered, up disabled.
    expect(wrapper.text()).toContain("此电脑");
    expect(wrapper.text()).toContain("D:\\");
    expect(
      wrapper.find('[data-testid="dir-up"]').attributes("disabled"),
    ).toBeDefined();
  });

  it("renders the drive list and enters a drive on double-click", async () => {
    getMock.mockImplementation(
      (endpoint: string, config?: { params?: { path?: string } }) => {
        if (endpoint === "spcode/home-directory") {
          return Promise.resolve({ data: { data: { home: "/home/user" } } });
        }
        if (endpoint === "spcode/drives") {
          return Promise.resolve({
            data: { data: { drives: ["C:\\", "D:\\"] } },
          });
        }
        const path = config?.params?.path ?? "";
        return Promise.resolve({
          data: {
            data: {
              type: "directory",
              path,
              entries: [dirEntry("proj", `${path}proj`)],
            },
          },
        });
      },
    );
    const wrapper = mountBrowser();
    await flush();
    // Open This PC via the breadcrumb.
    await wrapper.find('[data-testid="dir-computer"]').trigger("click");
    await flush();
    expect(wrapper.text()).toContain("C:\\");
    expect(wrapper.text()).toContain("D:\\");

    // Double-click "D:\" enters that drive.
    const driveItems = wrapper.findAll('[data-testid="dir-item"]');
    const dDrive = driveItems.find((i) => i.text().includes("D:\\"));
    expect(dDrive).toBeDefined();
    await dDrive!.trigger("dblclick");
    await flush();
    expect(getMock).toHaveBeenCalledWith("spcode/file-browser", {
      params: { path: "D:\\" },
    });
    expect(wrapper.text()).toContain("proj");
  });

  it("applies the blue selected class and emits a selected drive path", async () => {
    getMock.mockImplementation(
      (endpoint: string, config?: { params?: { path?: string } }) => {
        if (endpoint === "spcode/home-directory") {
          return Promise.resolve({ data: { data: { home: "/home/user" } } });
        }
        if (endpoint === "spcode/drives") {
          return Promise.resolve({
            data: { data: { drives: ["C:\\", "D:\\"] } },
          });
        }
        const path = config?.params?.path ?? "";
        return Promise.resolve({
          data: { data: { type: "directory", path, entries: [] } },
        });
      },
    );
    const wrapper = mountBrowser();
    await flush();
    await wrapper.find('[data-testid="dir-computer"]').trigger("click");
    await flush();
    // Click a drive → selected (blue highlight class, no black overlay rule).
    await wrapper.find('[data-testid="dir-item"]').trigger("click");
    const selected = wrapper.find(".dir-item--selected");
    expect(selected.exists()).toBe(true);
    expect(selected.text()).toContain("C:\\");
    // Confirm emits the selected drive path.
    await wrapper.find('[data-testid="dir-confirm"]').trigger("click");
    expect(wrapper.emitted("select")).toEqual([["C:\\"]]);
  });

  it("shows the no-drives hint when the drive list is empty", async () => {
    getMock.mockImplementation(
      (endpoint: string, config?: { params?: { path?: string } }) => {
        if (endpoint === "spcode/home-directory") {
          return Promise.resolve({ data: { data: { home: "/home/user" } } });
        }
        if (endpoint === "spcode/drives") {
          return Promise.resolve({ data: { data: { drives: [] } } });
        }
        const path = config?.params?.path ?? "";
        return Promise.resolve({
          data: { data: { type: "directory", path, entries: [] } },
        });
      },
    );
    const wrapper = mountBrowser();
    await flush();
    await wrapper.find('[data-testid="dir-computer"]').trigger("click");
    await flush();
    expect(wrapper.text()).toContain("没有可用的磁盘");
  });

  it("surfaces an error when the drives response lacks the drives array", async () => {
    getMock.mockImplementation(
      (endpoint: string, config?: { params?: { path?: string } }) => {
        if (endpoint === "spcode/home-directory") {
          return Promise.resolve({ data: { data: { home: "/home/user" } } });
        }
        if (endpoint === "spcode/drives") {
          // Route missing (plugin not reloaded) → empty envelope without drives.
          return Promise.resolve({ data: { data: {} } });
        }
        const path = config?.params?.path ?? "";
        return Promise.resolve({
          data: { data: { type: "directory", path, entries: [] } },
        });
      },
    );
    const wrapper = mountBrowser();
    await flush();
    await wrapper.find('[data-testid="dir-computer"]').trigger("click");
    await flush();
    expect(wrapper.text()).toContain("加载失败，请重试");
  });

  it("jumps to a manually typed absolute path on Enter", async () => {
    mockDirListing([dirEntry("src", "/home/user/src")]);
    const wrapper = mountBrowser();
    await flush();

    await wrapper.get('[data-testid="dir-path-input"]').setValue("D:\\repo");
    await wrapper
      .get('[data-testid="dir-path-input"]')
      .trigger("keydown.enter");
    await flush();

    expect(getMock).toHaveBeenCalledWith("spcode/file-browser", {
      params: { path: "D:\\repo" },
    });
    // The nested listing mock returns ["leaf"] for any non-home path.
    expect(wrapper.text()).toContain("leaf");
  });

  it("shows a hint and skips the request for a non-absolute jump input", async () => {
    mockDirListing([]);
    const wrapper = mountBrowser();
    await flush();

    await wrapper.get('[data-testid="dir-path-input"]').setValue("repo/foo");
    await wrapper.get('[data-testid="dir-jump"]').trigger("click");
    await flush();

    expect(wrapper.text()).toContain("请输入绝对路径");
    expect(getMock).not.toHaveBeenCalledWith("spcode/file-browser", {
      params: { path: "repo/foo" },
    });
  });
});

describe("host directory service feedback", () => {
  it.each(["zh-CN", "en-US"] as const)(
    "renders enable-plugin guidance in %s for real error envelopes without probing parents",
    async (locale) => {
      await initI18n(locale);
      const expected =
        locale === "zh-CN"
          ? "文件预览需要 spcode。请安装或启用 spcode 插件并检查服务后重试。"
          : "File preview requires spcode. Install or enable the spcode plugin and check its service, then retry.";
      for (const data of [{}, null]) {
        getMock.mockReset();
        getMock.mockImplementation(async (route: string) =>
          route === "spcode/home-directory"
            ? {
                data: {
                  status: "ok",
                  data: { home: "C:/fixture/host-folder" },
                },
              }
            : { data: { status: "error", message: "未找到该路由", data } },
        );
        const wrapper = mount(ProjectDirectoryBrowser, {
          props: { modelValue: true, hostBrowsing: true },
          global: { stubs },
        });
        await flush();
        expect(wrapper.get('[data-testid="alert"]').text()).toBe(expected);
        expect(wrapper.text()).not.toContain("[MISSING:");
        expect(wrapper.emitted("select")).toBeUndefined();
        expect(
          getMock.mock.calls.filter(
            ([route]) => route === "spcode/file-browser",
          ),
        ).toEqual([
          [
            "spcode/file-browser",
            { params: { path: "C:/fixture/host-folder", scope: "host" } },
          ],
        ]);
        wrapper.unmount();
      }
    },
  );
});
