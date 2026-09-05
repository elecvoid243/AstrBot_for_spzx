<template>
  <div class="workflow-editor">
    <div class="editor-toolbar">
      <v-select
        v-model="selectedWorkflowId"
        :items="workflowItems"
        item-title="title"
        item-value="value"
        class="workflow-picker"
        density="compact"
        hide-details
        style="max-width: 220px"
      />
      <v-text-field
        v-model="workflowName"
        :label="tm('editor.workflowName')"
        density="compact"
        hide-details
        style="max-width: 220px"
      />
      <v-select
        v-model="addMemberId"
        :items="memberItems"
        item-title="title"
        item-value="value"
        :label="tm('editor.nodeMember')"
        density="compact"
        hide-details
        class="add-node-picker"
        style="max-width: 180px"
      />
      <v-btn variant="text" prepend-icon="mdi-plus" :disabled="memberItems.length === 0" @click="addNode">
        {{ tm('editor.addNode') }}
      </v-btn>
      <v-btn variant="text" color="error" :disabled="!selectedNodeId" @click="deleteNode">
        {{ tm('editor.deleteNode') }}
      </v-btn>
      <v-spacer />
      <v-btn variant="tonal" :loading="saving" @click="save">
        {{ tm('editor.save') }}
      </v-btn>
    </div>

    <div v-if="bannerMessage" class="editor-banner">
      <strong>{{ tm('editor.validation') }}</strong>
      <span>{{ bannerMessage }}</span>
    </div>

    <div class="editor-body">
      <div class="editor-canvas">
        <TeamsFlowCanvas
          mode="edit"
          :nodes="displayNodes"
          :edges="graphEdges"
          @connect="onConnect"
          @positionChange="onPositionChange"
          @selectNode="onSelectNode"
        />
      </div>

      <aside v-if="selectedNode" class="editor-inspector">
        <v-select
          v-model="selectedMemberId"
          :items="memberItems"
          item-title="title"
          item-value="value"
          :label="tm('editor.nodeMember')"
          density="compact"
          hide-details
        />
        <v-textarea
          ref="taskAreaRef"
          v-model="selectedTask"
          :label="tm('editor.nodeTask')"
          rows="5"
          density="compact"
          hide-details
          class="mt-3"
        />
        <v-btn size="small" variant="tonal" class="mt-2" @click="insertInputToken">
          {{ tm('editor.insertInput') }}
        </v-btn>
        <p class="editor-task-hint">{{ tm('editor.nodeTaskHint') }}</p>
      </aside>
      <aside v-else class="editor-inspector-placeholder">{{ tm('editor.selectNode') }}</aside>
    </div>
  </div>
</template>

<script setup lang="ts">
// ComfyUI-style DAG editor for one team's workflows (Task 7). Owns the
// workflow name, the node/edge graph and a nodeId -> position layout map that
// round-trips through the backend payload:
//   { name, graph: { nodes: [{id, member_id, task}], edges: [{from, to}] }, layout }
// Validation is mirrored client-side (cycle / duplicate / dangling / missing
// member / >20 nodes) and rendered as a live banner; saving goes through the
// useAgentTeams().saveWorkflow composable, which also toasts error envelopes
// and refreshes the workflows list.
import { computed, nextTick, ref, watch } from 'vue';
import TeamsFlowCanvas from './TeamsFlowCanvas.vue';
import type { FlowNode } from './TeamsFlowCanvas.vue';
import { useAgentTeams } from '@/composables/useAgentTeams';
import { useModuleI18n } from '@/i18n/composables';
import { useToast } from '@/utils/toast';
import { findCycle, renderableError } from '@/utils/dagCheck';

// Mirrors AgentTeamService.MAX_NODES on the backend.
const MAX_NODES = 20;
const INPUT_TOKEN = '{{input}}';

const props = defineProps<{
  /** Currently selected team; drives the member pickers and save target. */
  team: any | null;
  /** Workflows of the selected team (rows carry name/graph/layout). */
  workflows: any[];
}>();

const { tm } = useModuleI18n('features/agent-teams');
const toast = useToast();
const { saveWorkflow } = useAgentTeams();

const selectedWorkflowId = ref('');
const workflowName = ref('');
const graphNodes = ref<FlowNode[]>([]);
const graphEdges = ref<{ id: string; source: string; target: string }[]>([]);
const layout = ref<Record<string, { x: number; y: number }>>({});
const selectedNodeId = ref<string | null>(null);
const addMemberId = ref('');
const saving = ref(false);
const taskAreaRef = ref<any>(null);

const memberItems = computed(() =>
  (props.team?.members ?? []).map((m: any) => ({
    title: m.name ?? m.member_id,
    value: m.member_id,
  })),
);

