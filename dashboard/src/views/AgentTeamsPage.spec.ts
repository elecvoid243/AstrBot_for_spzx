// Scaffold spec for the agent-teams page shell (Task 5, extended by Task 9):
// the page renders the team directory through AgentTeamsSidebar, exposes the
// editor/monitor/history tabs with the real panel components (stubbed here —
// each panel has its own spec) and forwards selection/refresh to the
// useAgentTeams composable. Task 9 covers the dialog wiring (create saved ->
// select new team, member add refresh), the confirm-guarded member removal
// and the history -> monitor handoff (openRun + initialRun + tab switch).
// The sidebar contract (select / create / removeMember emits, coordinator
// marking) is pinned in the second describe block. Vuetify is stubbed
// manually (the app registers it globally; tests do not pull it in).
import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent, h, inject, nextTick, provide } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const composableMocks = vi.hoisted(() => ({
  loadTeams: vi.fn(),
  loadWorkflows: vi.fn(),
  selectTeam: vi.fn(),
  createTeam: vi.fn(),
  updateTeam: vi.fn(),
  deleteTeam: vi.fn(),
  saveWorkflow: vi.fn(),
  deleteWorkflow: vi.fn(),
}));

const runMocks = vi.hoisted(() => ({ openRun: vi.fn() }));

const removeMemberApiMock = vi.hoisted(() => vi.fn());

const toastMocks = vi.hoisted(() => ({ error: vi.fn() }));

vi.mock('@/composables/useAgentTeams', async () => {
  const { computed, ref } = await vi.importActual<typeof import('vue')>('vue');
  const teams = ref([
    {
      team_id: 't1',
      name: 'Alpha 团队',
      members: [
        { member_id: 'm1', name: 'Alice' },
        { member_id: 'm2', name: 'Bob' },
      ],
      coordinator_member_id: 'm1',
    },
    {
      team_id: 't2',
      name: 'Beta 团队',
      members: [{ member_id: 'm3', name: 'Carol' }],
    },
  ]);
  const selectedTeamId = ref<string | null>('t1');
  const selectedTeam = computed(
    () => teams.value.find((team) => team.team_id === selectedTeamId.value) ?? null,
  );
  return {
    useAgentTeams: () => ({
      teams,
      selectedTeamId,
      selectedTeam,
      workflows: ref([]),
      loadTeams: composableMocks.loadTeams,
      loadWorkflows: composableMocks.loadWorkflows,
      selectTeam: composableMocks.selectTeam,
      createTeam: composableMocks.createTeam,
      updateTeam: composableMocks.updateTeam,
      deleteTeam: composableMocks.deleteTeam,
      saveWorkflow: composableMocks.saveWorkflow,
      deleteWorkflow: composableMocks.deleteWorkflow,
    }),
  };
});

vi.mock('@/composables/useAgentTeamsRun', async () => {
  const { ref, shallowRef } = await vi.importActual<typeof import('vue')>('vue');
  return {
    useAgentTeamsRun: () => ({
      runState: shallowRef(null),
      monitors: ref([]),
      loadActiveRuns: vi.fn(),
      openRun: runMocks.openRun,
      closeRun: vi.fn(),
      startRun: vi.fn(),
      pause: vi.fn(),
      resumeRun: vi.fn(),
      stop: vi.fn(),
      retryNode: vi.fn(),
      skipNode: vi.fn(),
      reconnect: vi.fn(),
    }),
  };
});

vi.mock('@/api/v1', () => ({
  agentTeamsApi: { removeMember: removeMemberApiMock },
}));

vi.mock('@/utils/toast', () => ({ useToast: () => toastMocks }));

import AgentTeamsSidebar from '@/components/agent_teams/AgentTeamsSidebar.vue';
import AgentTeamsPage from './AgentTeamsPage.vue';

