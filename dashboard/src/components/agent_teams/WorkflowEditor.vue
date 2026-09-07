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
      <div class="editor-name-wrap">
        <v-text-field
          v-model="workflowName"
          :label="tm('editor.workflowName')"
          density="compact"
          hide-details
          style="max-width: 220px"
        />
        <span
          v-if="isDirty"
          class="editor-unsaved-dot"
          role="img"
          :title="tm('editor.unsaved')"
          :aria-label="tm('editor.unsaved')"
        />
      </div>
      <div class="editor-validation">
        <v-btn
          variant="text"
          size="small"
          :disabled="localProblems.length === 0"
          :aria-label="tm('editor.validationBadge')"
          @click="problemsOpen = !problemsOpen"
        >
          <v-icon size="small" aria-hidden="true">mdi-alert-circle-outline</v-icon>
          {{ tm('editor.validationBadge') }}
          <span class="editor-problem-count">{{ localProblems.length }}</span>
        </v-btn>
        <ul v-if="problemsOpen" class="editor-problems-list">
          <li v-for="problem in localProblems" :key="problem.key">
            <button
              type="button"
              class="editor-problem-item"
              :disabled="!problem.nodeId"
              @click="onProblemClick(problem)"
            >
              {{ problem.message }}
            </button>
          </li>
        </ul>
      </div>
      <v-btn variant="text" color="error" :disabled="!selectedNodeId" @click="deleteNode">
        {{ tm('editor.deleteNode') }}
      </v-btn>
      <!-- ④ Only narrow viewports need this: the palette is docked otherwise. -->
      <v-btn
        v-if="!lgAndUp"
        variant="text"
        data-test="open-members"
        @click="membersOpen = true"
      >
        {{ tm('editor.membersPanel') }}
      </v-btn>
      <v-spacer />
      <v-btn variant="tonal" :loading="saving" @click="save">
        {{ tm('editor.save') }}
      </v-btn>
      <!-- ③ Save-and-run: the common path after editing a workflow is to run
           it, so the page switches to the monitor tab with this workflow
           preselected once the save succeeds. -->
      <v-btn
        variant="text"
        color="primary"
        data-test="save-and-run"
        :loading="saving"
        @click="saveAndRun"
      >
        {{ tm('editor.saveAndRun') }}
      </v-btn>
    </div>

    <div v-if="bannerMessage || displayErrorFields.length" class="editor-banner">
      <strong>{{ tm('editor.validation') }}</strong>
      <span v-if="bannerMessage">{{ bannerMessage }}</span>
      <!-- Structured backend validation errors (e.g. execution block issues)
           published by useAgentTeams; the plain message is toasted there.
           Entries pointing at a graph node (`nodes.<id>...`) select that node
           in the inspector; messages already shown locally are deduplicated. -->
      <ul v-if="displayErrorFields.length" class="editor-banner-fields">
        <li v-for="(field, index) in displayErrorFields" :key="`${field.path}-${index}`">
          <button
            v-if="fieldNodeId(field.path)"
            type="button"
            class="editor-banner-field"
            @click="onFieldClick(field.path)"
          >
            {{ field.path }}: {{ field.message }}
          </button>
          <span v-else class="editor-banner-field">{{ field.path }}: {{ field.message }}</span>
        </li>
      </ul>
    </div>

    <!-- Workbench body: member strip | canvas | inspector drawer. Rendered as
         a nested v-layout so the drawer registers in a local layout instead of
         the app-level one (v-main content never shifts when it opens). -->
    <v-layout class="editor-body" :class="{ 'has-inspector': lgAndUp && inspectorOpen }">
      <!-- ④ Member palette: docked strip on wide viewports, temporary drawer on
           narrow ones (the canvas needs the width more than the palette does). -->
      <v-navigation-drawer
        v-if="!lgAndUp"
        v-model="membersOpen"
        temporary
        location="left"
        :width="240"
        class="editor-members-drawer"
        data-test="members-drawer"
      >
        <aside class="editor-members" :aria-label="tm('editor.membersPanel')">
          <span class="editor-members-title">{{ tm('editor.membersPanel') }}</span>
          <p class="editor-members-hint">{{ tm('editor.dropHint') }}</p>
          <button
            v-for="row in memberRows"
            :key="'drawer-' + row.memberId"
            type="button"
            class="editor-member-row editor-member-row-drawer"
            draggable="true"
            :aria-label="row.name"
            @dragstart="onMemberDragStart($event, row.memberId)"
            @click="addNode(row.memberId); membersOpen = false"
          >
            <i class="editor-member-dot" :style="{ background: row.color }" aria-hidden="true" />
            <span class="editor-member-name">{{ row.name }}</span>
            <span v-if="row.isCoordinator" class="editor-member-coord">
              {{ tm('teams.coordinator') }}
            </span>
            <span class="editor-member-used">
              {{ tm('editor.usedCount', { n: row.usedCount }) }}
            </span>
            <span v-if="row.summary" class="editor-member-summary" :title="row.summary">{{ row.summary }}</span>
          </button>
        </aside>
      </v-navigation-drawer>

      <aside
        class="editor-members"
        :class="{ 'is-compact': !lgAndUp }"
        :aria-label="tm('editor.membersPanel')"
      >
        <span class="editor-members-title">{{ tm('editor.membersPanel') }}</span>
        <p class="editor-members-hint">{{ tm('editor.dropHint') }}</p>
        <button
          v-for="row in memberRows"
          :key="row.memberId"
          type="button"
          class="editor-member-row"
          draggable="true"
          :aria-label="row.name"
          @dragstart="onMemberDragStart($event, row.memberId)"
          @click="addNode(row.memberId)"
        >
          <i class="editor-member-dot" :style="{ background: row.color }" aria-hidden="true" />
          <span class="editor-member-name">{{ row.name }}</span>
          <span v-if="row.isCoordinator" class="editor-member-coord">
            {{ tm('teams.coordinator') }}
          </span>
          <span class="editor-member-used">{{ tm('editor.usedCount', { n: row.usedCount }) }}</span>
          <span v-if="row.summary" class="editor-member-summary" :title="row.summary">{{ row.summary }}</span>
        </button>
      </aside>

      <div class="editor-canvas" role="region" :aria-label="tm('editor.dropHint')">
        <!-- Floating canvas toolbar: undo/redo, clipboard, layout aids and the
             search box; only editor-level actions live here (zoom/fit stays in
             VueFlow's built-in Controls). -->
        <div class="editor-canvas-toolbar" data-test="canvas-toolbar">
          <div class="editor-tb-group">
            <v-btn
              size="small"
              :disabled="!canUndo"
              :title="tm('editor.undo')"
              data-test="undo"
              @click="undo"
            >
              <v-icon>mdi-undo</v-icon>
            </v-btn>
            <v-btn
              size="small"
              :disabled="!canRedo"
              :title="tm('editor.redo')"
              data-test="redo"
              @click="redo"
            >
              <v-icon>mdi-redo</v-icon>
            </v-btn>
          </div>
          <div class="editor-tb-group">
            <v-btn
              size="small"
              :disabled="!selectedNodeId"
              :title="tm('editor.copyNode')"
              data-test="copy-node"
              @click="copyNode"
            >
              <v-icon>mdi-content-copy</v-icon>
            </v-btn>
            <v-btn
              size="small"
              :disabled="!clipboard"
              :title="tm('editor.pasteNode')"
              data-test="paste-node"
              @click="pasteNode"
            >
              <v-icon>mdi-content-paste</v-icon>
            </v-btn>
            <v-btn
              size="small"
              :disabled="!canDeleteSelection"
              :title="tm('editor.deleteSelection')"
              data-test="delete-selection"
              @click="deleteSelection"
            >
              <v-icon>mdi-delete-outline</v-icon>
            </v-btn>
          </div>
          <div class="editor-tb-group">
            <v-btn size="small" :title="tm('editor.autoLayout')" data-test="auto-layout" @click="autoLayout">
              <v-icon>mdi-auto-fix</v-icon>
            </v-btn>
            <v-btn
              size="small"
              :title="snapToGrid ? tm('editor.snapGridOff') : tm('editor.snapGridOn')"
              data-test="snap-grid"
              @click="snapToGrid = !snapToGrid"
            >
              <v-icon>{{ snapToGrid ? 'mdi-grid' : 'mdi-grid-off' }}</v-icon>
            </v-btn>
            <v-btn
              size="small"
              :title="tm('editor.minimap')"
              data-test="minimap"
              @click="minimapVisible = !minimapVisible"
            >
              <v-icon>mdi-map-outline</v-icon>
            </v-btn>
          </div>
          <div class="editor-search-bar">
            <input
              v-model="searchQuery"
              class="editor-search-input"
              :placeholder="tm('editor.searchPlaceholder')"
              :aria-label="tm('editor.searchPlaceholder')"
              data-test="search-input"
            />
            <v-chip
              v-if="searchQuery && matchedNodes.length"
              size="small"
              variant="text"
              data-test="search-count"
            >
              {{ tm('editor.nodeSearchCount', { count: matchedNodes.length }) }}
            </v-chip>
          </div>
          <v-btn
            v-if="bulkDeleteEnabled"
            variant="outlined"
            color="error"
            data-test="bulk-delete"
            @click="bulkDelete"
          >
            <v-icon start>mdi-delete-multiple</v-icon>
            {{ tm('editor.bulkDeleteAction', { count: selectedNodeIds.size }) }}
          </v-btn>
        </div>
        <TeamsFlowCanvas
          ref="canvasRef"
          mode="edit"
          :nodes="displayNodes"
          :edges="graphEdges"
          :minimap-visible="minimapVisible"
          :snap-to-grid="snapToGrid"
          :selected-edge-id="selectedEdgeId"
          @connect="onConnect"
          @positionChange="onPositionChange"
          @selectNode="selectNode"
          @dropAt="onDropAt"
          @delete-edge="onDeleteEdge"
          @selection-change="onSelectionChange"
          @select-edge="onSelectEdge"
        />
      </div>

      <v-navigation-drawer
        v-model="inspectorOpen"
        location="right"
        :temporary="!lgAndUp"
        :width="340"
        class="editor-inspector-drawer"
      >
        <div v-if="selectedNode" class="editor-inspector">
          <!-- ① Basic info card: id + member as labelled rows (spec §3.1). -->
          <div class="inspector-card" data-test="basic-info">
            <div class="inspector-card-title">
              <v-icon size="small" aria-hidden="true">mdi-information-outline</v-icon>
              {{ tm('editor.basicInfo') }}
            </div>
            <div class="inspector-card-body">
              <div class="info-row">
                <span class="info-label">{{ tm('editor.basicNodeId') }}</span>
                <v-chip size="small" variant="tonal" color="primary">{{ selectedNodeId }}</v-chip>
              </div>
              <div class="info-row">
                <span class="info-label">{{ tm('editor.basicMember') }}</span>
                <v-chip
                  v-if="selectedNodeMemberName"
                  size="small"
                  color="primary"
                  prepend-icon="mdi-account-circle"
                >
                  {{ selectedNodeMemberName }}
                </v-chip>
                <v-chip v-else size="small" color="warning" variant="outlined">
                  <v-icon start size="small">mdi-alert</v-icon>
                  {{ tm('editor.memberMissing') }}
                </v-chip>
              </div>
            </div>
          </div>

          <!-- ① Dependencies card: side-by-side upstream / downstream (spec §3.1). -->
          <div class="inspector-card" data-test="relations">
            <div class="inspector-card-title">
              <v-icon size="small" aria-hidden="true">mdi-graph-outline</v-icon>
              {{ tm('editor.upstreamList') }} / {{ tm('editor.downstreamList') }}
            </div>
            <div class="inspector-card-body relation-cols">
              <div class="editor-relation-block">
                <div class="relation-header">
                  <v-icon size="x-small" color="primary" aria-hidden="true">mdi-arrow-left-circle</v-icon>
                  <span class="text-caption ml-1">{{ tm('editor.upstreamList') }}</span>
                </div>
                <div v-if="upstreamNodeIds.length" class="editor-relation-chips">
                  <v-chip
                    v-for="id in upstreamNodeIds"
                    :key="'up-' + id"
                    size="x-small"
                    variant="tonal"
                    data-test="upstream-chip"
                    @click="selectNode(id)"
                  >
                    {{ id }}
                  </v-chip>
                </div>
                <span v-else class="editor-relation-empty">{{ tm('editor.relationsEmpty') }}</span>
              </div>
              <div class="editor-relation-block">
                <div class="relation-header">
                  <v-icon size="x-small" color="primary" aria-hidden="true">mdi-arrow-right-circle</v-icon>
                  <span class="text-caption ml-1">{{ tm('editor.downstreamList') }}</span>
                </div>
                <div v-if="downstreamNodeIds.length" class="editor-relation-chips">
                  <v-chip
                    v-for="id in downstreamNodeIds"
                    :key="'down-' + id"
                    size="x-small"
                    variant="tonal"
                    data-test="downstream-chip"
                    @click="selectNode(id)"
                  >
                    {{ id }}
                  </v-chip>
                </div>
                <span v-else class="editor-relation-empty">{{ tm('editor.relationsEmpty') }}</span>
              </div>
            </div>
          </div>

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
                  :label="tm('editor.skillsDisableAll')"
                  value="disable_all"
                  density="compact"
                />
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
          <div class="editor-var-chips">
            <span class="editor-var-chips-label">{{ tm('editor.variableChips') }}</span>
            <button
              type="button"
              class="editor-var-chip editor-var-chip-input"
              :aria-label="tm('editor.insertInput')"
              @click="insertToken(INPUT_TOKEN)"
            >
              {{ INPUT_TOKEN }}
            </button>
            <button
              v-for="chip in predecessorChips"
              :key="chip.id"
              type="button"
              class="editor-var-chip"
              :aria-label="tm('editor.upstreamChip', { id: chip.id })"
              @click="insertToken(`{{${chip.id}}}`)"
            >
              {{ chip.label }}
            </button>
            <v-btn size="small" variant="tonal" @click="insertToken(INPUT_TOKEN)">
              {{ tm('editor.insertInput') }}
            </v-btn>
          </div>
          <v-textarea
            ref="taskAreaRef"
            v-model="selectedTask"
            :label="tm('editor.nodeTask')"
            rows="5"
            auto-grow
            max-rows="8"
            density="compact"
            hide-details
            class="mt-3"
          />
          <ul v-if="selectedUnconnected.length" class="editor-unconnected">
            <li v-for="ref in selectedUnconnected" :key="ref">
              {{ tm('editor.unconnectedRef', { id: ref }) }}
            </li>
          </ul>
          <p class="editor-task-hint">{{ taskHintText }}</p>
        </div>
      </v-navigation-drawer>
    </v-layout>
  </div>
</template>

<script setup lang="ts">
// ComfyUI-style DAG editor for one team's workflows (Task 7), reworked into a
// three-column workbench (Task 4): a draggable member strip, the flow canvas
// and a right-hand inspector drawer (docked on wide viewports, temporary
// overlay on narrow ones). Owns the workflow name, the node/edge graph and a
// nodeId -> position layout map that round-trips through the backend payload:
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
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { useDisplay } from 'vuetify';
import TeamsFlowCanvas from './TeamsFlowCanvas.vue';
import type { FlowNode } from './TeamsFlowCanvas.vue';
import PersonaSelector from '@/components/shared/PersonaSelector.vue';
import { configProfileApi, skillApi, toolApi } from '@/api/v1';
import { useAgentTeams } from '@/composables/useAgentTeams';
import { useModuleI18n } from '@/i18n/composables';
import { useToast } from '@/utils/toast';
import { collabMemberColor } from '@/utils/memberColors';
import { extractApiError } from '@/utils/extractApiError';
import type { ApiErrorField } from '@/utils/extractApiError';
import { findCycle, renderableError, unconnectedReferences } from '@/utils/dagCheck';
import type { DagCheckNode } from '@/utils/dagCheck';

// Mirrors AgentTeamService.MAX_NODES on the backend.
const MAX_NODES = 20;
const INPUT_TOKEN = '{{input}}';

/** Deep-clone plain editor data (nodes/edges/layout/execution) for snapshots. */
function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** Tool override modes; `inherit` keeps the backend field unset. */
type ExecToolsMode = 'inherit' | 'disable_all' | 'allowlist';
/** Skills use the same three-state override as tools (spec §2.2/§2.6). */
type ExecSkillsMode = 'inherit' | 'disable_all' | 'allowlist';

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

// Plan 3 T8: after a successful save-and-run the page switches to the monitor
// tab with this workflow preselected (the editor never starts runs itself).
const emit = defineEmits<{ saveAndRun: [workflowId: string] }>();

const { tm } = useModuleI18n('features/agent-teams');
const toast = useToast();
const { saveWorkflow, lastErrorFields } = useAgentTeams();
const { lgAndUp } = useDisplay();

const selectedWorkflowId = ref('');
const workflowName = ref('');
// ④ Narrow viewports show the member palette as a temporary drawer; wide ones
// keep the docked strip, so this flag only drives the drawer.
const membersOpen = ref(false);
const graphNodes = ref<FlowNode[]>([]);
const graphEdges = ref<{ id: string; source: string; target: string }[]>([]);
const layout = ref<Record<string, { x: number; y: number }>>({});
const selectedNodeId = ref<string | null>(null);
// Currently selected edge (set by the canvas on edge click); drives the
// canvas highlight and the toolbar's delete action.
const selectedEdgeId = ref<string | null>(null);
const saving = ref(false);
const taskAreaRef = ref<any>(null);
// Inspector drawer visibility: opens on node select, closes on deselect
// (canvas pane click, delete, workflow switch) and via the drawer itself.
const inspectorOpen = ref(false);

// --- Per-node execution override state ---------------------------------

const execOpen = ref(false);
const execOptionsLoaded = ref(false);
const configProfileOptions = ref<{ title: string; value: string }[]>([]);
const toolOptions = ref<{ title: string; value: string }[]>([]);
const skillOptions = ref<{ title: string; value: string }[]>([]);
const executionByNode = ref<Record<string, NodeExecState>>({});

// --- Undo / redo history ------------------------------------------------
// Snapshots of the whole editor graph are recorded before each structural
// mutation (add/remove node, add/remove edge, paste). The initial state is
// seeded when the editor blanks or loads a workflow so Ctrl+Z returns to it.
interface HistorySnapshot {
  nodes: FlowNode[];
  edges: { id: string; source: string; target: string }[];
  layout: Record<string, { x: number; y: number }>;
  executionByNode: Record<string, NodeExecState>;
}

const history = ref<HistorySnapshot[]>([]);
const historyIndex = ref(-1);
const MAX_HISTORY = 50;
const canUndo = computed(() => historyIndex.value > 0);
const canRedo = computed(() => historyIndex.value < history.value.length - 1);

function makeSnapshot(): HistorySnapshot {
  return {
    nodes: deepClone(graphNodes.value),
    edges: deepClone(graphEdges.value),
    layout: deepClone(layout.value),
    executionByNode: deepClone(executionByNode.value),
  };
}

/** Seal the current state as a history entry (branch-friendly). */
function pushHistory() {
  history.value = history.value.slice(0, historyIndex.value + 1);
  history.value.push(makeSnapshot());
  if (history.value.length > MAX_HISTORY) history.value.shift();
  historyIndex.value = history.value.length - 1;
}

/** Restore a snapshot and drop the selection if the node is gone. */
function restoreSnapshot(snapshot: HistorySnapshot) {
  graphNodes.value = deepClone(snapshot.nodes);
  graphEdges.value = deepClone(snapshot.edges);
  layout.value = deepClone(snapshot.layout);
  executionByNode.value = deepClone(snapshot.executionByNode);
  // A box selection may reference nodes the restored graph no longer has.
  selectedNodeIds.value.clear();
  if (selectedNodeId.value && !graphNodes.value.some((n) => n.id === selectedNodeId.value)) {
    selectNode(null);
  }
  if (selectedEdgeId.value && !graphEdges.value.some((e) => e.id === selectedEdgeId.value)) {
    selectedEdgeId.value = null;
  }
}

/**
 * Re-seed history to a single entry representing the current graph (blank or
 * freshly loaded). Called whenever the editor content is replaced wholesale.
 */
function resetHistory() {
  history.value = [makeSnapshot()];
  historyIndex.value = 0;
}

// Debounced checkpoint for edits that mutate the graph without going through
// a structural op (task text, member swap, exec config, node drags). Without
// it, Ctrl+Z would rewind past the edit and destroy it permanently.
let pushTimer: ReturnType<typeof setTimeout> | null = null;

function contentSignature(): string {
  return JSON.stringify(makeSnapshot());
}

function sealedSignature(): string {
  const entry = history.value[historyIndex.value];
  return entry ? JSON.stringify(entry) : '';
}

// Any graph mutation (structural or inspector-driven) schedules a trailing
// checkpoint. The signature guard skips loads and undo restores, where the
// state already equals the sealed entry, and absorbs the immediate pushes
// done by the structural ops.
watch(
  [graphNodes, graphEdges, layout, executionByNode],
  () => {
    if (pushTimer !== null) clearTimeout(pushTimer);
    pushTimer = setTimeout(() => {
      pushTimer = null;
      if (contentSignature() !== sealedSignature()) pushHistory();
    }, 300);
  },
  { deep: true },
);

/** Seal any pending debounced edit so the next undo step lands on it. */
function flushPendingHistory() {
  if (pushTimer === null) return;
  clearTimeout(pushTimer);
  pushTimer = null;
  if (contentSignature() !== sealedSignature()) pushHistory();
}

function undo() {
  flushPendingHistory();
  if (!canUndo.value) return;
  historyIndex.value -= 1;
  restoreSnapshot(history.value[historyIndex.value]!);
}

function redo() {
  flushPendingHistory();
  if (!canRedo.value) return;
  historyIndex.value += 1;
  restoreSnapshot(history.value[historyIndex.value]!);
}

// --- Node copy / paste ---------------------------------------------------

/** Clipboard holds the node payload plus its execution override (may be null). */
interface ClipboardData {
  node: FlowNode;
  exec: NodeExecState | null;
}

const clipboard = ref<ClipboardData | null>(null);

/** Copy the selected node (payload + execution override) to the clipboard. */
function copyNode() {
  const node = selectedNode.value;
  if (!node) return;
  clipboard.value = {
    node: deepClone(node),
    exec: executionByNode.value[node.id]
      ? deepClone(executionByNode.value[node.id]!)
      : null,
  };
  toast.success(tm('editor.nodeCopied', { id: node.id }));
}

/** Paste a clipboard node at a +20/+20 offset with a fresh auto id. */
function pasteNode() {
  const source = clipboard.value;
  if (!source) return;
  const sourcePos = layout.value[source.node.id] ?? source.node.position;
  const newId = nextNodeId();
  const newPos = { x: sourcePos.x + 20, y: sourcePos.y + 20 };
  const sourceMemberName = String(source.node.data.memberName ?? source.node.data.memberId ?? '');
  const newNode: FlowNode = {
    ...deepClone(source.node),
    id: newId,
    position: newPos,
    data: { ...deepClone(source.node.data), label: `${sourceMemberName} (${newId})` },
  };
  // The search highlight belongs to the copied node's query match, not the paste.
  newNode.class =
    (newNode.class ?? '')
      .split(' ')
      .filter((c) => c && c !== 'at-node-search-match')
      .join(' ') || undefined;
  graphNodes.value.push(newNode);
  layout.value[newId] = newPos;
  executionByNode.value[newId] = source.exec
    ? deepClone(source.exec)
    : defaultExecState();
  selectNode(newId);
  pushHistory();
  toast.success(tm('editor.nodePasted', { id: newId }));
}

// --- Bulk selection ------------------------------------------------------

/** Node ids currently box-selected on the canvas (Ctrl/Cmd + drag). */
const selectedNodeIds = ref<Set<string>>(new Set());
const bulkDeleteEnabled = computed(() => selectedNodeIds.value.size > 1);

function onSelectionChange(ids: string[] | null) {
  selectedNodeIds.value = new Set(ids ?? []);
}

/** Delete every box-selected node plus all edges touching them. */
function bulkDelete() {
  if (selectedNodeIds.value.size === 0) return;
  const count = selectedNodeIds.value.size;
  if (!window.confirm(tm('editor.bulkDeleteConfirm', { count }))) return;
  graphNodes.value = graphNodes.value.filter((n) => !selectedNodeIds.value.has(n.id));
  graphEdges.value = graphEdges.value.filter(
    (e) => !selectedNodeIds.value.has(e.source) && !selectedNodeIds.value.has(e.target),
  );
  for (const id of selectedNodeIds.value) {
    delete executionByNode.value[id];
    delete layout.value[id];
  }
  selectedNodeIds.value.clear();
  if (selectedNodeId.value && !graphNodes.value.some((n) => n.id === selectedNodeId.value)) {
    selectNode(null);
  }
  pushHistory();
  toast.success(tm('editor.bulkDeleted', { count }));
}

// --- Node search ---------------------------------------------------------

const searchQuery = ref('');

// Recompute the match highlight whenever the query changes.
watch(searchQuery, () => applySearchHighlight());

/** Nodes matching the search text against id, member name and task. */
const matchedNodes = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  if (!query) return [];
  return graphNodes.value.filter((node) => {
    const id = node.id.toLowerCase();
    const memberName = String(node.data.memberName ?? '').toLowerCase();
    const task = String(node.data.task ?? '').toLowerCase();
    return id.includes(query) || memberName.includes(query) || task.includes(query);
  });
});

