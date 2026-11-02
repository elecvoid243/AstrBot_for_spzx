<template>
  <div class="mb-3">
    <p role="status">{{ tm(`draft.${draft.status.value}`) }}</p>
    <v-btn
      v-if="draft.status.value === 'failed'"
      variant="text"
      @click="run(draft.retry)"
      >{{ tm("draft.retry") }}</v-btn
    >
    <template v-if="draft.status.value === 'conflict'">
      <v-btn variant="text" @click="$emit('reload')">{{
        tm("draft.reload")
      }}</v-btn>
      <v-btn variant="text" @click="run(draft.reviewPublished)">{{
        tm("draft.review")
      }}</v-btn>
    </template>
    <v-alert
      v-if="draft.reviewed.value"
      type="warning"
      variant="tonal"
      class="my-2"
    >
      {{
        tm("draft.baseComparison", {
          old: draft.baseVersion.value ?? 0,
          current: draft.reviewed.value.version,
        })
      }}
      {{ tm("draft.rebaseExplanation") }}
      <details>
        <summary>{{ tm("draft.review") }}</summary>
        <pre style="white-space: pre-wrap; overflow-wrap: anywhere">{{
          JSON.stringify(draft.reviewed.value.definition, null, 2)
        }}</pre>
      </details>
      <div>
        <v-btn variant="tonal" @click="run(draft.rebase)">{{
          tm("draft.keep")
        }}</v-btn>
        <v-btn
          variant="text"
          @click="$emit('useLatest', draft.reviewed.value)"
          >{{ tm("draft.useLatest") }}</v-btn
        >
        <v-btn variant="text" @click="draft.reviewed.value = null">{{
          tm("v2.close")
        }}</v-btn>
      </div>
    </v-alert>
    <v-btn
      v-if="
        draft.status.value === 'failed' || draft.status.value === 'conflict'
      "
      variant="text"
      @click="$emit('discard')"
      >{{ tm("draft.discardClose") }}</v-btn
    >
    <p v-if="error" role="alert">{{ error }}</p>
  </div>
</template>
<script setup lang="ts">
import { ref } from "vue";
import { useModuleI18n } from "@/i18n/composables";
defineProps<{ draft: any }>();
defineEmits<{ reload: []; discard: []; useLatest: [value: any] }>();
const { tm } = useModuleI18n("features/agent-teams");
const error = ref("");
async function run(action: () => Promise<void>) {
  error.value = "";
  try {
    await action();
  } catch {
    error.value = tm("errors.operationFailed");
  }
}
</script>
