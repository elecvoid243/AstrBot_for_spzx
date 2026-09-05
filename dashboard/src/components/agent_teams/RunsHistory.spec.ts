// Specs for the agent-teams runs history panel (Task 9): loads the team's run
// rows via agentTeamsApi.listTeamRuns on mount and on team change, renders
// time / mode chip / truncated input / colored status chip per row, shows an
// empty state, toasts error envelopes, and emits `open` with the run id plus
// the full row (the page forwards both to useAgentTeamsRun().openRun).
// Vuetify is stubbed manually (the app registers it globally; tests do not
// pull it in).
import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import zh from '@/i18n/locales/zh-CN/features/agent-teams.json';

const apiMocks = vi.hoisted(() => ({ listTeamRuns: vi.fn() }));

const toastMocks = vi.hoisted(() => ({ error: vi.fn() }));

vi.mock('@/api/v1', () => ({
  agentTeamsApi: { listTeamRuns: apiMocks.listTeamRuns },
}));

vi.mock('@/utils/toast', () => ({ useToast: () => toastMocks }));

import RunsHistory from './RunsHistory.vue';

const TEAM = { team_id: 't1', name: 'Alpha' };

const ROWS = [
  {
    run_id: 'r1',
    team_id: 't1',
    workflow_id: 'wf1',
    mode: 'dag',
    input: '修复登录 bug',
    status: 'completed',
    result_summary: 'done',
    graph: { nodes: [], edges: [] },
    node_states: {},
    rounds: 3,
    created_at: '2026-09-01T10:00:00',
    updated_at: '2026-09-01T10:05:00',
  },
  {
    run_id: 'r2',
    team_id: 't1',
    workflow_id: null,
    mode: 'auto',
    input: 'x'.repeat(120),
    status: 'failed',
    result_summary: null,
    graph: null,
    node_states: {},
    rounds: 1,
    created_at: '2026-09-02T12:00:00',
    updated_at: '2026-09-02T12:30:00',
  },
];

function okEnvelope(runs: unknown[]) {
  return { data: { status: 'ok', data: { runs } } };
}

const stubs = {
  'v-card': {
    props: { variant: { type: String, default: undefined } },
    template: '<div class="card-stub"><slot /></div>',
  },
  'v-chip': {
    props: { color: { type: String, default: undefined } },
    template: '<span class="chip-stub" :data-color="color"><slot /></span>',
  },
  'v-btn': {
    props: { disabled: { type: Boolean, default: false } },
    emits: ['click'],
    template:
      '<button type="button" class="v-btn-stub" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
  },
  'v-progress-circular': {
    props: { indeterminate: { type: Boolean, default: false } },
    template: '<div class="progress-circular-stub" />',
  },
};

function mountHistory(props: Record<string, unknown> = {}) {
  return mount(RunsHistory, {
    props: { team: TEAM, ...props },
    global: { stubs },
  });
}

