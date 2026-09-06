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
import { nextTick, ref } from 'vue';
import type { Ref } from 'vue';
import zh from '@/i18n/locales/zh-CN/features/agent-teams.json';
import { collabMemberColor } from '@/utils/memberColors';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

const composableMocks = vi.hoisted(() => ({
  saveWorkflow: vi.fn(),
  // Assigned a real ref right after imports; the mock factory only captures
  // the holder object, so the property is resolved at useAgentTeams() call time.
  lastErrorFields: null as unknown as Ref<{ path: string; message: string }[]>,
}));

const apiMocks = vi.hoisted(() => ({
  configProfileList: vi.fn(),
  toolList: vi.fn(),
  skillList: vi.fn(),
}));

// Canvas coordinate conversion spy: TeamsFlowCanvas calls useVueFlow()
// from @vue-flow/core (mocked below) to translate screen -> flow coords.
// The default mapping (x2/y2) makes conversion observable in assertions.
const flowMock = vi.hoisted(() => ({
  screenToFlowCoordinate: vi.fn(),
}));

// Holder for the mocked useDisplay().lgAndUp; a real ref is assigned after
// imports so template computeds stay reactive across viewport switches.
const displayMocks = vi.hoisted(() => ({
  lgAndUp: null as unknown as Ref<boolean>,
}));

vi.mock('@/utils/toast', () => ({
  useToast: () => toastMock,
}));

vi.mock('vuetify', () => ({
  useDisplay: () => ({ lgAndUp: displayMocks.lgAndUp }),
}));

vi.mock('@/composables/useAgentTeams', () => ({
  useAgentTeams: () => composableMocks,
}));

vi.mock('@/api/v1', () => ({
  configProfileApi: { list: apiMocks.configProfileList },
  toolApi: { list: apiMocks.toolList },
  skillApi: { list: apiMocks.skillList },
}));

// PersonaSelector pulls in the whole persona folder tree + PersonaForm; the
// execution specs only need the modelValue contract (no defineModel upstream).
vi.mock('@/components/shared/PersonaSelector.vue', async () => {
  const { defineComponent } = await import('vue');
  return {
    default: defineComponent({
      name: 'PersonaSelector',
      props: { modelValue: { type: String, default: '' } },
      emits: ['update:modelValue'],
      template:
        '<input class="persona-stub" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    }),
  };
});

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
      // Custom node registry forwarded by the canvas; asserted via props().
      nodeTypes: { type: Object, default: null },
    },
    emits: ['connect', 'nodesChange', 'nodeClick', 'paneClick'],
    // The default slot hosts the canvas chrome (Background/Controls/MiniMap).
    template: `<div class="vue-flow-stub">
      <slot />
      <div
        v-for="n in nodes"
        :key="n.id"
        class="vue-flow-stub-node"
        :class="n.class"
        :data-id="n.id"
      >{{ n.data ? n.data.label : n.label }}</div>
    </div>`,
  });
  return {
    VueFlow: VueFlowStub,
    // TeamsFlowCanvas calls useVueFlow() to convert drop coordinates.
    useVueFlow: () => ({ screenToFlowCoordinate: flowMock.screenToFlowCoordinate }),
    // MemberFlowNode imports these at module scope; the card only renders
    // them inside a real VueFlow node context.
    Handle: defineComponent({
      name: 'HandleStub',
      props: { type: { type: String, default: 'source' }, position: { type: String, default: '' } },
      template: '<div class="handle-stub" />',
    }),
    Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
    MarkerType: { Arrow: 'arrow', ArrowClosed: 'arrowclosed' },
  };
});

// The canvas chrome packages call useVueFlow() internally (needs a provider),
// so they are replaced with passthrough stubs rendering identifiable DOM.
vi.mock('@vue-flow/background', () => ({
  Background: {
    name: 'BackgroundStub',
    props: { gap: { type: Number, default: 20 } },
    template: '<div class="vf-background-stub" :data-gap="gap" />',
  },
}));

vi.mock('@vue-flow/minimap', () => ({
  MiniMap: {
    name: 'MiniMapStub',
    props: { pannable: { type: Boolean, default: false }, zoomable: { type: Boolean, default: false } },
    template: '<div class="vf-minimap-stub" />',
  },
}));

vi.mock('@vue-flow/controls', () => ({
  Controls: {
    name: 'ControlsStub',
    template: '<div class="vf-controls-stub" />',
  },
}));

import TeamsFlowCanvas from './TeamsFlowCanvas.vue';
import WorkflowEditor from './WorkflowEditor.vue';

// The mock factory captured only the holder; give it a reactive ref now.
composableMocks.lastErrorFields = ref([]);
displayMocks.lgAndUp = ref(true);

