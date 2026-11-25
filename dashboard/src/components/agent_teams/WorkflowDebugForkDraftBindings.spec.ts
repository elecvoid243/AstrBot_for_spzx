import AxiosMockAdapter from "axios-mock-adapter";
import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import type { WorkflowDebugForkAttempt, WorkflowDebugForkBindings } from "@/api/generated/openapi-v1";
import { httpClient } from "@/api/http";
import { workflowAssetsApi } from "@/api/workflowAssets";
import WorkflowDebugForkDraftBindings from "./WorkflowDebugForkDraftBindings.vue";

it.each(["constructor", "__proto__", "__v_raw"])("preserves a literal draft port named %s", async (name) => {
  const editor = mount(WorkflowDebugForkDraftBindings, {
    props: {
      disabled: false,
      inputs: [{ name, kind: "text", source: "absent", changed: false, available_sources: ["previous"] }],
    },
    global: {
      stubs: {
        VTextarea: true,
        VSelect: {
          props: ["modelValue", "items"],
          emits: ["update:modelValue"],
          template: `<select :value="modelValue" @change="$emit('update:modelValue', $event.target.value)">
            <option v-for="item in items" :key="item.value" :value="item.value">{{ item.title }}</option>
          </select>`,
        },
      },
    },
  });
  const mock = new AxiosMockAdapter(httpClient);
  mock.onAny().reply(200, { data: {} });
  try {
    await editor.get("select").setValue("source:previous");
    const bindings = editor.emitted<[WorkflowDebugForkBindings]>("update")?.at(-1)?.[0];
    expect(Object.keys(bindings ?? {})).toEqual([name]);
    expect(bindings?.[name]).toEqual({ source_port: "previous" });

    const attempt: WorkflowDebugForkAttempt = {
      scope_run_id: "root", task_run_id: "task", attempt_id: "attempt",
      definition_mode: "draft", draft_revision: 2, bindings,
    };
    await workflowAssetsApi.previewDebugFork("root", attempt, new AbortController().signal);
    await workflowAssetsApi.startDebugFork("root", {
      ...attempt, preview_token: "preview", request_id: "request",
    });
    expect(mock.history.post).toHaveLength(2);
    for (const request of mock.history.post) {
      const sent = JSON.parse(request.data).bindings;
      expect(Object.keys(sent)).toEqual([name]);
      expect(sent[name]).toEqual({ source_port: "previous" });
    }
  } finally {
    mock.restore();
    editor.unmount();
  }
});
