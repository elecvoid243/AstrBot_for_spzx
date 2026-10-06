import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent } from 'vue';
import { createMemoryHistory, createRouter, type RouteRecordRaw } from 'vue-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  getPlugin: vi.fn(),
  getEntry: vi.fn(),
  apiGet: vi.fn(),
}));

vi.mock('@/api/v1', () => ({ pluginApi: { get: mocks.getPlugin, page: mocks.getEntry } }));
vi.mock('axios', () => ({ default: { get: mocks.apiGet } }));
vi.mock('@/api/http', () => ({ fetchWithAuth: vi.fn() }));
vi.mock('@/stores/customizer', () => ({ useCustomizerStore: () => ({ isDark: false, uiTheme: 'light' }) }));
vi.mock('vue-router', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-router')>(),
  useRoute: () => ({ params: { pluginName: 'astrbot_plugin_tc_memory', pageName: 'memory-panel' } }),
}));

import PluginViewPage from './PluginViewPage.vue';
import MainRoutes from '@/router/MainRoutes';
import { usePluginI18n } from '@/utils/pluginI18n';

let wrapper: ReturnType<typeof mount> | undefined;

describe('Plugin Views compatibility', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mocks.getPlugin.mockResolvedValue({ data: { status: 'ok', data: {
      name: 'astrbot_plugin_tc_memory', activated: true,
    } } });
    mocks.getEntry.mockResolvedValue({ data: { status: 'ok', data: {
      name: 'memory-panel', title: 'Memory panel',
      content_path: '/api/v1/plugins/astrbot_plugin_tc_memory/views/memory-panel/_t/test-token/?asset_token=test-token',
    } } });
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = undefined;
  });

  it('loads the signed entry with the existing nested-panel sandbox permissions', async () => {
    wrapper = mount(PluginViewPage, { global: { stubs: { 'v-progress-circular': true, 'v-alert': true } } });
    await flushPromises();
    const iframe = wrapper.get('iframe');
    expect(iframe.attributes('sandbox')!.split(' ')).toEqual([
      'allow-scripts', 'allow-forms', 'allow-downloads', 'allow-same-origin', 'allow-popups',
    ]);
    const src = new URL(iframe.attributes('src')!, window.location.origin);
    expect(src.pathname).toBe('/api/v1/plugins/astrbot_plugin_tc_memory/views/memory-panel/_t/test-token/');
    expect(src.searchParams.get('asset_token')).toBe('test-token');
    expect(src.searchParams.get('theme')).toBe('light');
    expect(iframe.attributes('referrerpolicy')).toBe('no-referrer');
    expect(mocks.getEntry).toHaveBeenCalledWith('astrbot_plugin_tc_memory', 'memory-panel');
  });

  it('forwards the legacy bridge status request to the plugin extension API', async () => {
    mocks.apiGet.mockResolvedValue({ data: { status: 'ok', data: { user_key: 'test-key' } } });
    wrapper = mount(PluginViewPage, { global: { stubs: { 'v-progress-circular': true, 'v-alert': true } } });
    await flushPromises();
    const post = vi.fn();
    const frameWindow = { postMessage: post } as unknown as Window;
    Object.defineProperty(wrapper.get('iframe').element, 'contentWindow', { value: frameWindow });
    window.dispatchEvent(new MessageEvent('message', {
      source: frameWindow,
      origin: window.location.origin,
      data: { channel: 'astrbot-plugin-page', kind: 'request', requestId: 'memory-status', action: 'api:get', endpoint: 'status' },
    }));
    await flushPromises();
    expect(mocks.apiGet).toHaveBeenCalledWith('/api/v1/plugins/extensions/astrbot_plugin_tc_memory/status', { params: {} });
    expect(post).toHaveBeenCalledWith({
      channel: 'astrbot-plugin-page', kind: 'response', requestId: 'memory-status', ok: true, data: { user_key: 'test-key' },
    }, window.location.origin);
  });

  it('redirects old page links while retaining plugin parameters and query strings', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: MainRoutes.children.filter((route) => route.path.startsWith('/plugin-')).map((route) => ({
        ...route, component: defineComponent({ template: '<div />' }),
      })) as RouteRecordRaw[],
    });
    await router.push('/plugin-page/astrbot_plugin_tc_memory/memory-panel?theme=dark');
    expect(router.currentRoute.value.name).toBe('PluginView');
    expect(router.currentRoute.value.path).toBe('/plugin-view/astrbot_plugin_tc_memory/memory-panel');
    expect(router.currentRoute.value.query).toEqual({ theme: 'dark' });
  });

  it('prefers Views translations and falls back to existing Pages translations', () => {
    let translate: ReturnType<typeof usePluginI18n>;
    wrapper = mount(defineComponent({ setup() { translate = usePluginI18n(); return () => null; } }));
    const plugin = { i18n: { 'zh-CN': {
      views: { panel: { title: 'View title' } },
      pages: { panel: { title: 'Legacy title', description: 'Legacy description' } },
    } } };
    const page = { name: 'panel', i18n_key: 'pages.panel' };
    expect(translate!.pluginViewTitle(plugin, page)).toBe('View title');
    expect(translate!.pluginViewDescription(plugin, page)).toBe('Legacy description');
    expect(translate!.pluginViewTitle({ i18n: { 'zh-CN': { pages: plugin.i18n['zh-CN'].pages } } }, page)).toBe('Legacy title');
  });
});
