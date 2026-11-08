import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { beforeEach, expect, it, vi } from "vitest";
import ChatMessageList from "./ChatMessageList.vue";
import { remoteConnectionsApi } from "@/api/remoteConnections";
import { OPEN_FILE_REFERENCE } from "@/composables/fileReference";

vi.mock("@/api/remoteConnections", () => ({
  remoteConnectionsApi: {
    attachment: vi.fn(),
    answer: vi.fn(),
    cancelChoice: vi.fn(),
  },
}));
const scope = {
  connectionId: "remote",
  sessionId: "remote-session",
  revision: "key-v1",
};
beforeEach(() => {
  vi.clearAllMocks();
  URL.createObjectURL = vi.fn(() => "blob:remote-image");
  URL.revokeObjectURL = vi.fn();
});
function render(text: string, type = "bot", open = vi.fn()) {
  return mount(ChatMessageList, {
    props: {
      remoteHistory: true,
      remoteReady: true,
      remoteScope: scope,
      messages: [
        { id: 1, content: { type, message: [{ type: "plain", text }] } },
      ],
    },
    global: {
      plugins: [createPinia()],
      provide: { [OPEN_FILE_REFERENCE as symbol]: open },
      stubs: { RefsSidebar: true, GenerationIndicator: true },
    },
  });
}
it("renders remote HTML through the same sandboxed GenUI view", async () => {
  const wrapper = render(
    '<html-genui title="Remote preview"><div id="remote-html">Rich remote content</div></html-genui>',
  );
  await flushPromises();
  expect(wrapper.findComponent({ name: "HtmlGenUiNode" }).exists()).toBe(true);
  const frame = wrapper.find("iframe");
  expect(frame.attributes("srcdoc")).toContain("Rich remote content");
  expect(frame.attributes("sandbox")).not.toContain("allow-same-origin");
  wrapper.unmount();
});
it("replays remote skill requests, file references and review comments as native cards", async () => {
  const text = [
    "Review this change",
    "",
    "[File review comments]",
    "`src/app.py` line 1:",
    "````",
    "  >    1 │ print(1)",
    "         │ Comment: Check this output",
    "````",
    "",
    "[Referenced files]",
    "Local path",
    "- `src/app.py`",
    "",
    "[Requested skills]",
    "The user explicitly queued the following skill(s) for this request.",
    "- `pdf`",
  ].join("\n");
  const wrapper = render(text, "user");
  await flushPromises();
  expect(wrapper.find(".frc").text()).toContain("Check this output");
  expect(wrapper.find(".frf").text()).toContain("src/app.py");
  expect(wrapper.find(".srq").text()).toContain("pdf");
  expect(wrapper.find(".user-plain-part > .plain-content").text()).toBe(
    "Review this change",
  );
  wrapper.unmount();
});
it("displays file-link chips without navigating the local workspace", async () => {
  const open = vi.fn();
  const wrapper = render(
    "[app.py](G:/private/app.py#L4) and [website](https://example.com/)",
    "bot",
    open,
  );
  await flushPromises();
  await wrapper.find(".file-reference-link").trigger("click");
  expect(open).not.toHaveBeenCalled();
  expect(wrapper.find('a[href="https://example.com/"]').exists()).toBe(true);
  wrapper.unmount();
});
it("resolves Markdown attachment images through their remote parent session", async () => {
  vi.mocked(remoteConnectionsApi.attachment).mockResolvedValue({
    data: new Blob(["raster"], { type: "image/png" }),
  } as any);
  const wrapper = render("![Remote image](/api/v1/files/opaque/content)");
  await flushPromises();
  expect(remoteConnectionsApi.attachment).toHaveBeenCalledWith(
    scope,
    "opaque",
    expect.any(AbortSignal),
  );
  expect(wrapper.find('img[src="blob:remote-image"]').exists()).toBe(true);
  wrapper.unmount();
});
it("does not resolve remote raw HTML image paths against the local instance", async () => {
  vi.mocked(remoteConnectionsApi.attachment).mockResolvedValue({
    data: new Blob(["image"], { type: "image/png" }),
  } as any);
  const wrapper = render(
    '<img src="/api/v1/files/opaque/content" alt="Remote image">',
  );
  await flushPromises();
  expect(wrapper.find('img[src^="/api/"]').exists()).toBe(false);
  await vi.waitFor(() =>
    expect(wrapper.find('img[src^="data:image/png;base64,"]').exists()).toBe(
      true,
    ),
  );
  wrapper.unmount();
});
