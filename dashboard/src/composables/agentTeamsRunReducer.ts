// Author: elecvoid243
// Date: 2026-09-05
// Plan: docs/superpowers/plans/2026-09-05-agent-teams-frontend.md (Task 3)
// Pure fold core for the agent-teams run monitor. The backend streams JSON
// events over SSE; parseTeamsEvent validates one payload and applyTeamsEvent
// folds it into a plain mutable state object that a Vue composable hosts
// reactively (same in-place mutation pattern as subagentRunReducer).
// Dependency-free leaf: no vue / @/api imports.

/** Live message window for one member: task sent -> stream deltas -> reply. */
export interface MemberWindowState {
  memberId: string;
  /** Last task text delivered to the member; null before the first `sent`. */
  sent: string | null;
  /** Streaming buffer; a `reply` replaces it with the final answer text. */
  streamText: string;
  /** Structured message parts carried by the reply, if any. */
  parts: any[];
  streaming: boolean;
}

export interface TeamsRunProgress {
  done: number;
  running: number;
  pending: number;
  skipped: number;
  failed: number;
  total: number;
}

export interface TeamsNodeState {
  status: string;
  error?: string;
}

/** One member/task pair of an auto-orchestration dispatch. */
export interface TeamsDispatchAssignment {
  member: string;
  task: string;
}

/** One folded `dispatch` event: a round's assignments plus optional notes. */
export interface TeamsDispatchRecord {
  round: number;
  assignments: TeamsDispatchAssignment[];
  notes?: string;
}

export interface TeamsRunState {
  runId: string;
  status: string;
  /** Orchestration mode of the attached run: 'dag' by default, 'auto' for the round-based orchestrator. */
  mode: "auto" | "dag";
  progress: TeamsRunProgress;
  round: { n: number; max: number };
  windows: Record<string, MemberWindowState>;
  nodeStates: Record<string, TeamsNodeState>;
  busySessionIds: Set<string>;
  pausedNodeId: string | null;
  lastError: string | null;
  stoppedReason: string | null;
  /** Auto-orchestration dispatch log, appended in event order. */
  dispatches: TeamsDispatchRecord[];
}

export interface TeamsMessageEvent {
  type: "message";
  direction: "sent" | "stream" | "reply";
  member_id: string;
  session_id?: string;
  text: string;
  parts?: any[];
}

export interface TeamsNodeStatusEvent {
  type: "node_status";
  node_id: string;
  member_id?: string;
  status: string;
  error?: string;
}

export interface TeamsDagProgressEvent {
  type: "dag_progress";
  done: number;
  running: number;
  pending: number;
  skipped: number;
  failed: number;
  total: number;
}

export interface TeamsRoundEvent {
  type: "round";
  n: number;
  max: number;
}

export interface TeamsDispatchEvent {
  type: "dispatch";
  round: number;
  assignments: TeamsDispatchAssignment[];
  notes?: string;
}

export interface TeamsBusyEvent {
  type: "busy";
  session_id: string;
}

export interface TeamsPausedEvent {
  type: "paused";
  reason: string;
  node_id?: string;
}

export interface TeamsErrorEvent {
  type: "error";
  reason: string;
}

export interface TeamsStoppedEvent {
  type: "stopped";
  reason: string;
}

export type TeamsRunEvent =
  | TeamsMessageEvent
  | TeamsNodeStatusEvent
  | TeamsDagProgressEvent
  | TeamsRoundEvent
  | TeamsDispatchEvent
  | TeamsBusyEvent
  | TeamsPausedEvent
  | TeamsErrorEvent
  | TeamsStoppedEvent;

/** Run statuses a late `stopped` event must not overwrite. */
const TERMINAL_RUN_STATUSES = ["completed", "stopped", "failed"];

/** Seed taken from an active-run snapshot or a run history row. */
export interface TeamsRunStateSeed {
  status?: string;
  /** Orchestration mode carried by run rows/snapshots; defaults to 'dag'. */
  mode?: "auto" | "dag";
  nodeStates?: Record<string, TeamsNodeState>;
  /**
   * Workflow graph carried by the snapshot/history row. Accepted so callers
   * can pass the row as-is; the graph is rendered by the composable from the
   * row itself and is not part of the folded run state.
   */
  graph?: unknown;
}

