import { mount, flushPromises } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import RemoteMessageResource from "./RemoteMessageResource.vue";
import { remoteConnectionsApi } from "@/api/remoteConnections";
import { resetAccountSession } from "@/utils/accountSession";
import { createPinia } from "pinia";
import ChatMessageList from "./ChatMessageList.vue";
vi.mock("@/api/remoteConnections", () => ({
  remoteConnectionsApi: {
    attachment: vi.fn(),
    answer: vi.fn(),
    cancelChoice: vi.fn(),
  },
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
it("keeps active image content inert without download controls", async () => {
  vi.mocked(remoteConnectionsApi.attachment).mockResolvedValue({
    data: new Blob(["<svg onload='alert(1)'/>"], { type: "image/svg+xml" }),
  } as any);
  const wrapper = make({ type: "image", attachment_id: "remote-svg" });
  await flushPromises();
  expect(wrapper.find("img").exists()).toBe(false);
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  expect(wrapper.find("button").exists()).toBe(false);
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
  expect(wrapper.find(".sent-file-card button").exists()).toBe(false);
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
  await wrapper.find(".choice-option-button").trigger("click");
  expect(remoteConnectionsApi.answer).toHaveBeenCalledWith(scope, "question", {
    choice_id: "A",
    free_text: "",
  });
  expect(wrapper.findAll(".choice-option-button")).toHaveLength(2);
  finish();
  await flushPromises();
  expect(wrapper.find(".interactive-choice-box.is-submitted").exists()).toBe(
    true,
  );
  expect(remoteConnectionsApi.attachment).not.toHaveBeenCalled();
  wrapper.unmount();
});
it("keeps rejected answers reviewable and scopes persisted terminal answers", async () => {
  vi.mocked(remoteConnectionsApi.answer).mockRejectedValue({
    response: { data: { data: { code: "remote_rejected" } } },
  });
  const wrapper = make(choice);
  await wrapper.find(".choice-option-button").trigger("click");
  await flushPromises();
  expect(wrapper.find('[role="alert"]').exists()).toBe(true);
  expect(wrapper.findAll(".choice-option-button")).toHaveLength(2);
  await wrapper.setProps({
    part: { ...choice, answer: { choice_id: "B", free_text: "Later" } },
  });
  expect(wrapper.text()).toContain("Wait");
  expect(wrapper.find(".interactive-choice-box.is-submitted").exists()).toBe(
    true,
  );
  wrapper.unmount();
});

it("cancels a remote choice only after the scoped server accepts it", async () => {
  vi.mocked(remoteConnectionsApi.cancelChoice).mockResolvedValue({} as any);
  const wrapper = make(choice);
  await wrapper.find(".choice-cancel-button").trigger("click");
  await flushPromises();
  expect(remoteConnectionsApi.cancelChoice).toHaveBeenCalledWith(
    scope,
    "question",
  );
  expect(wrapper.find(".interactive-choice-box.is-cancelled").exists()).toBe(
    true,
  );
  wrapper.unmount();
});

it("preserves a rejected cancellation and clears its error after a successful retry", async () => {
  vi.mocked(remoteConnectionsApi.cancelChoice)
    .mockRejectedValueOnce({
      response: { data: { data: { code: "remote_rejected" } } },
    })
    .mockResolvedValueOnce({} as any);
  const wrapper = make(choice);
  await wrapper.find(".choice-cancel-button").trigger("click");
  await flushPromises();
  expect(wrapper.find(".interactive-choice-box.is-pending").exists()).toBe(
    true,
  );
  expect(wrapper.find('[role="alert"]').exists()).toBe(true);
  await wrapper.find(".choice-cancel-button").trigger("click");
  await flushPromises();
  expect(wrapper.find(".interactive-choice-box.is-cancelled").exists()).toBe(
    true,
  );
  expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  wrapper.unmount();
});

it.each([
  ["record", "audio/wav", "audio"],
  ["video", "video/mp4", "video"],
])(
  "renders remote %s with native playback controls",
  async (type, mime, tag) => {
    vi.mocked(remoteConnectionsApi.attachment).mockResolvedValue({
      data: new Blob(["media"], { type: mime }),
    } as any);
    const wrapper = make({ type, attachment_id: "remote-media" });
    await flushPromises();
    expect(wrapper.find(tag).attributes("src")).toBe("blob:safe");
    expect(wrapper.find(tag).attributes()).toHaveProperty("controls");
    wrapper.unmount();
  },
);
