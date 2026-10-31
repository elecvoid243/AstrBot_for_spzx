import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import FileBrowserBreadcrumb from "./FileBrowserBreadcrumb.vue";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/api/v1", () => ({ pluginExtensionApi: { get } }));

beforeEach(() => get.mockReset());

describe("manual host path navigation", () => {
  it.each(["C:/outside/file.py", "/tmp/中文 notes.py"])(
    "emits %s as one navigation intent without a competing type probe",
    async (path) => {
      const wrapper = mount(FileBrowserBreadcrumb, {
        props: {
          currentPath: "G:/project/src",
          rootPath: "G:/project/src",
          hostBrowsing: true,
        },
        global: { stubs: { "v-icon": true } },
      });
      await wrapper.get(".breadcrumb-leaf").trigger("click");
      await wrapper.get("input").setValue(path);
      await wrapper.get("input").trigger("keydown.enter");
      expect(wrapper.emitted("open-path")).toEqual([[path]]);
      expect(get).not.toHaveBeenCalled();
      wrapper.unmount();
    },
  );

  it("shows drive and ancestors outside the project root and resolves relative host navigation", async () => {
    const wrapper = mount(FileBrowserBreadcrumb, {
      props: {
        currentPath: "C:/outside/child",
        rootPath: "C:/outside/child",
        hostBrowsing: true,
      },
      global: { stubs: { "v-icon": true } },
    });
    expect(
      wrapper.findAll(".breadcrumb-segment").map((button) => button.text()),
    ).toEqual(["C:/", "outside"]);
    await wrapper.get(".breadcrumb-segment").trigger("click");
    expect(wrapper.emitted("navigate")).toEqual([
      [{ dirPath: "C:/", previewPath: null }],
    ]);
    await wrapper.get(".breadcrumb-leaf").trigger("click");
    await wrapper.get("input").setValue("../sibling");
    await wrapper.get("input").trigger("keydown.enter");
    expect(wrapper.emitted("open-path")).toEqual([
      ["C:/outside/child/../sibling"],
    ]);
    wrapper.unmount();
  });
});
