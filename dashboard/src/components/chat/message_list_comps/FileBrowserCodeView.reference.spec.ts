import { describe, expect, it, vi } from "vitest";
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
    const viewport = wrapper.get<HTMLElement>(".code-view").element;
    viewport.scrollLeft = 120;
    viewport.scrollTop = 75;
    vi.spyOn(viewport, "clientHeight", "get").mockReturnValue(200);
    vi.spyOn(viewport, "getBoundingClientRect").mockReturnValue({ top: 40 } as DOMRect);
    vi.spyOn(wrapper.get(".line").element, "getBoundingClientRect").mockReturnValue({
      top: 256,
      height: 18,
    } as DOMRect);
    await wrapper.setProps({ scrollToLine: 1, scrollToEndLine: 1 });
    await vi.waitFor(() => {
      expect(viewport.scrollLeft).toBe(0);
      expect(viewport.scrollTop).toBe(200);
    });
    expect(wrapper.get(".line-referenced").text()).toBe("first");
    await wrapper.setProps({ scrollToLine: 4, scrollToEndLine: 6 });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.findAll(".line-referenced")).toHaveLength(0);
    wrapper.unmount();
  });

  it("opens a source comment from navigation and reopens it from the gutter", async () => {
    const comment = {
      title: "Finding",
      body: "Explanation",
      file: "file.py",
      path: "G:/file.py",
      start_line: 2,
      end_line: 2,
      priority: 2,
      revision: "a".repeat(64),
      directive: "review",
    };
    const wrapper = mount(FileBrowserCodeView, {
      props: {
        highlightedHtml: '<code><span class="line">first</span><span class="line">second</span></code>',
        filePath: comment.path,
        comments: [],
        activeEditLine: null,
        activeEditCommentId: null,
        isDark: false,
        assistantComments: [comment],
        focusedAssistantComment: comment,
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.get(".line-referenced").text()).toBe("second");
    expect(wrapper.get("aside").text()).toContain("Explanation");
    await wrapper.get("aside button").trigger("click");
    expect(wrapper.find("aside").exists()).toBe(false);
    await wrapper.get(".assistant-comment-gutter button").trigger("click");
    expect(wrapper.get("aside").text()).toContain("[P2] Finding");
    wrapper.unmount();
  });
});
