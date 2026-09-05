// Specs for the agent-teams workflow editor (Task 7): TeamsFlowCanvas +
// WorkflowEditor.
//
// @vue-flow/core is replaced with a passthrough stub (named VueFlowStub) so
// the specs can drive canvas events (connect / nodesChange / nodeClick /
// paneClick) and assert the props flowing back into the canvas. The
// useAgentTeams composable is mocked so saveWorkflow calls can be asserted
// without hitting the API layer; @/utils/toast is mocked as everywhere else.
// Canvas events are followed by nextTick() because the editor updates its
// graph state through Vue's async re-render.
import { flushPromises, mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';
import zh from '@/i18n/locales/zh-CN/features/agent-teams.json';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

const composableMocks = vi.hoisted(() => ({
  saveWorkflow: vi.fn(),
}));

vi.mock('@/utils/toast', () => ({
  useToast: () => toastMock,
}));

vi.mock('@/composables/useAgentTeams', () => ({
  useAgentTeams: () => composableMocks,
}));

vi.mock('@vue-flow/core', async () => {
  const { defineComponent } = await import('vue');
  const VueFlowStub = defineComponent({
    name: 'VueFlowStub',
    props: {
      nodes: { type: Array, default: () => [] },
      edges: { type: Array, default: () => [] },
      nodesDraggable: { type: Boolean, default: false },
      nodesConnectable: { type: Boolean, default: false },
      elementsSelectable: { type: Boolean, default: false },
    },
    emits: ['connect', 'nodesChange', 'nodeClick', 'paneClick'],
    template: `<div class="vue-flow-stub">
      <div
        v-for="n in nodes"
        :key="n.id"
        class="vue-flow-stub-node"
        :class="n.class"
        :data-id="n.id"
      >{{ n.data ? n.data.label : n.label }}</div>
    </div>`,
  });
  return { VueFlow: VueFlowStub };
});

import TeamsFlowCanvas from './TeamsFlowCanvas.vue';
import WorkflowEditor from './WorkflowEditor.vue';

const TEAM = {
  team_id: 't1',
  name: 'Alpha',
  members: [
    { member_id: 'm1', name: 'Alice' },
    { member_id: 'm2', name: 'Bob' },
  ],
};

const WORKFLOW = {
  workflow_id: 'wf1',
  name: 'Pipe',
  graph: {
    nodes: [
      { id: 'n1', member_id: 'm1', task: 'Do A' },
      { id: 'n2', member_id: 'm2', task: 'Do B' },
    ],
    edges: [{ from: 'n1', to: 'n2' }],
  },
  layout: { n1: { x: 10, y: 20 }, n2: { x: 100, y: 40 } },
};

const GHOST_WORKFLOW = {
  workflow_id: 'wf2',
  name: 'Ghost',
  graph: { nodes: [{ id: 'n1', member_id: 'ghost', task: 'X' }], edges: [] },
  layout: { n1: { x: 0, y: 0 } },
};

const stubs = {
  'v-select': {
    props: {
      modelValue: { type: [String, Number], default: '' },
      label: { type: String, default: '' },
      items: { type: Array, default: () => [] },
      itemTitle: { type: String, default: 'title' },
      itemValue: { type: String, default: 'value' },
    },
    emits: ['update:modelValue'],
    methods: {
      optionTitle(this: any, it: any): string {
        return typeof it === 'object' && it !== null ? it[this.itemTitle] : it;
      },
      optionValue(this: any, it: any): string {
        return typeof it === 'object' && it !== null ? it[this.itemValue] : it;
      },
    },
    template: `<select class="select-stub" :data-label="label" :data-value="modelValue ?? ''" @change="$emit('update:modelValue', $event.target.value)">
      <option v-for="(it, i) in items" :key="i" :value="optionValue(it)">{{ optionTitle(it) }}</option>
    </select>`,
  },
  'v-text-field': {
    props: {
      modelValue: { type: [String, Number], default: '' },
      label: { type: String, default: '' },
    },
    emits: ['update:modelValue'],
    template:
      '<input class="tf-stub" :data-label="label" :value="modelValue ?? \'\'" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  'v-textarea': {
    props: {
      modelValue: { type: String, default: '' },
      label: { type: String, default: '' },
    },
    emits: ['update:modelValue'],
    template:
      '<textarea class="ta-stub" :data-label="label" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  'v-btn': {
    props: {
      disabled: { type: Boolean, default: false },
      loading: { type: Boolean, default: false },
    },
    emits: ['click'],
    template:
      '<button type="button" :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
  },
  'v-chip': { template: '<span class="chip-stub"><slot /></span>' },
  'v-spacer': { template: '<div class="v-spacer" />' },
};

function mountEditor(props: Record<string, unknown> = {}) {
  return mount(WorkflowEditor, {
    props: { team: TEAM, workflows: [], ...props },
    global: { stubs },
  }) as VueWrapper<any>;
}

function canvas(wrapper: VueWrapper<any>) {
  return wrapper.findComponent({ name: 'VueFlowStub' });
}

function canvasNodes(wrapper: VueWrapper<any>) {
  return canvas(wrapper).props('nodes') as any[];
}

function canvasEdges(wrapper: VueWrapper<any>) {
  return canvas(wrapper).props('edges') as any[];
}

async function emitConnect(wrapper: VueWrapper<any>, from: string, to: string) {
  canvas(wrapper).vm.$emit('connect', { source: from, target: to });
  await nextTick();
}

async function emitPositionChange(
  wrapper: VueWrapper<any>,
  positions: Record<string, { x: number; y: number }>,
) {
  canvas(wrapper).vm.$emit(
    'nodesChange',
    Object.entries(positions).map(([id, position]) => ({
      id,
      type: 'position',
      position,
      dragging: false,
    })),
  );
  await nextTick();
}

async function selectNode(wrapper: VueWrapper<any>, nodeId: string) {
  canvas(wrapper).vm.$emit('nodeClick', { node: { id: nodeId } });
  await nextTick();
}

function findButton(wrapper: VueWrapper<any>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text));
}

