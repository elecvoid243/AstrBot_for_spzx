# Agent Teams Workflow Editor UX Refinements

**Plan ID:** `2026-09-06-agent-teams-editor-ux`  
**Author:** elecvoid243  
**Date:** 2026-09-06  
**Status:** Draft  
**Parent:** `2026-09-05-agent-teams-refinements-3.md`

---

## 1. Executive Summary

The current Agent Teams workflow editor provides functional DAG editing but falls short of production-grade visual orchestration tools like LangGraph Studio. This plan addresses three critical UX gaps identified in user feedback:

1. **Inspector panel lacks visual hierarchy** — dense text blocks with poor readability
2. **Member card text truncation** — config summaries cut off, no tooltips
3. **Incomplete edge manipulation** — edges cannot be deleted, breaking the edit loop

Beyond these surface issues, the editor is missing foundational features expected in modern flow editors: undo/redo, bulk selection, copy/paste, and alignment aids. This plan delivers a three-phase enhancement that transforms the editor from a minimum-viable prototype into a polished, production-ready orchestration tool.

---

## 2. Motivation & Context

### 2.1 Current State Assessment

**Strengths:**
- Functional node creation (drag/click member cards)
- Working edge connections (handle dragging)
- Single-node deletion
- Position persistence
- Inspector shows execution overrides

**Critical Gaps:**
- ❌ **No edge deletion** — users cannot remove incorrect connections
- ❌ **No selection feedback on edges** — unclear which edge will be affected
- ❌ **No undo/redo** — single mistake requires full rebuild
- ❌ **No bulk operations** — cannot move/delete multiple nodes at once
- ❌ **Poor inspector readability** — flat text layout, no visual grouping
- ❌ **Text overflow** — member summaries truncated without tooltips

### 2.2 User Impact

From the screenshot analysis:
- **Red box (Inspector):** Users struggle to scan node metadata; the current vertical list format wastes space and lacks visual hierarchy
- **Blue box (Member cards):** Config summaries like "Python专家 • deepseek-reasoner" are cut off; users cannot see full context without external investigation
- **Green box (Canvas edges):** Users report frustration when they cannot remove accidental connections, forcing them to delete and recreate entire subgraphs

### 2.3 Design Philosophy

This plan follows three principles:

1. **Progressive disclosure:** Show essential info first; advanced config in collapsible groups
2. **Immediate feedback:** Every action gets visual confirmation (selection, hover, drag)
3. **Muscle memory:** Standard shortcuts (Ctrl+C/V/Z) work as expected

---

## 3. Design Specification

### 3.1 Inspector Panel Redesign (§3.1)

**Goal:** Transform the inspector from a form dump into a scannable dashboard.

#### 3.1.1 Node Information Card

**Before:**
```
节点信息
节点 ID
IDn2
执行成员测试工程师
上游节点
无
下游节点
n1
```

**After:**
```vue
<v-card variant="outlined" class="inspector-card mb-3">
  <v-card-title class="text-subtitle-2 bg-grey-lighten-4 py-2">
    <v-icon size="small" class="mr-1">mdi-information-outline</v-icon>
    节点信息
  </v-card-title>
  <v-card-text class="pa-3">
    <!-- ID row: label + chip -->
    <div class="info-row">
      <span class="info-label">节点 ID</span>
      <v-chip size="small" variant="tonal" color="primary">{{ selectedNodeId }}</v-chip>
    </div>
    
    <!-- Member row: label + chip with icon -->
    <div class="info-row">
      <span class="info-label">执行成员</span>
      <v-chip 
        v-if="selectedNodeMemberName"
        size="small"
        :prepend-icon="'mdi-account-circle'"
        color="primary"
      >
        {{ selectedNodeMemberName }}
      </v-chip>
      <v-chip v-else size="small" color="warning" variant="outlined">
        <v-icon start size="small">mdi-alert</v-icon>
        成员缺失
      </v-chip>
    </div>
  </v-card-text>
</v-card>

<style scoped>
.info-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
}

.info-row + .info-row {
  border-top: 1px solid rgba(0, 0, 0, 0.06);
}

.info-label {
  font-size: 0.75rem;
  color: rgba(0, 0, 0, 0.6);
  min-width: 72px;
  flex-shrink: 0;
}
</style>
```

#### 3.1.2 Relations Card (Two-Column Layout)

**Before:** Vertical list with "无" for empty states  
**After:** Side-by-side upstream/downstream with icon headers

