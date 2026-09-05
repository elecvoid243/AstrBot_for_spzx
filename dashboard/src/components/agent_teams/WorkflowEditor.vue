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

    <div v-if="bannerMessage || lastErrorFields.length" class="editor-banner">
      <strong>{{ tm('editor.validation') }}</strong>
      <span v-if="bannerMessage">{{ bannerMessage }}</span>
      <!-- Structured backend validation errors (e.g. execution block issues)
           published by useAgentTeams; the plain message is toasted there. -->
      <ul v-if="lastErrorFields.length" class="editor-banner-fields">
        <li v-for="(field, index) in lastErrorFields" :key="`${field.path}-${index}`">
          {{ field.path }}: {{ field.message }}
        </li>
      </ul>
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
        <div class="editor-exec mt-3">
          <v-btn size="small" variant="text" block class="exec-toggle" @click="toggleExecGroup">
            <v-icon size="small">{{ execOpen ? 'mdi-chevron-up' : 'mdi-chevron-down' }}</v-icon>
            {{ tm('editor.executionConfig') }}
          </v-btn>
          <template v-if="execOpen && selectedExec">
            <v-select
              v-model="selectedExec.config_id"
              :items="configProfileSelectItems"
              item-title="title"
              item-value="value"
              :label="tm('editor.configProfile')"
              density="compact"
              hide-details
              class="mt-2"
            />
            <v-checkbox-btn
              v-model="personaOverride"
              :label="tm('editor.personaOverride')"
              density="compact"
              hide-details
              class="mt-2"
            />
            <div v-if="personaOverride" class="mt-2">
              <PersonaSelector v-model="selectedExec.persona_id" />
              <p v-if="!selectedExec.persona_id" class="editor-exec-hint">
                {{ tm('editor.personaFollowProfile') }}
              </p>
            </div>
            <v-radio-group
              v-model="selectedToolsMode"
              :label="tm('editor.toolsOverride')"
              density="compact"
              hide-details
              class="mt-2"
            >
              <v-radio :label="tm('editor.toolsInherit')" value="inherit" density="compact" />
              <v-radio
                :label="tm('editor.toolsDisableAll')"
                value="disable_all"
                density="compact"
              />
              <v-radio :label="tm('editor.toolsAllowlist')" value="allowlist" density="compact" />
            </v-radio-group>
            <v-select
              v-if="selectedExec.toolsMode === 'allowlist'"
              v-model="selectedExec.tools"
              :items="toolOptions"
              multiple
              density="compact"
              hide-details
              class="mt-2"
            />
            <v-radio-group
              v-model="selectedSkillsMode"
              :label="tm('editor.skillsOverride')"
              density="compact"
              hide-details
              class="mt-2"
            >
              <v-radio :label="tm('editor.skillsInherit')" value="inherit" density="compact" />
              <v-radio
                :label="tm('editor.skillsAllowlist')"
                value="allowlist"
                density="compact"
              />
            </v-radio-group>
            <v-select
              v-if="selectedExec.skillsMode === 'allowlist'"
              v-model="selectedExec.skills"
              :items="skillOptions"
              multiple
              density="compact"
              hide-details
              class="mt-2"
            />
          </template>
        </div>
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
//   { name, graph: { nodes: [{id, member_id, task, execution?}], edges: [{from, to}] }, layout }
// Validation is mirrored client-side (cycle / duplicate / dangling / missing
// member / >20 nodes) and rendered as a live banner; saving goes through the
// useAgentTeams().saveWorkflow composable, which also toasts error envelopes
// and refreshes the workflows list. Structured backend validation fields are
// rendered in the same banner via the composable's lastErrorFields.
// Each node also carries an optional per-node `execution` override block
// (config profile / persona / tool+skill allowlists) edited in the inspector's
// collapsible "执行配置" group; the block is omitted from the payload when
// every field stays inherit.
import { computed, nextTick, ref, watch } from 'vue';
import TeamsFlowCanvas from './TeamsFlowCanvas.vue';
import type { FlowNode } from './TeamsFlowCanvas.vue';
import PersonaSelector from '@/components/shared/PersonaSelector.vue';
import { configProfileApi, skillApi, toolApi } from '@/api/v1';
import { useAgentTeams } from '@/composables/useAgentTeams';
import { useModuleI18n } from '@/i18n/composables';
import { useToast } from '@/utils/toast';
import { extractApiError } from '@/utils/extractApiError';
import { findCycle, renderableError } from '@/utils/dagCheck';
import type { DagCheckNode } from '@/utils/dagCheck';

// Mirrors AgentTeamService.MAX_NODES on the backend.
const MAX_NODES = 20;
const INPUT_TOKEN = '{{input}}';

