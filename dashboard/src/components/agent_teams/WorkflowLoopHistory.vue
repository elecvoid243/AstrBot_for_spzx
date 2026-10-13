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
          {{ tm("loops.exitResult") }}:
          {{ JSON.stringify(iteration.exit_value) }} ·
          {{ iteration.exit_reference.artifact_id }} / v{{
            iteration.exit_reference.version
          }}
        </p>
        <button
          v-for="task in iteration.tasks"
          :key="task.run_id"
          type="button"
          class="loop-task"
          :data-testid="`iteration-task-${task.run_id}`"
          @click="$emit('select', task.run_id)"
        >
          <strong
            >{{ task.snapshot.definition.name }} ·
            {{ tm(`v2.states.${task.status}`) }}</strong
          >
          <p>{{ tm("v2.attempt") }} {{ task.state.attempt_id }}</p>
          <p>
            {{ tm("loops.usage") }}:
            {{ task.recovery?.model_calls ?? tm("loops.unknown") }}
          </p>
          <p>
            {{ tm("graph.inputs") }}: {{ JSON.stringify(task.snapshot.inputs) }}
          </p>
          <p>{{ tm("v2.result") }}: {{ JSON.stringify(task.result) }}</p>
          <p v-if="task.result_ref">
            {{ task.result_ref.artifact_id }} / v{{ task.result_ref.version }}
          </p>
        </button>
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
import { useModuleI18n } from "@/i18n/composables";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";
defineProps<{ run: any }>();
defineEmits<{ select: [taskRunId: string] }>();
const { tm } = useModuleI18n("features/agent-teams");
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
.loop-task:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
}
</style>
