// Specs for the provider model config dialog composable: the context
// compression threshold must not exceed the configured model window (a value
// above it can never trigger before the request overflows), and providers
// created before the field existed get the default key added so the edit
// dialog renders the input.
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';

const apiMocks = vi.hoisted(() => ({
  update: vi.fn(),
  createInSource: vi.fn(),
}));

vi.mock('@/api/v1', () => ({
  providerApi: {
    update: apiMocks.update,
    createInSource: apiMocks.createInSource,
  },
}));

import { useProviderModelConfigDialog } from './useProviderModelConfigDialog';

function createDialog() {
  const showMessage = vi.fn();
  const dialog = useProviderModelConfigDialog({
    selectedProviderSource: ref({ id: 'source-1' }),
    configSchema: ref({}),
    buildModelProviderConfig: () => ({}),
    modelAlreadyConfigured: () => false,
    loadConfig: vi.fn(),
    tm: (key: string) => key,
    showMessage,
  });
  return { dialog, showMessage };
}

describe('useProviderModelConfigDialog compression threshold', () => {
  beforeEach(() => {
    apiMocks.update.mockReset();
    apiMocks.createInSource.mockReset();
  });

  it('blocks saving a threshold above the model context window', async () => {
    const { dialog, showMessage } = createDialog();
    dialog.openProviderEdit({
      id: 'provider-1',
      max_context_tokens: 100000,
      compress_threshold_tokens: 200000,
    });

    await dialog.saveEditedProvider();

    expect(showMessage).toHaveBeenCalledWith(
      'models.thresholdExceedsWindow',
      'error'
    );
    expect(apiMocks.update).not.toHaveBeenCalled();
  });

  it('saves a threshold within the model context window', async () => {
    apiMocks.update.mockResolvedValue({ data: { status: 'ok', message: '' } });
    const { dialog, showMessage } = createDialog();
    dialog.openProviderEdit({
      id: 'provider-1',
      max_context_tokens: 100000,
      compress_threshold_tokens: 50000,
    });

    await dialog.saveEditedProvider();

    expect(showMessage).not.toHaveBeenCalledWith(
      'models.thresholdExceedsWindow',
      'error'
    );
    expect(apiMocks.update).toHaveBeenCalledTimes(1);
  });

  it('adds the default threshold key for providers that predate the field', () => {
    const { dialog } = createDialog();
    dialog.openProviderEdit({ id: 'provider-1', max_context_tokens: 100000 });

    expect(dialog.providerEditData.value.compress_threshold_tokens).toBe(0);
  });
});
