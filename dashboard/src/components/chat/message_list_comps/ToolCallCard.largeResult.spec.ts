import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import ToolCallCard from "@/components/chat/message_list_comps/ToolCallCard.vue";
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

const baseToolCall = {
  id: "call-1",
  name: "astrbot_file_read_tool",
  args: { path: "a.py" },
  result_ref: { run_id: "run-1", tool_call_id: "call-1", bytes: 70000 },
  result_preview: "preview text",
  finished_ts: 2,
};

function mountCard(toolCall = baseToolCall) {
  return mount(ToolCallCard, {
    props: {
      toolCall,
      isDark: false,
      isActive: false,
      messageId: 7,
    },
    global: {
      stubs: {
        "v-icon": true,
        ToolResultView: {
          props: ["result"],
          template: "<pre data-testid='tool-result'>{{ result }}</pre>",
        },
        DiffPreview: true,
        CopyableText: true,
      },
    },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ToolCallCard large results", () => {
  it("shows the preview and fetches the full result on expand", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      new Response(
        JSON.stringify({ status: "ok", data: { result: "full result" } }),
        { headers: { "content-type": "application/json" } },
      ),
    );
    const wrapper = mountCard();

    expect(wrapper.text()).toContain("preview text");
    expect(fetchWithAuth).not.toHaveBeenCalled();

    await wrapper.find(".tool-call-header").trigger("click");
    await flushPromises();
    await vi.waitFor(() =>
      expect(wrapper.find("[data-testid='tool-result']").exists()).toBe(true),
    );
    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/chat/runs/run-1/tool-results/call-1",
    );
    expect(wrapper.find("[data-testid='tool-result']").text()).toBe(
      "full result",
    );
  });

  it("falls back to the persisted message endpoint when the run is gone", async () => {
    vi.mocked(fetchWithAuth).mockImplementation(async (url) => {
      const target = String(url);
      if (target.includes("/chat/messages/7/tool-results/call-1")) {
        return new Response(
          JSON.stringify({
            status: "ok",
            data: { result: "history result" },
          }),
          { headers: { "content-type": "application/json" } },
        );
      }
      return new Response(null, { status: 404 });
    });
    const wrapper = mountCard();

    await wrapper.find(".tool-call-header").trigger("click");
    await flushPromises();
    await vi.waitFor(() =>
      expect(wrapper.find("[data-testid='tool-result']").exists()).toBe(true),
    );
    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/chat/runs/run-1/tool-results/call-1",
    );
    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/chat/messages/7/tool-results/call-1",
    );
    expect(wrapper.find("[data-testid='tool-result']").text()).toBe(
      "history result",
    );
  });
});
