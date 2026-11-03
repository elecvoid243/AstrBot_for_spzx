<template>
  <v-dialog
    :model-value="modelValue"
    max-width="760"
    scrollable
    :persistent="starting"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
        tm("trial.title")
      }}</v-card-title>
      <v-card-text :inert="starting ? '' : undefined">
        <p class="text-caption mb-2">
          {{ tm("trial.revision") }} {{ revision }}
        </p>
        <v-alert type="warning" variant="tonal" class="mb-4">{{
          tm("trial.realCalls")
        }}</v-alert>
        <v-select
          v-if="kind === 'workflow'"
          v-model="mode"
          :label="tm('trial.scope')"
          :items="scopeOptions"
        />
        <v-select
          v-if="kind === 'workflow' && mode !== 'full'"
          v-model="node"
          :items="nodes"
          item-title="name"
          item-value="id"
          :label="tm('trial.startNode')"
        />
        <v-alert v-if="error" type="error" variant="tonal" role="alert">{{
          error
        }}</v-alert>
        <v-progress-linear v-if="loading" indeterminate />
        <template v-if="preview">
          <p class="mb-3">
            {{ tm("trial.selected") }}:
            {{ selectedNames.join(", ") || tm("trial.dynamicTeam") }}
          </p>
          <v-text-field
            v-for="name in preview.required_inputs"
            :key="name"
            v-model="inputs[name]"
            :label="name"
          />
          <section
            v-for="(requirement, id) in preview.requirements"
            :key="id"
            class="mb-5"
          >
            <h3 class="text-subtitle-1">{{ requirement.name }} · {{ id }}</h3>
            <v-select
              v-model="sourceModes[id]"
              :label="tm('trial.source')"
              :items="
                requirement.pinned_only
                  ? [artifactChoice]
                  : [manualChoice, artifactChoice]
              "
            />
            <template v-if="sourceModes[id] === 'artifact'">
              <v-select
                v-model="selectedArtifacts[id]"
                :label="tm('trial.history')"
                :items="artifacts"
                clearable
              />
              <v-text-field
                v-model="artifactIds[id]"
                :label="tm('trial.artifactId')"
              />
              <v-text-field
                v-model.number="artifactVersions[id]"
                type="number"
                min="1"
                :label="tm('trial.artifactVersion')"
              />
            </template>
            <template v-else>
              <v-switch
                v-model="useRaw[id]"
                :label="tm('trial.advancedMode')"
              />
              <div
                v-for="field in useRaw[id] ? [] : manualFields(requirement)"
                :key="field.path"
              >
                <v-select
                  v-if="field.types.length > 1"
                  v-model="fieldTypes[id][field.path]"
                  :label="`${field.path} · ${tm('trial.valueType')}`"
                  :items="field.types"
                />
                <v-select
                  v-if="
                    (fieldTypes[id][field.path] ?? field.types[0]) === 'boolean'
                  "
                  v-model="manual[id][field.path]"
                  :label="field.path"
                  :items="['true', 'false']"
                />
                <v-text-field
                  v-else-if="
                    (fieldTypes[id][field.path] ?? field.types[0]) !== 'null'
                  "
                  v-model="manual[id][field.path]"
                  :label="`${field.path} (${
                    fieldTypes[id][field.path] ?? field.types[0]
                  })`"
                  :type="
                    ['integer', 'number'].includes(
                      fieldTypes[id][field.path] ?? field.types[0],
                    )
                      ? 'number'
                      : 'text'
                  "
                />
              </div>
              <v-textarea
                v-if="useRaw[id] || !manualFields(requirement).length"
                v-model="rawContent[id]"
                :label="tm('trial.advancedJson')"
                rows="3"
              />
            </template>
          </section>
        </template>
      </v-card-text>
      <v-card-actions
        ><v-spacer /><v-btn
          variant="text"
          :disabled="starting"
          @click="emit('update:modelValue', false)"
          >{{ tm("v2.close") }}</v-btn
        ><v-btn
          variant="tonal"
          color="primary"
          :disabled="!preview || loading"
          :loading="starting"
          @click="start"
          >{{ tm("trial.start") }}</v-btn
        ></v-card-actions
      >
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import type { TrialRunRequest } from "@/api/generated/openapi-v1";

const props = defineProps<{
  modelValue: boolean;
  assetId: string;
  revision: number;
  kind: "workflow" | "autonomous";
  nodes: { id: string; name: string }[];
  initialNode?: string | null;
}>();
const emit = defineEmits<{
  "update:modelValue": [boolean];
  started: [string];
}>();
const { tm } = useModuleI18n("features/agent-teams");
const mode = ref<"full" | "single" | "partial">("full");
const node = ref<string | null>(null);
const preview = ref<any>(null);
const selectedNames = computed(() =>
  (preview.value?.selected_nodes ?? []).map(
    (id: string) => props.nodes.find((item) => item.id === id)?.name ?? id,
  ),
);
const loading = ref(false);
const starting = ref(false);
const error = ref("");
const inputs = ref<Record<string, string>>({});
const sourceModes = ref<Record<string, string>>({});
const manual = ref<Record<string, Record<string, string>>>({});
const fieldTypes = ref<Record<string, Record<string, string>>>({});
const rawContent = ref<Record<string, string>>({});
const useRaw = ref<Record<string, boolean>>({});
const artifactIds = ref<Record<string, string>>({});
const artifactVersions = ref<Record<string, number>>({});
const selectedArtifacts = ref<Record<string, string | null>>({});
const artifacts = ref<{ title: string; value: string }[]>([]);
const manualChoice = computed(() => ({
  title: tm("trial.manual"),
  value: "manual",
}));
const artifactChoice = computed(() => ({
  title: tm("trial.artifact"),
  value: "artifact",
}));
const scopeOptions = computed(() =>
  ["full", "single", "partial"].map((value) => ({
    title: tm(`trial.${value}`),
    value,
  })),
);
let generation = 0;
watch(
  () => props.modelValue,
  async (open) => {
    if (!open) {
      generation++;
      return;
    }
    mode.value = "full";
    node.value = props.nodes.some((item) => item.id === props.initialNode)
      ? props.initialNode!
      : props.nodes[0]?.id ?? null;
    try {
      const data = await workflowAssetsApi.runs();
      artifacts.value = data.runs.flatMap((run: any) =>
        (run.artifacts ?? []).map((item: any) => ({
          title: `${run.snapshot.definition.name} · ${item.artifact_id} v${item.version} · ${item.summary}`,
          value: JSON.stringify({
            artifact_id: item.artifact_id,
            version: item.version,
          }),
        })),
      );
    } catch {
      artifacts.value = [];
    }
  },
  { immediate: true },
);
watch(
  [() => props.modelValue, mode, node, () => props.revision],
  async () => {
    const token = ++generation;
    preview.value = null;
    error.value = "";
    loading.value = false;
    if (!props.modelValue || (mode.value !== "full" && !node.value)) return;
    loading.value = true;
    try {
      const value = await workflowAssetsApi.previewTrial(
        props.assetId,
        {
          kind: props.kind,
          revision: props.revision,
          mode: mode.value,
          node_id: mode.value === "full" ? null : node.value,
        },
      );
      if (token !== generation) return;
      preview.value = value;
      inputs.value = Object.fromEntries(
        value.required_inputs.map((name: string) => [name, ""]),
      );
      sourceModes.value = {};
      manual.value = {};
      fieldTypes.value = {};
      rawContent.value = {};
      useRaw.value = {};
      artifactIds.value = {};
      artifactVersions.value = {};
      selectedArtifacts.value = {};
      for (const [id, requirement] of Object.entries(value.requirements) as [
        string,
        any,
      ][]) {
        sourceModes.value[id] = requirement.pinned_only ? "artifact" : "manual";
        manual.value[id] = {};
        fieldTypes.value[id] = {};
        artifactVersions.value[id] = 1;
      }
    } catch (cause) {
      if (token === generation) {
        const failure = extractApiError(cause, tm("errors.operationFailed"));
        error.value = [
          failure.message,
          ...failure.fields.map((field) => `${field.path}: ${field.message}`),
        ].join(" · ");
      }
    } finally {
      if (token === generation) loading.value = false;
    }
  },
  { immediate: true },
);
watch(
  selectedArtifacts,
  (values) => {
    for (const [id, value] of Object.entries(values))
      if (value) {
        const item = JSON.parse(value);
        artifactIds.value[id] = item.artifact_id;
        artifactVersions.value[id] = item.version;
      }
  },
  { deep: true },
);
function manualFields(requirement: any): { path: string; types: string[] }[] {
  const fields: Record<string, string[]> = {};
  const visit = (schema: any, prefix = "") => {
    for (const name of schema?.required ?? []) {
      const item = schema.properties?.[name];
      const path = prefix ? `${prefix}.${name}` : name;
      if (item?.type === "object") visit(item, path);
      else if (
        ["string", "integer", "number", "boolean", "null"].includes(item?.type)
      )
        fields[path] = [item.type];
    }
  };
  visit(
    requirement.contract ?? {
      required: ["text"],
      properties: { text: { type: "string" } },
    },
  );
  for (const [path, types] of Object.entries(requirement.fields) as [
    string,
    string[],
  ][])
    fields[path] = types;
  return Object.entries(fields).map(([path, types]) => ({ path, types }));
}
async function start() {
  if (starting.value || !preview.value) return;
  starting.value = true;
  error.value = "";
  try {
    const sources: NonNullable<TrialRunRequest["sources"]> = {};
    for (const [id, requirement] of Object.entries(
      preview.value.requirements,
    ) as [string, any][]) {
      if (sourceModes.value[id] === "artifact")
        sources[id] = {
          artifact: {
            artifact_id: artifactIds.value[id],
            version: Number(artifactVersions.value[id]),
          },
        };
      else {
        const fields = useRaw.value[id] ? [] : manualFields(requirement);
        const content: Record<string, any> = fields.length
          ? {}
          : JSON.parse(rawContent.value[id] || "{}");
        for (const field of fields) {
          const raw = manual.value[id][field.path];
          const type = fieldTypes.value[id][field.path] ?? field.types[0];
          if (type !== "null" && (raw === undefined || raw === ""))
            throw new Error(`${field.path}: ${tm("trial.required")}`);
          const value =
            type === "null"
              ? null
              : type === "boolean"
              ? raw === "true"
              : ["integer", "number"].includes(type)
              ? Number(raw)
              : raw;
          const parts = field.path.split(".");
          let target = content;
          for (const part of parts.slice(0, -1)) target = target[part] ??= {};
          target[parts[parts.length - 1]] = value;
        }
        sources[id] = { content };
      }
    }
    const result = await workflowAssetsApi.startTrial(props.assetId, {
      kind: props.kind,
      revision: props.revision,
      mode: mode.value,
      node_id: mode.value === "full" ? null : node.value,
      inputs: inputs.value,
      sources,
    });
    emit("update:modelValue", false);
    emit("started", result.run_id);
  } catch (cause) {
    const failure = extractApiError(cause, tm("errors.operationFailed"));
    error.value = [
      failure.message,
      ...failure.fields.map((field) => `${field.path}: ${field.message}`),
    ].join(" · ");
  } finally {
    starting.value = false;
  }
}
</script>
