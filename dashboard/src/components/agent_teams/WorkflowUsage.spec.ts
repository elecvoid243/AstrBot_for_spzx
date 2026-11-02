import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import RunLimitsForm from "./RunLimitsForm.vue";
import RunUsagePanel from "./RunUsagePanel.vue";

const historyApi = vi.hoisted(() => vi.fn());
vi.mock("@/api/workflowAssets", () => ({ workflowAssetsApi: { runHistoryPage: historyApi } }));

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

it("edits a shared root allowance and rejects fractional limits", async () => {
  const wrapper = mount(RunLimitsForm, {
    props: { modelValue: { token_budget: 100, max_steps: 2 } },
    global: {
      stubs: {
        "v-text-field": {
          props: ["modelValue"],
          emits: ["update:modelValue"],
          template: `<input :value="modelValue" @input="$emit('update:modelValue', $event.target.value)" />`,
        },
      },
    },
  });
  await wrapper.find('[data-testid="limit-max_steps"]').setValue("1.5");
  expect(wrapper.emitted("validation")?.at(-1)).toEqual([true]);
  await wrapper.setProps({ modelValue: { token_budget: 100, max_steps: 1.5 } });
  await wrapper.find('[data-testid="limit-max_steps"]').setValue("4");
  expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([
    { token_budget: 100, max_steps: 4 },
  ]);
  expect(wrapper.emitted("validation")?.at(-1)).toEqual([false]);
});

it("shows unknown consumption, a limit stop and original call identities", () => {
  const wrapper = mount(RunUsagePanel, {
    props: {
      run: {
        run_id: "root",
        control: { root_run_id: "root" },
        state: { limit_stop: { limit: "token_budget", value: 100 } },
        usage: {
          model_calls: 1,
          steps_used: 2,
          limits: { token_budget: 100 },
          tokens: {
            reported: null,
            estimated: 12,
            reserved: 0,
            unknown_calls: 1,
          },
          ledger: [
            {
              receipt_id: "receipt",
              invocation_id: "original-call",
              attempt_id: "old-attempt",
              run_id: "child-task",
              scope_id: "child-scope",
              kind: "model",
              quality: "unknown",
              reported_tokens: null,
              estimated_tokens: 12,
            },
          ],
        },
      },
    },
  });
  expect(wrapper.find('[data-testid="reported-tokens"]').text()).toContain(
    "usage.unknown",
  );
  expect(wrapper.text()).toContain("usage.token_budget");
  expect(wrapper.text()).toContain("original-call");
  expect(wrapper.text()).toContain("old-attempt");
  expect(wrapper.text()).toContain("child-scope");
  expect(wrapper.text()).toContain("usage.boundary");
});

it("keeps historical uncertainty beside newly reported consumption", () => {
  const wrapper = mount(RunUsagePanel, {
    props: {
      run: {
        usage: {
          steps_used: 1,
          tokens: { reported: 12, unknown_calls: null },
          historical: { unmetered: true, model_responses: 3 },
        },
      },
    },
  });
  expect(wrapper.find('[data-testid="reported-tokens"]').text()).toContain(
    "12",
  );
  expect(wrapper.find('[data-testid="historical-usage"]').text()).toContain(
    "usage.historicalUnknown",
  );
  expect(wrapper.find('[data-testid="historical-usage"]').text()).toContain(
    "3",
  );
  expect(wrapper.text()).toContain("usage.unknownCalls: usage.unknown");
  expect(wrapper.text()).toContain("usage.calls: 1");
});


it("pages prior usage without changing the accurate summary", async () => {
  historyApi.mockResolvedValue({items:[{receipt_id:"old", created_at:"2026-01-01", kind:"model", quality:"reported", reported_tokens:3}], next_before:null});
  const wrapper = mount(RunUsagePanel, {props:{run:{run_id:"task", control:{root_run_id:"root"}, usage:{tokens:{reported:100}, ledger_before:"boundary", ledger:[{receipt_id:"new",created_at:"2026-01-02",kind:"model",quality:"reported",reported_tokens:5}]}}},global:{stubs:{"v-btn":{template:"<button><slot /></button>"}}}});
  await wrapper.find("button").trigger("click");
  await flushPromises();
  expect(historyApi).toHaveBeenCalledWith("root", "task", "usage", "boundary");
  expect(wrapper.find('[data-testid="reported-tokens"]').text()).toContain("100");
  expect(wrapper.text()).toContain("old");
  expect(wrapper.text()).toContain("new");
  expect(wrapper.find("button").exists()).toBe(false);
});
