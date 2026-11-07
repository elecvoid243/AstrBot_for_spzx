import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { defineComponent, h, ref } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { useMessages } from "../useMessages";
import ChatMessageList from "@/components/chat/ChatMessageList.vue";

vi.mock("@/api/v1", () => ({
  chatApi: {
    systemStreamUrl: () => "/system",
    sendStreamUrl: () => "/send",
    resumeRunStreamUrl: (id: string) => `/resume/${id}`,
    getSession: vi.fn(),
    getHistory: vi.fn(),
    getMarkers: vi.fn(),
  },
  fileApi: { contentUrl: () => "" },
}));
vi.mock("@/api/http", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/stores/interactiveChoice", () => ({
  useInteractiveChoiceStore: () => ({
    activeChoices: {},
    hydrate: vi.fn(),
    injectOrphans: () => 0,
    isIgnored: () => false,
    markIgnored: vi.fn(),
    addChoice: vi.fn(),
    submitChoice: vi.fn(),
    cancelChoice: vi.fn(),
  }),
}));
vi.mock("@/stores/interactiveChoiceAttention", () => ({
  useInteractiveChoiceAttentionStore: () => ({ clearByUmo: vi.fn() }),
}));
import { fetchWithAuth } from "@/api/http";
import { chatApi } from "@/api/v1";

const feeds = new Map<string, ReadableStreamDefaultController<Uint8Array>>();
const encoder = new TextEncoder();
const originalAbortTag = Object.getOwnPropertyDescriptor(
  AbortController.prototype,
  Symbol.toStringTag,
);
let core: ReturnType<typeof useMessages>;
let wrapper: ReturnType<typeof mount>;
function push(url: string, payload: unknown) {
  feeds
    .get(url)!
    .enqueue(encoder.encode(`data: ${JSON.stringify(payload)}\n\n`));
}
async function mountConversation(startHuman = true) {
  const Harness = defineComponent({
    setup() {
      core = useMessages({ currentSessionId: ref("recipient") });
      return () =>
        h(ChatMessageList, {
          messages: core.activeMessages.value,
          isStreaming:
            core.isSessionRunning("recipient") ||
            core.hasLiveSystemRecord("recipient"),
        });
    },
  });
  const passthrough = { template: "<div><slot /></div>" };
  wrapper = mount(Harness, {
    global: {
      stubs: {
        RouterLink: { template: "<a><slot /></a>" },
        StyledMenu: true,
        "v-btn": passthrough,
        "v-card": passthrough,
        "v-menu": passthrough,
        "v-icon": true,
        "v-progress-circular": true,
      },
    },
  });
  await flushPromises();
  if (!startHuman) return;
  const exchange = core.createLocalExchange({
    sessionId: "recipient",
    messageId: "human-run",
    parts: [{ type: "plain", text: "initial request" }],
  });
  core.sendMessageStream({
    ...exchange,
    sessionId: "recipient",
    messageId: "human-run",
    parts: [{ type: "plain", text: "initial request" }],
    transport: "sse",
  });
  await flushPromises();
  push("/send", {
    type: "run_started",
    data: { run_id: "running-agent" },
  });
  push("/send", {
    type: "plain",
    chain_type: "reasoning",
    data: "before follow-up",
    streaming: true,
  });
  await flushPromises();
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(chatApi.getMarkers).mockResolvedValue({
    data: { data: { markers: [], total_messages: 0 } },
  } as never);
  // Match native browser AbortController identity, avoiding happy-dom proxying.
  Object.defineProperty(AbortController.prototype, Symbol.toStringTag, {
    value: "AbortController",
    configurable: true,
  });
  setActivePinia(createPinia());
  feeds.clear();
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.mocked(fetchWithAuth).mockImplementation(
    async (url, options) =>
      new Response(
        new ReadableStream({
          start(controller) {
            feeds.set(String(url), controller);
            options?.signal?.addEventListener(
              "abort",
              () => controller.error(new DOMException("Aborted", "AbortError")),
              { once: true },
            );
          },
        }),
        { headers: { "content-type": "text/event-stream" } },
      ),
  );
});
afterEach(async () => {
  core?.cleanupConnections();
  wrapper?.unmount();
  await flushPromises();
  if (originalAbortTag)
    Object.defineProperty(
      AbortController.prototype,
      Symbol.toStringTag,
      originalAbortTag,
    );
  else Reflect.deleteProperty(AbortController.prototype, Symbol.toStringTag);
  vi.restoreAllMocks();
});

