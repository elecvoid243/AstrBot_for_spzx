<template>
  <div class="mb-2">
    <v-autocomplete
      v-if="recent.length"
      :items="recent"
      :model-value="recentSelection"
      :label="tm('trial.history')"
      variant="outlined"
      density="compact"
      :disabled="disabled"
      @update:model-value="pickRecent"
    />
    <div class="d-flex ga-3 flex-wrap">
      <v-text-field
        data-testid="debug-fork-artifact-id"
        v-model.trim="artifactId"
        :label="tm('trial.artifactId')"
        variant="outlined"
        density="compact"
        :disabled="disabled"
      />
      <v-text-field
        data-testid="debug-fork-artifact-version"
        v-model="version"
        type="number"
        min="1"
        :label="tm('trial.artifactVersion')"
        variant="outlined"
        density="compact"
        :disabled="disabled"
      />
    </div>
    <v-progress-linear v-if="loading" indeterminate />
    <v-alert v-if="error" type="warning" variant="tonal">{{ error }}</v-alert>
    <v-select
      v-if="file && files.length"
      v-model="fileId"
      :items="files"
      :item-title="item => `${item.filename} · ${item.file_id}`"
      data-testid="debug-fork-attachment"
      item-value="file_id"
      :label="tm('debugFork.attachment')"
      variant="outlined"
      density="compact"
      :disabled="disabled"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from "vue";
import type { ArtifactReference } from "@/api/generated/openapi-v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";

type Selection = ArtifactReference & { file_id?: string };

const props = defineProps<{ selection?: Selection; file: boolean; disabled: boolean }>();
const emit = defineEmits<{ "update:selection": [value: Selection | undefined] }>();
const { tm } = useModuleI18n("features/agent-teams");
const artifactId = ref(props.selection?.artifact_id ?? "");
const version = ref(String(props.selection?.version ?? ""));
const fileId = ref(props.selection?.file_id);
const files = ref<{ file_id: string; filename: string }[]>([]);
const recent = ref<{ title: string; value: ArtifactReference }[]>([]);
const checked = ref<ArtifactReference>();
const loading = ref(false);
const error = ref("");
const controller = new AbortController();
onScopeDispose(() => controller.abort());

void workflowAssetsApi.runs(true, controller.signal).then(({ runs }) => {
  if (controller.signal.aborted) return;
  recent.value = runs.flatMap((run: {
    snapshot: { definition: { name: string } };
    artifacts?: (ArtifactReference & { summary: string })[];
  }) => (run.artifacts ?? []).map(({ artifact_id, version, summary }) => ({
    title: `${run.snapshot.definition.name} · ${artifact_id} / v${version} · ${summary}`,
    value: { artifact_id, version },
  })));
}).catch(() => {});

const recentSelection = computed(() =>
  recent.value.find(option => option.value.artifact_id === artifactId.value &&
    option.value.version === Number(version.value))?.value ?? null,
);

function pickRecent(ref: ArtifactReference | null) {
  if (!ref) return;
  artifactId.value = ref.artifact_id;
  version.value = String(ref.version);
}

const selection = computed(() => {
  const ref = checked.value;
  if (!ref || artifactId.value !== ref.artifact_id || Number(version.value) !== ref.version) return;
  if (!props.file) return ref;
  const id = fileId.value;
  if (id && files.value.some(file => file.file_id === id)) return { ...ref, file_id: id };
});

watch(selection, value => {
  if (JSON.stringify(value) !== JSON.stringify(props.selection)) emit("update:selection", value);
}, { flush: "sync" });
watch([artifactId, version], async ([id, rawVersion], previous, onCleanup) => {
  const request = new AbortController();
  onCleanup(() => request.abort());
  if (previous.length) {
    fileId.value = undefined;
    emit("update:selection", undefined);
  }
  checked.value = undefined;
  files.value = [];
  error.value = "";
  loading.value = false;
  const exactVersion = Number(rawVersion);
  if (!id || !Number.isSafeInteger(exactVersion) || exactVersion < 1) return;

  loading.value = true;
  try {
    const artifact = await workflowAssetsApi.artifact(id, exactVersion, request.signal);
    if (request.signal.aborted) return;
    files.value = artifact.files ?? [];
    checked.value = { artifact_id: id, version: exactVersion };
    if (props.file && !files.value.length) error.value = tm("debugFork.noAttachments");
  } catch {
    if (!request.signal.aborted) error.value = tm("debugFork.referenceUnavailable");
  } finally {
    if (!request.signal.aborted) loading.value = false;
  }
}, { immediate: true });
</script>
