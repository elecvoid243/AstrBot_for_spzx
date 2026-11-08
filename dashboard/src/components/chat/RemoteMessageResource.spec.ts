import { mount, flushPromises } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import RemoteMessageResource from "./RemoteMessageResource.vue";
import { remoteConnectionsApi } from "@/api/remoteConnections";
import { resetAccountSession } from "@/utils/accountSession";
import { createPinia } from "pinia";
import ChatMessageList from "./ChatMessageList.vue";
vi.mock("@/api/remoteConnections", () => ({
  remoteConnectionsApi: { attachment: vi.fn(), answer: vi.fn() },
}));
const scope = { connectionId: "a", revision: "first", sessionId: "same" };
const make = (part: any) =>
  mount(RemoteMessageResource, {
    props: { scope, part, enabled: true },
    global: {
      stubs: {
        VBtn: { template: "<button><slot /></button>" },
        VTextarea: true,
      },
    },
  });
beforeEach(() => {
  vi.clearAllMocks();
  URL.createObjectURL = vi.fn(() => "blob:safe");
  URL.revokeObjectURL = vi.fn();
});
it("loads images only through the scoped attachment proxy and discards late replies", async () => {
  let finish!: (value: any) => void;
  vi.mocked(remoteConnectionsApi.attachment)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }) as any,
    )
    .mockResolvedValueOnce({
      data: new Blob(["B"], { type: "image/png" }),
    } as any);
  const wrapper = make({
    type: "image",
    attachment_id: "opaque",
    url: "https://third-party/image.png",
    path: "C:/private.png",
  });
  expect(remoteConnectionsApi.attachment).toHaveBeenCalledWith(
    scope,
    "opaque",
    expect.any(AbortSignal),
  );
  await wrapper.setProps({ scope: { ...scope, connectionId: "b" } });
  await flushPromises();
  finish({ data: new Blob(["A"], { type: "image/png" }) });
  await flushPromises();
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  expect(wrapper.find("img").attributes("src")).toBe("blob:safe");
  expect(wrapper.html()).not.toContain("third-party");
  await wrapper.setProps({ enabled: false });
  expect(wrapper.find("img").exists()).toBe(false);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:safe");
  wrapper.unmount();
});
it("revokes previews and invalidates in-flight actions on account change", async () => {
  vi.mocked(remoteConnectionsApi.attachment).mockResolvedValue({
    data: new Blob(["x"], { type: "image/png" }),
  } as any);
  const wrapper = make({ type: "image", attachment_id: "opaque" });
  await flushPromises();
  resetAccountSession();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:safe");
  wrapper.unmount();
  await flushPromises();
});
it.each(["image/x-icon", "image/vnd.microsoft.icon"])(
  "previews ICO attachments served as %s through the scoped proxy",
  async (mime) => {
    vi.mocked(remoteConnectionsApi.attachment).mockResolvedValue({
      data: new Blob([new Uint8Array([0, 0, 1, 0])], { type: mime }),
    } as any);
    const wrapper = make({
      type: "image",
      filename: "reaction.ico",
      attachment_id: "remote-icon",
    });
    await flushPromises();
    expect(wrapper.find("img").attributes("src")).toBe("blob:safe");
    expect(wrapper.find("img").attributes("alt")).toBe("reaction.ico");
    expect(remoteConnectionsApi.attachment).toHaveBeenCalledWith(
      scope,
      "remote-icon",
      expect.any(AbortSignal),
    );
    wrapper.unmount();
  },
);
it("keeps active image content download-only", async () => {
  vi.mocked(remoteConnectionsApi.attachment).mockResolvedValue({
    data: new Blob(["<svg onload='alert(1)'/>"], { type: "image/svg+xml" }),
  } as any);
  const wrapper = make({ type: "image", attachment_id: "remote-svg" });
  await flushPromises();
  expect(wrapper.find("img").exists()).toBe(false);
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  expect(wrapper.find("button").exists()).toBe(true);
  wrapper.unmount();
});
it("uses the local attachment cards above the remote user text bubble", async () => {
  vi.mocked(remoteConnectionsApi.attachment).mockResolvedValue({
    data: new Blob([new Uint8Array([0, 0, 1, 0])], { type: "image/x-icon" }),
  } as any);
  const wrapper = mount(ChatMessageList, {
    props: {
      remoteHistory: true,
      remoteReady: true,
      remoteScope: scope,
      messages: [
        {
          id: 1,
          content: {
            type: "user",
            message: [
              {
                type: "image",
                attachment_id: "remote-icon",
                filename: "reaction.ico",
              },
              {
                type: "file",
                attachment_id: "remote-file",
                filename: "123.txt",
              },
              { type: "plain", text: "What is in these files?" },
            ],
          },
        },
      ],
    },
    global: {
      plugins: [createPinia()],
      stubs: { RefsSidebar: true, GenerationIndicator: true },
    },
  });
  await flushPromises();
  expect(
    wrapper.find(".sent-attachments .sent-image-card img").attributes("src"),
  ).toBe("blob:safe");
  expect(wrapper.find(".sent-file-card .sent-attachment-name").text()).toBe(
    "123.txt",
  );
  expect(wrapper.find(".sent-file-card .sent-attachment-ext").text()).toBe(
    "TXT",
  );
  expect(wrapper.find(".message-bubble.user").text()).toBe(
    "What is in these files?",
  );
  expect(
    wrapper.find(".message-bubble.user .remote-attachment-resource").exists(),
  ).toBe(false);
  await wrapper.find(".sent-image-card").trigger("click");
  expect(wrapper.find(".image-preview-overlay").attributes("modelvalue")).toBe(
    "true",
  );
  await wrapper.setProps({ remoteReady: false });
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:safe");
  expect(wrapper.find(".image-preview-overlay").attributes("modelvalue")).toBe(
    "false",
  );
  wrapper.unmount();
});
const choice = {
  type: "interactive_choice",
  request_id: "question",
  prompt: "Choose",
  options: [
    { id: "A", label: "Approve" },
    { id: "B", label: "Wait" },
  ],
};
it("submits a human answer through the binding and waits for success before displaying it", async () => {
  let finish!: () => void;
  vi.mocked(remoteConnectionsApi.answer).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = () => resolve({} as any);
      }) as any,
  );
  const wrapper = make(choice);
  await wrapper.find("button").trigger("click");
  expect(remoteConnectionsApi.answer).toHaveBeenCalledWith(scope, "question", {
    choice_id: "A",
    free_text: "",
  });
  expect(wrapper.findAll("button")).toHaveLength(3);
  finish();
  await flushPromises();
  expect(wrapper.findAll("button")).toHaveLength(0);
  expect(remoteConnectionsApi.attachment).not.toHaveBeenCalled();
  wrapper.unmount();
});
it("keeps rejected answers reviewable and scopes persisted terminal answers", async () => {
  vi.mocked(remoteConnectionsApi.answer).mockRejectedValue({
    response: { data: { data: { code: "remote_rejected" } } },
  });
  const wrapper = make(choice);
  await wrapper.find("button").trigger("click");
  await flushPromises();
  expect(wrapper.find('[role="alert"]').exists()).toBe(true);
  expect(wrapper.findAll("button")).toHaveLength(3);
  await wrapper.setProps({
    part: { ...choice, answer: { choice_id: "B", free_text: "Later" } },
  });
  expect(wrapper.text()).toContain("Later");
  expect(wrapper.findAll("button")).toHaveLength(0);
  wrapper.unmount();
});
