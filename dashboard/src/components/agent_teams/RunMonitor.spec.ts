// Specs for the agent-teams run monitor (Task 8): control bar button matrix
// per run status, start payload shape, the resizable member grid forwarding
// window state, layout persistence, the mode/view toggles and mount-time run
// recovery.
//
// grid-layout-plus and TeamsFlowCanvas are replaced with passthrough stubs so
// the specs can assert layout payloads and canvas props without pulling in the
// drag-drop runtime or VueFlow. useAgentTeamsRun / useAgentTeams are mocked
// with canned run-state refs; MarkdownMessagePart / ReasoningBlock are mocked
// so AgentWindow rendering can be asserted on props. Vuetify is stubbed
// manually (the app registers it globally; tests do not pull it in).
import { flushPromises, mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia } from 'pinia';
import { nextTick } from 'vue';
import zh from '@/i18n/locales/zh-CN/features/agent-teams.json';

const fixtures = vi.hoisted(() => ({
  team: {
    team_id: 't1',
    name: 'Alpha',
    members: [
      { member_id: 'm1', name: 'Alice', session_id: 's1' },
      { member_id: 'm2', name: 'Bob', session_id: 's2' },
    ],
  },
  fiveMembers: [
    { member_id: 'm1', name: 'M1' },
    { member_id: 'm2', name: 'M2' },
    { member_id: 'm3', name: 'M3' },
    { member_id: 'm4', name: 'M4' },
    { member_id: 'm5', name: 'M5' },
  ],
  workflow: {
    workflow_id: 'wf1',
    name: 'Pipe',
    graph: { nodes: [{ id: 'w1', member_id: 'm1', task: 'A' }], edges: [] },
  },
  runRow: {
    run_id: 'run-9',
    team_id: 't1',
    status: 'interrupted',
    graph: {
      nodes: [
        { id: 'n1', member_id: 'm1', task: 'A' },
        { id: 'n2', member_id: 'm2', task: 'B' },
      ],
      edges: [{ from: 'n1', to: 'n2' }],
    },
    node_states: { n1: { status: 'done' }, n2: { status: 'pending' } },
  },
}));

const runMocks = vi.hoisted(() => ({
  loadActiveRuns: vi.fn(),
  openRun: vi.fn(),
  closeRun: vi.fn(),
  startRun: vi.fn(),
  pause: vi.fn(),
  resumeRun: vi.fn(),
  stop: vi.fn(),
  retryNode: vi.fn(),
  skipNode: vi.fn(),
  reconnect: vi.fn(),
  monitors: null as any,
  runState: null as any,
}));

const teamsMocks = vi.hoisted(() => ({
  loadWorkflows: vi.fn(),
  workflows: null as any,
}));

// Markdown/reasoning stubs must live in a hoisted block: the mock factories
// below run while RunMonitor.vue is being imported, before the spec body.
const renderStubs = vi.hoisted(() => ({
  MarkdownMessagePart: {
    name: 'MdPartStub',
    props: {
      content: { type: String, default: '' },
      isStreaming: { type: Boolean, default: false },
    },
    template: '<div class="md-part-stub">{{ content }}</div>',
  },
  ReasoningBlock: {
    name: 'ReasoningStub',
    props: { parts: { type: Array, default: () => [] } },
    template: '<div class="reasoning-stub" />',
  },
}));

vi.mock('@/composables/useAgentTeamsRun', async () => {
  // A deep ref mirrors the real composable: fold mutations flow through the
  // reactive proxy, so child components track nested window fields directly.
  const { ref } = await vi.importActual<typeof import('vue')>('vue');
  runMocks.runState = ref(null);
  runMocks.monitors = ref([]);
  return {
    useAgentTeamsRun: () => ({
      runState: runMocks.runState,
      monitors: runMocks.monitors,
      loadActiveRuns: runMocks.loadActiveRuns,
      openRun: runMocks.openRun,
      closeRun: runMocks.closeRun,
      startRun: runMocks.startRun,
      pause: runMocks.pause,
      resumeRun: runMocks.resumeRun,
      stop: runMocks.stop,
      retryNode: runMocks.retryNode,
      skipNode: runMocks.skipNode,
      reconnect: runMocks.reconnect,
    }),
  };
});