const workflowItems = computed(() => [
  { title: tm('editor.newWorkflow'), value: '' },
  ...props.workflows.map((w) => ({ title: w.name || w.workflow_id, value: w.workflow_id })),
]);

const selectedNode = computed(
  () => graphNodes.value.find((n) => n.id === selectedNodeId.value) ?? null,
);

/** Member ids currently on the team roster. */
const memberIds = computed(
  () => new Set((props.team?.members ?? []).map((m: any) => m.member_id)),
);

/** Nodes flagged with `at-node-missing` when their member was removed. */
const displayNodes = computed(() =>
  graphNodes.value.map((node) => {
    const missing = !!node.data.memberId && !memberIds.value.has(node.data.memberId);
    if (!missing) return node;
    return { ...node, class: 'at-node-missing' };
  }),
);

/** Two-way binding onto the selected node's member (updates the label too). */
const selectedMemberId = computed<string>({
  get: () => (selectedNode.value?.data.memberId as string) ?? '',
  set: (v) => {
    const node = selectedNode.value;
    if (!node) return;
    node.data.memberId = v;
    node.data.memberName = memberNameOf(v);
    node.data.label = `${node.data.memberName} (${node.id})`;
  },
});

/** Two-way binding onto the selected node's task template. */
const selectedTask = computed<string>({
  get: () => (selectedNode.value?.data.task as string) ?? '',
  set: (v) => {
    if (selectedNode.value) selectedNode.value.data.task = v;
  },
});

function memberNameOf(memberId: string): string {
  const member = (props.team?.members ?? []).find((m: any) => m.member_id === memberId);
  return (member?.name as string) ?? memberId;
}

/** Live validation banner: missing member / node overflow / structural / cycle. */
const bannerMessage = computed(() => {
  if (graphNodes.value.some((n) => n.data.memberId && !memberIds.value.has(n.data.memberId))) {
    return tm('editor.missingMember');
  }
  if (graphNodes.value.length > MAX_NODES) {
    return `Too many nodes: ${graphNodes.value.length} (max ${MAX_NODES})`;
  }
  const graph = buildGraphPayload();
  const structural = renderableError(graph);
  if (structural) return structural;
  const cycle = findCycle(graph.nodes, graph.edges);
  if (cycle) return `Cycle detected: ${cycle.join(' → ')}`;
  return '';
});

/** Append a node for the picked member with auto id `n{max+1}`. */
function addNode() {
  if (!addMemberId.value) {
    addMemberId.value = memberItems.value[0]?.value ?? '';
  }
  if (!addMemberId.value) return;
  const maxId = graphNodes.value.reduce((max, node) => {
    const match = /^n(\d+)$/.exec(node.id);
    return match ? Math.max(max, Number(match[1])) : max;
  }, 0);
  const id = `n${maxId + 1}`;
  const index = graphNodes.value.length;
  const position = {
    x: 60 + (index % 4) * 200,
    y: 60 + Math.floor(index / 4) * 130,
  };
  const memberName = memberNameOf(addMemberId.value);
  graphNodes.value.push({
    id,
    position,
    data: { label: `${memberName} (${id})`, memberName, memberId: addMemberId.value, task: '' },
  });
  layout.value[id] = position;
  selectedNodeId.value = id;
}

/** Remove the selected node plus every edge touching it. */
function deleteNode() {
  const id = selectedNodeId.value;
  if (!id) return;
  graphNodes.value = graphNodes.value.filter((n) => n.id !== id);
  graphEdges.value = graphEdges.value.filter((e) => e.source !== id && e.target !== id);
  delete layout.value[id];
  selectedNodeId.value = null;
}

/** Append a normalized edge from the canvas, deduplicating repeats. */
function onConnect(params: { from: string; to: string }) {
  const id = `e:${params.from}->${params.to}`;
  if (graphEdges.value.some((e) => e.id === id)) return;
  graphEdges.value = [...graphEdges.value, { id, source: params.from, target: params.to }];
}

function onPositionChange(positions: Record<string, { x: number; y: number }>) {
  Object.assign(layout.value, positions);
  // Keep node positions in sync as well: VueFlow re-applies the `nodes` prop
  // whenever the graph re-renders (addNode, data edits), and stale positions
  // there would snap dragged nodes back.
  for (const node of graphNodes.value) {
    const position = positions[node.id];
    if (position) node.position = { ...position };
  }
}

function onSelectNode(nodeId: string | null) {
  selectedNodeId.value = nodeId;
}