const TEAM = {
  team_id: 't1',
  name: 'Alpha',
  coordinator_member_id: 'm1',
  members: [
    { member_id: 'm1', name: 'Alice', persona_id: 'persona_a' },
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

const WORKFLOW_WITH_EXEC = {
  workflow_id: 'wf3',
  name: 'Exec',
  graph: {
    nodes: [
      {
        id: 'n1',
        member_id: 'm1',
        task: 'Do A',
        execution: {
          config_id: 'cfg1',
          persona_id: 'p1',
          tools: ['tool_a'],
          skills: ['skill_x'],
        },
      },
      { id: 'n2', member_id: 'm2', task: 'Do B' },
    ],
    edges: [],
  },
  layout: { n1: { x: 0, y: 0 }, n2: { x: 1, y: 1 } },
};

const WORKFLOW_WITH_SKILLS_DISABLED = {
  workflow_id: 'wf4',
  name: 'No Skills',
  graph: {
    nodes: [
      { id: 'n1', member_id: 'm1', task: 'Do A', execution: { skills: [] } },
      { id: 'n2', member_id: 'm2', task: 'Do B' },
    ],
    edges: [],
  },
  layout: { n1: { x: 0, y: 0 }, n2: { x: 1, y: 1 } },
};

// n1 -> n2, plus an unconnected n3 that n2's template references.
const WORKFLOW_CHAIN = {
  workflow_id: 'wf5',
  name: 'Chain',
  graph: {
    nodes: [
      { id: 'n1', member_id: 'm1', task: 'Do A' },
      { id: 'n2', member_id: 'm2', task: 'see {{n3}}' },
      { id: 'n3', member_id: 'm1', task: 'Do C' },
    ],
    edges: [{ from: 'n1', to: 'n2' }],
  },
  layout: { n1: { x: 0, y: 0 }, n2: { x: 10, y: 0 }, n3: { x: 20, y: 0 } },
};

const okEnvelope = (data: unknown) =>
  Promise.resolve({ data: { status: 'ok', message: null, data } });

/** Envelope mocks for the lazily loaded execution option lists. */
function stubExecutionOptions() {
  apiMocks.configProfileList.mockResolvedValue(
    okEnvelope({
      info_list: [
        { id: 'cfg1', name: 'Profile One' },
        { id: 'cfg2', name: 'Profile Two' },
      ],
    }),
  );
  apiMocks.toolList.mockResolvedValue(
    okEnvelope([
      { name: 'tool_a', active: true },
      { name: 'tool_b', active: false },
      { name: 'tool_c' },
    ]),
  );
  apiMocks.skillList.mockResolvedValue(
    okEnvelope({
      skills: [
        { name: 'skill_x', active: true },
        { name: 'skill_y', active: false },
      ],
    }),
  );
}

const stubs = {
  'v-select': {
    props: {
      modelValue: { type: [String, Number, Array], default: '' },
      label: { type: String, default: '' },
      items: { type: Array, default: () => [] },
      itemTitle: { type: String, default: 'title' },
      itemValue: { type: String, default: 'value' },
      multiple: { type: Boolean, default: false },
    },
    emits: ['update:modelValue'],
    methods: {
      optionTitle(this: any, it: any): string {
        return typeof it === 'object' && it !== null ? it[this.itemTitle] : it;
      },
      optionValue(this: any, it: any): string {
        return typeof it === 'object' && it !== null ? it[this.itemValue] : it;
      },
      onChange(this: any, e: Event) {
        const el = e.target as HTMLSelectElement;
        if (this.multiple) {
          // happy-dom has no `selectedOptions`; derive from option state.
          this.$emit(
            'update:modelValue',
            Array.from(el.options)
              .filter((o) => o.selected)
              .map((o) => o.value),
          );
        } else {
          this.$emit('update:modelValue', el.value);
        }
      },
    },
    template: `<select class="select-stub" :data-label="label" :data-value="multiple ? JSON.stringify(modelValue ?? []) : (modelValue ?? '')" :multiple="multiple" @change="onChange">
      <option v-for="(it, i) in items" :key="i" :value="optionValue(it)" :selected="multiple && Array.isArray(modelValue) && modelValue.includes(optionValue(it))">{{ optionTitle(it) }}</option>
    </select>`,
  },
  'v-radio-group': {
    name: 'v-radio-group',
    props: {
      modelValue: { type: [String, Number], default: '' },
      label: { type: String, default: '' },
    },
    emits: ['update:modelValue'],
    provide() {
      return {
        radioGroupChange: (value: string) => {
          (this as any).$emit('update:modelValue', value);
        },
      };
    },
    template:
      '<div class="radio-group-stub" :data-label="label" :data-value="modelValue"><slot /></div>',
  },
  'v-radio': {
    name: 'v-radio',
    props: {
      label: { type: String, default: '' },
      value: { type: [String, Number], default: '' },
    },
    inject: ['radioGroupChange'],
    template:
      '<label class="radio-stub" :data-value="value"><input type="radio" :value="value" @change="radioGroupChange(value)" />{{ label }}</label>',
  },
  'v-checkbox-btn': {
    name: 'v-checkbox-btn',
    props: {
      modelValue: { type: Boolean, default: false },
      label: { type: String, default: '' },
    },
    emits: ['update:modelValue'],
    template:
      '<label class="checkbox-stub" :data-label="label"><input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', !modelValue)" />{{ label }}</label>',
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
  // Nested layout root for the inspector drawer (keeps the drawer out of the
  // app-level layout so v-main content never shifts).
  'v-layout': { template: '<div class="layout-stub"><slot /></div>' },
  'v-navigation-drawer': {
    props: {
      modelValue: { type: Boolean, default: false },
      location: { type: String, default: '' },
      temporary: { type: Boolean, default: false },
      absolute: { type: Boolean, default: false },
      width: { type: [Number, String], default: undefined },
    },
    emits: ['update:modelValue'],
    template: `<aside v-if="modelValue" class="drawer-stub" :data-location="location" :data-temporary="temporary" :data-width="String(width)"><slot /></aside>`,
  },
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
  // Node creation moved into the member strip: each row click adds one node.
  const rows = wrapper.findAll('.editor-member-row');
  for (let i = 0; i < count; i += 1) {
    await rows[i % rows.length]!.trigger('click');
  }
}

// Reset the shared flow/display mocks before every test in this file.
beforeEach(() => {
  flowMock.screenToFlowCoordinate.mockReset();
  flowMock.screenToFlowCoordinate.mockImplementation((p: { x: number; y: number }) => ({
    x: p.x * 2,
    y: p.y * 2,
  }));
  displayMocks.lgAndUp!.value = true;
});

describe('WorkflowEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    composableMocks.saveWorkflow.mockResolvedValue({
      workflow_id: 'wf9',
      name: 'saved',
    });
    composableMocks.lastErrorFields.value = [];
  });

  it('adds nodes with auto ids, member binding and staggered positions', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await wrapper.findAll('.editor-member-row')[1]!.trigger('click');

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
    expect(banner.text()).toContain(zh.editor.cycleDetected.replace('{path}', 'n1 → n2 → n1'));

    await wrapper
      .find('input[data-label="' + zh.editor.workflowName + '"]')
      .setValue('Loop');
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();

    expect(composableMocks.saveWorkflow).not.toHaveBeenCalled();
    expect(wrapper.find('.editor-banner').exists()).toBe(true);
  });

  it('shows the localized node-overflow banner at the backend node limit', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 21);

    const banner = wrapper.find('.editor-banner');
    expect(banner.exists()).toBe(true);
    expect(banner.text()).toContain(
      zh.editor.tooManyNodes.replace('{count}', '21').replace('{max}', '20'),
    );

    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();
    expect(composableMocks.saveWorkflow).not.toHaveBeenCalled();
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

