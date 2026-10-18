<template>
  <div class="run-overview">
    <div class="d-flex flex-wrap align-start ga-3 mb-5">
      <div class="run-name flex-grow-1">
        <h2 class="text-h5 font-weight-bold">
          {{ run.snapshot.definition.name }}
        </h2>
        <p class="text-body-2 text-medium-emphasis mt-2">
          {{ run.run_id }} · v{{ run.snapshot.asset_version }} ·
          {{ run.snapshot.source }}
        </p>
        <p class="run-text mt-3" data-testid="run-goal">
          {{
            run.snapshot.task ??
            Object.values(run.snapshot.inputs ?? {}).join(" · ")
          }}
        </p>
        <p v-if="run.usage" class="text-caption mt-2">
          {{
            tm(
              run.usage.count_basis === "checkpoint"
                ? "usage.historicalModelCalls"
                : "child.modelCalls",
            )
          }}: {{ run.usage.model_calls ?? tm("child.unknownUsage") }} ·
          {{
            tm(
              run.usage.count_basis === "checkpoint"
                ? "usage.historicalSteps"
                : "child.steps",
            )
          }}:
          {{ run.usage.steps_used ?? tm("child.unknownUsage") }}
        </p>
      </div>
      <v-chip
        variant="tonal"
        :color="
          run.status === 'completed'
            ? 'success'
            : run.status === 'running'
            ? 'primary'
            : 'warning'
        "
        >{{ tm(`v2.states.${run.status}`) }}</v-chip
      >
    </div>
    <div class="run-columns">
      <v-card variant="outlined" class="run-progress">
        <v-card-title
          class="d-flex flex-wrap align-center ga-2 text-subtitle-1"
        >
          {{ tm("progress.title") }}
          <v-spacer />
          <span class="text-body-2"
            >{{ progress.completed }} / {{ progress.total }}</span
          >
        </v-card-title>
        <v-progress-linear
          :model-value="
            progress.total ? (progress.completed / progress.total) * 100 : 0
          "
          color="primary"
          :aria-label="tm('progress.title')"
        />
        <v-card-text class="pa-0">
          <button
            v-for="task in tasks"
            :key="task.run_id"
            class="run-task"
            :class="{
              'run-task-selected':
                task.node_id === selectedTask || task.run_id === selectedTask,
            }"
            @click="
              $emit(
                'select',
                task.snapshot.parent_run_id !== run.run_id
                  ? task.run_id
                  : task.node_id ?? null,
              )
            "
          >
            <v-icon
              :icon="
                task.status === 'completed'
                  ? 'mdi-check-circle-outline'
                  : task.status === 'running'
                  ? 'mdi-progress-clock'
                  : task.status === 'skipped'
                  ? 'mdi-skip-next-circle-outline'
                  : 'mdi-clock-outline'
              "
              size="22"
            />
            <div class="run-name flex-grow-1 text-left">
              <strong>{{ task.snapshot.definition.name }}</strong>
              <p class="text-caption text-medium-emphasis mt-1">
                {{ task.snapshot.member?.name }} · {{ tm("v2.attempt") }}
                {{ task.state.attempt_id }}
              </p>
              <p v-if="task.state.reason" class="run-text text-body-2 mt-2">
                {{ task.state.reason }}
              </p>
              <p v-else class="run-text text-body-2 text-medium-emphasis mt-2">
                {{ task.snapshot.task }}
              </p>
            </div>
            <span class="text-caption run-status">{{
              tm(
                task.status === "running" && task.state.capacity_released
                  ? "autonomous.waiting"
                  : `v2.states.${task.status}`,
              )
            }}</span>
          </button>
        </v-card-text>
      </v-card>
      <aside class="run-aside">
        <v-card
          variant="outlined"
          class="mb-4"
          :class="{ 'run-attention': waiting.length }"
        >
          <v-card-title class="text-subtitle-1 text-wrap">{{
            tm("progress.attention")
          }}</v-card-title>
          <v-card-text>
            <p v-if="!waiting.length" class="text-medium-emphasis">
              {{ tm("progress.noAttention") }}
            </p>
            <button
              v-for="task in waiting"
              :key="task.run_id"
              class="run-waiting run-text text-left"
              @click="
                $emit(
                  'select',
                  task.snapshot.parent_run_id !== run.run_id
                    ? task.run_id
                    : task.node_id ?? null,
                )
              "
            >
              <strong>{{ task.snapshot.definition.name }}</strong>
              <p class="mt-1">
                {{ task.state.reason ?? tm(`v2.states.${task.status}`) }}
              </p>
            </button>
          </v-card-text>
        </v-card>
        <v-card variant="outlined">
          <v-card-title class="text-subtitle-1 text-wrap">{{
            tm("progress.members")
          }}</v-card-title>
          <v-card-text>
            <div
              v-for="member in members"
              :key="member.member_id"
              class="mb-3 run-text"
            >
              <strong>{{ member.name }}</strong>
              <p class="text-caption text-medium-emphasis">
                {{ tm("v2.member") }} · {{ member.member_id }}
              </p>
            </div>
            <p class="text-caption text-medium-emphasis">
              {{ tm("progress.context") }}
            </p>
          </v-card-text>
        </v-card>
      </aside>
    </div>
    <details class="mt-4" :open="!!run.usage?.stops?.length">
      <summary>{{ tm("usage.title") }}</summary>
      <RunUsagePanel :run="run" />
    </details>
    <WorkflowLoopHistory :run="run" @select="$emit('select', $event)" />
    <details
      v-for="scope in run.scopes ?? []"
      :key="scope.run_id"
      class="child-scope mt-4 pa-3"
      data-testid="child-scope"
    >
      <summary class="run-text">
        {{ tm("child.scope") }}:
        {{
          run.tasks.find(
            (task: any) => task.run_id === scope.snapshot.parent_run_id,
          )?.snapshot.definition.name
        }}
        → {{ scope.snapshot.definition.name }} · v{{
          scope.snapshot.asset_version
        }}
        · {{ tm(`v2.states.${scope.status}`) }}
      </summary>
      <p class="run-text text-caption my-3">
        {{ tm("child.root") }}: {{ scope.control.root_run_id }} ·
        {{ tm("child.scope") }}: {{ scope.state.scope_id }}
      </p>
      <p v-if="scope.state.reason" class="run-text mb-3">
        {{ scope.state.reason }}
      </p>
      <WorkflowGraphCanvas
        :definition="scope.runtime_definition ?? scope.snapshot.definition"
        :tasks="scope.tasks"
        :dispatch-tasks="
          scope.tasks
            .filter((task: any) => task.snapshot.created_by_task_id)
            .map((task: any) => task.node_id)
        "
        readonly
        @select-task="
          $emit(
            'select',
            scope.tasks.find((task: any) => task.node_id === $event)?.run_id ??
              null,
          )
        "
      />
    </details>
    <details v-if="run.snapshot.definition.tasks" class="mt-4">
      <summary>{{ tm("progress.graph") }}</summary>
      <WorkflowGraphCanvas
        :definition="run.runtime_definition ?? run.snapshot.definition"
        :tasks="run.tasks"
        :child-assets="run.snapshot.child_assets ?? {}"
        :dispatch-tasks="
          run.snapshot.team_definition
            ? run.tasks
                .filter((task: any) => task.snapshot.created_by_task_id)
                .map((task: any) => task.node_id)
            : []
        "
        readonly
        class="mt-3"
        @select-task="$emit('select', $event)"
      />
    </details>
  </div>
