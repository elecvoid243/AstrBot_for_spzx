<template>
  <div class="member-config-form">
    <div class="member-form-grid">
      <v-text-field
        v-model="name"
        :label="tm('memberConfig.name')"
        density="compact"
        hide-details
      />
      <v-select
        v-model="configId"
        :items="configProfileItems"
        item-title="title"
        item-value="value"
        :label="tm('memberConfig.configProfile')"
        density="compact"
        hide-details
      />
      <div class="member-form-persona">
        <span class="member-form-label">{{ tm('memberConfig.persona') }}</span>
        <PersonaSelector v-model="personaId" />
        <p class="member-form-hint">{{ tm('memberConfig.personaHint') }}</p>
      </div>
      <v-select
        v-model="providerId"
        :items="providerItems"
        item-title="title"
        item-value="value"
        :label="tm('memberConfig.provider')"
        density="compact"
        hide-details
      />
    </div>

    <!-- Tools / skills overrides only apply once a persona is pinned: they
         describe how the member's runner filters the persona capabilities. -->
    <section v-if="personaId.trim()" class="member-form-section">
      <span class="member-form-section-title">{{ tm('memberConfig.tools') }}</span>
      <v-radio-group v-model="toolsMode" density="compact" hide-details>
        <v-radio :label="tm('memberConfig.toolsInherit')" value="inherit" density="compact" />
        <v-radio
          :label="tm('memberConfig.toolsDisableAll')"
          value="disable_all"
          density="compact"
        />
        <v-radio :label="tm('editor.toolsAllowlist')" value="allowlist" density="compact" />
      </v-radio-group>
      <div v-if="toolsMode === 'allowlist'" class="member-form-checkboxes">
        <v-checkbox-btn
          v-for="opt in toolOptions"
          :key="opt.value"
          :model-value="tools.includes(opt.value)"
          :label="opt.title"
          density="compact"
          hide-details
          @update:model-value="toggleTool(opt.value)"
        />
      </div>
    </section>

    <section v-if="personaId.trim()" class="member-form-section">
      <span class="member-form-section-title">{{ tm('memberConfig.skills') }}</span>
      <v-radio-group v-model="skillsMode" density="compact" hide-details>
        <v-radio :label="tm('memberConfig.skillsInherit')" value="inherit" density="compact" />
        <v-radio
          :label="tm('memberConfig.skillsDisableAll')"
          value="disable_all"
          density="compact"
        />
        <v-radio :label="tm('editor.skillsAllowlist')" value="allowlist" density="compact" />
      </v-radio-group>
      <div v-if="skillsMode === 'allowlist'" class="member-form-checkboxes">
        <v-checkbox-btn
          v-for="opt in skillOptions"
          :key="opt.value"
          :model-value="skills.includes(opt.value)"
          :label="opt.title"
          density="compact"
          hide-details
          @update:model-value="toggleSkill(opt.value)"
        />
      </div>
    </section>

    <div class="member-form-grid">
      <v-text-field
        v-model="maxSteps"
        type="number"
        :label="tm('memberConfig.maxSteps')"
        density="compact"
        hide-details
      />
      <v-text-field
        v-model="toolCallTimeout"
        type="number"
        :label="tm('memberConfig.toolCallTimeout')"
        density="compact"
        hide-details
      />
      <v-text-field
        v-model="contextLength"
        type="number"
        :label="tm('memberConfig.contextLength')"
        density="compact"
        hide-details
      />
    </div>

    <section class="member-form-section">
      <span class="member-form-section-title">{{ tm('memberConfig.knowledgeBase') }}</span>
      <KnowledgeBaseSelector v-model="kbNames" />
      <p v-if="kbNames.length === 0" class="member-form-hint">
        {{ tm('memberConfig.kbNone') }}
      </p>
    </section>

    <div class="member-form-actions">
      <v-spacer />
      <v-btn
        variant="tonal"
        color="primary"
        data-test="member-config-save"
        :loading="saving"
        @click="save"
      >
        {{ tm('memberConfig.save') }}
      </v-btn>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import PersonaSelector from '@/components/shared/PersonaSelector.vue';
import KnowledgeBaseSelector from '@/components/shared/KnowledgeBaseSelector.vue';
import { agentTeamsApi, configProfileApi, providerApi, skillApi, toolApi } from '@/api/v1';
import { useModuleI18n } from '@/i18n/composables';
import { extractApiError } from '@/utils/extractApiError';
import { useToast } from '@/utils/toast';

