import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { beforeEach, expect, it, vi } from "vitest";
import { createPinia } from "pinia";
import RemoteConversation from "./RemoteConversation.vue";
import ChatMessageList from "./ChatMessageList.vue";
import MarkdownMessagePart from "./message_list_comps/MarkdownMessagePart.vue";
import { remoteConnectionsApi } from "@/api/remoteConnections";
import { resetAccountSession } from "@/utils/accountSession";
vi.mock("@/api/remoteConnections", () => ({
  remoteConnectionsApi: {
    history: vi.fn(),
    events: vi.fn(() => new Promise(() => {})),
    send: vi.fn(),
    stop: vi.fn(),
    receipt: vi.fn(),
    upload: vi.fn(),
  },
}));
const connection = (id: string) => ({
  connection_id: id,
  revision: "initial",
  name: "Same name",
  base_url: "http://lan",
});
const session = {
  session_id: "same",
  display_name: "Same session",
  project_id: "same",
  project_name: "Same project",
  project_emoji: "",
  archived: false,
};
const row = (id: number, text: string) => ({
  id,
  content: { type: "bot", message: [{ type: "text", text }] },
});
const page = (text: string, hasMore = false) => ({
  data: {
    data: { history: [row(2, text)], has_more: hasMore, next_before_id: 2 },
  },
});
const settle = async () => {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await nextTick();
};
const make = () =>
  mount(RemoteConversation, {
    props: { connection: connection("a"), session, isDark: false },
    global: {
      plugins: [createPinia()],
      stubs: {
        ChatMessageList: {
          props: ["messages", "historyHasMore", "isStreaming"],
          template: "<div>{{ JSON.stringify(messages) }}</div>",
        },
      },
    },
  });
beforeEach(() => {
  vi.clearAllMocks();
});
it("ignores late connection history with the same session ID", async () => {
  let finish!: (value: any) => void;
  vi.mocked(remoteConnectionsApi.history)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }) as any,
    )
    .mockResolvedValueOnce(page("machine B") as any);
  const wrapper = make();
  await wrapper.setProps({ connection: connection("b") });
  await settle();
  finish(page("machine A"));
  await settle();
  expect(wrapper.text()).toContain("machine B");
  expect(wrapper.text()).not.toContain("machine A");
  wrapper.unmount();
});
it("pages full history and clears all pages on admission failure", async () => {
  vi.mocked(remoteConnectionsApi.history)
    .mockResolvedValueOnce(page("latest", true) as any)
    .mockResolvedValueOnce({
      data: {
        data: {
          history: [row(1, "older")],
          has_more: false,
          next_before_id: 1,
        },
      },
    } as any)
    .mockRejectedValueOnce({
      response: { data: { data: { code: "remote_unauthorized" } } },
    });
  const wrapper = make();
  await settle();
  wrapper.findComponent(ChatMessageList).vm.$emit("loadOlder");
  await settle();
  expect(wrapper.text()).toContain("older");
  expect(wrapper.text()).toContain("latest");
  expect(remoteConnectionsApi.history).toHaveBeenNthCalledWith(
    2,
    "a",
    "same",
    2,
  );
  await wrapper.find("header v-btn").trigger("click");
  await settle();
  expect(wrapper.text()).not.toContain("older");
  expect(wrapper.text()).not.toContain("latest");
  expect(wrapper.emitted("unavailable")).toHaveLength(1);
  wrapper.unmount();
});
it("clears account data before the next account response", async () => {
  vi.mocked(remoteConnectionsApi.history)
    .mockResolvedValueOnce(page("account A") as any)
    .mockResolvedValueOnce(page("account B") as any);
  const wrapper = make();
  await settle();
  resetAccountSession(false);
  await nextTick();
  expect(wrapper.text()).not.toContain("account A");
  await settle();
  expect(wrapper.text()).toContain("account B");
  wrapper.unmount();
});
it("renders remote Markdown without local links, images or custom capabilities", () => {
  const wrapper = mount(MarkdownMessagePart, {
    props: {
      content:
        "**Full text** [local](/api/v1/files/secret) ![image](/api/v1/files/secret) <file-reference>secret</file-reference>",
      refs: null,
      isDark: false,
      customHtmlTags: [],
      inert: true,
    },
  });
  expect(wrapper.find("strong").text()).toBe("Full text");
  expect(
    wrapper.findAll("a, img, file-reference").map((el) => el.html()),
  ).toEqual([]);
});
it("keeps remote user file references as text instead of local file controls", () => {
  const wrapper = mount(ChatMessageList, {
    props: { remoteHistory: true, messages: [{ id: 1, content: { type: "user", message: [
      { type: "plain", text: "Review this\n[Referenced files]\nLocal path\n- `C:/private/secret.txt`" },
    ] } }] },
    global: { plugins: [createPinia()], stubs: { GenerationIndicator: true, RefsSidebar: true } },
  });
  expect(wrapper.findComponent({ name: "UserPlainMessagePart" }).exists()).toBe(false);
  expect(wrapper.findAll("a, button, file-reference")).toHaveLength(0);
  expect(wrapper.text()).toContain("C:/private/secret.txt");
  wrapper.unmount();
});
it("reuses message styling with read-only reasoning and tool disclosures", async () => {
  const wrapper = mount(ChatMessageList, {
    props: {
      remoteHistory: true,
      messages: [
        {
          id: 1,
          content: {
            type: "bot",
            agent_source: { session_id: "same" } as any,
            message: [
              { type: "image", attachment_id: "same" },
              { type: "subagent_run", run_id: "same" },
              { type: "interactive_choice", request_id: "same" },
              {
                type: "tool_call",
                tool_calls: [{ name: "read_file", result: "complete result" }],
              },
            ],
          },
        },
      ],
    },
    global: {
      plugins: [createPinia()],
      stubs: {
        MarkdownMessagePart: true,
        GenerationIndicator: true,
        RefsSidebar: true,
      },
    },
  });
  expect(wrapper.findAll("a, img[src], input")).toHaveLength(0);
  expect(wrapper.findComponent({ name: "SubAgentRunBlock" }).exists()).toBe(false);
  await wrapper.find(".reasoning-header").trigger("click");
  const card = wrapper.findAllComponents({ name: "ToolCallCard" })
    .find((component) => component.props("toolCall").name === "read_file")!;
  expect(card.props("inert")).toBe(true);
  await card.find(".tool-call-header").trigger("click");
  expect(wrapper.text()).toContain("complete result");
  wrapper.unmount();
});