async function addNodes(wrapper: VueWrapper<any>, count: number) {
  const add = findButton(wrapper, zh.editor.addNode)!;
  for (let i = 0; i < count; i += 1) {
    await add.trigger('click');
  }
}

describe('WorkflowEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    composableMocks.saveWorkflow.mockResolvedValue({
      workflow_id: 'wf9',
      name: 'saved',
    });
  });

  it('adds nodes with auto ids, member binding and staggered positions', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await wrapper.find('select.add-node-picker').setValue('m2');
    await addNodes(wrapper, 1);

    const nodes = canvasNodes(wrapper);
    expect(nodes.map((n) => n.id)).toEqual(['n1', 'n2']);
    expect(nodes[0].data.memberId).toBe('m1');
    expect(nodes[0].data.label).toBe('Alice (n1)');
    expect(nodes[1].data.memberId).toBe('m2');
    expect(nodes[1].data.label).toBe('Bob (n2)');
    expect(nodes[1].position).not.toEqual(nodes[0].position);
  });

  it('normalizes connect emits, drops self-loops and duplicates', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 2);

    await emitConnect(wrapper, 'n1', 'n2');
    expect(canvasEdges(wrapper)).toHaveLength(1);
    expect(canvasEdges(wrapper)[0]).toMatchObject({ source: 'n1', target: 'n2' });

    // Self-loop is dropped.
    await emitConnect(wrapper, 'n2', 'n2');
    expect(canvasEdges(wrapper)).toHaveLength(1);

    // Reverse edge is kept; the same edge twice is not duplicated.
    await emitConnect(wrapper, 'n2', 'n1');
    expect(canvasEdges(wrapper)).toHaveLength(2);
    await emitConnect(wrapper, 'n1', 'n2');
    expect(canvasEdges(wrapper)).toHaveLength(2);
  });

  it('deleteNode removes the selected node and its edges', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 2);
    await emitConnect(wrapper, 'n1', 'n2');
    await selectNode(wrapper, 'n1');
    expect(wrapper.find('.editor-inspector').exists()).toBe(true);

    await findButton(wrapper, zh.editor.deleteNode)!.trigger('click');
    await nextTick();

    expect(canvasNodes(wrapper).map((n) => n.id)).toEqual(['n2']);
    expect(canvasEdges(wrapper)).toHaveLength(0);
    expect(wrapper.find('.editor-inspector').exists()).toBe(false);
  });

  it('shows a live cycle banner and blocks save until it is resolved', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 2);
    await emitConnect(wrapper, 'n1', 'n2');
    await emitConnect(wrapper, 'n2', 'n1');

    const banner = wrapper.find('.editor-banner');
    expect(banner.exists()).toBe(true);
    expect(banner.text()).toContain(zh.editor.validation);
    expect(banner.text()).toContain('n1');

    await wrapper
      .find('input[data-label="' + zh.editor.workflowName + '"]')
      .setValue('Loop');
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();

    expect(composableMocks.saveWorkflow).not.toHaveBeenCalled();
    expect(wrapper.find('.editor-banner').exists()).toBe(true);
  });

  it('flags nodes whose member no longer exists and blocks save', async () => {
    const wrapper = mountEditor({ workflows: [GHOST_WORKFLOW] });
    await wrapper.find('select.workflow-picker').setValue('wf2');
    await flushPromises();

    const banner = wrapper.find('.editor-banner');
    expect(banner.exists()).toBe(true);
    expect(banner.text()).toContain(zh.editor.missingMember);
    expect(canvasNodes(wrapper)[0].class).toContain('at-node-missing');

    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();
    expect(composableMocks.saveWorkflow).not.toHaveBeenCalled();
  });

  it('loads a workflow (name, tasks, layout) and round-trips it on save', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW] });
    await wrapper.find('select.workflow-picker').setValue('wf1');
    await flushPromises();

    expect(
      wrapper.find('input[data-label="' + zh.editor.workflowName + '"]').element,
    ).toHaveProperty('value', 'Pipe');

    const nodes = canvasNodes(wrapper);
    expect(nodes[0].position).toEqual({ x: 10, y: 20 });
    expect(nodes[1].position).toEqual({ x: 100, y: 40 });
    expect(canvasEdges(wrapper)).toHaveLength(1);

    // Inspector shows the loaded task of the selected node.
    await selectNode(wrapper, 'n1');
    const taskArea = wrapper.find('.editor-inspector textarea');
    expect(taskArea.element).toHaveProperty('value', 'Do A');

    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();

    expect(composableMocks.saveWorkflow).toHaveBeenCalledTimes(1);
    expect(composableMocks.saveWorkflow).toHaveBeenCalledWith('t1', {
      workflow_id: 'wf1',
      name: 'Pipe',
      graph: {
        nodes: [
          { id: 'n1', member_id: 'm1', task: 'Do A' },
          { id: 'n2', member_id: 'm2', task: 'Do B' },
        ],
        edges: [{ from: 'n1', to: 'n2' }],
      },
      layout: { n1: { x: 10, y: 20 }, n2: { x: 100, y: 40 } },
    });
    expect(toastMock.success).toHaveBeenCalled();
    // The saved workflow becomes the selected one for further updates.
    expect(wrapper.find('select.workflow-picker').attributes('data-value')).toBe('wf9');
  });

  it('blanks the editor when the team changes', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW] });
    await wrapper.find('select.workflow-picker').setValue('wf1');
    await flushPromises();
    expect(canvasNodes(wrapper)).toHaveLength(2);

    await wrapper.setProps({ team: { ...TEAM, team_id: 't2', name: 'Beta' } });
    await flushPromises();

    expect(canvasNodes(wrapper)).toHaveLength(0);
    expect(canvasEdges(wrapper)).toHaveLength(0);
    expect(wrapper.find('select.workflow-picker').attributes('data-value')).toBe('');
  });

  it('keeps dragged positions when the graph re-renders', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await emitPositionChange(wrapper, { n1: { x: 42, y: 24 } });
    await wrapper.find('select.add-node-picker').setValue('m2');
    await addNodes(wrapper, 1);

    const nodes = canvasNodes(wrapper);
    expect(nodes[0].position).toEqual({ x: 42, y: 24 });
  });

  it('saves a payload whose layout map tracks canvas drags', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 2);
    await emitPositionChange(wrapper, { n1: { x: 42, y: 24 } });

    await wrapper
      .find('input[data-label="' + zh.editor.workflowName + '"]')
      .setValue('Pipe');
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();

    expect(composableMocks.saveWorkflow).toHaveBeenCalledTimes(1);
    const payload = composableMocks.saveWorkflow.mock.calls[0][1];
    expect(payload.name).toBe('Pipe');
    expect(payload.graph.nodes).toHaveLength(2);
    expect(payload.graph.edges).toEqual([]);
    expect(payload.layout).toEqual({
      n1: { x: 42, y: 24 },
      n2: expect.any(Object),
    });
    expect(toastMock.success).toHaveBeenCalled();
  });

  it('renders the task hint with literal {{input}} text and inserts at cursor', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await selectNode(wrapper, 'n1');

    const hint = wrapper.find('.editor-task-hint');
    expect(hint.text()).toBe(zh.editor.nodeTaskHint);
    expect(hint.text()).toContain('{{input}}');

    const insertBtn = findButton(wrapper, zh.editor.insertInput)!;
    expect(insertBtn.text()).toBe(zh.editor.insertInput);

    const taskArea = wrapper.find('.editor-inspector textarea');
    await taskArea.setValue('Fix bug');
    (taskArea.element as HTMLTextAreaElement).setSelectionRange(3, 3);
    await insertBtn.trigger('click');

    expect(taskArea.element).toHaveProperty('value', 'Fix{{input}} bug');
  });
});

