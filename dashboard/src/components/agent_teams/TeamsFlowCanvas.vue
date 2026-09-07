<template>
  <div class="teams-flow-canvas" @dragover.prevent @drop="onDrop">
    <VueFlow
      :nodes="displayNodes"
      :edges="displayEdges"
      :node-types="nodeTypes"
      :nodes-draggable="isEdit"
      :nodes-connectable="isEdit"
      :elements-selectable="isEdit"
      :selection-key-code="['Control', 'Meta']"
      :selection-mode="SelectionMode.Partial"
      :snap-to-grid="isEdit && snapToGrid"
      :snap-grid="[16, 16]"
      :fit-view-on-init="true"
      :min-zoom="0.2"
      :max-zoom="1.5"
      @connect="onConnect"
      @nodes-change="onNodesChange"
      @node-click="onNodeClick"
      @edge-click="onEdgeClick"
      @edge-double-click="onEdgeDoubleClick"
      @selection-drag-stop="onSelectionDragStop"
      @pane-click="onPaneClick"
    >
      <Background :gap="16" />
      <Controls />
      <MiniMap v-if="minimapVisible" pannable zoomable />
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
//   red via the caller-provided `at-node-missing` class. Member drag-drops
//   from the editor's member strip are converted to flow coordinates here
//   (this component owns the VueFlow store) and forwarded as `dropAt`.
// - monitor: interaction locked; run status/error reach the card through
//   `data.status`/`data.error` (enriched by the caller), so the card owns the
//   status ring and native error tooltip. Edges get arrow markers and animate
//   out of running nodes.
import { computed } from 'vue';
import { VueFlow, MarkerType, useVueFlow, SelectionMode } from '@vue-flow/core';
import type { Connection, EdgeMouseEvent, NodeChange } from '@vue-flow/core';
import { useModuleI18n } from '@/i18n/composables';
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
    /** Minimap visibility, toggled from the editor's canvas toolbar. */
    minimapVisible?: boolean;
    /** Snap node positions to the grid while dragging (edit mode only). */
    snapToGrid?: boolean;
    /**
     * Id of the currently selected edge (edit mode). Owned by the parent so
     * the editor toolbar can enable its delete action; the canvas only reads
     * it to render the selected-edge highlight.
     */
    selectedEdgeId?: string | null;
  }>(),
  { mode: 'edit', nodeStates: null, minimapVisible: true, snapToGrid: false, selectedEdgeId: null },
);

const emit = defineEmits<{
  (e: 'connect', params: { from: string; to: string }): void;
  (e: 'positionChange', positions: Record<string, { x: number; y: number }>): void;
  (e: 'selectNode', nodeId: string | null): void;
  (e: 'dropAt', memberId: string, position: { x: number; y: number }): void;
  (e: 'deleteEdge', edgeId: string): void;
  (e: 'selectionChange', nodeIds: string[]): void;
  /** Edge selection changed (id when selected, null when cleared). */
  (e: 'selectEdge', edgeId: string | null): void;
}>();

// This component owns the <VueFlow> instance: calling useVueFlow() here
// creates the store that the inner VueFlow component adopts, so the drop
// handler can translate screen coordinates through the live viewport.
const { screenToFlowCoordinate, setCenter } = useVueFlow();
const { tm } = useModuleI18n('features/agent-teams');

/**
 * Center the viewport on a flow coordinate (search-to-result).
 *
 * Args:
 *   x: Target flow x.
 *   y: Target flow y.
 */
function panToFlow(x: number, y: number) {
  void setCenter(x, y, { duration: 300 });
}

defineExpose({ panTo: panToFlow });

/** DataTransfer MIME type carrying the dragged member id (set by the editor). */
const MEMBER_MIME = 'application/x-member-id';

/**
 * Convert a member drag-drop into a flow-coordinate `dropAt` event.
 *
 * Args:
 *   event: The native drop event on the canvas root.
 */
function onDrop(event: DragEvent) {
  const memberId = event.dataTransfer?.getData(MEMBER_MIME);
  if (!memberId) return;
  const position = screenToFlowCoordinate({ x: event.clientX, y: event.clientY });
  emit('dropAt', memberId, { x: position.x, y: position.y });
}

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
 * only; status comes from the node's enriched data). In edit mode edges also
 * get the smoothstep type and a selected/unselected stroke so a click makes
 * the target edge visually distinct (the edit loop needs to know what it is
 * about to delete).
 */
const displayEdges = computed(() => {
  const statusByNodeId = new Map(
    props.nodes.map((node) => [node.id, (node.data as Record<string, unknown>)?.status]),
  );
  return props.edges.map((edge) => {
    const next: FlowEdgePayload = {
      ...edge,
      markerEnd: MarkerType.ArrowClosed,
    };
    if (isEdit.value) {
      // Edit mode uses smoothstep edges with a selected/unselected stroke so
      // a click makes the target edge visually distinct before deletion.
      next.type = 'smoothstep';
      const selected = props.selectedEdgeId === edge.id;
      next.selected = selected;
      next.style = selected
        ? { stroke: '#1976d2', strokeWidth: 2.5 }
        : { stroke: '#b1b1b7', strokeWidth: 1.5 };
    }
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
  emit('selectEdge', null);
  emit('selectNode', event?.node?.id ?? null);
  // Clicking a node defeats any active box selection.
  if (isEdit.value) emit('selectionChange', []);
}

/**
 * Select an edge (edit mode): highlight the target edge and clear any node
 * selection so the inspector closes — the edit intent switched to the edge.
 */
function onEdgeClick(event: EdgeMouseEvent) {
  if (!isEdit.value) return;
  emit('selectEdge', event.edge.id);
  emit('selectNode', null);
}

/** Confirm and delete an edge on double-click (edit mode). */
function onEdgeDoubleClick(event: EdgeMouseEvent) {
  if (!isEdit.value) return;
  const from = event.edge.source;
  const to = event.edge.target;
  if (window.confirm(tm('editor.deleteEdgeConfirm', { from, to }))) {
    emit('deleteEdge', event.edge.id);
    emit('selectEdge', null);
  }
}

/** Forward a Ctrl/Cmd-drag box selection as the selected node id list. */
function onSelectionDragStop(event: { nodes?: { id: string }[] }) {
  const ids = (event?.nodes ?? [])
    .map((node) => node.id)
    .filter((id): id is string => Boolean(id));
  emit('selectionChange', ids);
}

function onPaneClick() {
  emit('selectEdge', null);
  emit('selectNode', null);
  // Pane clicks clear the box selection as well.
  if (isEdit.value) emit('selectionChange', []);
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
