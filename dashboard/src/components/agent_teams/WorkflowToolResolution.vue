<template>
  <div data-testid="tool-resolution" class="mt-4">
    <v-alert type="warning" variant="tonal" class="mb-3">{{
      tm("resolution.unknown")
    }}</v-alert>
    <v-select
      v-model="action"
      :items="actionItems"
      :disabled="busy"
      :label="tm('resolution.action')"
    />
    <p v-if="action === 'retry'" class="mb-3">
      {{ tm("resolution.retryWarning") }}
    </p>
    <v-textarea
      v-model="evidence"
      :disabled="busy"
      :label="tm('resolution.evidence')"
      data-testid="resolution-evidence"
    />
    <v-textarea
      v-if="action === 'confirm_result' || action === 'confirm_failure'"
      v-model="result"
      :disabled="busy"
      :label="tm('resolution.result')"
      data-testid="resolution-result"
    />
    <v-btn variant="tonal" :loading="busy" :disabled="!valid" @click="submit">{{
      tm(`resolution.actions.${action}`)
    }}</v-btn>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type { ToolResolutionRequest } from "@/api/generated/openapi-v1";

const props = defineProps<{
  actions: ToolResolutionRequest["action"][];
  busy: boolean;
}>();
const emit = defineEmits<{
  (event: "submit", payload: ToolResolutionRequest): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const actionItems = computed(() =>
  props.actions.map((value) => ({
    value,
    title: tm(`resolution.actions.${value}`),
  })),
);
const action = ref<ToolResolutionRequest["action"]>(
  props.actions.includes("reconcile") ? "reconcile" : "confirm_result",
);
const evidence = ref("");
const result = ref("");
let requestId: string | null = null;
watch([action, evidence, result], () => {
  requestId = null;
});
const valid = computed(
  () =>
    props.actions.includes(action.value) &&
    evidence.value.trim() &&
    (!["confirm_result", "confirm_failure"].includes(action.value) ||
      result.value.trim()),
);

function submit() {
  if (!valid.value || props.busy) return;
  requestId ??=
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  emit("submit", {
    request_id: requestId,
    action: action.value,
    evidence: evidence.value.trim(),
    result: ["confirm_result", "confirm_failure"].includes(action.value)
      ? result.value.trim()
      : null,
  });
}
</script>
