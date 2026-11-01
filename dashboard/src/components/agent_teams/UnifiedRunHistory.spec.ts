import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import UnifiedRunHistory from "./UnifiedRunHistory.vue";

const api = vi.hoisted(() => ({ history: vi.fn() }));
vi.mock("@/api/workflowAssets", () => ({ workflowAssetsApi: api }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
const slots = { template: "<div><slot /></div>" };
const stubs = {
  "v-card": slots,
  "v-card-text": slots,
  "v-alert": slots,
  "v-btn": {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
};
const record = {
  source: { kind: "v2_run", id: "run-1" },
  title: "Research report",
  status: "completed",
  goal: "Research energy",
  created_at: "2026-11-01T10:00:00Z",
};

beforeEach(() => {
  vi.resetAllMocks();
  api.history.mockResolvedValue({ records: [record], next_offset: null });
});

describe("asset run history", () => {
  it("opens an existing run through its normal detail view", async () => {
    const wrapper = mount(UnifiedRunHistory, { global: { stubs } });
    try {
      await flushPromises();
      expect(wrapper.text()).toContain("Research report");
      expect(wrapper.text()).toContain("Research energy");
      await wrapper.get('[data-testid="history-open-run-1"]').trigger("click");
      expect(wrapper.emitted("openRun")).toEqual([["run-1"]]);
      expect(
        wrapper.find('[data-testid="legacy-history-detail"]').exists(),
      ).toBe(false);
    } finally {
      wrapper.unmount();
    }
  });

  it("appends history pages and refreshes from the first page", async () => {
    api.history.mockResolvedValueOnce({ records: [record], next_offset: 20 });
    const wrapper = mount(UnifiedRunHistory, { global: { stubs } });
    try {
      await flushPromises();
      api.history.mockResolvedValueOnce({
        records: [
          {
            ...record,
            source: { kind: "v2_run", id: "run-2" },
            title: "Older report",
          },
        ],
        next_offset: null,
      });
      await wrapper.get('[data-testid="history-next"]').trigger("click");
      await flushPromises();
      expect(api.history).toHaveBeenLastCalledWith(20);
      expect(wrapper.text()).toContain("Older report");
      expect(wrapper.text()).toContain("Research report");
      expect(wrapper.find('[data-testid="history-next"]').exists()).toBe(false);
      await wrapper.findAll("button")[0].trigger("click");
      await flushPromises();
      expect(api.history).toHaveBeenLastCalledWith(0);
      expect(wrapper.text()).not.toContain("Older report");
    } finally {
      wrapper.unmount();
    }
  });

  it("keeps the loaded records visible during a refresh", async () => {
    const wrapper = mount(UnifiedRunHistory, { global: { stubs } });
    let resolvePage!: (value: {
      records: (typeof record)[];
      next_offset: null;
    }) => void;
    try {
      await flushPromises();
      const card = wrapper.get('[data-testid="history-open-run-1"]').element;
      api.history.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolvePage = resolve;
          }),
      );
      await wrapper.findAll("button")[0].trigger("click");
      expect(wrapper.get('[data-testid="history-open-run-1"]').element).toBe(
        card,
      );
      resolvePage({ records: [record], next_offset: null });
      await flushPromises();
      expect(wrapper.get('[data-testid="history-open-run-1"]').element).toBe(
        card,
      );
    } finally {
      wrapper.unmount();
    }
  });

  it("shows a request error without offering legacy controls", async () => {
    api.history.mockRejectedValueOnce(new Error("History unavailable"));
    const wrapper = mount(UnifiedRunHistory, { global: { stubs } });
    try {
      await flushPromises();
      expect(wrapper.get('[role="alert"]').text()).toContain(
        "History unavailable",
      );
      expect(wrapper.find('[data-testid="history-next"]').exists()).toBe(false);
    } finally {
      wrapper.unmount();
    }
  });
});
