<template>
  <div class="run-monitor">
    <div class="monitor-toolbar">
      <v-textarea
        v-model="goal"
        :label="tm('monitor.input')"
        :placeholder="tm('monitor.inputPlaceholder')"
        rows="1"
        auto-grow
        density="compact"
        hide-details
        class="monitor-goal"
      />
      <!-- Auto orchestration needs no workflow, so the select only shows
           for DAG runs (the backend rejects an empty workflow id there). -->
      <v-select
        v-if="mode === 'dag'"
        v-model="workflowId"
        :items="workflowItems"
        item-title="title"
        item-value="value"
        :label="tm('monitor.workflow')"
        density="compact"
        hide-details
        class="monitor-workflow"
      />
      <v-select
        v-model="mode"
        :items="modeItems"
        item-title="title"
        item-value="value"
        density="compact"
        hide-details
        class="monitor-mode"
      />
      <v-chip v-if="statusLabel" size="small" variant="tonal" class="monitor-status-chip">
        {{ statusLabel }}
      </v-chip>
      <v-spacer />
      <div class="monitor-actions">
        <v-btn
          v-if="canStart"
          variant="tonal"
          color="primary"
          :loading="starting"
          :disabled="!goal.trim() || (mode === 'dag' && !workflowId)"
          @click="onStart"
        >
          {{ tm('monitor.start') }}
        </v-btn>
        <template v-if="runState?.status === 'running'">
          <v-btn variant="text" @click="onPause">{{ tm('monitor.pause') }}</v-btn>
          <v-btn variant="text" color="error" @click="onStop">{{ tm('monitor.stop') }}</v-btn>
        </template>
        <template v-if="runState?.status === 'paused'">
          <v-btn variant="text" @click="onResume">{{ tm('monitor.resume') }}</v-btn>
          <v-btn variant="text" color="error" @click="onStop">{{ tm('monitor.stop') }}</v-btn>
          <template v-if="runState.pausedNodeId">
            <v-btn variant="text" color="warning" @click="onRetry">
              {{ tm('monitor.retryNode') }}
            </v-btn>
            <v-btn variant="text" color="warning" @click="onSkip">
              {{ tm('monitor.skipNode') }}
            </v-btn>
          </template>
        </template>
        <v-btn
          v-if="runState?.status === 'interrupted'"
          variant="tonal"
          color="primary"
          @click="onResumeInterrupted"
        >
          {{ tm('monitor.resumeInterrupted') }}
        </v-btn>
      </div>
    </div>

    <!-- Run-level error banner: folded lastError / non-benign stoppedReason.
         Normal completion ("done") and user stops never render it. -->
    <div v-if="bannerMessage" class="monitor-error-banner">
      <span class="monitor-error-banner-label">{{ tm('monitor.runError') }}</span>
      <span class="monitor-error-banner-text">{{ bannerMessage }}</span>
      <v-spacer />
      <v-btn
        icon="mdi-close"
        variant="text"
        size="small"
        class="monitor-error-dismiss"
        @click="bannerDismissed = true"
      />
    </div>

    <div v-if="busyCount" class="monitor-busy-hint">{{ tm('monitor.busy') }}</div>
    <div v-if="!runState" class="monitor-empty">{{ tm('monitor.empty') }}</div>

    <!-- Auto runs have no workflow graph: round chip + dispatch log replace
         the DAG view, the window grid stays for both modes. -->
    <div v-if="isAuto && runState" class="monitor-auto-panel">
      <v-chip size="small" variant="tonal" class="monitor-round-chip">
        {{ roundLabel }}
      </v-chip>
      <div v-if="dispatchLines.length" class="monitor-dispatch-log">
        <span class="monitor-dispatch-title">{{ tm('monitor.dispatchLog') }}</span>
        <div v-for="(line, i) in dispatchLines" :key="i" class="monitor-dispatch-line">
          {{ line }}
        </div>
      </div>
    </div>

    <div v-if="!isAuto" class="monitor-viewbar">
      <v-btn-toggle v-model="view" mandatory density="comfortable">
        <v-btn value="grid">{{ tm('monitor.viewWindow') }}</v-btn>
        <v-btn value="dag">{{ tm('monitor.viewDag') }}</v-btn>
      </v-btn-toggle>
    </div>

    <div v-if="view === 'grid' || isAuto" class="monitor-grid">
      <GridLayout
        v-model:layout="layout"
        :col-num="12"
        :row-height="48"
        is-draggable
        is-resizable
        class="monitor-grid-layout"
        @update:layout="onLayoutUpdate"
      >
        <GridItem
          v-for="item in layout"
          :key="item.i"
          :x="item.x"
          :y="item.y"
          :w="item.w"
          :h="item.h"
          :i="item.i"
        >
          <AgentWindow
            :member="memberById(item.i)"
            :window="runState?.windows[item.i] ?? null"
            :node-status="memberNodeStatus(item.i)"
            :node-error="memberNodeError(item.i)"
            :busy="isMemberBusy(item.i)"
            @expand="openMemberDialog(item.i)"
          />
        </GridItem>
      </GridLayout>
    </div>
    <div v-else class="monitor-dag">
      <div v-if="runState" class="monitor-dag-progress">
        <span class="monitor-dag-label">
          {{ tm('monitor.dagProgress') }} {{ progressText }}
        </span>
        <v-progress-linear :model-value="progressPercent" height="8" rounded color="primary" />
      </div>
      <TeamsFlowCanvas
        mode="monitor"
        :nodes="dagNodes"
        :edges="dagEdges"
        :node-states="runState?.nodeStates ?? null"
      />
    </div>
  </div>
  <MemberTranscriptDialog
    v-if="dialogMemberId && runState"
    :model-value="dialogOpen"
    :run-id="runState.runId"
    :member-id="dialogMemberId"
    :member-name="memberById(dialogMemberId).name"
    :umo="memberUmo(dialogMemberId)"
    :timeline="runState.windows[dialogMemberId]?.timeline ?? []"
    :has-more="false"
    @update:model-value="dialogOpen = $event"
    @send="onDialogSend"
    @interrupt="onDialogInterrupt"
  />
