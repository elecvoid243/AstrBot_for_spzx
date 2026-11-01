// Author: elecvoid243 @ 2026-10-22
// ThreadPanel: drag-to-resize, side-chat mode and close semantics.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, nextTick } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const { getThreadMock, fetchMock, stopMock, uploadMock } = vi.hoisted(() => ({
  getThreadMock: vi.fn(),
  fetchMock: vi.fn(),
  stopMock: vi.fn(),
  uploadMock: vi.fn(),
}));

vi.mock("@/api/v1", () => ({
  fileApi: { upload: uploadMock },
  chatApi: {
    getThread: getThreadMock,
    resumeRunStreamUrl: (runId: string) => `/runs/${runId}/stream`,
    stopThread: stopMock,
    sendThreadMessageUrl: (threadId: string) => `/threads/${threadId}/messages`,
  },
}));
vi.mock("@/api/http", () => ({ fetchWithAuth: fetchMock }));

import { accountSessionRevision } from "@/utils/accountSession";
import ThreadPanel from "./ThreadPanel.vue";
import type { ChatThread } from "@/composables/useMessages";

const THREAD: ChatThread = {
  thread_id: "thread-1",
  parent_session_id: "session-1",
  parent_message_id: 0,
  base_checkpoint_id: "",
  selected_text: "被选中的那段文字",
  created_at: "2026-10-22T00:00:00+00:00",
  updated_at: "2026-10-22T00:00:00+00:00",
};

const VBtnStub = defineComponent({
  name: "VBtn",
  props: { icon: { type: String, default: "" } },
  emits: ["click"],
  setup(props, { emit }) {
    return () =>
      h("button", {
        class: "v-btn-stub",
        "data-icon": props.icon,
        onClick: () => emit("click"),
      });
  },
});

