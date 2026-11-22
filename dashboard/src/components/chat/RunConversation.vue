<template>
  <section class="pa-6 overflow-y-auto" :aria-label="tm('runConversation.type')">
    <v-alert v-if="unavailable" type="error" variant="tonal">
      {{ tm("runConversation.unavailable") }}
    </v-alert>
    <v-progress-circular v-else-if="!summary" indeterminate size="24" />
    <template v-else>
      <h1 class="text-h4 mb-4">{{ title }}</h1>
      <dl class="run-summary">
        <dt>{{ tm("runConversation.asset") }}</dt>
        <dd>{{ summary.asset_id }}</dd>
        <dt>{{ tm(summary.draft_revision == null ? "runConversation.version" : "trial.revision") }}</dt>
        <dd>{{ summary.draft_revision ?? summary.asset_version }}</dd>
        <dt>{{ tm("child.root") }}</dt>
        <dd>{{ summary.run_id }}</dd>
        <dt>{{ tm("runConversation.status") }}</dt>
        <dd role="status">{{ statusLabel }}</dd>
      </dl>
      <div class="d-flex flex-wrap ga-2 mt-4">
        <v-btn
          variant="tonal"
          :to="{ name: 'AgentTeams', query: { workflow_run: summary.run_id } }"
        >{{ tm("runConversation.openWorkbench") }}</v-btn>
        <v-btn v-if="archived" variant="text" :disabled="busy" @click="restoreSession">
          {{ chatLabel("conversation.unarchive") }}
        </v-btn>
        <v-btn v-if="canCancel" variant="text" color="error" :disabled="busy" @click="cancel">
          {{ tm("controls.cancel") }}
        </v-btn>
      </div>
      <v-alert v-if="operationError" type="error" variant="tonal" class="mt-4">
        {{ tm("runConversation.updateFailed") }}
      </v-alert>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { isAxiosError } from "axios";
import { chatApi } from "@/api/v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import type { RunConversationSummary } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";
import { accountSessionRevision, assertCurrentAccount } from "@/utils/accountSession";
import { askForConfirmation, useConfirmDialog } from "@/utils/confirmDialog";

const props = defineProps<{ runId: string; restore: () => Promise<boolean> }>();
const { tm, getRaw } = useModuleI18n("features/agent-teams");
const { tm: chatLabel } = useModuleI18n("features/chat");
const confirm = useConfirmDialog();
const account = accountSessionRevision.value;
const title = ref("");
const summary = ref<RunConversationSummary | null>(null);
const statusLabel = computed(() => {
  const status = summary.value?.status ?? "";
  return getRaw(`v2.states.${status}`) ?? status;
});
const archived = ref(false);
const unavailable = ref(false);
const operationError = ref(false);
const busy = ref(false);
const canCancel = computed(() =>
  summary.value && !["completed", "cancelled"].includes(summary.value.status),
);
let refreshTimer: ReturnType<typeof setInterval>;
let loading = false;
let disposed = false;
let cancelRequestId: string | undefined;

async function refresh() {
  if (loading || disposed) return;
  loading = true;
  try {
    const { data } = await chatApi.getRunConversation(props.runId);
    assertCurrentAccount(account);
    const session = data?.data?.session;
    if (disposed) return;
    if (data?.status !== "ok" || session?.session_type !== "workflow_run" || !session.run_conversation) {
      unavailable.value = true;
      summary.value = null;
      return;
    }
    title.value = session.display_name ?? "";
    summary.value = session.run_conversation;
    archived.value = Boolean(session.archived);
    unavailable.value = false;
  } catch (error) {
    if (disposed) return;
    const status = isAxiosError(error) ? error.response?.status : undefined;
    if (account !== accountSessionRevision.value || [401, 403, 404].includes(status ?? 0)) {
      summary.value = null;
    }
    if (!summary.value) unavailable.value = true;
  } finally {
    loading = false;
  }
}

async function restoreSession() {
  if (busy.value) return;
  busy.value = true;
  try {
    if (await props.restore()) archived.value = false;
  } finally {
    busy.value = false;
  }
}

async function cancel() {
  if (!canCancel.value || busy.value || !summary.value) return;
  const runId = summary.value.run_id;
  busy.value = true;
  operationError.value = false;
  try {
    if (!(await askForConfirmation(tm("controls.cancelHint"), confirm)) || disposed) return;
    assertCurrentAccount(account);
    cancelRequestId ??= crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    await workflowAssetsApi.control(runId, { action: "cancel", request_id: cancelRequestId });
    await refresh();
  } catch {
    if (!disposed) operationError.value = true;
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  void refresh();
  refreshTimer = setInterval(() => void refresh(), 3000);
});
onBeforeUnmount(() => {
  disposed = true;
  clearInterval(refreshTimer);
});
</script>

<style scoped>
.run-summary {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 0.5rem 1rem;
}
.run-summary dt { font-weight: 600; }
.run-summary dd { overflow-wrap: anywhere; }
</style>
