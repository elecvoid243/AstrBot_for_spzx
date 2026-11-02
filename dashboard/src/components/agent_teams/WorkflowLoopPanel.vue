<template>
  <div class="loop-panel">
    <v-btn
      v-if="!readonly"
      variant="tonal"
      size="small"
      data-testid="add-loop"
      @click="addLoop"
      >{{ tm("loops.add") }}</v-btn
    >
    <details
      v-for="loop in definition.loops ?? []"
      :key="loop.id"
      class="loop-group"
      :open="
        newLoopId === loop.id ||
        issues.some(
          (issue) =>
            issue.path ===
              `definition.loops.${definition.loops?.indexOf(loop)}` ||
            issue.path.startsWith(
              `definition.loops.${definition.loops?.indexOf(loop)}.`,
            ),
        )
          ? true
          : undefined
      "
    >
      <summary>
        {{ loop.name }} · {{ tm("loops.maximum") }} {{ loop.max_iterations }} ·
        {{ loop.body.length }} {{ tm("loops.tasks") }} · {{ tm("loops.edit") }}
        <span class="loop-summary-detail">
          {{ tm("loops.exitTask") }}:
          {{
            taskItems.find((task) => task.value === loop.exit_task_id)?.title ??
            loop.exit_task_id
          }}.{{ loop.exit_condition.field }}
          {{ tm(`graph.operators.${loop.exit_condition.operator ?? "eq"}`) }}
          {{ JSON.stringify(loop.exit_condition.value) }}
          <span v-for="(feedback, index) in loop.feedback ?? []" :key="index">
            ·
            {{
              taskItems.find((task) => task.value === feedback.source)?.title ??
              feedback.source
            }}.{{ feedback.field }} →
            {{
              taskItems.find((task) => task.value === feedback.target)?.title ??
              feedback.target
            }}.{{ feedback.input }}
          </span>
        </span>
      </summary>
      <v-alert
        v-for="issue in issues.filter(
          (item) =>
            item.path ===
              `definition.loops.${definition.loops?.indexOf(loop)}` ||
            item.path.startsWith(
              `definition.loops.${definition.loops?.indexOf(loop)}.`,
            ),
        )"
        :key="`${issue.path}:${issue.message}`"
        type="warning"
        variant="tonal"
        :data-issue-path="issue.path"
        >{{ issue.path }}: {{ issue.message }}</v-alert
      >
      <v-btn
        variant="text"
        size="small"
        :data-testid="`collapse-loop-${loop.id}`"
        :aria-expanded="!collapsed.includes(loop.id)"
        @click="$emit('toggle', loop.id)"
        >{{
          tm(collapsed.includes(loop.id) ? "loops.expand" : "loops.collapse")
        }}</v-btn
      >
      <template v-if="!readonly">
        <div class="loop-fields">
          <v-text-field
            density="compact"
            :model-value="loop.name"
            :label="tm('v2.name')"
            @update:model-value="change(loop.id, { name: $event })"
          />
          <v-select
            density="compact"
            :model-value="loop.body"
            :items="taskItems"
            multiple
            :label="tm('loops.body')"
            data-testid="loop-body"
            @update:model-value="change(loop.id, { body: $event })"
          />
          <v-select
            density="compact"
            :model-value="loop.exit_task_id"
            :items="
              artifactTaskItems.filter((task) => loop.body.includes(task.value))
            "
            :label="tm('loops.exitTask')"
            @update:model-value="changeExitTask(loop.id, $event)"
          />
        </div>
        <div class="loop-fields">
          <v-select
            density="compact"
            :items="fieldsForTask(loop.exit_task_id)"
            :model-value="loop.exit_condition.field"
            :label="tm('graph.field')"
            @update:model-value="setExitField(loop.id, $event)"
          />
          <v-select
            density="compact"
            :model-value="loop.exit_condition.operator ?? 'eq'"
            :items="
              ['integer', 'number'].includes(exitType(loop))
                ? ['eq', 'ne', 'gt', 'ge', 'lt', 'le'].map((value) => ({
                    title: tm(`graph.operators.${value}`),
                    value,
                  }))
                : ['eq', 'ne'].map((value) => ({
                    title: tm(`graph.operators.${value}`),
                    value,
                  }))
            "
            :label="tm('graph.operator')"
            @update:model-value="
              change(loop.id, {
                exit_condition: { ...loop.exit_condition, operator: $event },
              })
            "
          />
          <v-select
            v-if="exitType(loop) === 'boolean'"
            density="compact"
            :model-value="loop.exit_condition.value"
            :items="[
              { title: 'true', value: true },
              { title: 'false', value: false },
            ]"
            :label="tm('graph.value')"
            @update:model-value="
              change(loop.id, {
                exit_condition: { ...loop.exit_condition, value: $event },
              })
            "
          />
          <v-text-field
            v-else-if="exitType(loop) !== 'null'"
            density="compact"
            :type="
              ['integer', 'number'].includes(exitType(loop)) ? 'number' : 'text'
            "
            :model-value="loop.exit_condition.value"
            :label="tm('graph.value')"
            @update:model-value="setValue(loop.id, $event)"
          />
          <v-text-field
            density="compact"
            :model-value="loop.max_iterations"
            type="number"
            min="1"
            max="100"
            :label="tm('loops.maximum')"
            data-testid="loop-maximum"
            @update:model-value="
              change(loop.id, { max_iterations: Number($event) })
            "
          />
        </div>
        <p class="text-caption my-3">{{ tm("loops.feedbackHint") }}</p>
        <div
          v-for="(feedback, index) in loop.feedback ?? []"
          :key="index"
          class="loop-fields"
        >
          <v-select
            density="compact"
            :model-value="feedback.target"
            :items="taskItems.filter((task) => loop.body.includes(task.value))"
            :label="tm('loops.target')"
            @update:model-value="
              setFeedback(loop.id, index, { target: $event })
            "
          />
          <v-select
            density="compact"
            :model-value="feedback.input"
            :items="
              Object.keys(
                definition.tasks.find((task) => task.id === feedback.target)
                  ?.inputs ?? {},
              )
            "
            :label="tm('graph.inputAlias')"
            @update:model-value="setFeedback(loop.id, index, { input: $event })"
          />
          <v-select
            density="compact"
            :model-value="feedback.source"
            :items="
              artifactTaskItems.filter((task) => loop.body.includes(task.value))
            "
            :label="tm('loops.source')"
            @update:model-value="
              setFeedback(loop.id, index, {
                source: $event,
                field: fieldsForTask($event)[0]?.value ?? '',
              })
            "
          />
          <v-select
            density="compact"
            :items="fieldsForTask(feedback.source)"
            :model-value="feedback.field ?? 'text'"
            :label="tm('graph.field')"
            @update:model-value="setFeedback(loop.id, index, { field: $event })"
          />
          <v-btn
            variant="text"
            @click="
              change(loop.id, {
                feedback: loop.feedback?.filter((_, i) => i !== index),
              })
            "
            >{{ tm("graph.removeInput") }}</v-btn
          >
        </div>
        <v-btn
          variant="text"
          size="small"
          @click="
            change(loop.id, {
              feedback: [
                ...(loop.feedback ?? []),
                {
                  target: loop.body[0] ?? '',
                  input: '',
                  source: loop.exit_task_id,
                  field: 'text',
                },
              ],
            })
          "
          >{{ tm("loops.addFeedback") }}</v-btn
        >
        <v-btn
          variant="text"
          size="small"
          color="error"
          @click="
            $emit(
              'update:loops',
              definition.loops?.filter((item) => item.id !== loop.id) ?? [],
            )
          "
          >{{ tm("loops.remove") }}</v-btn
        >
      </template>
    </details>
    <p v-if="definition.loops?.length" class="text-caption mt-2">
      {{ tm("loops.rules") }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import type {
  GraphDefinition,
  GraphLoop,
  LoopFeedback,
} from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";
const props = withDefaults(
  defineProps<{
    definition: GraphDefinition;
    readonly?: boolean;
    collapsed: string[];
    fieldsForTask: (id: string) => { title: string; value: string }[];
    issues: { path: string; message: string }[];
  }>(),
  { readonly: false },
);
const emit = defineEmits<{
  "update:loops": [loops: GraphLoop[]];
  toggle: [loopId: string];
}>();
const { tm } = useModuleI18n("features/agent-teams");
const taskItems = computed(() =>
  props.definition.tasks.map((task) => ({ title: task.name, value: task.id })),
);

const artifactTaskItems = computed(() =>
  props.definition.tasks
    .filter((task) => !task.control)
    .map((task) => ({ title: task.name, value: task.id })),
);

const newLoopId = ref<string | null>(null);
function addLoop() {
  const loops = props.definition.loops ?? [];
  let index = 1;
  while (loops.some((loop) => loop.id === `loop_${index}`)) index++;
  newLoopId.value = `loop_${index}`;
  emit("update:loops", [
    ...loops,
    {
      id: `loop_${index}`,
      name: tm("loops.new"),
      body: [],
      exit_task_id: "",
      exit_condition: { field: "approved", operator: "eq", value: true },
      max_iterations: 3,
      feedback: [],
    },
  ]);
}
function change(id: string, values: Partial<GraphLoop>) {
  emit(
    "update:loops",
    (props.definition.loops ?? []).map((loop) =>
      loop.id === id ? { ...loop, ...values } : loop,
    ),
  );
}
function changeExitTask(id: string, taskId: string) {
  const loop = props.definition.loops?.find((item) => item.id === id);
  if (!loop) return;
  const field = props.fieldsForTask(taskId)[0];
  const type = field?.title.split(" · ").pop();
  const value =
    type === "boolean"
      ? false
      : type === "null"
      ? null
      : ["integer", "number"].includes(type ?? "")
      ? 0
      : "";
  change(id, {
    exit_task_id: taskId,
    exit_condition: { field: field?.value ?? "", operator: "eq", value },
  });
}
function exitType(loop: GraphLoop) {
  return (
    props
      .fieldsForTask(loop.exit_task_id)
      .find((field) => field.value === loop.exit_condition.field)
      ?.title.split(" · ")
      .pop() ?? "string"
  );
}
function setExitField(id: string, field: string) {
  const loop = props.definition.loops?.find((item) => item.id === id);
  if (!loop) return;
  const type = props
    .fieldsForTask(loop.exit_task_id)
    .find((item) => item.value === field)
    ?.title.split(" · ")
    .pop();
  const value =
    type === "boolean"
      ? false
      : type === "null"
      ? null
      : ["integer", "number"].includes(type ?? "")
      ? 0
      : "";
  change(id, { exit_condition: { field, operator: "eq", value } });
}
function setValue(id: string, text: string) {
  const loop = props.definition.loops?.find((item) => item.id === id);
  if (!loop) return;
  const value = ["integer", "number"].includes(exitType(loop))
    ? Number(text)
    : text;
  change(id, { exit_condition: { ...loop.exit_condition, value } });
}
function setFeedback(id: string, index: number, values: Partial<LoopFeedback>) {
  const loop = props.definition.loops?.find((loop) => loop.id === id);
  if (loop)
    change(id, {
      feedback: loop.feedback?.map((item, i) =>
        i === index ? { ...item, ...values } : item,
      ),
    });
}
</script>

<style scoped>
.loop-panel {
  padding: 12px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12);
}
.loop-group {
  margin: 12px 0;
  padding: 12px;
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  border-radius: 12px;
  overflow-wrap: anywhere;
}
.loop-summary-detail {
  display: block;
  margin: 4px 0 0 16px;
  font-size: 12px;
  font-weight: normal;
}
summary {
  cursor: pointer;
  font-weight: 600;
}
.loop-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 8px;
}
</style>