describe('WorkflowEditor workbench', () => {
  it('adds nodes via member rows that carry drag payload, usage counts and badges', async () => {
    const wrapper = mountEditor();
    const rows = wrapper.findAll('.editor-member-row');
    expect(rows).toHaveLength(2);
    // Coordinator badge on Alice; usage counts start at zero.
    expect(rows[0]!.text()).toContain(zh.teams.coordinator);
    expect(rows[0]!.text()).toContain(zh.editor.usedCount.replace('{n}', '0'));

    const setData = vi.fn();
    await rows[0]!.trigger('dragstart', { dataTransfer: { setData } });
    expect(setData).toHaveBeenCalledWith('application/x-member-id', 'm1');

    await rows[0]!.trigger('click');
    await nextTick();
    expect(canvasNodes(wrapper).map((n) => n.data.memberId)).toEqual(['m1']);
    expect(wrapper.findAll('.editor-member-row')[0]!.text()).toContain(
      zh.editor.usedCount.replace('{n}', '1'),
    );
  });

  it('adds a node at the converted drop position with the dragged member', async () => {
    const wrapper = mountEditor();
    await wrapper.find('.teams-flow-canvas').trigger('drop', {
      dataTransfer: {
        getData: (type: string) => (type === 'application/x-member-id' ? 'm2' : ''),
      },
      clientX: 100,
      clientY: 80,
    });
    await nextTick();

    // The canvas converts screen -> flow coords (mock maps x2/y2) and the
    // editor places the new node there.
    expect(flowMock.screenToFlowCoordinate).toHaveBeenCalledWith({ x: 100, y: 80 });
    const nodes = canvasNodes(wrapper);
    expect(nodes).toHaveLength(1);
    expect(nodes[0]).toMatchObject({
      id: 'n1',
      position: { x: 200, y: 160 },
      data: { memberId: 'm2', memberName: 'Bob' },
    });
  });

  it('marks the editor dirty on edits and clears the dot after a save', async () => {
    const wrapper = mountEditor();
    expect(wrapper.find('.editor-unsaved-dot').exists()).toBe(false);

    await addNodes(wrapper, 1);
    expect(wrapper.find('.editor-unsaved-dot').exists()).toBe(true);

    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();
    expect(wrapper.find('.editor-unsaved-dot').exists()).toBe(false);
  });

  it('counts local problems in the validation badge and lists them on click', async () => {
    const wrapper = mountEditor();
    expect(findButton(wrapper, zh.editor.validationBadge)!.attributes('disabled')).toBeDefined();

    await addNodes(wrapper, 2);
    await emitConnect(wrapper, 'n1', 'n2');
    await emitConnect(wrapper, 'n2', 'n1');

    const badge = findButton(wrapper, zh.editor.validationBadge)!;
    expect(badge.attributes('disabled')).toBeUndefined();
    expect(badge.text()).toContain('1');

    await badge.trigger('click');
    const list = wrapper.find('.editor-problems-list');
    expect(list.exists()).toBe(true);
    expect(list.text()).toContain(zh.editor.cycleDetected.replace('{path}', 'n1 → n2 → n1'));
    // Structural problems have no node target: the entry is not clickable.
    expect(list.find('button.editor-problem-item').attributes('disabled')).toBeDefined();
  });

  it('opens the docked inspector on select and closes on deselect', async () => {
    const wrapper = mountEditor();
    expect(wrapper.find('.drawer-stub').exists()).toBe(false);

    await addNodes(wrapper, 1);
    const drawer = wrapper.find('.drawer-stub');
    expect(drawer.exists()).toBe(true);
    expect(drawer.attributes('data-location')).toBe('right');
    expect(drawer.attributes('data-temporary')).toBe('false');
    expect(wrapper.find('.editor-inspector').exists()).toBe(true);

    canvas(wrapper).vm.$emit('paneClick');
    await nextTick();
    expect(wrapper.find('.drawer-stub').exists()).toBe(false);
    expect(wrapper.find('.editor-inspector').exists()).toBe(false);
  });

  it('falls back to a temporary overlay drawer on narrow viewports', async () => {
    displayMocks.lgAndUp!.value = false;
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);

    const drawer = wrapper.find('.drawer-stub');
    expect(drawer.exists()).toBe(true);
    expect(drawer.attributes('data-temporary')).toBe('true');
  });
});

