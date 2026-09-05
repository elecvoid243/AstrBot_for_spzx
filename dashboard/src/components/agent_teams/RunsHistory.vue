<template>
  <div class="runs-history">
    <div class="history-head">{{ tm('history.title') }}</div>

    <div v-if="loading" class="history-loading">
      <v-progress-circular indeterminate size="24" width="2" />
    </div>

    <div v-else-if="runs.length === 0" class="history-empty">{{ tm('history.empty') }}</div>

    <template v-else>
      <v-card
        v-for="row in runs"
        :key="row.run_id"
        variant="outlined"
        class="history-row"
      >
        <div class="history-row-main">
          <div class="history-meta">
            <span class="history-time">{{ formatTime(row.updated_at) }}</span>
            <v-chip size="x-small" variant="tonal" label>{{ row.mode }}</v-chip>
            <v-chip size="x-small" variant="tonal" label :color="statusColor(row.status)">
              {{ statusText(row.status) }}
            </v-chip>
          </div>
          <div class="history-input" :title="row.input">{{ truncateInput(row.input) }}</div>
        </div>
        <v-btn
          variant="text"
          color="primary"
          size="small"
          class="history-open"
          @click="emit('open', row.run_id, row)"
        >
          {{ tm('history.open') }}
        </v-btn>
      </v-card>
    </template>
  </div>
</template>

<script setup lang="ts">
// Runs history panel (Task 9): lists a team's past runs and hands a row back
// to the page via `open(runId, row)` — the row doubles as the reducer seed,
// so the monitor can render the DAG/progress instantly while the SSE stream
// replays the event history on top.
import { ref, watch } from 'vue';
import { agentTeamsApi } from '@/api/v1';
import { useModuleI18n } from '@/i18n/composables';
import { useToast } from '@/utils/toast';

const props = defineProps<{
  /** Team whose run history is listed; switching teams reloads the rows. */
  team: any | null;
}>();

const emit = defineEmits<{
  (e: 'open', runId: string, row: any): void;
}>();

const { tm } = useModuleI18n('features/agent-teams');
const { error: toastError } = useToast();

const runs = ref<any[]>([]);
const loading = ref(false);

// Status -> chip color (semantic Vuetify palette, matching the page chips).
const STATUS_COLORS: Record<string, string> = {
  running: 'primary',
  paused: 'warning',
  completed: 'success',
  stopped: 'grey',
  failed: 'error',
  interrupted: 'warning',
};

// Statuses with an i18n label (monitor.status.*); unknown ones show raw.
const STATUS_LABEL_KEYS = [
  'idle',
  'running',
  'paused',
  'stopped',
  'completed',
  'failed',
  'interrupted',
];

function statusColor(status: unknown): string | undefined {
  return STATUS_COLORS[String(status ?? '')];
}

function statusText(status: unknown): string {
  const statusStr = String(status ?? '');
  return STATUS_LABEL_KEYS.includes(statusStr) ? tm(`monitor.status.${statusStr}`) : statusStr;
}

/** Format an ISO timestamp locale-aware (same convention as ProjectView). */
function formatTime(value: unknown): string {
  if (!value) return '';
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

/** Truncate the run input for the list row; the full text stays a tooltip. */
function truncateInput(value: unknown): string {
  const text = String(value ?? '');
  return text.length > 80 ? `${text.slice(0, 80)}…` : text;
}

/** Load the team's run rows; error envelopes are toasted, never thrown. */
async function loadHistory() {
  const teamId = props.team?.team_id;
  if (!teamId) {
    runs.value = [];
    return;
  }
  loading.value = true;
  try {
    const res = await agentTeamsApi.listTeamRuns(teamId);
    if (res.data.status === 'error') {
      toastError(res.data.message || tm('errors.loadFailed'));
      runs.value = [];
      return;
    }
    runs.value = res.data.data?.runs ?? [];
  } catch (err) {
    toastError(err instanceof Error ? err.message : String(err));
    runs.value = [];
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.team?.team_id,
  () => {
    void loadHistory();
  },
  { immediate: true },
);
</script>

<style scoped>
.runs-history {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.history-head {
  font-size: 15px;
  font-weight: 600;
}

.history-loading,
.history-empty {
  padding: 18px;
  border: 1px dashed var(--dashboard-border, rgba(128, 128, 128, 0.3));
  border-radius: 10px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.7));
  font-size: 13px;
  text-align: center;
}

.history-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
}

.history-row-main {
  flex: 1;
  min-width: 0;
}

.history-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.history-time {
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
  font-size: 12px;
}

.history-input {
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.history-open {
  flex-shrink: 0;
}
</style>
