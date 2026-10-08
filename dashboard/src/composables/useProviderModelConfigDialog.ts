import { computed, ref, type Ref } from 'vue'
import { providerApi } from '@/api/v1'

interface UseProviderModelConfigDialogOptions {
  selectedProviderSource: Ref<any | null>
  configSchema: Ref<Record<string, any>>
  buildModelProviderConfig: (modelId: string) => any
  modelAlreadyConfigured: (modelId: string) => boolean
  loadConfig: () => Promise<void> | void
  tm: (key: string, params?: Record<string, unknown>) => string
  showMessage: (message: string, color?: string) => void
}

export function useProviderModelConfigDialog(options: UseProviderModelConfigDialogOptions) {
  const {
    selectedProviderSource,
    configSchema,
    buildModelProviderConfig,
    modelAlreadyConfigured,
    loadConfig,
    tm,
    showMessage
  } = options

  const showProviderEditDialog = ref(false)
  const providerEditData = ref<any | null>(null)
  const providerEditOriginalId = ref('')
  const providerEditMode = ref<'add' | 'edit'>('edit')
  const savingProviders = ref<string[]>([])

  const providerModelConfigSchema = computed(() => {
    if (!configSchema.value?.provider?.items) {
      return configSchema.value
    }

    const schema = JSON.parse(JSON.stringify(configSchema.value))
    const sourceType = selectedProviderSource.value?.type
    const hiddenKeys = ['id', 'model']

    if (sourceType === 'googlegenai_chat_completion') {
      hiddenKeys.push('custom_extra_body')
    }

    for (const key of hiddenKeys) {
      if (schema.provider.items[key]) {
        schema.provider.items[key].invisible = true
      }
    }
    return schema
  })

  const providerEditDialogTitle = computed(() => {
    const actionTitle = providerEditMode.value === 'add'
      ? tm('dialogs.config.addTitle')
      : tm('dialogs.config.editTitle')
    const providerId = providerEditData.value?.id
    return providerId ? `${actionTitle} ${providerId}` : actionTitle
  })

  function openProviderEdit(provider: any) {
    const editableProvider = JSON.parse(JSON.stringify(provider))
    if (editableProvider.provider_source_id) {
      delete editableProvider.reasoning
    }
    // The edit dialog renders exactly the keys present on the provider object,
    // so a chat provider created before this field existed needs the default
    // filled in here or the input would never show up.
    if (editableProvider.compress_threshold_tokens === undefined) {
      editableProvider.compress_threshold_tokens = 0
    }
    providerEditData.value = editableProvider
    providerEditOriginalId.value = provider.id
    providerEditMode.value = 'edit'
    showProviderEditDialog.value = true
  }

  function openModelAddDialog(modelId: string) {
    if (!selectedProviderSource.value) {
      showMessage(tm('providerSources.selectHint'), 'error')
      return
    }
    if (!modelId) return
    if (modelAlreadyConfigured(modelId)) {
      showMessage(tm('models.manualModelExists'), 'error')
      return
    }

    const newProviderConfig = buildModelProviderConfig(modelId)
    if (!newProviderConfig) return

    providerEditData.value = newProviderConfig
    providerEditOriginalId.value = ''
    providerEditMode.value = 'add'
    showProviderEditDialog.value = true
  }

  async function saveEditedProvider() {
    if (!providerEditData.value) return

    // A compression threshold above the model window can never trigger before
    // the request overflows; refuse it while the window value is known. When
    // max_context_tokens is 0 (auto-filled at runtime), the runner-side check
    // is the backstop.
    const maxContextTokens = Number(providerEditData.value.max_context_tokens || 0)
    const compressThreshold = Number(
      providerEditData.value.compress_threshold_tokens || 0
    )
    if (maxContextTokens > 0 && compressThreshold > maxContextTokens) {
      showMessage(tm('models.thresholdExceedsWindow'), 'error')
      return
    }

    savingProviders.value.push(providerEditData.value.id)
    try {
      const isAdding = providerEditMode.value === 'add'
      const sourceId = providerEditData.value.provider_source_id || selectedProviderSource.value?.id
      if (isAdding && !sourceId) {
        throw new Error(tm('providerSources.selectHint'))
      }
      const res = isAdding
        ? await providerApi.createInSource(sourceId, providerEditData.value)
        : await providerApi.update(
          providerEditOriginalId.value || providerEditData.value.id,
          providerEditData.value
      )

      if (res.data.status === 'error') {
        throw new Error(res.data.message || tm('providerSources.saveError'))
      }

      showMessage(
        res.data.message || (
          isAdding
            ? tm('models.addSuccess', { model: providerEditData.value.model })
            : tm('providerSources.saveSuccess')
        )
      )
      showProviderEditDialog.value = false
      await loadConfig()
    } catch (err: any) {
      showMessage(err.response?.data?.message || err.message || tm('providerSources.saveError'), 'error')
    } finally {
      savingProviders.value = savingProviders.value.filter(id => id !== providerEditData.value?.id)
    }
  }

  return {
    showProviderEditDialog,
    providerEditData,
    savingProviders,
    providerModelConfigSchema,
    providerEditDialogTitle,
    openProviderEdit,
    openModelAddDialog,
    saveEditedProvider
  }
}
