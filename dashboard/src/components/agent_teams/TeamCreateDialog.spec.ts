// Specs for the agent-teams TeamCreateDialog and MemberAddDialog (Task 6).
//
// The dialogs are exercised through interactive stubs (real <input>/<select>
// elements) so the emitted save payloads can be asserted end to end. The
// useAgentTeams composable is intentionally NOT mocked: TeamCreateDialog must
// stay double-toast-safe (the composable toasts error envelopes and maps them
// to null; the dialog must not toast again), which is only observable when the
// real composable runs against the mocked @/api/v1 + @/utils/toast layers.
// MemberAddDialog calls agentTeamsApi.addMember directly (Task 5 pattern) and
// handles the envelope itself.
import { flushPromises, mount } from '@vue/test-utils';
import type { DOMWrapper, VueWrapper } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import zh from '@/i18n/locales/zh-CN/features/agent-teams.json';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  createTeam: vi.fn(),
  updateTeam: vi.fn(),
  listTeams: vi.fn(),
  addMember: vi.fn(),
  personaList: vi.fn(),
  providerList: vi.fn(),
}));

vi.mock('@/utils/toast', () => ({
  useToast: () => toastMock,
}));

vi.mock('@/api/v1', () => ({
  agentTeamsApi: {
    createTeam: apiMocks.createTeam,
    updateTeam: apiMocks.updateTeam,
    listTeams: apiMocks.listTeams,
    addMember: apiMocks.addMember,
  },
  personaApi: { list: apiMocks.personaList },
  providerApi: { listByProviderType: apiMocks.providerList },
}));

import MemberAddDialog from './MemberAddDialog.vue';
import TeamCreateDialog from './TeamCreateDialog.vue';

const PERSONAS = [{ persona_id: 'persona-a' }, { persona_id: 'persona-b' }];
const PROVIDERS = [
  { id: 'prov-openai', enable: true },
  { id: 'prov-off', enable: false },
];

const TEAM = {
  team_id: 't1',
  name: 'Alpha',
  members: [
    { member_id: 'm1', name: 'Alice' },
    { member_id: 'm2', name: 'Bob' },
  ],
  coordinator_member_id: 'm2',
  config: { failure_policy: 'auto_skip', reply_timeout: 300 },
};

const stubs = {
  'v-dialog': {
    props: { modelValue: { type: Boolean, default: false } },
    template: '<div v-if="modelValue" class="dialog-stub"><slot /></div>',
  },
  'v-card': { template: '<div><slot /></div>' },
  'v-card-title': { template: '<div class="card-title"><slot /></div>' },
  'v-card-text': { template: '<div><slot /></div>' },
  'v-card-actions': { template: '<div><slot /></div>' },
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
  'v-btn': {
    props: {
      disabled: { type: Boolean, default: false },
      loading: { type: Boolean, default: false },
    },
    emits: ['click'],
    template:
      '<button type="button" :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
  },
  'v-icon': { template: '<i><slot /></i>' },
  'v-spacer': { template: '<div class="v-spacer" />' },
};

function mountDialog(component: any, props: Record<string, unknown>) {
  return mount(component, { props, global: { stubs } }) as VueWrapper<any>;
}

/** Mount the create dialog with modelValue=true and settle option loading. */
async function openCreateDialog(props: Record<string, unknown> = {}) {
  const wrapper = mountDialog(TeamCreateDialog, { modelValue: true, ...props });
  await flushPromises();
  return wrapper;
}

/** Mount the edit dialog with a team payload and settle option loading. */
async function openEditDialog(team: Record<string, unknown>) {
  const wrapper = mountDialog(TeamCreateDialog, { modelValue: true, team });
  await flushPromises();
  return wrapper;
}

function memberRows(wrapper: VueWrapper<any>) {
  return wrapper.findAll('.member-row');
}

function byLabel(wrapper: VueWrapper<any>, label: string) {
  return wrapper.find(`[data-label="${label}"]`);
}

function findButton(wrapper: VueWrapper<any>, text: string) {
  // includes(): buttons may embed icon text (e.g. the config toggle's chevron).
  return wrapper.findAll('button').find((b) => b.text().includes(text));
}

/**
 * Fill one member row: name, optional mode switch to custom, then the
 * persona select / system prompt textarea and the provider select.
 */
