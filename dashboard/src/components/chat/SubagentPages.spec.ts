import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import { fetchWithAuth } from "@/api/http";
import SubagentPages from "./SubagentPages.vue";
import ChatMessageList from "./ChatMessageList.vue";
import SubAgentRunBlock from "./message_list_comps/SubAgentRunBlock.vue";

vi.mock("@/api/http", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/api/v1", () => ({
  chatApi: {
    subagentRunsStreamUrl: (session: string, run?: string) =>
      `/runs/${session}${run ? `/${run}` : ""}`,
  },
}));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
vi.mock("./ChatMessageList.vue", async () => {
  const { defineComponent, h } = await import("vue");
  return {
    default: defineComponent({
      name: "ChatMessageList",
      props: ["messages", "isStreaming"],
      setup: () => () => h("div", { class: "message-renderer" }),
    }),
  };
});
vi.mock("./ReasoningSidebar.vue", () => ({
  default: { template: "<aside />" },
}));

const controllers = new Map<
  string,
  ReadableStreamDefaultController<Uint8Array>
>();
const wrappers: Array<ReturnType<typeof mount>> = [];
const run = {
  subagent_run_id: "run-1",
  agent_name: "researcher",
  status: "running",
  background: true,
  cleaned: false,
  started_ts: 1,
  revision: 1,
};
const snapshot = {
  ...run,
  type: "subagent_run",
  input_full: "Complete delegated task",
  input_preview: "Complete",
  activity: [],
  reasoning: "",
  text: "",
  tool_calls: [],
};

function push(url: string, payload: unknown) {
  controllers
    .get(url)!
    .enqueue(new TextEncoder().encode(`data: ${JSON.stringify(payload)}\n\n`));
}

