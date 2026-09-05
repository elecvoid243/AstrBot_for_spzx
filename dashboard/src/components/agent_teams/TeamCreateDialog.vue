<template>
  <v-dialog v-model="dialog" max-width="720">
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ isEdit ? tm('teams.edit') : tm('teams.create') }}
      </v-card-title>
      <v-card-text>
        <v-text-field
          v-model="name"
          :label="tm('teams.name')"
          density="compact"
          class="mb-2"
        />

        <!-- Create mode only: member editing stays add-only in v1. -->
        <template v-if="!isEdit">
          <div v-for="(m, i) in members" :key="i" class="member-row">
            <div class="d-flex align-center mb-1">
              <v-text-field
                v-model="m.name"
                :label="tm('members.name')"
                density="compact"
                hide-details
                style="max-width: 180px"
                class="mr-2"
              />
              <v-btn
                size="small"
                :variant="m.mode === 'persona' ? 'tonal' : 'text'"
                class="mr-1"
                @click="m.mode = 'persona'"
              >
                {{ tm('members.fromPersona') }}
              </v-btn>
              <v-btn
                size="small"
                :variant="m.mode === 'custom' ? 'tonal' : 'text'"
                class="mr-1"
                @click="m.mode = 'custom'"
              >
                {{ tm('members.custom') }}
              </v-btn>
              <v-spacer />
              <v-btn
                icon="mdi-close"
                variant="text"
                size="small"
                color="error"
                :aria-label="tm('members.remove')"
                class="member-remove"
                @click="removeMember(i)"
              />
            </div>
            <div class="d-flex align-center mb-2">
              <v-select
                v-if="m.mode === 'persona'"
                v-model="m.persona_id"
                :items="personaItems"
                item-title="title"
                item-value="value"
                :label="tm('members.persona')"
                density="compact"
                hide-details
                class="mr-2"
              />
              <v-textarea
                v-else
                v-model="m.system_prompt"
                :label="tm('members.systemPrompt')"
                density="compact"
                rows="2"
                hide-details
                class="mr-2"
              />
              <v-select
                v-model="m.provider_id"
                :items="providerItems"
                item-title="title"
                item-value="value"
                :label="tm('members.provider')"
                density="compact"
                hide-details
              />
            </div>
          </div>
          <v-btn
            variant="text"
            prepend-icon="mdi-plus"
            :disabled="members.length >= MAX_MEMBERS"
            class="mb-2"
            @click="addMember"
          >
            {{ tm('members.add') }}
          </v-btn>
        </template>

        <v-select
          v-model="coordinator"
          :items="coordinatorItems"
          item-title="title"
          item-value="value"
          :label="tm('teams.coordinator')"
          density="compact"
          hide-details
          class="mb-2"
        />

        <v-btn variant="text" block @click="configOpen = !configOpen">
          <v-icon>{{ configOpen ? 'mdi-chevron-up' : 'mdi-chevron-down' }}</v-icon>
          {{ tm('teams.config') }}
        </v-btn>
        <div v-if="configOpen" class="config-grid">
          <v-select
            v-model="config.failure_policy"
            :items="failurePolicyItems"
            item-title="title"
            item-value="value"
            :label="tm('teams.failurePolicy')"
            density="compact"
            hide-details
          />
          <v-text-field
            v-model="config.reply_timeout"
            type="number"
            :label="tm('teams.replyTimeout')"
            density="compact"
            hide-details
          />
          <v-text-field
            v-model="config.max_rounds"
            type="number"
            :label="tm('teams.maxRounds')"
            density="compact"
            hide-details
          />
          <v-text-field
            v-model="config.max_parallel"
            type="number"
            :label="tm('teams.maxParallel')"
            density="compact"
            hide-details
          />
          <v-text-field
            v-model="config.inject_max_length"
            type="number"
            :label="tm('teams.injectMaxLength')"
            density="compact"
            hide-details
          />
        </div>
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
import { personaApi, providerApi } from '@/api/v1';
import { useAgentTeams } from '@/composables/useAgentTeams';
import { useModuleI18n } from '@/i18n/composables';
import { useToast } from '@/utils/toast';

// NOTE: do not use `defineModel` here — the project's Vue 3.3 SFC compiler
// does not transform that macro, leaving a runtime `undefined` that breaks
// mounting. Classic modelValue/update:modelValue keeps v-model working.
const props = defineProps<{
  modelValue?: boolean;
  /** Present team switches the dialog into edit mode (name/config/coordinator only). */
  team?: any | null;
}>();
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
  (e: 'saved', team: any): void;
}>();

const MAX_MEMBERS = 10; // mirrors AgentTeamService.MAX_MEMBERS
const DEFAULT_CONFIG = {
  failure_policy: 'pause',
  reply_timeout: 600,
  max_rounds: 20,
  max_parallel: 5,
  inject_max_length: 4000,
};

interface MemberDraft {
  name: string;
  mode: 'persona' | 'custom';
  persona_id: string;
  system_prompt: string;
  provider_id: string;
}

