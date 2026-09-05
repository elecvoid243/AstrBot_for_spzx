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
          @edit="onEditTeam"
          @add-member="onAddMember"
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
              <section v-if="tab === 'editor'" class="agent-teams-panel-editor">
                <WorkflowEditor :team="selectedTeam" :workflows="workflows" />
              </section>
            </v-window-item>
            <v-window-item value="monitor">
              <section v-if="tab === 'monitor'" class="agent-teams-panel-monitor">
                <RunMonitor :team="selectedTeam" :initial-run="pendingRun" />
              </section>
            </v-window-item>
            <v-window-item value="history">
              <section v-if="tab === 'history'" class="agent-teams-panel-history">
                <RunsHistory :team="selectedTeam" @open="onOpenRun" />
              </section>
            </v-window-item>
          </v-window>
        </div>
      </div>

      <TeamCreateDialog v-model="showCreateDialog" :team="editingTeam" @saved="onTeamSaved" />
      <MemberAddDialog
        v-model="showMemberDialog"
        :team-id="selectedTeam?.team_id ?? ''"
        @saved="onMemberSaved"
      />
    </v-container>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import { agentTeamsApi } from '@/api/v1';
import AgentTeamsSidebar from '@/components/agent_teams/AgentTeamsSidebar.vue';
import MemberAddDialog from '@/components/agent_teams/MemberAddDialog.vue';
import RunMonitor from '@/components/agent_teams/RunMonitor.vue';
import RunsHistory from '@/components/agent_teams/RunsHistory.vue';
import TeamCreateDialog from '@/components/agent_teams/TeamCreateDialog.vue';
import WorkflowEditor from '@/components/agent_teams/WorkflowEditor.vue';
import { useAgentTeams } from '@/composables/useAgentTeams';
import { useAgentTeamsRun } from '@/composables/useAgentTeamsRun';
import { useModuleI18n } from '@/i18n/composables';
import { askForConfirmation, useConfirmDialog } from '@/utils/confirmDialog';
import { extractApiError } from '@/utils/extractApiError';
import { useToast } from '@/utils/toast';

const { tm } = useModuleI18n('features/agent-teams');
const {
  teams,
  selectedTeamId,
  selectedTeam,
  workflows,
  loadTeams,
  loadWorkflows,
  selectTeam,
} = useAgentTeams();
const { openRun } = useAgentTeamsRun();
const confirmDialog = useConfirmDialog();

const tab = ref('editor');
const showCreateDialog = ref(false);
// Team handed to TeamCreateDialog: null = create mode, a team = edit mode
// (the dialog edits name/coordinator/config only). Set by the sidebar's
// create button vs. the selected team row's edit button.
const editingTeam = ref<any | null>(null);
const showMemberDialog = ref(false);
// History row handed to the monitor when a run is opened from the history
// tab: RunMonitor seeds its DAG view from the row's graph snapshot and skips
// mount recovery when the attached run matches this row (openRun has already
// attached it).
const pendingRun = ref<any | null>(null);

/** Reload the team list plus the selected team's workflows. */
async function refresh() {
  await loadTeams();
  if (selectedTeamId.value) {
    await loadWorkflows(selectedTeamId.value);
  }
}

function onCreate() {
  editingTeam.value = null;
  showCreateDialog.value = true;
}

/** Edit the selected team through TeamCreateDialog's edit mode. */
function onEditTeam() {
  if (!selectedTeam.value) return;
  editingTeam.value = selectedTeam.value;
  showCreateDialog.value = true;
}

function onAddMember() {
  if (!selectedTeam.value) return;
  showMemberDialog.value = true;
}

/** After the create dialog saved, refresh the list and select the team. */
async function onTeamSaved(team: any) {
  await loadTeams();
  if (team?.team_id) {
    selectTeam(team.team_id);
  }
}

/** After a member was added, refresh the list to pick up the new member. */
async function onMemberSaved() {
  await loadTeams();
}

/** Confirm, remove one member from the selected team and refresh the list. */
async function onRemoveMember(memberId: string) {
  const team = selectedTeam.value;
  if (!team) return;
  const memberName = String(
    (team.members ?? []).find((m: any) => m.member_id === memberId)?.name ?? memberId,
  );
  const ok = await askForConfirmation(
    tm('members.removeConfirm', { name: memberName }),
    confirmDialog,
  );
  if (!ok) return;
  const { error } = useToast();
  try {
    const res = await agentTeamsApi.removeMember(team.team_id, memberId);
    if (res.data.status === 'error') {
      error(res.data.message || tm('errors.operationFailed'));
      return;
    }
    await loadTeams();
  } catch (err) {
    // Non-2xx rejections carry the backend's error envelope; surface its
    // message instead of axios's generic "Request failed with status code N".
    error(extractApiError(err, tm('errors.operationFailed')).message);
  }
}

/**
 * Open a run from the history tab in the monitor: the history row seeds the
 * run state and DAG instantly, and the SSE stream replays the event history
 * on top. The row is also passed down to RunMonitor so its mount recovery
 * keeps the explicitly opened run instead of clobbering it.
 */
function onOpenRun(runId: string, row: any) {
  pendingRun.value = row ?? null;
  void openRun(runId, row);
  tab.value = 'monitor';
}

// Reload the workflows whenever the selected team changes so the editor's
// workflow picker always reflects the active team. A pending history row
// belongs to the previous team and is dropped.
watch(selectedTeamId, (teamId) => {
  pendingRun.value = null;
  if (teamId) {
    void loadWorkflows(teamId);
  }
});

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

@media (max-width: 900px) {
  .agent-teams-body {
    flex-direction: column;
  }

  .agent-teams-sidebar {
    width: 100%;
  }
}
</style>