function mountPanel(
  props: Partial<{
    modelValue: boolean;
    thread: ChatThread | null;
    isDark: boolean;
    sideChat: boolean;
    title: string;
  }> = {},
) {
  return mount(ThreadPanel, {
    props: {
      modelValue: true,
      thread: THREAD,
      isDark: false,
      ...props,
    },
    global: {
      stubs: {
        ChatMessageList: true,
        ChatInput: defineComponent({
          name: "ChatInput",
          props: { disabled: Boolean, stagedFiles: Array },
          emits: ["send-command", "stop", "send", "file-select"],
          setup(props, { expose }) {
            expose({
              prepareConfig: async () => "profile-a",
              getCurrentSelection: () => ({}),
              getThinkingEffort: () => "",
            });
            return () =>
              h("chat-input-stub", { disabled: String(props.disabled) });
          },
        }),
        ProviderModelMenu: true,
        VBtn: VBtnStub,
      },
    },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  getThreadMock.mockReset();
  getThreadMock.mockResolvedValue({ data: { data: { history: [] } } });
  fetchMock.mockReset();
  stopMock.mockReset();
  uploadMock.mockReset();
  stopMock.mockResolvedValue({});
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ThreadPanel", () => {
  it("preserves persisted file sources and imported restrictions when reopened", async () => {
    const files = [
      {
        path: "/owned/result.txt",
        runtime: "local",
        kind: "edit",
        backup_id: "backup",
        sha256: "checksum",
        diff_available: true,
      },
    ];
    getThreadMock.mockResolvedValue({
      data: {
        data: {
          history: [
            {
              id: 81,
              content: { type: "bot", message: [], file_changes: { files } },
            },
            {
              id: 82,
              content: {
                type: "bot",
                message: [],
                file_changes: { files },
                _imported: true,
              },
            },
          ],
        },
      },
    });
    const wrapper = mountPanel();
    await flushPromises();
    for (let reopening = 0; reopening < 2; reopening += 1) {
      const list = wrapper.getComponent({ name: "ChatMessageList" });
      expect(list.props("sessionId")).toBe(THREAD.thread_id);
      const records = list.props("messages");
      expect(records[0].id).toBe(81);
      expect(records[0].content.fileChangeSummary).toEqual(files);
      expect(records[0].content.fileChangeSourceUnavailable).toBe(false);
      expect(records[1].content.fileChangeSourceUnavailable).toBe(true);
      if (reopening === 0) {
        await wrapper.setProps({ modelValue: false, thread: null });
        await wrapper.setProps({ modelValue: true, thread: THREAD });
        await flushPromises();
      }
    }
    wrapper.unmount();
  });

  it.each([false, true])(
    "keeps live file results tied to the thread and server run (sideChat=%s)",
    async (sideChat) => {
      let controller!: ReadableStreamDefaultController<Uint8Array>;
      fetchMock.mockResolvedValue({
        ok: true,
        body: new ReadableStream({
          start(value) {
            controller = value;
          },
        }),
      });
      const wrapper = mountPanel({ sideChat });
      await flushPromises();
      if (sideChat)
        wrapper
          .getComponent({ name: "ChatInput" })
          .vm.$emit("send-command", "Run the tool");
      else {
        await wrapper.get(".thread-input").setValue("Run the tool");
        await wrapper.get("form").trigger("submit");
      }
      await flushPromises();
      const files = [
        {
          path: "/owned/live.txt",
          kind: "edit",
          sha256: "checksum",
          runtime: "local",
          backup_id: "backup",
          diff_available: true,
        },
      ];
      const frames = [
        {
          type: "session_id",
          session_id: THREAD.thread_id,
          run_id: "server-thread-run",
        },
        { type: "file_changes", data: { files } },
      ];
      controller.enqueue(
        new TextEncoder().encode(
          frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join(""),
        ),
      );
      await flushPromises();
      const list = wrapper.getComponent({ name: "ChatMessageList" });
      expect(list.props("sessionId")).toBe(THREAD.thread_id);
      const bot = list.props("messages").at(-1);
      expect(bot.content.fileChangeSummary).toEqual(files);
      expect(bot.content.fileChangeRunId).toBe("server-thread-run");
      controller.enqueue(
        new TextEncoder().encode(
          `data: ${JSON.stringify({
            type: "run_snapshot",
            data: {
              run_id: "server-thread-run",
              history_record_id: 85,
              content: {
                type: "bot",
                file_changes: { files },
                _imported: true,
              },
            },
          })}\n\n`,
        ),
      );
      await flushPromises();
      expect(bot.id).toBe(85);
      expect(bot.content.fileChangeSummary).toEqual(files);
      expect(bot.content.fileChangeSourceUnavailable).toBe(true);
      expect(bot.content.fileChangeRunId).toBe("server-thread-run");
      controller.close();
      await flushPromises();
      wrapper.unmount();
    },
  );

  it("retains live A records across hiding and reopening, and isolates B's stream and stop action", async () => {
    const streams = new Map<
      string,
      ReadableStreamDefaultController<Uint8Array>
    >();
    fetchMock.mockImplementation(async (url: string) => ({
      ok: true,
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          streams.set(url, controller);
        },
      }),
    }));
    const wrapper = mountPanel({ sideChat: true });
    await flushPromises();
    await wrapper
      .getComponent({ name: "ChatInput" })
      .vm.$emit("send-command", "A prompt");
    await flushPromises();
    const a = streams.get("/threads/thread-1/messages")!;
    const encode = new TextEncoder();
    a.enqueue(
      encode.encode(
        'data: {"type":"plain","data":"A live reference","streaming":true}\n\n',
      ),
    );
    await flushPromises();
    const liveMessages = wrapper
      .getComponent({ name: "ChatMessageList" })
      .props("messages");
    await wrapper.setProps({
      thread: null,
      modelValue: false,
    });
    await wrapper.setProps({ thread: THREAD, modelValue: true });
    await flushPromises();
    expect(
      wrapper.getComponent({ name: "ChatMessageList" }).props("messages"),
    ).toBe(liveMessages);
    expect(JSON.stringify(liveMessages)).toContain("A live reference");
    expect(getThreadMock).toHaveBeenCalledTimes(1);
    const bThread = { ...THREAD, thread_id: "thread-b" };
    await wrapper.setProps({ thread: bThread });
    await flushPromises();
    expect(
      wrapper.getComponent({ name: "ChatInput" }).attributes("disabled"),
    ).toBe("false");
    await wrapper
      .getComponent({ name: "ChatInput" })
      .vm.$emit("send-command", "B prompt");
    await flushPromises();
    a.enqueue(
      encode.encode(
        'data: {"type":"plain","data":" A continued","streaming":true}\n\n',
      ),
    );
    a.close();
    const b = streams.get("/threads/thread-b/messages")!;
    b.enqueue(
      encode.encode(
        'data: {"type":"plain","data":"B live reply","streaming":true}\n\n',
      ),
    );
    await flushPromises();
    const visibleB = JSON.stringify(
      wrapper.getComponent({ name: "ChatMessageList" }).props("messages"),
    );
    expect(visibleB).toContain("B live reply");
    expect(visibleB).not.toContain("A continued");
    await wrapper.getComponent({ name: "ChatInput" }).vm.$emit("stop");
    await flushPromises();
    expect(stopMock).toHaveBeenCalledWith("thread-b");
    b.close();
    getThreadMock.mockResolvedValue({
      data: { data: { history: liveMessages } },
    });
    await wrapper.setProps({ thread: THREAD });
    await flushPromises();
    const visibleA = JSON.stringify(
      wrapper.getComponent({ name: "ChatMessageList" }).props("messages"),
    );
    expect(visibleA).toContain("A live reference A continued");
    expect(visibleA).not.toContain("B live reply");
    wrapper.unmount();
  });

  it("ignores stale history loads and a history response superseded by a local stream", async () => {
    let oldA!: (value: unknown) => void;
    getThreadMock.mockReturnValueOnce(
      new Promise((resolve) => {
        oldA = resolve;
      }),
    );
    const wrapper = mountPanel({ sideChat: true });
    await wrapper.setProps({ thread: { ...THREAD, thread_id: "thread-b" } });
    await flushPromises();
    oldA({
      data: {
        data: {
          history: [
            {
              content: {
                type: "bot",
                message: [{ type: "plain", text: "STALE A" }],
              },
            },
          ],
        },
      },
    });
    await flushPromises();
    expect(
      JSON.stringify(
        wrapper.getComponent({ name: "ChatMessageList" }).props("messages"),
      ),
    ).not.toContain("STALE A");
    let reloadA!: (value: unknown) => void;
    getThreadMock.mockReturnValueOnce(
      new Promise((resolve) => {
        reloadA = resolve;
      }),
    );
    await wrapper.setProps({ thread: THREAD });
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    fetchMock.mockResolvedValue({
      ok: true,
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          stream = controller;
        },
      }),
    });
    await wrapper
      .getComponent({ name: "ChatInput" })
      .vm.$emit("send-command", "new A turn");
    await flushPromises();
    reloadA({ data: { data: { history: [] } } });
    stream.enqueue(
      new TextEncoder().encode('data: {"type":"plain","data":"CURRENT A"}\n\n'),
    );
    await flushPromises();
    const visible = JSON.stringify(
      wrapper.getComponent({ name: "ChatMessageList" }).props("messages"),
    );
    expect(visible).toContain("new A turn");
    expect(visible).toContain("CURRENT A");
    stream.close();
    await flushPromises();
    wrapper.unmount();
  });

  it("renders the side-chat header, full composer and delete action", async () => {
    const wrapper = mountPanel({ sideChat: true });
    await flushPromises();

    expect(wrapper.get(".thread-panel-title").text()).toBe("侧边聊天");
    expect(wrapper.find(".thread-selected-text").exists()).toBe(false);
    expect(wrapper.find('[data-icon="mdi-delete-outline"]').exists()).toBe(
      true,
    );
    expect(wrapper.find("chat-input-stub").exists()).toBe(true);
  });

  it("prefers the list title supplied by the parent", async () => {
    const wrapper = mountPanel({ sideChat: true, title: "这段代码为什么慢" });
    await flushPromises();

    expect(wrapper.get(".thread-panel-title").text()).toBe("这段代码为什么慢");
  });

  it("keeps the quoted selection and delete button for branch threads", async () => {
    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.get(".thread-panel-title").text()).toBe("分支");
    expect(wrapper.get(".thread-selected-text").text()).toBe(
      "被选中的那段文字",
    );
    expect(wrapper.find('[data-icon="mdi-delete-outline"]').exists()).toBe(
      true,
    );
    expect(wrapper.find("chat-input-stub").exists()).toBe(false);
  });

  it("resizes by dragging the left handle and clamps to [320, 1800]", async () => {
    const wrapper = mountPanel();
    await flushPromises();
    const panel = wrapper.get(".thread-panel");

    await wrapper.get(".thread-panel-resizer").trigger("mousedown");
    // happy-dom reports a zero rect, so the sidebar's right edge is 0.
    document.dispatchEvent(new MouseEvent("mousemove", { clientX: -600 }));
    await nextTick();
    expect(panel.attributes("style")).toContain("width: 600px");

    document.dispatchEvent(new MouseEvent("mousemove", { clientX: -9999 }));
    await nextTick();
    expect(panel.attributes("style")).toContain("width: 1800px");

    document.dispatchEvent(new MouseEvent("mousemove", { clientX: 900 }));
    await nextTick();
    expect(panel.attributes("style")).toContain("width: 320px");

    document.dispatchEvent(new MouseEvent("mouseup"));
  });

  it("emits update:modelValue=false from the close button", async () => {
    const wrapper = mountPanel({ sideChat: true });
    await flushPromises();

    await wrapper.get('[data-icon="mdi-close"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")).toEqual([[false]]);
  });

  it("emits delete for branch threads", async () => {
    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.get('[data-icon="mdi-delete-outline"]').trigger("click");
    expect(wrapper.emitted("delete")?.[0]).toEqual([THREAD]);
  });
});