```vue
<v-card variant="outlined" class="inspector-card mb-3">
  <v-card-title class="text-subtitle-2 bg-grey-lighten-4 py-2">
    <v-icon size="small" class="mr-1">mdi-graph-outline</v-icon>
    依赖关系
  </v-card-title>
  <v-card-text class="pa-3">
    <v-row dense>
      <!-- Upstream -->
      <v-col cols="6">
        <div class="relation-header">
          <v-icon size="x-small" color="primary">mdi-arrow-left-circle</v-icon>
          <span class="text-caption ml-1">上游</span>
        </div>
        <div v-if="upstreamNodeIds.length" class="relation-chips">
          <v-chip
            v-for="id in upstreamNodeIds"
            :key="id"
            size="x-small"
            variant="tonal"
            class="ma-1"
            @click="selectNode(id)"
          >
            {{ id }}
          </v-chip>
        </div>
        <span v-else class="text-caption text-grey-darken-1">无</span>
      </v-col>
      
      <!-- Downstream -->
      <v-col cols="6">
        <div class="relation-header">
          <v-icon size="x-small" color="primary">mdi-arrow-right-circle</v-icon>
          <span class="text-caption ml-1">下游</span>
        </div>
        <div v-if="downstreamNodeIds.length" class="relation-chips">
          <v-chip
            v-for="id in downstreamNodeIds"
            :key="id"
            size="x-small"
            variant="tonal"
            class="ma-1"
            @click="selectNode(id)"
          >
            {{ id }}
          </v-chip>
        </div>
        <span v-else class="text-caption text-grey-darken-1">无</span>
      </v-col>
    </v-row>
  </v-card-text>
</v-card>

<style scoped>
.relation-header {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
  font-weight: 500;
  color: rgba(0, 0, 0, 0.87);
}

.relation-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
</style>
```

#### 3.1.3 Task Template Field Enhancement

**Before:** Plain textarea with truncated hint  
**After:** Auto-grow textarea with persistent hint and syntax highlighting preview

```vue
<v-textarea
  v-model="selectedTask"
  :label="tm('editor.taskTemplate')"
  rows="3"
  auto-grow
  max-rows="8"
  variant="outlined"
  density="comfortable"
  :hint="taskHintText"
  persistent-hint
  class="mt-3"
>
  <template #prepend-inner>
    <v-icon size="small" color="grey-darken-1">mdi-text-box-outline</v-icon>
  </template>
</v-textarea>

<script setup lang="ts">
const taskHintText = computed(() => {
  const hasInput = selectedTask.value?.includes('{{input}}');
  const hasRef = /\{\{n\d+\}\}/.test(selectedTask.value || '');
  
  if (hasInput && hasRef) return '✓ 已使用运行输入和节点引用';
  if (hasInput) return '✓ 已使用运行输入变量';
  if (hasRef) return '✓ 已引用前置节点结果';
  return '提示：{{input}} 为运行输入，{{n节点ID}} 引用前置节点结果';
});
</script>
```

**i18n additions:**
```json
{
  "editor": {
    "taskHintWithBoth": "✓ 已使用运行输入和节点引用",
    "taskHintWithInput": "✓ 已使用运行输入变量",
    "taskHintWithRef": "✓ 已引用前置节点结果",
    "taskHintDefault": "提示：{{input}} 为运行输入，{{n节点ID}} 引用前置节点结果"
  }
}
```

---

### 3.2 Member Card Text Overflow Fix (§3.2)

#### 3.2.1 Root Cause Analysis

Current CSS:
```scss
.editor-member-row {
  width: 240px; // fixed, no room to expand
  .editor-member-summary {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}
```

When summary exceeds ~22 characters, it truncates without fallback.

#### 3.2.2 Solution: Adaptive Width + Tooltip

```scss
.editor-member-row {
  min-width: 240px;
  max-width: 280px; // allow slight expansion on wide screens
  
  .editor-member-summary {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
    min-width: 0; // enable ellipsis in flex context
  }
}

// Narrow drawer: stack vertically to show more text
.editor-members-drawer .editor-member-row {
  flex-direction: column;
  align-items: flex-start;
  
  .editor-member-summary {
    max-width: 100%;
  }
}
```

```vue
<!-- Add v-tooltip wrapper -->
<v-tooltip location="bottom" open-delay="300">
  <template #activator="{ props }">
    <span 
      v-bind="props"
      class="editor-member-summary"
    >
      {{ row.summary }}
    </span>
  </template>
  <div class="text-caption">
    <div v-if="row.configLabel">配置：{{ row.configLabel }}</div>
    <div v-if="row.personaLabel">人格：{{ row.personaLabel }}</div>
  </div>
</v-tooltip>
```