</template>

<script setup lang="ts">
// Run monitor (Task 8): control bar (goal / workflow / mode / status-driven
// actions), a resizable per-member window grid whose tile layout is persisted
// per team in localStorage, and a DAG view (TeamsFlowCanvas in monitor mode +
// progress strip). On mount the active-run list is checked and an existing run
// for this team is reopened, so a page reload resumes the live monitor.
import { computed, onMounted, ref, watch } from 'vue';
import { GridLayout, GridItem } from 'grid-layout-plus';
import AgentWindow from './AgentWindow.vue';
import MemberTranscriptDialog from './MemberTranscriptDialog.vue';
import TeamsFlowCanvas from './TeamsFlowCanvas.vue';
import type { FlowEdgePayload, FlowNode } from './TeamsFlowCanvas.vue';
import type { TeamsRunStateSeed } from '@/composables/agentTeamsRunReducer';
import { useAgentTeams } from '@/composables/useAgentTeams';
import { useAgentTeamsRun, type AgentTeamRunSummary } from '@/composables/useAgentTeamsRun';
import { useModuleI18n } from '@/i18n/composables';
import { chatApi } from '@/api/v1';
import { extractApiError } from '@/utils/extractApiError';
import { useToast } from '@/utils/toast';
import { collabMemberColor } from '@/utils/memberColors';

