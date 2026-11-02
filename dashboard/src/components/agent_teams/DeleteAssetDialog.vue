<template>
  <v-dialog
    :model-value="!!name"
    max-width="520"
    @update:model-value="!$event && $emit('cancel')"
  >
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
        tm("deletion.title")
      }}</v-card-title>
      <v-card-text>
        <strong>{{ name }}</strong>
        <p class="mt-3">{{ tm("deletion.impact") }}</p>
        <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{
          error
        }}</v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="busy" @click="$emit('cancel')">{{
          tm("deletion.cancel")
        }}</v-btn>
        <v-btn
          data-testid="confirm-asset-deletion"
          variant="tonal"
          color="error"
          :loading="busy"
          @click="$emit('confirm')"
          >{{ tm("deletion.delete") }}</v-btn
        >
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { useModuleI18n } from "@/i18n/composables";
defineProps<{ name: string | null; busy: boolean; error: string }>();
defineEmits<{ cancel: []; confirm: [] }>();
const { tm } = useModuleI18n("features/agent-teams");
</script>