vi.mock('@/composables/useAgentTeams', async () => {
  const { ref } = await vi.importActual<typeof import('vue')>('vue');
  teamsMocks.workflows = ref([fixtures.workflow]);
  return {
    useAgentTeams: () => ({
      teams: ref([fixtures.team]),
      selectedTeamId: ref('t1'),
      selectedTeam: ref(fixtures.team),
      workflows: teamsMocks.workflows,
      loadTeams: vi.fn(),
      selectTeam: vi.fn(),
      createTeam: vi.fn(),
      updateTeam: vi.fn(),
      deleteTeam: vi.fn(),
      loadWorkflows: teamsMocks.loadWorkflows,
      saveWorkflow: vi.fn(),
      deleteWorkflow: vi.fn(),
    }),
  };
});

vi.mock('grid-layout-plus', () => ({
  GridLayout: {
    name: 'GridLayoutStub',
    props: {
      layout: { type: Array, default: () => [] },
      colNum: { type: Number, default: 12 },
      rowHeight: { type: Number, default: 100 },
      isDraggable: { type: Boolean, default: false },
      isResizable: { type: Boolean, default: false },
    },
    emits: ['update:layout'],
    template: '<div class="grid-layout-stub"><slot /></div>',
  },
  GridItem: {
    name: 'GridItemStub',
    props: {
      i: { type: [String, Number], required: true },
      x: { type: Number, default: 0 },
      y: { type: Number, default: 0 },
      w: { type: Number, default: 1 },
      h: { type: Number, default: 1 },
    },
    template:
      '<div class="grid-item-stub" :data-i="i" :data-x="x" :data-y="y" :data-w="w" :data-h="h"><slot /></div>',
  },
}));

vi.mock('@/components/agent_teams/TeamsFlowCanvas.vue', () => ({
  default: {
    name: 'TeamsFlowCanvasStub',
    props: {
      nodes: { type: Array, default: () => [] },
      edges: { type: Array, default: () => [] },
      mode: { type: String, default: 'edit' },
      nodeStates: { type: Object, default: null },
    },
    template: '<div class="flow-canvas-stub" :data-mode="mode" />',
  },
}));

vi.mock('@/components/chat/message_list_comps/MarkdownMessagePart.vue', () => ({
  default: renderStubs.MarkdownMessagePart,
}));

vi.mock('@/components/chat/message_list_comps/ReasoningBlock.vue', () => ({
  default: renderStubs.ReasoningBlock,
}));

import RunMonitor from './RunMonitor.vue';

function makeRunState(overrides: Record<string, any> = {}) {
  return {
    runId: 'run-1',
    status: 'running',
    mode: 'dag' as string,
    progress: { done: 1, running: 1, pending: 1, skipped: 0, failed: 0, total: 3 },
    round: { n: 1, max: 3 },
    windows: {} as Record<string, any>,
    nodeStates: {
      n1: { status: 'done' },
      n2: { status: 'running' },
      n3: { status: 'pending' },
    } as Record<string, { status: string }>,
    busySessionIds: new Set<string>(),
    pausedNodeId: null as string | null,
    lastError: null,
    stoppedReason: null,
    dispatches: [] as any[],
    ...overrides,
  };
}

const stubs = {
  'v-select': {
    props: {
      modelValue: { type: [String, Number], default: '' },
      items: { type: Array, default: () => [] },
      itemTitle: { type: String, default: 'title' },
      itemValue: { type: String, default: 'value' },
      label: { type: String, default: '' },
      hint: { type: String, default: '' },
      // Declared so specs can pin whether the component suppresses the
      // details block (boolean `hide-details` hides Vuetify's hint too).
      hideDetails: { type: [Boolean, String], default: false },
    },
    emits: ['update:modelValue'],
    methods: {
      optionTitle(this: any, it: any): string {
        return typeof it === 'object' && it !== null ? it[this.itemTitle] : it;
      },
      optionValue(this: any, it: any): string {
        return typeof it === 'object' && it !== null ? it[this.itemValue] : it;
      },
      optionDisabled(it: any): boolean {
        return !!(it && typeof it === 'object' && it.props && it.props.disabled);
      },
    },
    template: `<select class="select-stub" :data-label="label" :data-hint="hint" @change="$emit('update:modelValue', $event.target.value)">
      <option v-for="(it, i) in items" :key="i" :value="optionValue(it)" :disabled="optionDisabled(it)">
        {{ optionTitle(it) }}
      </option>
    </select>`,
  },
  'v-textarea': {
    props: {
      modelValue: { type: String, default: '' },
      label: { type: String, default: '' },
      placeholder: { type: String, default: '' },
    },
    emits: ['update:modelValue'],
    template:
      '<textarea class="ta-stub" :placeholder="placeholder" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
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
  'v-btn-toggle': {
    name: 'VBtnToggleStub',
    props: { modelValue: { type: String, default: null } },
    emits: ['update:modelValue'],
    template: '<div class="btn-toggle-stub"><slot /></div>',
  },
  'v-progress-linear': {
    name: 'ProgressStub',
    props: { modelValue: { type: Number, default: 0 } },
    template: '<div class="progress-stub" :data-value="modelValue" />',
  },
};

function mountRun(props: Record<string, unknown> = {}) {
  return mount(RunMonitor, {
    props: { team: fixtures.team, ...props },
    global: { plugins: [createPinia()], stubs },
  }) as VueWrapper<any>;
}

function findButton(wrapper: VueWrapper<any>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text));
}

