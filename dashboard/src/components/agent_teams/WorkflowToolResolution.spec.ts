import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import WorkflowToolResolution from "./WorkflowToolResolution.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

describe("tool resolution decision identity", () => {
  it.each([{}, undefined])(
    "submits and preserves an unchanged decision without secure-context crypto (%s)",
    async (cryptoValue) => {
      vi.stubGlobal("crypto", cryptoValue);
      const errors: unknown[] = [];
      const wrapper = mount(WorkflowToolResolution, {
        props: { actions: ["retry"], busy: false },
        global: {
          config: {
            errorHandler: (error) => {
              errors.push(error);
            },
          },
          stubs: {
            "v-alert": { template: "<div><slot /></div>" },
            "v-select": {
              props: ["modelValue", "items"],
              emits: ["update:modelValue"],
              template:
                '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="item in items" :value="item.value">{{ item.title }}</option></select>',
            },
            "v-textarea": {
              props: ["modelValue"],
              emits: ["update:modelValue"],
              template:
                '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
            },
            "v-btn": {
              props: ["disabled", "loading"],
              template:
                '<button :disabled="disabled || loading"><slot /></button>',
            },
          },
        },
      });
      try {
        await wrapper.get("select").setValue("retry");
        const evidence = wrapper.get('[data-testid="resolution-evidence"]');
        await evidence.setValue("Owner explicitly chose a new execution.");
        await wrapper.get("button").trigger("click");
        await wrapper.get("button").trigger("click");
        expect(errors).toEqual([]);
        const first = wrapper.emitted("submit")?.[0]?.[0] as {
          request_id: string;
        };
        const repeated = wrapper.emitted("submit")?.[1]?.[0] as {
          request_id: string;
        };
        expect(first).toEqual(
          expect.objectContaining({
            request_id: expect.any(String),
            action: "retry",
            evidence: "Owner explicitly chose a new execution.",
          }),
        );
        expect(first.request_id.length).toBeGreaterThan(0);
        expect(repeated.request_id).toBe(first.request_id);
        await evidence.setValue("Owner changed the decision evidence.");
        await wrapper.get("button").trigger("click");
        const changed = wrapper.emitted("submit")?.[2]?.[0] as {
          request_id: string;
        };
        expect(changed.request_id).not.toBe(first.request_id);
      } finally {
        wrapper.unmount();
        vi.unstubAllGlobals();
      }
    },
  );
});


it.each([false, true])("validates only structured component confirmations (%s)", async (structured) => {
  const outputContract = structured
    ? { type: "object", properties: { count: { type: "integer" } }, required: ["count"] }
    : undefined;
  const wrapper = mount(WorkflowToolResolution, {
    props: { actions: ["confirm_result"], busy: false, outputContract },
    global: {
      stubs: {
        "v-alert": { template: "<div><slot /></div>" },
        "v-select": true,
        "v-textarea": {
          props: ["modelValue", "label"],
          emits: ["update:modelValue"],
          template: `<textarea :aria-label="label" :value="modelValue" @input="$emit('update:modelValue', $event.target.value)" />`,
        },
        "v-btn": { props: ["disabled"], template: '<button :disabled="disabled"><slot /></button>' },
      },
    },
  });
  try {
    await wrapper.get('[data-testid="resolution-evidence"]').setValue("Independent receipt");
    const result = wrapper.get('[data-testid="resolution-result"]');
    const button = wrapper.get("button");
    await result.setValue("plain text");
    expect(button.attributes("disabled") !== undefined).toBe(structured);
    expect(wrapper.find("details").exists()).toBe(structured);
    if (structured) {
      expect(result.attributes("aria-label")).toBe("resolution.structuredResult");
      await result.setValue('[42]');
      expect(button.attributes("disabled")).toBeDefined();
      await result.setValue('{"count":42}');
    } else {
      expect(result.attributes("aria-label")).toBe("resolution.result");
    }
    await button.trigger("click");
    expect(wrapper.emitted("submit")?.[0]?.[0]).toMatchObject({
      action: "confirm_result", result: structured ? '{"count":42}' : "plain text",
    });
  } finally {
    wrapper.unmount();
  }
});
