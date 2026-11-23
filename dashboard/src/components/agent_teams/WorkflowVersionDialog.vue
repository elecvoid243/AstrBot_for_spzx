<template>
  <v-dialog :model-value="true" max-width="920" :persistent="restoring" @update:model-value="!$event && emit('close')">
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{ tm("reuse.versions") }}</v-card-title>
      <v-card-text>
        <div class="version-comparison">
          <v-select v-model="left" :items="versions" :label="tm('reuse.restoreSource')" density="compact" :disabled="restoring" />
          <v-select v-model="right" :items="versions" :label="tm('reuse.compareWith')" density="compact" :disabled="restoring" />
        </div>
        <v-progress-linear v-if="loading" indeterminate />
        <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
        <template v-if="definitions && !loading">
          <p v-if="!changes.length">{{ tm("reuse.noChanges") }}</p>
          <details v-for="change in changes" :key="change.key" class="version-change">
            <summary>{{ change.label }}</summary>
            <div class="version-comparison">
              <pre>{{ change.before ?? "—" }}</pre>
              <pre>{{ change.after ?? "—" }}</pre>
            </div>
          </details>
          <v-alert v-if="confirming" type="warning" variant="tonal" class="mt-3">
            {{ tm("reuse.confirmRestore", { version: left ?? "" }) }}
          </v-alert>
        </template>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="restoring" @click="emit('close')">{{ tm("v2.close") }}</v-btn>
        <v-btn v-if="confirming" variant="tonal" :loading="restoring" :disabled="blocked || restoring" @click="restore">
          {{ tm("reuse.restore") }}
        </v-btn>
        <v-btn v-else variant="tonal" :disabled="!definitions || loading || blocked" @click="confirming = true">
          {{ tm("reuse.restore") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { GraphDefinition } from "@/api/generated/openapi-v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";

const props = defineProps<{
  assetId: string;
  restoreVersion: (source: number, latest: number) => Promise<void>;
  blocked: boolean;
}>();
const emit = defineEmits<{ close: [] }>();
const { tm } = useModuleI18n("features/agent-teams");
const versions = ref<number[]>([]);
const left = ref<number>();
const right = ref<number>();
const latest = ref<number>();
const definitions = ref<[GraphDefinition, GraphDefinition]>();
const loading = ref(true);
const restoring = ref(false);
const confirming = ref(false);
const error = ref("");

watch(() => props.assetId, async (id, _, onCleanup) => {
  let active = true;
  onCleanup(() => { active = false; });
  try {
    const asset = await workflowAssetsApi.get(id);
    if (!active) return;
    versions.value = asset.versions;
    latest.value = right.value = asset.version;
    left.value = asset.versions.find((version: number) => version !== asset.version) ?? asset.version;
  } catch (cause) {
    if (!active) return;
    loading.value = false;
    error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  }
}, { immediate: true });

watch([left, right], async ([before, after], _, onCleanup) => {
  if (!before || !after) return;
  let active = true;
  onCleanup(() => { active = false; });
  confirming.value = false;
  definitions.value = undefined;
  error.value = "";
  loading.value = true;
  try {
    const [a, b] = await Promise.all([
      workflowAssetsApi.get(props.assetId, before),
      workflowAssetsApi.get(props.assetId, after),
    ]);
    if (active) definitions.value = [a.definition, b.definition];
  } catch (cause) {
    if (active) error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    if (active) loading.value = false;
  }
});

const changes = computed(() => {
  const sections = definitions.value?.map(({ tasks, edges, loops, canvas, ...settings }) => {
    return new Map<string, { label: string; value: unknown }>([
      ["settings", { label: tm("editor.settings"), value: settings }],
      ...tasks.map((task): [string, { label: string; value: unknown }] =>
        ["task:" + task.id, { label: task.name + " · " + task.id, value: task }]),
      ["order", { label: tm("reuse.taskOrder"), value: tasks.map((task) => task.id) }],
      ["edges", { label: tm("reuse.controlEdges"), value: edges ?? [] }],
      ["loops", { label: tm("editor.structure"), value: loops ?? [] }],
      ["canvas", { label: tm("reuse.annotations"), value: canvas ?? {} }],
    ]);
  });
  if (!sections) return [];
  const [a, b] = sections;
  return [...new Set([...a.keys(), ...b.keys()])].flatMap((key) => {
    const before = JSON.stringify(a.get(key)?.value, null, 2);
    const after = JSON.stringify(b.get(key)?.value, null, 2);
    return before === after ? [] : [{ key, label: b.get(key)?.label ?? a.get(key)?.label, before, after }];
  });
});

async function restore() {
  if (!left.value || !latest.value || props.blocked || restoring.value) return;
  restoring.value = true;
  error.value = "";
  try {
    await props.restoreVersion(left.value, latest.value);
    emit("close");
  } catch (cause) {
    error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    restoring.value = false;
  }
}
</script>

<style scoped>
.version-comparison { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.version-change { padding: 8px 0; border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.16); }
.version-change pre { white-space: pre-wrap; overflow-wrap: anywhere; padding: 8px; }
@media (max-width: 640px) { .version-comparison { grid-template-columns: 1fr; } }
</style>