export function createTeamsRunState(
  runId: string,
  seed?: TeamsRunStateSeed,
): TeamsRunState {
  // Copy seed entries so later folds never mutate the caller's row object.
  const nodeStates: Record<string, TeamsNodeState> = {};
  for (const [nodeId, ns] of Object.entries(seed?.nodeStates ?? {})) {
    nodeStates[nodeId] = {
      status: ns && typeof ns.status === "string" ? ns.status : "pending",
      ...(ns && typeof ns.error === "string" ? { error: ns.error } : {}),
    };
  }
  return {
    runId,
    status: seed?.status ?? "running",
    mode: seed?.mode === "auto" ? "auto" : "dag",
    progress: { done: 0, running: 0, pending: 0, skipped: 0, failed: 0, total: 0 },
    round: { n: 0, max: 0 },
    windows: {},
    nodeStates,
    busySessionIds: new Set<string>(),
    pausedNodeId: null,
    lastError: null,
    stoppedReason: null,
    dispatches: [],
  };
}

/**
 * Validate one raw SSE payload into a typed run event.
 *
 * Returns null for unknown event types, non-object inputs, and known types
 * with missing required fields — the SSE layer drops nulls instead of
 * feeding malformed data into the reducer.
 *
 * Args:
 *   raw: The JSON-parsed `data` payload of one SSE event.
 *
 * Returns:
 *   The parsed event, or null when the payload is not a valid run event.
 */
export function parseTeamsEvent(raw: unknown): TeamsRunEvent | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const obj = raw as Record<string, any>;
  switch (obj.type) {
    case "message": {
      if (!obj.member_id || typeof obj.member_id !== "string") return null;
      if (
        obj.direction !== "sent" &&
        obj.direction !== "stream" &&
        obj.direction !== "reply"
      ) {
        return null;
      }
      if (typeof obj.text !== "string") return null;
      const ev: TeamsMessageEvent = {
        type: "message",
        direction: obj.direction,
        member_id: obj.member_id,
        text: obj.text,
      };
      // Stream/reply deltas may legitimately omit session_id, so it stays
      // optional even though the busy fold uses it when present.
      if (typeof obj.session_id === "string") ev.session_id = obj.session_id;
      if (Array.isArray(obj.parts)) ev.parts = obj.parts;
      return ev;
    }
    case "node_status": {
      if (!obj.node_id || typeof obj.node_id !== "string") return null;
      if (!obj.status || typeof obj.status !== "string") return null;
      const ev: TeamsNodeStatusEvent = {
        type: "node_status",
        node_id: obj.node_id,
        status: obj.status,
      };
      if (typeof obj.member_id === "string") ev.member_id = obj.member_id;
      if (typeof obj.error === "string") ev.error = obj.error;
      return ev;
    }
    case "dag_progress": {
      if (
        typeof obj.done !== "number" ||
        typeof obj.running !== "number" ||
        typeof obj.pending !== "number" ||
        typeof obj.skipped !== "number" ||
        typeof obj.failed !== "number" ||
        typeof obj.total !== "number"
      ) {
        return null;
      }
      return {
        type: "dag_progress",
        done: obj.done,
        running: obj.running,
        pending: obj.pending,
        skipped: obj.skipped,
        failed: obj.failed,
        total: obj.total,
      };
    }
    case "round": {
      if (typeof obj.n !== "number") return null;
      // The auto orchestrator names the limit `max_rounds`; DAG-originated
      // payloads may use `max`. Accept both so the round folds either way.
      const max = typeof obj.max === "number" ? obj.max : obj.max_rounds;
      if (typeof max !== "number") return null;
      return { type: "round", n: obj.n, max };
    }
    case "dispatch": {
      // Auto-orchestration dispatch: the round plus an array of {member,
      // task} pairs. Anything else is dropped — this lands the deferred
      // Array.isArray guard (the legacy Record shape parses to null now).
      if (typeof obj.round !== "number") return null;
      if (!Array.isArray(obj.assignments)) return null;
      const assignments: TeamsDispatchAssignment[] = [];
      for (const item of obj.assignments) {
        if (
          !item ||
          typeof item !== "object" ||
          typeof item.member !== "string" ||
          typeof item.task !== "string"
        ) {
          return null;
        }
        assignments.push({ member: item.member, task: item.task });
      }
      const ev: TeamsDispatchEvent = {
        type: "dispatch",
        round: obj.round,
        assignments,
      };
      // The backend sends `notes: null` when the coordinator added none.
      if (typeof obj.notes === "string") ev.notes = obj.notes;
      return ev;
    }
    case "busy": {
      if (!obj.session_id || typeof obj.session_id !== "string") return null;
      return { type: "busy", session_id: obj.session_id };
    }
    case "paused": {
      if (typeof obj.reason !== "string") return null;
      const ev: TeamsPausedEvent = { type: "paused", reason: obj.reason };
      if (typeof obj.node_id === "string") ev.node_id = obj.node_id;
      return ev;
    }
    case "error": {
      if (typeof obj.reason !== "string") return null;
      return { type: "error", reason: obj.reason };
    }
    case "stopped": {
      if (typeof obj.reason !== "string") return null;
      return { type: "stopped", reason: obj.reason };
    }
    default:
      return null;
  }
}

