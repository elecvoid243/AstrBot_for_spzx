<template>
  <div class="teams-flow-canvas">
    <VueFlow
      :nodes="displayNodes"
      :edges="displayEdges"
      :node-types="nodeTypes"
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
    >
      <Background :gap="16" />
      <Controls />
      <MiniMap pannable zoomable />
    </VueFlow>
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
  /** Renderer type; the canvas forces the custom `member` card. */
  type?: string;
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

import { markRaw } from 'vue';
import type { NodeComponent, NodeTypesObject } from '@vue-flow/core';
import MemberFlowNode from './MemberFlowNode.vue';

/**
 * Custom node renderer registry. Declared at module level and markRaw'd so
 * VueFlow never observes it (reactive nodeTypes cause a perf warning and
 * re-render churn). Cast through NodeComponent: Vue Flow forwards the full
 * NodeProps to the card, but the card only declares the subset it uses.
 */
const nodeTypes: NodeTypesObject = {
  member: markRaw(MemberFlowNode) as unknown as NodeComponent,
};

export default {};
</script>

<script setup lang="ts">
// Shared DAG canvas for the agent-teams workflow editor (edit mode) and the
// run monitor (monitor mode). Every node renders as the MemberFlowNode card:
// - edit: draggable / connectable / selectable, emits normalized connects,
//   position maps after drags and node selection; missing members are dashed
//   red via the caller-provided `at-node-missing` class.
// - monitor: interaction locked; run status/error reach the card through
//   `data.status`/`data.error` (enriched by the caller), so the card owns the
//   status ring and native error tooltip. Edges get arrow markers and animate
//   out of running nodes.
import { computed } from 'vue';
import { VueFlow, MarkerType } from '@vue-flow/core';
import type { Connection, NodeChange } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { MiniMap } from '@vue-flow/minimap';
import { Controls } from '@vue-flow/controls';
// Background ships its styles inline; the other chrome packages need theirs.
import '@vue-flow/minimap/dist/style.css';
import '@vue-flow/controls/dist/style.css';
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';

const props = withDefaults(
  defineProps<{
    nodes: FlowNode[];
    edges: FlowEdgePayload[];
    mode?: 'edit' | 'monitor';
    /**
     * Per-node run states, still bound by the monitor for the data contract.
     * Status rendering itself moved into the node card (via data.status, also
     * enriched by the caller from this same map).
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

/** Every node renders as the member card; enriched data is passed through. */
const displayNodes = computed(() =>
  props.nodes.map((node) => ({
    ...node,
    type: 'member',
  })),
);

/**
 * Edges handed to VueFlow: arrow markers on all of them, plus a running
 * animation on edges whose SOURCE node is currently running (monitor mode
 * only; status comes from the node's enriched data).
 */
const displayEdges = computed(() => {
  const statusByNodeId = new Map(
    props.nodes.map((node) => [node.id, (node.data as Record<string, unknown>)?.status]),
  );
  return props.edges.map((edge) => {
    const next: FlowEdgePayload = { ...edge, markerEnd: MarkerType.ArrowClosed };
    if (!isEdit.value && statusByNodeId.get(edge.source) === 'running') {
      next.animated = true;
    }
    return next;
  });
});

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
</style>