/** Highlight matches and pan the viewport to the first one. */
function applySearchHighlight() {
  for (const node of graphNodes.value) {
    const matched = matchedNodes.value.some((m) => m.id === node.id);
    const classes = (node.class ?? '').split(' ').filter((c) => c !== 'at-node-search-match');
    node.class = matched ? [...classes, 'at-node-search-match'].join(' ') : classes.join(' ') || undefined;
  }
  const first = matchedNodes.value[0];
  if (!first) return;
  const pos = layout.value[first.id] ?? first.position;
  canvasRef.value?.panTo?.(pos.x, pos.y);
}

// --- Canvas toolbar ------------------------------------------------------

const canvasRef = ref<any>(null);
const snapToGrid = ref(false);
const minimapVisible = ref(true);

/** Lay nodes out in topological columns (a lightweight auto-layout). */
function autoLayout() {
  if (graphNodes.value.length === 0) return;
  const indegree = new Map<string, number>();
  const adj = new Map<string, string[]>();
  for (const node of graphNodes.value) {
    indegree.set(node.id, 0);
    adj.set(node.id, []);
  }
  for (const edge of graphEdges.value) {
    adj.get(edge.source)?.push(edge.target);
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
  }
  const queue = graphNodes.value
    .filter((n) => (indegree.get(n.id) ?? 0) === 0)
    .map((n) => n.id);
  const order: string[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    order.push(id);
    for (const next of adj.get(id) ?? []) {
      indegree.set(next, (indegree.get(next) ?? 0) - 1);
      if ((indegree.get(next) ?? 0) === 0) queue.push(next);
    }
  }
  // Anything left over belongs to a cycle; append it after the acyclic part.
  for (const node of graphNodes.value) {
    if (!order.includes(node.id)) order.push(node.id);
  }
  const X_GAP = 180;
  const Y_GAP = 130;
  order.forEach((id, index) => {
    const col = Math.floor(index / 4);
    const row = index % 4;
    const pos = { x: 60 + col * X_GAP, y: 60 + row * Y_GAP };
    layout.value[id] = pos;
    const node = graphNodes.value.find((n) => n.id === id);
    if (node) node.position = { ...pos };
  });
  pushHistory();
}

