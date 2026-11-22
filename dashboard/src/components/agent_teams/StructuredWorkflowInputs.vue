<template>
  <div v-for="port in referencePorts" :key="port.name" class="mb-2">
    <v-select
      :model-value="selectedReferences[port.name]?.version"
      :items="history ?? []"
      :label="`${port.name} · ${tm('trial.history')}`"
      :data-testid="`reference-history-${port.name}`"
      clearable
      @update:model-value="selectVersion(port, $event)"
    />
    <v-select
      v-if="port.file && selectedReferences[port.name]?.version"
      :items="files[port.name] ?? []"
      :model-value="selectedReferences[port.name]?.file"
      item-title="filename"
      item-value="file_id"
      :label="`${port.name} · ${tm('graph.portFormats.astrbot-file')}`"
      :data-testid="`reference-file-${port.name}`"
      @update:model-value="selectFile(port.name, $event)"
    />
    <p v-if="errors[port.name]" role="alert" class="text-error">{{ errors[port.name] }}</p>
  </div>
  <v-textarea
    :model-value="modelValue"
    :label="tm('graph.inputValuesJson')"
    :hint="tm('graph.inputValuesHint')"
    persistent-hint
    spellcheck="false"
    rows="4"
    data-testid="structured-input-values"
    @update:model-value="emit('update:modelValue', $event)"
  />
  <details class="mb-4">
    <summary>{{ tm("graph.input_contract") }}</summary>
    <pre class="text-caption">{{ JSON.stringify(contract, null, 2) }}</pre>
  </details>
</template>

<script lang="ts">
export function parseWorkflowInputs(text: string): Record<string, unknown> {
  const values: unknown = JSON.parse(text, (_key, value) => {
    if (typeof value === "number" && !Number.isFinite(value))
      throw new Error("Use finite JSON numbers.");
    return value;
  });
  if (values === null || typeof values !== "object" || Array.isArray(values))
    throw new Error("Provide a JSON object.");
  return values as Record<string, unknown>;
}
</script>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { extractApiError } from "@/utils/extractApiError";
import type { ArtifactReference, WorkflowPortContract } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  contract: WorkflowPortContract;
  modelValue: string;
  history?: { title: string; value: string }[];
}>();
const referencePorts = computed(() => Object.entries(props.contract.properties).flatMap(([name, schema]) =>
  schema.format === "astrbot-artifact" || schema.format === "astrbot-file"
    ? [{ name, file: schema.format === "astrbot-file" }] : [],
));
const pendingVersions = ref<Record<string, string | null>>(Object.create(null));
const parsedInputs = computed(() => {
  try {
    return parseWorkflowInputs(props.modelValue || "{}");
  } catch {
    return null;
  }
});
const selectedReferences = computed(() =>
  Object.fromEntries(referencePorts.value.map(({ name, file }) => {
    const values = parsedInputs.value;
    const value = (values && Object.prototype.hasOwnProperty.call(values, name)
      ? values[name] : undefined) as Partial<ArtifactReference & { file_id: string }> | undefined;
    const reference = value && typeof value.artifact_id === "string" &&
      typeof value.version === "number" && Number.isSafeInteger(value.version) && value.version > 0
      ? { artifact_id: value.artifact_id, version: value.version } : null;
    return [name, {
      version: reference ? JSON.stringify(reference) : parsedInputs.value ? pendingVersions.value[name] : null,
      file: reference && file && typeof value?.file_id === "string" ? value.file_id : null,
    }];
  })),
);
const files = ref<Record<string, { file_id: string; filename: string }[]>>(Object.create(null));
const errors = ref<Record<string, string>>(Object.create(null));
const requests: Record<string, number> = Object.create(null);
const emit = defineEmits<{ (event: "update:modelValue", value: string): void }>();
const { tm } = useModuleI18n("features/agent-teams");

function setReference(name: string, reference: ArtifactReference | null, fileId?: string) {
  if (!parsedInputs.value) {
    errors.value[name] = tm("graph.inputValuesHint");
    return false;
  }
  const values = reference
    ? { ...parsedInputs.value, [name]: fileId ? { ...reference, file_id: fileId } : reference }
    : { ...parsedInputs.value };
  if (!reference) Reflect.deleteProperty(values, name);
  errors.value[name] = "";
  emit("update:modelValue", JSON.stringify(values, null, 2));
  return true;
}

function selectVersion(port: { name: string; file: boolean }, key: string | null) {
  const reference: ArtifactReference | null = key ? JSON.parse(key) : null;
  if (setReference(port.name, port.file ? null : reference))
    pendingVersions.value[port.name] = port.file ? key : null;
}

function selectFile(name: string, fileId: string | null) {
  const key = selectedReferences.value[name]?.version;
  if (setReference(name, key && fileId ? JSON.parse(key) : null, fileId ?? undefined))
    pendingVersions.value[name] = fileId ? null : key ?? null;
}

watch(selectedReferences, (selections, previous) => {
  for (const { name, file } of referencePorts.value) {
    const key = selections[name]?.version;
    if (!file || key === previous?.[name]?.version) continue;
    const request = (requests[name] ?? 0) + 1;
    requests[name] = request;
    files.value[name] = [];
    if (!key) continue;
    const reference: ArtifactReference = JSON.parse(key);
    void workflowAssetsApi.artifact(reference.artifact_id, reference.version).then(
      (artifact) => {
        if (request !== requests[name]) return;
        files.value[name] = artifact.files ?? [];
        errors.value[name] = files.value[name].length ? "" : tm("trial.noManagedFiles");
      },
      (error) => {
        if (request === requests[name])
          errors.value[name] = extractApiError(error, tm("errors.operationFailed")).message;
      },
    );
  }
}, { immediate: true });
</script>
