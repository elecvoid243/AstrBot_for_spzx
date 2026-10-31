import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
const { get, fetchStream } = vi.hoisted(() => ({
  get: vi.fn(),
  fetchStream: vi.fn(),
}));
vi.mock("@/api/v1", () => ({ pluginExtensionApi: { get } }));
vi.mock("@/api/http", () => ({ fetchWithAuth: fetchStream }));
import AgentBrowserView from "./AgentBrowserView.vue";

const current = {
  available: true,
  generation: "run-1",
  browser_session_id: "browser-1",
  tab_id: "tab-1",
};
const ok = (data: unknown) => ({ data: { status: "ok", data } });
let stream: ReadableStreamDefaultController<Uint8Array>;
beforeEach(() => {
  get.mockReset();
  fetchStream.mockReset();
  fetchStream.mockImplementation(async () => ({
    ok: true,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        stream = controller;
      },
    }),
  }));
});

describe("Agent browser snapshots", () => {
  it("renders the owned frame and passes server identities to the snapshot request", async () => {
    get.mockResolvedValueOnce(ok(current)).mockResolvedValueOnce(
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

  it("follows new targets, rejects old sequences/generations and clears old pixels", async () => {
    get.mockResolvedValueOnce(ok(current)).mockResolvedValueOnce(
      ok({
        ...current,
        sequence: 1,
        image: "first",
        mime_type: "image/jpeg",
        width: 1280,
        height: 720,
      }),
    );
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    const emit = async (data: unknown) => {
      stream.enqueue(
        new TextEncoder().encode(`data: ${JSON.stringify(data)}\n\n`),
      );
      await flushPromises();
    };
    await emit({
      ...current,
      tab_id: "tab-2",
      type: "state",
      sequence: 3,
      url: "/second",
    });
    expect(view.find("img").exists()).toBe(false);
    await emit({
      ...current,
      type: "frame",
      sequence: 2,
      image: "old",
      mime_type: "image/jpeg",
      width: 1280,
      height: 720,
    });
    await emit({
      ...current,
      generation: "old-run",
      type: "frame",
      sequence: 99,
      image: "old",
      mime_type: "image/jpeg",
      width: 1280,
      height: 720,
    });
    expect(view.find("img").exists()).toBe(false);
    await emit({
      ...current,
      tab_id: "tab-2",
      type: "frame",
      sequence: 4,
      url: "/second",
      title: "New tab",
      image: "new",
      mime_type: "image/jpeg",
      width: 1280,
      height: 720,
    });
    expect(view.get("img").attributes("src")).toContain("base64,new");
    expect(view.attributes("data-tab-id")).toBe("tab-2");
    expect(view.text()).toContain("New tab");
    const signal = fetchStream.mock.calls[0][1].signal;
    await view.setProps({ visible: false });
    expect(signal.aborted).toBe(true);
    expect(view.find("img").exists()).toBe(false);
    view.unmount();
  });

  it("marks a disconnected stream and obtains a fresh snapshot when reopened", async () => {
    const frame = {
      ...current,
      sequence: 1,
      image: "first",
      mime_type: "image/jpeg",
      width: 1280,
      height: 720,
    };
    get.mockResolvedValueOnce(ok(current)).mockResolvedValueOnce(ok(frame));
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    stream.close();
    await flushPromises();
    expect(view.find('[role="alert"]').exists()).toBe(true);
    await view.setProps({ visible: false });
    get
      .mockResolvedValueOnce(ok(current))
      .mockResolvedValueOnce(ok({ ...frame, sequence: 8 }));
    await view.setProps({ visible: true });
    await flushPromises();
    expect(view.get("img").attributes("src")).toContain("base64,first");
    expect(view.find('[role="alert"]').exists()).toBe(false);
    const signal = fetchStream.mock.calls[1][1].signal;
    view.unmount();
    expect(signal.aborted).toBe(true);
  });
});