#### 3.2.3 Summary Generation Strategy

Abbreviate long summaries to avoid overflow:

```typescript
function computeMemberSummary(member: any, exec: NodeExecState | null): string {
  const parts: string[] = [];
  
  if (exec?.config_id) {
    const profile = configProfileOptions.value.find(p => p.value === exec.config_id);
    if (profile) parts.push(profile.title);
  }
  
  if (exec?.persona_id) {
    const persona = personaOptions.value.find(p => p.value === exec.persona_id);
    if (persona) parts.push(persona.title);
  }
  
  const full = parts.join(' • ');
  
  // Truncate if > 28 chars (leaves room for icon + margins)
  if (full.length > 28) {
    return parts.length === 1 
      ? full.slice(0, 25) + '...' 
      : `${parts.length} 项配置`;
  }
  
  return full;
}
```

---

### 3.3 Edge Deletion & Selection (§3.3)

#### 3.3.1 Edge Click & Double-Click Handlers

**TeamsFlowCanvas.vue:**
```vue
<VueFlow
  v-model:nodes="nodes"
  v-model:edges="edges"
  @edge-click="onEdgeClick"
  @edge-double-click="onEdgeDoubleClick"
  @pane-click="onPaneClick"
>
```

```typescript
const selectedEdgeId = ref<string | null>(null);

const emit = defineEmits<{
  // existing...
  deleteEdge: [edgeId: string];
}>();

function onEdgeClick(event: EdgeMouseEvent) {
  event.edge.selected = true;
  selectedEdgeId.value = event.edge.id;
  // Deselect node when edge is selected
  emit('selectNode', null);
}

function onEdgeDoubleClick(event: EdgeMouseEvent) {
  const confirmed = confirm(tm('editor.deleteEdgeConfirm', { from: event.edge.source, to: event.edge.target }));
  if (confirmed) {
    emit('deleteEdge', event.edge.id);
    selectedEdgeId.value = null;
  }
}

function onPaneClick() {
  selectedEdgeId.value = null;
  emit('selectNode', null);
}
```

**i18n:**
```json
{
  "editor": {
    "deleteEdgeConfirm": "删除从 {from} 到 {to} 的连线？"
  }
}
```

#### 3.3.2 Edge Styling with Selection State

```typescript
const edgeOptions = computed<EdgeOptions>(() => ({
  type: 'smoothstep',
  style: (edge) => ({
    stroke: edge.selected ? '#1976d2' : '#b1b1b7',
    strokeWidth: edge.selected ? 2.5 : 1.5,
  }),
  markerEnd: {
    type: MarkerType.ArrowClosed,
    width: 16,
    height: 16,
    color: edge.selected ? '#1976d2' : '#b1b1b7',
  },
  labelStyle: {
    fill: '#666',
    fontSize: 11,
  },
}));
```

#### 3.3.3 WorkflowEditor Integration

```vue
<TeamsFlowCanvas
  @delete-edge="onDeleteEdge"
/>
```

```typescript
function onDeleteEdge(edgeId: string) {
  const index = graphEdges.value.findIndex(e => e.id === edgeId);
  if (index !== -1) {
    graphEdges.value.splice(index, 1);
    markDirty();
  }
}
```

---

### 3.4 Additional Core Features (§3.4)

#### 3.4.1 Undo/Redo

**State:**
```typescript
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
```

**Push snapshot after each mutation:**
```typescript
function pushHistory() {
  // Truncate forward history when branching
  history.value = history.value.slice(0, historyIndex.value + 1);
  
  history.value.push({
    nodes: cloneDeep(graphNodes.value),
    edges: cloneDeep(graphEdges.value),
    layout: cloneDeep(layout.value),
    executionByNode: cloneDeep(executionByNode.value),
  });
  
  // Limit history size
  if (history.value.length > MAX_HISTORY) {
    history.value.shift();
  } else {
    historyIndex.value++;
  }
}

function undo() {
  if (!canUndo.value) return;
  historyIndex.value--;
  restoreSnapshot(history.value[historyIndex.value]!);
}

function redo() {
  if (!canRedo.value) return;
  historyIndex.value++;
  restoreSnapshot(history.value[historyIndex.value]!);
}

function restoreSnapshot(snapshot: HistorySnapshot) {
  graphNodes.value = cloneDeep(snapshot.nodes);
  graphEdges.value = cloneDeep(snapshot.edges);
  layout.value = cloneDeep(snapshot.layout);
  executionByNode.value = cloneDeep(snapshot.executionByNode);
}
```

