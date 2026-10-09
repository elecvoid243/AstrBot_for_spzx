import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  listByProviderType: vi.fn(),
  schema: vi.fn(),
  update: vi.fn(),
  error: vi.fn(),
}));

vi.mock('@/api/v1', () => ({ providerApi: apiMocks }));
vi.mock('vuetify', () => ({ useDisplay: () => ({ height: { value: 900 } }) }));
vi.mock('@/utils/toast', () => ({
  useToast: () => ({ success: vi.fn(), error: apiMocks.error }),
}));
vi.mock('@/components/shared/AstrBotConfig.vue', () => ({ default: {} }));
vi.mock('@/components/provider/ProviderChatCompletionPanel.vue', () => ({ default: {} }));
vi.mock('@/views/ProviderPage.vue', () => ({ default: {} }));

import ProviderSelectMenu from './ProviderSelectMenu.vue';

const menuStub = defineComponent({
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template: `<div>
    <slot name="activator" :props="{ onClick: () => $emit('update:modelValue', true) }" />
    <slot v-if="modelValue" />
  </div>`,
});
const dialogStub = defineComponent({
  props: ['modelValue'],
  template: '<div v-if="modelValue" role="dialog"><slot /></div>',
});
const buttonStub = defineComponent({
  props: ['disabled'],
  template: '<button :disabled="disabled"><slot /></button>',
});
const containerStub = {
  template: '<div><slot /><slot name="append" /></div>',
};
const editorStub = defineComponent({
  props: ['iterable'],
  template: `<input
    data-testid="compression-threshold"
    type="number"
    v-model.number="iterable.compress_threshold_tokens"
  />`,
});
const wrappers: ReturnType<typeof mount>[] = [];

function mountSelector(modelValue: string | string[], variant = 'config') {
  const wrapper = mount(ProviderSelectMenu, {
    props: {
      modelValue,
      multiple: Array.isArray(modelValue),
      variant: variant as 'config' | 'header',
    },
    global: {
      stubs: {
        'v-menu': menuStub,
        'v-dialog': dialogStub,
        'v-overlay': dialogStub,
        'v-btn': buttonStub,
        'v-card': containerStub,
        'v-card-title': containerStub,
        'v-card-text': containerStub,
        'v-card-actions': containerStub,
        'v-list': containerStub,
        'v-list-item': containerStub,
        'v-list-item-title': containerStub,
        'v-list-item-subtitle': containerStub,
        'v-list-subheader': containerStub,
        'v-text-field': true,
        'v-progress-linear': true,
        'v-divider': true,
        'v-spacer': true,
        AstrBotConfig: editorStub,
        ProviderPage: true,
        ProviderChatCompletionPanel: true,
      },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}

describe('ProviderSelectMenu model settings shortcuts', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    apiMocks.listByProviderType.mockResolvedValue({
      data: {
        status: 'ok',
        data: [{ id: 'model-1', model: 'gpt-model', type: 'openai_chat_completion', reasoning: true }],
      },
    });
    apiMocks.schema.mockResolvedValue({
      data: {
        status: 'ok',
        data: {
          providers: [{
            id: 'model-1',
            model: 'gpt-model',
            provider_source_id: 'source-1',
            max_context_tokens: 100000,
            temperature: 0.25,
          }],
          provider_sources: [{ id: 'source-1', type: 'openai_chat_completion' }],
          config_schema: {},
        },
      },
    });
    apiMocks.update.mockResolvedValue({ data: { status: 'ok', message: '' } });
  });

  afterEach(() => {
    for (const wrapper of wrappers.splice(0)) wrapper.unmount();
  });

  it('edits the stored model without persisting inherited list fields or changing selection', async () => {
    const wrapper = mountSelector('model-1');
    await wrapper.get('button[icon="mdi-cog-outline"]').trigger('click');
    await flushPromises();
    await wrapper.get('[data-testid="compression-threshold"]').setValue('50000');
    await wrapper.get('[role="dialog"] button[color="primary"]').trigger('click');
    await flushPromises();

    expect(apiMocks.update).toHaveBeenCalledWith('model-1', {
      id: 'model-1', model: 'gpt-model', provider_source_id: 'source-1',
      max_context_tokens: 100000, temperature: 0.25, compress_threshold_tokens: 50000,
    });
    expect(apiMocks.listByProviderType).toHaveBeenCalledWith('chat_completion');
    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
    expect(wrapper.emitted('select')).toBeUndefined();
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  });

  it('keeps fallback order when opening and saving a model from the menu', async () => {
    const fallbackOrder = ['model-2', 'model-1'];
    const wrapper = mountSelector(fallbackOrder);
    await wrapper.get('.provider-trigger').trigger('click');
    await flushPromises();
    await wrapper.get('button[icon="mdi-cog-outline"]').trigger('click');
    await flushPromises();
    await wrapper.get('[role="dialog"] button[color="primary"]').trigger('click');
    await flushPromises();

    expect(apiMocks.update).toHaveBeenCalledWith('model-1', expect.any(Object));
    expect(wrapper.props('modelValue')).toEqual(['model-2', 'model-1']);
    expect(fallbackOrder).toEqual(['model-2', 'model-1']);
    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
  });

  it('preserves the local compression threshold validation through the shortcut', async () => {
    const wrapper = mountSelector('model-1');
    await wrapper.get('button[icon="mdi-cog-outline"]').trigger('click');
    await flushPromises();
    await wrapper.get('[data-testid="compression-threshold"]').setValue('200000');
    await wrapper.get('[role="dialog"] button[color="primary"]').trigger('click');
    await flushPromises();

    expect(apiMocks.update).not.toHaveBeenCalled();
    expect(apiMocks.error).toHaveBeenCalled();
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true);
  });

  it('keeps settings shortcuts out of automatic selection and chat headers', async () => {
    const wrapper = mountSelector('');
    expect(wrapper.find('button[icon="mdi-cog-outline"]').exists()).toBe(false);
    await wrapper.setProps({ modelValue: 'model-1', variant: 'header' });
    await wrapper.get('.provider-trigger').trigger('click');
    await flushPromises();
    expect(wrapper.find('button[icon="mdi-cog-outline"]').exists()).toBe(false);
  });
});
