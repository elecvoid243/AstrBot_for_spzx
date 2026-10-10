import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ setup: vi.fn(), imported: vi.fn(), loader: {} }));
vi.mock('@/plugins/monaco', () => ({ setupMonaco: mocks.setup }));
vi.mock('@guolao/vue-monaco-editor', async () => {
  mocks.imported();
  const { defineComponent, h } = await import('vue');
  return {
    loader: mocks.loader,
    VueMonacoEditor: defineComponent({
      props: ['value', 'language', 'theme'],
      emits: ['update:value'],
      setup(props, { emit }) {
        return () => h('textarea', {
          value: props.value,
          'data-language': props.language,
          'data-theme': props.theme,
          onInput: (event: Event) => emit('update:value', (event.target as HTMLTextAreaElement).value),
        });
      },
    }),
  };
});

import { LazyMonacoEditor } from './LazyMonacoEditor';

let wrapper: ReturnType<typeof mount> | undefined;
afterEach(() => { wrapper?.unmount(); });

describe('lazy Monaco editor', () => {
  it('defers the editor import and setup until the component is mounted', async () => {
    expect(mocks.imported).not.toHaveBeenCalled();
    expect(mocks.setup).not.toHaveBeenCalled();
    wrapper = mount(defineComponent({
      components: { LazyMonacoEditor },
      props: ['value', 'language', 'theme'],
      emits: ['update:value'],
      template: `<LazyMonacoEditor :value="value" :language="language" :theme="theme"
        @update:value="$emit('update:value', $event)" />`,
    }), { props: { value: '{}', language: 'json', theme: 'vs-dark' } });
    await flushPromises();
    expect(mocks.imported).toHaveBeenCalledTimes(1);
    expect(mocks.setup).toHaveBeenCalledWith(mocks.loader);
    const editor = wrapper.get('textarea');
    expect(editor.attributes('data-language')).toBe('json');
    expect(editor.attributes('data-theme')).toBe('vs-dark');
    await editor.setValue('{"enabled":true}');
    expect(wrapper.emitted('update:value')).toEqual([['{"enabled":true}']]);
    await wrapper.setProps({ theme: 'vs', language: 'yaml' });
    expect(editor.attributes('data-theme')).toBe('vs');
    expect(editor.attributes('data-language')).toBe('yaml');
  });
});
