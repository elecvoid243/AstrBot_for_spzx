import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("refreshes the open original result and selected member process without hiding the detail", async () => {
    let completed = false;
    let detail = {
      source: { kind: "legacy_run", id: "refresh-run" },
      engine: "legacy",
      read_only: true,
      members: ["writer"],
      migrated_assets: [],
      run: {
        run_id: "refresh-run",
        team_name: "Original report",
        status: "running",
        history_status: "running",
        execution_active: true,
        input: "Original goal",
        result_summary: "",
        node_states: {},
        graph: {},
        rounds: [],
      },
    };
    api.history.mockImplementation(async () => ({
      records: [
        { ...detail, title: "Original report", status: detail.run.status },
      ],
      next_offset: null,
    }));
    api.historyRecord.mockImplementation(async () => structuredClone(detail));
    api.historyProcess.mockImplementation(async () => ({
      messages: [
        {
          id: completed ? 2 : 1,
          run_id: "refresh-run",
          member_id: "writer",
          turn_id: completed ? "final-turn" : "first-turn",
          text: completed ? "Final saved response" : "Earlier saved response",
        },
      ],
      next_before_id: completed ? null : 1,
    }));
    const wrapper = mount(UnifiedRunHistory, { global: { stubs } });
    try {
      await flushPromises();
      await wrapper
        .get('[data-testid="history-open-legacy_run-refresh-run"]')
        .trigger("click");
      await flushPromises();
      await wrapper
        .get('[data-testid="history-member-writer"]')
        .trigger("click");
      await flushPromises();
      expect(wrapper.text()).toContain("Earlier saved response");
      let resolveOlder:
        | ((value: {
            messages: { id: number; text: string }[];
            next_before_id: null;
          }) => void)
        | undefined;
      api.historyProcess.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOlder = resolve;
          }),
      );
      await wrapper
        .get('[data-testid="history-more-process"]')
        .trigger("click");
      await flushPromises();
      completed = true;
      detail = {
        ...detail,
        run: {
          ...detail.run,
          status: "completed",
          history_status: "completed",
          execution_active: false,
          result_summary: "Actual final result",
        },
      };
      let resolveDetail: ((value: typeof detail) => void) | undefined;
      api.historyRecord.mockImplementationOnce(
        () =>
          new Promise<typeof detail>((resolve) => {
            resolveDetail = resolve;
          }),
      );
      const refresh = wrapper
        .findAll("button")
        .find((button) => button.text() === "page.refresh");
      expect(refresh).toBeDefined();
      await refresh!.trigger("click");
      await flushPromises();
      expect(
        wrapper.get('[data-testid="legacy-history-detail"]').text(),
      ).toContain("Original report");
      expect(wrapper.get(".history-list").text()).toContain(
        "monitor.status.completed",
      );
      resolveDetail?.(structuredClone(detail));
      await flushPromises();
      const refreshed = wrapper.get('[data-testid="legacy-history-detail"]');
      expect(refreshed.text()).toContain("Actual final result");
      expect(refreshed.text()).toContain("monitor.status.completed");
      expect(refreshed.text()).toContain("Final saved response");
      expect(refreshed.text()).toContain("final-turn");
      expect(refreshed.text()).not.toContain("Earlier saved response");
      expect(refreshed.text()).not.toContain("legacyHistory.stillActive");
      expect(refreshed.text()).not.toContain("legacyHistory.noResult");
      expect(refreshed.find("input, textarea").exists()).toBe(false);
      resolveOlder?.({
        messages: [{ id: 0, text: "Obsolete older response" }],
        next_before_id: null,
      });
      await flushPromises();
      expect(refreshed.text()).not.toContain("Obsolete older response");
    } finally {
      wrapper.unmount();
    }
  });

  it("keeps a newly selected record when an earlier refresh list arrives", async () => {
    const records = ["first", "second"].map((id) => ({
      source: { kind: "legacy_run", id },
      engine: "legacy",
      read_only: true,
      title: id,
      status: "completed",
    }));
    api.history.mockResolvedValue({ records, next_offset: null });
    api.historyRecord.mockImplementation(async (_kind, id) => ({
      source: { kind: "legacy_run", id },
      members: [],
      migrated_assets: [],
      run: {
        history_status: "completed",
        result_summary:
          id === "first" ? "First record result" : "Second record result",
        node_states: {},
        graph: {},
        rounds: [],
      },
    }));
    const wrapper = mount(UnifiedRunHistory, { global: { stubs } });
    try {
      await flushPromises();
      await wrapper
        .get('[data-testid="history-open-legacy_run-first"]')
        .trigger("click");
      await flushPromises();
      let resolvePage:
        | ((value: { records: typeof records; next_offset: null }) => void)
        | undefined;
      api.history.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolvePage = resolve;
          }),
      );
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "page.refresh")!
        .trigger("click");
      await flushPromises();
      await wrapper
        .get('[data-testid="history-open-legacy_run-second"]')
        .trigger("click");
      await flushPromises();
      expect(
        wrapper.get('[data-testid="legacy-history-detail"]').text(),
      ).toContain("Second record result");
      resolvePage?.({ records, next_offset: null });
      await flushPromises();
      const detail = wrapper.get('[data-testid="legacy-history-detail"]');
      expect(detail.text()).toContain("Second record result");
      expect(detail.text()).not.toContain("First record result");
    } finally {
      wrapper.unmount();
    }
  });

  it.each(["list", "detail"])(
    "ignores a late refresh %s response after unmount",
    async (phase) => {
      const page = {
        records: [
          {
            source: { kind: "legacy_run", id: "unmount-run" },
            title: "Original",
            engine: "legacy",
            read_only: true,
            status: "running",
          },
        ],
        next_offset: null,
      };
      const detail = {
        source: { kind: "legacy_run", id: "unmount-run" },
        members: ["writer"],
        migrated_assets: [],
        run: {
          history_status: "running",
          execution_active: true,
          node_states: {},
          graph: {},
          rounds: [],
        },
      };
      api.history.mockResolvedValue(page);
      api.historyRecord.mockResolvedValue(detail);
      const host = document.createElement("div");
      document.body.appendChild(host);
      const wrapper = mount(UnifiedRunHistory, {
        attachTo: host,
        global: { stubs },
      });
      await flushPromises();
      await wrapper
        .get('[data-testid="history-open-legacy_run-unmount-run"]')
        .trigger("click");
      await flushPromises();
      let resolveLate:
        | ((value: typeof page | typeof detail) => void)
        | undefined;
      const pending = new Promise((resolve) => {
        resolveLate = resolve;
      });
      if (phase === "list") api.history.mockReturnValueOnce(pending);
      else api.historyRecord.mockReturnValueOnce(pending);
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "page.refresh")!
        .trigger("click");
      await flushPromises();
      expect(
        wrapper.find('[data-testid="legacy-history-detail"]').exists(),
      ).toBe(true);
      wrapper.unmount();
      resolveLate?.(phase === "list" ? page : detail);
      await flushPromises();
      expect(host.textContent).toBe("");
      expect(wrapper.emitted("openRun")).toBeUndefined();
      expect(wrapper.emitted("locateAsset")).toBeUndefined();
      expect(api.historyRecord).toHaveBeenCalledTimes(phase === "list" ? 1 : 2);
      host.remove();
    },
  );

  it("refreshes saved messages in the selected long-term conversation", async () => {
    api.history.mockResolvedValue({
      records: [
        {
          source: { kind: "legacy_conversation", id: "conversation" },
          engine: "legacy",
          read_only: true,
          title: "Saved conversation",
          status: "saved",
        },
      ],
      next_offset: null,
    });
    api.historyRecord.mockResolvedValue({
      source: { kind: "legacy_conversation", id: "conversation" },
      migrated_assets: [],
      source_links: [],
      conversation: {
        title: "Saved conversation",
        messages: [{ role: "user", content: "Original question" }],
        next_offset: null,
      },
    });
    const wrapper = mount(UnifiedRunHistory, { global: { stubs } });
    try {
      await flushPromises();
      await wrapper
        .get('[data-testid="history-open-legacy_conversation-conversation"]')
        .trigger("click");
      await flushPromises();
      api.historyRecord.mockResolvedValueOnce({
        source: { kind: "legacy_conversation", id: "conversation" },
        migrated_assets: [],
        source_links: [],
        conversation: {
          title: "Saved conversation",
          messages: [
            { role: "user", content: "Original question" },
            { role: "assistant", content: "Newly saved answer" },
          ],
          next_offset: null,
        },
      });
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "page.refresh")!
        .trigger("click");
      await flushPromises();
      const detail = wrapper.get('[data-testid="legacy-history-detail"]');
      expect(detail.text()).toContain("Original question");
      expect(detail.text()).toContain("Newly saved answer");
      expect(detail.find("input, textarea").exists()).toBe(false);
    } finally {
      wrapper.unmount();
    }
  });

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