/** Global shortcut handlers. */
function onKeydown(e: KeyboardEvent) {
  const target = e.target as HTMLElement | null;
  if (
    target &&
    (target.tagName === 'INPUT' ||
      target.tagName === 'TEXTAREA' ||
      target.isContentEditable ||
      target.closest?.('.v-field'))
  ) {
    return;
  }
  const mod = e.ctrlKey || e.metaKey;
  if (!mod) {
    // Delete/Backspace remove the current selection (edge first, then node).
    if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault();
      deleteSelection();
    }
    return;
  }
  const key = e.key.toLowerCase();
  if (key === 'z' && !e.shiftKey) {
    e.preventDefault();
    undo();
  } else if (key === 'y' || (key === 'z' && e.shiftKey)) {
    e.preventDefault();
    redo();
  } else if (key === 'c') {
    e.preventDefault();
    copyNode();
  } else if (key === 'v') {
    e.preventDefault();
    pasteNode();
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown));
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown);
  if (pushTimer !== null) clearTimeout(pushTimer);
});

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
    skillsMode: skills === null ? 'inherit' : skills.length === 0 ? 'disable_all' : 'allowlist',
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
  if (state.skillsMode === 'disable_all') block.skills = [];
  else if (state.skillsMode === 'allowlist') block.skills = [...(state.skills ?? [])];
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

