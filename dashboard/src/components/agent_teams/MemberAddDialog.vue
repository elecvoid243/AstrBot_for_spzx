<template>
  <v-dialog v-model="dialog" max-width="560">
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{ tm('members.add') }}</v-card-title>
      <v-card-text>
        <v-text-field
          v-model="name"
          :label="tm('members.name')"
          density="compact"
          class="mb-2"
        />
        <div class="d-flex align-center mb-2">
          <v-btn
            size="small"
            :variant="mode === 'persona' ? 'tonal' : 'text'"
            class="mr-1"
            @click="mode = 'persona'"
          >
            {{ tm('members.fromPersona') }}
          </v-btn>
          <v-btn
            size="small"
            :variant="mode === 'custom' ? 'tonal' : 'text'"
            class="mr-1"
            @click="mode = 'custom'"
          >
            {{ tm('members.custom') }}
          </v-btn>
        </div>
        <v-select
          v-if="mode === 'persona'"
          v-model="personaId"
          :items="personaItems"
          item-title="title"
          item-value="value"
          :label="tm('members.persona')"
          density="compact"
          hide-details
          class="mb-2"
        />
        <v-textarea
          v-else
          v-model="systemPrompt"
          :label="tm('members.systemPrompt')"
          density="compact"
          rows="3"
          hide-details
          class="mb-2"
        />
        <v-select
          v-model="providerId"
          :items="providerItems"
          item-title="title"
          item-value="value"
          :label="tm('members.provider')"
          density="compact"
          hide-details
        />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="dialog = false">{{ tCommon('cancel') }}</v-btn>
        <v-btn variant="tonal" :loading="saving" @click="save">{{ tCommon('save') }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { agentTeamsApi, personaApi, providerApi } from '@/api/v1';
import { useModuleI18n } from '@/i18n/composables';
import { useToast } from '@/utils/toast';

// NOTE: do not use `defineModel` here — the project's Vue 3.3 SFC compiler
// does not transform that macro, leaving a runtime `undefined` that breaks
// mounting. Classic modelValue/update:modelValue keeps v-model working.
const props = defineProps<{
  modelValue?: boolean;
  teamId: string;
}>();
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
  (e: 'saved', member: any): void;
}>();

const { tm } = useModuleI18n('features/agent-teams');
const { tm: tCommon } = useModuleI18n('core/common');
const toast = useToast();

const dialog = computed({
  get: () => props.modelValue ?? false,
  set: (v: boolean) => emit('update:modelValue', v),
});

const name = ref('');
const mode = ref<'persona' | 'custom'>('persona');
const personaId = ref('');
const systemPrompt = ref('');
const providerId = ref('');
const saving = ref(false);
const personas = ref<any[]>([]);
const providers = ref<any[]>([]);

const personaItems = computed(() =>
  personas.value.map((p) => ({ title: p.persona_id, value: p.persona_id })),
);

const providerItems = computed(() => [
  { title: tm('members.providerDefault'), value: '' },
  ...providers.value.map((p) => ({ title: p.id, value: p.id })),
]);

/** Load persona and chat-completion provider options for the pickers. */
async function loadOptions() {
  try {
    const res = await personaApi.list();
    personas.value = res.data?.status === 'ok' ? res.data.data ?? [] : [];
  } catch {
    personas.value = [];
  }
  try {
    const res = await providerApi.listByProviderType('chat_completion');
    providers.value =
      res.data?.status === 'ok'
        ? ((res.data.data ?? []) as any[]).filter((p) => p.enable !== false)
        : [];
  } catch {
    providers.value = [];
  }
}

/**
 * Normalize the member row into the AgentTeamService payload shape:
 * persona_id and system_prompt are mutually exclusive, an empty provider
 * is omitted entirely.
 */
function memberPayload() {
  const payload: Record<string, unknown> = { name: name.value.trim() };
  if (mode.value === 'persona') {
    if (personaId.value) payload.persona_id = personaId.value;
  } else {
    const prompt = systemPrompt.value.trim();
    if (prompt) payload.system_prompt = prompt;
  }
  const provider = providerId.value.trim();
  if (provider) payload.provider_id = provider;
  return payload;
}

async function save() {
  const memberName = name.value.trim();
  if (!memberName) {
    toast.error(tm('members.nameRequired'));
    return;
  }
  saving.value = true;
  try {
    // Server-side validation arrives as an error envelope with HTTP 200.
    const res = await agentTeamsApi.addMember(props.teamId, memberPayload());
    if (res.data.status === 'error') {
      toast.error(res.data.message || tm('errors.saveFailed'));
      return;
    }
    toast.success(tm('members.addSuccess', { name: memberName }));
    dialog.value = false;
    emit('saved', res.data.data ?? null);
  } catch {
    toast.error(tm('errors.saveFailed'));
  } finally {
    saving.value = false;
  }
}

watch(
  dialog,
  (open) => {
    if (!open) return;
    name.value = '';
    mode.value = 'persona';
    personaId.value = '';
    systemPrompt.value = '';
    providerId.value = '';
    void loadOptions();
  },
  { immediate: true },
);
</script>
