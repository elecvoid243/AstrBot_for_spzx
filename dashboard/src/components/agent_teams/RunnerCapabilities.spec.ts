import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import RunnerCapabilities from "./RunnerCapabilities.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

describe("task executor capabilities", () => {
  it.each([false, true])(
    "explains task readiness when result support is %s",
    (supported) => {
      const panel = mount(RunnerCapabilities, {
        props: {
          runner: {
            label: "Remote",
            recovery: "remote_query",
            capabilities: supported ? ["task_result"] : ["remote_query"],
          },
        },
        global: { stubs: { "v-chip": { template: "<span><slot /></span>" } } },
      });
      expect(panel.text().includes("runner.taskResultRequired")).toBe(
        !supported,
      );
      panel.unmount();
    },
  );
});
