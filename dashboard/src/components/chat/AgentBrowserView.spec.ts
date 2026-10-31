import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/api/v1", () => ({ pluginExtensionApi: { get } }));
import AgentBrowserView from "./AgentBrowserView.vue";

const current = {
  available: true,
  generation: "run-1",
  browser_session_id: "browser-1",
  tab_id: "tab-1",
};
const ok = (data: unknown) => ({ data: { status: "ok", data } });
beforeEach(() => get.mockReset());

describe("Agent browser snapshots", () => {
  it("renders the owned frame and passes server identities to the snapshot request", async () => {
    get
      .mockResolvedValueOnce(ok(current))
      .mockResolvedValueOnce(
        ok({
          ...current,
          title: "中文表单",
          url: "http://127.0.0.1/form",
          width: 1280,
          height: 720,
          image: "abc",
          mime_type: "image/jpeg",
        }),
      );
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    expect(get.mock.calls[1][1].params).toEqual({
      chat_id: "chat-1",
      generation: "run-1",
      browser_session_id: "browser-1",
    });
    expect(view.find("img").attributes("src")).toBe(
      "data:image/jpeg;base64,abc",
    );
    expect(view.text()).toContain("中文表单");
    expect(view.text()).toContain("http://127.0.0.1/form");
    view.unmount();
  });

  it("clears a prior chat image immediately and ignores its late response", async () => {
    let resolve!: (value: unknown) => void;
    get.mockReturnValueOnce(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    get.mockResolvedValueOnce(ok({ available: false }));
    await view.setProps({ chatId: "chat-2" });
    resolve(ok(current));
    await flushPromises();
    expect(view.find("img").exists()).toBe(false);
    expect(get).toHaveBeenCalledTimes(2);
    view.unmount();
  });

  it("does not fetch while hidden and surfaces busy without retaining an image", async () => {
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-2", visible: false },
    });
    expect(get).not.toHaveBeenCalled();
    get.mockRejectedValueOnce({
      response: { data: { message: "Browser is busy in another chat." } },
    });
    await view.setProps({ visible: true });
    await flushPromises();
    expect(view.find('[role="alert"]').text()).toContain("busy");
    expect(view.find("img").exists()).toBe(false);
    view.unmount();
  });
});