// NOTE: do not use `defineModel` here — the project's Vue 3.3 SFC compiler
// does not transform that macro, leaving a runtime `undefined` that breaks
// mounting. Classic modelValue/update:modelValue keeps v-model working.
const props = defineProps<{
  /** The member row being edited (id/name/persona/provider/runner_config). */
  member: any;
  /** Owning team; provides team_id for the PATCH target. */
  team: any;
}>();
const emit = defineEmits<{
  (e: 'saved', team: any): void;
}>();

/** Tools/skills override modes (mirrors WorkflowEditor exec semantics):
 * `inherit` follows the persona, `disable_all` sends `[]`, `allowlist` sends
 * the checked names. */
type CapabilityMode = 'inherit' | 'disable_all' | 'allowlist';

const { tm } = useModuleI18n('features/agent-teams');
const toast = useToast();

const name = ref('');
const configId = ref('');
const personaId = ref('');
const providerId = ref('');
const toolsMode = ref<CapabilityMode>('inherit');
const tools = ref<string[]>([]);
const skillsMode = ref<CapabilityMode>('inherit');
const skills = ref<string[]>([]);
const maxSteps = ref('');
const toolCallTimeout = ref('');
const contextLength = ref('');
const kbNames = ref<string[]>([]);
const saving = ref(false);

const configProfileOptions = ref<{ title: string; value: string }[]>([]);
const toolOptions = ref<{ title: string; value: string }[]>([]);
const skillOptions = ref<{ title: string; value: string }[]>([]);
const providerOptions = ref<{ title: string; value: string }[]>([]);

/** Profile dropdown: the "follow session default" empty option plus profiles. */
const configProfileItems = computed(() => [
  { title: tm('memberConfig.configProfileDefault'), value: '' },
  ...configProfileOptions.value,
]);

/** Provider dropdown: the "session default model" empty option plus providers. */
const providerItems = computed(() => [
  { title: tm('memberConfig.providerDefault'), value: '' },
  ...providerOptions.value,
]);

/**
 * Parse the stored capability list into an override mode.
 *
 * Args:
 *   value: The stored `tools`/`skills` value from runner_config.
 *
 * Returns:
 *   The mode and prefilled items: absent/None = follow, `[]` = disable all,
 *   a non-empty list = allowlist (backend node execution semantics).
 */
function parseCapabilityList(value: unknown): { mode: CapabilityMode; items: string[] } {
  if (!Array.isArray(value)) return { mode: 'inherit', items: [] };
  const items = value.map((item) => String(item)).filter((item) => item.trim());
  return items.length === 0
    ? { mode: 'disable_all', items: [] }
    : { mode: 'allowlist', items };
}

/** Seed the draft from the selected member row (reset on member switch). */
function seedMember() {
  const member = props.member ?? null;
  const rc = (member?.runner_config ?? {}) as Record<string, unknown>;
  name.value = member?.name ?? '';
  configId.value = typeof rc.config_id === 'string' ? rc.config_id : '';
  personaId.value = member?.persona_id ?? '';
  providerId.value = member?.provider_id ?? '';
  const toolsParsed = parseCapabilityList(rc.tools);
  toolsMode.value = toolsParsed.mode;
  tools.value = [...toolsParsed.items];
  const skillsParsed = parseCapabilityList(rc.skills);
  skillsMode.value = skillsParsed.mode;
  skills.value = [...skillsParsed.items];
  maxSteps.value = rc.max_steps != null ? String(rc.max_steps) : '';
  toolCallTimeout.value = rc.tool_call_timeout != null ? String(rc.tool_call_timeout) : '';
  contextLength.value = rc.context_length != null ? String(rc.context_length) : '';
  kbNames.value = Array.isArray(rc.kb_names) ? rc.kb_names.map(String) : [];
}

