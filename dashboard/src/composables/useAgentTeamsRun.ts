// Module-singleton composable hosting the agent-teams run monitor: one
// attached run, one SSE connection (shared by the monitor panel, team page
// and run dialogs). SSE payloads are folded into the pure reducer state from
// agentTeamsRunReducer; control actions (start/pause/stop/retry/skip) wrap
// the API facade and toast error envelopes instead of throwing.
import { ref } from 'vue';
import type { AxiosResponse } from 'axios';
import { fetchWithAuth } from '@/api/http';
import { agentTeamsApi } from '@/api/v1';
import type { ApiEnvelope } from '@/api/v1';
import { extractApiError } from '@/utils/extractApiError';
import { useToast } from '@/utils/toast';
import { readSseStream } from '@/utils/sseReader';
import {
  applyTeamsEvent,
  createTeamsRunState,
  parseTeamsEvent,
  type TeamsRunState,
  type TeamsRunStateSeed,
} from './agentTeamsRunReducer';

export interface AgentTeamRunSummary {
  run_id: string;
  status?: string;
  team_id?: string;
  node_states?: Record<string, { status: string; error?: string }>;
  graph?: unknown;
  [key: string]: unknown;
}

const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY_MS = 1000;
// Statuses where a closed stream is normal (the backend ends the connection
// when the run finishes); no reconnect is attempted afterwards.
const TERMINAL_RUN_STATUSES = ['completed', 'stopped', 'failed'];

// Invoked when the SSE reconnect attempts for a run are exhausted. The attach
// loop lives at module level (singleton composable), so the hook is a module
// field too; UI hosts register it (e.g. RunMonitor toasts the failure).
let onAttachFailed: ((runId: string) => void) | null = null;

/**
 * Register (or clear) the handler invoked when stream reconnection fails.
 *
 * Args:
 *   handler: Called with the run id once reconnect attempts are exhausted, or
 *     null to clear the current handler.
 */
function setOnAttachFailed(handler: ((runId: string) => void) | null) {
  onAttachFailed = handler;
}

// runState is a deep ref over the reducer's plain mutable state: folds
// mutate the reactive proxy in place and nested property writes trigger the
// watchers/computeds that track them (e.g. AgentWindow's `blocks` computed
// reads `windows[member].streamText` two levels deep — a shallowRef +
// triggerRef host freezes those child computeds because the window objects
// keep a stable identity across folds).
const runState = ref<TeamsRunState | null>(null);
// Active-run summaries for the monitor list (run_id/status/progress/...).
const monitors = ref<AgentTeamRunSummary[]>([]);
let attachAbort: AbortController | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Buffered SSE reader: splits
 * the byte stream on blank lines, joins each event's `data:` lines and
 * JSON-parses the payload. Events with empty data are skipped, so the
 * backend's `: heartbeat` comment lines are ignored.
 *
 * Args:
 *   body: The fetch response body stream.
 *   onEvent: Callback invoked with each JSON-parsed payload.
 */
async function readRunStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (payload: any) => void,
) {
  await readSseStream(body, onEvent);
}

/** Abort the current SSE attachment and any pending reconnect timer. */
function detach() {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  attachAbort?.abort();
  attachAbort = null;
}

/**
 * Attach the run's SSE stream and fold every valid event into the state.
 *
 * The connection auto-reconnects up to MAX_RECONNECT_ATTEMPTS times with a
 * 1s backoff when the stream ends abnormally (connection drop or fetch
 * failure). A run that already reached a terminal status is not
 * reconnected, and only one run is attached at a time.
 *
 * Args:
 *   runId: The run to stream events for.
 */