const TEAMS = [
  {
    team_id: 't1',
    name: 'Alpha 团队',
    members: [
      { member_id: 'm1', name: 'Alice' },
      { member_id: 'm2', name: 'Bob' },
    ],
    coordinator_member_id: 'm1',
  },
  {
    team_id: 't2',
    name: 'Beta 团队',
    members: [{ member_id: 'm3', name: 'Carol' }],
  },
];

// The v-tab stub reaches its parent v-tabs stub through this key (mirroring
// Vuetify's provide/inject wiring) so clicking a tab emits update:modelValue.
const TABS_KEY = Symbol('tabs-stub');

const stubs = {
  'v-container': { template: '<div class="container-stub"><slot /></div>' },
  'v-btn': {
    props: {
      icon: { type: String, default: undefined },
      disabled: { type: Boolean, default: false },
      loading: { type: Boolean, default: false },
    },
    emits: ['click'],
    // The native event is re-emitted (Vuetify does the same) so handlers can
    // use modifiers such as .stop.
    template:
      '<button type="button" class="v-btn-stub" :disabled="disabled || loading" @click="$emit(\'click\', $event)"><i v-if="icon" class="mdi" :class="icon" /><slot /></button>',
  },
  'v-chip': { template: '<span class="chip-stub"><slot /></span>' },
  'v-tabs': defineComponent({
    props: { modelValue: { type: String, default: '' } },
    emits: ['update:modelValue'],
    setup(props, { slots, emit }) {
      provide(TABS_KEY, (value: string) => emit('update:modelValue', value));
      return () => h('div', { class: 'tabs-stub' }, slots.default?.());
    },
  }),
  'v-tab': defineComponent({
    props: { value: { type: String, default: '' } },
    setup(props, { slots }) {
      const select = inject<((value: string) => void) | null>(TABS_KEY, null);
      return () =>
        h(
          'button',
          { type: 'button', class: 'tab-stub', onClick: () => select?.(props.value) },
          slots.default?.(),
        );
    },
  }),
  'v-window': {
    props: { modelValue: { type: String, default: '' } },
    template: '<div class="window-stub"><slot /></div>',
  },
  'v-window-item': {
    props: { value: { type: String, default: '' } },
    template: '<div class="window-item-stub"><slot /></div>',
  },
  // Panel components are stubbed at page level: each has its own spec, and
  // the page specs only assert the wiring around them.
  WorkflowEditor: {
    name: 'WorkflowEditorStub',
    props: ['team', 'workflows'],
    template: '<div class="workflow-editor-stub" />',
  },
  RunMonitor: {
    name: 'RunMonitorStub',
    props: ['team', 'initialRun'],
    template: '<div class="run-monitor-stub" />',
  },
  RunsHistory: {
    name: 'RunsHistoryStub',
    props: ['team'],
    emits: ['open'],
    template: '<div class="runs-history-stub" />',
  },
  TeamCreateDialog: {
    name: 'TeamCreateDialogStub',
    props: ['modelValue', 'team'],
    emits: ['update:modelValue', 'saved'],
    template: '<div class="team-create-stub" />',
  },
  MemberAddDialog: {
    name: 'MemberAddDialogStub',
    props: ['modelValue', 'teamId'],
    emits: ['update:modelValue', 'saved'],
    template: '<div class="member-add-stub" />',
  },
};

function mountPage(provide: Record<string, unknown> = {}) {
  return mount(AgentTeamsPage, { global: { stubs, provide } });
}

function mountSidebar(props: Record<string, unknown> = {}) {
  return mount(AgentTeamsSidebar, {
    props: {
      teams: TEAMS,
      selectedTeamId: 't1',
      selectedTeam: TEAMS[0],
      ...props,
    },
    global: { stubs },
  });
}