describe('WorkflowEditor variable chips and error mapping', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    composableMocks.saveWorkflow.mockResolvedValue({
      workflow_id: 'wf9',
      name: 'saved',
    });
    composableMocks.lastErrorFields.value = [];
  });

  /** Load a workflow fixture, then select one of its nodes. */
  async function openWorkflowNode(wrapper: VueWrapper<any>, workflowId: string, nodeId: string) {
    await wrapper.find('select.workflow-picker').setValue(workflowId);
    await flushPromises();
    await selectNode(wrapper, nodeId);
  }

  it('renders an {{input}} chip plus one labeled chip per direct predecessor', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW] });
    await openWorkflowNode(wrapper, 'wf1', 'n2');

    const chips = wrapper.findAll('.editor-var-chip');
    expect(chips.map((c) => c.text())).toEqual(['{{input}}', '{{n1}} · Alice']);

    // n1 has no predecessors: only the input chip remains.
    await selectNode(wrapper, 'n1');
    expect(wrapper.findAll('.editor-var-chip').map((c) => c.text())).toEqual(['{{input}}']);
  });

  it('inserts a predecessor variable at the cursor from its chip', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW] });
    await openWorkflowNode(wrapper, 'wf1', 'n2');

    const taskArea = wrapper.find('.editor-inspector textarea');
    await taskArea.setValue('Fix bug');
    (taskArea.element as HTMLTextAreaElement).setSelectionRange(3, 3);
    // Second chip is the n1 predecessor; the first is {{input}}.
    await wrapper.findAll('.editor-var-chip')[1]!.trigger('click');

    expect(taskArea.element).toHaveProperty('value', 'Fix{{n1}} bug');
  });

  it('warns locally about unconnected references without blocking save', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW_CHAIN] });
    await openWorkflowNode(wrapper, 'wf5', 'n2');

    const warnings = wrapper.findAll('.editor-unconnected li');
    expect(warnings.map((w) => w.text())).toEqual([
      zh.editor.unconnectedRef.replace('{id}', 'n3'),
    ]);

    // A node with clean references shows no warning block.
    await selectNode(wrapper, 'n1');
    expect(wrapper.find('.editor-unconnected').exists()).toBe(false);

    // Warnings are informational only: saving is never blocked by them.
    await wrapper.find('input[data-label="' + zh.editor.workflowName + '"]').setValue('Chain');
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();
    expect(composableMocks.saveWorkflow).toHaveBeenCalledTimes(1);
    expect(toastMock.success).toHaveBeenCalled();
  });

  it('lists unconnected references in the summary and selects the node on click', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW_CHAIN] });
    // n1 is selected, but the problem belongs to n2.
    await openWorkflowNode(wrapper, 'wf5', 'n1');

    const badge = findButton(wrapper, zh.editor.validationBadge)!;
    expect(badge.attributes('disabled')).toBeUndefined();
    expect(badge.text()).toContain('1');

    await badge.trigger('click');
    const list = wrapper.find('.editor-problems-list');
    expect(list.text()).toContain(zh.editor.unconnectedRef.replace('{id}', 'n3'));

    const entry = list.find('button.editor-problem-item:not([disabled])');
    expect(entry.exists()).toBe(true);
    await entry.trigger('click');

    // Clicking the problem selects (and opens the inspector for) node n2.
    const taskArea = wrapper.find('.editor-inspector textarea');
    expect(taskArea.element).toHaveProperty('value', 'see {{n3}}');
  });

  it('makes server field errors with node paths clickable to select the node', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW] });
    await openWorkflowNode(wrapper, 'wf1', 'n1');
    composableMocks.saveWorkflow.mockResolvedValueOnce(null);
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();
    composableMocks.lastErrorFields.value = [
      { path: 'nodes.n2.task', message: '任务模板过长' },
      { path: 'name', message: '名称已存在' },
    ];
    await nextTick();

    const items = wrapper.findAll('.editor-banner-fields .editor-banner-field');
    expect(items).toHaveLength(2);
    // `nodes.n2.task` maps to a graph node -> clickable; `name` does not.
    expect(items[0]!.element.tagName).toBe('BUTTON');
    expect(items[0]!.text()).toContain('nodes.n2.task');
    expect(items[1]!.element.tagName).toBe('SPAN');

    await items[0]!.trigger('click');
    const taskArea = wrapper.find('.editor-inspector textarea');
    expect(taskArea.element).toHaveProperty('value', 'Do B');
  });

  it('dedupes server field messages against local problems and themselves', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 2);
    await emitConnect(wrapper, 'n1', 'n2');
    await emitConnect(wrapper, 'n2', 'n1');
    const localCycle = zh.editor.cycleDetected.replace('{path}', 'n1 → n2 → n1');
    composableMocks.lastErrorFields.value = [
      { path: 'graph', message: localCycle },
      { path: 'graph', message: '其他错误' },
      { path: 'graph', message: '其他错误' },
    ];
    await nextTick();

    const items = wrapper.findAll('.editor-banner-fields .editor-banner-field');
    // The server entry matching the local banner is dropped, and the two
    // identical server messages collapse into one.
    expect(items).toHaveLength(1);
    expect(items[0]!.text()).toContain('其他错误');
  });
});