function attach(runId: string) {
  detach();
  const abort = new AbortController();
  attachAbort = abort;

  void (async () => {
    for (
      let attempt = 0;
      attempt < MAX_RECONNECT_ATTEMPTS && !abort.signal.aborted;
      attempt += 1
    ) {
      try {
        const response = await fetchWithAuth(agentTeamsApi.runStreamUrl(runId), {
          headers: { Accept: 'text/event-stream' },
          signal: abort.signal,
        });
        const contentType = response.headers.get('content-type') || '';
        if (!response.ok || !response.body || !contentType.includes('text/event-stream')) {
          return;
        }
        await readRunStream(response.body, (raw) => {
          // Unknown/malformed payloads are dropped by the parser.
          const event = parseTeamsEvent(raw);
          if (!event) return;
          const state = runState.value;
          // Ignore late events from a stream of a run we already left.
          if (!state || state.runId !== runId) return;
          // state is the reactive proxy: folds mutate deeply and the nested
          // writes notify the trackers themselves (no triggerRef needed).
          applyTeamsEvent(state, event);
        });
      } catch (error) {
        if (abort.signal.aborted) return;
        console.error('Agent teams run stream failed:', error);
      }
      if (abort.signal.aborted) return;
      if (
        runState.value?.runId === runId &&
        TERMINAL_RUN_STATUSES.includes(runState.value.status)
      ) {
        return;
      }
      // Server closed the stream: back off briefly, then reconnect.
      await new Promise<void>((resolve) => {
        reconnectTimer = setTimeout(resolve, RECONNECT_DELAY_MS);
      });
    }
    // Reconnect attempts exhausted while this run is still the attached one:
    // notify through the hook (previously console-only, invisible to users).
    if (!abort.signal.aborted && runState.value?.runId === runId) {
      onAttachFailed?.(runId);
    }
  })();
}

/**
 * Run one agent-teams API call and normalize the envelope result.
 *
 * Error envelopes and network failures are toasted (never re-thrown) and
 * mapped to null.
 *
 * Args:
 *   call: Closure performing the API request.
 *
 * Returns:
 *   The envelope `data` payload on success, otherwise null.
 */
async function unwrapEnvelope(
  call: () => Promise<AxiosResponse<ApiEnvelope<any>>>,
): Promise<any | null> {
  const { error } = useToast();
  try {
    const res = await call();
    if (res.data.status === 'error') {
      error(res.data.message || 'Agent teams request failed');
      return null;
    }
    return res.data.data ?? null;
  } catch (err) {
    // Non-2xx responses (e.g. HTTP 409 team-already-running) carry the
    // backend's error envelope in err.response.data — extractApiError prefers
    // its message over axios's generic "Request failed with status code N".
    error(extractApiError(err, 'Agent teams request failed').message);
    return null;
  }
}

/**
 * Set a local status on the attached run and notify watchers.

 * Args:
 *   runId: Only applied when this run is the attached one.
 *   status: The optimistic status to display until the next SSE event.
 */
function setLocalStatus(runId: string, status: string) {
  if (runState.value?.runId !== runId) return;
  runState.value.status = status;
}

/**
 * Load the active-run summaries into `monitors`.

 * Returns:
 *   The monitors array, or null on failure.
 */
async function loadActiveRuns() {
  const data = await unwrapEnvelope(() => agentTeamsApi.listActiveRuns());
  if (data === null) return null;
  monitors.value = data.runs ?? [];
  return monitors.value;
}

/**
 * Open a run monitor: seeds the state and attaches the SSE stream.
 *
 * Seeding order: the optional caller-provided seed, then the active-run
 * snapshot from listActiveRuns (matched by run_id), then a bare state — the
 * backend replays the event history on connect, rebuilding the rest.
 *
 * Args:
 *   runId: The run to open.
 *   seed: Optional seed (e.g. from a run history row the caller has).
 *
 * Returns:
 *   The created run state, or null when seeding failed entirely.
 */
async function openRun(runId: string, seed?: TeamsRunStateSeed) {
  closeRun();
  let row: AgentTeamRunSummary | TeamsRunStateSeed | null | undefined = seed ?? null;
  if (!row) {
    const data = await unwrapEnvelope(() => agentTeamsApi.listActiveRuns());
    row = (data?.runs ?? []).find((r: AgentTeamRunSummary) => r?.run_id === runId) ?? null;
  }
  const seedRow = row as
    | {
        status?: string;
        mode?: string;
        nodeStates?: Record<string, { status: string; error?: string }>;
        node_states?: Record<string, { status: string; error?: string }>;
        graph?: unknown;
      }
    | null
    | undefined;
  // Caller seeds use the reducer's camelCase shape, snapshot/history rows the
  // backend's snake_case — accept both. Unknown mode values fall back to the
  // reducer's 'dag' default.
  runState.value = createTeamsRunState(
    runId,
    seedRow
      ? {
          status: seedRow.status,
          mode:
            seedRow.mode === 'auto' ? 'auto' : seedRow.mode === 'dag' ? 'dag' : undefined,
          nodeStates: seedRow.nodeStates ?? seedRow.node_states,
          graph: seedRow.graph,
        }
      : undefined,
  );
  attach(runId);
  return runState.value;
}