it("discards a late paging failure after switching connections", async () => {
  let fail!: (reason: unknown) => void;
  vi.mocked(remoteConnectionsApi.history)
    .mockResolvedValueOnce(page("machine A", true) as any)
    .mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          fail = reject;
        }) as any,
    )
    .mockResolvedValueOnce(page("machine B") as any);
  const wrapper = make();
  await settle();
  wrapper.findComponent(ChatMessageList).vm.$emit("loadOlder");
  await wrapper.setProps({ connection: connection("b") });
  await settle();
  fail({ response: { data: { data: { code: "remote_unauthorized" } } } });
  await settle();
  expect(wrapper.text()).toContain("machine B");
  expect(wrapper.emitted("unavailable")).toBeUndefined();
  wrapper.unmount();
});

it("clears history and cursor on same-ID binding revision changes", async () => {
  vi.mocked(remoteConnectionsApi.history)
    .mockResolvedValueOnce(page("OLD MACHINE HISTORY", true) as any)
    .mockResolvedValueOnce(page("NEW MACHINE HISTORY") as any)
    .mockResolvedValueOnce(page("NEW KEY HISTORY") as any);
  const wrapper = make();
  await settle();
  await wrapper.setProps({
    connection: {
      ...connection("a"),
      base_url: "http://new-origin",
      revision: "rebound",
    },
  });
  expect(wrapper.text()).not.toContain("OLD MACHINE HISTORY");
  await settle();
  expect(wrapper.text()).toContain("NEW MACHINE HISTORY");
  await wrapper.setProps({
    connection: {
      ...connection("a"),
      base_url: "http://new-origin",
      revision: "rekeyed",
    },
  });
  expect(wrapper.text()).not.toContain("NEW MACHINE HISTORY");
  await settle();
  expect(wrapper.text()).toContain("NEW KEY HISTORY");
  expect(remoteConnectionsApi.history).toHaveBeenNthCalledWith(
    3,
    "a",
    "same",
    undefined,
  );
  wrapper.unmount();
});