beforeEach(() => {
  vi.clearAllMocks();
  // clearAllMocks keeps implementations: the stale-run test teaches closeRun
  // to null the singleton state, which must not leak into later tests that
  // flush mount recovery with no active run.
  runMocks.closeRun.mockReset();
  localStorage.clear();
  runMocks.runState.value = null;
  runMocks.loadActiveRuns.mockResolvedValue([]);
  runMocks.openRun.mockResolvedValue(null);
  runMocks.startRun.mockResolvedValue({ run_id: 'run-new', status: 'running' });
  teamsMocks.loadWorkflows.mockResolvedValue([]);
  teamsMocks.workflows.value = [fixtures.workflow];
});

describe('RunMonitor control bar', () => {
  it('shows only 开始运行 and the empty hint when no run is attached', () => {
    const wrapper = mountRun();
    expect(findButton(wrapper, zh.monitor.start)).toBeTruthy();
    for (const label of [
      zh.monitor.pause,
      zh.monitor.stop,
      zh.monitor.resume,
      zh.monitor.retryNode,
      zh.monitor.skipNode,
      zh.monitor.resumeInterrupted,
    ]) {
      expect(findButton(wrapper, label)).toBeUndefined();
    }
    expect(wrapper.find('.monitor-empty').text()).toBe(zh.monitor.empty);
  });

  it('running: shows 暂停/停止 wired to pause/stop with the run id', async () => {
    runMocks.runState.value = makeRunState();
    const wrapper = mountRun();
    expect(findButton(wrapper, zh.monitor.start)).toBeUndefined();
    expect(wrapper.find('.monitor-status-chip').text()).toBe(zh.monitor.status.running);

    await findButton(wrapper, zh.monitor.pause)!.trigger('click');
    expect(runMocks.pause).toHaveBeenCalledWith('run-1');
    await findButton(wrapper, zh.monitor.stop)!.trigger('click');
    expect(runMocks.stop).toHaveBeenCalledWith('run-1');
  });

  it('paused with pausedNodeId: 继续/重试/跳过 wired, 停止 kept', async () => {
    runMocks.runState.value = makeRunState({ status: 'paused', pausedNodeId: 'n2' });
    const wrapper = mountRun();

    await findButton(wrapper, zh.monitor.resume)!.trigger('click');
    expect(runMocks.resumeRun).toHaveBeenCalledWith('run-1');
    await findButton(wrapper, zh.monitor.retryNode)!.trigger('click');
    expect(runMocks.retryNode).toHaveBeenCalledWith('n2');
    await findButton(wrapper, zh.monitor.skipNode)!.trigger('click');
    expect(runMocks.skipNode).toHaveBeenCalledWith('n2');
    expect(findButton(wrapper, zh.monitor.stop)).toBeTruthy();
  });

  it('paused without pausedNodeId hides the node actions', () => {
    runMocks.runState.value = makeRunState({ status: 'paused' });
    const wrapper = mountRun();
    expect(findButton(wrapper, zh.monitor.resume)).toBeTruthy();
    expect(findButton(wrapper, zh.monitor.retryNode)).toBeUndefined();
    expect(findButton(wrapper, zh.monitor.skipNode)).toBeUndefined();
  });

  it('interrupted: 恢复运行 wired to resumeRun', async () => {
    runMocks.runState.value = makeRunState({ status: 'interrupted' });
    const wrapper = mountRun();
    expect(findButton(wrapper, zh.monitor.resumeInterrupted)).toBeTruthy();
    expect(findButton(wrapper, zh.monitor.start)).toBeUndefined();

    await findButton(wrapper, zh.monitor.resumeInterrupted)!.trigger('click');
    expect(runMocks.resumeRun).toHaveBeenCalledWith('run-1');
  });

  it('completed: offers 开始运行 again', () => {
    runMocks.runState.value = makeRunState({ status: 'completed' });
    const wrapper = mountRun();
    expect(findButton(wrapper, zh.monitor.start)).toBeTruthy();
    expect(findButton(wrapper, zh.monitor.pause)).toBeUndefined();
  });

  it('shows the busy hint when a member session is busy', () => {
    runMocks.runState.value = makeRunState({ busySessionIds: new Set(['s1']) });
    const wrapper = mountRun();
    expect(wrapper.find('.monitor-busy-hint').text()).toBe(zh.monitor.busy);
  });
});

