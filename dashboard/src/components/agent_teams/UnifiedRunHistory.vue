<template>
  <section aria-labelledby="unified-history-title">
    <div class="d-flex flex-wrap align-center ga-2 mb-3">
      <h2 id="unified-history-title" class="text-h6">
        {{ tm("legacyHistory.title") }}
      </h2>
      <v-btn variant="text" :loading="loading" @click="loadPage(0)">{{
        tm("page.refresh")
      }}</v-btn>
    </div>
    <p class="text-body-2 mb-4">{{ tm("legacyHistory.intro") }}</p>
    <v-alert
      v-if="error"
      type="error"
      variant="tonal"
      role="alert"
      class="mb-4"
      >{{ error }}</v-alert
    >
    <v-alert
      v-if="!loading && !records.length && !error"
      type="info"
      variant="tonal"
      >{{ tm("legacyHistory.empty") }}</v-alert
    >
    <details
      class="history-list workflow-run-list"
      :open="selected ? undefined : true"
    >
      <summary class="text-subtitle-1 mb-3">
        {{ tm("legacyHistory.recordList") }} · {{ records.length }}
      </summary>
      <v-card
        v-for="record in records"
        :key="`${record.source.kind}:${record.source.id}`"
        variant="outlined"
        class="mb-3"
      >
        <v-card-text class="d-flex flex-wrap align-center ga-3">
          <div class="history-name flex-grow-1">
            <strong>{{ record.title }}</strong>
            <div class="d-flex flex-wrap ga-2 my-2">
              <v-chip size="small" variant="tonal">{{
                tm(
                  record.engine === "legacy"
                    ? "legacyHistory.legacyEngine"
                    : "legacyHistory.v2Engine",
                )
              }}</v-chip>
              <v-chip v-if="record.read_only" size="small" variant="tonal">{{
                tm("legacyHistory.readOnly")
              }}</v-chip>
              <span
                >{{
                  tm(
                    record.source.kind === "legacy_conversation"
                      ? "legacyHistory.longTerm"
                      : "legacyHistory.run",
                  )
                }}
                ·
                {{
                  record.status === "saved"
                    ? tm("legacyHistory.saved")
                    : tm(
                        `${
                          record.engine === "legacy"
                            ? "monitor.status"
                            : "v2.states"
                        }.${record.status}`,
                      )
                }}</span
              >
            </div>
            <p class="history-text">{{ record.goal }}</p>
            <p class="text-caption history-text">
              {{ record.source.id }} · {{ record.created_at }}
            </p>
          </div>
          <v-btn
            variant="text"
            :disabled="busy"
            :data-testid="`history-open-${record.source.kind}-${record.source.id}`"
            @click="open(record)"
            >{{ tm("v2.details") }}</v-btn
          >
        </v-card-text>
      </v-card>
      <v-btn
        v-if="nextOffset !== null"
        variant="tonal"
        :loading="loading"
        data-testid="history-next"
        @click="loadPage(nextOffset)"
        >{{ tm("legacyHistory.moreRecords") }}</v-btn
      >
    </details>

    <v-card
      v-if="selected"
      variant="outlined"
      class="mt-5"
      data-testid="legacy-history-detail"
    >
      <v-card-text>
        <v-alert type="info" variant="tonal" class="mb-4"
          >{{ tm("legacyHistory.readOnly") }} ·
          {{ tm("legacyHistory.noResume") }}</v-alert
        >
        <p class="text-caption history-text">
          {{ selected.source.kind }} · {{ selected.source.id }}
        </p>
        <template v-if="selected.run">
          <h3 class="text-h6 mt-3">{{ selected.run.team_name }}</h3>
          <p class="my-2">
            {{ tm(`monitor.status.${selected.run.history_status}`) }}
          </p>
          <v-alert
            v-if="selected.run.execution_active"
            type="warning"
            variant="tonal"
            class="mb-3"
            >{{ tm("legacyHistory.stillActive") }}</v-alert
          >
          <p
            v-else-if="selected.run.history_status === 'interrupted'"
            class="text-body-2 mb-3"
          >
            {{ tm("legacyHistory.interrupted") }}
          </p>
          <p class="text-caption history-text">
            {{ tm("legacyHistory.originalStatus") }}:
            {{ selected.run.status }} · {{ selected.run.mode }} ·
            {{ selected.run.team_id }} · {{ selected.run.workflow_id }}
          </p>
          <h4 class="text-subtitle-1 mt-4">{{ tm("v2.task") }}</h4>
          <pre class="history-text">{{ selected.run.input }}</pre>
          <h4 class="text-subtitle-1 mt-4">
            {{ tm("legacyHistory.originalResult") }}
          </h4>
          <pre v-if="selected.run.result_summary" class="history-text">{{
            selected.run.result_summary
          }}</pre>
          <p v-else class="text-body-2">{{ tm("legacyHistory.noResult") }}</p>
          <details
            v-for="(state, nodeId) in selected.run.node_states"
            :key="nodeId"
            class="history-record"
          >
            <summary>
              {{ nodeId }} · {{ state.member_id }} · {{ state.status }}
            </summary>
            <pre class="history-text pa-3">{{
              JSON.stringify(state, null, 2)
            }}</pre>
          </details>
          <details class="history-record">
            <summary>{{ tm("legacyHistory.originalStructure") }}</summary>
            <pre class="history-text pa-3">{{
              JSON.stringify(
                { graph: selected.run.graph, rounds: selected.run.rounds },
                null,
                2,
              )
            }}</pre>
          </details>
          <h4 class="text-subtitle-1 mt-4">{{ tm("v2.process") }}</h4>
          <p class="text-body-2 mb-2">
            {{ tm("legacyHistory.processSource") }}
          </p>
          <v-btn
            v-for="member in selected.members"
            :key="member"
            variant="text"
            :disabled="busy"
            :data-testid="`history-member-${member}`"
            @click="loadProcess(member)"
            >{{ member }}</v-btn
          >
          <p v-if="!selected.members.length" class="text-body-2">
            {{ tm("progress.emptyProcess") }}
          </p>
          <template v-if="memberId">
            <v-btn
              v-if="beforeId !== null"
              variant="tonal"
              :disabled="busy"
              data-testid="history-more-process"
              @click="loadProcess(memberId, beforeId)"
              >{{ tm("dialog.loadMore") }}</v-btn
            >
            <details
              v-for="message in messages"
              :key="message.id"
              class="history-record"
              open
            >
              <summary>
                {{ message.direction }} · {{ message.member_id }} ·
                {{ message.node_id }} · {{ message.round }} ·
                {{ message.turn_id }}
              </summary>
              <div class="pa-3">
                <p class="text-caption history-text">
                  {{ message.run_id }} · {{ message.id }} ·
                  {{ message.created_at }}
                </p>
                <pre class="history-text my-2">{{ message.text }}</pre>
                <pre v-if="message.parts?.length" class="history-text">{{
                  JSON.stringify(message.parts, null, 2)
                }}</pre>
                <pre v-if="message.metadata" class="history-text">{{
                  JSON.stringify(message.metadata, null, 2)
                }}</pre>
              </div>
            </details>
            <p v-if="!messages.length" class="text-body-2">
              {{ tm("progress.emptyProcess") }}
            </p>
          </template>
        </template>
        <template v-else-if="selected.conversation">
          <h3 class="text-h6 my-3">
            {{ selected.conversation.title || selected.source.id }}
          </h3>
          <p class="text-body-2 mb-3">
            {{ tm("legacyHistory.longTermScope") }}
          </p>
          <p
            v-for="source in selected.source_links"
            :key="`${source.team_id}:${source.member_id}`"
            class="history-text text-caption"
          >
            {{ source.team_name }} · {{ source.member_name }} ·
            {{ source.member_id }} · {{ source.session_id }}
          </p>
          <details
            v-for="(message, index) in selected.conversation.messages"
            :key="index"
            class="history-record"
            open
          >
            <summary>{{ message.role }} · {{ index + 1 }}</summary>
            <pre class="history-text pa-3">{{
              JSON.stringify(message, null, 2)
            }}</pre>
          </details>
          <p v-if="!selected.conversation.messages.length" class="text-body-2">
            {{ tm("progress.emptyProcess") }}
          </p>
          <v-btn
            v-if="selected.conversation.next_offset !== null"
            variant="tonal"
            :disabled="busy"
            data-testid="history-more-messages"
            @click="loadConversationPage"
            >{{ tm("legacyHistory.moreMessages") }}</v-btn
          >
        </template>
        <div class="mt-4">
          <p class="text-body-2 mb-2">{{ tm("legacyHistory.freshContext") }}</p>
          <v-btn
            v-for="asset in selected.migrated_assets"
            :key="asset.asset_id"
            variant="tonal"
            data-testid="history-locate-asset"
            @click="emit('locateAsset', asset)"
            >{{ tm("legacyHistory.locateAsset") }} · {{ asset.name }} · v{{
              asset.version
            }}</v-btn
          >
          <p v-if="!selected.migrated_assets?.length" class="text-body-2">
            {{ tm("legacyHistory.noMigration") }}
          </p>
        </div>
      </v-card-text>
    </v-card>
  </section>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";