it("keeps uncertain input locked until an authoritative accepted receipt", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(value) {
      controller = value;
    },
  });
  vi.mocked(remoteConnectionsApi.events).mockResolvedValueOnce({
    ok: true,
    body,
  } as Response);
  vi.mocked(remoteConnectionsApi.history).mockResolvedValue(
    page("history") as any,
  );
  vi.mocked(remoteConnectionsApi.send).mockRejectedValue({
    response: { data: { data: { code: "remote_acceptance_unknown" } } },
  });
  const wrapper = make();
  await settle();
  controller.enqueue(
    new TextEncoder().encode(
      "data: " +
        JSON.stringify({
          type: "snapshot",
          data: {
            history: [],
            active_runs: [],
            has_more: false,
            is_running: false,
          },
        }) +
        "\n\n",
    ),
  );
  await settle();
  const vm = wrapper.vm as any;
  // Drive the same composer actions without mounting unrelated local ChatInput services.
  vm.draft = "Do not duplicate";
  vm.attachments = [
    { attachment_id: "uploaded", filename: "notes.txt", type: "file" },
  ];
  await wrapper.find("form").trigger("submit");
  await settle();
  expect(remoteConnectionsApi.send).toHaveBeenCalledTimes(1);
  expect(vm.uncertain).toBe(true);
  expect(vm.draft).toBe("Do not duplicate");
  vi.mocked(remoteConnectionsApi.receipt).mockResolvedValueOnce({
    data: { data: { status: "unknown" } },
  } as any);
  await vm.checkReceipt();
  expect(vm.uncertain).toBe(true);
  expect(vm.attachments).toHaveLength(1);
  await wrapper.find("form").trigger("submit");
  expect(remoteConnectionsApi.send).toHaveBeenCalledTimes(1);
  vi.mocked(remoteConnectionsApi.receipt).mockResolvedValueOnce({
    data: { data: { status: "accepted" } },
  } as any);
  await vm.checkReceipt();
  expect(vm.uncertain).toBe(false);
  expect(vm.draft).toBe("");
  expect(vm.attachments).toHaveLength(0);
  wrapper.unmount();
});

it("limits cumulative uploads and keeps definitely rejected input editable", async () => {
  vi.mocked(remoteConnectionsApi.history).mockResolvedValue(
    page("history") as any,
  );
  vi.mocked(remoteConnectionsApi.send).mockRejectedValue({
    response: { status: 422, data: { data: { code: "remote_rejected" } } },
  });
  const wrapper = make();
  await settle();
  const vm = wrapper.vm as any;
  vm.ready = true;
  vm.attachments = Array.from({ length: 20 }, (_, i) => ({
    attachment_id: String(i),
    filename: String(i),
    type: "file",
  }));
  await vm.uploadFiles({
    target: { files: [new File(["x"], "extra.txt")], value: "x" },
  });
  expect(remoteConnectionsApi.upload).not.toHaveBeenCalled();
  expect(vm.error).toBe("remote_attachment_limit");
  expect(vm.attachments).toHaveLength(20);
  vm.draft = "Keep the draft";
  await wrapper.find("form").trigger("submit");
  await settle();
  expect(vm.uncertain).toBe(false);
  expect(vm.draft).toBe("Keep the draft");
  expect(vm.attachments).toHaveLength(20);
  wrapper.unmount();
});

it("renders successive run snapshots before completion, including persisted active rows", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
  vi.mocked(remoteConnectionsApi.events).mockResolvedValueOnce({ ok: true, body } as Response);
  vi.mocked(remoteConnectionsApi.history).mockResolvedValueOnce(page("earlier") as any);
  const wrapper = make();
  await settle();
  const publish = (text: string, active: boolean) => controller.enqueue(new TextEncoder().encode(
    "data: " + JSON.stringify({ type: "snapshot", data: {
      history: [{ id: 2, content: { type: "bot", message: [{ type: "plain", text }], agent_stats: { duration: 3 } } }],
      active_runs: active ? [{ run_id: "live", history_record_id: 2, content: { type: "bot", reasoning: "Checking now", message: [{ type: "plain", text }] } }] : [],
      is_running: active, has_more: false,
    } }) + "\n\n"));
  publish("first token", true);
  await settle();
  let list = wrapper.findComponent(ChatMessageList);
  expect(list.props("isStreaming")).toBe(true);
  expect(list.props("messages")?.find((message: any) => message.id === 2)?.isStreaming).toBe(true);
  expect(wrapper.text()).toContain("first token");
  publish("first token and later tokens", true);
  await settle();
  expect(wrapper.text()).toContain("later tokens");
  publish("finished reply", false);
  await settle();
  list = wrapper.findComponent(ChatMessageList);
  expect(list.props("isStreaming")).toBe(false);
  expect(list.props("messages")?.find((message: any) => message.id === 2)?.content.agentStats).toEqual({ duration: 3 });
  controller.close();
  wrapper.unmount();
});