describe('RunMonitor start payload', () => {
  it('start is disabled without a workflow and carries the selected workflow_id with one', async () => {
    // No "none" option: the backend rejects an empty workflow_id for dag
    // runs, so the select defaults to the first workflow and 开始运行 is
    // gated on a selection.
    teamsMocks.workflows.value = [];
    const wrapper = mountRun();
    await wrapper.find('textarea.ta-stub').setValue(' 修复登录 bug ');

    const start = findButton(wrapper, zh.monitor.start)!;
    expect(start.attributes('disabled')).toBeDefined();
    await start.trigger('click');
    await flushPromises();
    // Defense in depth: onStart re-checks the workflow guard.
    expect(runMocks.startRun).not.toHaveBeenCalled();

    // The team's workflow list arriving re-defaults the select and enables
    // the start; the payload then carries that workflow_id.
    teamsMocks.workflows.value = [fixtures.workflow];
    await flushPromises();
    const enabled = findButton(wrapper, zh.monitor.start)!;
    expect(enabled.attributes('disabled')).toBeUndefined();
    await enabled.trigger('click');
    await flushPromises();

    expect(runMocks.startRun).toHaveBeenCalledWith('t1', {
      mode: 'dag',
      input: '修复登录 bug',
      workflow_id: 'wf1',
    });
  });

  it('forwards the selected workflow id', async () => {
    const wrapper = mountRun();
    await wrapper.find('textarea.ta-stub').setValue('目标');
    await wrapper.find('select.monitor-workflow').setValue('wf1');
    await findButton(wrapper, zh.monitor.start)!.trigger('click');
    await flushPromises();

    expect(runMocks.startRun).toHaveBeenCalledWith('t1', {
      mode: 'dag',
      input: '目标',
      workflow_id: 'wf1',
    });
  });

  it('mode select offers dag and auto both enabled without a hint', () => {
    const wrapper = mountRun();
    const modeSelect = wrapper.find('select.monitor-mode');
    expect(modeSelect.find('option[value="dag"]').attributes('disabled')).toBeUndefined();
    expect(modeSelect.find('option[value="auto"]').attributes('disabled')).toBeUndefined();

    // The auto-mode hint is gone, so the select suppresses the details block
    // like its sibling selects (no dead hint markup left behind).
    const modeSelectComponent = wrapper.findComponent('.monitor-mode') as VueWrapper<any>;
    expect(modeSelectComponent.props('hint')).toBeFalsy();
    expect(modeSelectComponent.props('hideDetails')).toBeTruthy();
  });

  it('auto mode starts without a workflow and sends workflow_id: null', async () => {
    const wrapper = mountRun();
    await wrapper.find('textarea.ta-stub').setValue('自动编排目标');
    await wrapper.find('select.monitor-mode').setValue('auto');
    await nextTick();

    // The workflow select is hidden entirely in auto mode and the start
    // button is enabled even though a workflow pick is not needed.
    expect(wrapper.find('select.monitor-workflow').exists()).toBe(false);
    const start = findButton(wrapper, zh.monitor.start)!;
    expect(start.attributes('disabled')).toBeUndefined();
    await start.trigger('click');
    await flushPromises();

    expect(runMocks.startRun).toHaveBeenCalledWith('t1', {
      mode: 'auto',
      input: '自动编排目标',
      workflow_id: null,
    });
  });
});