/** Close the monitor: abort the stream, clear reconnects and null the state. */
function closeRun() {
  detach();
  runState.value = null;
}

/**
 * Start a DAG run for a team, seed the state from the returned snapshot and
 * attach the stream.

 * Returns:
 *   The run snapshot, or null on failure.
 */
async function startRun(
  teamId: string,
  payload: { mode: string; input: string; workflow_id?: string | null },
) {
  const snapshot = await unwrapEnvelope(() => agentTeamsApi.startRun(teamId, payload));
  if (!snapshot?.run_id) return null;
  const runId = String(snapshot.run_id);
  runState.value = createTeamsRunState(runId, {
    status: snapshot.status,
    // The auto orchestrator's snapshot carries mode: "auto"; DAG snapshots
    // predate the field and fall back to the reducer's 'dag' default.
    mode: snapshot.mode === 'auto' ? 'auto' : undefined,
    nodeStates: snapshot.node_states,
  });
  attach(runId);
  return snapshot;
}

/**
 * Pause a run; optimistically reflects the status on the attached state.

 * Returns:
 *   The envelope data payload, or null on failure.
 */
async function pause(runId: string) {
  const data = await unwrapEnvelope(() => agentTeamsApi.pauseRun(runId));
  if (data !== null) setLocalStatus(runId, 'paused');
  return data;
}

/**
 * Resume a paused run; optimistically reflects the status.

 * Returns:
 *   The envelope data payload, or null on failure.
 */
async function resumeRun(runId: string) {
  const data = await unwrapEnvelope(() => agentTeamsApi.resumeRun(runId));
  if (data !== null) setLocalStatus(runId, 'running');
  return data;
}

/**
 * Request a run stop; optimistically reflects the status (the final
 * `stopped` event arrives over SSE).

 * Returns:
 *   The envelope data payload, or null on failure.
 */
async function stop(runId: string) {
  const data = await unwrapEnvelope(() => agentTeamsApi.stopRun(runId));
  if (data !== null) setLocalStatus(runId, 'stopping');
  return data;
}

/**
 * Retry a failed node on the currently attached run.

 * Returns:
 *   The envelope data payload, or null when no run is attached / on failure.
 */
async function retryNode(nodeId: string) {
  const runId = runState.value?.runId;
  if (!runId) return null;
  return unwrapEnvelope(() => agentTeamsApi.retryNode(runId, nodeId));
}

/**
 * Skip a node (and its pending downstream) on the currently attached run.

 * Returns:
 *   The envelope data payload, or null when no run is attached / on failure.
 */
async function skipNode(nodeId: string) {
  const runId = runState.value?.runId;
  if (!runId) return null;
  return unwrapEnvelope(() => agentTeamsApi.skipNode(runId, nodeId));
}

/**
 * Interrupt one member's in-flight turn on the attached run.
 *
 * The backend stops the member agent turn and lands its DAG node in the
 * independent interrupted state (the run pauses; retry/skip stay available).
 *
 * Args:
 *   memberId: Member whose turn should be interrupted.
 *
 * Returns:
 *   The envelope data, or null when no run is attached or the call failed.
 */
async function interruptMember(memberId: string) {
  const runId = runState.value?.runId;
  if (!runId) return null;
  return unwrapEnvelope(() => agentTeamsApi.interruptRunMember(runId, memberId));
}

/** Re-attach the SSE stream of the currently opened run. */
function reconnect() {
  if (runState.value) attach(runState.value.runId);
}

export function useAgentTeamsRun() {
  return {
    runState,
    monitors,
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
    reconnect,
    setOnAttachFailed,
  };
}
