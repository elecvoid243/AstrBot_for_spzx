import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import UnifiedRunHistory from "./UnifiedRunHistory.vue";

const api = vi.hoisted(() => ({
  history: vi.fn(),
  historyRecord: vi.fn(),
  historyProcess: vi.fn(),
}));
vi.mock("@/api/workflowAssets", () => ({ workflowAssetsApi: api }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
const slots = { template: "<div><slot /></div>" };
const stubs = {
  "v-card": slots,
  "v-card-text": slots,
  "v-alert": slots,
  "v-chip": slots,
  "v-btn": {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
};

describe("unified run history", () => {
  it("opens original read-only legacy results and process beside a v2 record", async () => {
    api.history.mockResolvedValue({
      records: [
        {
          source: { kind: "legacy_run", id: "old-run" },
          engine: "legacy",
          read_only: true,
          title: "Original report",
          status: "interrupted",
          goal: "Original goal",
        },
        {
          source: { kind: "v2_run", id: "new-run" },
          engine: "v2",
          read_only: false,
          title: "Fresh report",
          status: "completed",
        },
      ],
      next_offset: null,
    });
    api.historyRecord.mockResolvedValue({
      source: { kind: "legacy_run", id: "old-run" },
      engine: "legacy",
      read_only: true,
      members: ["writer"],
      migrated_assets: [
        {
          asset_id: "migrated-report",
          version: 1,
          name: "Migrated report",
          kind: "graph",
        },
      ],
      run: {
        run_id: "old-run",
        team_id: "old-team",
        history_status: "interrupted",
        status: "running",
        input: "Original goal",
        result_summary: "Full original report",
        node_states: { research: { result: "Original task evidence" } },
        graph: {},
        rounds: [],
      },
    });
    api.historyProcess.mockResolvedValue({
      messages: [
        {
          id: 12,
          run_id: "old-run",
          member_id: "writer",
          node_id: "research",
          turn_id: "saved-turn",
          text: "Original response",
          parts: [{ type: "think", text: "Actual original thought" }],
        },
      ],
      next_before_id: null,
    });
    const wrapper = mount(UnifiedRunHistory, { global: { stubs } });
    await flushPromises();
    expect(wrapper.text()).toContain("Fresh report");
    await wrapper
      .get('[data-testid="history-open-legacy_run-old-run"]')
      .trigger("click");
    await flushPromises();
    const detail = wrapper.get('[data-testid="legacy-history-detail"]');
    expect(wrapper.get(".history-list").attributes("open")).toBeUndefined();
    expect(detail.text()).toContain("legacyHistory.readOnly");
    expect(detail.text()).toContain("Full original report");
    expect(detail.text()).toContain("Original task evidence");
    expect(detail.find("input, textarea").exists()).toBe(false);
    await wrapper.get('[data-testid="history-member-writer"]').trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Actual original thought");
    expect(wrapper.text()).toContain("saved-turn");
    expect(wrapper.text()).toContain("research");
    await wrapper.get('[data-testid="history-locate-asset"]').trigger("click");
    expect(wrapper.emitted("locateAsset")?.[0]?.[0]).toMatchObject({
      asset_id: "migrated-report",
    });
    await wrapper
      .get('[data-testid="history-open-v2_run-new-run"]')
      .trigger("click");
    expect(wrapper.emitted("openRun")?.[0]).toEqual(["new-run"]);
    wrapper.unmount();
  });
});