interface MonitorTile {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

const props = defineProps<{
  /** Team whose run is monitored; members drive the window grid. */
  team: any | null;
  /**
   * History row the page explicitly opened via `openRun(runId, row)` before
   * mounting this panel (history-tab handoff). When the attached run matches
   * this row, mount recovery is skipped (it must not clobber the explicitly
   * opened run) and the DAG is seeded from the row's graph snapshot.
   */
  initialRun?: any | null;
}>();

const { tm } = useModuleI18n('features/agent-teams');
const { error: toastError } = useToast();
const { workflows, loadWorkflows } = useAgentTeams();
const {
  runState,
  loadActiveRuns,
  openRun,
  closeRun,
  startRun,
  pause,
  resumeRun,
  stop,
  retryNode,
  skipNode,
  interruptMember,
  setOnAttachFailed,
} = useAgentTeamsRun();

// Reconnect exhaustion inside the composable is invisible to users (console
// only); surface it here. The composable is a module singleton, so the hook
// is re-registered on every mount of this panel.
setOnAttachFailed(() => {
  toastError(tm('monitor.attachFailed'));
});

// ----- control bar state -----
const goal = ref('');
const workflowId = ref('');
const mode = ref('dag');
const view = ref<'grid' | 'dag'>('grid');
const starting = ref(false);

// Graph snapshot of the attached run: seeded by recovery rows and fresh starts
// (the reducer state carries node states but deliberately not the graph).
const runGraph = ref<{ nodes?: any[]; edges?: any[] } | null>(null);

// DAG runs always execute a workflow graph (the backend rejects an empty
// workflow id), so there is deliberately no "none" option here: the select
// defaults to the team's first workflow and is hidden entirely for auto runs
// (auto orchestration is goal-driven and needs no workflow).
const workflowItems = computed(() =>
  workflows.value.map((w) => ({
    title: String(w.name || w.workflow_id),
    value: String(w.workflow_id),
  })),
);

// Re-default the selection whenever the loaded team's workflow list changes
// (initial load, team switch) and the current pick is no longer valid.
watch(
  workflowItems,
  (items) => {
    if (!items.some((it) => it.value === workflowId.value)) {
      workflowId.value = items[0]?.value ?? '';
    }
  },
  { immediate: true },
);

// 'auto' dispatches to the backend's round-based AutoOrchestrator; 'dag'
// replays the team's workflow graph.
const modeItems = [
  { title: 'DAG', value: 'dag' },
  { title: 'Auto', value: 'auto' },
];

// Statuses where starting a fresh run is allowed (detached or terminal).
// The workflow requirement gates the button's `disabled` state (visible but
// inert until a workflow is picked), not its visibility.
const STARTABLE_STATUSES = ['idle', 'completed', 'stopped', 'failed'];
const canStart = computed(
  () => !runState.value || STARTABLE_STATUSES.includes(runState.value.status),
);

// Chip label only for statuses with an i18n key (the optimistic 'stopping'
// state has none and shows nothing instead of a MISSING placeholder).
const STATUS_KEYS = ['idle', 'running', 'paused', 'stopped', 'completed', 'failed', 'interrupted'];
const statusLabel = computed(() => {
  const status = runState.value?.status;
  return status && STATUS_KEYS.includes(status) ? tm(`monitor.status.${status}`) : '';
});

const busyCount = computed(() => runState.value?.busySessionIds.size ?? 0);

// ----- run-error banner -----
// Stop reasons that mean normal completion, not a failure.
const BENIGN_STOP_REASONS = ['done', 'user stop'];

const bannerDismissed = ref(false);

/**
 * Run-level error to surface in the banner: the folded `lastError`, else a
 * `stoppedReason` that is not a benign completion ("done" / "user stop").
 */
const bannerError = computed(() => {
  const state = runState.value;
  if (!state) return null;
  if (state.lastError) return state.lastError;
  if (state.stoppedReason && !BENIGN_STOP_REASONS.includes(state.stoppedReason)) {
    return state.stoppedReason;
  }
  return null;
});

const bannerMessage = computed(() => (bannerDismissed.value ? null : bannerError.value));

// A freshly opened run state must show its own banner even when the previous
// one was dismissed (folds mutate in place, so only reassignment triggers).
watch(runState, () => {
  bannerDismissed.value = false;
});

const progressPercent = computed(() => {
  const p = runState.value?.progress;
  if (!p || !p.total) return 0;
  return Math.round(((p.done + p.skipped) / p.total) * 100);
});

const progressText = computed(() => {
  const p = runState.value?.progress;
  if (!p) return '';
  return `${p.done + p.skipped} / ${p.total}`;
});

// ----- auto mode display -----
// The attached run is the backend's round-based orchestrator: node_states
// stays empty and the round/dispatch events drive the display instead.
const isAuto = computed(() => runState.value?.mode === 'auto');

// Round limit from the monitored team's config, matching the backend default.
const maxRounds = computed(() => Number(props.team?.config?.max_rounds) || 20);

const roundLabel = computed(() =>
  tm('monitor.roundLabel', { n: runState.value?.round?.n ?? 0, max: maxRounds.value }),
);

// Compact log lines for the last 5 dispatches: `#<round> <member>: <task>`,
// with task text truncated at 40 chars.
const dispatchLines = computed(() =>
  (runState.value?.dispatches ?? []).slice(-5).flatMap((d) =>
    d.assignments.map((a) => {
      const task = a.task.length > 40 ? `${a.task.slice(0, 40)}…` : a.task;
      return `#${d.round} ${a.member}: ${task}`;
    }),
  ),
);

// ----- actions -----
async function onStart() {
  // Guard mirrors the button's disabled state (defense in depth); only DAG
  // runs require a picked workflow.
  if (!props.team || !goal.value.trim() || (mode.value === 'dag' && !workflowId.value)) return;
  starting.value = true;
  try {
    const isDag = mode.value === 'dag';
    const snapshot = await startRun(props.team.team_id, {
      mode: mode.value,
      input: goal.value.trim(),
      workflow_id: isDag ? workflowId.value : null,
    });
    if (snapshot && isDag) {
      // DAG runs always execute a workflow graph — reuse the selected row's.
      const wf = workflows.value.find((w) => String(w.workflow_id) === workflowId.value);
      runGraph.value = (wf?.graph as typeof runGraph.value) ?? null;
    }
  } finally {
    starting.value = false;
  }
}

function onPause() {
  if (runState.value) void pause(runState.value.runId);
}

function onResume() {
  if (runState.value) void resumeRun(runState.value.runId);
}

function onStop() {
  if (runState.value) void stop(runState.value.runId);
}

function onResumeInterrupted() {
  if (runState.value) void resumeRun(runState.value.runId);
}

// retryNode/skipNode resolve the attached run inside the composable.
function onRetry() {
  if (runState.value?.pausedNodeId) void retryNode(runState.value.pausedNodeId);
}

function onSkip() {
  if (runState.value?.pausedNodeId) void skipNode(runState.value.pausedNodeId);
}

// ----- window grid -----
const layout = ref<MonitorTile[]>([]);

function storageKey(teamId: string) {
  return `agent-teams-grid-${teamId}`;
}

/**
 * Default tile packing: ceil(sqrt(n)) columns, tile width 12/cols (min 3),
 * height 6, x/y staggered by index row by row.
 *
 * Args:
 *   members: Team members to pack.
 *
 * Returns:
 *   One tile per member keyed by member_id.
 */
function defaultTiles(members: any[]): MonitorTile[] {
  const n = members.length;
  if (!n) return [];
  const cols = Math.max(1, Math.ceil(Math.sqrt(n)));
  const w = Math.max(3, Math.floor(12 / cols));
  return members.map((m, i) => ({
    i: String(m.member_id),
    x: (i % cols) * w,
    y: Math.floor(i / cols) * 6,
    w,
    h: 6,
  }));
}

function coerceTile(tile: any): MonitorTile {
  return {
    i: String(tile.i),
    x: Number(tile.x) || 0,
    y: Number(tile.y) || 0,
    w: Number(tile.w) || 6,
    h: Number(tile.h) || 6,
  };
}

/**
 * Seed the layout from the per-team localStorage entry, keeping saved tiles
 * for members that still exist and appending defaults for new ones
 * (vertical compacting resolves overlaps in grid-layout-plus).
 */
function loadLayout() {
  const members = props.team?.members ?? [];
  let saved: unknown = null;
  try {
    const raw = props.team?.team_id ? localStorage.getItem(storageKey(props.team.team_id)) : null;
    saved = raw ? JSON.parse(raw) : null;
  } catch {
    saved = null;
  }
  const ids = new Set(members.map((m: any) => String(m.member_id)));
  const restored = (Array.isArray(saved) ? saved : []).filter(
    (tile: any) => tile && ids.has(String(tile.i)),
  );
  const known = new Set(restored.map((tile: any) => String(tile.i)));
  const missing = members.filter((m: any) => !known.has(String(m.member_id)));
  layout.value = [...restored.map(coerceTile), ...defaultTiles(missing)];
}

/** Persist a layout update emitted by the grid (best-effort). */
function onLayoutUpdate(next: MonitorTile[]) {
  if (!props.team?.team_id) return;
  try {
    localStorage.setItem(storageKey(props.team.team_id), JSON.stringify(next));
  } catch {
    // Storage unavailable/full: persistence is best-effort.
  }
}

function memberById(memberId: string): any {
  return (
    (props.team?.members ?? []).find((m: any) => String(m.member_id) === memberId) ?? {
      member_id: memberId,
      name: memberId,
    }
  );
}

function isMemberBusy(memberId: string): boolean {
  const sessionId = memberById(memberId)?.session_id;
  return sessionId ? (runState.value?.busySessionIds.has(String(sessionId)) ?? false) : false;
}

// Node status for the window header: prefer the member's live node, then a
// failed one, else the most recent status in event order.
function memberNodeStatus(memberId: string): string | undefined {
  const nodeIds = new Set(
    dagNodes.value.filter((n) => n.data.memberId === memberId).map((n) => n.id),
  );
  if (!nodeIds.size) return undefined;
  const statuses = Object.entries(runState.value?.nodeStates ?? {})
    .filter(([nodeId]) => nodeIds.has(nodeId))
    .map(([, ns]) => ns.status);
  if (!statuses.length) return undefined;
  return (
    statuses.find((s) => s === 'running') ??
    statuses.find((s) => s === 'failed') ??
    statuses[statuses.length - 1]
  );
}

// Node statuses that carry a backend error text for the window error block.
const NODE_ERROR_STATUSES = ['failed', 'interrupted'];

/**
 * Backend error text of the member's most recent failed node, for the inline
 * AgentWindow error block (dag mode: nodes reference members via the run
 * graph). Auto mode v1 has no per-member nodes, so its windows never show a
 * node error — the run-level banner covers it.
 */
function memberNodeError(memberId: string): string | null {
  if (isAuto.value) return null;
  const nodeIds = new Set(
    dagNodes.value.filter((n) => n.data.memberId === memberId).map((n) => n.id),
  );
  if (!nodeIds.size) return null;
  const errored = Object.entries(runState.value?.nodeStates ?? {}).filter(
    ([nodeId, ns]) =>
      nodeIds.has(nodeId) && !!ns.error && NODE_ERROR_STATUSES.includes(ns.status),
  );
  if (!errored.length) return null;
  return errored[errored.length - 1][1].error ?? null;
}

// ----- DAG view -----
// Positions are staggered (run graph snapshots carry no layout map); the
// canvas fits the view on init anyway. Node data is enriched for the
// MemberFlowNode card: member color, folded run status/error, task preview
// (task_rendered when the node already ran, else the raw template) and the
// inbound edge count. Config/persona chips are omitted in monitor v1 — they
// are not part of node_states.
const dagNodes = computed<FlowNode[]>(() => {
  const edges = runGraph.value?.edges ?? [];
  const states = runState.value?.nodeStates ?? {};
  return (runGraph.value?.nodes ?? []).map((n: any, i: number) => {
    const id = String(n.id ?? `n${i}`);
    const memberId = String(n.member_id ?? '');
    const memberName = String(memberById(memberId).name ?? memberId);
    const state = states[id];
    const rawTask = String(n.task_rendered ?? n.task ?? '');
    return {
      id,
      position: { x: 60 + (i % 3) * 220, y: 50 + Math.floor(i / 3) * 140 },
      data: {
        label: `${memberName} (${id})`,
        memberName,
        memberId,
        memberColor: collabMemberColor(memberName),
        task: String(n.task ?? ''),
        taskPreview: rawTask.length > 60 ? `${rawTask.slice(0, 60)}…` : rawTask,
        status: state?.status,
        error: state?.error || undefined,
        inDegree: edges.filter((e: any) => String(e.to) === id).length,
        interactive: true,
      },
    };
  });
});

const dagEdges = computed<FlowEdgePayload[]>(() =>
  (runGraph.value?.edges ?? []).map((e: any) => ({
    id: `e:${e.from}->${e.to}`,
    source: String(e.from),
    target: String(e.to),
  })),
);

// ----- mount / team recovery -----
/**
 * Reopen the team's active run if one exists: the run row (snake_case
 * graph/node_states) doubles as the reducer seed and feeds the DAG view until
 * SSE events refresh the state.
 *
 * A run explicitly opened from the history tab (the page calls
 * `openRun(runId, row)` before switching to this tab and passes the row down
 * as `initialRun`) is kept as-is: the recovery below must not clobber it, so
 * only the DAG graph is seeded from the handed-off row.
 */
async function initTeam() {
  runGraph.value = null;
  if (runState.value && props.initialRun?.run_id === runState.value.runId) {
    runGraph.value = (props.initialRun.graph as typeof runGraph.value) ?? null;
    return;
  }
  const runs = await loadActiveRuns();
  const row: AgentTeamRunSummary | undefined = (runs ?? []).find(
    (r) => props.team && r?.team_id && r.team_id === props.team.team_id,
  );
  if (row) {
    runGraph.value = (row.graph as typeof runGraph.value) ?? null;
    await openRun(row.run_id, row as TeamsRunStateSeed);
  } else {
    // The composable is a module singleton: without this, a run attached
    // while another team was monitored (or before this mount) would stay
    // attached here — stale status chip, 暂停/停止 targeting the wrong run
    // and 开始运行 blocked for this team.
    closeRun();
  }
}

// Seed the grid synchronously so tiles render on first paint.
loadLayout();

onMounted(() => {
  void initTeam();
  if (props.team?.team_id) void loadWorkflows(props.team.team_id);
});

// Switching teams closes the previously attached run and re-recovers.
watch(
  () => props.team?.team_id,
  (teamId) => {
    closeRun();
    loadLayout();
    void initTeam();
    if (teamId) void loadWorkflows(teamId);
  },
);

// Plan 3 T7: per-member transcript dialog (timeline + speak + interrupt).
const dialogOpen = ref(false);
const dialogMemberId = ref<string | null>(null);

/** Open the transcript dialog for one member (AgentWindow expand button). */
function openMemberDialog(memberId: string) {
  dialogMemberId.value = memberId;
  dialogOpen.value = true;
}

/**
 * Resolve a member umo for the choice-box binding and the run stream.
 *
 * Members created by the Teams service carry their umo; fall back to the
 * session id so an older row still renders (the choice store keys by umo).
 */
function memberUmo(memberId: string): string {
  const m = (props.team?.members ?? []).find((x: any) => x.member_id === memberId);
  return m?.umo || m?.session_id || memberId;
}

/**
 * Send a mid-run instruction to the member session.
 *
 * This is the ordinary chat send path (spec §4.4): while the member's turn is
 * in flight the backend captures the message as a follow-up and merges it into
 * that turn; when the member is idle it starts a new turn. Failures keep the
 * dialog open and surface the backend reason.
 */
async function onDialogSend(text: string) {
  const memberId = dialogMemberId.value;
  if (!memberId) return;
  const member = (props.team?.members ?? []).find(
    (x: any) => x.member_id === memberId,
  );
  const sessionId = member?.session_id;
  if (!sessionId) return;
  try {
    await chatApi.send({ message: text, session_id: sessionId });
  } catch (err) {
    toastError(extractApiError(err, tm('errors.operationFailed')).message);
  }
}

/**
 * Interrupt the member's in-flight turn.
 *
 * The node lands in the independent `interrupted` state and the run pauses;
 * retry/skip stay available (spec §4.3). The composable toasts failures.
 */
async function onDialogInterrupt() {
  const memberId = dialogMemberId.value;
  if (!memberId) return;
  await interruptMember(memberId);
}
</script>

<style scoped>
.run-monitor {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.monitor-toolbar {
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 12px;
}

.monitor-goal {
  flex: 1;
  min-width: 260px;
}

.monitor-workflow {
  width: 220px;
}

.monitor-mode {
  width: 160px;
}

.monitor-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  min-height: 40px;
}

.monitor-busy-hint {
  padding: 6px 12px;
  border: 1px solid rgba(250, 204, 21, 0.5);
  border-radius: 8px;
  background: rgba(250, 204, 21, 0.12);
  color: inherit;
  font-size: 13px;
}

/* Run-level error banner: red tint, dismissible. */
.monitor-error-banner {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid rgba(248, 113, 113, 0.45);
  border-radius: 8px;
  background: rgba(248, 113, 113, 0.12);
  color: inherit;
  font-size: 13px;
}

.monitor-error-banner-label {
  font-weight: 600;
  color: #f87171;
  white-space: nowrap;
}

.monitor-error-banner-text {
  flex: 1;
  min-width: 120px;
  word-break: break-word;
}

.monitor-error-dismiss {
  align-self: center;
  flex: none;
}

.monitor-empty {
  padding: 18px;
  border: 1px dashed var(--dashboard-border, rgba(128, 128, 128, 0.3));
  border-radius: 10px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.7));
  font-size: 13px;
  text-align: center;
}

.monitor-viewbar {
  display: flex;
  align-items: center;
}

.monitor-auto-panel {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.monitor-dispatch-log {
  flex: 1;
  min-width: 240px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 12px;
  border: 1px dashed var(--dashboard-border, rgba(128, 128, 128, 0.3));
  border-radius: 8px;
}

.monitor-dispatch-title {
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
  font-size: 12px;
}

.monitor-dispatch-line {
  font-size: 13px;
  line-height: 1.6;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.monitor-grid-layout {
  min-height: 320px;
}

.monitor-grid {
  /* grid-layout-plus needs an explicit height context to compute tiles. */
  min-height: 320px;
}

.monitor-dag {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.monitor-dag-progress {
  display: flex;
  align-items: center;
  gap: 12px;
}

.monitor-dag-label {
  flex: none;
  font-size: 13px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
  white-space: nowrap;
}

.monitor-dag :deep(.teams-flow-canvas) {
  min-height: 420px;
}
</style>