describe('WorkflowEditor execution config', () => {
  /** Expand the inspector's execution config group for the selected node. */
  async function expandExecGroup(wrapper: VueWrapper<any>) {
    await findButton(wrapper, zh.editor.executionConfig)!.trigger('click');
    await flushPromises();
  }

  beforeEach(() => {
    vi.clearAllMocks();
    composableMocks.saveWorkflow.mockResolvedValue({
      workflow_id: 'wf9',
      name: 'saved',
    });
    composableMocks.lastErrorFields.value = [];
    stubExecutionOptions();
  });

  it('loads profile/tool/skill options lazily on first group expansion', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await selectNode(wrapper, 'n1');

    expect(apiMocks.configProfileList).not.toHaveBeenCalled();
    expect(apiMocks.toolList).not.toHaveBeenCalled();
    expect(apiMocks.skillList).not.toHaveBeenCalled();

    await expandExecGroup(wrapper);

    expect(apiMocks.configProfileList).toHaveBeenCalledTimes(1);
    expect(apiMocks.toolList).toHaveBeenCalledTimes(1);
    expect(apiMocks.skillList).toHaveBeenCalledTimes(1);

    // Collapsing and re-expanding must not reload the lists.
    await findButton(wrapper, zh.editor.executionConfig)!.trigger('click');
    await expandExecGroup(wrapper);
    expect(apiMocks.configProfileList).toHaveBeenCalledTimes(1);
    expect(apiMocks.toolList).toHaveBeenCalledTimes(1);
    expect(apiMocks.skillList).toHaveBeenCalledTimes(1);

    // Options come from the facades; inactive entries are filtered out.
    const profileSelect = wrapper.find(
      `select[data-label="${zh.editor.configProfile}"]`,
    );
    const profileTitles = profileSelect
      .findAll('option')
      .map((o: any) => o.text());
    expect(profileTitles).toEqual([
      zh.editor.configProfileDefault,
      'Profile One',
      'Profile Two',
    ]);
    // The tool multi-select only renders in allowlist mode.
    await wrapper
      .find('.radio-stub[data-value="allowlist"] input[type="radio"]')
      .trigger('change');
    const toolTitles = wrapper
      .find('select[multiple]')
      .findAll('option')
      .map((o: any) => o.text());
    expect(toolTitles).toEqual(['tool_a', 'tool_c']);
  });

  it('renders profile default option, persona toggle and mode radios', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await selectNode(wrapper, 'n1');
    await expandExecGroup(wrapper);

    const groups = wrapper.findAll('.radio-group-stub');
    expect(groups).toHaveLength(2);
    expect(groups[0].attributes('data-label')).toBe(zh.editor.toolsOverride);
    expect(groups[0].attributes('data-value')).toBe('inherit');
    expect(
      groups[0].findAll('.radio-stub').map((r: any) => r.attributes('data-value')),
    ).toEqual(['inherit', 'disable_all', 'allowlist']);
    expect(groups[0].text()).toContain(zh.editor.toolsInherit);
    expect(groups[0].text()).toContain(zh.editor.toolsDisableAll);
    expect(groups[0].text()).toContain(zh.editor.toolsAllowlist);

    expect(groups[1].attributes('data-label')).toBe(zh.editor.skillsOverride);
    expect(
      groups[1].findAll('.radio-stub').map((r: any) => r.attributes('data-value')),
    ).toEqual(['inherit', 'disable_all', 'allowlist']);
    expect(groups[1].text()).toContain(zh.editor.skillsDisableAll);

    expect(wrapper.find(`.checkbox-stub[data-label="${zh.editor.personaOverride}"]`).exists()).toBe(
      true,
    );
    // The PersonaSelector only appears after the override toggle is on.
    expect(wrapper.find('.persona-stub').exists()).toBe(false);
    await wrapper.find('input[type="checkbox"]').setValue(true);
    expect(wrapper.find('.persona-stub').exists()).toBe(true);
    expect(wrapper.text()).toContain(zh.editor.personaFollowProfile);
  });

  it('omits the execution block entirely when every field stays inherit', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await selectNode(wrapper, 'n1');
    await expandExecGroup(wrapper);

    await wrapper.find('input[data-label="' + zh.editor.workflowName + '"]').setValue('Pipe');
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();

    expect(composableMocks.saveWorkflow).toHaveBeenCalledTimes(1);
    const payload = composableMocks.saveWorkflow.mock.calls[0][1];
    expect(payload.graph.nodes[0]).toEqual({ id: 'n1', member_id: 'm1', task: '' });
    expect('execution' in payload.graph.nodes[0]).toBe(false);
  });

  it('serializes the selected node execution block on save', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 2);
    await selectNode(wrapper, 'n2');
    await expandExecGroup(wrapper);

    await wrapper
      .find(`select[data-label="${zh.editor.configProfile}"]`)
      .setValue('cfg1');
    await wrapper.find('input[type="checkbox"]').setValue(true);
    await wrapper.find('.persona-stub').setValue('p1');
    // First radio group is tools; switch it to allowlist and pick tools.
    await wrapper
      .find('.radio-stub[data-value="allowlist"] input[type="radio"]')
      .trigger('change');
    await wrapper.find('select[multiple]').setValue(['tool_a']);
    // Second radio group is skills; its multi-select is the second one.
    const groups = wrapper.findAll('.radio-group-stub');
    await groups[1].find('.radio-stub[data-value="allowlist"] input[type="radio"]').trigger('change');
    await wrapper.findAll('select[multiple]')[1].setValue(['skill_x']);

    await wrapper.find('input[data-label="' + zh.editor.workflowName + '"]').setValue('Pipe');
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();

    const payload = composableMocks.saveWorkflow.mock.calls[0][1];
    // Only the node edited in the inspector carries an execution block.
    expect('execution' in payload.graph.nodes[0]).toBe(false);
    expect(payload.graph.nodes[1].execution).toEqual({
      config_id: 'cfg1',
      persona_id: 'p1',
      tools: ['tool_a'],
      skills: ['skill_x'],
    });
  });

  it('serializes tools disable_all as an empty list and inherit omits the key', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await selectNode(wrapper, 'n1');
    await expandExecGroup(wrapper);

    await wrapper
      .find('.radio-stub[data-value="disable_all"] input[type="radio"]')
      .trigger('change');
    await wrapper.find('input[data-label="' + zh.editor.workflowName + '"]').setValue('Pipe');
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();

    expect(composableMocks.saveWorkflow.mock.calls[0][1].graph.nodes[0].execution).toEqual({
      tools: [],
    });

    // The successful save re-points the workflow picker, which clears the
    // canvas selection; select the node again before editing further.
    await selectNode(wrapper, 'n1');
    // Back to inherit: the block disappears again.
    await wrapper
      .find('.radio-stub[data-value="inherit"] input[type="radio"]')
      .trigger('change');
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();
    expect(composableMocks.saveWorkflow.mock.calls[1][1].graph.nodes[0].execution).toBeUndefined();
  });

  it('serializes skills disable_all as an empty list and inherit omits the key', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await selectNode(wrapper, 'n1');
    await expandExecGroup(wrapper);

    // Second radio group is skills; switch it to disable_all.
    const groups = wrapper.findAll('.radio-group-stub');
    await groups[1].find('.radio-stub[data-value="disable_all"] input[type="radio"]').trigger('change');
    await wrapper.find('input[data-label="' + zh.editor.workflowName + '"]').setValue('Pipe');
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();

    expect(composableMocks.saveWorkflow.mock.calls[0][1].graph.nodes[0].execution).toEqual({
      skills: [],
    });

    // The successful save re-points the workflow picker, which clears the
    // canvas selection; select the node again before editing further.
    await selectNode(wrapper, 'n1');
    // Back to inherit: the block disappears again (re-query: the inspector
    // re-rendered after the save).
    const regroups = wrapper.findAll('.radio-group-stub');
    await regroups[1].find('.radio-stub[data-value="inherit"] input[type="radio"]').trigger('change');
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();
    expect(composableMocks.saveWorkflow.mock.calls[1][1].graph.nodes[0].execution).toBeUndefined();
  });

  it('loads a workflow with skills: [] as disable-all and round-trips it', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW_WITH_SKILLS_DISABLED] });
    await wrapper.find('select.workflow-picker').setValue('wf4');
    await flushPromises();
    await selectNode(wrapper, 'n1');
    await expandExecGroup(wrapper);

    // `[]` parses as the disable-all state, not an empty allowlist.
    const groups = wrapper.findAll('.radio-group-stub');
    expect(groups[1].attributes('data-value')).toBe('disable_all');
    // The skills multi-select only renders in allowlist mode.
    expect(wrapper.findAll('select[multiple]')).toHaveLength(0);

    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();

    expect(composableMocks.saveWorkflow.mock.calls[0][1].graph.nodes[0].execution).toEqual({
      skills: [],
    });
  });

  it('loads execution state from a workflow and round-trips it on save', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW_WITH_EXEC] });
    await wrapper.find('select.workflow-picker').setValue('wf3');
    await flushPromises();
    await selectNode(wrapper, 'n1');
    await expandExecGroup(wrapper);

    expect(
      wrapper.find(`select[data-label="${zh.editor.configProfile}"]`).attributes('data-value'),
    ).toBe('cfg1');
    expect(
      (wrapper.find('input[type="checkbox"]').element as HTMLInputElement).checked,
    ).toBe(true);
    expect(wrapper.find('.persona-stub').element).toHaveProperty('value', 'p1');
    const groups = wrapper.findAll('.radio-group-stub');
    expect(groups[0].attributes('data-value')).toBe('allowlist');
    expect(groups[1].attributes('data-value')).toBe('allowlist');
    expect(
      JSON.parse(wrapper.find('select[multiple]').attributes('data-value')!),
    ).toEqual(['tool_a']);

    await findButton(wrapper, zh.editor.save)!.trigger('click');
    await flushPromises();

    const payload = composableMocks.saveWorkflow.mock.calls[0][1];
    expect(payload.graph.nodes[0].execution).toEqual({
      config_id: 'cfg1',
      persona_id: 'p1',
      tools: ['tool_a'],
      skills: ['skill_x'],
    });
    expect('execution' in payload.graph.nodes[1]).toBe(false);
  });

  it('toasts option load failures and keeps the lists usable', async () => {
    apiMocks.configProfileList.mockRejectedValue(new Error('boom'));
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await selectNode(wrapper, 'n1');
    await expandExecGroup(wrapper);

    expect(toastMock.error).toHaveBeenCalledWith('boom');
    const profileOptions = wrapper
      .find(`select[data-label="${zh.editor.configProfile}"]`)
      .findAll('option')
      .map((o: any) => o.text());
    expect(profileOptions).toEqual([zh.editor.configProfileDefault]);
  });

  it('renders backend field errors in the banner after a failed save', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    composableMocks.saveWorkflow.mockResolvedValueOnce(null);
    await wrapper.find('input[data-label="' + zh.editor.workflowName + '"]').setValue('Pipe');
    await findButton(wrapper, zh.editor.save)!.trigger('click');
    // The composable captures the structured fields from the error envelope
    // while the editor only renders them.
    composableMocks.lastErrorFields.value = [
      { path: 'nodes.n1.execution.config_id', message: '节点 n1 的配置档案不存在: xxx' },
    ];
    await flushPromises();

    const banner = wrapper.find('.editor-banner');
    expect(banner.exists()).toBe(true);
    expect(banner.text()).toContain(zh.editor.validation);
    expect(banner.text()).toContain('nodes.n1.execution.config_id');
    expect(banner.text()).toContain('节点 n1 的配置档案不存在: xxx');

    // Fields clear with the next successful save.
    composableMocks.lastErrorFields.value = [];
    await flushPromises();
    expect(wrapper.find('.editor-banner').exists()).toBe(false);
  });
});

