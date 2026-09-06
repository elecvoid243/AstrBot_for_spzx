// Specs for the agent-teams member config panel + form (Task 5).
//
// The panel is exercised with the real component tree minus the two folder
// pickers: PersonaSelector and KnowledgeBaseSelector are replaced with
// input-based stubs via vi.mock (same convention WorkflowEditor.spec uses for
// PersonaSelector), so only the modelValue/update:modelValue contract needs
// to hold. Every API facade the form consumes is mocked at @/api/v1 and
// @/utils/toast is mocked as in the other agent-teams specs, keeping the
// suite deterministic (no timers or network). agentTeamsApi.updateMember
// resolves the team dict directly (Task 4 contract) and rejects with HTTP
// errors, which the error-path test asserts via extractApiError.
import { flushPromises, mount } from '@vue/test-utils';
import type { DOMWrapper, VueWrapper } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import zh from '@/i18n/locales/zh-CN/features/agent-teams.json';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  updateMember: vi.fn(),
  configProfileList: vi.fn(),
  toolList: vi.fn(),
  skillList: vi.fn(),
  providerList: vi.fn(),
}));

vi.mock('@/utils/toast', () => ({
  useToast: () => toastMock,
}));

vi.mock('@/api/v1', () => ({
  agentTeamsApi: { updateMember: apiMocks.updateMember },
  configProfileApi: { list: apiMocks.configProfileList },
  personaApi: {},
  toolApi: { list: apiMocks.toolList },
  skillApi: { list: apiMocks.skillList },
  providerApi: { listByProviderType: apiMocks.providerList },
}));

// PersonaSelector pulls in the whole persona folder tree + PersonaForm; the
// form only needs the modelValue contract (no defineModel upstream).
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

// KnowledgeBaseSelector is a chip + dialog picker over an array of kb names;
// an input joining/ splitting on commas keeps the same v-model contract.
vi.mock('@/components/shared/KnowledgeBaseSelector.vue', async () => {
  const { defineComponent } = await import('vue');
  return {
    default: defineComponent({
      name: 'KnowledgeBaseSelector',
      props: { modelValue: { type: Array, default: () => [] } },
      emits: ['update:modelValue'],
      template:
        '<input class="kb-stub" :value="modelValue.join(\',\')" @input="$emit(\'update:modelValue\', $event.target.value.split(\',\').filter(Boolean))" />',
    }),
  };
});

import MemberConfigPanel from './MemberConfigPanel.vue';

const TEAM = {
  team_id: 't1',
  name: 'Alpha',
  coordinator_member_id: 'm1',
  members: [
    { member_id: 'm1', name: 'Alice', persona_id: 'persona_a' },
    { member_id: 'm2', name: 'Bob' },
  ],
};

const UPDATED_TEAM = { ...TEAM, name: 'Alpha Updated' };

const okEnvelope = (data: unknown) =>
  Promise.resolve({ data: { status: 'ok', message: null, data } });