it.each([
  { agent: false, receiptFirst: false, confirmed: false },
  { agent: true, receiptFirst: false, confirmed: true },
  { agent: true, receiptFirst: true, confirmed: true },
  { agent: true, receiptFirst: false, confirmed: false },
])(
  "keeps the spinner on the real reply (Agent=$agent, receipt first=$receiptFirst, confirmed=$confirmed)",
  async ({ agent, receiptFirst, confirmed }) => {
    await mountConversation();
    const input = {
      id: 3,
      content: {
        type: "user",
        message: [{ type: "plain", text: "follow-up" }],
        ...(agent
          ? {
              agent_source: {
                kind: "agent",
                account_id: "test",
                session_id: "source",
                display_name: "Source",
              },
            }
          : {}),
      },
    };
    if (agent && receiptFirst) {
      push("/system", {
        type: "run_started",
        synthetic: true,
        message_id: "agent-receipt",
        data: { input_message: input },
      });
      await flushPromises();
    }
    if (!agent)
      core.createLocalExchange({
        sessionId: "recipient",
        messageId: "follow-up-request",
        parts: [{ type: "plain", text: "follow-up" }],
      });
    push("/send", {
      type: "message_segment",
      data: {
        segment_id: "after-follow-up",
        user: input,
        client_message_id: "follow-up-request",
        previous: {
          id: 2,
          content: {
            type: "bot",
            message: [{ type: "think", think: "before follow-up" }],
          },
        },
        content: { type: "bot", message: [] },
      },
    });
    push("/send", {
      type: "plain",
      chain_type: "reasoning",
      data: "still thinking in the real run",
      streaming: true,
    });
    await flushPromises();
    if (agent) {
      if (!receiptFirst) {
        push("/system", {
          type: "run_started",
          synthetic: true,
          message_id: "agent-receipt",
          data: { input_message: input },
        });
        await flushPromises();
      }
      push("/resume/agent-receipt", {
        type: "run_snapshot",
        data: {
          run_id: "agent-receipt",
          status: "running",
          input_message: input,
          content: { type: "bot", message: [] },
        },
      });
      await flushPromises();
      expect(
        core.activeMessages.value.filter(
          (record) =>
            record.content.type === "bot" &&
            record.content.message.length === 0,
        ),
      ).toHaveLength(0);
      if (confirmed) {
        push("/resume/agent-receipt", {
          type: "follow_up_captured",
          data: { target_run_id: "running-agent" },
        });
        // A replay after confirmation must not resurrect the receipt.
        push("/resume/agent-receipt", {
          type: "run_snapshot",
          data: {
            run_id: "agent-receipt",
            status: "running",
            content: { type: "bot", message: [] },
          },
        });
      }
      push("/resume/agent-receipt", { type: "end" });
      feeds.get("/resume/agent-receipt")!.close();
      await flushPromises();
    }
    const records = core.activeMessages.value;
    const emptyBots = records.filter(
      (record) =>
        record.content.type === "bot" && record.content.message.length === 0,
    );
    const spinningRows = wrapper
      .findAll(".message-row")
      .filter((row) =>
        row.find(".message-rail .generation-indicator:not(.is-off)").exists(),
      );
    const spinnerOwner = spinningRows.map((row) =>
      row.attributes("data-message-id"),
    );
    expect(core.isSessionRunning("recipient")).toBe(true);
    expect(emptyBots).toHaveLength(0);
    expect(spinnerOwner).toEqual(["active-segment-after-follow-up"]);
    expect(
      records.find(
        (record) => String(record.id) === "active-segment-after-follow-up",
      )?.content.message.length,
    ).toBeGreaterThan(0);
  },
);

