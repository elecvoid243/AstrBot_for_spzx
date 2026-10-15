<template>
  <v-dialog
    :model-value="modelValue"
    max-width="900"
    :persistent="installing"
    @update:model-value="close"
  >
    <v-card class="package-dialog">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
        tm(exportedPackage ? "package.export" : "package.import")
      }}</v-card-title>
      <v-card-text class="package-body">
        <p class="mb-4">{{ tm("package.scope") }}</p>
        <template v-if="exportedPackage">
          <p class="mb-3">{{ tm("package.includedPrompt") }}</p>
          <v-textarea
            :model-value="JSON.stringify(exportedPackage, null, 2)"
            :label="tm('package.content')"
            readonly
            rows="14"
          />
        </template>
        <template v-else>
          <v-file-input
            :disabled="installing"
            accept="application/json,.json"
            :label="tm('package.file')"
            @update:model-value="readFile"
          />
          <v-textarea
            :disabled="installing"
            :model-value="content"
            :label="tm('package.content')"
            rows="6"
            @update:model-value="changeContent"
          />
          <v-btn
            variant="tonal"
            :loading="checking"
            :disabled="!content.trim() || installing"
            data-testid="preview-package"
            @click="previewPackage"
            >{{ tm("package.preview") }}</v-btn
          >
          <template v-if="preview">
            <h3 class="text-h6 mt-5 package-text">
              {{ preview.package.definition.name }} · v{{
                preview.package.version
              }}
            </h3>
            <p class="text-caption package-text">
              {{ preview.package.asset_id }}
            </p>
            <v-alert
              v-if="preview.existing_asset_id"
              type="info"
              variant="tonal"
              class="my-3"
              >{{ tm("package.existing") }}</v-alert
            >
            <v-alert
              :type="preview.ready ? 'success' : 'warning'"
              variant="tonal"
              class="my-3"
              data-testid="package-readiness"
              >{{
                tm(preview.ready ? "package.ready" : "package.pending")
              }}</v-alert
            >
            <v-card
              v-for="role in preview.roles"
              :key="role.key"
              variant="outlined"
              class="my-4"
            >
              <v-card-text>
                <h4 class="text-subtitle-1 package-text">{{ role.name }}</h4>
                <p class="text-caption">
                  {{ tm("package.includedPrompt") }} ·
                  {{ tm("package.promptOrigin") }}: {{ role.prompt_source }}
                </p>
                <pre class="package-text my-3">{{ role.prompt }}</pre>
                <div
                  v-for="dependency in role.dependencies"
                  :key="dependency.slot"
                  class="my-4"
                >
                  <p class="package-text">
                    <strong>{{
                      dependency.display_name || dependency.slot
                    }}</strong>
                    · {{ dependency.kind }} ·
                    {{ tm(`package.status.${dependency.status}`) }}
                  </p>
                  <p class="text-caption package-text">
                    {{ dependency.capabilities.join(", ") }}
                    {{
                      dependency.fingerprint
                        ? tm("package.verifiedSource")
                        : tm("package.manualSource")
                    }}
                  </p>
                  <p
                    v-if="dependency.logical_slots?.length"
                    class="package-text"
                  >
                    {{ tm("package.logicalPurpose") }}:
                    {{ dependency.logical_slots.join(", ") }}
                  </p>
                  <p v-if="dependency.description" class="package-text my-2">
                    {{ dependency.description }}
                  </p>
                  <details v-if="dependency.input_schema" class="my-2">
                    <summary>{{ tm("package.requiredInput") }}</summary>
                    <pre class="package-text">{{
                      JSON.stringify(dependency.input_schema, null, 2)
                    }}</pre>
                  </details>
                  <v-select
                    :menu-props="{ contentClass: 'workflow-package-menu' }"
                    :model-value="dependency.selected"
                    :items="dependency.candidates"
                    item-title="name"
                    item-value="id"
                    :label="tm('package.localResource')"
                    clearable
                    :disabled="installing || checking"
                    @update:model-value="
                      chooseResource(role.key, dependency, $event)
                    "
                  >
                    <template #selection="{ item }"
                      ><span class="package-text">{{
                        item.title
                      }}</span></template
                    >
                  </v-select>
                  <template
                    v-for="candidate in dependency.candidates"
                    :key="candidate.id"
                  >
                    <details
                      v-if="candidate.description || candidate.input_schema"
                      class="mb-3"
                    >
                      <summary class="package-text">
                        {{ tm("package.resourceDetails") }} ·
                        {{ candidate.name }}
                      </summary>
                      <p class="package-text">{{ candidate.description }}</p>
                      <pre v-if="candidate.input_schema" class="package-text">{{
                        JSON.stringify(candidate.input_schema, null, 2)
                      }}</pre>
                    </details>
                  </template>
                  <router-link
                    v-if="!dependency.candidates.length"
                    :to="
                      dependency.kind === 'profiles'
                        ? '/config'
                        : dependency.kind === 'models'
                        ? '/providers'
                        : dependency.kind === 'knowledge_bases'
                        ? '/knowledge-base'
                        : '/extension'
                    "
                    @click="close(false)"
                    >{{ tm("package.configureResources") }}</router-link
                  >
                </div>
              </v-card-text>
            </v-card>
            <details class="my-3">
              <summary>{{ tm("package.definition") }}</summary>
              <pre class="package-text">{{
                JSON.stringify(preview.package, null, 2)
              }}</pre>
            </details>
            <ul v-if="preview.issues.length" class="pl-5 package-text">
              <li v-for="(issue, index) in preview.issues" :key="index">
                {{ issue.path }}: {{ issue.message }}
              </li>
            </ul>
          </template>
        </template>
        <v-alert
          v-if="error"
          type="error"
          variant="tonal"
          class="mt-3 package-text"
          role="alert"
          >{{ error }}</v-alert
        >
      </v-card-text>
      <v-card-actions class="flex-wrap ga-2">
        <v-spacer />
        <v-btn variant="text" :disabled="installing" @click="close(false)">{{
          tm("v2.close")
        }}</v-btn>
        <v-btn
          v-if="exportedPackage"
          variant="tonal"
          color="primary"
          @click="download"
          >{{ tm("package.download") }}</v-btn
        >
        <template v-else-if="preview">
          <v-btn
            v-if="!preview.ready"
            variant="tonal"
            :disabled="checking || installing"
            @click="install(true)"
            >{{ tm("package.savePending") }}</v-btn
          >
          <v-btn
            variant="tonal"
            color="primary"
            :disabled="!preview.ready || checking || installing"
            :loading="installing"
            data-testid="confirm-package"
            @click="install(false)"
            >{{ tm("package.confirm") }}</v-btn
          >
        </template>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { workflowAssetsApi } from "@/api/workflowAssets";