/** Tool override modes; `inherit` keeps the backend field unset. */
type ExecToolsMode = 'inherit' | 'disable_all' | 'allowlist';
/** Skills have no disable-all mode (spec §2.3): inherit or allowlist only. */
type ExecSkillsMode = 'inherit' | 'allowlist';

/** Editable per-node execution override state (inspector binding target). */
interface NodeExecState {
  config_id: string;
  persona_id: string;
  /** UI toggle state; serialization keys off `persona_id` alone. */
  personaOn: boolean;
  tools: string[] | null;
  skills: string[] | null;
  toolsMode: ExecToolsMode;
  skillsMode: ExecSkillsMode;
}

const props = defineProps<{
  /** Currently selected team; drives the member pickers and save target. */
  team: any | null;
  /** Workflows of the selected team (rows carry name/graph/layout). */
  workflows: any[];
}>();

const { tm } = useModuleI18n('features/agent-teams');
const toast = useToast();
const { saveWorkflow, lastErrorFields } = useAgentTeams();

const selectedWorkflowId = ref('');
const workflowName = ref('');
const graphNodes = ref<FlowNode[]>([]);
const graphEdges = ref<{ id: string; source: string; target: string }[]>([]);
const layout = ref<Record<string, { x: number; y: number }>>({});
const selectedNodeId = ref<string | null>(null);
const addMemberId = ref('');
const saving = ref(false);
const taskAreaRef = ref<any>(null);

// --- Per-node execution override state ---------------------------------

const execOpen = ref(false);
const execOptionsLoaded = ref(false);
const configProfileOptions = ref<{ title: string; value: string }[]>([]);
const toolOptions = ref<{ title: string; value: string }[]>([]);
const skillOptions = ref<{ title: string; value: string }[]>([]);
const executionByNode = ref<Record<string, NodeExecState>>({});

/** Blank override state: everything follows the team defaults. */
function defaultExecState(): NodeExecState {
  return {
    config_id: '',
    persona_id: '',
    personaOn: false,
    tools: null,
    skills: null,
    toolsMode: 'inherit',
    skillsMode: 'inherit',
  };
}

/**
 * Parse a stored execution block into editable state.
 *
 * Args:
 *   raw: The `execution` value from a loaded graph node (may be undefined).
 *
 * Returns:
 *   The editable state; an empty/absent list maps to `inherit`.
 */
function parseExecState(raw: unknown): NodeExecState {
  const exec = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  const list = (value: unknown): string[] | null => {
    if (!Array.isArray(value)) return null;
    return value.map((item) => String(item)).filter((item) => item.trim());
  };
  const tools = list(exec.tools);
  const skills = list(exec.skills);
  return {
    config_id: typeof exec.config_id === 'string' ? exec.config_id : '',
    persona_id: typeof exec.persona_id === 'string' ? exec.persona_id : '',
    personaOn: typeof exec.persona_id === 'string' && exec.persona_id !== '',
    tools,
    skills,
    // `[]` is the backend's disable-all marker; any non-empty list is a filter.
    toolsMode: tools === null ? 'inherit' : tools.length === 0 ? 'disable_all' : 'allowlist',
    skillsMode: skills === null ? 'inherit' : 'allowlist',
  };
}

/**
 * Serialize editable state into the payload's `execution` block.
 *
 * Args:
 *   state: The node's editable execution state.
 *
 * Returns:
 *   The block with only overridden fields set, or null when every field
 *   stays inherit (keeps default payloads byte-identical to pre-execution).
 */
function serializeExecState(state: NodeExecState): Record<string, unknown> | null {
  const block: Record<string, unknown> = {};
  if (state.config_id.trim()) block.config_id = state.config_id;
  if (state.persona_id.trim()) block.persona_id = state.persona_id;
  if (state.toolsMode === 'disable_all') block.tools = [];
  else if (state.toolsMode === 'allowlist') block.tools = [...(state.tools ?? [])];
  if (state.skillsMode === 'allowlist') block.skills = [...(state.skills ?? [])];
  return Object.keys(block).length > 0 ? block : null;
}

/** Selected node's execution state (created lazily so bindings always resolve). */
const selectedExec = computed<NodeExecState | null>(() =>
  selectedNodeId.value ? (executionByNode.value[selectedNodeId.value] ?? null) : null,
);

/** Persona override toggle; off clears the persona so it follows the profile default. */
const personaOverride = computed<boolean>({
  get: () => !!selectedExec.value?.personaOn,
  set: (on) => {
    const exec = selectedExec.value;
    if (!exec) return;
    exec.personaOn = on;
    if (!on) exec.persona_id = '';
  },
});

/** Tools mode switch that guarantees an array exists in allowlist mode. */
const selectedToolsMode = computed<ExecToolsMode>({
  get: () => selectedExec.value?.toolsMode ?? 'inherit',
  set: (mode) => {
    const exec = selectedExec.value;
    if (!exec) return;
    exec.toolsMode = mode;
    if (mode === 'allowlist' && exec.tools === null) exec.tools = [];
  },
});