describe('RunMonitor member grid', () => {
  it('renders one AgentWindow per member and forwards window/busy state', () => {
    const win = { memberId: 'm1', sent: '任务A', streamText: '', parts: [], streaming: false };
    runMocks.runState.value = makeRunState({
      windows: { m1: win },
      busySessionIds: new Set(['s1']),
    });
    const wrapper = mountRun();

    const agents = wrapper.findAllComponents({ name: 'AgentWindow' });
    expect(agents).toHaveLength(2);
    // The deep-ref state hands the window down as a reactive proxy of win.
    expect(agents[0].props('window')).toEqual(win);
    expect(agents[0].props('busy')).toBe(true);
    expect(agents[1].props('window')).toBeNull();
    expect(agents[1].props('busy')).toBe(false);
  });

  it('packs the default layout as sqrt columns (n=2 and n=5)', () => {
    const wrapper = mountRun();
    let items = wrapper.findAll('.grid-item-stub');
    expect(items.map((i) => i.attributes('data-i'))).toEqual(['m1', 'm2']);
    expect(items[0].attributes()).toMatchObject({
      'data-x': '0',
      'data-y': '0',
      'data-w': '6',
      'data-h': '6',
    });
    expect(items[1].attributes()).toMatchObject({
      'data-x': '6',
      'data-y': '0',
      'data-w': '6',
      'data-h': '6',
    });
    wrapper.unmount();

    const five = { ...fixtures.team, members: fixtures.fiveMembers };
    const wrapper5 = mountRun({ team: five });
    items = wrapper5.findAll('.grid-item-stub');
    expect(items).toHaveLength(5);
    expect(items[0].attributes()).toMatchObject({ 'data-x': '0', 'data-y': '0', 'data-w': '4' });
    expect(items[2].attributes()).toMatchObject({ 'data-x': '8', 'data-y': '0', 'data-w': '4' });
    expect(items[3].attributes()).toMatchObject({ 'data-x': '0', 'data-y': '6', 'data-w': '4' });
    expect(items[4].attributes()).toMatchObject({ 'data-x': '4', 'data-y': '6', 'data-w': '4' });
  });

  it('persists grid edits to localStorage and restores them on remount', async () => {
    const wrapper = mountRun();
    const nextLayout = [
      { i: 'm1', x: 2, y: 3, w: 6, h: 6 },
      { i: 'm2', x: 0, y: 9, w: 6, h: 6 },
    ];
    wrapper.findComponent({ name: 'GridLayoutStub' }).vm.$emit('update:layout', nextLayout);
    await nextTick();
    expect(JSON.parse(localStorage.getItem('agent-teams-grid-t1')!)).toEqual(nextLayout);
    wrapper.unmount();

    const remounted = mountRun();
    const items = remounted.findAll('.grid-item-stub');
    expect(items[0].attributes()).toMatchObject({ 'data-x': '2', 'data-y': '3' });
    expect(items[1].attributes()).toMatchObject({ 'data-x': '0', 'data-y': '9' });
  });
});