describe('AgentTeamsPage', () => {
  it('renders the header title, beta chip and both team names', () => {
    const wrapper = mountPage();
    expect(wrapper.find('.dashboard-title').text()).toBe('Agent 团队');
    expect(wrapper.find('.chip-stub').text()).toBe('Beta');
    expect(wrapper.text()).toContain('Alpha 团队');
    expect(wrapper.text()).toContain('Beta 团队');
  });

  it('renders the three tab labels', () => {
    const wrapper = mountPage();
    const labels = wrapper.findAll('.tab-stub').map((tab) => tab.text());
    expect(labels).toEqual(['工作流编排', '运行监控', '历史']);
  });

  it('shows the editor panel by default and swaps panels when a tab is clicked', async () => {
    const wrapper = mountPage();
    expect(wrapper.find('.agent-teams-panel-editor').exists()).toBe(true);
    expect(wrapper.find('.agent-teams-panel-monitor').exists()).toBe(false);

    const monitorTab = wrapper
      .findAll('.tab-stub')
      .find((tab) => tab.text() === '运行监控');
    expect(monitorTab).toBeTruthy();
    await monitorTab!.trigger('click');

    expect(wrapper.find('.agent-teams-panel-monitor').exists()).toBe(true);
    expect(wrapper.find('.agent-teams-panel-editor').exists()).toBe(false);
  });

  it('clicking a team row forwards select to the composable', async () => {
    const wrapper = mountPage();
    const sidebar = wrapper.findComponent(AgentTeamsSidebar);
    const rows = wrapper.findAll('.team-row');

    await rows[1].trigger('click');

    expect(sidebar.emitted('select')).toEqual([['t2']]);
    expect(composableMocks.selectTeam).toHaveBeenCalledWith('t2');
  });

  it('refresh reloads teams and the selected team workflows', async () => {
    const wrapper = mountPage();
    await flushPromises(); // let the onMounted refresh settle

    composableMocks.loadTeams.mockClear();
    composableMocks.loadWorkflows.mockClear();

    const refreshBtn = wrapper
      .findAll('button.v-btn-stub')
      .find((btn) => btn.text() === '刷新');
    expect(refreshBtn).toBeTruthy();
    await refreshBtn!.trigger('click');
    await flushPromises();

    expect(composableMocks.loadTeams).toHaveBeenCalledTimes(1);
    expect(composableMocks.loadWorkflows).toHaveBeenCalledWith('t1');
  });
});