describe('RunsHistory', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads the team runs on mount and renders time, mode, input and status', async () => {
    apiMocks.listTeamRuns.mockResolvedValue(okEnvelope(ROWS));
    const wrapper = mountHistory();
    await flushPromises();

    expect(apiMocks.listTeamRuns).toHaveBeenCalledWith('t1');
    expect(wrapper.find('.history-head').text()).toBe(zh.history.title);
    expect(wrapper.find('.history-empty').exists()).toBe(false);

    const rows = wrapper.findAll('.history-row');
    expect(rows).toHaveLength(2);

    // Locale-aware formatting of updated_at (ProjectView convention).
    expect(rows[0].text()).toContain(new Date(ROWS[0].updated_at).toLocaleString());
    expect(rows[0].text()).toContain('dag');
    expect(rows[0].text()).toContain('修复登录 bug');
    expect(rows[0].text()).toContain(zh.monitor.status.completed);

    const chips = rows[0].findAll('.chip-stub');
    expect(chips).toHaveLength(2);
    // Status chip color mapping: completed -> green (success).
    expect(chips[1].attributes('data-color')).toBe('success');
  });

  it('maps the other run statuses to chip colors', async () => {
    apiMocks.listTeamRuns.mockResolvedValue(
      okEnvelope([
        { run_id: 'r-running', mode: 'dag', input: 'a', status: 'running', updated_at: '2026-09-01T10:00:00' },
        { run_id: 'r-paused', mode: 'dag', input: 'b', status: 'paused', updated_at: '2026-09-01T10:00:00' },
        { run_id: 'r-stopped', mode: 'dag', input: 'c', status: 'stopped', updated_at: '2026-09-01T10:00:00' },
        { run_id: 'r-interrupted', mode: 'dag', input: 'd', status: 'interrupted', updated_at: '2026-09-01T10:00:00' },
        { run_id: 'r-unknown', mode: 'dag', input: 'e', status: 'mystery', updated_at: '2026-09-01T10:00:00' },
      ]),
    );
    const wrapper = mountHistory();
    await flushPromises();

    const rows = wrapper.findAll('.history-row');
    expect(rows[0].findAll('.chip-stub')[1].attributes('data-color')).toBe('primary');
    expect(rows[1].findAll('.chip-stub')[1].attributes('data-color')).toBe('warning');
    expect(rows[2].findAll('.chip-stub')[1].attributes('data-color')).toBe('grey');
    expect(rows[3].findAll('.chip-stub')[1].attributes('data-color')).toBe('warning');
    // Unknown status: default chip, raw status as label.
    expect(rows[4].findAll('.chip-stub')[1].attributes('data-color')).toBeUndefined();
    expect(rows[4].findAll('.chip-stub')[1].text()).toBe('mystery');
  });

  it('truncates long input to about 80 characters', async () => {
    apiMocks.listTeamRuns.mockResolvedValue(okEnvelope([ROWS[1]]));
    const wrapper = mountHistory();
    await flushPromises();

    const row = wrapper.find('.history-row');
    expect(row.find('.history-input').text()).toBe(`${'x'.repeat(80)}…`);
    // The full text stays available as a tooltip.
    expect(row.find('.history-input').attributes('title')).toBe('x'.repeat(120));
  });

  it('shows the empty state when the team has no runs', async () => {
    apiMocks.listTeamRuns.mockResolvedValue(okEnvelope([]));
    const wrapper = mountHistory();
    await flushPromises();

    expect(wrapper.find('.history-empty').text()).toBe(zh.history.empty);
    expect(wrapper.find('.history-row').exists()).toBe(false);
  });

  it('shows a loading indicator until the rows arrive', async () => {
    let resolveApi!: (envelope: unknown) => void;
    apiMocks.listTeamRuns.mockReturnValue(
      new Promise((resolve) => {
        resolveApi = resolve;
      }),
    );
    const wrapper = mountHistory();
    await flushPromises();
    expect(wrapper.find('.history-loading').exists()).toBe(true);

    resolveApi(okEnvelope([]));
    await flushPromises();
    expect(wrapper.find('.history-loading').exists()).toBe(false);
  });

  it('toasts an error envelope and falls back to the empty state', async () => {
    apiMocks.listTeamRuns.mockResolvedValue({ data: { status: 'error', message: 'boom' } });
    const wrapper = mountHistory();
    await flushPromises();

    expect(toastMocks.error).toHaveBeenCalledWith('boom');
    expect(wrapper.find('.history-empty').exists()).toBe(true);
  });

  it('toasts network failures too', async () => {
    apiMocks.listTeamRuns.mockRejectedValue(new Error('network down'));
    mountHistory();
    await flushPromises();

    expect(toastMocks.error).toHaveBeenCalledWith('network down');
  });

  it('emits open with the run id and the full row', async () => {
    apiMocks.listTeamRuns.mockResolvedValue(okEnvelope(ROWS));
    const wrapper = mountHistory();
    await flushPromises();

    const buttons = wrapper.findAll('.history-row button');
    expect(buttons[0].text()).toBe(zh.history.open);
    await buttons[0].trigger('click');

    expect(wrapper.emitted('open')).toEqual([['r1', ROWS[0]]]);
  });

  it('reloads the rows when the selected team changes', async () => {
    apiMocks.listTeamRuns.mockResolvedValue(okEnvelope([]));
    const wrapper = mountHistory();
    await flushPromises();
    apiMocks.listTeamRuns.mockClear();

    await wrapper.setProps({ team: { team_id: 't2', name: 'Beta' } });
    await flushPromises();

    expect(apiMocks.listTeamRuns).toHaveBeenCalledWith('t2');
  });

  it('renders the empty state without a selected team and calls nothing', async () => {
    const wrapper = mountHistory({ team: null });
    await flushPromises();

    expect(apiMocks.listTeamRuns).not.toHaveBeenCalled();
    expect(wrapper.find('.history-empty').text()).toBe(zh.history.empty);
  });
});