async function page() {
  const wrapper = mount(SubagentPages, {
    props: { sessionId: "session-1", title: "Parent conversation" },
    global: { stubs: { "v-icon": true } },
  });
  wrappers.push(wrapper);
  await flushPromises();
  push("/runs/session-1", { type: "snapshot", data: [run] });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  controllers.clear();
  vi.mocked(fetchWithAuth).mockImplementation(async (url, options) => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controllers.set(String(url), controller);
        options?.signal?.addEventListener(
          "abort",
          () => controller.error(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
      },
    });
    return new Response(stream, {
      headers: { "content-type": "text/event-stream" },
    });
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(async () => {
  for (const wrapper of wrappers.splice(0)) wrapper.unmount();
  await flushPromises();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("Subagent conversation pages", () => {
  it("updates running/background entries without automatically opening a child renderer", async () => {
    const wrapper = await page();
    expect(wrapper.findComponent(ChatMessageList).exists()).toBe(false);
    await wrapper.find(".subagent-picker > button").trigger("click");
    expect(wrapper.find(".subagent-picker-item").text()).toContain(
      "researcher",
    );
    expect(wrapper.find(".subagent-picker-item .running").exists()).toBe(true);
    expect(wrapper.find(".subagent-picker-item").text()).toContain(
      "subagentPages.background",
    );
    push("/runs/session-1", {
      type: "changed",
      data: {
        ...run,
        subagent_run_id: "run-2",
        agent_name: "second",
        started_ts: 2,
      },
    });
    await flushPromises();
    expect(wrapper.findAll(".subagent-picker-item")).toHaveLength(2);
    push("/runs/session-1", {
      type: "changed",
      data: { ...run, status: "completed", revision: 3 },
    });
    push("/runs/session-1", {
      type: "changed",
      data: { ...run, status: "running", revision: 2 },
    });
    await flushPromises();
    expect(
      wrapper
        .findAll(".subagent-picker-item")
        .find((item) => item.text().includes("researcher"))!
        .text(),
    ).toContain("subagentStatus.completed");
    expect(wrapper.findComponent(ChatMessageList).exists()).toBe(false);
  });

  it("renders one readonly invocation with the main message renderer and ordered live updates", async () => {
    const wrapper = await page();
    wrapper.vm.openRun(run);
    await flushPromises();
    push("/runs/session-1/run-1", { type: "snapshot", data: snapshot });
    await flushPromises();
    push("/runs/session-1/run-1", {
      type: "event",
      revision: 2,
      data: {
        subagent_run_id: "run-1",
        agent_name: "researcher",
        kind: "reasoning_delta",
        payload: { text: "Checking files" },
      },
    });
    push("/runs/session-1/run-1", {
      type: "event",
      revision: 3,
      data: {
        subagent_run_id: "run-1",
        agent_name: "researcher",
        kind: "tool_call",
        payload: { id: "tool-1", name: "read_file", args: { path: "a.py" } },
      },
    });
    // An event already included in a recovery snapshot must not append twice.
    push("/runs/session-1/run-1", {
      type: "event",
      revision: 2,
      data: {
        subagent_run_id: "run-1",
        kind: "reasoning_delta",
        payload: { text: "duplicate" },
      },
    });
    await flushPromises();
    const records = wrapper.findComponent(ChatMessageList).props("messages");
    expect(records[0].content.message[0].text).toBe("Complete delegated task");
    expect(records[1].content.message).toEqual([
      { type: "think", think: "Checking files" },
      {
        type: "tool_call",
        tool_calls: [expect.objectContaining({ id: "tool-1" })],
      },
    ]);
    expect(wrapper.findAll(".message-renderer")).toHaveLength(1);
    expect(wrapper.find("input, textarea").exists()).toBe(false);
  });

  it("keeps the selected page readable after completion and cleanup, with archived entries initially folded", async () => {
    const wrapper = await page();
    wrapper.vm.openRun(run);
    await flushPromises();
    push("/runs/session-1/run-1", { type: "snapshot", data: snapshot });
    push("/runs/session-1/run-1", {
      type: "event",
      revision: 2,
      data: {
        subagent_run_id: "run-1",
        kind: "completed",
        payload: { result_text: "Final report" },
      },
    });
    push("/runs/session-1", {
      type: "changed",
      data: { ...run, status: "completed", cleaned: true, revision: 3 },
    });
    push("/runs/session-1/run-1", {
      type: "metadata",
      data: { ...run, status: "completed", cleaned: true, revision: 3 },
    });
    await flushPromises();
    expect(wrapper.findComponent(ChatMessageList).props("isStreaming")).toBe(
      false,
    );
    expect(
      wrapper.findComponent(ChatMessageList).props("messages")[1].content
        .message[0].text,
    ).toBe("Final report");
    expect(wrapper.emitted("viewChange")?.at(-1)).toEqual([true]);
    await wrapper.find(".subagent-picker > button").trigger("click");
    expect(wrapper.findAll(".subagent-picker-item")).toHaveLength(0);
    await wrapper.find(".cleaned-toggle").trigger("click");
    expect(wrapper.findAll(".subagent-picker-item")).toHaveLength(1);
  });

  it("applies the final result when cleanup metadata overtakes terminal persistence", async () => {
    const wrapper = await page();
    wrapper.vm.openRun(run);
    await flushPromises();
    push("/runs/session-1/run-1", { type: "snapshot", data: snapshot });
    push("/runs/session-1/run-1", {
      type: "metadata",
      data: { ...run, status: "completed", cleaned: true, revision: 3 },
    });
    const completed = {
      type: "event",
      revision: 3,
      data: {
        subagent_run_id: "run-1",
        kind: "completed",
        payload: { result_text: "Final report" },
      },
    };
    push("/runs/session-1/run-1", completed);
    push("/runs/session-1/run-1", completed);
    push("/runs/session-1/run-1", {
      type: "metadata",
      data: { ...run, status: "running", revision: 2 },
    });
    await flushPromises();
    expect(wrapper.findComponent(ChatMessageList).props("isStreaming")).toBe(
      false,
    );
    expect(
      wrapper.findComponent(ChatMessageList).props("messages")[1].content
        .message,
    ).toEqual([{ type: "plain", text: "Final report" }]);
    expect(wrapper.find(".subagent-picker > button").text()).toContain(
      "subagentPages.cleaned",
    );
  });

  it("returns to the main page on session switch and aborts the previous detail subscription", async () => {
    const wrapper = await page();
    wrapper.vm.openRun(run);
    await flushPromises();
    const detailRequest = vi
      .mocked(fetchWithAuth)
      .mock.calls.find(([url]) => String(url).endsWith("/run-1"))!;
    await wrapper.setProps({
      sessionId: "session-2",
      title: "Other conversation",
    });
    await flushPromises();
    expect(detailRequest[1]?.signal?.aborted).toBe(true);
    expect(wrapper.findComponent(ChatMessageList).exists()).toBe(false);
    expect(wrapper.emitted("viewChange")?.at(-1)).toEqual([false]);
    expect(wrapper.find(".session-pages-title").text()).toBe(
      "Other conversation",
    );
    expect(controllers.has("/runs/session-2")).toBe(true);
  });

  it("unmounts the child renderer on returning to the main conversation", async () => {
    const wrapper = await page();
    wrapper.vm.openRun(run);
    await flushPromises();
    push("/runs/session-1/run-1", { type: "snapshot", data: snapshot });
    await flushPromises();
    await wrapper.find(".session-pages-nav > button").trigger("click");
    expect(wrapper.findComponent(ChatMessageList).exists()).toBe(false);
    expect(wrapper.emitted("viewChange")?.at(-1)).toEqual([false]);
  });

  it("follows new tokens, yields to scrolling up, and resumes after returning to the tail", async () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    const wrapper = await page();
    wrapper.vm.openRun(run);
    await flushPromises();
    const viewport = wrapper.find(".subagent-messages");
    let height = 600;
    let top = 200;
    Object.defineProperties(viewport.element, {
      scrollHeight: { configurable: true, get: () => height },
      clientHeight: { configurable: true, get: () => 400 },
      scrollTop: {
        configurable: true,
        get: () => top,
        set: (value) => {
          top = Math.min(height - 400, value);
        },
      },
    });
    push("/runs/session-1/run-1", { type: "snapshot", data: snapshot });
    await flushPromises();
    frames.shift()?.(0);
    height = 900;
    push("/runs/session-1/run-1", {
      type: "event",
      revision: 2,
      data: {
        subagent_run_id: "run-1",
        kind: "text_delta",
        payload: { text: "new output" },
      },
    });
    await flushPromises();
    frames.shift()?.(0);
    expect(top).toBe(500);
    await viewport.trigger("wheel", { deltaY: -50 });
    top = 400;
    await viewport.trigger("scroll");
    height = 1100;
    push("/runs/session-1/run-1", {
      type: "event",
      revision: 3,
      data: {
        subagent_run_id: "run-1",
        kind: "text_delta",
        payload: { text: "below the reader" },
      },
    });
    await flushPromises();
    expect(frames).toHaveLength(0);
    expect(top).toBe(400);
    await viewport.trigger("wheel", { deltaY: 100 });
    top = 700;
    await viewport.trigger("scroll");
    height = 1300;
    push("/runs/session-1/run-1", {
      type: "event",
      revision: 4,
      data: {
        subagent_run_id: "run-1",
        kind: "text_delta",
        payload: { text: "following again" },
      },
    });
    await flushPromises();
    frames.shift()?.(0);
    expect(top).toBe(900);
  });

  it("keeps parent cards compact and navigates instead of mounting hidden execution content", async () => {
    const open = vi.fn();
    const state = ref({ status: "completed", cleaned: true });
    const wrapper = mount(SubAgentRunBlock, {
      props: {
        part: {
          ...snapshot,
          activity: [{ kind: "think", text: "long hidden trace" }],
        },
        sessionId: "session-1",
      },
      global: {
        stubs: { "v-icon": true },
        provide: {
          openSubagentRun: open,
          subagentRunMetadata: () => state.value,
        },
      },
    });
    wrappers.push(wrapper);
    expect(wrapper.find(".subagent-run-body").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("long hidden trace");
    expect(wrapper.text()).toContain("subagentPages.cleaned");
    await wrapper.trigger("click");
    expect(open).toHaveBeenCalledWith(
      expect.objectContaining({ subagent_run_id: "run-1" }),
      "session-1",
    );
    expect(wrapper.find("input, textarea").exists()).toBe(false);
  });
});