const emit = defineEmits<{
  openRun: [id: string];
  locateAsset: [
    asset: { asset_id: string; version: number; name: string; kind: string },
  ];
}>();
const { tm } = useModuleI18n("features/agent-teams");
const records = ref<any[]>([]);
const nextOffset = ref<number | null>(null);
const selected = ref<any>(null);
const messages = ref<any[]>([]);
const memberId = ref("");
const beforeId = ref<number | null>(null);
const loading = ref(false);
const busy = ref(false);
const error = ref("");
let selection = 0;
let disposed = false;

async function loadPage(offset: number) {
  if (loading.value) return;
  const read = selection;
  const record = offset === 0 ? selected.value : null;
  loading.value = true;
  error.value = "";
  try {
    const page = await workflowAssetsApi.history(offset);
    if (disposed) return;
    records.value = offset ? [...records.value, ...page.records] : page.records;
    nextOffset.value = page.next_offset;
    if (record && read === selection) await open(record, true);
  } catch (cause) {
    if (!disposed)
      error.value = extractApiError(cause, tm("errors.loadFailed")).message;
  } finally {
    if (!disposed) loading.value = false;
  }
}

async function open(record: any, refresh = false) {
  if (record.source.kind === "v2_run") {
    emit("openRun", record.source.id);
    return;
  }
  const member = refresh ? memberId.value : "";
  const read = ++selection;
  if (!refresh) selected.value = null;
  messages.value = [];
  memberId.value = "";
  beforeId.value = null;
  error.value = "";
  busy.value = true;
  try {
    const detail = await workflowAssetsApi.historyRecord(
      record.source.kind,
      record.source.id,
    );
    if (!disposed && read === selection) {
      selected.value = detail;
      if (member && detail.members?.includes(member)) await loadProcess(member);
    }
  } catch (cause) {
    if (!disposed && read === selection) {
      selected.value = null;
      error.value = extractApiError(cause, tm("errors.loadFailed")).message;
    }
  } finally {
    if (!disposed && read === selection) busy.value = false;
  }
}

