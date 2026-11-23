<template>
  <v-dialog :model-value="true" max-width="560" :persistent="generating" @update:model-value="!$event && emit('close')">
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{ tm("reuse.sources") }}</v-card-title>
      <v-card-text>
        <p class="mb-4">{{ tm("reuse.replaceDraft") }}</p>
        <v-btn variant="tonal" :disabled="generating" @click="useTemplate">{{ tm("reuse.template") }}</v-btn>
        <v-divider class="my-4" />
        <v-select v-model="provider" :items="models" item-title="name" item-value="id"
          :label="tm('reuse.generationModel')" :disabled="generating"
          :no-data-text="tm('reuse.noGenerationModel')" />
        <v-textarea v-model="description" :label="tm('reuse.description')" maxlength="4000" :disabled="generating" />
        <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="generating" @click="emit('close')">{{ tm("v2.close") }}</v-btn>
        <v-btn variant="tonal" :loading="generating" :disabled="!provider || !description.trim()" @click="generate">
          {{ tm("reuse.generate") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import type { GraphDefinition } from "@/api/generated/openapi-v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";

const props = defineProps<{ models: { id: string; name: string }[]; defaultProvider?: string | null }>();
const emit = defineEmits<{ close: []; apply: [definition: GraphDefinition] }>();
const { tm } = useModuleI18n("features/agent-teams");
const provider = ref(props.models.some(({ id }) => id === props.defaultProvider)
  ? props.defaultProvider : null);
const description = ref("");
const generating = ref(false);
const error = ref("");
let active = true;
onBeforeUnmount(() => { active = false; });

function useTemplate() {
  emit("apply", {
    name: tm("reuse.template"),
    port_contract_version: 2,
    input_contract: { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
    entry_task_ids: ["parse"],
    result_task_id: "parse",
    tasks: [{
      id: "parse", name: tm("graph.parse_json"), transform: "parse_json",
      inputs: { text: { input: "text" } },
      output_contract: { type: "object", properties: { title: { type: "string" } }, required: ["title"] },
      position: { x: 260, y: 60 },
    }],
  });
}

async function generate() {
  if (!provider.value || !description.value.trim() || generating.value) return;
  generating.value = true;
  error.value = "";
  try {
    const definition = await workflowAssetsApi.generateDraft({
      description: description.value.trim(), provider_id: provider.value,
    });
    if (active) emit("apply", definition);
  } catch (cause) {
    if (active) error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    if (active) generating.value = false;
  }
}
</script>