describe('RunMonitor DAG view', () => {
  it('swaps the monitor canvas in with node states and the progress strip', async () => {
    runMocks.runState.value = makeRunState({
      progress: { done: 1, running: 1, pending: 1, skipped: 1, failed: 0, total: 4 },
    });
    const wrapper = mountRun();
    expect(wrapper.find('.flow-canvas-stub').exists()).toBe(false);

    await wrapper.findComponent({ name: 'VBtnToggleStub' }).vm.$emit('update:modelValue', 'dag');
    await nextTick();

    const canvas = wrapper.findComponent({ name: 'TeamsFlowCanvasStub' });
    expect(canvas.exists()).toBe(true);
    expect(canvas.props('mode')).toBe('monitor');
    expect(canvas.props('nodeStates')).toBe(runMocks.runState.value.nodeStates);

    // done(1) + skipped(1) of total(4) -> 50%.
    expect(wrapper.find('.progress-stub').attributes('data-value')).toBe('50');
    expect(wrapper.find('.monitor-dag-label').text()).toContain(zh.monitor.dagProgress);
    expect(wrapper.find('.monitor-dag-label').text()).toContain('2 / 4');
  });

  it('recovers the team run on mount and seeds the DAG view from the row graph', async () => {
    runMocks.loadActiveRuns.mockResolvedValue([fixtures.runRow]);
    runMocks.runState.value = makeRunState({
      runId: 'run-9',
      status: 'interrupted',
      nodeStates: { n1: { status: 'done' }, n2: { status: 'pending' } },
    });
    const wrapper = mountRun();
    await flushPromises();

    // The snake_case run row doubles as the reducer seed.
    expect(runMocks.openRun).toHaveBeenCalledWith('run-9', fixtures.runRow);

    await wrapper.findComponent({ name: 'VBtnToggleStub' }).vm.$emit('update:modelValue', 'dag');
    await nextTick();
    const canvas = wrapper.findComponent({ name: 'TeamsFlowCanvasStub' });
    expect(canvas.props('nodes').map((n: any) => n.id)).toEqual(['n1', 'n2']);
    expect(canvas.props('nodes')[0].data.label).toBe('Alice (n1)');
    expect(canvas.props('edges')).toEqual([{ id: 'e:n1->n2', source: 'n1', target: 'n2' }]);
  });

  it('does not open a run belonging to another team', async () => {
    runMocks.loadActiveRuns.mockResolvedValue([{ ...fixtures.runRow, team_id: 'other' }]);
    mountRun();
    await flushPromises();
    expect(runMocks.openRun).not.toHaveBeenCalled();
  });

  it('closes a stale run from another team when this team has no active run', async () => {
    // The composable is a module singleton: a run attached while another team
    // was monitored must not leak into this monitor (stale status chip,
    // 暂停/停止 targeting the wrong run, 开始运行 blocked for this team).
    runMocks.runState.value = makeRunState({ runId: 'run-old', status: 'running' });
    // Mirror the real closeRun: it nulls the singleton state.
    runMocks.closeRun.mockImplementation(() => {
      runMocks.runState.value = null;
    });

    const wrapper = mountRun({ team: { ...fixtures.team, team_id: 't2', name: 'Beta' } });
    await flushPromises();
    await nextTick();

    expect(runMocks.closeRun).toHaveBeenCalled();
    expect(runMocks.runState.value).toBeNull();
    expect(wrapper.find('.monitor-empty').text()).toBe(zh.monitor.empty);
    // The new team can start its own run again.
    expect(findButton(wrapper, zh.monitor.start)).toBeTruthy();
  });
});

describe('RunMonitor auto mode display', () => {
  it('hides the view toggle and DAG canvas for an attached auto run and shows the round chip', () => {
    runMocks.runState.value = makeRunState({ mode: 'auto', round: { n: 2, max: 0 } });
    const wrapper = mountRun();

    // The 窗口/DAG toggle and canvas are DAG-only; the window grid stays.
    expect(wrapper.find('.monitor-viewbar').exists()).toBe(false);
    expect(findButton(wrapper, zh.monitor.viewDag)).toBeUndefined();
    expect(wrapper.find('.flow-canvas-stub').exists()).toBe(false);
    expect(wrapper.findAll('.grid-item-stub')).toHaveLength(2);

    // Round chip: n from the folded round event, max falls back to 20 when
    // the team config carries no max_rounds.
    const chip = wrapper.find('.monitor-round-chip');
    expect(chip.exists()).toBe(true);
    expect(chip.text()).toBe(
      zh.monitor.roundLabel.replace('{n}', '2').replace('{max}', '20'),
    );
  });

  it('renders the last 5 dispatches as a compact log with tasks truncated at 40 chars', async () => {
    const dispatches = [1, 2, 3, 4, 5, 6, 7].map((round) => ({
      round,
      assignments: [{ member: 'm1', task: '任务'.repeat(25) }],
    }));
    runMocks.runState.value = makeRunState({
      mode: 'auto',
      round: { n: 7, max: 0 },
      dispatches,
    });
    const wrapper = mountRun({
      team: { ...fixtures.team, config: { max_rounds: 7 } },
    });
    await flushPromises();

    // Max comes from the team config when present.
    expect(wrapper.find('.monitor-round-chip').text()).toBe(
      zh.monitor.roundLabel.replace('{n}', '7').replace('{max}', '7'),
    );

    const log = wrapper.find('.monitor-dispatch-log');
    expect(log.exists()).toBe(true);
    expect(log.text()).toContain(zh.monitor.dispatchLog);
    const lines = log.findAll('.monitor-dispatch-line');
    // Only the last 5 dispatches render, in order.
    expect(lines).toHaveLength(5);
    expect(lines[0].text()).toContain('#3');
    expect(lines[4].text()).toContain('#7');
    // Task text is truncated to 40 chars with an ellipsis.
    expect(lines[0].text()).toBe(`#3 m1: ${'任务'.repeat(20)}…`);
  });

  it('hides the dispatch log block while no dispatch has arrived', () => {
    runMocks.runState.value = makeRunState({ mode: 'auto' });
    const wrapper = mountRun();
    expect(wrapper.find('.monitor-round-chip').exists()).toBe(true);
    expect(wrapper.find('.monitor-dispatch-log').exists()).toBe(false);
  });
});

