import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent } from 'vue';
import { describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ get: vi.fn(), update: vi.fn(), list: vi.fn() }));
vi.mock('@/api/v1', () => ({
  personaApi: api,
  skillApi: { list: api.list },
  toolApi: { list: api.list },
}));

import PersonaQuickPreview from './PersonaQuickPreview.vue';

describe('editable system default persona preview', () => {
  it.each([null, []])('loads saved content and preserves capability selection %j', async (selection) => {
    api.get.mockResolvedValue({ data: { status: 'ok', data: {
      persona_id: 'default', system_prompt: 'Edited default prompt',
      tools: selection, skills: selection,
    } } });
    api.list.mockResolvedValue({ data: { status: 'ok', data: [] } });
    api.update.mockResolvedValue({ data: { status: 'ok' } });
    api.update.mockClear();
    const editor = defineComponent({
      props: ['tools', 'skills', 'readonly', 'updateCapability'],
      template: '<div />',
    });
    const wrapper = mount(PersonaQuickPreview, {
      props: { modelValue: 'default', editable: true },
      global: { stubs: {
        PersonaCapabilitiesEditor: editor,
        'v-progress-circular': true,
        'v-alert': true,
      } },
    });
    try {
      await flushPromises();
      expect(wrapper.find('.prompt-content').text()).toBe('Edited default prompt');
      const capabilities = wrapper.findComponent(editor);
      expect(capabilities.props('readonly')).toBe(false);
      expect(capabilities.props('tools')).toEqual(selection);
      expect(capabilities.props('skills')).toEqual(selection);
      await capabilities.props('updateCapability')('tools', ['tool-a'], selection);
      expect(api.update).toHaveBeenCalledWith('default', { tools: ['tool-a'] });
      expect(capabilities.props('skills')).toEqual(selection);
    } finally {
      wrapper.unmount();
    }
  });
});