describe('WorkflowEditor member card data', () => {
  /** Expand the inspector's execution config group (loads the option lists). */
  async function expandExecGroup(wrapper: VueWrapper<any>) {
    await findButton(wrapper, zh.editor.executionConfig)!.trigger('click');
    await flushPromises();
  }

  it('feeds the canvas member cards (color, number, task preview, in-degree)', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW] });
    await wrapper.find('select.workflow-picker').setValue('wf1');
    await flushPromises();

    const nodes = canvasNodes(wrapper);
    expect(nodes[0].type).toBe('member');
    expect(nodes[0].data).toMatchObject({
      memberName: 'Alice',
      memberId: 'm1',
      memberColor: collabMemberColor('Alice'),
      nodeTitle: 'n1',
      nodeNumber: 1,
      taskPreview: 'Do A',
      inDegree: 0,
      interactive: true,
      missingMember: false,
    });
    // n2 sits behind the loaded n1->n2 edge.
    expect(nodes[1].data).toMatchObject({ nodeNumber: 2, inDegree: 1 });
  });

  it('truncates long task previews to ~60 chars with an ellipsis', async () => {
    const longTask = '任务'.repeat(60);
    const wf = {
      ...WORKFLOW,
      graph: { nodes: [{ id: 'n1', member_id: 'm1', task: longTask }], edges: [] },
      layout: { n1: { x: 0, y: 0 } },
    };
    const wrapper = mountEditor({ workflows: [wf] });
    await wrapper.find('select.workflow-picker').setValue('wf1');
    await flushPromises();

    expect(canvasNodes(wrapper)[0].data.taskPreview).toBe(`${longTask.slice(0, 60)}…`);
  });

  it('exposes execution chip labels with the raw config id as fallback', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW_WITH_EXEC] });
    await wrapper.find('select.workflow-picker').setValue('wf3');
    await flushPromises();

    const nodes = canvasNodes(wrapper);
    // The profile option cache is not loaded until the inspector expands, so
    // the card shows the raw execution ids (v1 fallback).
    expect(nodes[0].data.configLabel).toBe('cfg1');
    expect(nodes[0].data.personaLabel).toBe('p1');
    expect(nodes[1].data.configLabel).toBeUndefined();
    expect(nodes[1].data.personaLabel).toBeUndefined();
  });

  it('uses the loaded config profile name once the option cache exists', async () => {
    const wrapper = mountEditor({ workflows: [WORKFLOW_WITH_EXEC] });
    await wrapper.find('select.workflow-picker').setValue('wf3');
    await flushPromises();
    await selectNode(wrapper, 'n1');
    await expandExecGroup(wrapper);

    expect(canvasNodes(wrapper)[0].data.configLabel).toBe('Profile One');
  });

  it('flags nodes whose member is missing on the card data', async () => {
    const wrapper = mountEditor({ workflows: [GHOST_WORKFLOW] });
    await wrapper.find('select.workflow-picker').setValue('wf2');
    await flushPromises();

    const node = canvasNodes(wrapper)[0];
    expect(node.data.missingMember).toBe(true);
    expect(node.class).toContain('at-node-missing');
  });
});