/** One draggable row in the member strip. */
interface MemberRow {
  memberId: string;
  name: string;
  color: string;
  isCoordinator: boolean;
  /** Persona / provider summary line (may be empty). */
  summary: string;
  /** How many graph nodes reference this member. */
  usedCount: number;
}

/** Member strip rows: identity, accent color and per-member usage count. */
const memberRows = computed<MemberRow[]>(() =>
  (props.team?.members ?? []).map((m: any) => {
    const name = String(m.name ?? m.member_id ?? '');
    return {
      memberId: String(m.member_id),
      name,
      color: collabMemberColor(name),
      isCoordinator: m.member_id === props.team?.coordinator_member_id,
      summary: [m.persona_id, m.provider_id]
        .map((value: unknown) => String(value ?? '').trim())
        .filter(Boolean)
        .join(' · '),
      usedCount: graphNodes.value.filter((n) => n.data.memberId === m.member_id).length,
    };
  }),
);

/** Dragstart payload consumed by the canvas drop handler (TeamsFlowCanvas). */
function onMemberDragStart(event: DragEvent, memberId: string) {
  event.dataTransfer?.setData('application/x-member-id', memberId);
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'copy';
}

const workflowItems = computed(() => [
  { title: tm('editor.newWorkflow'), value: '' },
  ...props.workflows.map((w) => ({ title: w.name || w.workflow_id, value: w.workflow_id })),
]);

