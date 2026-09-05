<template>
  <div class="dashboard-page agent-teams-page">
    <v-container fluid class="dashboard-shell pa-4 pa-md-6">
      <div class="dashboard-header">
        <div class="dashboard-header-main">
          <div class="d-flex align-center flex-wrap" style="gap: 8px;">
            <h1 class="dashboard-title">{{ tm('page.title') }}</h1>
            <v-chip size="x-small" color="orange-darken-2" variant="tonal" label>
              {{ tm('page.beta') }}
            </v-chip>
          </div>
        </div>

        <div class="dashboard-header-actions">
          <v-btn variant="text" color="primary" prepend-icon="mdi-refresh" @click="refresh">
            {{ tm('page.refresh') }}
          </v-btn>
        </div>
      </div>

      <div class="agent-teams-body">
        <AgentTeamsSidebar
          class="agent-teams-sidebar"
          :teams="teams"
          :selected-team-id="selectedTeamId"
          :selected-team="selectedTeam"
          @select="selectTeam"
          @create="onCreate"
          @remove-member="onRemoveMember"
        />

        <div class="agent-teams-main">
          <v-tabs v-model="tab" color="primary" density="comfortable">
            <v-tab value="editor">{{ tm('tabs.editor') }}</v-tab>
            <v-tab value="monitor">{{ tm('tabs.monitor') }}</v-tab>
            <v-tab value="history">{{ tm('tabs.history') }}</v-tab>
          </v-tabs>

          <v-window v-model="tab" class="mt-4">
            <v-window-item value="editor">
              <!-- Task 7 replaces this -->
              <section v-if="tab === 'editor'" class="agent-teams-panel agent-teams-panel-editor">
                {{ tm('tabs.editor') }}
              </section>
            </v-window-item>
            <v-window-item value="monitor">
              <!-- Task 8 replaces this -->
              <section v-if="tab === 'monitor'" class="agent-teams-panel agent-teams-panel-monitor">
                {{ tm('tabs.monitor') }}
              </section>
            </v-window-item>
            <v-window-item value="history">
              <!-- Task 9 replaces this -->
              <section v-if="tab === 'history'" class="agent-teams-panel agent-teams-panel-history">
                {{ tm('tabs.history') }}
              </section>
            </v-window-item>
          </v-window>
        </div>
      </div>
    </v-container>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { agentTeamsApi } from '@/api/v1';
import AgentTeamsSidebar from '@/components/agent_teams/AgentTeamsSidebar.vue';
import { useAgentTeams } from '@/composables/useAgentTeams';
import { useModuleI18n } from '@/i18n/composables';
import { useToast } from '@/utils/toast';

const { tm } = useModuleI18n('features/agent-teams');
const {
  teams,
  selectedTeamId,
  selectedTeam,
  loadTeams,
  loadWorkflows,
  selectTeam,
} = useAgentTeams();

const tab = ref('editor');

/** Reload the team list plus the selected team's workflows. */
async function refresh() {
  await loadTeams();
  if (selectedTeamId.value) {
    await loadWorkflows(selectedTeamId.value);
  }
}

// Task 9 mounts TeamCreateDialog here and replaces this placeholder.
function onCreate() {}

/** Remove one member from the selected team and refresh the list. */
async function onRemoveMember(memberId: string) {
  const team = selectedTeam.value;
  if (!team) return;
  const { error } = useToast();
  try {
    const res = await agentTeamsApi.removeMember(team.team_id, memberId);
    if (res.data.status === 'error') {
      error(res.data.message || tm('errors.operationFailed'));
      return;
    }
    await loadTeams();
  } catch (err) {
    error(err instanceof Error ? err.message : String(err));
  }
}

onMounted(() => {
  refresh();
});
</script>

<style scoped>
@import '@/styles/dashboard-shell.css';

.agent-teams-page {
  padding-bottom: 40px;
}

.agent-teams-body {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  margin-top: 16px;
}

.agent-teams-sidebar {
  width: 300px;
  flex-shrink: 0;
}

.agent-teams-main {
  flex: 1;
  min-width: 0;
}

.agent-teams-panel {
  min-height: 320px;
  padding: 24px;
  border: 1px dashed var(--dashboard-border, rgba(0, 0, 0, 0.12));
  border-radius: 12px;
  color: var(--dashboard-muted, rgba(0, 0, 0, 0.55));
  font-size: 14px;
}

@media (max-width: 900px) {
  .agent-teams-body {
    flex-direction: column;
  }

  .agent-teams-sidebar {
    width: 100%;
  }
}
</style>
