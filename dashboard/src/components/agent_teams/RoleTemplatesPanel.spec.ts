import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import RoleTemplatesPanel from './RoleTemplatesPanel.vue';

const api = vi.hoisted(() => ({
  list: vi.fn(),
  resources: vi.fn(),
  save: vi.fn(),
  preview: vi.fn(),
  get: vi.fn(),
}));
vi.mock('@/api/roleAssets', () => ({ roleAssetsApi: api }));
vi.mock('@/i18n/composables', () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
vi.mock('vuetify', () => ({ useDisplay: () => ({ mobile: false }) }));

const slotStub = { template: '<div><slot /></div>' };
const stubs = Object.fromEntries(
  [
    'v-card',
    'v-card-text',
    'v-card-actions',
    'v-alert',
    'v-chip',
    'v-expansion-panels',
    'v-expansion-panel',
    'v-expansion-panel-title',
    'v-expansion-panel-text',
    'v-card-title',
    'v-combobox',
    'v-spacer',
  ].map((name) => [name, slotStub]),
);
Object.assign(stubs, {
  'v-dialog': {
    props: ['modelValue'],
    template: '<div v-if="modelValue"><slot /></div>',
  },
  'v-btn': {
    props: ['disabled', 'loading'],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  'v-text-field': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  'v-textarea': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  'v-select': {
    props: ['modelValue', 'items'],
    emits: ['update:modelValue'],
    template:
      '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="item in items" :value="item.id ?? item.value ?? item">{{ item.name ?? item.title ?? item }}</option></select>',
  },
});

describe('reusable role configuration', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.list.mockResolvedValue({ roles: [] });
    api.resources.mockResolvedValue({
      profiles: [{ id: 'default', name: 'Default' }],
      models: [],
      personas: [],
      tools: [],
      skills: [],
      knowledge_bases: [],
    });
    api.preview.mockResolvedValue({
      ready: false,
      issues: [
        {
          path: 'installation.bindings.model',
          code: 'MISSING',
          message: 'Model is missing',
        },
      ],
      effective: { max_steps: 30 },
      sources: { max_steps: 'profile:default' },
      export_content: { prompt: null },
    });
    api.save.mockResolvedValue({ role_id: 'r1', version: 1 });
  });

  it('saves a reusable definition and shows field readiness without creating a member', async () => {
    const panel = mount(RoleTemplatesPanel, { global: { stubs } });
    await flushPromises();
    expect(panel.text()).toContain('roles.empty');
    await panel.get('[data-testid="new-role"]').trigger('click');
    await panel.get('[data-testid="role-name"]').setValue('Researcher');
    await panel.get('[data-testid="role-prompt"]').setValue('Check evidence.');
    await panel.get('[data-testid="preview-role"]').trigger('click');
    await flushPromises();
    expect(panel.text()).toContain('installation.bindings.model');
    expect(panel.text()).toContain('profile:default');
    await panel.get('[data-testid="save-role"]').trigger('click');
    await flushPromises();
    expect(api.save.mock.calls[0][0]).toBeNull();
    expect(api.save.mock.calls[0][1].definition).toMatchObject({
      name: 'Researcher',
      prompt: 'Check evidence.',
      model_slot: 'model',
    });
    expect(api.save.mock.calls[0][1].installation).toEqual({
      profile_id: 'default',
      bindings: {},
    });
  });
});