async function fillMemberRow(
  row: DOMWrapper<Element>,
  opts: { name: string; mode?: 'persona' | 'custom'; persona?: string; prompt?: string; provider?: string },
) {
  await row.find(`[data-label="${zh.members.name}"]`).setValue(opts.name);
  if (opts.mode === 'custom') {
    const customBtn = row
      .findAll('button')
      .find((b) => b.text() === zh.members.custom);
    await customBtn!.trigger('click');
    await row.find(`[data-label="${zh.members.systemPrompt}"]`).setValue(opts.prompt ?? '');
  } else if (opts.persona) {
    await row.find(`[data-label="${zh.members.persona}"]`).setValue(opts.persona);
  }
  if (opts.provider !== undefined) {
    await row.find(`[data-label="${zh.members.provider}"]`).setValue(opts.provider);
  }
}

describe('TeamCreateDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.personaList.mockResolvedValue({
      data: { status: 'ok', data: PERSONAS },
    });
    apiMocks.providerList.mockResolvedValue({
      data: { status: 'ok', data: PROVIDERS },
    });
    apiMocks.listTeams.mockResolvedValue({
      data: { status: 'ok', data: { teams: [] } },
    });
  });

  it('renders two member rows by default plus persona/provider options', async () => {
    const wrapper = await openCreateDialog();

    expect(wrapper.find('.card-title').text()).toBe(zh.teams.create);
    expect(memberRows(wrapper)).toHaveLength(2);

    const personaOptions = memberRows(wrapper)[0]
      .find(`select[data-label="${zh.members.persona}"]`)
      .findAll('option')
      .map((o) => o.text());
    expect(personaOptions).toEqual(['persona-a', 'persona-b']);

    const providerOptions = wrapper
      .find(`select[data-label="${zh.members.provider}"]`)
      .findAll('option')
      .map((o) => o.text());
    // The disabled provider is filtered out; the empty default option leads.
    expect(providerOptions).toEqual([zh.members.providerDefault, 'prov-openai']);
  });

  it('saves a normalized payload and closes on success', async () => {
    apiMocks.createTeam.mockResolvedValue({
      data: { status: 'ok', data: { team_id: 't9' } },
    });
    const wrapper = await openCreateDialog();

    await byLabel(wrapper, zh.teams.name).setValue('Dream');
    const rows = memberRows(wrapper);
    // Persona row with a provider chosen.
    await fillMemberRow(rows[0], { name: 'Alice', persona: 'persona-a', provider: 'prov-openai' });
    // Custom row without a provider → provider_id omitted.
    await fillMemberRow(rows[1], { name: 'Bob', mode: 'custom', prompt: 'You are Bob' });
    await byLabel(wrapper, zh.teams.coordinator).setValue('Bob');

    await findButton(wrapper, '保存')!.trigger('click');
    await flushPromises();

    expect(apiMocks.createTeam).toHaveBeenCalledTimes(1);
    expect(apiMocks.createTeam).toHaveBeenCalledWith({
      name: 'Dream',
      members: [
        { name: 'Alice', persona_id: 'persona-a', provider_id: 'prov-openai' },
        { name: 'Bob', system_prompt: 'You are Bob' },
      ],
      coordinator: 'Bob',
      config: {
        failure_policy: 'pause',
        reply_timeout: 600,
        max_rounds: 20,
        max_parallel: 5,
        inject_max_length: 4000,
      },
    });
    expect(wrapper.emitted('saved')).toEqual([[{ team_id: 't9' }]]);
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([false]);
    expect(toastMock.success).toHaveBeenCalled();
  });

  it('keeps the dialog open and toasts the envelope exactly once on error', async () => {
    apiMocks.createTeam.mockResolvedValue({
      data: { status: 'error', message: '成员名必须 unique: Alice' },
    });
    const wrapper = await openCreateDialog();

    await byLabel(wrapper, zh.teams.name).setValue('Dream');
    const rows = memberRows(wrapper);
    await fillMemberRow(rows[0], { name: 'Alice', persona: 'persona-a' });
    await fillMemberRow(rows[1], { name: 'Alice', mode: 'custom', prompt: 'dup' });

    await findButton(wrapper, '保存')!.trigger('click');
    await flushPromises();

    // The composable already toasted the error envelope; the dialog must not
    // add a second toast (double-toast guard via the null return check).
    expect(toastMock.error).toHaveBeenCalledTimes(1);
    expect(toastMock.error).toHaveBeenCalledWith('成员名必须 unique: Alice');
    expect(wrapper.emitted('saved')).toBeUndefined();
    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
  });

  it('toasts the backend reason exactly once when createTeam rejects (HTTP 400)', async () => {
    // Non-2xx responses arrive as axios rejections carrying the error envelope
    // body: the composable surfaces response.data.message and the dialog's
    // null guard must keep it at exactly one toast (no double-toast).
    apiMocks.createTeam.mockRejectedValue({
      message: 'Request failed with status code 400',
      response: {
        status: 400,
        data: { status: 'error', message: '后端具体原因' },
      },
    });
    const wrapper = await openCreateDialog();

    await byLabel(wrapper, zh.teams.name).setValue('Dream');
    const rows = memberRows(wrapper);
    await fillMemberRow(rows[0], { name: 'Alice', persona: 'persona-a' });
    await fillMemberRow(rows[1], { name: 'Bob', persona: 'persona-b' });

    await findButton(wrapper, '保存')!.trigger('click');
    await flushPromises();

    expect(toastMock.error).toHaveBeenCalledTimes(1);
    expect(toastMock.error).toHaveBeenCalledWith('后端具体原因');
    expect(wrapper.emitted('saved')).toBeUndefined();
    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
  });

  it('blocks save when the team name is missing', async () => {
    const wrapper = await openCreateDialog();
    const rows = memberRows(wrapper);
    await fillMemberRow(rows[0], { name: 'Alice', persona: 'persona-a' });
    await fillMemberRow(rows[1], { name: 'Bob', persona: 'persona-b' });

    await findButton(wrapper, '保存')!.trigger('click');
    await flushPromises();

    expect(toastMock.error).toHaveBeenCalledWith(zh.teams.nameRequired);
    expect(apiMocks.createTeam).not.toHaveBeenCalled();
    expect(wrapper.emitted('saved')).toBeUndefined();
  });

  it('removes a row and re-points the coordinator to the first remaining member', async () => {
    const wrapper = await openCreateDialog();

    await findButton(wrapper, zh.members.add)!.trigger('click');
    expect(memberRows(wrapper)).toHaveLength(3);

    const rows = memberRows(wrapper);
    await fillMemberRow(rows[0], { name: 'Alice', persona: 'persona-a' });
    await fillMemberRow(rows[1], { name: 'Bob', persona: 'persona-b' });
    await fillMemberRow(rows[2], { name: 'Carol', persona: 'persona-a' });
    await byLabel(wrapper, zh.teams.coordinator).setValue('Carol');
    expect(byLabel(wrapper, zh.teams.coordinator).attributes('data-value')).toBe('Carol');

    const removeBtns = wrapper.findAll(`button[aria-label="${zh.members.remove}"]`);
    expect(removeBtns).toHaveLength(3);
    await removeBtns[2].trigger('click');

    expect(memberRows(wrapper)).toHaveLength(2);
    // Coordinator row disappeared → falls back to the first member.
    expect(byLabel(wrapper, zh.teams.coordinator).attributes('data-value')).toBe('Alice');
  });

  it('caps member rows at 10 and disables the add button', async () => {
    const wrapper = await openCreateDialog();
    const add = findButton(wrapper, zh.members.add)!;
    for (let i = 0; i < 8; i += 1) {
      await add.trigger('click');
    }
    expect(memberRows(wrapper)).toHaveLength(10);
    const addAfter = findButton(wrapper, zh.members.add)!;
    expect(addAfter.attributes('disabled')).toBeDefined();
  });

  it('edit mode prefills name/coordinator/config and saves via updateTeam', async () => {
    apiMocks.updateTeam.mockResolvedValue({
      data: { status: 'ok', data: { team_id: 't1' } },
    });
    const wrapper = await openEditDialog(TEAM);

    expect(wrapper.find('.card-title').text()).toBe(zh.teams.edit);
    expect(byLabel(wrapper, zh.teams.name).element).toHaveProperty('value', 'Alpha');
    // Member editing stays add-only in v1: no editable rows in edit mode.
    expect(memberRows(wrapper)).toHaveLength(0);
    expect(
      byLabel(wrapper, zh.teams.coordinator).attributes('data-value'),
    ).toBe('Bob');

    await findButton(wrapper, zh.teams.config)!.trigger('click');
    expect(
      byLabel(wrapper, zh.teams.failurePolicy).attributes('data-value'),
    ).toBe('auto_skip');
    expect(byLabel(wrapper, zh.teams.replyTimeout).element).toHaveProperty('value', '300');

    await findButton(wrapper, '保存')!.trigger('click');
    await flushPromises();

    expect(apiMocks.createTeam).not.toHaveBeenCalled();
    expect(apiMocks.updateTeam).toHaveBeenCalledWith('t1', {
      name: 'Alpha',
      coordinator: 'Bob',
      config: {
        failure_policy: 'auto_skip',
        reply_timeout: 300,
        max_rounds: 20,
        max_parallel: 5,
        inject_max_length: 4000,
      },
    });
    expect(wrapper.emitted('saved')).toEqual([[{ team_id: 't1' }]]);
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([false]);
  });
});