describe('WorkflowEditor inspector relations and save-and-run (Plan 3 T8)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    composableMocks.saveWorkflow.mockResolvedValue({
      workflow_id: 'wf9',
      name: 'saved',
    });
    composableMocks.lastErrorFields.value = [];
  });

  it('lists the selected node basic info and its upstream/downstream nodes', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 2);
    await emitConnect(wrapper, 'n1', 'n2');
    await selectNode(wrapper, 'n2');

    expect(wrapper.find('[data-test="basic-info"]').text()).toContain('n2');
    expect(wrapper.findAll('[data-test="upstream-chip"]').map((c) => c.text())).toEqual(['n1']);
    // n2 is a leaf here, so its downstream list stays empty.
    expect(wrapper.findAll('[data-test="downstream-chip"]')).toHaveLength(0);

    await selectNode(wrapper, 'n1');
    expect(wrapper.findAll('[data-test="downstream-chip"]').map((c) => c.text())).toEqual(['n2']);
    expect(wrapper.findAll('[data-test="upstream-chip"]')).toHaveLength(0);
  });

  it('emits saveAndRun with the saved workflow id after a successful save', async () => {
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await wrapper.find('[data-test="save-and-run"]').trigger('click');
    await flushPromises();

    expect(composableMocks.saveWorkflow).toHaveBeenCalledTimes(1);
    expect(wrapper.emitted('saveAndRun')).toEqual([['wf9']]);
  });

  it('does not emit saveAndRun when the save returns an error envelope', async () => {
    composableMocks.saveWorkflow.mockResolvedValueOnce(null);
    const wrapper = mountEditor();
    await addNodes(wrapper, 1);
    await wrapper.find('[data-test="save-and-run"]').trigger('click');
    await flushPromises();

    expect(wrapper.emitted('saveAndRun')).toBeUndefined();
  });

  it('offers the member palette as a drawer on narrow viewports', async () => {
    displayMocks.lgAndUp!.value = false;
    const wrapper = mountEditor();
    await nextTick();

    // The palette drawer is opt-in: the toolbar button opens it, so the canvas
    // keeps the width until the user asks for the palette.
    const openButton = wrapper.find('[data-test="open-members"]');
    expect(openButton.exists()).toBe(true);
    expect(wrapper.find('[data-test="members-drawer"]').exists()).toBe(false);

    await openButton.trigger('click');
    await nextTick();
    expect(wrapper.find('[data-test="members-drawer"]').exists()).toBe(true);
  });

  it('keeps the toolbar palette button hidden on wide viewports', async () => {
    displayMocks.lgAndUp!.value = true;
    const wrapper = mountEditor();
    await nextTick();
    expect(wrapper.find('[data-test="open-members"]').exists()).toBe(false);
  });
});