/** Insert the `{{input}}` token at the textarea cursor (or at the end). */
function insertInputToken() {
  const root = taskAreaRef.value?.$el as HTMLElement | undefined;
  const el = ((root?.querySelector?.('textarea') as HTMLTextAreaElement | null) ??
    (root as HTMLTextAreaElement | null)) as HTMLTextAreaElement | null;
  const current = selectedTask.value ?? '';
  if (el && typeof el.selectionStart === 'number') {
    const start = el.selectionStart;
    const end = el.selectionEnd;
    selectedTask.value = current.slice(0, start) + INPUT_TOKEN + current.slice(end);
    const cursor = start + INPUT_TOKEN.length;
    void nextTick(() => {
      el.focus?.();
      el.setSelectionRange?.(cursor, cursor);
    });
  } else {
    selectedTask.value = current + INPUT_TOKEN;
  }
}

/** Current editor content as the backend graph payload. */
function buildGraphPayload() {
  return {
    nodes: graphNodes.value.map((n) => ({
      id: n.id,
      member_id: (n.data.memberId as string) ?? '',
      task: ((n.data.task as string) ?? '').trim(),
    })),
    edges: graphEdges.value.map((e) => ({ from: e.source, to: e.target })),
  };
}

async function save() {
  if (!props.team || bannerMessage.value) return;
  const layoutPayload: Record<string, { x: number; y: number }> = {};
  for (const node of graphNodes.value) {
    layoutPayload[node.id] = { ...(layout.value[node.id] ?? node.position) };
  }
  const payload: Record<string, unknown> = {
    name: workflowName.value.trim(),
    graph: buildGraphPayload(),
    layout: layoutPayload,
  };
  if (selectedWorkflowId.value) payload.workflow_id = selectedWorkflowId.value;
  saving.value = true;
  try {
    const result = await saveWorkflow(props.team.team_id, payload as any);
    if (result === null) return; // error envelope already toasted by the composable
    toast.success(tm('editor.saveSuccess'));
    // Track the saved row so further saves update instead of create.
    if (result?.workflow_id) selectedWorkflowId.value = result.workflow_id;
  } finally {
    saving.value = false;
  }
}

/** Blank the editor (used for the "new workflow" option). */
function resetBlank() {
  workflowName.value = '';
  graphNodes.value = [];
  graphEdges.value = [];
  layout.value = {};
  selectedNodeId.value = null;
}

// Switching teams blanks the editor: workflow ids and member bindings are
// team-scoped, so nothing from the previous team can be reused.
watch(
  () => props.team?.team_id,
  () => {
    selectedWorkflowId.value = '';
    resetBlank();
  },
);

// Loading an existing workflow populates name, graph and layout; picking the
// "new workflow" option blanks the editor. Unknown ids keep the current state
// (e.g. right after a save, before the refreshed rows reach the prop).
watch(selectedWorkflowId, (wfId) => {  selectedNodeId.value = null;
  if (!wfId) {
    resetBlank();
    return;
  }
  const wf = props.workflows.find((w) => w.workflow_id === wfId);
  if (!wf) return;
  workflowName.value = wf.name ?? '';
  graphNodes.value = [];
  graphEdges.value = [];
  layout.value = {};
  const graph = (wf.graph ?? {}) as {
    nodes?: { id?: string; member_id?: string; task?: string }[];
    edges?: { from?: string; to?: string }[];
  };
  const wfLayout = (wf.layout ?? {}) as Record<string, { x?: number; y?: number }>;
  for (const gn of graph.nodes ?? []) {
    const id = String(gn.id ?? '');
    if (!id) continue;
    const position = { x: Number(wfLayout[id]?.x ?? 0), y: Number(wfLayout[id]?.y ?? 0) };
    layout.value[id] = { ...position };
    const memberId = String(gn.member_id ?? '');
    const memberName = memberNameOf(memberId);
    graphNodes.value.push({
      id,
      position,
      data: { label: `${memberName} (${id})`, memberName, memberId, task: String(gn.task ?? '') },
    });
  }
  for (const ge of graph.edges ?? []) {
    if (!ge?.from || !ge?.to) continue;
    graphEdges.value.push({
      id: `e:${ge.from}->${ge.to}`,
      source: String(ge.from),
      target: String(ge.to),
    });
  }
});
</script>

<style scoped>
.editor-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.editor-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  border: 1px solid rgba(248, 113, 113, 0.6);
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.12);
  color: inherit;
  font-size: 13px;
}

.editor-body {
  display: flex;
  align-items: stretch;
  gap: 12px;
}

.editor-canvas {
  flex: 1;
  min-width: 0;
  height: 520px;
}

.editor-inspector {
  width: 300px;
  flex-shrink: 0;
  padding: 12px;
  border: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 12px;
}

.editor-inspector-placeholder {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  border: 1px dashed var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 12px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
  font-size: 13px;
  text-align: center;
}

.editor-task-hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
  word-break: break-word;
}

@media (max-width: 1100px) {
  .editor-body {
    flex-direction: column;
  }

  .editor-inspector,
  .editor-inspector-placeholder {
    width: 100%;
  }
}
</style>
