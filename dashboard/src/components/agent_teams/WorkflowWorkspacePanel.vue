<template>
  <section>
    <div class="d-flex flex-wrap align-center ga-2 mb-4">
      <h2 class="text-h5">{{ tm(`v2.${mode}`) }}</h2>
      <v-spacer />
      <v-btn variant="text" :loading="loading" @click="refresh">{{
        tm("page.refresh")
      }}</v-btn>
      <v-btn
        v-if="mode === 'assets'"
        variant="tonal"
        color="primary"
        @click="edit(null)"
        >{{ tm("v2.create") }}</v-btn
      >
    </div>
    <v-alert
      v-if="error"
      type="error"
      variant="tonal"
      class="mb-4"
      role="alert"
      >{{ error }}</v-alert
    >
    <p class="text-body-2 mb-4">{{ tm("v2.scope") }}</p>

    <template v-if="mode === 'assets'">
      <v-alert v-if="!assets.length && !loading" type="info" variant="tonal">{{
        tm("v2.emptyAssets")
      }}</v-alert>
      <v-card
        v-for="asset in assets"
        :key="asset.asset_id"
        class="mb-3"
        variant="outlined"
      >
        <v-card-title class="text-wrap"
          >{{ asset.definition.name }} · v{{ asset.version }}</v-card-title
        >
        <v-card-text class="text-wrap">
          <p>
            {{ tm("v2.role") }}: {{ roleName(asset.definition.role_id) }} · v{{
              asset.definition.role_version
            }}
          </p>
          <p class="workflow-text mt-2">{{ asset.definition.task }}</p>
        </v-card-text>
        <v-card-actions class="flex-wrap">
          <v-btn variant="text" @click="edit(asset)">{{ tm("v2.edit") }}</v-btn>
          <v-btn variant="tonal" color="primary" @click="prepareRun(asset)">{{
            tm("v2.start")
          }}</v-btn>
        </v-card-actions>
      </v-card>
    </template>

    <template v-else>
      <v-alert v-if="!runs.length && !loading" type="info" variant="tonal">{{
        tm("v2.emptyRuns")
      }}</v-alert>
      <v-card
        v-for="run in runs"
        :key="run.run_id"
        class="mb-3"
        variant="outlined"
      >
        <v-card-text class="d-flex flex-wrap align-center ga-2">
          <div class="flex-grow-1 workflow-name">
            <strong>{{ run.snapshot.definition.name }}</strong>
            <p class="text-body-2">
              {{ tm(`v2.states.${run.status}`) }} · {{ run.run_id }}
            </p>
          </div>
          <v-btn variant="text" @click="openRun(run.run_id)">{{
            tm("v2.details")
          }}</v-btn>
        </v-card-text>
      </v-card>
      <v-card
        v-if="selectedRun"
        variant="tonal"
        class="mt-4"
        data-testid="run-detail"
      >
        <v-card-title class="text-wrap"
          >{{ tm("v2.snapshot") }} ·
          {{ selectedRun.snapshot.definition.name }}</v-card-title
        >
        <v-card-text>
          <p>{{ tm(`v2.states.${selectedRun.status}`) }}</p>
          <v-alert
            v-if="selectedRun.state.reason"
            type="warning"
            variant="tonal"
            class="my-3"
            >{{ selectedRun.state.reason }}</v-alert
          >
          <p class="workflow-text my-3">{{ selectedRun.snapshot.task }}</p>
          <p>
            {{ tm("v2.assetVersion") }}: {{ selectedRun.snapshot.asset_id }} ·
            v{{ selectedRun.snapshot.asset_version }}
          </p>
          <p>
            {{ tm("v2.member") }}: {{ selectedRun.snapshot.member.name }} ·
            {{ selectedRun.snapshot.member.member_id }}
          </p>
          <p>{{ tm("v2.attempt") }}: {{ selectedRun.state.attempt_id }}</p>
          <h3 class="text-h6 mt-4">{{ tm("v2.result") }}</h3>
          <pre
            v-if="selectedRun.result"
            class="workflow-text mt-2"
            data-testid="result-text"
            >{{ selectedRun.result.text }}</pre
          >
          <p v-else>{{ tm("v2.noResult") }}</p>
          <v-expansion-panels class="mt-4" variant="accordion">
            <v-expansion-panel :title="tm('v2.effective')">
              <v-expansion-panel-text>
                <pre class="workflow-text">{{
                  JSON.stringify(selectedRun.snapshot.effective, null, 2)
                }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
            <v-expansion-panel :title="tm('v2.process')">
              <v-expansion-panel-text>
                <pre class="workflow-text">{{
                  JSON.stringify(selectedRun.state.response, null, 2)
                }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </v-card-text>
      </v-card>
    </template>

    <v-dialog v-model="editing" max-width="720" :persistent="saving">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("v2.edit")
        }}</v-card-title>
        <v-card-text>
          <v-text-field v-model="form.name" :label="tm('v2.name')" />
          <v-select
            v-model="form.role_id"
            :items="roles"
            item-title="definition.name"
            item-value="role_id"
            :label="tm('v2.role')"
            @update:model-value="selectRole"
          />
          <v-select
            v-model="form.role_version"
            :items="roleVersions"
            :label="tm('v2.roleVersion')"
          />
          <v-text-field
            v-model="inputNames"
            :label="tm('v2.inputNames')"
            :hint="tm('v2.inputHint')"
            persistent-hint
          />
          <v-textarea
            v-model="form.task"
            :label="tm('v2.task')"
            :hint="tm('v2.taskHint')"
            persistent-hint
          />
          <v-alert v-if="formError" type="error" variant="tonal" class="mt-3">{{
            formError
          }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="saving" @click="editing = false">{{
            tm("v2.close")
          }}</v-btn>
          <v-btn
            variant="tonal"
            color="primary"
            :loading="saving"
            @click="save"
            >{{ tm("v2.save") }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="starting" max-width="600" :persistent="saving">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("v2.start")
        }}</v-card-title>
        <v-card-text v-if="runAsset">
          <p class="mb-4">
            {{ runAsset.definition.name }} · v{{ runAsset.version }}
          </p>
          <v-text-field
            v-for="name in runAsset.definition.required_inputs"
            :key="name"
            v-model="inputs[name]"
            :label="name"
            :rules="[(value) => !!value?.trim() || tm('v2.required')]"
          />
          <v-alert v-if="formError" type="error" variant="tonal">{{
            formError
          }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="saving" @click="starting = false">{{
            tm("v2.close")
          }}</v-btn>
          <v-btn
            variant="tonal"
            color="primary"
            :loading="saving"
            @click="start"
            >{{ tm("v2.start") }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { roleAssetsApi } from "@/api/roleAssets";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import type { WorkflowDefinition } from "@/api/generated/openapi-v1";

const props = defineProps<{ mode: "assets" | "runs" }>();
const emit = defineEmits<{ (event: "started", runId: string): void }>();
const { tm } = useModuleI18n("features/agent-teams");
const route = useRoute();
const router = useRouter();
const assets = ref<any[]>([]);
const roles = ref<any[]>([]);
const runs = ref<any[]>([]);
const selectedRun = ref<any>(null);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const formError = ref("");
const editing = ref(false);
const starting = ref(false);
const editingAsset = ref<any>(null);
const runAsset = ref<any>(null);
const form = ref<WorkflowDefinition>({
  name: "",
  role_id: "",
  role_version: 1,
  task: "",
  required_inputs: [],
  overrides: {},
});
const inputNames = ref("");
const inputs = ref<Record<string, string>>({});
const roleVersions = computed(
  () =>
    roles.value.find((role) => role.role_id === form.value.role_id)?.versions ??
    [],
);
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;

function roleName(id: string) {
  return roles.value.find((role) => role.role_id === id)?.definition.name ?? id;
}

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  try {
    if (props.mode === "assets") {
      const [assetData, roleData] = await Promise.all([
        workflowAssetsApi.list(),
        roleAssetsApi.list(),
      ]);
      assets.value = assetData.assets;
      roles.value = roleData.roles;
    } else {
      runs.value = (await workflowAssetsApi.runs()).runs;
      const id = selectedRun.value?.run_id ?? route.query.workflow_run;
      if (typeof id === "string" && id)
        selectedRun.value = await workflowAssetsApi.run(id);
    }
    error.value = "";
  } catch (err) {
    error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    loading.value = false;
  }
}

function edit(asset: any) {
  editingAsset.value = asset;
  form.value = asset
    ? JSON.parse(JSON.stringify(asset.definition))
    : {
        name: "",
        role_id: "",
        role_version: 1,
        task: "",
        required_inputs: [],
        overrides: {},
      };
  inputNames.value = form.value.required_inputs?.join(", ") ?? "";
  formError.value = "";
  editing.value = true;
}

function selectRole(id: string) {
  form.value.role_version =
    roles.value.find((role) => role.role_id === id)?.version ?? 1;
}

async function save() {
  if (saving.value) return;
  saving.value = true;
  formError.value = "";
  try {
    form.value.required_inputs = inputNames.value
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean);
    await workflowAssetsApi.save(editingAsset.value?.asset_id ?? null, {
      definition: form.value,
      expected_version: editingAsset.value?.version,
    });
    editing.value = false;
    await refresh();
  } catch (err) {
    const failure = extractApiError(err, tm("errors.operationFailed"));
    formError.value = `${failure.message} ${failure.fields
      .map((field) => `${field.path}: ${field.message}`)
      .join("; ")}`;
  } finally {
    saving.value = false;
  }
}

