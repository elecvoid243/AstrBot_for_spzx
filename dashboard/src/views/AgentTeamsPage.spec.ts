// Scaffold spec for the agent-teams page shell (Task 5): the page renders
// the team directory through AgentTeamsSidebar, exposes the
// editor/monitor/history tab shell with placeholder panels, and forwards
// selection/refresh to the useAgentTeams composable. The sidebar contract
// (select / create / removeMember emits, coordinator marking) is pinned in
// the second describe block. Vuetify is stubbed manually (the app registers
// it globally; tests do not pull it in).
import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent, h, inject, provide } from 'vue';
import { describe, expect, it, vi } from 'vitest';

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
    template:
      '<button type="button" class="v-btn-stub" :disabled="disabled || loading" @click="$emit(\'click\')"><i v-if="icon" class="mdi" :class="icon" /><slot /></button>',
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
};

function mountPage() {
  return mount(AgentTeamsPage, { global: { stubs } });
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
