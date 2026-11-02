<template>
  <section class="my-4" :aria-label="tm('workflowCall.allowed')">
    <h4 class="text-subtitle-1 mb-2">{{ tm("workflowCall.allowed") }}</h4>
    <p class="text-body-2 mb-3">{{ tm("workflowCall.hint") }}</p>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{
      error
    }}</v-alert>
    <v-select
      v-model="newWorkflow"
      :items="assets"
      item-title="definition.name"
      item-value="asset_id"
      :label="tm('workflowCall.add')"
      data-testid="allowed-workflow-add"
      :disabled="!active || loading"
      @update:model-value="addWorkflow"
    />
    <div
      v-for="(ref, index) in modelValue"
      :key="ref.asset_id + ':' + index"
      class="allowed-workflow mb-4"
    >
      <strong>{{
        definitions[`${ref.asset_id}:${ref.version}`]?.definition.name ??
        assets.find((asset) => asset.asset_id === ref.asset_id)?.definition
          .name ??
        ref.asset_id
      }}</strong>
      <div class="workflow-selectors mt-2">
        <v-select
          :model-value="ref.version"
          :items="
            assets.find((asset) => asset.asset_id === ref.asset_id)
              ?.versions ?? [ref.version]
          "
          :label="tm('workflowCall.version')"
          :data-testid="`allowed-workflow-version-${index}`"
          @update:model-value="changeVersion(index, $event)"
        />
        <v-btn
          variant="text"
          :data-testid="`allowed-workflow-remove-${index}`"
          @click="
            $emit(
              'update:modelValue',
              modelValue.filter((_, i) => i !== index),
            )
          "
          >{{ tm("workflowCall.remove") }}</v-btn
        >
      </div>
      <template v-if="definitions[`${ref.asset_id}:${ref.version}`]">
        <p class="text-caption">
          {{ tm("graph.inputs") }}:
          {{
            definitions[
              `${ref.asset_id}:${ref.version}`
            ].definition.required_inputs.join(", ") || "—"
          }}
        </p>
        <pre class="text-caption contract-preview">{{
          JSON.stringify(
            definitions[
              `${ref.asset_id}:${ref.version}`
            ].definition.tasks?.find(
              (task: any) =>
                task.id ===
                definitions[`${ref.asset_id}:${ref.version}`].definition
                  .result_task_id,
            )?.output_contract ?? {
              type: "object",
              properties: { text: { type: "string" } },
              required: ["text"],
            },
            null,
            2,
          )
        }}</pre>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import type { WorkflowCall } from "@/api/generated/openapi-v1";

const props = defineProps<{ modelValue: WorkflowCall[]; active: boolean }>();
const emit = defineEmits<{
  "update:modelValue": [value: WorkflowCall[]];
  validation: [invalid: boolean];
}>();
const { tm } = useModuleI18n("features/agent-teams");
const assets = ref<any[]>([]);
const definitions = ref<Record<string, any>>({});
const error = ref("");
const reading = ref(false);
const versionReads = ref(new Map<WorkflowCall, symbol>());
const loading = computed(() => reading.value || versionReads.value.size > 0);
const newWorkflow = ref<string | null>(null);
let generation = 0;
onBeforeUnmount(() => {
  generation++;
  versionReads.value.clear();
});

watch(
  [() => props.active, loading, error],
  () => {
    emit("validation", props.active && (loading.value || !!error.value));
  },
  { immediate: true },
);

watch(
  () => [props.active, props.modelValue] as const,
  async ([active, refs]) => {
    // Independent rows retain their requests when another row changes.
    for (const selected of versionReads.value.keys()) {
      if (!active || !refs.includes(selected))
        versionReads.value.delete(selected);
    }
    const request = ++generation;
    const signature = JSON.stringify(refs);
    const current = () =>
      request === generation &&
      props.active &&
      JSON.stringify(props.modelValue) === signature;
    error.value = "";
    reading.value = active;
    if (!active) return;
    try {
      const list = await workflowAssetsApi.list();
      if (!current()) return;
      assets.value = list.assets.filter(
        (asset: any) => asset.definition.kind !== "autonomous",
      );
      for (const ref of refs) {
        const key = `${ref.asset_id}:${ref.version}`;
        if (definitions.value[key]) continue;
        const asset = await workflowAssetsApi.get(ref.asset_id, ref.version);
        if (!current()) return;
        definitions.value[key] = asset;
      }
    } catch (err) {
      if (!current()) return;
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
    } finally {
      if (current()) {
        reading.value = false;
      }
    }
  },
  { immediate: true, deep: true },
);

function addWorkflow(assetId: string | null) {
  newWorkflow.value = null;
  if (!assetId) return;
  const asset = assets.value.find((item) => item.asset_id === assetId);
  if (
    !asset ||
    props.modelValue.some(
      (item) => item.asset_id === assetId && item.version === asset.version,
    )
  )
    return;
  definitions.value[`${assetId}:${asset.version}`] = asset;
  emit("update:modelValue", [
    ...props.modelValue,
    {
      asset_id: assetId,
      version: asset.version,
    },
  ]);
}

async function changeVersion(index: number, version: number) {
  const selected = props.modelValue[index];
  if (!selected || !props.active) return;
  const request = Symbol();
  versionReads.value.set(selected, request);
  const current = () =>
    versionReads.value.get(selected) === request &&
    props.active &&
    props.modelValue.includes(selected);
  error.value = "";
  try {
    const key = `${selected.asset_id}:${version}`;
    const asset =
      definitions.value[key] ??
      (await workflowAssetsApi.get(selected.asset_id, version));
    if (!current()) return;
    definitions.value[key] = asset;
    emit(
      "update:modelValue",
      props.modelValue.map((item) =>
        item === selected
          ? {
              ...item,
              version,
            }
          : item,
      ),
    );
  } catch (err) {
    if (!current()) return;
    error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    if (versionReads.value.get(selected) === request)
      versionReads.value.delete(selected);
  }
}
</script>

<style scoped>
.allowed-workflow {
  overflow-wrap: anywhere;
}
.workflow-selectors {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.workflow-selectors :deep(button) {
  grid-column: 1 / -1;
  justify-self: start;
}
@media (max-width: 600px) {
  .workflow-selectors {
    grid-template-columns: minmax(0, 1fr);
  }
}
.contract-preview {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 180px;
  overflow: auto;
}
</style>
