import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { MarkdownRender } from "markstream-vue";
import { registerChatMarkdownComponents } from "@/components/chat/chatMarkdownComponents";
import { OPEN_FILE_REFERENCE, parseFileReference } from "./fileReference";

describe("host file references", () => {
  it.each([
    [
      "G:/中文%20目录/a%23b%25.py#L2-L4",
      { path: "G:/中文 目录/a#b%.py", startLine: 2, endLine: 4 },
    ],
    ["/tmp/a.py#L1", { path: "/tmp/a.py", startLine: 1, endLine: 1 }],
    ["C:/a.py", { path: "C:/a.py", startLine: null, endLine: null }],
  ])("parses %s", (href, expected) =>
    expect(parseFileReference(href)).toEqual(expected),
  );

  it.each([
    "relative/a.py",
    "https://example.com/a#L2",
    "file:///tmp/a",
    "//example.com/a",
    "C:a.py",
    "/a#L0",
    "/a#L4-L2",
    "/a#L2-L9007199254740992",
    "/a%ZZ",
    "/a#other",
  ])("rejects %s", (href) => {
    expect(parseFileReference(href)).toBeNull();
  });

  it("activates completed explicit links through the real chat renderer only on click", async () => {
    registerChatMarkdownComponents();
    const open = vi.fn();
    const wrapper = mount(MarkdownRender, {
      props: {
        customId: "chat-message",
        final: true,
        content:
          "[file](G:/中文%20目录/a.py#L2-L3)\n\nG:/plain.py\n\n`[code](G:/inline.py)`\n\n```text\n[code](G:/fenced.py)\n```\n\n[web](https://example.com) [relative](src/a.py)",
      },
      global: { provide: { [OPEN_FILE_REFERENCE as symbol]: open } },
    });
    await vi.waitFor(() =>
      expect(wrapper.findAll(".file-reference-link")).toHaveLength(1),
    );
    expect(open).not.toHaveBeenCalled();
    await wrapper.get(".file-reference-link").trigger("click");
    expect(open).toHaveBeenCalledWith({
      path: "G:/中文 目录/a.py",
      startLine: 2,
      endLine: 3,
    });
    await wrapper.setProps({
      final: false,
      content: "[file](G:/unfinished.py#L2",
    });
    expect(wrapper.findAll(".file-reference-link")).toHaveLength(0);
    wrapper.unmount();
  });
});
