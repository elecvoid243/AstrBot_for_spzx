import { afterEach, describe, expect, it } from 'vitest';
import { initI18n, useI18n } from './composables';
import { localeLoaders, type Locale } from './translations';

describe('lazy locale loading', () => {
  afterEach(async () => { await initI18n('zh-CN'); });

  it.each(Object.keys(localeLoaders) as Locale[])('loads all existing modules for %s', async (locale) => {
    const messages = await localeLoaders[locale]();
    expect(messages.core.navigation.pluginView).toBeTruthy();
    expect(messages.features.extension.buttons.openView).toBeTruthy();
    if (locale !== 'ja-JP') {
      expect(messages.features.chat).toHaveProperty('interactiveChoice.cancelled');
      expect('agent-teams' in messages.features).toBe(true);
    }
    await initI18n(locale);
    const i18n = useI18n();
    expect(i18n.t('core.navigation.pluginView')).toBe(messages.core.navigation.pluginView);
  });

  it('awaits the new language before notifying pages of a locale change', async () => {
    await initI18n('zh-CN');
    const i18n = useI18n();
    let displayedTitle = '';
    const onChanged = () => { displayedTitle = i18n.t('core.navigation.pluginView'); };
    window.addEventListener('astrbot-locale-changed', onChanged);
    try {
      await i18n.setLocale('en-US');
      expect(displayedTitle).toBe('Plugin Views');
      expect(localStorage.getItem('astrbot-locale')).toBe('en-US');
      expect(document.documentElement.lang).toBe('en-US');
    } finally {
      window.removeEventListener('astrbot-locale-changed', onChanged);
    }
  });
});