describe("ThreadPanel account isolation and recovery", () => {
  it("discards history that arrives after the account changed", async () => {
    let resolve!: (value: unknown) => void;
    getThreadMock.mockReturnValueOnce(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const wrapper = mountPanel();
    accountSessionRevision.value += 1;
    resolve({
      data: {
        data: {
          history: [
            {
              id: 1,
              content: {
                type: "bot",
                message: [{ type: "plain", text: "previous account" }],
              },
            },
          ],
        },
      },
    });
    await flushPromises();
    expect(
      wrapper.getComponent({ name: "ChatMessageList" }).props("messages"),
    ).toEqual([]);
    wrapper.unmount();
  });

  it("resumes an owned active run and clears its stream and cache on account switch", async () => {
    getThreadMock.mockResolvedValueOnce({
      data: {
        data: {
          history: [],
          active_runs: [
            {
              run_id: "private-run",
              status: "running",
              content: { type: "bot", message: [] },
            },
          ],
        },
      },
    });
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    let signal!: AbortSignal;
    fetchMock.mockImplementationOnce(
      async (_url: string, options: RequestInit) => {
        signal = options.signal!;
        return {
          ok: true,
          body: new ReadableStream<Uint8Array>({
            start(value) {
              controller = value;
            },
          }),
        };
      },
    );
    const wrapper = mountPanel({ sideChat: true });
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledWith(
      "/runs/private-run/stream",
      expect.anything(),
    );
    const encode = new TextEncoder();
    controller.enqueue(
      encode.encode(
        'data: {"type":"run_snapshot","data":{"history_record_id":9,"content":{"type":"bot","message":[{"type":"plain","text":"recovered"}]}}}\n\ndata: {"type":"plain","data":" delta","streaming":true}\n\n',
      ),
    );
    await flushPromises();
    expect(
      JSON.stringify(
        wrapper.getComponent({ name: "ChatMessageList" }).props("messages"),
      ),
    ).toContain("recovered delta");
    accountSessionRevision.value += 1;
    expect(signal.aborted).toBe(true);
    await nextTick();
    expect(
      wrapper.getComponent({ name: "ChatMessageList" }).props("messages"),
    ).toEqual([]);
    controller.close();
    wrapper.unmount();
  });
});

describe("ThreadPanel staged upload payload", () => {
  it("keeps the local preview while serializing the public attachment fields", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "file-turn" });
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:local-side-preview");
    uploadMock.mockResolvedValue({
      data: {
        status: "ok",
        data: {
          attachment_id: "owned-side-file",
          filename: "side.txt",
          type: "file",
        },
      },
    });
    fetchMock.mockResolvedValue({
      ok: true,
      body: new ReadableStream({
        start(controller) {
          controller.close();
        },
      }),
    });
    const wrapper = mountPanel({ sideChat: true });
    await flushPromises();
    const input = wrapper.getComponent({ name: "ChatInput" });
    input.vm.$emit("file-select", [
      new File(["owned side bytes"], "side.txt", { type: "text/plain" }),
    ]);
    await flushPromises();
    expect(uploadMock).toHaveBeenCalledTimes(1);
    expect(input.props("stagedFiles")).toHaveLength(1);
    input.vm.$emit("send", []);
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const payload = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(payload.message).toEqual([
      { type: "file", attachment_id: "owned-side-file", filename: "side.txt" },
    ]);
    const local = wrapper
      .getComponent({ name: "ChatMessageList" })
      .props("messages");
    expect(local[0].content.message[0].embedded_url).toBe(
      "blob:local-side-preview",
    );
    expect(input.props("stagedFiles")).toEqual([]);
    wrapper.unmount();
  });
});
