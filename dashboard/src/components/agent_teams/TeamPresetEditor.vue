<template>
  <v-expansion-panels v-model="open" class="mb-4">
    <v-expansion-panel value="details">
      <v-expansion-panel-title>
        <div class="preset-summary">
          <strong data-testid="preset-name"
            >{{ title }} ·
            {{
              preset.role?.name ||
              preview?.effective?.name ||
              preview?.definition?.name ||
              tm("graph.previewLoading")
            }}</strong
          >
          <div class="text-caption">
            {{
              preset.role
                ? tm("graph.directSource")
                : `${tm("graph.templateSource")} v${preset.role_version}`
            }}
            ·
            <span data-testid="preset-model"
              >{{ tm("roles.model") }}:
              {{
                preview
                  ? preview.effective?.provider_id || tm("graph.noModel")
                  : tm("graph.previewLoading")
              }}</span
            >
            · {{ tm("roles.tools") }}:
            {{
              (preset.role ?? preset.overrides)?.tools == null
                ? tm("roles.inherit")
                : (preset.role ?? preset.overrides).tools.length ||
                  tm("graph.resourcesDisabled")
            }}
          </div>
        </div>
      </v-expansion-panel-title>
      <v-expansion-panel-text>
        <v-btn v-if="removable" variant="text" @click="$emit('remove')">{{
          tm("autonomous.removePreset")
        }}</v-btn>
        <v-select
          :model-value="preset.role ? 'inline' : 'template'"
          :items="[
            { title: tm('graph.directSource'), value: 'inline' },
            { title: tm('graph.templateSource'), value: 'template' },
          ]"
          :label="tm('graph.roleSource')"
          @update:model-value="changeSource"
        />
        <template v-if="preset.role && preset.installation">
          <v-text-field v-model="preset.role.name" :label="tm('roles.name')" />
          <v-textarea
            v-model="preset.role.prompt"
            :label="tm('roles.prompt')"
          />
          <v-select
            v-model="preset.installation.profile_id"
            :items="catalog.profiles"
            item-title="name"
            item-value="id"
            :label="tm('roles.profile')"
          />
          <v-select
            v-model="preset.role.provider_id"
            :items="catalog.models"
            item-title="name"
            item-value="id"
            :label="tm('graph.modelOverride')"
            clearable
          />
          <v-select
            v-model="preset.role.persona_id"
            :items="catalog.personas"
            item-title="name"
            item-value="id"
            :label="tm('roles.localPersona')"
            clearable
          />
        </template>
        <template v-else>
          <v-select
            v-model="preset.role_id"
            :items="roleItems"
            :label="tm('graph.templateSource')"
            @update:model-value="
              preset.role_version = roleById($event)?.version ?? 1
            "
          />
          <v-select
            v-model="preset.role_version"
            :items="roleById(preset.role_id)?.versions ?? []"
            :label="tm('v2.roleVersion')"
          />
          <template
            v-for="field in ['prompt', 'provider_id', 'persona_id']"
            :key="field"
          >
            <v-switch
              :model-value="
                Object.prototype.hasOwnProperty.call(preset.overrides, field)
              "
              :label="`${tm('roles.override')}: ${tm(
                field === 'prompt'
                  ? 'roles.prompt'
                  : field === 'provider_id'
                  ? 'graph.modelOverride'
                  : 'roles.localPersona',
              )}`"
              @update:model-value="setOverride(field, $event)"
            />
            <v-textarea
              v-if="field === 'prompt' && field in preset.overrides"
              v-model="preset.overrides[field]"
              :label="tm('roles.prompt')"
            />
            <v-select
              v-else-if="field !== 'prompt' && field in preset.overrides"
              v-model="preset.overrides[field]"
              :items="
                field === 'provider_id' ? catalog.models : catalog.personas
              "
              item-title="name"
              item-value="id"
              clearable
              :label="
                tm(
                  field === 'provider_id'
                    ? 'graph.modelOverride'
                    : 'roles.localPersona',
                )
              "
            />
          </template>
        </template>
        <v-expansion-panels multiple>
          <RoleResourcesEditor
            :definition="preset.role ?? preset.overrides"
            :modes="modes"
            :catalog="catalog"
          />
          <v-expansion-panel>
            <v-expansion-panel-title>{{
              tm("roles.limits")
            }}</v-expansion-panel-title>
            <v-expansion-panel-text>
              <v-text-field
                v-for="field in [
                  'max_instances',
                  'max_parallel',
                  'max_steps',
                  'max_iterations',
                  'max_depth',
                ]"
                :key="field"
                type="number"
                min="1"
                :model-value="(preset.role ?? preset.overrides)[field]"
                :label="tm(`roles.${field}`)"
                clearable
                @update:model-value="setLimit(field, $event)"
              />
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
        <div v-if="preview" class="mt-3">
          <strong>{{ tm("graph.effectiveRole") }}</strong>
          <p
            v-for="field in [
              'prompt',
              'provider_id',
              'persona_id',
              'tools',
              'skills',
              'knowledge_bases',
            ]"
            :key="field"
            class="text-caption preview"
          >
            {{ tm(`roles.${field}`) }}:
            {{
              Array.isArray(preview.effective[field])
                ? preview.effective[field].join(", ") ||
                  tm("graph.resourcesDisabled")
                : preview.effective[field] || tm("roles.none")
            }}
            <span v-if="preview.sources?.[field]">
              ({{ preview.sources[field] }})</span
            >
          </p>
          <v-alert
            v-if="preview.issues?.length"
            type="warning"
            variant="tonal"
            >{{
              preview.issues.map((issue: any) => issue.message).join("; ")
            }}</v-alert
          >
        </div>
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</template>
<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import { roleAssetsApi } from "@/api/roleAssets";
import RoleResourcesEditor from "./RoleResourcesEditor.vue";
const props = defineProps<{
  preset: any;
  title: string;
  removable?: boolean;
  roles: any[];
  catalog: Record<string, any[]>;
  defaultProvider?: string | null;
  initiallyOpen?: boolean;
}>();
defineEmits<{ remove: [] }>();
const { tm } = useModuleI18n("features/agent-teams");
const open = ref<string | undefined>(
  props.initiallyOpen ? "details" : undefined,
);
const modes = ref<Record<string, string>>({});
const roleItems = computed(() =>
  props.roles.map((role) => ({
    title: role.definition.name,
    value: role.role_id,
  })),
);
const roleById = (id: string) =>
  props.roles.find((role) => role.role_id === id);
const preview = ref<any>(null);
let generation = 0;
watch(
  () => [props.preset.role, props.preset.overrides],
  () => {
    for (const field of ["tools", "skills", "knowledge_bases"])
      modes.value[field] =
        (props.preset.role ?? props.preset.overrides)?.[field] == null
          ? "inherit"
          : "custom";
  },
  { immediate: true },
);
watch(
  () =>
    JSON.stringify([
      props.preset.role_id,
      props.preset.role_version,
      props.preset.role,
      props.preset.installation,
      props.preset.overrides,
      props.defaultProvider,
    ]),
  async () => {
    const request = ++generation;
    preview.value = null;
    try {
      if (props.preset.role && props.preset.installation) {
        const definition = {
          ...props.preset.role,
          provider_id:
            props.preset.role.provider_id || props.defaultProvider || null,
        };
        const result = await roleAssetsApi.preview({
          definition,
          installation: props.preset.installation,
        });
        if (request === generation) preview.value = result;
        return;
      }
      if (!props.preset.role_id || !props.preset.role_version) return;
      let result = await roleAssetsApi.resolve(
        props.preset.role_id,
        props.preset.role_version,
        props.preset.overrides,
      );
      if (
        props.defaultProvider &&
        result.effective.runner.type === "local" &&
        !result.effective.provider_id
      )
        result = await roleAssetsApi.resolve(
          props.preset.role_id,
          props.preset.role_version,
          { ...props.preset.overrides, provider_id: props.defaultProvider },
        );
      if (request === generation) preview.value = result;
    } catch {
      if (request === generation) preview.value = null;
    }
  },
  { immediate: true },
);
function changeSource(source: string) {
  if (source === "inline") {
    delete props.preset.role_id;
    delete props.preset.role_version;
    props.preset.role = { name: props.title };
    props.preset.installation = { profile_id: "default" };
    props.preset.overrides = {};
  } else {
    delete props.preset.role;
    delete props.preset.installation;
    props.preset.role_id = props.roles[0]?.role_id ?? "";
    props.preset.role_version = props.roles[0]?.version ?? 1;
    props.preset.overrides = {};
  }
}
function setLimit(field: string, value: string | null) {
  const target = props.preset.role ?? props.preset.overrides;
  if (value == null || value === "") delete target[field];
  else target[field] = Number(value);
}
function setOverride(field: string, enabled: boolean | null) {
  if (enabled) props.preset.overrides[field] = field === "prompt" ? "" : null;
  else delete props.preset.overrides[field];
}
</script>
<style scoped>
.preset-summary {
  overflow-wrap: anywhere;
  padding-right: 12px;
}
.preview {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
