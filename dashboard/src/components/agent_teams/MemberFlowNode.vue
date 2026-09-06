<template>
  <div class="member-flow-node" :class="cardClasses" :title="data.error || undefined">
    <!-- Handles only render inside a real Vue Flow node context; hiding them
         via `interactive === false` doubles as the provider-free test seam. -->
    <Handle
      v-if="data.interactive !== false"
      type="target"
      :position="Position.Left"
      class="at-node-handle"
      role="button"
      :aria-label="tm('editor.handleTarget')"
    />
    <Handle
      v-if="data.interactive !== false"
      type="source"
      :position="Position.Right"
      class="at-node-handle"
      role="button"
      :aria-label="tm('editor.handleSource')"
    />

    <div class="at-node-header">
      <span class="at-node-title">{{ titleText }}</span>
      <span v-if="data.nodeNumber !== undefined" class="at-node-number">#{{ data.nodeNumber }}</span>
      <span v-if="data.status" class="at-node-status" :data-status="data.status">
        <i class="at-node-status-dot" />{{ statusText }}
      </span>
    </div>

    <div class="at-node-member">
      <i class="at-node-member-dot" :style="{ background: data.memberColor }" />
      <!-- ② The missing-member marker is a real button so keyboard users reach
           it; the click bubbles into Vue Flow's node click, which selects the
           node and opens the inspector on its member field (spec §3.3). -->
      <button
        v-if="data.missingMember"
        type="button"
        class="at-node-member-missing"
        data-test="missing-marker"
        :aria-label="tm('editor.missingMember')"
      >
        {{ tm('editor.memberMissing') }}
      </button>
      <span v-else class="at-node-member-name">{{ data.memberName }}</span>
    </div>

    <div v-if="data.configLabel || data.personaLabel" class="at-node-chips">
      <span v-if="data.configLabel" class="at-node-chip">{{ data.configLabel }}</span>
      <span v-if="data.personaLabel" class="at-node-chip">{{ data.personaLabel }}</span>
    </div>

    <p v-if="data.taskPreview" class="at-node-task at-node-task-clamp">{{ data.taskPreview }}</p>

    <div class="at-node-footer">
      <span class="at-node-indegree">{{ tm('editor.inDegree') }} {{ data.inDegree ?? 0 }}</span>
    </div>
  </div>
</template>

<script lang="ts">
/**
 * Data contract carried on `node.data` for the member card. Call sites
 * (WorkflowEditor / RunMonitor) enrich their node lists with these fields;
 * everything except `memberName`/`memberColor` is optional.
 */
export interface MemberFlowNodeData {
  /** Legacy default-node label (header fallback when `nodeTitle` is unset). */
  label?: string;
  memberName: string;
  /** Member accent color (hex) for the body dot; also reused on chips. */
  memberColor: string;
  nodeTitle?: string;
  nodeNumber?: number;
  taskPreview?: string;
  /** Monitor run status: 'pending' | 'running' | 'done' | 'failed' | 'skipped' | 'interrupted'. */
  status?: string;
  error?: string;
  configLabel?: string;
  personaLabel?: string;
  missingMember?: boolean;
  inDegree?: number;
  /**
   * False hides the connect handles (test seam); production always passes
   * true / leaves it undefined, which keeps the handles visible.
   */
  interactive?: boolean;
}

export default {};
</script>

<script setup lang="ts">
// Custom Vue Flow node card for the agent-teams canvas (edit + monitor).
// Rendered inside VueFlow's node wrapper via the canvas' nodeTypes registry;
// Vuetify-free on purpose so the card also works standalone. The wrapper
// forwards many internal props (position/dimensions/zIndex/...) — they are
// dropped instead of leaking onto the card's DOM attributes.
import { computed } from 'vue';
import { Handle, Position } from '@vue-flow/core';
import { useModuleI18n } from '@/i18n/composables';

const props = defineProps<{
  /** Vue Flow node id. */
  id: string;
  /** Card payload (see MemberFlowNodeData). */
  data: MemberFlowNodeData;
  /** Selection state forwarded by Vue Flow's node wrapper. */
  selected?: boolean;
}>();

defineOptions({ name: 'MemberFlowNode', inheritAttrs: false });

