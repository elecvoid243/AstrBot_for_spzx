import { httpClient } from "./http";
import type { RoleSaveRequest } from "./generated/openapi-v1";

export const roleAssetsApi = {
  async list() {
    return (await httpClient.get("/api/v1/agent_roles")).data.data;
  },
  async resources() {
    return (await httpClient.get("/api/v1/agent_roles/resources")).data.data;
  },
  async get(roleId: string, version?: number) {
    return (
      await httpClient.get(
        `/api/v1/agent_roles/${encodeURIComponent(roleId)}`,
        { params: { version } },
      )
    ).data.data;
  },
  async preview(payload: RoleSaveRequest) {
    return (await httpClient.post("/api/v1/agent_roles/preview", payload)).data
      .data;
  },
  async resolve(
    roleId: string,
    version: number,
    overrides: Record<string, unknown>,
  ) {
    return (
      await httpClient.post(
        `/api/v1/agent_roles/${encodeURIComponent(roleId)}/resolve`,
        overrides,
        { params: { version } },
      )
    ).data.data;
  },
  async save(roleId: string | null, payload: RoleSaveRequest) {
    return (
      roleId
        ? await httpClient.put(
            `/api/v1/agent_roles/${encodeURIComponent(roleId)}`,
            payload,
          )
        : await httpClient.post("/api/v1/agent_roles", payload)
    ).data.data;
  },
};
