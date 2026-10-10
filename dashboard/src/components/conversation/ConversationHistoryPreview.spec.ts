import { mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ConversationHistoryPreview from "./ConversationHistoryPreview.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({
    tm: (key: string, args?: { count: number }) =>
      key.endsWith("messageCount")
        ? `${args?.count} messages`
        : key.split(".").at(-1),
  }),
}));

let wrapper: VueWrapper | undefined;
beforeEach(() => localStorage.clear());
afterEach(() => wrapper?.unmount());

describe("upstream conversation history reader", () => {
  it("keeps roles and tool results in order, hiding only checkpoints", () => {
    const history = [
      { role: "system", content: "System instructions" },
      { role: "_checkpoint", content: "Internal checkpoint" },
      { role: "user", content: "Read the file" },
      { role: "assistant", content: "Calling a tool" },
      { role: "tool", tool_call_id: "call-1", content: "File contents" },
      { role: "developer", content: "Developer instructions" },
    ];
    const original = JSON.stringify(history);
    wrapper = mount(ConversationHistoryPreview, {
      props: { messages: history },
    });

    expect(wrapper.findAll(".record-role").map((node) => node.text())).toEqual([
      "system",
      "user",
      "assistant",
      "tool",
      "developer",
    ]);
    expect(wrapper.find(".record-count").text()).toBe("5 messages");
    expect(wrapper.text()).not.toContain("Internal checkpoint");
    expect(wrapper.find(".record-call-id").text()).toBe("call-1");
    expect(wrapper.findAll(".history-record--user")).toHaveLength(1);
    expect(JSON.stringify(history)).toBe(original);
  });

  it("uses upstream reasoning and structured-data display without the local think conversion", () => {
    wrapper = mount(ConversationHistoryPreview, {
      props: {
        messages: [
          {
            role: "assistant",
            reasoning_content: "Reasoning field",
            content: [{ type: "think", think: "Persisted reasoning block" }],
          },
        ],
      },
    });
    const details = wrapper.findAll("details");
    expect(details.map((node) => node.find("summary").text())).toEqual([
      "structuredData",
      "reasoning",
    ]);
    expect(JSON.parse(details[0].find("pre").text())).toEqual({
      type: "think",
      think: "Persisted reasoning block",
    });
    expect(details[1].find("pre").text()).toBe("Reasoning field");
    expect(wrapper.find(".reasoning-block").exists()).toBe(false);
  });

  it("formats valid tool arguments and preserves malformed arguments and unknown content", () => {
    const history = [
      {
        role: "assistant",
        content: [{ custom: "Preserved" }],
        tool_calls: [
          {
            id: "one",
            function: { name: "read_file", arguments: '{"path":"file.txt"}' },
          },
          { id: "two", function: { name: "partial", arguments: '{"path":' } },
        ],
      },
    ];
    const original = JSON.stringify(history);
    wrapper = mount(ConversationHistoryPreview, {
      props: { messages: history },
    });
    const blocks = wrapper
      .findAll("details pre")
      .map((node) => JSON.parse(node.text()));
    expect(blocks[0]).toEqual({ custom: "Preserved" });
    expect(blocks[1].function.arguments).toEqual({ path: "file.txt" });
    expect(blocks[2].function.arguments).toBe('{"path":');
    expect(JSON.stringify(history)).toBe(original);
  });

  it("toggles Markdown and restores reading preferences when another history opens", async () => {
    const props = { messages: [{ role: "assistant", content: "**Bold**" }] };
    wrapper = mount(ConversationHistoryPreview, { props });
    expect(wrapper.find("strong").text()).toBe("Bold");
    await wrapper.find(".markdown-toggle").trigger("click");
    await wrapper.findAll(".font-controls button")[1].trigger("click");
    expect(wrapper.find(".record-text").text()).toBe("**Bold**");
    expect(wrapper.find("output").text()).toBe("14px");
    wrapper.unmount();
    wrapper = mount(ConversationHistoryPreview, { props });
    expect(wrapper.find(".markdown-toggle").attributes("aria-pressed")).toBe(
      "false",
    );
    expect(wrapper.find("output").text()).toBe("14px");
  });

  it("renders allowed image thumbnails and keeps unsafe URLs as data", () => {
    wrapper = mount(ConversationHistoryPreview, {
      props: {
        messages: [
          {
            role: "user",
            content: [
              {
                type: "image_url",
                image_url: { url: "https://example.com/image.png" },
              },
              { type: "image_url", image_url: { url: "javascript:alert(1)" } },
              {
                type: "text",
                text: "<script>alert(1)</script> [unsafe](javascript:alert(1))",
              },
            ],
          },
        ],
      },
    });
    expect(wrapper.findAll("img")).toHaveLength(1);
    expect(wrapper.find("img").attributes("loading")).toBe("lazy");
    expect(wrapper.find("details pre").text()).toContain("javascript:alert(1)");
    expect(wrapper.find("script").exists()).toBe(false);
    expect(wrapper.find('a[href^="javascript:"]').exists()).toBe(false);
    expect(wrapper.find(".image-preview-overlay").exists()).toBe(false);
  });
});
