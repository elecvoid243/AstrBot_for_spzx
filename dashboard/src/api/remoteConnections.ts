import { apiV1Client } from "./http";
import type { ApiEnvelope } from "./v1";
import type { ChatRecord } from "@/composables/useMessages";

export interface RemoteConnection {
  connection_id: string;
  revision: string;
  name: string;
  base_url: string;
}
export interface RemoteSession {
  session_id: string;
  display_name: string;
  project_id: string | null;
  project_name: string | null;
  project_emoji: string | null;
  archived: boolean;
}
export interface RemoteHistory {
  history: ChatRecord[];
  has_more: boolean;
  next_before_id: number | null;
}
const path = (id: string) =>
  `/chat/remote-connections/${encodeURIComponent(id)}`;
export const remoteConnectionsApi = {
  list: () =>
    apiV1Client.get<
      ApiEnvelope<{ enabled: boolean; connections: RemoteConnection[] }>
    >("/chat/remote-connections"),
  save: (
    data: { name: string; base_url: string; key?: string },
    id?: string,
  ) =>
    id
      ? apiV1Client.put(path(id), data)
      : apiV1Client.post("/chat/remote-connections", data),
  remove: (id: string) => apiV1Client.delete(path(id)),
  sessions: (id: string) =>
    apiV1Client.get<ApiEnvelope<RemoteSession[]>>(`${path(id)}/sessions`),
  history: (id: string, session: string, before?: number, limit = 50) =>
    apiV1Client.get<ApiEnvelope<RemoteHistory>>(
      `${path(id)}/sessions/${encodeURIComponent(session)}/history`,
      { params: { limit, before_id: before } },
    ),
};
