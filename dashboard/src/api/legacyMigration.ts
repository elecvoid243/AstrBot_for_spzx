import { httpClient } from "./http";
import type { LegacyMigrationRequest } from "./generated/openapi-v1";

export const legacyMigrationApi = {
  async preview(teamId: string) {
    return (
      await httpClient.get(
        `/api/v1/agent_teams/${encodeURIComponent(teamId)}/migration/preview`,
      )
    ).data.data;
  },
  async confirm(teamId: string, payload: LegacyMigrationRequest) {
    return (
      await httpClient.post(
        `/api/v1/agent_teams/${encodeURIComponent(teamId)}/migration`,
        payload,
      )
    ).data.data;
  },
};