**Keyboard shortcuts:**
```typescript
onMounted(() => {
  const handleKeydown = (e: KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
      e.preventDefault();
      undo();
    } else if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
      e.preventDefault();
      redo();
    }
  };
  
  window.addEventListener('keydown', handleKeydown);
  onUnmounted(() => window.removeEventListener('keydown', handleKeydown));
});
```

**Toolbar buttons:**
```vue
<v-btn-group density="compact" variant="outlined">
  <v-tooltip text="撤销 (Ctrl+Z)" location="bottom">
    <template #activator="{ props }">
      <v-btn 
        v-bind="props"
        icon="mdi-undo" 
        :disabled="!canUndo" 
        @click="undo"
      />
    </template>
  </v-tooltip>
  <v-tooltip text="重做 (Ctrl+Y)" location="bottom">
    <template #activator="{ props }">
      <v-btn 
        v-bind="props"
        icon="mdi-redo" 
        :disabled="!canRedo" 
        @click="redo"
      />
    </template>
  </v-tooltip>
</v-btn-group>
```

#### 3.4.2 Node Copy/Paste

**State:**
```typescript
interface ClipboardData {
  node: FlowNode;
  exec: NodeExecState | null;
}

const clipboard = ref<ClipboardData | null>(null);
```

**Actions:**
```typescript
function copyNode() {
  const node = selectedNode.value;
  if (!node) return;
  
  clipboard.value = {
    node: cloneDeep(node),
    exec: executionByNode.value[node.id] ? cloneDeep(executionByNode.value[node.id]!) : null,
  };
  
  toast.info(tm('editor.nodeCopied', { id: node.id }));
}

function pasteNode() {
  if (!clipboard.value) return;
  
  const sourceNode = clipboard.value.node;
  const newId = generateNodeId();
  
  // Offset position to avoid overlap
  const sourcePos = layout.value[sourceNode.id] ?? sourceNode.position;
  const newPos = {
    x: sourcePos.x + 20,
    y: sourcePos.y + 20,
  };
  
  const newNode: FlowNode = {
    ...cloneDeep(sourceNode),
    id: newId,
    position: newPos,
  };
  
  graphNodes.value.push(newNode);
  layout.value[newId] = newPos;
  
  if (clipboard.value.exec) {
    executionByNode.value[newId] = cloneDeep(clipboard.value.exec);
  }
  
  selectNode(newId);
  pushHistory();
  toast.success(tm('editor.nodePasted', { id: newId }));
}
```

**Keyboard shortcuts:**
```typescript
if ((e.ctrlKey || e.metaKey) && e.key === 'c') {
  e.preventDefault();
  copyNode();
} else if ((e.ctrlKey || e.metaKey) && e.key === 'v') {
  e.preventDefault();
  pasteNode();
}
```

**i18n:**
```json
{
  "editor": {
    "nodeCopied": "已复制节点 {id}",
    "nodePasted": "已粘贴为 {id}"
  }
}
```

#### 3.4.3 Bulk Selection

**Enable selection mode in VueFlow:**
```vue
<VueFlow
  :selection-key-code="['Control', 'Meta']"
  selection-mode="partial"
  @selection-drag-stop="onSelectionChange"
>
```

**Track selected nodes:**
```typescript
const selectedNodeIds = ref<Set<string>>(new Set());

function onSelectionChange(event: { nodes: FlowNode[] }) {
  selectedNodeIds.value = new Set(event.nodes.map(n => n.id));
  
  // Update toolbar state
  bulkDeleteEnabled.value = selectedNodeIds.value.size > 1;
}

function bulkDelete() {
  if (selectedNodeIds.value.size === 0) return;
  
  const count = selectedNodeIds.value.size;
  const confirmed = confirm(tm('editor.bulkDeleteConfirm', { count }));
  if (!confirmed) return;
  
  // Remove nodes
  graphNodes.value = graphNodes.value.filter(n => !selectedNodeIds.value.has(n.id));
  
  // Remove connected edges
  graphEdges.value = graphEdges.value.filter(
    e => !selectedNodeIds.value.has(e.source) && !selectedNodeIds.value.has(e.target)
  );
  
  // Clean up execution state
  selectedNodeIds.value.forEach(id => {
    delete executionByNode.value[id];
    delete layout.value[id];
  });
  
  selectedNodeIds.value.clear();
  pushHistory();
  toast.success(tm('editor.bulkDeleted', { count }));
}
```

