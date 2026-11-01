<template>
  <section aria-labelledby="run-history-title">
    <div class="d-flex flex-wrap align-center ga-2 mb-3">
      <h2 id="run-history-title" class="text-h6">
        {{ tm("runHistory.title") }}
      </h2>
      <v-btn variant="text" :loading="loading" @click="loadPage(0)">{{
        tm("page.refresh")
      }}</v-btn>
    </div>
    <p class="text-body-2 mb-4">{{ tm("runHistory.intro") }}</p>
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
      >{{ tm("runHistory.empty") }}</v-alert
    >
    <details class="history-list workflow-run-list" open>
      <summary class="text-subtitle-1 mb-3">
        {{ tm("runHistory.recordList") }} · {{ records.length }}
      </summary>
      <v-card
        v-for="record in records"
        :key="record.source.id"
        variant="outlined"
        class="mb-3"
      >
        <v-card-text class="d-flex flex-wrap align-center ga-3">
          <div class="history-name flex-grow-1">
            <strong>{{ record.title }}</strong>
            <div class="d-flex flex-wrap ga-2 my-2">
              <span>{{ tm(`v2.states.${record.status}`) }}</span>
            </div>
            <p class="history-text">{{ record.goal }}</p>
            <p class="text-caption history-text">
              {{ record.source.id }} · {{ record.created_at }}
            </p>
          </div>
          <v-btn
            variant="text"
            :data-testid="`history-open-${record.source.id}`"
            @click="emit('openRun', record.source.id)"
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
        >{{ tm("runHistory.moreRecords") }}</v-btn
      >
    </details>
  </section>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";

const emit = defineEmits<{
  openRun: [id: string];
}>();
const { tm } = useModuleI18n("features/agent-teams");
const records = ref<any[]>([]);
const nextOffset = ref<number | null>(null);
const loading = ref(false);
const error = ref("");
let disposed = false;

async function loadPage(offset: number) {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    const page = await workflowAssetsApi.history(offset);
    if (disposed) return;
    const runs = page.records.filter(
      (record: any) => record.source.kind === "v2_run",
    );
    records.value = offset ? [...records.value, ...runs] : runs;
    nextOffset.value = page.next_offset;
  } catch (cause) {
    if (!disposed)
      error.value = extractApiError(cause, tm("errors.loadFailed")).message;
  } finally {
    if (!disposed) loading.value = false;
  }
}

onMounted(() => loadPage(0));
onUnmounted(() => {
  disposed = true;
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
</style>