import type {
  WorkflowImportRequest,
  WorkflowPackage,
} from "@/api/generated/openapi-v1";
import { extractApiError } from "@/utils/extractApiError";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  modelValue: boolean;
  exportedPackage?: WorkflowPackage | null;
}>();
const emit = defineEmits<{
  (event: "update:modelValue", value: boolean): void;
  (event: "installed"): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const content = ref("");
const preview = ref<any>(null);
const installations = ref<NonNullable<WorkflowImportRequest["installations"]>>(
  {},
);
const checking = ref(false);
const installing = ref(false);
const error = ref("");
let generation = 0;

watch(
  () => props.modelValue,
  () => {
    generation++;
    content.value = "";
    preview.value = null;
    installations.value = {};
    checking.value = false;
    error.value = "";
  },
);

function close(value: boolean) {
  if (!installing.value) emit("update:modelValue", value);
}

function changeContent(value: string) {
  generation++;
  content.value = value;
  preview.value = null;
  installations.value = {};
  checking.value = false;
  error.value = "";
}

async function readFile(value: File | File[] | null) {
  const file = Array.isArray(value) ? value[0] : value;
  if (!file) return;
  const current = ++generation;
  try {
    const text = await file.text();
    if (current === generation) changeContent(text);
  } catch (cause) {
    if (current === generation)
      error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  }
}

async function previewPackage() {
  const current = ++generation;
  checking.value = true;
  error.value = "";
  try {
    const result = await workflowAssetsApi.previewImport({
      package: JSON.parse(content.value),
      installations: installations.value,
    });
    if (current !== generation) return;
    preview.value = result;
    installations.value = Object.fromEntries(
      result.roles.map((role: any) => [role.key, role.installation]),
    );
  } catch (cause) {
    if (current !== generation) return;
    preview.value = null;
    const failure = extractApiError(cause, tm("errors.operationFailed"));
    error.value = [
      failure.message,
      ...failure.fields.map((field) => `${field.path}: ${field.message}`),
    ].join("\n");
  } finally {
    if (current === generation) checking.value = false;
  }
}

async function chooseResource(
  key: string,
  dependency: any,
  resourceId: string | null,
) {
  const installation = installations.value[key];
  if (dependency.kind === "profiles")
    installation.profile_id = resourceId || "__unconfigured__";
  else if (resourceId)
    installation.bindings = {
      ...installation.bindings,
      [dependency.slot]: resourceId,
    };
  else delete installation.bindings?.[dependency.slot];
  await previewPackage();
}

async function install(savePending: boolean) {
  if (!preview.value || checking.value || installing.value) return;
  installing.value = true;
  error.value = "";
  try {
    await workflowAssetsApi.importPackage({
      package: preview.value.package,
      installations: installations.value,
      save_pending: savePending,
    });
    emit("installed");
    emit("update:modelValue", false);
  } catch (cause) {
    const failure = extractApiError(cause, tm("errors.operationFailed"));
    error.value = [
      failure.message,
      ...failure.fields.map((field) => `${field.path}: ${field.message}`),
    ].join("\n");
  } finally {
    installing.value = false;
  }
}

function download() {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(props.exportedPackage, null, 2)], {
      type: "application/json",
    }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "workflow.json";
  link.click();
  URL.revokeObjectURL(url);
}
</script>

<style scoped>
.package-dialog {
  max-width: 100%;
  max-height: calc(100vh - 48px);
}
.package-body {
  min-height: 0;
  overflow-y: auto;
}
.package-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  min-width: 0;
}
</style>

<style>
.workflow-package-menu .v-list-item-title,
.workflow-package-menu .v-list-item-subtitle {
  white-space: normal;
  overflow-wrap: anywhere;
  -webkit-line-clamp: unset;
}
</style>