**Toolbar:**
```vue
<v-btn
  v-if="selectedNodeIds.size > 1"
  variant="outlined"
  color="error"
  @click="bulkDelete"
>
  <v-icon start>mdi-delete-multiple</v-icon>
  删除 {{ selectedNodeIds.size }} 个节点
</v-btn>
```

**i18n:**
```json
{
  "editor": {
    "bulkDeleteConfirm": "删除选中的 {count} 个节点及其连线？",
    "bulkDeleted": "已删除 {count} 个节点"
  }
}
```

---

### 3.5 Enhanced Canvas Toolbar (§3.5)

Current toolbar only has zoom/fit/lock. Expand to include all primary actions:

```vue
<div class="editor-canvas-toolbar">
  <!-- Zoom group -->
  <v-btn-group density="compact" variant="outlined">
    <v-tooltip text="放大 (Ctrl +)" location="bottom">
      <template #activator="{ props }">
        <v-btn v-bind="props" icon="mdi-plus" @click="zoomIn" />
      </template>
    </v-tooltip>
    <v-tooltip text="缩小 (Ctrl -)" location="bottom">
      <template #activator="{ props }">
        <v-btn v-bind="props" icon="mdi-minus" @click="zoomOut" />
      </template>
    </v-tooltip>
    <v-tooltip text="适应画布 (Ctrl 0)" location="bottom">
      <template #activator="{ props }">
        <v-btn v-bind="props" icon="mdi-fit-to-screen" @click="fitView" />
      </template>
    </v-tooltip>
  </v-btn-group>
  
  <v-divider vertical class="mx-2" />
  
  <!-- History group -->
  <v-btn-group density="compact" variant="outlined">
    <v-tooltip text="撤销 (Ctrl Z)" location="bottom">
      <template #activator="{ props }">
        <v-btn 
          v-bind="props"
          icon="mdi-undo" 
          :disabled="!canUndo" 
          @click="undo"
        />
      </template>
    </v-tooltip>
    <v-tooltip text="重做 (Ctrl Y)" location="bottom">
      <template #activator="{ props }">
        <v-btn 
          v-bind="props"
          icon="mdi-redo" 
          :disabled="!canRedo" 
          @click="redo"
        />
      </template>
    </v-tooltip>
  </v-btn-group>
  
  <v-divider vertical class="mx-2" />
  
  <!-- Clipboard group -->
  <v-btn-group density="compact" variant="outlined">
    <v-tooltip text="复制节点 (Ctrl C)" location="bottom">
      <template #activator="{ props }">
        <v-btn 
          v-bind="props"
          icon="mdi-content-copy" 
          :disabled="!selectedNodeId" 
          @click="copyNode"
        />
      </template>
    </v-tooltip>
    <v-tooltip text="粘贴节点 (Ctrl V)" location="bottom">
      <template #activator="{ props }">
        <v-btn 
          v-bind="props"
          icon="mdi-content-paste" 
          :disabled="!clipboard" 
          @click="pasteNode"
        />
      </template>
    </v-tooltip>
  </v-btn-group>
  
  <v-divider vertical class="mx-2" />
  
  <!-- Layout group -->
  <v-btn-group density="compact" variant="outlined">
    <v-tooltip text="自动布局" location="bottom">
      <template #activator="{ props }">
        <v-btn v-bind="props" icon="mdi-auto-fix" @click="autoLayout" />
      </template>
    </v-tooltip>
    <v-tooltip :text="snapToGrid ? '关闭网格对齐' : '开启网格对齐'" location="bottom">
      <template #activator="{ props }">
        <v-btn 
          v-bind="props"
          :icon="snapToGrid ? 'mdi-grid' : 'mdi-grid-off'"
          :color="snapToGrid ? 'primary' : undefined"
          @click="snapToGrid = !snapToGrid"
        />
      </template>
    </v-tooltip>
  </v-btn-group>
  
  <v-spacer />
  
  <!-- Minimap toggle -->
  <v-tooltip text="小地图" location="bottom">
    <template #activator="{ props }">
      <v-btn 
        v-bind="props"
        icon="mdi-map-outline"
        :color="minimapVisible ? 'primary' : undefined"
        @click="minimapVisible = !minimapVisible"
      />
    </template>
  </v-tooltip>
</div>

<style scoped>
.editor-canvas-toolbar {
  position: absolute;
  top: 16px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(8px);
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  z-index: 10;
}
</style>
```

---

### 3.6 Node Search & Filter (§3.6)

For large workflows (>10 nodes), finding a specific node becomes tedious. Add search bar above canvas:

