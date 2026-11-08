import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, ref } from "vue";
import { remoteConnectionsApi } from "@/api/remoteConnections";
import { fetchWithAuth } from "@/api/http";
import SubagentPages from "./SubagentPages.vue";
import ChatMessageList from "./ChatMessageList.vue";
import SubAgentRunBlock from "./message_list_comps/SubAgentRunBlock.vue";
import SubagentConfigurationDialog from "./SubagentConfigurationDialog.vue";

vi.mock("./SubagentConfigurationDialog.vue", () => ({
  default: {
    props: ["run", "sessionId"],
    template: '<div class="config-dialog" />',
  },
}));

vi.mock("@/api/remoteConnections", () => ({
  remoteConnectionsApi: { subagentEvents: vi.fn() },
}));

vi.mock("@/api/http", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/api/v1", () => ({
  chatApi: {
    subagentRunsStreamUrl: (session: string, run?: string) =>
      `/runs/${session}${run ? `/${run}` : ""}`,
  },
}));
vi.mock("@/i18n/composables", () => ({
  useI18n: () => ({ locale: ref("en-US") }),
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
  agent_instance_id: "instance-researcher-original",
  input_preview: "Inspect the first file",
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
const menuStub = defineComponent({
  props: { modelValue: Boolean },
  emits: ["update:modelValue"],
  setup(props, { slots, emit }) {
    return () =>
      h("div", [
        slots.activator?.({
          props: {
            onClick: () => emit("update:modelValue", !props.modelValue),
          },
        }),
        props.modelValue ? slots.default?.() : null,
      ]);
  },
});

function push(url: string, payload: unknown) {
  controllers
    .get(url)!
    .enqueue(new TextEncoder().encode(`data: ${JSON.stringify(payload)}\n\n`));
}

async function page() {
  const wrapper = mount(SubagentPages, {
    props: { sessionId: "session-1" },
    global: { stubs: { "v-icon": true, "v-menu": menuStub } },
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
  it("places navigation in the existing toolbar without rendering a duplicate title", async () => {
    const host = document.createElement("div");
    host.id = "test-session-toolbar";
    document.body.append(host);
    const wrapper = mount(SubagentPages, {
      props: { sessionId: "session-1", toolbarTarget: "#test-session-toolbar" },
      global: { stubs: { "v-icon": true, "v-menu": menuStub } },
    });
    wrappers.push(wrapper);
    await flushPromises();
    expect(host.querySelector(".session-pages-nav")).not.toBeNull();
    expect(wrapper.find(".session-pages-header, h1").exists()).toBe(false);
    await wrapper.setProps({ toolbarTarget: undefined });
    await flushPromises();
    expect(host.children).toHaveLength(0);
    expect(wrapper.find(".session-pages-nav").exists()).toBe(true);
    await wrapper.setProps({ toolbarTarget: "#test-session-toolbar" });
    await flushPromises();
    expect(host.querySelector(".session-pages-nav")).not.toBeNull();
    wrapper.unmount();
    expect(host.children).toHaveLength(0);
    host.remove();
  });

  it("honors unsaved-file navigation cancellation from the toolbar", async () => {
    const wrapper = await page();
    const confirm = vi.fn().mockResolvedValue(false);
    await wrapper.setProps({ beforeNavigate: confirm });
    await wrapper.find(".subagent-picker-trigger").trigger("click");
    await wrapper.find(".subagent-picker-item").trigger("click");
    await flushPromises();
    expect(confirm).toHaveBeenCalledOnce();
    expect(wrapper.findComponent(ChatMessageList).exists()).toBe(false);
    expect(vi.mocked(fetchWithAuth).mock.calls).toHaveLength(1);
  });

  it("opens configuration on demand without changing the selected conversation", async () => {
    const wrapper = await page();
    expect(wrapper.findComponent(SubagentConfigurationDialog).exists()).toBe(
      false,
    );
    await wrapper.find(".subagent-picker-trigger").trigger("click");
    await wrapper.find(".subagent-info-button").trigger("click");
    const dialog = wrapper.findComponent(SubagentConfigurationDialog);
    expect(dialog.props("run")).toMatchObject({ subagent_run_id: "run-1" });
    expect(dialog.props("sessionId")).toBe("session-1");
    expect(wrapper.findComponent(ChatMessageList).exists()).toBe(false);
    expect(vi.mocked(fetchWithAuth).mock.calls).toHaveLength(1);
  });

  it("updates running/background entries without automatically opening a child renderer", async () => {
    const wrapper = await page();
    expect(wrapper.findComponent(ChatMessageList).exists()).toBe(false);
    await wrapper.find(".subagent-picker-trigger").trigger("click");
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
        agent_instance_id: "instance-second",
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

  it("folds repeated invocations and exposes each call's ID, time, preview and conversation", async () => {
    const wrapper = await page();
    const second = {
      ...run,
      subagent_run_id: "run-2",
      started_ts: 1500000000,
      input_preview: "Inspect the second file",
      status: "completed",
      background: false,
    };
    push("/runs/session-1", { type: "changed", data: second });
    await flushPromises();
    await wrapper.find(".subagent-picker-trigger").trigger("click");
    expect(wrapper.findAll(".subagent-agent-toggle")).toHaveLength(1);
    expect(
      wrapper.find(".subagent-agent-toggle").attributes("aria-expanded"),
    ).toBe("false");
    expect(wrapper.find(".subagent-invocations").exists()).toBe(false);
    expect(vi.mocked(fetchWithAuth).mock.calls).toHaveLength(1);
    await wrapper.find(".subagent-agent-toggle").trigger("click");
    const calls = wrapper.findAll(".invocation-item");
    expect(calls).toHaveLength(2);
    expect(calls[0].text()).toContain("run-2");
    expect(calls[0].text()).toContain("Inspect the second file");
    expect(calls[0].find("time").attributes("datetime")).toBe(
      "2017-07-14T02:40:00.000Z",
    );
    expect(calls[0].find("time").text()).toContain("2017");
    expect(calls[1].text()).toContain("Inspect the first file");
    await calls[0].trigger("click");
    await flushPromises();
    expect(controllers.has("/runs/session-1/run-2")).toBe(true);
    expect(controllers.has("/runs/session-1/run-1")).toBe(false);
    push("/runs/session-1", {
      type: "changed",
      data: { ...run, cleaned: true, status: "completed", revision: 2 },
    });
    push("/runs/session-1", {
      type: "changed",
      data: { ...second, cleaned: true, revision: 2 },
    });
    await flushPromises();
    expect(wrapper.find(".subagent-messages").exists()).toBe(true);
    await wrapper.find(".subagent-picker-trigger").trigger("click");
    expect(wrapper.find(".subagent-agent-toggle").exists()).toBe(false);
    await wrapper.find(".cleaned-toggle").trigger("click");
    expect(wrapper.findAll(".subagent-agent-toggle")).toHaveLength(1);
    expect(wrapper.findAll(".invocation-item")).toHaveLength(2);
    await wrapper.find(".subagent-agent-toggle").trigger("click");
    push("/runs/session-1", {
      type: "changed",
      data: {
        ...second,
        subagent_run_id: "run-3",
        started_ts: 1500000010,
        cleaned: true,
      },
    });
    await flushPromises();
    expect(wrapper.find(".subagent-invocations").exists()).toBe(false);
  });

  it("distinguishes recreated same-name agents across current and cleaned sections, including short ID collisions", async () => {
    const wrapper = await page();
    const current = { ...run, agent_instance_id: "instance-a-112233" };
    const previous = {
      ...run,
      subagent_run_id: "previous",
      agent_instance_id: "instance-b-112233",
      status: "completed",
      cleaned: true,
    };
    push("/runs/session-1", { type: "snapshot", data: [current, previous] });
    await flushPromises();
    await wrapper.find(".subagent-picker-trigger").trigger("click");
    await wrapper.find(".cleaned-toggle").trigger("click");
    const groups = wrapper.findAll(".subagent-agent-toggle");
    expect(groups).toHaveLength(2);
    const markers = groups.map((group) =>
      group.find(".instance-marker").text(),
    );
    expect(new Set(markers).size).toBe(2);
    await groups[1].trigger("click");
    await flushPromises();
    push("/runs/session-1/previous", {
      type: "snapshot",
      data: { ...snapshot, ...previous },
    });
    await flushPromises();
    expect(wrapper.find(".session-pages-nav .instance-marker").text()).toBe(
      markers[1],
    );
  });

  it("keeps legacy same-name calls separate and displays missing call details explicitly", async () => {
    const wrapper = await page();
    push("/runs/session-1", {
      type: "snapshot",
      data: [
        { ...run, agent_instance_id: "", subagent_run_id: "legacy-1" },
        { ...run, agent_instance_id: "", subagent_run_id: "legacy-2" },
        {
          ...run,
          agent_instance_id: "known",
          subagent_run_id: "known-1",
          input_preview: "",
          started_ts: 0,
        },
        {
          ...run,
          agent_instance_id: "known",
          subagent_run_id: "known-2",
          input_preview: "",
          started_ts: 0,
        },
      ],
    });
    await flushPromises();
    await wrapper.find(".subagent-picker-trigger").trigger("click");
    expect(wrapper.findAll(".subagent-agent-toggle")).toHaveLength(3);
    const repeated = wrapper
      .findAll(".subagent-agent-toggle")
      .find((group) => group.attributes("aria-expanded") === "false")!;
    await repeated.trigger("click");
    expect(wrapper.find(".invocation-item").text()).toContain(
      "subagentPages.noPreview",
    );
    expect(wrapper.find(".invocation-item time").text()).toContain(
      "subagentPages.unknownTime",
    );
    expect(
      wrapper.find(".invocation-item time").attributes("datetime"),
    ).toBeUndefined();
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
    await wrapper.find(".subagent-picker-trigger").trigger("click");
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
    expect(wrapper.find(".subagent-picker-trigger").text()).toContain(
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
    });
    await flushPromises();
    expect(detailRequest[1]?.signal?.aborted).toBe(true);
    expect(wrapper.findComponent(ChatMessageList).exists()).toBe(false);
    expect(wrapper.emitted("viewChange")?.at(-1)).toEqual([false]);
    expect(wrapper.find(".session-pages-title").exists()).toBe(false);
    expect(controllers.has("/runs/session-2")).toBe(true);
  });

  it("unmounts the child renderer on returning to the main conversation", async () => {
    const wrapper = await page();
    wrapper.vm.openRun(run);
    await flushPromises();
    push("/runs/session-1/run-1", { type: "snapshot", data: snapshot });
    await flushPromises();
    await wrapper.find(".session-main-trigger").trigger("click");
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
    await wrapper.find(".subagent-run-header").trigger("click");
    expect(open).toHaveBeenCalledWith(
      expect.objectContaining({ subagent_run_id: "run-1" }),
      "session-1",
    );
    expect(wrapper.find("input, textarea").exists()).toBe(false);
  });
});

it("reads remote subagent pages through the binding and clears traces on revocation", async () => {
  const scope = {
    connectionId: "machine",
    sessionId: "session-1",
    revision: "key-v1",
  };
  vi.mocked(remoteConnectionsApi.subagentEvents).mockImplementation(
    (_scope, runId, signal) =>
      fetchWithAuth(`/remote/${runId || "list"}`, { signal }),
  );
  const wrapper = mount(SubagentPages, {
    props: { sessionId: "session-1", remoteScope: scope, remoteReady: true },
    global: { stubs: { "v-icon": true, "v-menu": menuStub } },
  });
  wrappers.push(wrapper);
  await flushPromises();
  push("/remote/list", { type: "snapshot", data: [run] });
  await flushPromises();
  await (wrapper.vm as any).openRun(run);
  await flushPromises();
  push("/remote/run-1", {
    type: "snapshot",
    data: { ...snapshot, text: "Remote delegated output" },
  });
  await flushPromises();
  expect(remoteConnectionsApi.subagentEvents).toHaveBeenCalledWith(
    scope,
    "run-1",
    expect.any(AbortSignal),
  );
  expect(wrapper.findComponent(ChatMessageList).props("messages")).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        content: expect.objectContaining({
          message: expect.arrayContaining([
            expect.objectContaining({ text: "Remote delegated output" }),
          ]),
        }),
      }),
    ]),
  );
  push("/remote/run-1", { type: "access_error", code: "remote_unavailable" });
  await flushPromises();
  expect(wrapper.findComponent(ChatMessageList).exists()).toBe(false);
  expect((wrapper.vm as any).getRun("run-1")).toBeUndefined();
});