/** Load profile/tool/skill/provider options once; each fails independently. */
async function loadOptions() {
  try {
    const res = await configProfileApi.list();
    if (res.data?.status === 'ok') {
      configProfileOptions.value = ((res.data.data?.info_list ?? []) as any[]).map((p) => ({
        title: String(p.name || p.id || ''),
        value: String(p.id || ''),
      }));
    }
  } catch (err) {
    toast.error(extractApiError(err, tm('memberConfig.loadFailed')).message);
  }
  try {
    const res = await toolApi.list();
    if (res.data?.status === 'ok') {
      toolOptions.value = ((res.data.data ?? []) as any[])
        .filter((t) => t && t.name && t.active !== false)
        .map((t) => ({ title: String(t.name), value: String(t.name) }));
    }
  } catch (err) {
    toast.error(extractApiError(err, tm('memberConfig.loadFailed')).message);
  }
  try {
    const res = await skillApi.list();
    if (res.data?.status === 'ok') {
      const payload = res.data.data ?? [];
      const skills = Array.isArray(payload) ? payload : (payload.skills ?? []);
      skillOptions.value = (skills as any[])
        .filter((s) => s && s.name && s.active !== false)
        .map((s) => ({ title: String(s.name), value: String(s.name) }));
    }
  } catch (err) {
    toast.error(extractApiError(err, tm('memberConfig.loadFailed')).message);
  }
  try {
    const res = await providerApi.listByProviderType('chat_completion');
    if (res.data?.status === 'ok') {
      providerOptions.value = ((res.data.data ?? []) as any[])
        .filter((p) => p.enable !== false)
        .map((p) => ({ title: String(p.id), value: String(p.id) }));
    }
  } catch (err) {
    toast.error(extractApiError(err, tm('memberConfig.loadFailed')).message);
  }
}

onMounted(() => {
  void loadOptions();
});

watch(
  () => props.member?.member_id,
  () => seedMember(),
  { immediate: true },
);

/** Toggle one allowlist checkbox for tools. */
function toggleTool(tool: string) {
  const index = tools.value.indexOf(tool);
  if (index === -1) tools.value.push(tool);
  else tools.value.splice(index, 1);
}

/** Toggle one allowlist checkbox for skills. */
function toggleSkill(skill: string) {
  const index = skills.value.indexOf(skill);
  if (index === -1) skills.value.push(skill);
  else skills.value.splice(index, 1);
}

/** Coerce a numeric input to a number, keeping empty/invalid as omitted. */
function numericOrUndefined(value: string): number | undefined {
  const text = value.trim();
  if (text === '') return undefined;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * Build the PATCH payload: the runner_config block is sent in full (the
 * backend replaces the stored block wholesale), with empty fields omitted —
 * except `kb_names`, the disable-all capability markers (`[]` is meaningful),
 * and the persona/provider pins, which are always sent so a clear arrives as
 * an explicit `null` instead of being silently dropped.
 */
function buildPayload(): Record<string, unknown> {
  const payload: Record<string, unknown> = { name: name.value.trim() };
  payload.persona_id = personaId.value.trim() || null;
  payload.provider_id = providerId.value.trim() || null;
  const systemPrompt = String(props.member?.system_prompt ?? '').trim();
  if (systemPrompt) payload.system_prompt = systemPrompt;

  const runnerConfig: Record<string, unknown> = {};
  if (configId.value.trim()) runnerConfig.config_id = configId.value.trim();
  if (toolsMode.value === 'disable_all') runnerConfig.tools = [];
  else if (toolsMode.value === 'allowlist') runnerConfig.tools = [...tools.value];
  if (skillsMode.value === 'disable_all') runnerConfig.skills = [];
  else if (skillsMode.value === 'allowlist') runnerConfig.skills = [...skills.value];
  const maxStepsValue = numericOrUndefined(maxSteps.value);
  if (maxStepsValue !== undefined) runnerConfig.max_steps = maxStepsValue;
  const timeoutValue = numericOrUndefined(toolCallTimeout.value);
  if (timeoutValue !== undefined) runnerConfig.tool_call_timeout = timeoutValue;
  const contextValue = numericOrUndefined(contextLength.value);
  if (contextValue !== undefined) runnerConfig.context_length = contextValue;
  runnerConfig.kb_names = [...kbNames.value];
  payload.runner_config = runnerConfig;
  return payload;
}

/** Save via the Task 4 facade: resolves the updated team, rejects on errors. */
async function save() {
  if (!name.value.trim()) {
    toast.error(tm('members.nameRequired'));
    return;
  }
  saving.value = true;
  try {
    const team = await agentTeamsApi.updateMember(
      props.team.team_id,
      props.member.member_id,
      buildPayload(),
    );
    toast.success(tm('memberConfig.saveSuccess'));
    emit('saved', team);
  } catch (err) {
    toast.error(extractApiError(err, tm('memberConfig.saveFailed')).message);
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.member-config-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.member-form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  align-items: start;
}

.member-form-persona {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.member-form-label {
  font-size: 12px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
}

.member-form-hint {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
}

.member-form-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.member-form-section-title {
  font-size: 13px;
  font-weight: 600;
}

.member-form-checkboxes {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
}

.member-form-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
}
</style>
