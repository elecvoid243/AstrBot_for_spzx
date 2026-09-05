<template>
  <div class="teams-flow-canvas">
    <VueFlow
      :nodes="displayNodes"
      :edges="edges"
      :nodes-draggable="isEdit"
      :nodes-connectable="isEdit"
      :elements-selectable="isEdit"
      :fit-view-on-init="true"
      :min-zoom="0.2"
      :max-zoom="1.5"
      @connect="onConnect"
      @nodes-change="onNodesChange"
      @node-click="onNodeClick"
      @pane-click="onPaneClick"
    />
  </div>
</template>

<script lang="ts">
// Node/edge contracts shared with WorkflowEditor (and future monitor views).
// Declared in a plain <script> block because <script setup> cannot contain
// exports.
/** Node accepted and emitted by the canvas (VueFlow DEFAULT node shape). */
export interface FlowNode {
  id: string;
  position: { x: number; y: number };
  data: {
    label: string;
    memberName?: string;
    /** Bound team member id; empty when the node is not bound yet. */
    memberId?: string;
    task?: string;
    [key: string]: unknown;
  };
  /** Extra CSS class rendered on the node (e.g. `at-node-missing`). */
  class?: string;
}

/** Minimal edge shape (VueFlow Edge requires id/source/target). */
export interface FlowEdgePayload {
  id: string;
  source: string;
  target: string;
  [key: string]: unknown;
}

export default {};
</script>

<script setup lang="ts">
// Shared DAG canvas for the agent-teams workflow editor (edit mode) and the
// run monitor (monitor mode, wired in Task 8). Keeps to VueFlow DEFAULT nodes
// — behavior differs per mode:
// - edit: draggable / connectable / selectable, emits normalized connects,
//   position maps after drags and node selection.
// - monitor: interaction locked, each node gets an `at-node-<status>` class
//   driven by the `nodeStates` prop (styled below, dark-theme friendly) and a
//   native error tooltip on failed/interrupted nodes.
import { computed } from 'vue';
import { VueFlow } from '@vue-flow/core';
import type { Connection, NodeChange } from '@vue-flow/core';
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';

const props = withDefaults(
  defineProps<{
    nodes: FlowNode[];
    edges: FlowEdgePayload[];
    mode?: 'edit' | 'monitor';
    /**
     * Per-node run states; only read in monitor mode. `error` carries the
     * backend failure text of failed/interrupted nodes (spec §3.1).
     */
    nodeStates?: Record<string, { status: string; error?: string | null }> | null;
  }>(),
  { mode: 'edit', nodeStates: null },
);

const emit = defineEmits<{
  (e: 'connect', params: { from: string; to: string }): void;
  (e: 'positionChange', positions: Record<string, { x: number; y: number }>): void;
  (e: 'selectNode', nodeId: string | null): void;
}>();

const isEdit = computed(() => props.mode === 'edit');

/**
 * Node list handed to VueFlow: passthrough in edit mode; in monitor mode the
 * node class is bound to the node's run status (`at-node-<status>`) and
 * failed/interrupted nodes expose their error as a native `title` tooltip on
 * the node container via VueFlow's `domAttributes` escape hatch (spec §3.1).
 */
const displayNodes = computed(() =>
  props.nodes.map((node) => {
    if (!isEdit.value) {
      const state = props.nodeStates?.[node.id];
      if (state?.status) {
        return {
          ...node,
          class: `at-node-${state.status}`,
          ...(state.error ? { domAttributes: { title: state.error } } : {}),
        };
      }
    }
    return node;
  }),
);

/** Normalize a raw VueFlow connection and drop self-loops. */
function onConnect(connection: Connection) {
  const { source, target } = connection ?? ({} as Connection);
  if (!source || !target || source === target) return;
  emit('connect', { from: source, to: target });
}

/**
 * Collect node positions from change batches (drag updates) and emit the
 * id -> position map so the parent can keep its layout up to date.
 */
function onNodesChange(changes: NodeChange[]) {
  let positions: Record<string, { x: number; y: number }> | null = null;
  for (const change of changes ?? []) {
    if (change?.type === 'position' && change.position) {
      positions ??= {};
      positions[change.id] = { x: change.position.x, y: change.position.y };
    }
  }
  if (positions) emit('positionChange', positions);
}

function onNodeClick(event: { node: { id: string } }) {
  emit('selectNode', event?.node?.id ?? null);
}

function onPaneClick() {
  emit('selectNode', null);
}
</script>

<style scoped>
.teams-flow-canvas {
  width: 100%;
  height: 100%;
  min-height: 420px;
  border: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 12px;
  overflow: hidden;
  background: var(--dashboard-surface, rgba(128, 128, 128, 0.04));
}

/* Node states in monitor mode. The classes land on elements rendered inside
   the VueFlow subtree, so :deep() is required; colors use translucent rgba
   plus a solid accent so they read on both light and dark themes. */
.teams-flow-canvas :deep(.vue-flow__node.at-node-pending) {
  border-color: rgba(148, 163, 184, 0.9);
  background: rgba(100, 116, 139, 0.25);
  color: inherit;
}

.teams-flow-canvas :deep(.vue-flow__node.at-node-running) {
  border-color: #60a5fa;
  background: rgba(59, 130, 246, 0.22);
  color: inherit;
  animation: at-node-pulse 1.4s ease-in-out infinite;
}

.teams-flow-canvas :deep(.vue-flow__node.at-node-done) {
  border-color: #4ade80;
  background: rgba(34, 197, 94, 0.2);
  color: inherit;
}

.teams-flow-canvas :deep(.vue-flow__node.at-node-failed) {
  border-color: #f87171;
  background: rgba(239, 68, 68, 0.22);
  color: inherit;
}

.teams-flow-canvas :deep(.vue-flow__node.at-node-skipped) {
  border-color: #facc15;
  background: rgba(250, 204, 21, 0.18);
  color: inherit;
}

@keyframes at-node-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(96, 165, 250, 0.55);
  }
  50% {
    box-shadow: 0 0 0 8px rgba(96, 165, 250, 0);
  }
}

/* Edit mode: nodes bound to a member that no longer exists. */
.teams-flow-canvas :deep(.vue-flow__node.at-node-missing) {
  border: 1px dashed #f87171;
}
</style>