describe('MemberAddDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.personaList.mockResolvedValue({
      data: { status: 'ok', data: PERSONAS },
    });
    apiMocks.providerList.mockResolvedValue({
      data: { status: 'ok', data: PROVIDERS },
    });
  });

  function openMemberDialog() {
    const wrapper = mountDialog(MemberAddDialog, { modelValue: true, teamId: 't1' });
    return flushPromises().then(() => wrapper);
  }

  it('adds a member via agentTeamsApi.addMember and emits saved', async () => {
    apiMocks.addMember.mockResolvedValue({
      data: { status: 'ok', data: { member_id: 'm9', name: 'Carol' } },
    });
    const wrapper = await openMemberDialog();

    expect(wrapper.find('.card-title').text()).toBe(zh.members.add);
    await byLabel(wrapper, zh.members.name).setValue('Carol');
    await byLabel(wrapper, zh.members.persona).setValue('persona-b');
    await byLabel(wrapper, zh.members.provider).setValue('prov-openai');

    await findButton(wrapper, '保存')!.trigger('click');
    await flushPromises();

    expect(apiMocks.addMember).toHaveBeenCalledTimes(1);
    expect(apiMocks.addMember).toHaveBeenCalledWith('t1', {
      name: 'Carol',
      persona_id: 'persona-b',
      provider_id: 'prov-openai',
    });
    expect(wrapper.emitted('saved')).toEqual([[{ member_id: 'm9', name: 'Carol' }]]);
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([false]);
    expect(toastMock.success).toHaveBeenCalled();
  });

  it('keeps the dialog open and toasts the server message on error', async () => {
    apiMocks.addMember.mockResolvedValue({
      data: { status: 'error', message: '成员名必须 unique: Carol' },
    });
    const wrapper = await openMemberDialog();

    await byLabel(wrapper, zh.members.name).setValue('Carol');

    await findButton(wrapper, '保存')!.trigger('click');
    await flushPromises();

    expect(toastMock.error).toHaveBeenCalledWith('成员名必须 unique: Carol');
    expect(wrapper.emitted('saved')).toBeUndefined();
    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
  });

  it('toasts the backend reason when addMember rejects (HTTP 400)', async () => {
    // Validation errors arrive as HTTP 400/409 with an error envelope body —
    // axios rejects and the catch must surface response.data.message instead
    // of the generic errors.saveFailed fallback.
    apiMocks.addMember.mockRejectedValue({
      message: 'Request failed with status code 400',
      response: {
        status: 400,
        data: { status: 'error', message: '后端具体原因' },
      },
    });
    const wrapper = await openMemberDialog();

    await byLabel(wrapper, zh.members.name).setValue('Carol');
    await findButton(wrapper, '保存')!.trigger('click');
    await flushPromises();

    expect(toastMock.error).toHaveBeenCalledWith('后端具体原因');
    expect(wrapper.emitted('saved')).toBeUndefined();
  });

  it('toasts option-loading failures non-fatally and keeps the dialog usable', async () => {
    apiMocks.personaList.mockRejectedValue({
      message: 'Request failed with status code 500',
      response: {
        status: 500,
        data: { status: 'error', message: '后端具体原因' },
      },
    });
    apiMocks.providerList.mockResolvedValue({
      data: { status: 'ok', data: PROVIDERS },
    });
    const wrapper = await openMemberDialog();

    expect(toastMock.error).toHaveBeenCalledWith('后端具体原因');
    // The persona picker stays empty but the provider list still loaded, so
    // the dialog remains usable (the load failure is not fatal).
    expect(
      wrapper.find(`select[data-label="${zh.members.persona}"]`).findAll('option'),
    ).toHaveLength(0);
    expect(
      wrapper.find(`select[data-label="${zh.members.provider}"]`).findAll('option'),
    ).toHaveLength(PROVIDERS.filter((p) => p.enable !== false).length + 1);
  });

  it('blocks save when the member name is missing', async () => {
    const wrapper = await openMemberDialog();

    await findButton(wrapper, '保存')!.trigger('click');

    expect(toastMock.error).toHaveBeenCalledWith(zh.members.nameRequired);
    expect(apiMocks.addMember).not.toHaveBeenCalled();
  });
});
