<template>
  <v-dialog :model-value="true" max-width="760" scrollable @update:model-value="open => { if (!open) emit('close'); }">
    <v-card data-testid="member-attempt-inspector">
      <h3 class="text-h3 pa-4 pb-0 pl-6">{{ tm("memberInspection.title") }}</h3>
      <v-card-text>
        <p v-if="loading" role="status">{{ tm("inspection.loading") }}</p>
        <p v-else-if="error" role="alert">{{ error }}</p>
        <template v-else-if="data">
          <p class="text-subtitle-1 font-weight-bold" data-testid="member-attempt-identity">
            {{ data.member.name }} · {{ getRaw(`v2.states.${data.status}`) ?? data.status }}
          </p>
          <p class="text-caption evidence-text">
            {{ tm("v2.member") }} {{ data.member.member_id }} · {{ tm("v2.attempt") }} {{ data.attempt_id }}
          </p>
          <p class="text-caption evidence-text">
            {{ data.team.name }}
            <span v-if="data.team.draft_revision != null"> · {{ tm("memberInspection.sourceDraft", { revision: data.team.draft_revision }) }}</span>
            <span v-else-if="data.team.version != null"> · {{ tm("memberInspection.sourceVersion", { version: data.team.version }) }}</span>
          </p>
          <h4 class="text-subtitle-1 mt-4">{{ tm("memberInspection.dispatch") }}</h4>
          <p class="font-weight-medium evidence-text">{{ data.dispatch.name }}</p>
          <pre v-if="data.dispatch.task !== null" class="evidence-text" data-testid="member-attempt-dispatch">{{ data.dispatch.task }}</pre>
          <p v-else>{{ tm("progress.sourceAbsent") }}</p>
          <h4 class="text-subtitle-1 mt-4">{{ tm("memberInspection.messages") }}</h4>
          <p v-if="!data.messages.length" class="text-body-2" data-testid="member-attempt-no-messages">{{ tm("memberInspection.missingMessages") }}</p>
          <p v-if="data.unconfirmed_deliveries" class="text-body-2" data-testid="member-attempt-unconfirmed">
            {{ tm("memberInspection.unconfirmed", { count: data.unconfirmed_deliveries }) }}
          </p>
          <v-sheet v-for="message in data.messages" :key="message.message_id" border rounded class="pa-3 my-2" data-testid="member-attempt-message">
            <p class="text-caption evidence-text">{{ tm("memberInspection.sender", { id: message.sender_member_id }) }}</p>
            <pre class="evidence-text my-1">{{ message.text }}</pre>
            <p class="text-caption evidence-text">
              {{ tm("communication.consumed") }} ·
              {{ tm("recovery.contextPosition") }} {{ message.consumption.context_position }} ·
              {{ message.consumption.invocation_id }}
            </p>
            <p v-for="ref in message.artifact_refs" :key="`${ref.artifact_id}:${ref.version}`" class="text-caption evidence-text">
              {{ ref.artifact_id }} · v{{ ref.version }}
            </p>
          </v-sheet>
          <h4 class="text-subtitle-1 mt-4">{{ tm("progress.artifacts") }}</h4>
          <p v-if="!data.result_artifact" class="text-body-2" data-testid="member-attempt-no-result">{{ tm("memberInspection.missingResult") }}</p>
          <div v-for="{ ref, labels, key } in artifacts" :key="key" class="my-2">
            <p class="text-caption evidence-text">
              {{ [...labels].map(label => tm(`memberInspection.${label}`)).join(" · ") }}
              · {{ ref.artifact_id }} · v{{ ref.version }}
            </p>
            <v-btn size="small" variant="text" data-testid="member-attempt-read-artifact"
              :loading="artifactReads[key]?.loading" @click="readArtifact(ref, key)">
              {{ tm("memberInspection.readArtifact") }}
            </v-btn>
            <p v-if="artifactReads[key]?.error" role="alert">{{ artifactReads[key].error }}</p>
            <pre v-if="artifactReads[key]?.content != null"
              class="evidence-text" data-testid="member-attempt-artifact">{{ JSON.stringify(artifactReads[key].content, null, 2) }}</pre>
          </div>
          <p v-if="trialError" class="text-error mt-4" role="alert">{{ trialError }}</p>
        </template>
      </v-card-text>
      <v-card-actions class="flex-wrap">
        <v-spacer />
        <v-btn variant="text" @click="emit('close')">{{ tm("v2.close") }}</v-btn>
        <v-btn v-if="data?.team.asset_id" variant="tonal" :loading="openingTrial" data-testid="member-attempt-trial" @click="openTrial">
          {{ tm("memberInspection.wholeTrial") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
  <TrialRunDialog v-if="trialDraft" v-model="trialOpen" :asset-id="trialDraft.asset_id" :revision="trialDraft.revision"
    kind="autonomous" :nodes="[]" :team-name="trialDraft.body.definition?.name ?? ''" @started="emit('trial-started', $event)" />
</template>

<script setup lang="ts">
import { isAxiosError } from "axios";
import { computed, reactive, ref, watch } from "vue";
import { workflowAssetsApi } from "@/api/workflowAssets";
import type { ArtifactReference, WorkflowMemberAttempt } from "@/api/generated/openapi-v1";
import { draftApi, type Draft } from "@/composables/useAssetDraft";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import TrialRunDialog from "./TrialRunDialog.vue";

const props = defineProps<{ rootId: string; taskId: string; attemptId: string }>();
const emit = defineEmits<{ close: []; "trial-started": [runId: string] }>();
const { tm, getRaw } = useModuleI18n("features/agent-teams");
const data = ref<WorkflowMemberAttempt>();
const loading = ref(false);
const error = ref("");
const trialError = ref("");
const openingTrial = ref(false);
const trialOpen = ref(false);
const trialDraft = ref<Draft>();
const artifactReads = reactive<Record<string, { loading: boolean; content?: unknown; error?: string }>>({});
let signal: AbortSignal;

const artifacts = computed(() => {
  const item = data.value;
  if (!item) return [];
  const byVersion = new Map<string, { ref: ArtifactReference; key: string; labels: Set<string> }>();
  for (const { ref, label } of [
    ...(item.result_artifact ? [{ ref: item.result_artifact, label: "outputArtifact" }] : []),
    ...item.input_artifacts.map(ref => ({ ref, label: "inputArtifact" })),
    ...item.read_artifacts.map(ref => ({ ref, label: "recordedRead" })),
    ...item.messages.flatMap(message => message.artifact_refs.map(ref => ({ ref, label: "messageArtifact" }))),
  ]) {
    const key = `${ref.artifact_id}:${ref.version}`;
    const previous = byVersion.get(key);
    if (previous) previous.labels.add(label);
    else byVersion.set(key, { ref, key, labels: new Set([label]) });
  }
  return [...byVersion.values()];
});

watch(() => [props.rootId, props.taskId, props.attemptId] as const, async ([root, task, attempt], _previous, onCleanup) => {
  const controller = new AbortController();
  const request = controller.signal;
  signal = request;
  onCleanup(() => controller.abort());
  data.value = undefined;
  error.value = "";
  loading.value = true;
  try {
    const evidence = await workflowAssetsApi.memberAttempt(root, task, attempt, request);
    if (!request.aborted) data.value = evidence;
  } catch (cause) {
    if (!request.aborted) error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    if (!request.aborted) loading.value = false;
  }
}, { immediate: true });

async function readArtifact(ref: ArtifactReference, key: string) {
  const request = signal;
  if (request.aborted || artifactReads[key]?.loading) return;
  artifactReads[key] = { loading: true };
  try {
    const artifact = await workflowAssetsApi.artifact(ref.artifact_id, ref.version, request);
    if (!request.aborted) artifactReads[key].content = artifact.content;
  } catch (cause) {
    if (!request.aborted) artifactReads[key].error = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    if (!request.aborted) artifactReads[key].loading = false;
  }
}

async function openTrial() {
  const assetId = data.value?.team.asset_id;
  const request = signal;
  if (!assetId || request.aborted || openingTrial.value) return;
  openingTrial.value = true;
  trialError.value = "";
  try {
    let draft: Draft;
    try {
      draft = await draftApi.get("autonomous", assetId);
    } catch (cause) {
      if (!isAxiosError(cause) || cause.response?.status !== 404) throw cause;
      if (request.aborted) return;
      const asset = await workflowAssetsApi.get(assetId);
      if (request.aborted) return;
      try {
        draft = await draftApi.create("autonomous", { definition: asset.definition }, assetId);
      } catch (conflict) {
        if (!isAxiosError(conflict) || conflict.response?.status !== 409) throw conflict;
        if (request.aborted) return;
        draft = await draftApi.get("autonomous", assetId);
      }
    }
    if (request.aborted) return;
    trialDraft.value = draft;
    trialOpen.value = true;
  } catch (cause) {
    if (!request.aborted) trialError.value = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    if (!request.aborted) openingTrial.value = false;
  }
}
</script>

<style scoped>
.evidence-text {
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
pre {
  max-height: 14rem;
  overflow: auto;
}
</style>