</template>

<script setup lang="ts">
import RunUsagePanel from "./RunUsagePanel.vue";
import { computed } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";
import WorkflowLoopHistory from "./WorkflowLoopHistory.vue";
const props = defineProps<{ run: any; selectedTask: string | null }>();
defineEmits<{ select: [nodeId: string | null] }>();
const { tm } = useModuleI18n("features/agent-teams");
const tasks = computed<any[]>(() => [
  ...(props.run.tasks ?? [props.run]),
  ...(props.run.scopes ?? []).flatMap((scope: any) => scope.tasks),
]);
const waiting = computed(() =>
  tasks.value.filter(
    (task) =>
      !["pending", "running", "completed", "skipped"].includes(task.status) ||
      (task.status === "running" && task.state.capacity_released),
  ),
);
const progress = computed(
  () =>
    props.run.progress ?? {
      total: tasks.value.length,
      completed: tasks.value.filter((task) =>
        ["completed", "skipped"].includes(task.status),
      ).length,
    },
);
const members = computed(() =>
  props.run.members
    ? Object.values(props.run.members)
    : [
        ...new Map(
          tasks.value
            .filter((task) => task.snapshot.member)
            .map((task) => [
              task.snapshot.member.member_id,
              task.snapshot.member,
            ]),
        ).values(),
      ],
);
</script>

<style scoped>
.child-scope {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 10px;
}
.run-name {
  min-width: 0;
  overflow-wrap: anywhere;
}
.run-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.run-columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(240px, 30%);
  gap: 20px;
}
.run-progress,
.run-aside {
  min-width: 0;
}
.run-attention {
  border-color: rgba(var(--v-theme-warning), 0.55);
}
.run-task {
  cursor: pointer;
  display: flex;
  align-items: flex-start;
  gap: 14px;
  width: 100%;
  padding: 20px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  color: rgb(var(--v-theme-on-surface));
}
.run-task:last-child {
  border-bottom: 0;
}
.run-task-selected {
  background: rgba(var(--v-theme-primary), 0.07);
}
.run-task:focus-visible,
.run-waiting:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}
.run-status {
  max-width: 25%;
  flex-shrink: 0;
}
.run-waiting {
  width: 100%;
  margin-bottom: 12px;
}
summary {
  cursor: pointer;
  font-weight: 600;
  padding: 12px 0;
}
@media (max-width: 900px) {
  .run-columns {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 500px) {
  .run-task {
    padding: 14px;
    gap: 10px;
    flex-wrap: wrap;
  }
  .run-task .run-name {
    width: calc(100% - 40px);
  }
  .run-status {
    max-width: 100%;
    margin-left: 32px;
  }
}
</style>
