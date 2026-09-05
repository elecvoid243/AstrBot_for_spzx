// Module-singleton composable for the agent-teams directory (teams +
// workflows): all state lives at module level so the panel, dialogs and pages
// share one list and one selection.
// Every action normalizes the API envelope: error envelopes and network
// failures are toasted and mapped to null — nothing is ever re-thrown to the
// caller.
import { computed, ref } from 'vue';
import type { AxiosResponse } from 'axios';
import { agentTeamsApi } from '@/api/v1';
import type { ApiEnvelope } from '@/api/v1';
import { extractApiError } from '@/utils/extractApiError';
import { useToast } from '@/utils/toast';

export interface AgentTeamMember {
  member_id: string;
  session_id?: string;
  [key: string]: unknown;
}

export interface AgentTeam {
  team_id: string;
  name: string;
  members: AgentTeamMember[];
  coordinator_member_id?: string;
  config?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface AgentTeamWorkflow {
  workflow_id: string;
  name?: string;
  graph?: unknown;
  [key: string]: unknown;
}

// Module-level singleton state.
const teams = ref<AgentTeam[]>([]);
const selectedTeamId = ref<string | null>(null);
const workflows = ref<AgentTeamWorkflow[]>([]);

const selectedTeam = computed<AgentTeam | null>(
  () => teams.value.find((t) => t.team_id === selectedTeamId.value) ?? null,
);

/**
 * Run one agent-teams API call and normalize the envelope result.
 *
 * Error envelopes and network failures are toasted (never re-thrown) and
 * mapped to null, so actions stay safe for fire-and-forget callers.
 *
 * Args:
 *   call: Closure performing the API request.
 *
 * Returns:
 *   The envelope `data` payload on success, otherwise null.
 */
async function unwrapEnvelope(
  call: () => Promise<AxiosResponse<ApiEnvelope<any>>>,
): Promise<any | null> {
  const { error } = useToast();
  try {
    const res = await call();
    if (res.data.status === 'error') {
      error(res.data.message || 'Agent teams request failed');
      return null;
    }
    return res.data.data ?? null;
  } catch (err) {
    // Non-2xx responses arrive as axios rejections carrying the backend's
    // error envelope body; extractApiError prefers its message over axios's
    // generic "Request failed with status code N" (and handles the 429
    // plain-string rejection).
    error(extractApiError(err, 'Agent teams request failed').message);
    return null;
  }
}

/**
 * Load all teams owned by the current user.

 * Returns:
 *   The fresh teams array, or null on failure.
 */
async function loadTeams() {
  const data = await unwrapEnvelope(() => agentTeamsApi.listTeams());
  if (data === null) return null;
  teams.value = data.teams ?? [];
  return teams.value;
}

/** Switch the selected team (null deselects). */
function selectTeam(teamId: string | null) {
  selectedTeamId.value = teamId;
}

/**
 * Create a team, then refresh the list from the server.

 * Returns:
 *   The created team, or null on failure.
 */
async function createTeam(payload: Partial<AgentTeam> & { name: string }) {
  const data = await unwrapEnvelope(() => agentTeamsApi.createTeam(payload));
  if (data === null) return null;
  await loadTeams();
  return data;
}

/**
 * Update a team and replace its row in the list with the server response.

 * Returns:
 *   The updated team, or null on failure.
 */
async function updateTeam(teamId: string, payload: Partial<AgentTeam>) {
  const data = await unwrapEnvelope(() => agentTeamsApi.updateTeam(teamId, payload));
  if (data === null) return null;
  const index = teams.value.findIndex((t) => t.team_id === teamId);
  if (index >= 0) teams.value[index] = data;
  return data;
}

/**
 * Delete a team, drop it from the list and clear a matching selection.

 * Returns:
 *   The envelope data payload, or null on failure.
 */
async function deleteTeam(teamId: string) {
  const data = await unwrapEnvelope(() => agentTeamsApi.deleteTeam(teamId));
  if (data === null) return null;
  teams.value = teams.value.filter((t) => t.team_id !== teamId);
  if (selectedTeamId.value === teamId) selectedTeamId.value = null;
  return data;
}

/**
 * Load the workflows of one team.

 * Returns:
 *   The workflows array, or null on failure.
 */
async function loadWorkflows(teamId: string) {
  const data = await unwrapEnvelope(() => agentTeamsApi.listWorkflows(teamId));
  if (data === null) return null;
  workflows.value = data.workflows ?? [];
  return workflows.value;
}

/**
 * Create or update a workflow (routed by the presence of `workflow_id`),
 * then refresh the workflows list.

 * Returns:
 *   The saved workflow, or null on failure.
 */
async function saveWorkflow(teamId: string, wf: Partial<AgentTeamWorkflow>) {
  const data = await unwrapEnvelope(() =>
    wf.workflow_id
      ? agentTeamsApi.updateWorkflow(teamId, wf.workflow_id, wf)
      : agentTeamsApi.createWorkflow(teamId, wf),
  );
  if (data === null) return null;
  await loadWorkflows(teamId);
  return data;
}

/**
 * Delete a workflow and drop it from the workflows list.

 * Returns:
 *   The envelope data payload, or null on failure.
 */
async function deleteWorkflow(teamId: string, workflowId: string) {
  const data = await unwrapEnvelope(() =>
    agentTeamsApi.deleteWorkflow(teamId, workflowId),
  );
  if (data === null) return null;
  workflows.value = workflows.value.filter((w) => w.workflow_id !== workflowId);
  return data;
}

export function useAgentTeams() {
  return {
    teams,
    selectedTeamId,
    selectedTeam,
    workflows,
    loadTeams,
    selectTeam,
    createTeam,
    updateTeam,
    deleteTeam,
    loadWorkflows,
    saveWorkflow,
    deleteWorkflow,
  };
}