/**
 * Fold one parsed event into the run state, mutating it in place so a
 * reactive host (Vue composable) sees the updates without reassignment.
 *
 * Args:
 *   state: The plain mutable run state created by createTeamsRunState.
 *   ev: An event returned by parseTeamsEvent.
 */
export function applyTeamsEvent(state: TeamsRunState, ev: TeamsRunEvent): void {
  switch (ev.type) {
    case "message": {
      let win = state.windows[ev.member_id];
      if (!win) {
        win = {
          memberId: ev.member_id,
          sent: null,
          streamText: "",
          parts: [],
          streaming: false,
        };
        state.windows[ev.member_id] = win;
      }
      if (ev.direction === "sent") {
        // A new task delivery opens a fresh window phase: parts from the
        // previous round's reply are cleared too, or they would mask the
        // round-2 stream until its own reply arrives.
        win.sent = ev.text;
        win.streamText = "";
        win.streaming = false;
        win.parts = [];
      } else if (ev.direction === "stream") {
        win.streamText += ev.text;
        win.streaming = true;
      } else {
        // The final text always replaces the buffer: when the reply extends
        // the stream, assignment lands stream + tail; when it does not
        // (replay gap / non-streamed reply), it sets the value outright.
        win.streamText = ev.text;
        win.streaming = false;
        if (Array.isArray(ev.parts)) win.parts = ev.parts;
        // The runner emits `busy` only while waiting for this member's
        // reply, so the reply is the natural clear signal for its session.
        if (ev.session_id) state.busySessionIds.delete(ev.session_id);
      }
      break;
    }
    case "node_status": {
      let ns = state.nodeStates[ev.node_id];
      if (!ns) {
        ns = { status: ev.status };
        state.nodeStates[ev.node_id] = ns;
      } else {
        ns.status = ev.status;
      }
      if (typeof ev.error === "string") ns.error = ev.error;
      else delete ns.error;
      // Mirror the backend: progress is the count of node states by status,
      // so a node transition keeps progress consistent even when the paired
      // dag_progress event has not been applied yet.
      const progress: TeamsRunProgress = {
        done: 0,
        running: 0,
        pending: 0,
        skipped: 0,
        failed: 0,
        total: 0,
      };
      for (const node of Object.values(state.nodeStates)) {
        progress.total++;
        if (
          node.status === "done" ||
          node.status === "running" ||
          node.status === "pending" ||
          node.status === "skipped" ||
          node.status === "failed"
        ) {
          progress[node.status]++;
        }
      }
      state.progress = progress;
      break;
    }
    case "dag_progress":
      state.progress = {
        done: ev.done,
        running: ev.running,
        pending: ev.pending,
        skipped: ev.skipped,
        failed: ev.failed,
        total: ev.total,
      };
      break;
    case "round":
      state.round = { n: ev.n, max: ev.max };
      break;
    case "dispatch": {
      // Append to the auto-orchestration dispatch log; the monitor renders
      // the tail for auto runs (DAG runs never emit dispatch events).
      const record: TeamsDispatchRecord = {
        round: ev.round,
        assignments: ev.assignments,
      };
      if (ev.notes !== undefined) record.notes = ev.notes;
      state.dispatches.push(record);
      break;
    }
    case "busy":
      // Emitted repeatedly while waiting — add-only marker here; never
      // removed by busy events themselves.
      state.busySessionIds.add(ev.session_id);
      break;
    case "paused":
      state.status = "paused";
      state.pausedNodeId = ev.node_id ?? null;
      break;
    case "error":
      state.lastError = ev.reason;
      break;
    case "stopped":
      state.stoppedReason = ev.reason;
      // History replay may deliver events after the run already reached a
      // terminal status (e.g. seeded `completed`); keep the first terminal.
      if (!TERMINAL_RUN_STATUSES.includes(state.status)) {
        state.status = "stopped";
      }
      break;
  }
}
