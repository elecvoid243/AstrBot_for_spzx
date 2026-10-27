<template>
  <section class="mb-4" :aria-label="tm('cutover.title')">
    <v-alert v-if="error" type="error" variant="tonal" role="alert">
      {{ error }}
      <v-btn variant="text" :loading="loading" @click="refresh">{{
        tm("page.refresh")
      }}</v-btn>
    </v-alert>
    <v-alert
      v-else-if="runs.length"
      type="warning"
      variant="tonal"
      role="status"
    >
      <h2 class="text-h6">{{ tm("cutover.title") }}</h2>
      <p class="my-2">{{ tm("cutover.pending") }}</p>
      <ul class="pl-5">
        <li v-for="run in runs" :key="run.run_id" class="mb-3">
          <div class="text-break">
            {{
              teams.find((team) => team.team_id === run.team_id)?.name ??
              run.team_id
            }}
            · {{ run.run_id }} · {{ tm(`monitor.status.${run.status}`) }}
          </div>
          <p class="text-break">{{ run.input }}</p>
          <v-btn
            v-if="run.status === 'paused'"
            variant="tonal"
            :disabled="!!operation"
            :loading="operation === run.run_id"
            @click="control(run, 'continue')"
          >
            {{ tm("cutover.continue") }}
          </v-btn>
          <v-btn
            variant="text"
            :disabled="!!operation || run.status === 'stopping'"
            :loading="operation === run.run_id"
            @click="control(run, 'stop')"
          >
            {{ tm("cutover.stop") }}
          </v-btn>
        </li>
      </ul>
      <v-btn variant="text" :loading="loading" @click="refresh">{{
        tm("page.refresh")
      }}</v-btn>
    </v-alert>
    <p v-else-if="!loading" class="text-body-2 text-medium-emphasis">
      {{ tm("cutover.ready") }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { agentTeamsApi } from "@/api/v1";
import { useModuleI18n } from "@/i18n/composables";
import { askForConfirmation, useConfirmDialog } from "@/utils/confirmDialog";
import { extractApiError } from "@/utils/extractApiError";

defineProps<{ teams: { team_id: string; name: string }[] }>();
const emit = defineEmits<{ (event: "ready", value: boolean): void }>();
const { tm } = useModuleI18n("features/agent-teams");
const confirmDialog = useConfirmDialog();
const runs = ref<
  { run_id: string; team_id: string; status: string; input: string }[]
>([]);
const loading = ref(false);
const operation = ref("");
const error = ref("");
let timer: ReturnType<typeof setInterval> | undefined;

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  try {
    const response = await agentTeamsApi.listActiveRuns();
    if (response.data.status === "error")
      throw new Error(response.data.message || tm("errors.operationFailed"));
    runs.value = response.data.data.runs;
    error.value = "";
    emit("ready", runs.value.length === 0);
  } catch (err) {
    error.value = extractApiError(err, tm("errors.operationFailed")).message;
    emit("ready", false);
  } finally {
    loading.value = false;
  }
}

async function control(run: { run_id: string }, action: "continue" | "stop") {
  if (
    action === "stop" &&
    !(await askForConfirmation(
      tm("cutover.stopConfirm", { id: run.run_id }),
      confirmDialog,
    ))
  )
    return;
  operation.value = run.run_id;
  try {
    const response = await (action === "stop"
      ? agentTeamsApi.stopRun(run.run_id)
      : agentTeamsApi.resumeRun(run.run_id));
    if (response.data.status === "error")
      throw new Error(response.data.message || tm("errors.operationFailed"));
    await refresh();
  } catch (err) {
    error.value = extractApiError(err, tm("errors.operationFailed")).message;
    emit("ready", false);
  } finally {
    operation.value = "";
  }
}

onMounted(() => {
  void refresh();
  timer = setInterval(() => void refresh(), 3000);
});
onUnmounted(() => clearInterval(timer));
</script>