const selectedNode = computed(
  () => graphNodes.value.find((n) => n.id === selectedNodeId.value) ?? null,
);

/** Display name of the selected node's bound member ('' when unbound/missing). */
const selectedNodeMemberName = computed<string>(() => {
  const memberId = selectedNode.value?.data.memberId;
  if (!memberId) return '';
  const member = (props.team?.members ?? []).find((m: any) => m.member_id === memberId);
  return member?.name ?? '';
});

/**
 * Direct predecessors of the selected node.
 *
 * Mirrors the runtime injection rule (spec §3.2): an edge is a data-flow edge,
 * so the inspector lists exactly the nodes whose results reach this one.
 */
const upstreamNodeIds = computed<string[]>(() => {
  const nodeId = selectedNodeId.value;
  if (!nodeId) return [];
  return graphEdges.value.filter((e) => e.target === nodeId).map((e) => e.source);
});

/** Direct successors of the selected node (who consumes this node's result). */
const downstreamNodeIds = computed<string[]>(() => {
  const nodeId = selectedNodeId.value;
  if (!nodeId) return [];
  return graphEdges.value.filter((e) => e.source === nodeId).map((e) => e.target);
});

/** Member ids currently on the team roster. */
const memberIds = computed(
  () => new Set((props.team?.members ?? []).map((m: any) => m.member_id)),
);

