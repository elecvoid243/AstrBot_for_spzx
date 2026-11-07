<template>
  <div class="dashboard-page agent-teams-page">
    <v-container fluid class="dashboard-shell pa-4 pa-md-6">
      <div class="dashboard-header">
        <div class="dashboard-header-main at-page-heading">
          <span class="at-page-mark" aria-hidden="true"><v-icon icon="mdi-graph-outline" size="24" /></span>
          <div>
          <div class="d-flex align-center flex-wrap ga-2">
            <h1 class="dashboard-title">{{ tm("page.title") }}</h1>
            <v-chip size="x-small" color="orange-darken-2" variant="tonal" label>
              {{ tm("page.incomplete") }}
            </v-chip>
          </div>
          <p class="dashboard-subtitle">{{ tm("workbench.intro") }}</p>
          </div>
        </div>
      </div>
      <nav
        class="workbench-navigation"
        :aria-label="tm('workbench.navigation')"
      >
        <v-btn
          :variant="tab !== 'runs' ? 'tonal' : 'text'"
          :aria-pressed="tab !== 'runs'"
          color="primary"
          prepend-icon="mdi-folder-outline"
          @click="tab = 'assets'"
          >{{ tm("workbench.assets") }}</v-btn
        >
        <v-btn
          :variant="tab === 'runs' ? 'tonal' : 'text'"
          :aria-pressed="tab === 'runs'"
          color="primary"
          prepend-icon="mdi-play-circle-outline"
          @click="tab = 'runs'"
          >{{ tm("workbench.runs") }}</v-btn
        >
      </nav>
      <section
        class="workbench-content"
        :aria-label="tm(tab === 'runs' ? 'workbench.runs' : 'workbench.assets')"
      >
        <div v-show="tab === 'assets'" class="workbench-asset-navigation">
          <v-tabs
            v-model="assetTab"
            :mandatory="false"
            color="primary"
            height="32"
            show-arrows
          >
            <v-tab value="teams">{{ tm("workbench.teams") }}</v-tab>
            <v-tab value="assets">{{ tm("workbench.workflows") }}</v-tab>
            <v-tab value="roles">{{ tm("roles.title") }}</v-tab>
          </v-tabs>
        </div>
        <div class="workbench-panel">
          <WorkflowWorkspacePanel
            v-if="tab === 'assets' && assetTab !== 'roles'"
            :key="assetTab"
            mode="assets"
            :asset-kind="assetTab === 'teams' ? 'teams' : 'workflows'"
            @started="tab = 'runs'"
            @installed="assetTab = $event === 'teams' ? 'teams' : 'assets'"
          />
          <WorkflowWorkspacePanel v-else-if="tab === 'runs'" mode="runs" />
          <RoleTemplatesPanel
            v-else-if="tab === 'assets' && assetTab === 'roles'"
          />
        </div>
      </section>
    </v-container>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import "@/styles/agent-teams-workbench.css";
import { useRoute, useRouter } from "vue-router";
import RoleTemplatesPanel from "@/components/agent_teams/RoleTemplatesPanel.vue";
import WorkflowWorkspacePanel from "@/components/agent_teams/WorkflowWorkspacePanel.vue";
import { useModuleI18n } from "@/i18n/composables";

const { tm } = useModuleI18n("features/agent-teams");
const route = useRoute();
const router = useRouter();
const tab = ref<"assets" | "runs">(
  route.query.workflow_run ? "runs" : "assets",
);
const assetTab = ref("assets");
watch(
  () => route.query.workflow_run,
  (runId) => {
    if (typeof runId === "string" && runId) tab.value = "runs";
  },
);
watch(tab, (selected, previous) => {
  // Keep an incoming run link usable after browsing another panel.
  if (previous === "runs" && selected !== "runs" && route.query.workflow_run) {
    const query = { ...route.query };
    delete query.workflow_run;
    void router.replace({ query });
  }
});
</script>

<style scoped>
.agent-teams-page {
  background: var(--at-paper);
  min-height: calc(100vh - 64px);
}
</style>