/** Skills mode switch that guarantees an array exists in allowlist mode. */
const selectedSkillsMode = computed<ExecSkillsMode>({
  get: () => selectedExec.value?.skillsMode ?? 'inherit',
  set: (mode) => {
    const exec = selectedExec.value;
    if (!exec) return;
    exec.skillsMode = mode;
    if (mode === 'allowlist' && exec.skills === null) exec.skills = [];
  },
});

/** Profile dropdown items: the "follow default" empty option plus profiles. */
const configProfileSelectItems = computed(() => [
  { title: tm('editor.configProfileDefault'), value: '' },
  ...configProfileOptions.value,
]);

/** Expand/collapse the execution group, loading option lists once. */
function toggleExecGroup() {
  execOpen.value = !execOpen.value;
  if (execOpen.value) void loadExecutionOptions();
}

/**
 * Lazily load config profile / tool / skill options on first expansion.
 *
 * Each list fails independently: a failure is toasted (non-fatal) and simply
 * leaves that list empty.
 */
async function loadExecutionOptions() {
  if (execOptionsLoaded.value) return;
  execOptionsLoaded.value = true;
  try {
    const res = await configProfileApi.list();
    if (res.data?.status === 'ok') {
      configProfileOptions.value = ((res.data.data?.info_list ?? []) as any[]).map((p) => ({
        title: String(p.name || p.id || ''),
        value: String(p.id || ''),
      }));
    }
  } catch (err) {
    toast.error(extractApiError(err, tm('errors.loadFailed')).message);
  }
  try {
    const res = await toolApi.list();
    if (res.data?.status === 'ok') {
      toolOptions.value = ((res.data.data ?? []) as any[])
        .filter((t) => t && t.name && t.active !== false)
        .map((t) => ({ title: String(t.name), value: String(t.name) }));
    }
  } catch (err) {
    toast.error(extractApiError(err, tm('errors.loadFailed')).message);
  }
  try {
    const res = await skillApi.list();
    if (res.data?.status === 'ok') {
      const payload = res.data.data ?? [];
      const skills = Array.isArray(payload) ? payload : (payload.skills ?? []);
      skillOptions.value = (skills as any[])
        .filter((s) => s && s.name && s.active !== false)
        .map((s) => ({ title: String(s.name), value: String(s.name) }));
    }
  } catch (err) {
    toast.error(extractApiError(err, tm('errors.loadFailed')).message);
  }
}

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
    return tm('editor.tooManyNodes', { count: graphNodes.value.length, max: MAX_NODES });
  }
  const graph = buildGraphPayload();
  const structural = renderableError(graph);
  if (structural) return structural;
  const cycle = findCycle(graph.nodes, graph.edges);
  if (cycle) return tm('editor.cycleDetected', { path: cycle.join(' → ') });
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
  executionByNode.value[id] = defaultExecState();
  selectedNodeId.value = id;
}

/** Remove the selected node plus every edge touching it. */
function deleteNode() {
  const id = selectedNodeId.value;
  if (!id) return;
  graphNodes.value = graphNodes.value.filter((n) => n.id !== id);
  graphEdges.value = graphEdges.value.filter((e) => e.source !== id && e.target !== id);
  delete layout.value[id];
  delete executionByNode.value[id];
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

// Ensure the selected node always has an execution state entry so inspector
// bindings resolve even for nodes created outside addNode (e.g. loaded graphs
// missing the block).
watch(selectedNodeId, (id) => {
  if (id && !executionByNode.value[id]) executionByNode.value[id] = defaultExecState();
});

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
    nodes: graphNodes.value.map((n) => {
      const node: DagCheckNode = {
        id: n.id,
        member_id: (n.data.memberId as string) ?? '',
        task: ((n.data.task as string) ?? '').trim(),
      };
      const exec = executionByNode.value[n.id];
      if (exec) {
        const execution = serializeExecState(exec);
        // Omitted entirely when every field stays inherit, so payloads for
        // default-state graphs stay byte-identical to the pre-execution format.
        if (execution) node.execution = execution;
      }
      return node;
    }),
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
  executionByNode.value = {};
  selectedNodeId.value = null;
  // Stale save-error fields belong to the discarded graph.
  lastErrorFields.value = [];
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
  executionByNode.value = {};
  // Stale save-error fields belong to the previous graph.
  lastErrorFields.value = [];
  const graph = (wf.graph ?? {}) as {
    nodes?: { id?: string; member_id?: string; task?: string; execution?: unknown }[];
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
    executionByNode.value[id] = parseExecState(gn.execution);
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

.editor-banner-fields {
  margin: 0;
  padding-left: 16px;
  word-break: break-word;
}

.editor-exec-hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
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