function prepareRun(asset: any) {
  runAsset.value = asset;
  inputs.value = Object.fromEntries(
    asset.definition.required_inputs.map((name: string) => [name, ""]),
  );
  formError.value = "";
  starting.value = true;
}

async function start() {
  if (saving.value || !runAsset.value) return;
  saving.value = true;
  formError.value = "";
  try {
    const run = await workflowAssetsApi.start(runAsset.value.asset_id, {
      version: runAsset.value.version,
      inputs: inputs.value,
      source: "dashboard",
    });
    starting.value = false;
    await router.replace({
      query: { ...route.query, workflow_run: run.run_id },
    });
    emit("started", run.run_id);
  } catch (err) {
    const failure = extractApiError(err, tm("errors.operationFailed"));
    formError.value = `${failure.message} ${failure.fields
      .map((field) => `${field.path}: ${field.message}`)
      .join("; ")}`;
  } finally {
    saving.value = false;
  }
}

async function openRun(runId: string) {
  try {
    selectedRun.value = await workflowAssetsApi.run(runId);
    await router.replace({ query: { ...route.query, workflow_run: runId } });
    error.value = "";
  } catch (err) {
    error.value = extractApiError(err, tm("errors.operationFailed")).message;
  }
}

onMounted(async () => {
  await refresh();
  // Poll persisted progress. Unmount only closes observation, never the run.
  if (!disposed && props.mode === "runs") timer = setInterval(refresh, 2000);
});
onUnmounted(() => {
  disposed = true;
  if (timer) clearInterval(timer);
});
</script>

<style scoped>
.workflow-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  max-width: 100%;
  font: inherit;
}
.workflow-name {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