const { tm } = useModuleI18n('features/agent-teams');

const titleText = computed(() => props.data.nodeTitle ?? props.data.label ?? '');

/** Status text reuses the monitor.node.* keys (not color-only signaling). */
const statusText = computed(() =>
  props.data.status ? tm(`monitor.node.${props.data.status}`) : '',
);

const cardClasses = computed(() => ({
  selected: !!props.selected,
  'is-missing': !!props.data.missingMember,
  'has-error': !!props.data.error,
  [`at-node-${props.data.status}`]: !!props.data.status,
}));
</script>

<style scoped>
/* Base card = the pending palette; status classes restyle border/background
   with the same colors the canvas used for the old default nodes. */
.member-flow-node {
  width: 220px;
  padding: 8px 10px;
  border: 1px solid rgba(148, 163, 184, 0.9);
  border-radius: 10px;
  background: rgba(100, 116, 139, 0.25);
  color: inherit;
  font-size: 12px;
  line-height: 1.4;
  box-sizing: border-box;
}

.member-flow-node.at-node-running {
  border-color: #60a5fa;
  background: rgba(59, 130, 246, 0.22);
  animation: at-card-pulse 1.4s ease-in-out infinite;
}

.member-flow-node.at-node-done {
  border-color: #4ade80;
  background: rgba(34, 197, 94, 0.2);
}

.member-flow-node.at-node-failed {
  border-color: #f87171;
  background: rgba(239, 68, 68, 0.22);
}

.member-flow-node.at-node-skipped {
  border-color: #facc15;
  background: rgba(250, 204, 21, 0.18);
}

.member-flow-node.at-node-interrupted {
  border-color: #a78bfa;
  background: rgba(139, 92, 246, 0.2);
}

/* Edit mode: node bound to a member that no longer exists. */
.member-flow-node.is-missing {
  border: 1px dashed #f87171;
}

.member-flow-node.has-error {
  border-color: #f87171;
  box-shadow: 0 0 0 1px rgba(248, 113, 113, 0.55);
}

.member-flow-node.selected {
  outline: 2px solid #60a5fa;
  outline-offset: 1px;
}

@keyframes at-card-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(96, 165, 250, 0.55);
  }
  50% {
    box-shadow: 0 0 0 8px rgba(96, 165, 250, 0);
  }
}

.at-node-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.at-node-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.at-node-number {
  flex: none;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
  font-weight: 400;
}

.at-node-status {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  font-weight: 400;
}

.at-node-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #94a3b8;
  flex: none;
}

.at-node-status[data-status='running'] .at-node-status-dot {
  background: #60a5fa;
}

.at-node-status[data-status='done'] .at-node-status-dot {
  background: #4ade80;
}

.at-node-status[data-status='failed'] .at-node-status-dot {
  background: #f87171;
}

.at-node-status[data-status='skipped'] .at-node-status-dot {
  background: #facc15;
}

.at-node-status[data-status='interrupted'] .at-node-status-dot {
  background: #a78bfa;
}

.at-node-member {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-weight: 600;
}

.at-node-member-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: none;
}

.at-node-member-missing {
  color: #f87171;
}

.at-node-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.at-node-chip {
  max-width: 100%;
  padding: 1px 6px;
  border-radius: 6px;
  background: rgba(128, 128, 128, 0.15);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.at-node-task {
  margin: 6px 0 0;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
  word-break: break-word;
}

/* Two-line clamp for the task preview. */
.at-node-task-clamp {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

.at-node-footer {
  margin-top: 6px;
  padding-top: 4px;
  border-top: 1px dashed rgba(128, 128, 128, 0.3);
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
  font-size: 11px;
}

/* Handles: enlarged connect area, accent-tinted, always visible in edit mode
   so the connect targets are discoverable. */
.member-flow-node :deep(.vue-flow__handle) {
  width: 8px;
  height: 28px;
  border: none;
  border-radius: 4px;
  background: rgba(148, 163, 184, 0.9);
}

.member-flow-node :deep(.vue-flow__handle-left) {
  left: -4px;
}

.member-flow-node :deep(.vue-flow__handle-right) {
  right: -4px;
}
</style>