it("reattaches to the actual run when only the follow-up receipt was announced", async () => {
  await mountConversation(false);
  push("/system", {
    type: "run_started",
    synthetic: true,
    message_id: "receipt",
    data: {
      input_message: {
        id: 3,
        content: {
          type: "user",
          message: [{ type: "plain", text: "Agent follow-up" }],
        },
      },
    },
  });
  await flushPromises();
  expect(core.activeMessages.value.at(-1)?.content.isLoading).toBe(true);
  push("/resume/receipt", {
    type: "follow_up_captured",
    data: { target_run_id: "original" },
  });
  await flushPromises();
  expect(feeds.has("/resume/original")).toBe(true);
  expect(
    core.activeMessages.value.some((record) =>
      String(record.id).includes("receipt"),
    ),
  ).toBe(false);
  push("/resume/original", {
    type: "run_snapshot",
    data: {
      run_id: "original",
      status: "running",
      content: {
        type: "bot",
        message: [
          { type: "think", think: "The original Agent is still working" },
        ],
      },
    },
  });
  push("/resume/receipt", { type: "end" });
  feeds.get("/resume/receipt")!.close();
  await flushPromises();
  const spinningRows = wrapper
    .findAll(".message-row")
    .filter((row) =>
      row.find(".message-rail .generation-indicator:not(.is-off)").exists(),
    );
  expect(spinningRows.map((row) => row.attributes("data-message-id"))).toEqual([
    "local-bot-original",
  ]);
  expect(core.isSessionRunning("recipient")).toBe(true);
});

it("settles a captured human send without refreshing or interrupting the original run", async () => {
  await mountConversation();
  const originalFeed = feeds.get("/send")!;
  const exchange = core.createLocalExchange({
    sessionId: "recipient",
    messageId: "human-follow-up",
    parts: [{ type: "plain", text: "continue" }],
  });
  core.sendMessageStream({
    ...exchange,
    sessionId: "recipient",
    messageId: "human-follow-up",
    parts: [{ type: "plain", text: "continue" }],
    transport: "sse",
  });
  await flushPromises();
  push("/send", {
    type: "follow_up_captured",
    data: { target_run_id: "human-run" },
  });
  push("/send", { type: "end" });
  feeds.get("/send")!.close();
  await flushPromises();
  expect(chatApi.getSession).not.toHaveBeenCalled();
  expect(
    core.activeMessages.value.some(
      (record) => record.id === "local-bot-human-follow-up",
    ),
  ).toBe(false);
  originalFeed.enqueue(
    encoder.encode(
      `data: ${JSON.stringify({
        type: "plain",
        chain_type: "reasoning",
        data: "continued after the follow-up",
        streaming: true,
      })}\n\n`,
    ),
  );
  await flushPromises();
  expect(core.isSessionRunning("recipient")).toBe(true);
  expect(
    core.activeMessages.value.find(
      (record) => record.id === "local-bot-human-run",
    )?.content.message,
  ).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        type: "think",
        think: expect.stringContaining("continued after the follow-up"),
      }),
    ]),
  );
});

it("renders an independent Agent reply after an idle-session announcement", async () => {
  await mountConversation(false);
  push("/system", {
    type: "run_started",
    synthetic: true,
    message_id: "independent",
    data: {},
  });
  await flushPromises();
  expect(wrapper.find(".loading-message").exists()).toBe(true);
  push("/resume/independent", {
    type: "run_snapshot",
    data: {
      run_id: "independent",
      status: "running",
      content: { type: "bot", message: [] },
    },
  });
  push("/resume/independent", {
    type: "plain",
    data: "Independent Agent answer",
    streaming: true,
  });
  await flushPromises();
  expect(wrapper.text()).toContain("Independent Agent answer");
  expect(
    wrapper.find(".message-rail .generation-indicator.is-slow").exists(),
  ).toBe(true);
});