describe('AgentTeamsPage integration (Task 9)', () => {
  // Call history of the shared API/run mocks leaks between tests otherwise.
  beforeEach(() => {
    removeMemberApiMock.mockClear();
    runMocks.openRun.mockClear();
  });

  it('sidebar create opens TeamCreateDialog; saved refreshes and selects the new team', async () => {
    const wrapper = mountPage();
    await flushPromises();
    composableMocks.loadTeams.mockClear();
    composableMocks.selectTeam.mockClear();

    const createBtn = wrapper
      .findAll('button')
      .find((btn) => btn.text() === '新建团队');
    await createBtn!.trigger('click');

    const dialog = wrapper.findComponent({ name: 'TeamCreateDialogStub' });
    expect(dialog.props('modelValue')).toBe(true);

    dialog.vm.$emit('saved', { team_id: 't9', name: 'New 团队' });
    await flushPromises();

    expect(composableMocks.loadTeams).toHaveBeenCalledTimes(1);
    expect(composableMocks.selectTeam).toHaveBeenCalledWith('t9');
  });

  it('sidebar edit opens TeamCreateDialog in edit mode; saved refreshes and selects', async () => {
    const wrapper = mountPage();
    await flushPromises();
    composableMocks.loadTeams.mockClear();
    composableMocks.selectTeam.mockClear();

    const editBtn = wrapper.find('.team-row.is-selected .team-edit');
    expect(editBtn.exists()).toBe(true);
    await editBtn.trigger('click');

    const sidebar = wrapper.findComponent(AgentTeamsSidebar);
    expect(sidebar.emitted('edit')).toHaveLength(1);
    // .stop: the edit click must not double as a row selection.
    expect(sidebar.emitted('select')).toBeUndefined();
    expect(composableMocks.selectTeam).not.toHaveBeenCalled();

    const dialog = wrapper.findComponent({ name: 'TeamCreateDialogStub' });
    expect(dialog.props('modelValue')).toBe(true);
    // Edit mode: the dialog receives the selected team.
    expect(dialog.props('team')).toEqual(TEAMS[0]);

    dialog.vm.$emit('saved', { team_id: 't1', name: 'Alpha 团队' });
    await flushPromises();

    expect(composableMocks.loadTeams).toHaveBeenCalledTimes(1);
    expect(composableMocks.selectTeam).toHaveBeenCalledWith('t1');
  });

  it('sidebar add-member opens MemberAddDialog for the selected team; saved refreshes', async () => {
    const wrapper = mountPage();
    await flushPromises();
    composableMocks.loadTeams.mockClear();

    const addBtn = wrapper.find('button[aria-label="添加成员"]');
    expect(addBtn.exists()).toBe(true);
    await addBtn.trigger('click');

    const dialog = wrapper.findComponent({ name: 'MemberAddDialogStub' });
    expect(dialog.props('modelValue')).toBe(true);
    expect(dialog.props('teamId')).toBe('t1');

    dialog.vm.$emit('saved', { member_id: 'm9' });
    await flushPromises();

    expect(composableMocks.loadTeams).toHaveBeenCalledTimes(1);
  });

  it('removeMember confirms, removes through the API and refreshes the list', async () => {
    removeMemberApiMock.mockResolvedValue({ data: { status: 'ok' } });
    const confirmMock = vi.fn().mockResolvedValue(true);
    const wrapper = mountPage({ $confirm: confirmMock });
    await flushPromises();
    composableMocks.loadTeams.mockClear();

    const sidebar = wrapper.findComponent(AgentTeamsSidebar);
    sidebar.vm.$emit('removeMember', 'm2');
    await flushPromises();

    expect(confirmMock).toHaveBeenCalledTimes(1);
    // The confirmation message names the member being removed
    // (askForConfirmation passes { message } to the dialog handler).
    expect(confirmMock.mock.calls[0][0].message).toContain('Bob');
    expect(removeMemberApiMock).toHaveBeenCalledWith('t1', 'm2');
    expect(composableMocks.loadTeams).toHaveBeenCalledTimes(1);
  });

  it('removeMember cancelled leaves the team untouched', async () => {
    const confirmMock = vi.fn().mockResolvedValue(false);
    const wrapper = mountPage({ $confirm: confirmMock });
    await flushPromises();

    const sidebar = wrapper.findComponent(AgentTeamsSidebar);
    sidebar.vm.$emit('removeMember', 'm2');
    await flushPromises();

    expect(confirmMock).toHaveBeenCalledTimes(1);
    expect(removeMemberApiMock).not.toHaveBeenCalled();
  });

  it('removeMember toasts the backend reason when the API rejects (HTTP 400)', async () => {
    // Non-2xx responses arrive as axios rejections carrying the error envelope
    // body; its message must reach the toast, not axios's generic text.
    removeMemberApiMock.mockRejectedValue({
      message: 'Request failed with status code 400',
      response: {
        status: 400,
        data: { status: 'error', message: '后端具体原因' },
      },
    });
    const confirmMock = vi.fn().mockResolvedValue(true);
    const wrapper = mountPage({ $confirm: confirmMock });
    await flushPromises();
    composableMocks.loadTeams.mockClear();

    const sidebar = wrapper.findComponent(AgentTeamsSidebar);
    sidebar.vm.$emit('removeMember', 'm2');
    await flushPromises();

    expect(toastMocks.error).toHaveBeenCalledWith('后端具体原因');
    // The failure must not refresh the list (the member was not removed).
    expect(composableMocks.loadTeams).not.toHaveBeenCalled();
  });

  it('history open switches to the monitor tab and opens the run with the row', async () => {
    const wrapper = mountPage();
    await flushPromises();

    const historyTab = wrapper
      .findAll('.tab-stub')
      .find((tab) => tab.text() === '历史');
    await historyTab!.trigger('click');

    const history = wrapper.findComponent({ name: 'RunsHistoryStub' });
    expect(history.exists()).toBe(true);

    const row = { run_id: 'run-9', graph: { nodes: [], edges: [] }, node_states: {} };
    history.vm.$emit('open', 'run-9', row);
    await nextTick();

    expect(runMocks.openRun).toHaveBeenCalledWith('run-9', row);
    expect(wrapper.find('.agent-teams-panel-monitor').exists()).toBe(true);
    // strictEqual: the page stores the row in a ref, so the prop arrives as
    // its reactive proxy.
    expect(wrapper.findComponent({ name: 'RunMonitorStub' }).props('initialRun')).toStrictEqual(
      row,
    );
  });
});

