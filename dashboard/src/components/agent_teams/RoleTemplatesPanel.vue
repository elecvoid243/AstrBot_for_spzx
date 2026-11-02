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
              tm("roles.none")
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
            {{ role.definition.provider_id || tm("roles.unbound") }}
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
          <div ref="editor" class="role-editor">
            <div>
              <v-text-field
                data-testid="role-name"
                data-issue-path="definition.name"
                v-model="definition.name"
                :label="tm('roles.name')"
                :error-messages="fieldErrors('definition.name')"
              />
              <v-textarea
                data-issue-path="definition.description"
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
                    <v-textarea
                      data-testid="role-prompt"
                      data-issue-path="definition.prompt"
                      v-model="definition.prompt"
                      :label="tm('roles.prompt')"
                      rows="4"
                      :error-messages="fieldErrors('definition.prompt')"
                    />
                    <v-select
                      data-issue-path="definition.persona_id"
                      v-model="definition.persona_id"
                      :items="catalog.personas"
                      item-title="name"
                      item-value="id"
                      :label="tm('roles.localPersona')"
                      :aria-label="tm('roles.localPersona')"
                      clearable
                      :error-messages="fieldErrors('definition.persona_id')"
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
                      data-issue-path="installation.profile_id"
                      v-model="installation.profile_id"
                      :items="catalog.profiles"
                      item-title="name"
                      item-value="id"
                      :label="tm('roles.profile')"
                      :aria-label="tm('roles.profile')"
                      :error-messages="fieldErrors('installation.profile_id')"
                    />
                    <v-select
                      v-if="
                        (selectedProfile?.runner_type ?? 'local') === 'local' ||
                        definition.provider_id
                      "
                      v-model="definition.provider_id"
                      data-issue-path="definition.provider_id"
                      :items="catalog.models"
                      item-title="name"
                      item-value="id"
                      :label="tm('roles.localModel')"
                      :aria-label="tm('roles.localModel')"
                      clearable
                      :error-messages="fieldErrors('definition.provider_id')"
                    />
                    <v-select
                      data-issue-path="definition.model_capabilities"
                      v-model="definition.model_capabilities"
                      :items="['tool_use', 'image', 'audio']"
                      multiple
                      chips
                      :label="tm('roles.modelCapabilities')"
                      :aria-label="tm('roles.modelCapabilities')"
                    />
                    <v-select
                      data-testid="runner-requirements"
                      data-issue-path="definition.runner_capabilities"
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
                      :data-issue-path="
                        modes[field] === 'custom'
                          ? undefined
                          : `definition.${field}`
                      "
                      :items="capabilityModes"
                      :label="tm('roles.mode')"
                      :aria-label="`${tm(`roles.${field}`)}: ${tm(
                        'roles.mode',
                      )}`"
                      @update:model-value="
                        (mode: string) => {
                          if (
                            mode === 'custom' &&
                            !Array.isArray(definition[field])
                          )
                            definition[field] = [];
                        }
                      "
                    />
                    <template v-if="modes[field] === 'custom'">
                      <p class="text-caption mb-2">
                        {{ tm("roles.customEmptyHint") }}
                      </p>
                      <v-select
                        :data-issue-path="`definition.${field}`"
                        v-model="definition[field]"
                        :items="catalog[field]"
                        item-title="name"
                        item-value="id"
                        :multiple="true"
                        chips
                        closable-chips
                        :label="tm(`roles.${field}`)"
                        :aria-label="tm(`roles.${field}`)"
                        :error-messages="fieldErrors(`definition.${field}`)"
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
                      :data-issue-path="`definition.${field}`"
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
import { computed, nextTick, onMounted, ref, watch } from "vue";
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
const editor = ref<HTMLElement | null>(null);
const currentId = ref<string | null>(null);
const latestVersion = ref<number | null>(null);
const selectedVersion = ref<number | null>(null);
const versions = ref<number[]>([]);
const definition = ref<Record<string, any>>({});
const installation = ref<{
  profile_id: string;
}>({ profile_id: "default" });
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
const capabilityModes = computed(() => [
  { title: tm("roles.inherit"), value: "inherit" },
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
  [definition, installation, modes],
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
        provider_id: null,
        persona_id: null,
        model_capabilities: [],
        runner_capabilities: [],
      };
  installation.value = role
    ? JSON.parse(JSON.stringify(role.installation))
    : { profile_id: "default" };
  for (const field of resourceFields)
    modes.value[field] = definition.value[field] == null ? "inherit" : "custom";
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
  // Preserve null (inherit), [] (disable), and selected resource identities.
  const content: Record<string, any> = { ...definition.value };
  content.persona_id = content.persona_id || null;
  content.provider_id = content.provider_id || null;
  for (const field of resourceFields)
    content[field] =
      modes.value[field] === "inherit" ? null : content[field] ?? [];
  for (const field of limitFields)
    content[field] =
      content[field] == null || content[field] === ""
        ? null
        : Number.isFinite(Number(content[field]))
        ? Number(content[field])
        : content[field];
  return {
    definition: content as any,
    installation: {
      profile_id: installation.value.profile_id,
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
  if (saving.value) return;
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

async function focusIssue(path: string) {
  const field = path.split(".")[1];
  const group = resourceFields.includes(field as any)
    ? field
    : limitFields.includes(field as any)
    ? "limits"
    : ["name", "prompt", "persona_id"].includes(field)
    ? "responsibility"
    : "model";
  groups.value = [...new Set([...groups.value, group])];
  await nextTick();
  // Match the longest field path so indexed errors locate their control.
  const target = Array.from(
    editor.value?.querySelectorAll<HTMLElement>("[data-issue-path]") ?? [],
  )
    .filter((element) => {
      const fieldPath = element.dataset.issuePath!;
      return path === fieldPath || path.startsWith(`${fieldPath}.`);
    })
    .sort(
      (a, b) => b.dataset.issuePath!.length - a.dataset.issuePath!.length,
    )[0];
  const control = target?.matches("input, textarea, select")
    ? target
    : target?.querySelector<HTMLElement>("input, textarea, select");
  // Vuetify starts the height transition on the next frame after rendering.
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  await Promise.all(
    (target?.closest(".v-expansion-panel-text")?.getAnimations?.() ?? []).map(
      (animation) => animation.finished.catch(() => undefined),
    ),
  );
  if (!control || !editor.value?.contains(control)) return;
  control.scrollIntoView({ block: "center", inline: "nearest" });
  control.focus({ preventScroll: true });
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
