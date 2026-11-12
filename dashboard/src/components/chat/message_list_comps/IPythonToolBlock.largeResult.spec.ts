import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import IPythonToolBlock from "@/components/chat/message_list_comps/IPythonToolBlock.vue";
import { fetchWithAuth } from "@/api/http";

vi.mock("@/api/http", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/api/v1", () => ({
  chatApi: {
    runToolResultUrl: (runId: string, toolCallId: string) =>
      `/api/v1/chat/runs/${runId}/tool-results/${toolCallId}`,
    messageToolResultUrl: (messageId: string | number, toolCallId: string) =>
      `/api/v1/chat/messages/${messageId}/tool-results/${toolCallId}`,
  },
}));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({
    tm: (key: string) => key,
  }),
}));
vi.mock("@/utils/shiki", () => ({
  ensureShikiLanguages: vi.fn().mockResolvedValue({}),
  escapeHtml: (text: string) => text,
  renderShikiCode: () => "",
}));

const baseToolCall = {
  id: "call-1",
  name: "astrbot_execute_python",
  args: { code: "print('x')" },
  result_ref: { run_id: "run-1", tool_call_id: "call-1", bytes: 70000 },
  result_preview: "preview text",
  finished_ts: 2,
};

function mountBlock(toolCall = baseToolCall) {
  return mount(IPythonToolBlock, {
    props: {
      toolCall,
      isDark: false,
      showHeader: false,
      forceExpanded: true,
      messageId: 7,
    },
    global: {
      stubs: {
        CopyableText: {
          props: ["value"],
          template: "<pre data-testid='copyable'>{{ value }}</pre>",
        },
      },
    },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("IPythonToolBlock large results", () => {
  it("fetches the referenced result on mount and renders it", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      new Response(
        JSON.stringify({ status: "ok", data: { result: "full result" } }),
        { headers: { "content-type": "application/json" } },
      ),
    );
    const wrapper = mountBlock();
    await flushPromises();

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/chat/runs/run-1/tool-results/call-1",
    );
    expect(wrapper.text()).toContain("full result");
  });

  it("falls back to the persisted message endpoint when the run is gone", async () => {
    vi.mocked(fetchWithAuth).mockImplementation(async (url) => {
      if (String(url).includes("/chat/messages/7/tool-results/call-1")) {
        return new Response(
          JSON.stringify({ status: "ok", data: { result: "history result" } }),
          { headers: { "content-type": "application/json" } },
        );
      }
      return new Response(null, { status: 404 });
    });
    const wrapper = mountBlock();
    await flushPromises();

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/chat/runs/run-1/tool-results/call-1",
    );
    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/chat/messages/7/tool-results/call-1",
    );
    expect(wrapper.text()).toContain("history result");
  });
});