/** Default passing stubs for the Vuetify components the form renders. */
const stubs = {
  'v-select': {
    props: {
      modelValue: { type: [String, Number, Array], default: '' },
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
};

function mountPanel(props: Record<string, unknown> = {}) {
  return mount(MemberConfigPanel, {
    props: { team: TEAM, ...props },
    global: { stubs },
  }) as VueWrapper<any>;
}

function byLabel(wrapper: VueWrapper<any>, label: string) {
  return wrapper.find(`[data-label="${label}"]`);
}

function radioGroup(wrapper: VueWrapper<any>, index: number) {
  return wrapper.findAll('.radio-group-stub')[index];
}

function clickRadio(group: DOMWrapper<Element>, value: string) {
  return group.find(`.radio-stub[data-value="${value}"] input[type="radio"]`).trigger('change');
}

describe('MemberConfigPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
    apiMocks.providerList.mockResolvedValue(
      okEnvelope([
        { id: 'prov-openai', enable: true },
        { id: 'prov-off', enable: false },
      ]),
    );
  });

  it('renders the member list, marks the coordinator and loads a member into the form', async () => {
    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.findAll('.member-config-item')).toHaveLength(2);
    // m1 is the coordinator; exactly one badge is rendered.
    expect(wrapper.findAll('.member-config-coord')).toHaveLength(1);
    expect(wrapper.findAll('.member-config-coord')[0].text()).toBe(zh.teams.coordinator);
    // The first member is selected by default.
    expect(byLabel(wrapper, zh.memberConfig.name).element).toHaveProperty('value', 'Alice');

    // Selecting another member re-seeds the form from that row.
    await wrapper.findAll('.member-config-item')[1].trigger('click');
    expect(byLabel(wrapper, zh.memberConfig.name).element).toHaveProperty('value', 'Bob');
  });

  it('saves the complete member payload via updateMember and emits updateTeam', async () => {
    apiMocks.updateMember.mockResolvedValue(UPDATED_TEAM);
    const wrapper = mountPanel();
    await flushPromises();

    await byLabel(wrapper, zh.memberConfig.name).setValue('Alice Renamed');
    await byLabel(wrapper, zh.memberConfig.configProfile).setValue('cfg1');
    await byLabel(wrapper, zh.memberConfig.provider).setValue('prov-openai');
    // Tools: allowlist with one active tool.
    await clickRadio(radioGroup(wrapper, 0), 'allowlist');
    await wrapper.find('.checkbox-stub[data-label="tool_a"] input[type="checkbox"]').trigger('change');
    // Skills: disable all.
    await clickRadio(radioGroup(wrapper, 1), 'disable_all');
    await byLabel(wrapper, zh.memberConfig.maxSteps).setValue('8');
    await byLabel(wrapper, zh.memberConfig.toolCallTimeout).setValue('90');
    await byLabel(wrapper, zh.memberConfig.contextLength).setValue('16000');
    await wrapper.find('.kb-stub').setValue('kb_one');

    await wrapper.find('[data-test="member-config-save"]').trigger('click');
    await flushPromises();

    expect(apiMocks.updateMember).toHaveBeenCalledTimes(1);
    expect(apiMocks.updateMember).toHaveBeenCalledWith('t1', 'm1', {
      name: 'Alice Renamed',
      persona_id: 'persona_a',
      provider_id: 'prov-openai',
      runner_config: {
        config_id: 'cfg1',
        tools: ['tool_a'],
        skills: [],
        max_steps: 8,
        tool_call_timeout: 90,
        context_length: 16000,
        kb_names: ['kb_one'],
      },
    });
    expect(toastMock.success).toHaveBeenCalledWith(zh.memberConfig.saveSuccess);
    expect(wrapper.emitted('updateTeam')).toEqual([[UPDATED_TEAM]]);
  });

  it('serializes tools disable_all as an empty list and unset persona/provider as null', async () => {
    const wrapper = mountPanel();
    await flushPromises();

    await clickRadio(radioGroup(wrapper, 0), 'disable_all');
    await wrapper.find('[data-test="member-config-save"]').trigger('click');
    await flushPromises();

    expect(apiMocks.updateMember).toHaveBeenCalledWith('t1', 'm1', {
      name: 'Alice',
      persona_id: 'persona_a',
      // persona/provider pins are always sent: null means "cleared" so the
      // backend can actually un-pin the session.
      provider_id: null,
      // Backend semantics: `[]` = disable all, list = allowlist, omission = follow.
      runner_config: { tools: [], kb_names: [] },
    });
  });

  it('transmits null when an existing persona is cleared', async () => {
    const wrapper = mountPanel();
    await flushPromises();

    // m1 (Alice) has persona pinned; clear the persona input.
    await wrapper.find('.persona-stub').setValue('');
    await wrapper.find('[data-test="member-config-save"]').trigger('click');
    await flushPromises();

    expect(apiMocks.updateMember).toHaveBeenCalledWith('t1', 'm1', {
      name: 'Alice',
      persona_id: null,
      provider_id: null,
      runner_config: { kb_names: [] },
    });
  });

  it('toasts the backend reason and emits nothing when updateMember rejects', async () => {
    apiMocks.updateMember.mockRejectedValue({
      message: 'Request failed with status code 400',
      response: {
        status: 400,
        data: { status: 'error', message: '后端具体原因' },
      },
    });
    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.find('[data-test="member-config-save"]').trigger('click');
    await flushPromises();

    expect(toastMock.error).toHaveBeenCalledWith('后端具体原因');
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(wrapper.emitted('updateTeam')).toBeUndefined();
  });
});