describe('AgentTeamsSidebar', () => {
  it('emits create when the create button is clicked', async () => {
    const wrapper = mountSidebar();
    const createBtn = wrapper
      .findAll('button')
      .find((btn) => btn.text() === '新建团队');
    expect(createBtn).toBeTruthy();
    await createBtn!.trigger('click');
    expect(wrapper.emitted('create')).toHaveLength(1);
  });

  it('renders team rows with names, member counts and the selected highlight', () => {
    const wrapper = mountSidebar();
    const rows = wrapper.findAll('.team-row');
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain('Alpha 团队');
    expect(rows[0].text()).toContain('2');
    expect(rows[1].text()).toContain('Beta 团队');
    expect(rows[1].text()).toContain('1');
    expect(rows[0].classes()).toContain('is-selected');
    expect(rows[1].classes()).not.toContain('is-selected');
  });

  it('emits select with the team id when a row is clicked', async () => {
    const wrapper = mountSidebar();
    const rows = wrapper.findAll('.team-row');
    await rows[1].trigger('click');
    expect(wrapper.emitted('select')).toEqual([['t2']]);
  });

  it('renders the edit pencil only on the selected team row and emits edit without select', async () => {
    const wrapper = mountSidebar();
    const rows = wrapper.findAll('.team-row');
    expect(rows[0].find('.team-edit').exists()).toBe(true);
    expect(rows[1].find('.team-edit').exists()).toBe(false);
    expect(rows[0].find('.team-edit').attributes('aria-label')).toBe('编辑团队');

    await rows[0].find('.team-edit').trigger('click');
    expect(wrapper.emitted('edit')).toHaveLength(1);
    // The pencil must not double as a row selection (.stop).
    expect(wrapper.emitted('select')).toBeUndefined();
  });

  it('marks the coordinator member and emits removeMember from the remove button', async () => {
    const wrapper = mountSidebar();

    const coordinatorRow = wrapper.find('.member-row.is-coordinator');
    expect(coordinatorRow.text()).toContain('Alice');
    expect(coordinatorRow.text()).toContain('协调者');
    expect(coordinatorRow.find('.mdi-crown').exists()).toBe(true);
    expect(wrapper.text()).toContain('Bob');

    const removeButtons = wrapper.findAll('button[aria-label="移除"]');
    expect(removeButtons).toHaveLength(2);
    await removeButtons[1].trigger('click');
    expect(wrapper.emitted('removeMember')).toEqual([['m2']]);
  });

  it('hides the member list when no team is selected', () => {
    const wrapper = mountSidebar({ selectedTeamId: null, selectedTeam: null });
    expect(wrapper.find('.member-row').exists()).toBe(false);
  });

  it('shows the empty hint when there are no teams', () => {
    const wrapper = mountSidebar({
      teams: [],
      selectedTeamId: null,
      selectedTeam: null,
    });
    expect(wrapper.text()).toContain('暂无团队');
  });
});