async function loadProcess(member: string, before?: number) {
  const read = selection;
  const identity = selected.value.source.id;
  busy.value = true;
  error.value = "";
  try {
    const page = await workflowAssetsApi.historyProcess(
      identity,
      member,
      before,
    );
    if (disposed || read !== selection) return;
    messages.value = before
      ? [...page.messages, ...messages.value]
      : page.messages;
    memberId.value = member;
    beforeId.value = page.next_before_id;
  } catch (cause) {
    if (!disposed && read === selection)
      error.value = extractApiError(cause, tm("errors.loadFailed")).message;
  } finally {
    if (!disposed && read === selection) busy.value = false;
  }
}

async function loadConversationPage() {
  const read = selection;
  const record = selected.value;
  busy.value = true;
  error.value = "";
  try {
    const page = await workflowAssetsApi.historyRecord(
      "legacy_conversation",
      record.source.id,
      record.conversation.next_offset,
    );
    if (disposed || read !== selection) return;
    record.conversation.messages.push(...page.conversation.messages);
    record.conversation.next_offset = page.conversation.next_offset;
  } catch (cause) {
    if (!disposed && read === selection)
      error.value = extractApiError(cause, tm("errors.loadFailed")).message;
  } finally {
    if (!disposed && read === selection) busy.value = false;
  }
}

onMounted(() => loadPage(0));
onUnmounted(() => {
  disposed = true;
  selection++;
});
</script>

<style scoped>
.history-name {
  min-width: 0;
}
.history-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  max-width: 100%;
}
.history-record {
  margin-top: 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.18);
  border-radius: 8px;
}
.history-record summary {
  padding: 12px;
  cursor: pointer;
  overflow-wrap: anywhere;
}
</style>