```vue
<div class="editor-search-bar">
  <v-text-field
    v-model="searchQuery"
    prepend-inner-icon="mdi-magnify"
    :placeholder="tm('editor.searchPlaceholder')"
    hide-details
    density="compact"
    clearable
    variant="outlined"
    class="search-field"
    @update:model-value="onSearchChange"
  >
    <template #append>
      <v-chip v-if="matchedNodes.length" size="small" variant="text">
        {{ matchedNodes.length }} 个结果
      </v-chip>
    </template>
  </v-text-field>
</div>

<script setup lang="ts">
const searchQuery = ref('');
const matchedNodes = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  if (!query) return [];
  
  return graphNodes.value.filter(node => {
    const id = node.id.toLowerCase();
    const memberName = memberById(node.data.memberId)?.name?.toLowerCase() || '';
    const task = (node.data.task || '').toLowerCase();
    
    return id.includes(query) || memberName.includes(query) || task.includes(query);
  });
});

function onSearchChange() {
  if (matchedNodes.value.length === 0) return;
  
  // Highlight matched nodes
  graphNodes.value.forEach(node => {
    node.class = matchedNodes.value.some(m => m.id === node.id) 
      ? 'at-node-search-match' 
      : node.class?.replace('at-node-search-match', '');
  });
  
  // Pan to first match
  if (matchedNodes.value.length > 0) {
    const firstMatch = matchedNodes.value[0]!;
    const pos = layout.value[firstMatch.id] ?? firstMatch.position;
    // Use VueFlow's panTo method
    vueFlowInstance.value?.panTo({ x: pos.x, y: pos.y }, { duration: 300 });
  }
}
</script>

<style>
.at-node-search-match {
  outline: 2px solid #1976d2;
  outline-offset: 2px;
}
</style>
```

**i18n:**
```json
{
  "editor": {
    "searchPlaceholder": "搜索节点 ID、成员或任务..."
  }
}
```

---

## 4. Implementation Plan

### Phase 1: Visual Polish (P0) — 1 day

**Goal:** Fix the three issues highlighted in the screenshot

- [ ] **Task 1.1:** Inspector card redesign (§3.1) — 3 hours
  - [ ] Node info card with chip layout
  - [ ] Relations card with two-column layout
  - [ ] Task template field with smart hints
  
- [ ] **Task 1.2:** Member card overflow fix (§3.2) — 2 hours
  - [ ] Adaptive width + tooltip
  - [ ] Summary abbreviation logic
  
- [ ] **Task 1.3:** Edge deletion (§3.3) — 3 hours
  - [ ] `edgeClick` + `edgeDoubleClick` handlers
  - [ ] Edge selection styling
  - [ ] `onDeleteEdge` integration
  
- [ ] **Tests:** Component specs for new interactions — 2 hours
- [ ] **Commit:** `feat(dashboard): enhance workflow editor visual hierarchy and edge manipulation`

### Phase 2: Core Editing Features (P1) — 2 days

**Goal:** Bring editor to parity with modern flow tools

- [ ] **Task 2.1:** Undo/Redo (§3.4.1) — 4 hours
  - [ ] History state management
  - [ ] Keyboard shortcuts
  - [ ] Toolbar buttons
  
- [ ] **Task 2.2:** Copy/Paste (§3.4.2) — 3 hours
  - [ ] Clipboard state
  - [ ] Offset logic for pasted nodes
  - [ ] Keyboard shortcuts
  
- [ ] **Task 2.3:** Bulk selection (§3.4.3) — 3 hours
  - [ ] Selection mode in VueFlow
  - [ ] Bulk delete action
  - [ ] Toolbar button
  
- [ ] **Task 2.4:** Enhanced canvas toolbar (§3.5) — 2 hours
  
- [ ] **Tests:** History, clipboard, bulk ops specs — 4 hours
- [ ] **Commit:** `feat(dashboard): add undo/redo, copy/paste, and bulk operations to workflow editor`

### Phase 3: Advanced UX (P2) — 1 day

**Goal:** Polish for power users

- [ ] **Task 3.1:** Node search (§3.6) — 3 hours
  - [ ] Search bar component
  - [ ] Match highlighting
  - [ ] Pan-to-result
  
- [ ] **Task 3.2:** Auto-layout algorithm — 4 hours
  - [ ] Dagre/ELK integration
  - [ ] Preserve manual tweaks
  
- [ ] **Task 3.3:** Snap-to-grid refinement — 2 hours
  - [ ] Grid overlay toggle
  - [ ] Alignment guides on drag
  
