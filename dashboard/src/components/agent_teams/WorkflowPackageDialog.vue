<template>
  <v-dialog
    class="workbench-dialog"
    scrollable
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
          <p class="mb-3">{{ tm("package.scope") }}</p>
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
            <h4 class="text-subtitle-1 mt-4">{{ tm("package.closure") }}</h4>
            <ul class="pl-5 package-text">
              <li
                v-for="asset in preview.assets"
                :key="`${asset.asset_id}:${asset.version}`"
              >
                {{ asset.name }} · v{{ asset.version }} ·
                {{ tm(`package.actions.${asset.action}`) }}
              </li>
            </ul>
            <p class="text-caption mt-2">{{ tm("package.versionSafety") }}</p>
            <v-alert
              :type="preview.ready ? 'success' : 'warning'"
              variant="tonal"
              class="my-3"
              data-testid="package-readiness"
              >{{
                tm(preview.ready ? "package.ready" : "package.pending")
              }}</v-alert
            >
            <p class="text-caption">{{ tm("package.autoRecheck") }}</p>
            <v-card
              v-for="requirement in preview.requirements"
              :key="`${requirement.kind}:${requirement.source_id}`"
              variant="outlined"
              class="my-4"
            >
              <v-card-text>
                <p class="package-text">
                  <strong>{{
                    requirement.display_name || requirement.source_id
                  }}</strong>
                  · {{ tm(`package.kinds.${requirement.kind}`) }} ·
                  {{ requirement.source_id }}
                </p>
                <p v-if="requirement.description" class="package-text">
                  {{ requirement.description }}
                </p>
                <p v-if="requirement.capabilities?.length" class="text-caption">
                  {{ tm("package.requiredCapabilities") }}:
                  {{ requirement.capabilities.join(", ") }}
                </p>
                <p v-if="requirement.fingerprint" class="text-caption">
                  {{ tm("package.verifiedSource") }}
                </p>
                <details v-if="requirement.input_schema" class="my-2">
                  <summary>{{ tm("package.requiredInput") }}</summary>
                  <pre class="package-text">{{
                    JSON.stringify(requirement.input_schema, null, 2)
                  }}</pre>
                </details>
                <v-select
                  :model-value="requirement.selected"
                  :items="requirement.candidates"
                  item-title="name"
                  item-value="id"
                  item-props
                  :label="tm('package.localResource')"
                  clearable
                  :disabled="installing || checking"
                  @update:model-value="chooseResource(requirement, $event)"
                />
                <ul class="package-text pl-5">
                  <li
                    v-for="candidate in requirement.candidates"
                    :key="candidate.id"
                  >
                    {{ candidate.name }} ({{ candidate.id }})
                    <span v-if="candidate.reasons.length">
                      · {{ candidate.reasons.join(", ") }}</span
                    >
                    <span v-if="candidate.warnings?.length">
                      · {{ tm("package.differentSource") }}</span
                    >
                  </li>
                </ul>
              </v-card-text>
            </v-card>
            <details
              v-if="preview.effective?.length"
              :open="preview.ready"
              class="my-3"
            >
              <summary>{{ tm("package.effective") }}</summary>
              <v-card
                v-for="item in preview.effective"
                :key="`${item.asset_id}:${item.version}:${item.member}`"
                variant="outlined"
                class="my-2"
              >
                <v-card-text>
                  <strong>{{ item.asset_name }} · {{ item.member }}</strong>
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
                    class="package-text my-1"
                  >
                    {{ tm(`package.fields.${field}`) }}:
                    {{
                      Array.isArray(item.effective[field])
                        ? item.effective[field].join(", ") || "—"
                        : item.effective[field] ?? "—"
                    }}
                    <span v-if="item.sources[field]" class="text-caption">
                      · {{ tm("package.source") }}:
                      {{
                        tm(
                          `package.sources.${
                            item.sources[field].split(":")[0]
                          }`,
                        )
                      }}<template
                        v-if="
                          item.sources[field].includes(':') &&
                          !item.sources[field].startsWith('workflow:')
                        "
                        >:
                        {{
                          item.sources[field].split(":").slice(1).join(":")
                        }}</template
                      ></span
                    >
                  </p>
                </v-card-text>
              </v-card>
            </details>
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
            variant="tonal"
            color="primary"
            :disabled="!preview.ready || checking || installing"
            :loading="installing"
            data-testid="confirm-package"
            @click="install"
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
  (event: "installed", directory: "teams" | "workflows"): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const content = ref("");
const preview = ref<any>(null);
const selections = ref<NonNullable<WorkflowImportRequest["selections"]>>([]);
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
    selections.value = [];
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
  selections.value = [];
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
      error.value = extractApiError(
        cause,
        tm("errors.operationFailed"),
      ).message;
  }
}

async function previewPackage() {
  const current = ++generation;
  checking.value = true;
  error.value = "";
  try {
    const result = await workflowAssetsApi.previewImport({
      package: preview.value?.package ?? JSON.parse(content.value),
      selections: selections.value,
    });
    if (current !== generation) return;
    preview.value = result;
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

async function chooseResource(requirement: any, resourceId: string | null) {
  selections.value = selections.value.filter(
    (item) =>
      item.kind !== requirement.kind ||
      item.source_id !== requirement.source_id,
  );
  if (resourceId)
    selections.value.push({
      kind: requirement.kind,
      source_id: requirement.source_id,
      target_id: resourceId,
    });
  await previewPackage();
}

async function install() {
  if (!preview.value || checking.value || installing.value) return;
  installing.value = true;
  error.value = "";
  try {
    await workflowAssetsApi.importPackage({
      package: preview.value.package,
      selections: selections.value,
    });
    emit(
      "installed",
      preview.value.package.definition.kind === "autonomous"
        ? "teams"
        : "workflows",
    );
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
