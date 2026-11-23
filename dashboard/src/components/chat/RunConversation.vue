<template>
  <section class="pa-6 overflow-y-auto" :aria-label="tm('runConversation.type')">
    <v-alert v-if="unavailable" type="error" variant="tonal">
      {{ tm("runConversation.unavailable") }}
    </v-alert>
    <div v-else-if="!summary">
      <v-alert v-if="loadError" type="error" variant="tonal">
        {{ tm("errors.operationFailed") }}
        <v-btn variant="text" @click="loadSession">{{ tm("page.refresh") }}</v-btn>
      </v-alert>
      <v-progress-circular v-else indeterminate size="24" />
    </div>
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
      </div>
      <WorkflowWorkspacePanel
        class="agent-teams-page mt-4"
        mode="runs"
        :conversation-run-id="runId"
        @run-status="status = $event"
        @run-unavailable="unavailable = true"
      />
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import WorkflowWorkspacePanel from "@/components/agent_teams/WorkflowWorkspacePanel.vue";
import "@/styles/agent-teams-workbench.css";
import { isAxiosError } from "axios";
import { chatApi } from "@/api/v1";
import type { RunConversationSummary } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";
import { accountSessionRevision, assertCurrentAccount } from "@/utils/accountSession";

const props = defineProps<{ runId: string; restore: () => Promise<boolean> }>();
const { tm, getRaw } = useModuleI18n("features/agent-teams");
const { tm: chatLabel } = useModuleI18n("features/chat");
const account = accountSessionRevision.value;
const title = ref("");
const summary = ref<RunConversationSummary | null>(null);
const status = ref("");
const statusLabel = computed(() => getRaw(`v2.states.${status.value}`) ?? status.value);
const archived = ref(false);
const unavailable = ref(false);
const loadError = ref(false);
const busy = ref(false);
const observation = new AbortController();
let loading = false;

async function loadSession() {
  if (loading || observation.signal.aborted) return;
  loading = true;
  loadError.value = false;
  try {
    const { data } = await chatApi.getRunConversation(props.runId, observation.signal);
    assertCurrentAccount(account);
    const session = data?.data?.session;
    if (observation.signal.aborted) return;
    if (data?.status !== "ok" || session?.session_type !== "workflow_run" || session.run_conversation?.run_id !== props.runId) {
      unavailable.value = true;
      summary.value = null;
      return;
    }
    title.value = session.display_name ?? "";
    summary.value = session.run_conversation;
    status.value = session.run_conversation.status;
    archived.value = Boolean(session.archived);
    unavailable.value = false;
  } catch (error) {
    if (observation.signal.aborted) return;
    const httpStatus = isAxiosError(error) ? error.response?.status : undefined;
    unavailable.value = [401, 403, 404].includes(httpStatus ?? 0);
    loadError.value = !unavailable.value;
  } finally {
    loading = false;
  }
}

async function restoreSession() {
  if (busy.value) return;
  busy.value = true;
  try {
    if (await props.restore() && !observation.signal.aborted) archived.value = false;
  } finally {
    busy.value = false;
  }
}

watch(accountSessionRevision, () => {
  observation.abort();
  unavailable.value = true;
  summary.value = null;
}, { flush: "sync" });
onMounted(loadSession);
onBeforeUnmount(() => observation.abort());
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
