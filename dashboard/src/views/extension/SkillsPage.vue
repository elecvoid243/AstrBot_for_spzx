<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { skillApi } from "@/api/v1";
import { useCustomizerStore } from "@/stores/customizer";
import SkillsSection from "@/components/extension/SkillsSection.vue";
import { useModuleI18n } from "@/i18n/composables";

const { tm } = useModuleI18n("features/extension");
const customizer = useCustomizerStore();
const storeUrl = ref("");
const section = ref("installed");
const iframeSrc = computed(() => {
  if (!storeUrl.value) return "";
  const url = new URL(storeUrl.value);
  url.searchParams.set("theme", customizer.isDark ? "dark" : "light");
  return url.toString();
});

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
    <v-btn-toggle
      v-if="storeUrl"
      v-model="section"
      mandatory
      density="comfortable"
      class="mb-4"
    >
      <v-btn value="installed" variant="text">{{
        tm("status.installed")
      }}</v-btn>
      <v-btn value="store" variant="text">{{ tm("skills.store") }}</v-btn>
    </v-btn-toggle>
    <SkillsSection v-show="section === 'installed'" />
    <iframe
      v-if="storeUrl && section === 'store'"
      :src="iframeSrc"
      :title="tm('skills.store')"
      class="skill-store-frame"
      referrerpolicy="no-referrer"
      sandbox="allow-scripts allow-forms allow-downloads allow-same-origin allow-popups"
    />
  </div>
</template>

<style scoped>
.plugin-section-page {
  margin: 0 auto;
  max-width: 1200px;
  padding: 0 12px;
  width: 100%;
}
.skill-store-frame {
  display: block;
  width: 100%;
  height: calc(100dvh - 240px);
  min-height: 480px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 12px;
  background: rgb(var(--v-theme-surface));
}
</style>