describe('TeamsFlowCanvas', () => {
  const NODES = [
    { id: 'n1', position: { x: 0, y: 0 }, data: { label: 'Alice (n1)' } },
    { id: 'n2', position: { x: 80, y: 0 }, data: { label: 'Bob (n2)' } },
  ];

  it('edit mode is draggable/connectable/selectable and re-emits normalized connects', async () => {
    const wrapper = mount(TeamsFlowCanvas, {
      props: { nodes: NODES, edges: [], mode: 'edit' },
      global: { stubs },
    }) as VueWrapper<any>;
    const flow = canvas(wrapper);

    expect(flow.props('nodesDraggable')).toBe(true);
    expect(flow.props('nodesConnectable')).toBe(true);
    expect(flow.props('elementsSelectable')).toBe(true);

    flow.vm.$emit('connect', { source: 'n1', target: 'n1' });
    flow.vm.$emit('connect', { source: 'n1', target: 'n2' });
    await flushPromises();

    expect(wrapper.emitted('connect')).toEqual([[{ from: 'n1', to: 'n2' }]]);
  });

  it('emits position maps after drags and selection events', async () => {
    const wrapper = mount(TeamsFlowCanvas, {
      props: { nodes: NODES, edges: [], mode: 'edit' },
      global: { stubs },
    }) as VueWrapper<any>;
    const flow = canvas(wrapper);

    flow.vm.$emit('nodesChange', [
      { id: 'n1', type: 'position', position: { x: 42, y: 24 }, dragging: false },
      { id: 'n2', type: 'dimensions' },
    ]);
    flow.vm.$emit('nodeClick', { node: { id: 'n2' } });
    flow.vm.$emit('paneClick');
    await flushPromises();

    expect(wrapper.emitted('positionChange')).toEqual([[{ n1: { x: 42, y: 24 } }]]);
    expect(wrapper.emitted('selectNode')).toEqual([['n2'], [null]]);
  });

  it('monitor mode locks interaction and binds node status classes', () => {
    const wrapper = mount(TeamsFlowCanvas, {
      props: {
        nodes: NODES,
        edges: [],
        mode: 'monitor',
        nodeStates: { n1: { status: 'running' }, n2: { status: 'failed' } },
      },
      global: { stubs },
    }) as VueWrapper<any>;
    const flow = canvas(wrapper);

    expect(flow.props('nodesDraggable')).toBe(false);
    expect(flow.props('nodesConnectable')).toBe(false);
    const nodes = flow.props('nodes') as any[];
    expect(nodes[0].class).toBe('at-node-running');
    expect(nodes[1].class).toBe('at-node-failed');
  });
});