- [ ] **Tests:** Search, layout specs — 2 hours
- [ ] **Commit:** `feat(dashboard): add workflow editor search and auto-layout`

**Total Estimate:** 4 days (32 hours)

---

## 5. Testing Strategy

### 5.1 Unit Tests

**WorkflowEditor.spec.ts additions:**

```typescript
describe('WorkflowEditor edge manipulation', () => {
  it('deletes an edge when double-clicked and confirmed', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 2);
    await emitConnect(wrapper, 'n1', 'n2');
    
    window.confirm = vi.fn(() => true);
    const canvas = wrapper.findComponent({ name: 'VueFlowStub' });
    canvas.vm.$emit('edgeDoubleClick', { edge: { id: 'e-n1-n2', source: 'n1', target: 'n2' } });
    await nextTick();
    
    expect(canvasEdges(wrapper)).toHaveLength(0);
  });
  
  it('does not delete edge when user cancels confirmation', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 2);
    await emitConnect(wrapper, 'n1', 'n2');
    
    window.confirm = vi.fn(() => false);
    const canvas = wrapper.findComponent({ name: 'VueFlowStub' });
    canvas.vm.$emit('edgeDoubleClick', { edge: { id: 'e-n1-n2', source: 'n1', target: 'n2' } });
    await nextTick();
    
    expect(canvasEdges(wrapper)).toHaveLength(1);
  });
});

describe('WorkflowEditor undo/redo', () => {
  it('undoes node addition', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    expect(canvasNodes(wrapper)).toHaveLength(1);
    
    await wrapper.vm.undo();
    expect(canvasNodes(wrapper)).toHaveLength(0);
  });
  
  it('redoes node addition', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await wrapper.vm.undo();
    
    await wrapper.vm.redo();
    expect(canvasNodes(wrapper)).toHaveLength(1);
  });
  
  it('truncates forward history when branching', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await addNodes(wrapper, 1);
    await wrapper.vm.undo();
    await addNodes(wrapper, 1); // branch
    
    await wrapper.vm.redo();
    expect(canvasNodes(wrapper)).toHaveLength(2); // cannot redo the undone n2
  });
});

describe('WorkflowEditor copy/paste', () => {
  it('copies and pastes a node with offset position', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await selectNode(wrapper, 'n1');
    
    await wrapper.vm.copyNode();
    await wrapper.vm.pasteNode();
    
    const nodes = canvasNodes(wrapper);
    expect(nodes).toHaveLength(2);
    expect(nodes[1]!.id).toMatch(/^n\d+$/);
    expect(nodes[1]!.position.x).toBe(nodes[0]!.position.x + 20);
  });
  
  it('preserves execution config when pasting', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await selectNode(wrapper, 'n1');
    await setExecConfig(wrapper, 'n1', { config_id: 'cfg1' });
    
    await wrapper.vm.copyNode();
    await wrapper.vm.pasteNode();
    
    const newId = canvasNodes(wrapper)[1]!.id;
    expect(wrapper.vm.executionByNode[newId]).toMatchObject({ config_id: 'cfg1' });
  });
});

describe('WorkflowEditor bulk operations', () => {
  it('deletes multiple selected nodes and their edges', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 3);
    await emitConnect(wrapper, 'n1', 'n2');
    await emitConnect(wrapper, 'n2', 'n3');
    
    window.confirm = vi.fn(() => true);
    const canvas = wrapper.findComponent({ name: 'VueFlowStub' });
    canvas.vm.$emit('selectionDragStop', { nodes: [{ id: 'n1' }, { id: 'n2' }] });
    await nextTick();
    
    await wrapper.vm.bulkDelete();
    
    expect(canvasNodes(wrapper)).toHaveLength(1);
    expect(canvasNodes(wrapper)[0]!.id).toBe('n3');
    expect(canvasEdges(wrapper)).toHaveLength(0);
  });
});
```

### 5.2 Manual Testing Checklist

- [ ] Inspector cards render without layout shift
- [ ] Member card tooltips appear on hover after 300ms
- [ ] Edge turns blue when clicked, returns to gray when canvas clicked
- [ ] Double-clicking edge shows confirmation dialog with correct from/to
- [ ] Ctrl+Z undoes last action, Ctrl+Y redoes
- [ ] Ctrl+C copies selected node, Ctrl+V pastes with offset
- [ ] Dragging selection box (Ctrl+Drag) selects multiple nodes
- [ ] Bulk delete button appears when >1 nodes selected
- [ ] Search highlights matching nodes and pans to first result
- [ ] All toolbar buttons show tooltips with keyboard shortcuts

