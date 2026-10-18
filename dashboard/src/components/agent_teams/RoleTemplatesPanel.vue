<template>
  <section :aria-label="tm('roles.title')">
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <h2 class="text-h5">{{ tm("roles.title") }}</h2>
      <v-btn
        data-testid="new-role"
        variant="tonal"
        color="primary"
        @click="edit()"
        >{{ tm("roles.create") }}</v-btn
      >
      <v-btn variant="text" :loading="loading" @click="load">{{
        tm("page.refresh")
      }}</v-btn>
    </div>
    <p class="text-body-2 mb-4">{{ tm("roles.intro") }}</p>
    <v-alert v-if="loadError" type="error" variant="tonal" class="mb-4">{{
      loadError
    }}</v-alert>
    <p v-if="loading" role="status">{{ tm("roles.loading") }}</p>
    <p v-else-if="!roles.length && !loadError">{{ tm("roles.empty") }}</p>
    <div class="role-grid">
      <v-card v-for="role in roles" :key="role.role_id" variant="outlined">
        <v-card-text>
          <h3 class="role-name text-h6">{{ role.definition.name }}</h3>
          <v-chip
            size="x-small"
            variant="outlined"
            class="my-2"
            prepend-icon="mdi-account-outline"
            >{{ tm("workbench.template") }}</v-chip
          >
          <p class="text-caption">
            {{ tm("roles.version") }} {{ role.version }}
          </p>
          <p class="role-name my-2">
            {{
              role.definition.description ||
              role.definition.prompt ||
              tm("roles.personaPrompt")
            }}
          </p>
          <v-chip
            size="small"
            :color="role.readiness?.ready ? 'success' : 'warning'"
            variant="tonal"
          >
            {{
              role.readiness?.ready ? tm("roles.ready") : tm("roles.pending")
            }}
          </v-chip>
          <p
            v-if="
              role.readiness?.effective?.runner?.type &&
              role.readiness.effective.runner.type !== 'local'
            "
            class="text-caption mt-2"
          >
            {{ tm("runner.title") }}:
            {{ role.readiness.effective.runner.label }}
          </p>
          <p v-else class="text-caption mt-2">
            {{ tm("roles.model") }}:
            {{
              role.installation.bindings[role.definition.model_slot] ||
              tm("roles.unbound")
            }}
          </p>
        </v-card-text>
        <v-card-actions
          ><v-btn variant="text" @click="edit(role)">{{
            tm("roles.edit")
          }}</v-btn></v-card-actions
        >
      </v-card>
    </div>

    <v-dialog
      v-model="dialog"
      class="workbench-dialog"
      max-width="1100"
      scrollable
      :fullscreen="mobile"
    >
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          currentId ? tm("roles.edit") : tm("roles.create")
        }}</v-card-title>
        <v-card-text>
          <v-alert
            v-if="errorMessage"
            type="error"
            variant="tonal"
            class="mb-4"
            >{{ errorMessage }}</v-alert
          >
          <v-alert v-if="currentId" type="info" variant="tonal" class="mb-4">{{
            tm("roles.versionNotice")
          }}</v-alert>
          <v-select
            v-if="versions.length"
            :model-value="selectedVersion"
            :items="versions"
            :label="tm('roles.history')"
            :aria-label="tm('roles.history')"
            @update:model-value="openVersion"
          />
          <div class="role-editor">
            <div>
              <v-text-field
                data-testid="role-name"
                v-model="definition.name"
                :label="tm('roles.name')"
                :error-messages="fieldErrors('definition.name')"
              />
              <v-textarea
                v-model="definition.description"
                :label="tm('roles.description')"
                rows="2"
              />
              <v-expansion-panels v-model="groups" multiple>
                <v-expansion-panel value="responsibility">
                  <v-expansion-panel-title>{{
                    tm("roles.responsibility")
                  }}</v-expansion-panel-title>
                  <v-expansion-panel-text>
                    <v-select
                      v-model="promptMode"
                      :items="promptModes"
                      :label="tm('roles.promptSource')"
                      :aria-label="tm('roles.promptSource')"
                    />
                    <v-textarea
                      v-if="promptMode === 'inline'"
                      data-testid="role-prompt"
                      v-model="definition.prompt"
                      :label="tm('roles.prompt')"
                      rows="4"
                      :error-messages="fieldErrors('definition.prompt')"
                    />
                    <v-text-field
                      v-model="definition.persona_slot"
                      :label="tm('roles.personaSlot')"
                      clearable
                    />
                    <v-select
                      v-if="definition.persona_slot"
                      v-model="installation.bindings[definition.persona_slot]"
                      :items="catalog.personas"
                      item-title="name"
                      item-value="id"
                      :label="tm('roles.localPersona')"
                      :aria-label="tm('roles.localPersona')"
                      clearable
                      :error-messages="
                        fieldErrors(
                          `installation.bindings.${definition.persona_slot}`,
                        )
                      "
                    />
                    <p class="text-caption">{{ tm("roles.exportNotice") }}</p>
                  </v-expansion-panel-text>
                </v-expansion-panel>
                <v-expansion-panel value="model">
                  <v-expansion-panel-title>{{
                    tm("roles.model")
                  }}</v-expansion-panel-title>
                  <v-expansion-panel-text>
                    <v-select
                      v-model="installation.profile_id"
                      :items="catalog.profiles"
                      item-title="name"
                      item-value="id"
                      :label="tm('roles.profile')"
                      :aria-label="tm('roles.profile')"
                      :error-messages="fieldErrors('installation.profile_id')"
                    />
                    <v-text-field
                      v-if="
                        (selectedProfile?.runner_type ?? 'local') === 'local'
                      "
                      v-model="definition.model_slot"
                      :label="tm('roles.modelSlot')"
                      :error-messages="fieldErrors('definition.model_slot')"
                    />
                    <v-select
                      v-if="
                        (selectedProfile?.runner_type ?? 'local') === 'local'
                      "
                      v-model="installation.bindings[definition.model_slot]"
                      :items="catalog.models"
                      item-title="name"
                      item-value="id"
                      :label="tm('roles.localModel')"
                      :aria-label="tm('roles.localModel')"
                      clearable
                      :error-messages="
                        fieldErrors(
                          `installation.bindings.${definition.model_slot}`,
                        )
                      "
                    />
                    <v-select
                      v-model="definition.model_capabilities"
                      :items="['tool_use', 'image', 'audio']"
                      multiple
                      chips
                      :label="tm('roles.modelCapabilities')"
                      :aria-label="tm('roles.modelCapabilities')"
                    />
                    <v-select
                      data-testid="runner-requirements"
                      v-model="definition.runner_capabilities"
                      :items="
                        [
                          'internal_checkpoint',
                          'remote_query',
                          'task_tools',
                          'task_result',
                        ].map((value) => ({
                          value,
                          title: tm(`runner.capabilities.${value}`),
                        }))
                      "
                      multiple
                      chips
                      :label="tm('runner.requirements')"
                      :aria-label="tm('runner.requirements')"
                      :error-messages="
                        fieldErrors('definition.runner_capabilities')
                      "
                    />
                  </v-expansion-panel-text>
                </v-expansion-panel>
                <v-expansion-panel
                  v-for="field in resourceFields"
                  :key="field"
                  :value="field"
                >
                  <v-expansion-panel-title>{{
                    tm(`roles.${field}`)
                  }}</v-expansion-panel-title>
                  <v-expansion-panel-text>
                    <v-select
                      v-model="modes[field]"
                      :items="capabilityModes"
                      :label="tm('roles.mode')"
                      :aria-label="`${tm(`roles.${field}`)}: ${tm(
                        'roles.mode',
                      )}`"
                    />
                    <template v-if="modes[field] === 'custom'">
                      <v-combobox
                        v-model="definition[field]"
                        multiple
                        chips
                        closable-chips
                        :label="tm('roles.logicalSlots')"
                        :aria-label="`${tm(`roles.${field}`)}: ${tm(
                          'roles.logicalSlots',
                        )}`"
                        :error-messages="fieldErrors(`definition.${field}`)"
                      />
                      <v-select
                        v-for="slot in definition[field] || []"
                        :key="slot"
                        v-model="installation.bindings[slot]"
                        :items="catalog[field]"
                        item-title="name"
                        item-value="id"
                        :label="`${tm('roles.binding')}: ${slot}`"
                        :aria-label="`${tm('roles.binding')}: ${slot}`"
                        clearable
                        :error-messages="
                          fieldErrors(`installation.bindings.${slot}`)
                        "
                      />
                    </template>
                  </v-expansion-panel-text>
                </v-expansion-panel>
                <v-expansion-panel value="limits">
                  <v-expansion-panel-title>{{
                    tm("roles.limits")
                  }}</v-expansion-panel-title>
                  <v-expansion-panel-text>
                    <p class="text-caption mb-3">
                      {{ tm("roles.limitsNotice") }}
                    </p>
                    <v-text-field
                      v-for="field in limitFields"
                      :key="field"
                      v-model="definition[field]"
                      type="number"
                      :label="tm(`roles.${field}`)"
                      :placeholder="tm('roles.inherit')"
                      :error-messages="fieldErrors(`definition.${field}`)"
                      clearable
                    />
                  </v-expansion-panel-text>
                </v-expansion-panel>
              </v-expansion-panels>
            </div>
            <aside :aria-label="tm('roles.effective')">
              <h3 class="text-h6 mb-3">{{ tm("roles.effective") }}</h3>
              <p v-if="previewStale" role="status" class="mb-3">
                {{ tm("roles.previewStale") }}
              </p>
              <v-btn
                data-testid="preview-role"
                variant="tonal"
                :loading="previewing"
                :disabled="saving"
                @click="preview"
                >{{ tm("roles.preview") }}</v-btn
              >
              <div
                v-for="issue in issues"
                :key="`${issue.path}:${issue.code}`"
                role="alert"
                class="issue-row mt-3"
              >
                <button
                  type="button"
                  class="issue-link"
                  @click="focusIssue(issue.path)"
                >
                  {{ issue.path }}
                </button>
                <p>{{ issue.code }} · {{ issue.message }}</p>
              </div>
              <template v-if="resolved">
                <v-chip
                  variant="tonal"
                  class="my-3"
                  :color="resolved.ready ? 'success' : 'warning'"
                  >{{
                    resolved.ready ? tm("roles.ready") : tm("roles.pending")
                  }}</v-chip
                >
                <div
                  v-for="(value, field) in effectiveFields"
                  :key="field"
                  class="effective-row"
                >
                  <strong>{{ tm(`roles.${field}`) }}</strong>
                  <span class="text-caption">{{
                    resolved.sources[field]
                  }}</span>
                  <p>
                    {{
                      Array.isArray(value)
                        ? value.join(", ") || tm("roles.none")
                        : value ?? tm("roles.none")
                    }}
                  </p>
                </div>
                <RunnerCapabilities :runner="resolved.effective.runner" />
                <p class="text-caption mt-3">
                  {{ tm("roles.exportPrompt") }}:
                  {{
                    resolved.export_content.prompt ||
                    tm("roles.personaDependency")
                  }}
                </p>
              </template>
            </aside>
          </div>
        </v-card-text>
        <v-card-actions class="pa-4">
          <v-btn variant="text" :disabled="saving" @click="dialog = false">{{
            tm("roles.cancel")
          }}</v-btn>
          <v-spacer />
          <v-btn
            data-testid="save-role"
            color="primary"
            variant="tonal"
            :loading="saving"
            :disabled="loading || previewing"
            @click="save"
            >{{ currentId ? tm("roles.saveVersion") : tm("roles.save") }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useDisplay } from "vuetify";
import { roleAssetsApi } from "@/api/roleAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import RunnerCapabilities from "./RunnerCapabilities.vue";

const { tm } = useModuleI18n("features/agent-teams");
const { mobile } = useDisplay();
const roles = ref<any[]>([]);
const catalog = ref<Record<string, any[]>>({
  profiles: [],
  models: [],
  personas: [],
  tools: [],
  skills: [],
  knowledge_bases: [],
});
const loading = ref(false);
const loadError = ref("");
const dialog = ref(false);
const currentId = ref<string | null>(null);
const latestVersion = ref<number | null>(null);
const selectedVersion = ref<number | null>(null);
const versions = ref<number[]>([]);
const definition = ref<Record<string, any>>({});
const installation = ref<{
  profile_id: string;
  bindings: Record<string, string | null>;
}>({ profile_id: "default", bindings: {} });
const resourceFields = ["tools", "skills", "knowledge_bases"] as const;
const limitFields = [
  "max_instances",
  "max_parallel",
  "max_steps",
  "max_iterations",
  "max_depth",
  "tool_call_timeout",
  "context_length",
] as const;
const modes = ref<Record<string, string>>({});
const promptMode = ref("inline");
const promptModes = computed(() => [
  { title: tm("roles.inlinePrompt"), value: "inline" },
  { title: tm("roles.personaPrompt"), value: "persona" },
]);
const capabilityModes = computed(() => [
  { title: tm("roles.inherit"), value: "inherit" },
  { title: tm("roles.disable"), value: "none" },
  { title: tm("roles.override"), value: "custom" },
]);
const groups = ref<string[]>(["responsibility", "model"]);
const resolved = ref<any>(null);
const effectiveFields = computed(() =>
  Object.fromEntries(
    Object.entries(resolved.value?.effective ?? {}).filter(
      ([field]) =>
        field !== "runner" &&
        ((resolved.value?.effective?.runner?.type ?? "local") === "local" ||
          ![
            "provider_id",
            "max_steps",
            "tool_call_timeout",
            "context_length",
          ].includes(field)),
    ),
  ),
);
const selectedProfile = computed(() =>
  catalog.value.profiles.find(
    (profile: any) => profile.id === installation.value.profile_id,
  ),
);
const issues = ref<{ path: string; code?: string; message: string }[]>([]);
const previewStale = ref(true);
const previewing = ref(false);
const saving = ref(false);
const errorMessage = ref("");
let editorRevision = 0;
watch(
  [definition, installation, modes, promptMode],
  () => {
    editorRevision++;
    previewStale.value = true;
  },
  { deep: true },
);

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    const [directory, resources] = await Promise.all([
      roleAssetsApi.list(),
      roleAssetsApi.resources(),
    ]);
    catalog.value = resources;
    roles.value = await Promise.all(
      directory.roles.map(async (role: any) => ({
        ...role,
        readiness: await roleAssetsApi.preview(role),
      })),
    );
  } catch (err) {
    loadError.value = extractApiError(err, tm("roles.loadFailed")).message;
  } finally {
    loading.value = false;
  }
}

function edit(role?: any) {
  currentId.value = role?.role_id ?? null;
  latestVersion.value = role?.versions?.[0] ?? role?.version ?? null;
  selectedVersion.value = role?.version ?? null;
  versions.value = role?.versions ?? [];
  definition.value = role
    ? JSON.parse(JSON.stringify(role.definition))
    : {
        name: "",
        description: "",
        prompt: "",
        model_slot: "model",
        persona_slot: null,
        model_capabilities: [],
        runner_capabilities: [],
      };
  installation.value = role
    ? JSON.parse(JSON.stringify(role.installation))
    : { profile_id: "default", bindings: {} };
  promptMode.value = definition.value.prompt == null ? "persona" : "inline";
  for (const field of resourceFields)
    modes.value[field] =
      definition.value[field] == null
        ? "inherit"
        : definition.value[field].length
        ? "custom"
        : "none";
  issues.value = [];
  resolved.value = null;
  errorMessage.value = "";
  groups.value = ["responsibility", "model"];
  dialog.value = true;
}

async function openVersion(version: number) {
  if (!currentId.value) return;
  try {
    edit(await roleAssetsApi.get(currentId.value, version));
  } catch (err) {
    errorMessage.value = extractApiError(err, tm("roles.loadFailed")).message;
  }
}

function payload() {
  // Preserve null (inherit), [] (disable), and logical slots (custom) in storage.
  const content: Record<string, any> = {
    ...definition.value,
    prompt: promptMode.value === "persona" ? null : definition.value.prompt,
  };
  content.persona_slot = content.persona_slot?.trim() || null;
  for (const field of resourceFields)
    content[field] =
      modes.value[field] === "inherit"
        ? null
        : modes.value[field] === "none"
        ? []
        : content[field] ?? [];
  for (const field of limitFields)
    content[field] =
      content[field] == null || content[field] === ""
        ? null
        : Number.isFinite(Number(content[field]))
        ? Number(content[field])
        : content[field];
  const activeSlots = new Set([
    content.model_slot,
    content.persona_slot,
    ...resourceFields.flatMap((field) => content[field] ?? []),
  ]);
  const bindings = Object.fromEntries(
    Object.entries(installation.value.bindings).filter(
      ([slot, id]) => activeSlots.has(slot) && id,
    ),
  );
  return {
    definition: content as any,
    installation: {
      profile_id: installation.value.profile_id,
      bindings: bindings as Record<string, string>,
    },
    expected_version: latestVersion.value,
  };
}

async function preview() {
  // Ignore responses for an earlier edit so stale values cannot appear current.
  const revision = editorRevision;
  previewing.value = true;
  errorMessage.value = "";
  try {
    const result = await roleAssetsApi.preview(payload());
    if (editorRevision !== revision) return;
    resolved.value = result;
    issues.value = result.issues;
    previewStale.value = false;
  } catch (err) {
    if (editorRevision !== revision) return;
    const failure = extractApiError(err, tm("roles.previewFailed"));
    errorMessage.value = failure.message;
    issues.value = failure.fields;
  } finally {
    previewing.value = false;
  }
}

async function save() {
  saving.value = true;
  errorMessage.value = "";
  try {
    await roleAssetsApi.save(currentId.value, payload());
    dialog.value = false;
    await load();
  } catch (err) {
    const failure = extractApiError(err, tm("roles.saveFailed"));
    errorMessage.value = failure.message;
    issues.value = failure.fields;
  } finally {
    saving.value = false;
  }
}

function fieldErrors(path: string) {
  return issues.value
    .filter((issue) => issue.path === path || issue.path.startsWith(`${path}.`))
    .map((issue) => issue.message);
}

function focusIssue(path: string) {
  // Binding errors name logical slots; reveal the section that owns that slot.
  const field = path.split(".")[1];
  const group = path.startsWith("installation.bindings.")
    ? resourceFields.find(
        (key) =>
          definition.value[key]?.includes(
            path.slice("installation.bindings.".length),
          ),
      ) ||
      (path.endsWith(definition.value.persona_slot)
        ? "responsibility"
        : "model")
    : resourceFields.includes(field as any)
    ? field
    : limitFields.includes(field as any)
    ? "limits"
    : ["name", "prompt", "persona_slot"].includes(field)
    ? "responsibility"
    : "model";
  groups.value = [...new Set([...groups.value, group])];
}

onMounted(load);
</script>

<style scoped>
.role-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 270px), 1fr));
  gap: 16px;
}
.role-name {
  overflow-wrap: anywhere;
}
.role-editor {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 24px;
}
.role-editor aside {
  min-width: 0;
  overflow-wrap: anywhere;
}
.effective-row {
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  padding: 12px 0;
}
.effective-row span {
  display: block;
  opacity: 0.7;
}
.effective-row p {
  white-space: pre-wrap;
}
.issue-row {
  border-inline-start: 3px solid rgb(var(--v-theme-warning));
  padding-inline-start: 10px;
}
.issue-link {
  color: rgb(var(--v-theme-primary));
  text-decoration: underline;
  text-align: start;
  overflow-wrap: anywhere;
}
@media (max-width: 760px) {
  .role-editor {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
