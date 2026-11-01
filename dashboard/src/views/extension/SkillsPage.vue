<script setup lang="ts">
import { onMounted, ref } from "vue";
import { skillApi } from "@/api/v1";
import SkillsSection from "@/components/extension/SkillsSection.vue";
import { useModuleI18n } from "@/i18n/composables";

const { tm } = useModuleI18n("features/extension");
const storeUrl = ref("");

onMounted(async () => {
  try {
    const response = await skillApi.store();
    const store = response.data?.data;
    if (store?.available && store.url) storeUrl.value = store.url;
  } catch {
    // An optional store must not prevent installed skills from loading.
    storeUrl.value = "";
  }
});
</script>

<template>
  <div class="plugin-section-page">
    <div class="mb-4 pt-4 pb-4">
      <div class="d-flex flex-column" style="gap: 6px">
        <h2 class="text-h2 mb-0">{{ tm("tabs.skills") }}</h2>
        <div class="text-body-2 text-medium-emphasis">
          {{ tm("skills.runtimeHint") }}
        </div>
      </div>
    </div>
    <v-btn
      v-if="storeUrl"
      :href="storeUrl"
      variant="tonal"
      append-icon="mdi-arrow-right"
      class="mb-4"
    >
      {{ tm("skills.openStore") }}
    </v-btn>
    <SkillsSection />
  </div>
</template>

<style scoped>
.plugin-section-page {
  margin: 0 auto;
  max-width: 1200px;
  padding: 0 12px;
  width: 100%;
}
</style>