it("keeps system-stream replies live when they capture an Agent follow-up", async () => {
  await mountConversation(false);
  push("/system", {
    type: "plain",
    message_id: "orphan-run",
    chain_type: "reasoning",
    data: "The system Agent is working",
    streaming: true,
  });
  await flushPromises();
  expect(core.hasLiveSystemRecord("recipient")).toBe(true);
  push("/system", {
    type: "run_started",
    synthetic: true,
    message_id: "receipt",
    data: {},
  });
  await flushPromises();
  push("/resume/receipt", {
    type: "run_snapshot",
    data: {
      run_id: "receipt",
      status: "running",
      content: { type: "bot", message: [] },
    },
  });
  push("/resume/receipt", {
    type: "follow_up_captured",
    data: { target_run_id: "orphan-run" },
  });
  push("/resume/receipt", { type: "end" });
  feeds.get("/resume/receipt")!.close();
  await flushPromises();
  expect(feeds.has("/resume/orphan-run")).toBe(false);
  expect(core.activeMessages.value).toHaveLength(1);
  expect(
    wrapper
      .get('[data-message-id="system-orphan-run"] .generation-indicator')
      .classes(),
  ).toContain("is-slow");
});

it("removes a reattachment placeholder if the original run has already disappeared", async () => {
  await mountConversation(false);
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.mocked(chatApi.getSession).mockResolvedValue({
    data: {
      status: "ok",
      data: {
        history: [],
        active_runs: [],
        threads: [],
        has_more: false,
        total_messages: 0,
      },
    },
  } as never);
  const fetchStream = vi.mocked(fetchWithAuth).getMockImplementation()!;
  vi.mocked(fetchWithAuth).mockImplementation((url, options) =>
    String(url) === "/resume/finished"
      ? Promise.resolve(new Response("", { status: 404 }))
      : fetchStream(url, options),
  );
  push("/system", {
    type: "run_started",
    synthetic: true,
    message_id: "receipt",
    data: {},
  });
  await flushPromises();
  push("/resume/receipt", {
    type: "follow_up_captured",
    data: { target_run_id: "finished" },
  });
  push("/resume/receipt", { type: "end" });
  feeds.get("/resume/receipt")!.close();
  await vi.waitFor(() => expect(chatApi.getSession).toHaveBeenCalled());
  await flushPromises();
  expect(core.activeMessages.value).toEqual([]);
  expect(core.isSessionRunning("recipient")).toBe(false);
});

it("shows a queued request if it starts its own reply instead of being captured", async () => {
  await mountConversation();
  push("/system", {
    type: "run_started",
    synthetic: true,
    message_id: "queued",
    data: {},
  });
  await flushPromises();
  push("/resume/queued", {
    type: "run_snapshot",
    data: {
      run_id: "queued",
      status: "running",
      content: { type: "bot", message: [] },
    },
  });
  await flushPromises();
  expect(
    core.activeMessages.value.some((record) =>
      String(record.id).includes("queued"),
    ),
  ).toBe(false);
  push("/resume/queued", { type: "run_started", data: { run_id: "queued" } });
  await flushPromises();
  expect(
    wrapper
      .find('[data-message-id="local-bot-queued"] .loading-message')
      .exists(),
  ).toBe(true);
  push("/resume/queued", {
    type: "plain",
    data: "A separate reply",
    streaming: true,
  });
  await flushPromises();
  expect(wrapper.get('[data-message-id="local-bot-queued"]').text()).toContain(
    "A separate reply",
  );
});

it("stops the spinner when the real run ends even if the captured receipt remains connected", async () => {
  await mountConversation();
  push("/system", {
    type: "run_started",
    synthetic: true,
    message_id: "receipt",
    data: {},
  });
  await flushPromises();
  push("/resume/receipt", {
    type: "follow_up_captured",
    data: { target_run_id: "running-agent" },
  });
  await flushPromises();
  push("/send", { type: "plain", data: "Final answer", streaming: true });
  push("/send", { type: "end" });
  feeds.get("/send")!.close();
  await flushPromises();
  expect(core.isSessionRunning("recipient")).toBe(false);
  expect(
    core.activeMessages.value.some((record) =>
      String(record.id).includes("receipt"),
    ),
  ).toBe(false);
  expect(
    wrapper.findAll(".message-rail .generation-indicator:not(.is-off)"),
  ).toHaveLength(0);
});
