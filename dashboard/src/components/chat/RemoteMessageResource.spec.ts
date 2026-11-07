import { mount, flushPromises } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import RemoteMessageResource from "./RemoteMessageResource.vue";
import { remoteConnectionsApi } from "@/api/remoteConnections";
import { resetAccountSession } from "@/utils/accountSession";
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
