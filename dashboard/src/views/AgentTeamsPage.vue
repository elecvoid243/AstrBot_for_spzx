<template>
  <div class="dashboard-page agent-teams-page">
    <v-container fluid class="dashboard-shell pa-4 pa-md-6">
      <div class="dashboard-header">
        <div class="dashboard-header-main">
          <div class="d-flex align-center flex-wrap ga-2">
            <h1 class="dashboard-title">{{ tm("page.title") }}</h1>
            <v-chip size="x-small" color="primary" variant="tonal" label>{{
              tm("page.beta")
            }}</v-chip>
          </div>
          <p class="dashboard-subtitle">{{ tm("workbench.intro") }}</p>
        </div>
      </div>
      <nav
        class="workbench-navigation"
        :aria-label="tm('workbench.navigation')"
      >
        <v-btn
          :variant="tab !== 'runs' ? 'tonal' : 'text'"
          color="primary"
          prepend-icon="mdi-folder-outline"
          @click="tab = 'assets'"
          >{{ tm("workbench.assets") }}</v-btn
        >
        <v-btn
          :variant="tab === 'runs' ? 'tonal' : 'text'"
          color="primary"
          prepend-icon="mdi-play-circle-outline"
          @click="tab = 'runs'"
          >{{ tm("workbench.runs") }}</v-btn
        >
      </nav>
      <section class="workbench-content" :aria-label="tm(tab === 'runs' ? 'workbench.runs' : 'workbench.assets')">
        <div v-show="tab === 'assets'" class="workbench-asset-navigation">
        <v-tabs
          v-model="assetTab"
          :mandatory="false"
          color="primary"
          density="comfortable"
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
          <RoleTemplatesPanel v-else-if="tab === 'assets' && assetTab === 'roles'" />
        </div>
      </section>
      <details
        class="workbench-legacy mt-6"
        :open="legacyOpen"
        @toggle="legacyOpen = ($event.target as HTMLDetailsElement).open"
      >
        <summary>{{ tm("workbench.legacy") }}</summary>
        <p class="text-body-2 text-medium-emphasis my-3">
          {{ tm("workbench.legacyHint") }}
        </p>
        <v-btn variant="text" prepend-icon="mdi-refresh" @click="refresh">{{
          tm("page.refresh")
        }}</v-btn>
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
            <v-tabs
              v-model="legacyTab"
              @update:model-value="legacyOpen = true"
              :mandatory="false"
              color="primary"
              density="comfortable"
              show-arrows
            >
              <v-tab value="editor">{{ tm("tabs.editor") }}</v-tab>
              <v-tab value="members">{{ tm("tabs.members") }}</v-tab>
              <v-tab value="monitor">{{ tm("tabs.monitor") }}</v-tab>
              <v-tab value="history">{{ tm("tabs.history") }}</v-tab>
            </v-tabs>
            <section
              v-if="legacyOpen && legacyTab === 'editor'"
              class="agent-teams-panel-editor mt-4"
            >
              <WorkflowEditor
                :team="selectedTeam"
                :workflows="workflows"
                @save-and-run="onSaveAndRun"
              />
            </section>
            <section
              v-if="legacyOpen && legacyTab === 'members'"
              class="agent-teams-panel-members mt-4"
            >
              <MemberConfigPanel
                v-if="selectedTeam"
                :team="selectedTeam"
                @update-team="onTeamMembersUpdated"
              />
              <div v-else class="members-empty">{{ tm("members.noTeam") }}</div>
            </section>
            <section
              v-if="legacyOpen && legacyTab === 'monitor'"
              class="agent-teams-panel-monitor mt-4"
            >
              <RunMonitor
                :team="selectedTeam"
                :initial-run="pendingRun"
                :preset-workflow-id="presetWorkflowId"
              />
            </section>
            <section
              v-if="legacyOpen && legacyTab === 'history'"
              class="agent-teams-panel-history mt-4"
            >
              <RunsHistory :team="selectedTeam" @open="onOpenRun" />
            </section>
          </div>
        </div>
      </details>
      <TeamCreateDialog
        v-model="showCreateDialog"
        :team="editingTeam"
        @saved="onTeamSaved"
      />
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
import '@/styles/agent-teams-workbench.css';
import { useRoute, useRouter } from 'vue-router';
import { agentTeamsApi } from '@/api/v1';
import AgentTeamsSidebar from '@/components/agent_teams/AgentTeamsSidebar.vue';
import MemberAddDialog from '@/components/agent_teams/MemberAddDialog.vue';
import MemberConfigPanel from '@/components/agent_teams/MemberConfigPanel.vue';
import RunMonitor from '@/components/agent_teams/RunMonitor.vue';
import RunsHistory from '@/components/agent_teams/RunsHistory.vue';
import TeamCreateDialog from '@/components/agent_teams/TeamCreateDialog.vue';
import WorkflowEditor from '@/components/agent_teams/WorkflowEditor.vue';
import RoleTemplatesPanel from '@/components/agent_teams/RoleTemplatesPanel.vue';
import WorkflowWorkspacePanel from '@/components/agent_teams/WorkflowWorkspacePanel.vue';
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

const route = useRoute();
const router = useRouter();
const tab = ref<'assets' | 'runs'>(route.query.workflow_run ? 'runs' : 'assets');
const assetTab = ref('assets');
const legacyTab = ref('editor');
const legacyOpen = ref(false);
watch(() => route.query.workflow_run, (runId) => {
  if (typeof runId === 'string' && runId) tab.value = 'runs';
});
watch(tab, (selected, previous) => {
  // Keep the same incoming run link usable after browsing another panel.
  if (previous === 'runs' && selected !== 'runs' && route.query.workflow_run) {
    const query = { ...route.query };
    delete query.workflow_run;
    void router.replace({ query });
  }
});
// Workflow preselected for the monitor by the legacy editor's save-and-run handoff.
const presetWorkflowId = ref('');
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

/**
 * Replace the selected team with the resolved team the member config panel
 * re-emitted after a save, so the sidebar member list and the other tabs see
 * the fresh member data (the panel already carries the server response).
 */
function onTeamMembersUpdated(team: any) {
  const index = teams.value.findIndex((t) => t.team_id === team?.team_id);
  if (index >= 0) {
    teams.value[index] = team;
  }
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
  legacyTab.value = 'monitor';
  legacyOpen.value = true;
}

/**
 * Hand a freshly saved workflow to the monitor tab (legacy editor save-and-run).
 *
 * Only the tab switch and the workflow preselection happen here — the user
 * still types the goal and presses Start Run, because a run needs an input.
 */
function onSaveAndRun(workflowId: string) {
  presetWorkflowId.value = workflowId;
  legacyTab.value = 'monitor';
  legacyOpen.value = true;
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

.members-empty {
  padding: 18px;
  border: 1px dashed var(--dashboard-border, rgba(128, 128, 128, 0.3));
  border-radius: 10px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.7));
  font-size: 13px;
  text-align: center;
}

@media (max-width: 900px) {
  .agent-teams-body {
    flex-direction: column;
  }

  .agent-teams-sidebar {
    width: 100%;
  }

  .agent-teams-main {
    width: 100%;
  }
}
</style>