const { tm } = useModuleI18n('features/agent-teams');
const { tm: tCommon } = useModuleI18n('core/common');
const toast = useToast();
const { createTeam, updateTeam } = useAgentTeams();

const dialog = computed({
  get: () => props.modelValue ?? false,
  set: (v: boolean) => emit('update:modelValue', v),
});

const isEdit = computed(() => !!editingTeam.value);
const editingTeam = ref<any | null>(null);
const name = ref('');
const members = ref<MemberDraft[]>([]);
const coordinator = ref('');
const config = ref({ ...DEFAULT_CONFIG });
const configOpen = ref(false);
const saving = ref(false);
const personas = ref<any[]>([]);
const providers = ref<any[]>([]);

function emptyMember(): MemberDraft {
  return { name: '', mode: 'persona', persona_id: '', system_prompt: '', provider_id: '' };
}

const personaItems = computed(() =>
  personas.value.map((p) => ({ title: p.persona_id, value: p.persona_id })),
);

const providerItems = computed(() => [
  { title: tm('members.providerDefault'), value: '' },
  ...providers.value.map((p) => ({ title: p.id, value: p.id })),
]);

const failurePolicyItems = computed(() => [
  { title: tm('teams.failurePolicyPause'), value: 'pause' },
  { title: tm('teams.failurePolicyAutoSkip'), value: 'auto_skip' },
]);

const coordinatorItems = computed(() => {
  const source = isEdit.value
    ? (editingTeam.value?.members ?? [])
    : members.value;
  return source
    .filter((m: any) => (m.name ?? '').trim())
    .map((m: any) => ({ title: m.name, value: m.name }));
});

function addMember() {
  if (members.value.length >= MAX_MEMBERS) return;
  members.value.push(emptyMember());
}

function removeMember(index: number) {
  members.value.splice(index, 1);
  const names = members.value.map((m) => m.name);
  if (!names.includes(coordinator.value)) {
    coordinator.value = names[0] ?? '';
  }
}

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
 * Normalize one member row into the AgentTeamService payload shape:
 * persona_id and system_prompt are mutually exclusive, an empty provider
 * is omitted entirely.
 */
function memberPayload(m: MemberDraft) {
  const payload: Record<string, unknown> = { name: m.name.trim() };
  if (m.mode === 'persona') {
    if (m.persona_id) payload.persona_id = m.persona_id;
  } else {
    const prompt = m.system_prompt.trim();
    if (prompt) payload.system_prompt = prompt;
  }
  const provider = (m.provider_id ?? '').trim();
  if (provider) payload.provider_id = provider;
  return payload;
}

function configPayload() {
  return {
    failure_policy: config.value.failure_policy,
    reply_timeout: Number(config.value.reply_timeout),
    max_rounds: Number(config.value.max_rounds),
    max_parallel: Number(config.value.max_parallel),
    inject_max_length: Number(config.value.inject_max_length),
  };
}

async function save() {
  const teamName = name.value.trim();
  if (!teamName) {
    toast.error(tm('teams.nameRequired'));
    return;
  }
  if (!isEdit.value) {
    if (members.value.length < 2) {
      toast.error(tm('members.empty'));
      return;
    }
    if (members.value.some((m) => !m.name.trim())) {
      toast.error(tm('members.nameRequired'));
      return;
    }
  }
  saving.value = true;
  try {
    // Both actions toast the error envelope themselves and resolve to null;
    // returning early here guards against a second toast from this dialog.
    // The cast is needed because create payloads carry members without
    // member_id — the server assigns those (AgentTeamMember requires it).
    const result = isEdit.value
      ? await updateTeam(editingTeam.value.team_id, {
          name: teamName,
          coordinator: coordinator.value,
          config: configPayload(),
        })
      : await createTeam({
          name: teamName,
          members: members.value.map(memberPayload),
          coordinator: coordinator.value || members.value[0]?.name.trim() || '',
          config: configPayload(),
        } as any);
    if (result === null) return;
    toast.success(
      isEdit.value
        ? tm('teams.updateSuccess', { name: teamName })
        : tm('teams.createSuccess', { name: teamName }),
    );
    dialog.value = false;
    emit('saved', result);
  } finally {
    saving.value = false;
  }
}

watch(
  dialog,
  (open) => {
    if (!open) return;
    const team = props.team ?? null;
    editingTeam.value = team;
    name.value = team?.name ?? '';
    members.value = team ? [] : [emptyMember(), emptyMember()];
    coordinator.value = team
      ? (team.members ?? []).find(
            (m: any) => m.member_id === team.coordinator_member_id,
          )?.name ?? team.members?.[0]?.name ?? ''
      : '';
    config.value = { ...DEFAULT_CONFIG, ...(team?.config ?? {}) };
    void loadOptions();
  },
  { immediate: true },
);
</script>

<style scoped>
.member-row {
  padding: 8px;
  border: 1px solid var(--dashboard-border, rgba(0, 0, 0, 0.08));
  border-radius: 8px;
  margin-bottom: 8px;
}

.config-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 8px;
  padding-top: 8px;
}
</style>