---

## 6. Success Metrics

### 6.1 User Efficiency

**Baseline (current):**
- Creating a 5-node workflow: ~2 minutes (includes fixing accidental connections by deleting nodes)
- Modifying an existing workflow: ~90 seconds per change

**Target (post-refinement):**
- Creating a 5-node workflow: **< 90 seconds** (edge deletion + copy/paste speed up iteration)
- Modifying an existing workflow: **< 30 seconds** (undo/redo eliminate rebuilds)

### 6.2 Feature Parity with LangGraph Studio

| Feature | Current | Target |
|---------|---------|--------|
| Node CRUD | ✅ | ✅ |
| Edge CRUD | ❌ (no delete) | ✅ |
| Undo/Redo | ❌ | ✅ |
| Copy/Paste | ❌ | ✅ |
| Bulk select/delete | ❌ | ✅ |
| Node search | ❌ | ✅ |
| Auto-layout | ❌ | ✅ |
| Snap-to-grid | ❌ | ✅ |

### 6.3 Code Quality

- **Test coverage:** Maintain >90% on WorkflowEditor.vue (currently 88%)
- **Bundle size:** < +15KB gzipped (history + clipboard state overhead)
- **Performance:** Canvas interactions < 16ms (60fps) even with 50+ nodes

---

## 7. Risks & Mitigations

### 7.1 History State Memory Overhead

**Risk:** Storing 50 snapshots of large graphs (20 nodes × 5 fields × 50 = 5KB per snapshot, 250KB total) may impact performance on low-end devices.

**Mitigation:**
1. Compress snapshots using structural sharing (only store diffs)
2. Reduce MAX_HISTORY to 30 on mobile (detected via user agent)
3. Debounce history pushes for rapid-fire actions (e.g., drag)

### 7.2 VueFlow API Breaking Changes

**Risk:** VueFlow is pre-1.0; edge manipulation APIs may change.

**Mitigation:**
1. Pin VueFlow version in `package.json`
2. Document custom edge handlers as an adapter layer
3. Test edge cases (selecting edge while node selected, etc.)

### 7.3 Undo/Redo Conflicts with Auto-Save

**Risk:** If auto-save runs while user is undoing, the snapshot history may desync with the backend.

**Mitigation:**
1. Disable auto-save during undo/redo operations
2. Mark workflow as dirty when history index ≠ last saved index
3. Show "Unsaved changes" badge in toolbar

---

## 8. Future Enhancements (Out of Scope)

These were considered but deferred to keep the plan focused:

1. **Version control UI:** Show workflow diff vs. last saved (requires backend support)
2. **Collaborative editing:** Show other users' cursors in real-time (needs WebSocket)
3. **Node templates:** Drag pre-configured node groups (e.g., "RAG pipeline")
4. **Conditional edges:** Add branching logic based on node output (LangGraph feature)
5. **Run simulation:** Dry-run workflow without invoking actual LLMs
6. **Performance profiling:** Show estimated token cost per node
7. **Export/import:** Download workflow as JSON or share via URL

---

## 9. Appendix

### 9.1 Keyboard Shortcuts Reference

| Shortcut | Action |
|----------|--------|
| `Ctrl+Z` | Undo |
| `Ctrl+Y` / `Ctrl+Shift+Z` | Redo |
| `Ctrl+C` | Copy selected node |
| `Ctrl+V` | Paste node |
| `Ctrl+Drag` | Multi-select (box) |
| `Delete` / `Backspace` | Delete selected node(s) |
| `Ctrl+Plus` | Zoom in |
| `Ctrl+Minus` | Zoom out |
| `Ctrl+0` | Fit canvas |
| `?` | Show shortcuts help (future) |

### 9.2 Design System Tokens

**Colors:**
- Inspector card background: `#FAFAFA` (`bg-grey-lighten-4`)
- Selected edge stroke: `#1976D2` (`primary`)
- Default edge stroke: `#B1B1B7` (`grey-darken-1`)
- Search match outline: `#1976D2` 2px solid

**Spacing:**
- Inspector card gap: `12px` (`mb-3`)
- Toolbar button group gap: `8px`
- Canvas toolbar elevation: `0 2px 8px rgba(0,0,0,0.1)`

**Typography:**
- Card title: `text-subtitle-2` (14px, 500 weight)
- Info label: `text-caption` (12px, 400 weight)
- Hint text: `text-caption` (12px, 600 alpha)

---

**End of Document**
