import { createApp } from 'vue';
import { createPinia } from 'pinia';
import { useInteractiveChoiceStore } from './stores/interactiveChoice';
import { useInteractiveChoiceAttentionStore } from './stores/interactiveChoiceAttention';
import { useChatHeaderStore } from './stores/chatHeader';
import { useRunFinishedAttentionStore } from './stores/runFinishedAttention';
import App from './App.vue';
import { router } from './router';
import vuetify from './plugins/vuetify';
import confirmPlugin from './plugins/confirmPlugin';
import { setupI18n } from './i18n/composables';
import '@/scss/style.scss';

import print from 'vue3-print-nb';
import { setupHttpClient } from './api/http';
import { waitForRouterReadyInBackground } from './utils/routerReadiness.mjs';

setupHttpClient();

/**
 * 挂载后初始化主题并注册全局系统主题监听器。
 * 职责：
 *   - 同步 Vuetify theme 名称与 store 中的 uiTheme
 *   - 当 themeMode === 'system' 时，监听系统色彩模式变化，实时更新两者
 *   - 应用自定义 primary/secondary 色
 * 注意：VerticalHeader.vue / ThemeSwitcher.vue 不再自行注册 matchMedia 监听器，
 *       避免与此处产生竞态。
 */
function setupThemeSync(pinia: ReturnType<typeof createPinia>) {
  import('./stores/customizer').then(({ useCustomizerStore }) => {
    const customizer = useCustomizerStore(pinia);

    // 1. 若当前是 system 模式，重新用 matchMedia 计算，防止 SSR / 构建时偏差
    if (customizer.themeMode === 'system') {
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      const uiTheme = prefersDark ? 'PurpleThemeDark' : 'PurpleTheme';
      customizer.uiTheme = uiTheme;
      localStorage.setItem('uiTheme', uiTheme);
    }

    // 2. 将 Vuetify 主题对齐到 store
    vuetify.theme.global.name.value = customizer.uiTheme;

    // 3. 应用用户自定义色
    const storedPrimary = localStorage.getItem('themePrimary');
    const storedSecondary = localStorage.getItem('themeSecondary');
    if (storedPrimary || storedSecondary) {
      const themes = vuetify.theme.themes.value;
      ['PurpleTheme', 'PurpleThemeDark'].forEach((name) => {
        const theme = themes[name];
        if (!theme?.colors) return;
        if (storedPrimary) theme.colors.primary = storedPrimary;
        if (storedSecondary) theme.colors.secondary = storedSecondary;
        if (storedPrimary && theme.colors.darkprimary) theme.colors.darkprimary = storedPrimary;
        if (storedSecondary && theme.colors.darksecondary) theme.colors.darksecondary = storedSecondary;
      });
    }

    // 4. 全局唯一 matchMedia 监听器：仅在 system 模式下响应系统切换
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    mediaQuery.addEventListener('change', (e) => {
      if (customizer.themeMode !== 'system') return;
      const uiTheme = e.matches ? 'PurpleThemeDark' : 'PurpleTheme';
      customizer.uiTheme = uiTheme;
      localStorage.setItem('uiTheme', uiTheme);
      vuetify.theme.global.name.value = uiTheme;
    });
  });
}

// 初始化新的i18n系统，等待完成后再挂载应用
setupI18n().then(async () => {
  console.log('🌍 新i18n系统初始化完成');

  const app = createApp(App);
  const pinia = createPinia();
  app.use(pinia);
  window.addEventListener('astrbot:account-changed', () => {
    useInteractiveChoiceStore(pinia).$reset();
    useInteractiveChoiceAttentionStore(pinia).$reset();
    useRunFinishedAttentionStore(pinia).$reset();
    useChatHeaderStore(pinia).CLEAR_CONTEXT();
  });
  app.use(router);
  app.use(print);
  app.use(vuetify);
  app.use(confirmPlugin);
  await router.isReady();
  app.mount('#app');

  setupThemeSync(pinia);
}).catch(error => {
  console.error('❌ 新i18n系统初始化失败:', error);

  // 即使i18n初始化失败，也要挂载应用（使用回退机制）
  const app = createApp(App);
  const pinia = createPinia();
  app.use(pinia);
  window.addEventListener('astrbot:account-changed', () => {
    useInteractiveChoiceStore(pinia).$reset();
    useInteractiveChoiceAttentionStore(pinia).$reset();
    useRunFinishedAttentionStore(pinia).$reset();
    useChatHeaderStore(pinia).CLEAR_CONTEXT();
  });
  app.use(router);
  app.use(print);
  app.use(vuetify);
  app.use(confirmPlugin);
  app.mount('#app');
  waitForRouterReadyInBackground(router);

  setupThemeSync(pinia);
});