/**
 * Node list handed to the canvas, enriched for the MemberFlowNode card:
 * member accent color, header title/number, task preview, per-node execution
 * chip labels, inbound edge count and the missing-member flag. The profile
 * name comes from the lazily loaded option cache; until that list is fetched
 * the raw config id is shown (v1 fallback).
 */
const displayNodes = computed<FlowNode[]>(() =>
  graphNodes.value.map((node, index) => {
    const missing = !!node.data.memberId && !memberIds.value.has(node.data.memberId);
    const exec = executionByNode.value[node.id];
    const configId = (exec?.config_id ?? '').trim();
    const personaId = (exec?.persona_id ?? '').trim();
    const configTitle = configProfileOptions.value.find((p) => p.value === configId)?.title;
    const rawTask = String(node.data.task ?? '');
    return {
      ...node,
      class: missing ? 'at-node-missing' : node.class,
      data: {
        ...node.data,
        memberColor: collabMemberColor(
          String(node.data.memberName ?? node.data.memberId ?? node.id),
        ),
        nodeTitle: node.id,
        nodeNumber: index + 1,
        taskPreview: rawTask.length > 60 ? `${rawTask.slice(0, 60)}…` : rawTask,
        missingMember: missing,
        inDegree: graphEdges.value.filter((e) => e.target === node.id).length,
        interactive: true,
        configLabel: configId ? (configTitle ?? configId) : undefined,
        personaLabel: personaId || undefined,
      },
    };
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

/**
 * Live hint for the task template: flags whether the run-input token and/or a
 * predecessor reference are in use, so users see at a glance what the editor
 * will inject at runtime.
 */
const taskHintText = computed(() => {
  const value = selectedTask.value ?? '';
  const hasInput = value.includes(INPUT_TOKEN);
  const hasRef = /\{\{n\d+\}\}/.test(value);
  if (hasInput && hasRef) return tm('editor.taskHintWithBoth');
  if (hasInput) return tm('editor.taskHintWithInput');
  if (hasRef) return tm('editor.taskHintWithRef');
  return tm('editor.taskHintDefault');
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

/** Select (or deselect) a node and sync the inspector drawer visibility. */
function selectNode(nodeId: string | null) {
  selectedNodeId.value = nodeId;
  inspectorOpen.value = nodeId !== null;
}

/** Next auto node id: `n{current max + 1}` (skips gaps after deletions). */
function nextNodeId(): string {
  const maxId = graphNodes.value.reduce((max, node) => {
    const match = /^n(\d+)$/.exec(node.id);
    return match ? Math.max(max, Number(match[1])) : max;
  }, 0);
  return `n${maxId + 1}`;
}

/** Append a node for the given member with auto id `n{max+1}`. */
function addNode(memberId: string, position?: { x: number; y: number }) {
  if (!memberId) return;
  const id = nextNodeId();
  const index = graphNodes.value.length;
  // Explicit position for canvas drops; staggered fallback for row clicks.
  const finalPosition = position ?? {
    x: 60 + (index % 4) * 200,
    y: 60 + Math.floor(index / 4) * 130,
  };
  const memberName = memberNameOf(memberId);
  graphNodes.value.push({
    id,
    position: finalPosition,
    data: { label: `${memberName} (${id})`, memberName, memberId, task: '' },
  });
  layout.value[id] = finalPosition;
  executionByNode.value[id] = defaultExecState();
  selectNode(id);
  pushHistory();
}

/** Remove the selected node plus every edge touching it. */
function deleteNode() {
  const id = selectedNodeId.value;
  if (!id) return;
  graphNodes.value = graphNodes.value.filter((n) => n.id !== id);
  graphEdges.value = graphEdges.value.filter((e) => e.source !== id && e.target !== id);
  delete layout.value[id];
  delete executionByNode.value[id];
  selectNode(null);
  selectedEdgeId.value = null;
  pushHistory();
}

/** Remove a single edge by id (delete-edge event from the canvas). */
function onDeleteEdge(edgeId: string) {
  const index = graphEdges.value.findIndex((e) => e.id === edgeId);
  if (index === -1) return;
  graphEdges.value.splice(index, 1);
  if (selectedEdgeId.value === edgeId) selectedEdgeId.value = null;
  pushHistory();
}

/** Edge selection changed on the canvas (id when selected, null when cleared). */
function onSelectEdge(edgeId: string | null) {
  selectedEdgeId.value = edgeId;
}

/** Delete the current selection: a selected edge, or the selected node. */
function deleteSelection() {
  const edgeId = selectedEdgeId.value;
  if (edgeId) {
    onDeleteEdge(edgeId);
    return;
  }
  if (selectedNodeId.value) deleteNode();
}

/** Whether the delete action has a target (selected node or edge). */
const canDeleteSelection = computed(
  () => selectedNodeId.value !== null || selectedEdgeId.value !== null,
);

/** Place a node dragged from the member strip onto the canvas. */
function onDropAt(memberId: string, position: { x: number; y: number }) {
  addNode(memberId, position);
}

/** Append a normalized edge from the canvas, deduplicating repeats. */
function onConnect(params: { from: string; to: string }) {
  const id = `e:${params.from}->${params.to}`;
  if (graphEdges.value.some((e) => e.id === id)) return;
  graphEdges.value = [...graphEdges.value, { id, source: params.from, target: params.to }];
  pushHistory();
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

// Ensure the selected node always has an execution state entry so inspector
// bindings resolve even for nodes created outside addNode (e.g. loaded graphs
// missing the block).
watch(selectedNodeId, (id) => {
  if (id && !executionByNode.value[id]) executionByNode.value[id] = defaultExecState();
});

/**
 * Insert a variable token at the textarea cursor (or append at the end when
 * the textarea element is not reachable, e.g. in tests without focus).
 */
function insertToken(token: string) {
  const root = taskAreaRef.value?.$el as HTMLElement | undefined;
  const el = ((root?.querySelector?.('textarea') as HTMLTextAreaElement | null) ??
    (root as HTMLTextAreaElement | null)) as HTMLTextAreaElement | null;
  const current = selectedTask.value ?? '';
  if (el && typeof el.selectionStart === 'number') {
    const start = el.selectionStart;
    const end = el.selectionEnd;
    selectedTask.value = current.slice(0, start) + token + current.slice(end);
    const cursor = start + token.length;
    void nextTick(() => {
      el.focus?.();
      el.setSelectionRange?.(cursor, cursor);
    });
  } else {
    selectedTask.value = current + token;
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

// --- Dirty tracking -----------------------------------------------------
// Lightweight snapshot of the last saved/loaded content (name + graph +
// layout); `null` until the first reset/load marks a baseline.

const savedSnapshot = ref<string | null>(null);

function snapshotNow(): string {
  return JSON.stringify({
    name: workflowName.value.trim(),
    graph: buildGraphPayload(),
    layout: { ...layout.value },
  });
}

/** True when the current content differs from the last saved/loaded snapshot. */
const isDirty = computed(() => savedSnapshot.value !== null && snapshotNow() !== savedSnapshot.value);

function markSaved() {
  savedSnapshot.value = snapshotNow();
}

// Baseline snapshot for the blank editor; the load/save watchers re-baseline.
markSaved();
resetHistory();

// --- Local problems (validation badge summary) --------------------------

/** One entry of the toolbar validation summary. */
interface EditorProblem {
  key: string;
  message: string;
  /** Graph node the problem belongs to; entries with a node are clickable. */
  nodeId?: string;
}

/**
 * Local problems for the toolbar badge: the save-blocking banner (missing
 * member / overflow / structural / cycle) plus one entry per unconnected
 * `{{node}}` reference. Only the banner is save-blocking; reference warnings
 * are informational (the backend auto-injects unconnected predecessors).
 */
const localProblems = computed<EditorProblem[]>(() => {
  const problems: EditorProblem[] = [];
  if (bannerMessage.value) problems.push({ key: 'banner', message: bannerMessage.value });
  const graph = buildGraphPayload();
  for (const node of graph.nodes) {
    for (const ref of unconnectedReferences(
      node.id,
      String(node.task ?? ''),
      graph.nodes,
      graph.edges,
    )) {
      problems.push({
        key: `unconnected:${node.id}:${ref}`,
        message: tm('editor.unconnectedRef', { id: ref }),
        nodeId: node.id,
      });
    }
  }
  return problems;
});

const problemsOpen = ref(false);

function onProblemClick(problem: EditorProblem) {
  problemsOpen.value = false;
  if (problem.nodeId) selectNode(problem.nodeId);
}

/** Unconnected reference warnings for the node open in the inspector. */
const selectedUnconnected = computed<string[]>(() => {
  const node = selectedNode.value;
  if (!node) return [];
  const graph = buildGraphPayload();
  return unconnectedReferences(
    node.id,
    String(node.data.task ?? ''),
    graph.nodes,
    graph.edges,
  );
});

/** Variable chips: {{input}} plus one labeled chip per direct predecessor. */
const predecessorChips = computed<{ id: string; label: string }[]>(() => {
  const id = selectedNodeId.value;
  if (!id) return [];
  const chips: { id: string; label: string }[] = [];
  const seen = new Set<string>();
  for (const edge of graphEdges.value) {
    if (edge.target !== id || seen.has(edge.source)) continue;
    seen.add(edge.source);
    const source = graphNodes.value.find((n) => n.id === edge.source);
    const title = (source?.data.memberName as string) ?? edge.source;
    chips.push({ id: edge.source, label: `{{${edge.source}}} · ${title}` });
  }
  return chips;
});

// --- Server field-error mapping -----------------------------------------

/** Node id encoded in a `nodes.<id>...` field path, if any. */
function fieldNodeId(path: string): string | null {
  return /^nodes\.([^.]+)/.exec(path)?.[1] ?? null;
}

/**
 * Server field errors ready for the banner: deduplicated by message text,
 * against the local problems (banner + reference warnings) and among
 * themselves, so a reason already shown locally is not repeated.
 */
const displayErrorFields = computed<ApiErrorField[]>(() => {
  const local = new Set(localProblems.value.map((p) => p.message));
  const seen = new Set<string>();
  const out: ApiErrorField[] = [];
  for (const field of lastErrorFields.value) {
    if (local.has(field.message) || seen.has(field.message)) continue;
    seen.add(field.message);
    out.push(field);
  }
  return out;
});

/** Click a server field entry whose path points at a graph node. */
function onFieldClick(path: string) {
  const id = fieldNodeId(path);
  if (id && graphNodes.value.some((n) => n.id === id)) selectNode(id);
}

async function save(): Promise<string | null> {
  if (!props.team || bannerMessage.value) return null;
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
    if (result === null) return null; // error envelope already toasted by the composable
    toast.success(tm('editor.saveSuccess'));
    // Track the saved row so further saves update instead of create.
    if (result?.workflow_id) selectedWorkflowId.value = result.workflow_id;
    markSaved();
    return selectedWorkflowId.value || null;
  } finally {
    saving.value = false;
  }
}

/**
 * Save, then hand the workflow to the monitor tab.
 *
 * Only a successful save hands off: a validation failure or an error envelope
 * leaves the user in the editor with the banner/toast that explains why.
 */
async function saveAndRun() {
  const workflowId = await save();
  if (workflowId) emit('saveAndRun', workflowId);
}

/** Blank the editor (used for the "new workflow" option). */
function resetBlank() {
  workflowName.value = '';
  graphNodes.value = [];
  graphEdges.value = [];
  layout.value = {};
  executionByNode.value = {};
  selectNode(null);
  clipboard.value = null;
  selectedNodeIds.value.clear();
  // Stale save-error fields belong to the discarded graph.
  lastErrorFields.value = [];
  markSaved();
  resetHistory();
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
watch(selectedWorkflowId, (wfId) => {
  selectNode(null);
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
  // The freshly loaded row is the clean baseline for the unsaved dot.
  markSaved();
  resetHistory();
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

.editor-name-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
}

/* Unsaved-changes marker; text-labeled via title/aria-label (not color-only). */
.editor-unsaved-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f59e0b;
  flex: none;
}

.editor-validation {
  position: relative;
}

.editor-problem-count {
  padding: 0 6px;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.18);
  font-size: 11px;
}

.editor-problems-list {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 20;
  min-width: 260px;
  max-width: 460px;
  margin: 0;
  padding: 6px;
  list-style: none;
  border: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 8px;
  background: var(--dashboard-surface, rgba(128, 128, 128, 0.04));
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.14);
}

.editor-problem-item {
  display: block;
  width: 100%;
  padding: 4px 6px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12px;
  text-align: left;
  word-break: break-word;
  cursor: default;
}

.editor-problem-item:not(:disabled) {
  cursor: pointer;
}

.editor-problem-item:not(:disabled):hover {
  background: rgba(128, 128, 128, 0.12);
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

/* Server field-error entries: node-path ones are buttons that select the node. */
.editor-banner-field {
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  word-break: break-word;
}

button.editor-banner-field {
  cursor: pointer;
  text-decoration: underline dotted;
}

/* Variable chips above the task textarea. */
.editor-var-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
}

.editor-var-chips-label {
  font-size: 11px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
}

.editor-var-chip {
  max-width: 100%;
  padding: 1px 6px;
  border: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 6px;
  background: rgba(128, 128, 128, 0.1);
  color: inherit;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.editor-var-chip:hover {
  background: rgba(128, 128, 128, 0.2);
}

/* Local-only reference warnings under the task editor (never save-blocking). */
.editor-unconnected {
  margin: 8px 0 0;
  padding-left: 16px;
  font-size: 12px;
  color: #f59e0b;
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
  /* Anchor for the inspector drawer rendered inside this nested layout. */
  position: relative;
  min-height: 520px;
}

.editor-members {
  width: 200px;
  flex: none;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 12px;
  overflow-y: auto;
  max-height: 520px;
}

.editor-members-title {
  font-size: 13px;
  font-weight: 600;
}

.editor-members-hint {
  margin: 0;
  font-size: 11px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
}

.editor-member-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 6px;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: grab;
}

.editor-member-row:hover {
  background: rgba(128, 128, 128, 0.12);
  border-color: var(--dashboard-border, rgba(128, 128, 128, 0.25));
}

.editor-member-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: none;
}

.editor-member-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 600;
}

.editor-member-coord {
  flex: none;
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(128, 128, 128, 0.15);
  font-size: 10px;
}

.editor-member-used {
  font-size: 10px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
}

.editor-member-summary {
  width: 100%;
  font-size: 11px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.editor-canvas {
  flex: 1;
  min-width: 0;
  height: 520px;
}

/* Docked inspector column (wide viewports): keep the canvas clear of the
   drawer instead of letting it overlay the graph. */
.editor-body.has-inspector .editor-canvas {
  margin-right: 340px;
}

.editor-inspector {
  box-sizing: border-box;
  height: 100%;
  padding: 12px;
  overflow-y: auto;
}

.editor-task-hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
  word-break: break-word;
}

/* Inspector card grouping: a titled block with labelled key/value rows and a
   divider between rows, replacing the flat text list (spec §3.1). */
.inspector-card {
  margin-bottom: 12px;
  border: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 10px;
  background: rgba(128, 128, 128, 0.05);
  overflow: hidden;
}

.inspector-card-title {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: rgba(128, 128, 128, 0.14);
  font-size: 13px;
  font-weight: 600;
}

.inspector-card-body {
  padding: 6px 10px 10px;
}

.info-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
}

.info-row + .info-row {
  border-top: 1px solid rgba(128, 128, 128, 0.12);
}

.info-label {
  min-width: 72px;
  flex: none;
  font-size: 12px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
}

/* Side-by-side upstream / downstream columns (spec §3.1.2). */
.relation-cols {
  display: flex;
  gap: 12px;
}

.relation-cols .editor-relation-block {
  flex: 1;
  min-width: 0;
}

.relation-header {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
  font-weight: 500;
  color: inherit;
}

/* Floating canvas toolbar + search (spec §3.5). */
.editor-canvas {
  position: relative;
}

.editor-canvas-toolbar {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  white-space: nowrap;
  justify-content: center;
  gap: 4px;
  z-index: 10;
  max-width: calc(100% - 24px);
  padding: 4px 8px;
  border: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.editor-tb-group {
  display: flex;
  align-items: center;
  gap: 2px;
}

.editor-tb-group + .editor-tb-group,
.editor-search-bar {
  border-left: 1px solid rgba(128, 128, 128, 0.2);
  padding-left: 6px;
}

.editor-search-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  /* The search box is the only flexible piece: it shrinks first so the
     toolbar keeps a single row on narrow viewports. */
  flex: 0 1 auto;
  min-width: 40px;
}

.editor-search-input {
  width: 140px;
  min-width: 40px;
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 8px;
  font: inherit;
  font-size: 12px;
  background: transparent;
  color: inherit;
}

.editor-search-input:focus {
  outline: 2px solid #1976d2;
  outline-offset: 1px;
}

@media (max-width: 1100px) {
  .editor-body {
    flex-direction: column;
  }

  .editor-members {
    width: 100%;
    max-height: none;
    flex-direction: row;
    flex-wrap: wrap;
    align-items: flex-start;
  }

  .editor-members-hint {
    width: 100%;
  }

  .editor-member-row {
    width: auto;
  }
}
</style>

<style>
/* Node search highlight lands on the VueFlow node wrapper, which is outside
   this component's scoped scope, so it must be declared globally. */
.at-node-search-match {
  outline: 2px solid #1976d2;
  outline-offset: 2px;
  border-radius: 10px;
}
</style>
