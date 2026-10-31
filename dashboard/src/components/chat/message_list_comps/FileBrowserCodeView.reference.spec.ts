import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import FileBrowserCodeView from "./FileBrowserCodeView.vue";

describe("source line references", () => {
  it("highlights on initial mount, changes ranges and clears invalid ranges", async () => {
    const wrapper = mount(FileBrowserCodeView, {
      props: {
        highlightedHtml:
          '<code><span class="line">first</span><span class="line">second</span><span class="line">third</span></code>',
        filePath: "G:/file.py",
        comments: [],
        activeEditLine: null,
        activeEditCommentId: null,
        isDark: false,
        scrollToLine: 2,
        scrollToEndLine: 3,
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(
      wrapper.findAll(".line-referenced").map((line) => line.text()),
    ).toEqual(["second", "third"]);
    await wrapper.setProps({ scrollToLine: 1, scrollToEndLine: 1 });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.get(".line-referenced").text()).toBe("first");
    await wrapper.setProps({ scrollToLine: 4, scrollToEndLine: 6 });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.findAll(".line-referenced")).toHaveLength(0);
    wrapper.unmount();
  });
});
