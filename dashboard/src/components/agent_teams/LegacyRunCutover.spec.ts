import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LegacyRunCutover from "./LegacyRunCutover.vue";

const api = vi.hoisted(() => ({
  listActiveRuns: vi.fn(),
  stopRun: vi.fn(),
  resumeRun: vi.fn(),
}));
vi.mock("@/api/v1", () => ({ agentTeamsApi: api }));
const original = {
  run_id: "original-run",
  team_id: "original-team",
  status: "paused",
  input: "Original goal",
};
let wrapper: ReturnType<typeof mount>;

function openCutover(confirm = vi.fn().mockResolvedValue(true)) {
  wrapper = mount(LegacyRunCutover, {
    props: { teams: [{ team_id: "original-team", name: "Original team" }] },
    global: {
      provide: { $confirm: confirm },
      stubs: {
        "v-alert": { template: "<div><slot /></div>" },
        "v-btn": {
          props: ["disabled"],
          emits: ["click"],
          template:
            '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
        },
      },
    },
  });
  return wrapper;
}
beforeEach(() => {
  vi.clearAllMocks();
  api.listActiveRuns.mockResolvedValue({
    data: { status: "ok", data: { runs: [original] } },
  });
  api.stopRun.mockResolvedValue({ data: { status: "ok" } });
  api.resumeRun.mockResolvedValue({ data: { status: "ok" } });
});
afterEach(() => wrapper?.unmount());

describe("LegacyRunCutover", () => {
  it("lists a normal owned pause and blocks switching without stopping it", async () => {
    const page = openCutover();
    await flushPromises();
    expect(page.text()).toContain("Original team");
    expect(page.text()).toContain("original-run");
    expect(page.text()).toContain("Original goal");
    expect(page.emitted("ready")?.at(-1)).toEqual([false]);
    expect(api.stopRun).not.toHaveBeenCalled();
    expect(api.resumeRun).not.toHaveBeenCalled();
  });
  it("keeps the original run when the owner cancels the stop confirmation", async () => {
    const confirm = vi.fn().mockResolvedValue(false);
    const page = openCutover(confirm);
    await flushPromises();
    await page
      .findAll("button")
      .find((button) => button.text() === "停止原运行")!
      .trigger("click");
    await flushPromises();
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(api.stopRun).not.toHaveBeenCalled();
    expect(page.emitted("ready")?.at(-1)).toEqual([false]);
  });
  it("enables new runs only after explicit stopping and an empty server list", async () => {
    const page = openCutover();
    await flushPromises();
    api.listActiveRuns.mockResolvedValue({
      data: { status: "ok", data: { runs: [] } },
    });
    await page
      .findAll("button")
      .find((button) => button.text() === "停止原运行")!
      .trigger("click");
    await flushPromises();
    expect(api.stopRun).toHaveBeenCalledWith("original-run");
    expect(page.emitted("ready")?.at(-1)).toEqual([true]);
  });
  it("keeps switching blocked when the active-run list cannot be read", async () => {
    api.listActiveRuns.mockRejectedValue(
      new Error("Original run list unavailable"),
    );
    const page = openCutover();
    await flushPromises();
    expect(page.text()).toContain("Original run list unavailable");
    expect(page.emitted("ready")?.at(-1)).toEqual([false]);
    expect(api.stopRun).not.toHaveBeenCalled();
  });
});
