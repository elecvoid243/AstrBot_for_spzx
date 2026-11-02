<template>
  <section
    v-if="run.loops?.length"
    class="mt-5"
    :aria-label="tm('loops.history')"
  >
    <h3 class="text-h6 mb-3">{{ tm("loops.history") }}</h3>
    <details
      v-for="loop in run.loops"
      :key="loop.loop_id"
      class="loop-history"
      open
    >
      <summary>
        {{
          run.snapshot.definition.loops?.find(
            (item: any) => item.id === loop.loop_id,
          )?.name ?? loop.loop_id
        }}
        · {{ tm(`loops.states.${loop.status}`) }}
      </summary>
      <p v-if="loop.reason" class="text-body-2 mt-2">{{ loop.reason }}</p>
      <details
        v-for="iteration in loop.iterations"
        :key="iteration.iteration"
        class="my-3"
      >
        <summary>
          {{ tm("loops.iteration") }} {{ iteration.iteration }} ·
          {{ tm(`loops.states.${iteration.status}`) }}
        </summary>
        <p v-if="iteration.exit_reference" class="text-caption mt-2">
          {{ tm("loops.exitResult") }}: {{ String(iteration.exit_value) }} ·
          {{ iteration.exit_reference.artifact_id }} / v{{
            iteration.exit_reference.version
          }}
        </p>
        <article
          v-for="task in iteration.tasks"
          :key="task.run_id"
          class="loop-task"
        >
          <button
            type="button"
            class="loop-task-link"
            :data-testid="`iteration-task-${task.run_id}`"
            @click="$emit('select', task.run_id)"
          >
            <strong
              >{{ task.snapshot.definition.name }} ·
              {{ tm(`v2.states.${task.status}`) }}</strong
            >
          </button>
          <p>{{ tm("v2.attempt") }} {{ task.state.attempt_id }}</p>
          <p>
            {{ tm("loops.usage") }}:
            {{
              task.usage?.model_calls ??
              task.recovery?.model_calls ??
              tm("loops.unknown")
            }}
          </p>
          <div v-if="taskInputs[task.run_id]?.length" class="mt-2">
            <strong>{{ tm("graph.inputs") }}</strong>
            <dl
              class="loop-values"
              :data-testid="`iteration-inputs-${task.run_id}`"
            >
              <template
                v-for="[name, value] in taskInputs[task.run_id]"
                :key="name"
              >
                <dt>{{ name }}</dt>
                <dd>
                  {{
                    value !== null && typeof value === "object"
                      ? tm("loops.structuredValue")
                      : String(value)
                  }}
                </dd>
              </template>
            </dl>
          </div>
          <div
            v-if="task.result != null"
            class="mt-2"
            :data-testid="`iteration-result-${task.run_id}`"
          >
            <strong>{{ tm("v2.result") }}</strong>
            <MarkdownMessagePart
              v-if="typeof task.result.text === 'string'"
              :content="task.result.text"
              :refs="null"
              :is-dark="isDark"
              :custom-html-tags="CHAT_MARKDOWN_CUSTOM_TAGS"
            />
            <dl v-if="typeof task.result === 'object'" class="loop-values">
              <template v-for="(value, name) in task.result" :key="name">
                <template v-if="String(name) !== 'text' || typeof value !== 'string'">
                  <dt>{{ name }}</dt>
                  <dd>
                    {{
                      value !== null && typeof value === "object"
                        ? tm("loops.structuredValue")
                        : String(value)
                    }}
                  </dd>
                </template>
              </template>
            </dl>
            <p v-else>{{ String(task.result) }}</p>
          </div>
          <button
            v-if="task.result_ref"
            type="button"
            class="loop-task-link"
            @click="$emit('select', task.run_id)"
          >
            {{ tm("loops.openArtifact") }} · {{ task.result_ref.artifact_id }} /
            v{{ task.result_ref.version }}
          </button>
          <details class="mt-2">
            <summary>{{ tm("loops.rawDetails") }}</summary>
            <pre class="loop-raw">{{
              JSON.stringify(
                {
                  inputs: task.snapshot.inputs,
                  iteration_inputs: task.snapshot.iteration_inputs,
                  result: task.result,
                },
                null,
                2,
              )
            }}</pre>
          </details>
        </article>
        <details class="mt-2">
          <summary>{{ tm("progress.graph") }}</summary>
          <WorkflowGraphCanvas
            :definition="{
              ...run.snapshot.definition,
              tasks: run.snapshot.definition.tasks.filter(
                (task: any) => iteration.task_runs[task.id],
              ),
              edges: run.snapshot.definition.edges.filter(
                (edge: any) =>
                  iteration.task_runs[edge.source] &&
                  iteration.task_runs[edge.target],
              ),
              loops: [],
            }"
            :tasks="iteration.tasks"
            readonly
            @select-task="$emit('select', iteration.task_runs[$event!])"
          />
        </details>
      </details>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import MarkdownMessagePart from "@/components/chat/message_list_comps/MarkdownMessagePart.vue";
import { CHAT_MARKDOWN_CUSTOM_TAGS } from "@/components/chat/chatMarkdownComponents";
import { useCustomizerStore } from "@/stores/customizer";
import { useModuleI18n } from "@/i18n/composables";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";
const props = defineProps<{ run: any }>();
defineEmits<{ select: [taskRunId: string] }>();
const { tm } = useModuleI18n("features/agent-teams");
const customizer = useCustomizerStore();
const isDark = computed(() => customizer.isDark);
const taskInputs = computed<Record<string, [string, unknown][]>>(() => {
  const values: Record<string, [string, unknown][]> = {};
  for (const loop of props.run.loops ?? []) {
    for (const iteration of loop.iterations) {
      for (const task of iteration.tasks) {
        if (
          ["pending", "skipped", "dependency_failed"].includes(task.status) ||
          (task.status === "cancelled" && !task.state.execution_id)
        ) {
          values[task.run_id] = [];
          continue;
        }
        const inputs = { ...task.snapshot.inputs };
        for (const [name, input] of Object.entries(
          task.snapshot.iteration_inputs ?? {},
        )) {
          inputs[name] = (input as { raw_value: unknown }).raw_value;
        }
        values[task.run_id] = Object.entries(inputs);
      }
    }
  }
  return values;
});
</script>

<style scoped>
.loop-history {
  padding: 12px;
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  border-radius: 12px;
  margin-bottom: 12px;
  overflow-wrap: anywhere;
}
summary {
  cursor: pointer;
  font-weight: 600;
}
.loop-task {
  box-sizing: border-box;
  max-width: 100%;
  display: block;
  padding: 12px;
  margin-top: 8px;
  text-align: left;
  width: 100%;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 8px;
}
.loop-task p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
  margin-top: 6px;
}
.loop-values {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 4px 12px;
  margin-top: 6px;
  font-size: 12px;
}
.loop-values dd,
.loop-raw {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.loop-task-link {
  text-align: left;
  color: rgb(var(--v-theme-primary));
  text-decoration: underline;
}
.loop-task-link:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
}
</style>