describe('TeamsFlowCanvas', () => {
  const NODES = [
    {
      id: 'n1',
      position: { x: 0, y: 0 },
      data: { label: 'Alice (n1)', memberName: 'Alice', memberId: 'm1', task: 'Do A' },
    },
    {
      id: 'n2',
      position: { x: 80, y: 0 },
      data: { label: 'Bob (n2)', memberName: 'Bob', memberId: 'm2', task: 'Do B' },
    },
  ];
  const EDGES = [{ id: 'e:n1->n2', source: 'n1', target: 'n2' }];

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

  it('renders every node as the member card type with a non-reactive registry', () => {
    const wrapper = mount(TeamsFlowCanvas, {
      props: { nodes: NODES, edges: [], mode: 'edit' },
      global: { stubs },
    }) as VueWrapper<any>;
    const flow = canvas(wrapper);

    const nodes = flow.props('nodes') as any[];
    expect(nodes.map((n) => n.type)).toEqual(['member', 'member']);
    // Enriched data is forwarded as-is for the card to render.
    expect(nodes[0].data).toMatchObject({ memberName: 'Alice', memberId: 'm1', task: 'Do A' });
    expect(nodes[0].data.label).toBe('Alice (n1)');

    const registry = flow.props('nodeTypes') as Record<string, unknown>;
    expect(registry.member).toBeTruthy();
  });

  it('adds arrow markers to every edge and never animates in edit mode', () => {
    const wrapper = mount(TeamsFlowCanvas, {
      props: { nodes: NODES, edges: EDGES, mode: 'edit' },
      global: { stubs },
    }) as VueWrapper<any>;

    const out = canvas(wrapper).props('edges') as any[];
    expect(out).toHaveLength(1);
    expect(out[0].markerEnd).toBe('arrowclosed');
    expect(out[0].animated).toBeFalsy();
  });

  it('animates monitor edges only from running source nodes', () => {
    const nodes = [
      { id: 'n1', position: { x: 0, y: 0 }, data: { label: 'n1', status: 'running' } },
      { id: 'n2', position: { x: 80, y: 0 }, data: { label: 'n2', status: 'pending' } },
      { id: 'n3', position: { x: 160, y: 0 }, data: { label: 'n3', status: 'done' } },
    ];
    const wrapper = mount(TeamsFlowCanvas, {
      props: {
        nodes,
        edges: [
          { id: 'e1', source: 'n1', target: 'n2' },
          { id: 'e2', source: 'n2', target: 'n3' },
          { id: 'e3', source: 'unknown', target: 'n1' },
        ],
        mode: 'monitor',
        nodeStates: {
          n1: { status: 'running' },
          n2: { status: 'pending' },
          n3: { status: 'done' },
        },
      },
      global: { stubs },
    }) as VueWrapper<any>;

    const out = canvas(wrapper).props('edges') as any[];
    expect(out.map((e) => e.markerEnd)).toEqual(['arrowclosed', 'arrowclosed', 'arrowclosed']);
    // e1's source n1 is running -> animated; e2/e3 are not.
    expect(out[0].animated).toBe(true);
    expect(out[1].animated).toBeFalsy();
    expect(out[2].animated).toBeFalsy();
  });

  it('monitor mode locks interaction, forwards status data and drops domAttributes tooltips', () => {
    const nodes = [
      {
        id: 'n1',
        position: { x: 0, y: 0 },
        data: { label: 'Alice (n1)', status: 'running', error: undefined },
      },
      {
        id: 'n2',
        position: { x: 80, y: 0 },
        data: { label: 'Bob (n2)', status: 'failed', error: '模型返回 500' },
      },
    ];
    const wrapper = mount(TeamsFlowCanvas, {
      props: {
        nodes,
        edges: [],
        mode: 'monitor',
        nodeStates: {
          n1: { status: 'running' },
          n2: { status: 'failed', error: '模型返回 500' },
        },
      },
      global: { stubs },
    }) as VueWrapper<any>;
    const flow = canvas(wrapper);

    expect(flow.props('nodesDraggable')).toBe(false);
    expect(flow.props('nodesConnectable')).toBe(false);
    const out = flow.props('nodes') as any[];
    // The card owns status/error rendering: data passes through untouched and
    // the node-level domAttributes tooltip escape hatch is gone (no double
    // tooltip on failed nodes).
    expect(out[0].data).toMatchObject({ status: 'running' });
    expect(out[1].data).toMatchObject({ status: 'failed', error: '模型返回 500' });
    for (const node of out) {
      expect(node.domAttributes).toBeUndefined();
    }
  });

  it('forwards member drops as dropAt with converted flow coordinates', async () => {
    const wrapper = mount(TeamsFlowCanvas, {
      props: { nodes: NODES, edges: [], mode: 'edit' },
      global: { stubs },
    }) as VueWrapper<any>;
    const root = wrapper.find('.teams-flow-canvas');

    // The root prevent-defaults dragover so the browser allows the drop.
    const dragover = new Event('dragover', { bubbles: true, cancelable: true });
    root.element.dispatchEvent(dragover);
    expect(dragover.defaultPrevented).toBe(true);

    await root.trigger('drop', {
      dataTransfer: {
        getData: (type: string) => (type === 'application/x-member-id' ? 'm2' : ''),
      },
      clientX: 100,
      clientY: 80,
    });
    await flushPromises();

    expect(flowMock.screenToFlowCoordinate).toHaveBeenCalledWith({ x: 100, y: 80 });
    expect(wrapper.emitted('dropAt')).toEqual([['m2', { x: 200, y: 160 }]]);
  });

  it('ignores drops that carry no member payload', async () => {
    const wrapper = mount(TeamsFlowCanvas, {
      props: { nodes: NODES, edges: [], mode: 'edit' },
      global: { stubs },
    }) as VueWrapper<any>;
    await wrapper.find('.teams-flow-canvas').trigger('drop', {
      dataTransfer: { getData: () => '' },
      clientX: 5,
      clientY: 5,
    });
    await flushPromises();
    expect(wrapper.emitted('dropAt')).toBeUndefined();
  });

  it('mounts the canvas chrome (background, controls, minimap) in both modes', () => {
    for (const mode of ['edit', 'monitor'] as const) {
      const wrapper = mount(TeamsFlowCanvas, {
        props: { nodes: NODES, edges: EDGES, mode },
        global: { stubs },
      }) as VueWrapper<any>;

      expect(wrapper.find('.vf-background-stub').exists()).toBe(true);
      expect(wrapper.find('.vf-background-stub').attributes('data-gap')).toBe('16');
      expect(wrapper.find('.vf-controls-stub').exists()).toBe(true);
      const minimap = wrapper.find('.vf-minimap-stub');
      expect(minimap.exists()).toBe(true);
      // MiniMap is rendered with pannable + zoomable enabled.
      const minimapComponent = wrapper.findComponent({ name: 'MiniMapStub' });
      expect(minimapComponent.props('pannable')).toBe(true);
      expect(minimapComponent.props('zoomable')).toBe(true);
      wrapper.unmount();
    }
  });
});