describe('RunMonitor history handoff', () => {
  it('keeps a run explicitly opened from history and skips mount recovery', async () => {
    // The page called openRun(run-9, row) before mounting this panel and
    // passes the history row down as initialRun.
    runMocks.runState.value = makeRunState({ runId: 'run-9', status: 'completed' });
    const wrapper = mountRun({ initialRun: fixtures.runRow });
    await flushPromises();

    // Recovery must not clobber the explicitly opened run...
    expect(runMocks.loadActiveRuns).not.toHaveBeenCalled();
    expect(runMocks.openRun).not.toHaveBeenCalled();

    // ...and the DAG is seeded from the handed-off row's graph snapshot.
    await wrapper.findComponent({ name: 'VBtnToggleStub' }).vm.$emit('update:modelValue', 'dag');
    await nextTick();
    const canvas = wrapper.findComponent({ name: 'TeamsFlowCanvasStub' });
    expect(canvas.props('nodes').map((n: any) => n.id)).toEqual(['n1', 'n2']);
    expect(canvas.props('edges')).toEqual([{ id: 'e:n1->n2', source: 'n1', target: 'n2' }]);
  });

  it('falls back to mount recovery when the attached run does not match initialRun', async () => {
    runMocks.runState.value = makeRunState({ runId: 'run-other' });
    runMocks.loadActiveRuns.mockResolvedValue([fixtures.runRow]);
    mountRun({ initialRun: fixtures.runRow });
    await flushPromises();

    expect(runMocks.openRun).toHaveBeenCalledWith('run-9', fixtures.runRow);
  });
});

describe('AgentWindow rendering (through the grid)', () => {
  it('renders the sent block and falls back to streamText with is-streaming', () => {
    runMocks.runState.value = makeRunState({
      windows: {
        m1: { memberId: 'm1', sent: '任务A', streamText: '部分回答', parts: [], streaming: true },
      },
    });
    const wrapper = mountRun();
    expect(wrapper.find('.agent-window-sent').text()).toContain('任务A');

    const md = wrapper.findComponent({ name: 'MdPartStub' });
    expect(md.props('content')).toBe('部分回答');
    expect(md.props('isStreaming')).toBe(true);
  });

  it('renders structured reply parts: thinking block + plain markdown', () => {
    runMocks.runState.value = makeRunState({
      windows: {
        m1: {
          memberId: 'm1',
          sent: null,
          streamText: '',
          parts: [
            { type: 'think', think: '思考过程' },
            { type: 'plain', text: '最终回答' },
          ],
          streaming: false,
        },
      },
    });
    const wrapper = mountRun();
    expect(wrapper.findComponent({ name: 'ReasoningStub' }).exists()).toBe(true);

    const parts = wrapper.findAllComponents({ name: 'MdPartStub' });
    expect(parts).toHaveLength(1);
    expect(parts[0].props('content')).toBe('最终回答');
    expect(parts[0].props('isStreaming')).toBe(false);
  });

  it('shows the per-window empty hint when the member has no content', () => {
    runMocks.runState.value = makeRunState();
    const wrapper = mountRun();
    expect(wrapper.find('.agent-window-empty').text()).toBe(zh.monitor.empty);
  });

  it('shows per-member node status chips', async () => {
    runMocks.loadActiveRuns.mockResolvedValue([fixtures.runRow]);
    runMocks.runState.value = makeRunState({
      runId: 'run-9',
      nodeStates: { n1: { status: 'done' }, n2: { status: 'running' } },
    });
    const wrapper = mountRun();
    await flushPromises();

    const chips = wrapper.findAll('.agent-window-node-chip');
    expect(chips).toHaveLength(2);
    expect(chips[0].text()).toBe(zh.monitor.node.done);
    expect(chips[1].text()).toBe(zh.monitor.node.running);
  });
});
