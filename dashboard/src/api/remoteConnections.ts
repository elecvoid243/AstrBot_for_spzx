import { apiV1Client, fetchWithAuth } from "./http";
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
export interface RemoteResourceScope {
  connectionId: string;
  revision: string;
  sessionId: string;
}
export interface RemoteUpload {
  attachment_id: string;
  filename: string;
  type: string;
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
  creation: (id: string, revision: string) =>
    apiV1Client.get<
      ApiEnvelope<{
        allowed: boolean;
        projects: { project_id: string; title: string }[];
      }>
    >(`${path(id)}/creation`, { params: { revision } }),
  createSession: (
    id: string,
    revision: string,
    title: string,
    project_id: string | null,
  ) =>
    apiV1Client.post<ApiEnvelope<{ session_id: string }>>(
      `${path(id)}/sessions`,
      { revision, title, project_id },
    ),
  send: (
    id: string,
    session: string,
    text: string,
    revision: string,
    request_id: string,
    attachment_ids: string[] = [],
  ) =>
    apiV1Client.post(
      `${path(id)}/sessions/${encodeURIComponent(session)}/messages`,
      { text, revision, request_id, attachment_ids },
    ),
  receipt: (id: string, session: string, revision: string, requestId: string) =>
    apiV1Client.get<ApiEnvelope<{ status: string }>>(
      `${path(id)}/sessions/${encodeURIComponent(
        session,
      )}/receipts/${encodeURIComponent(requestId)}`,
      { params: { revision } },
    ),
  stop: (id: string, session: string, revision: string) =>
    apiV1Client.post(
      `${path(id)}/sessions/${encodeURIComponent(session)}/stop`,
      { revision },
    ),
  events: (
    id: string,
    session: string,
    revision: string,
    signal: AbortSignal,
  ) =>
    fetchWithAuth(
      `/api/v1${path(id)}/sessions/${encodeURIComponent(
        session,
      )}/events?revision=${encodeURIComponent(revision)}`,
      { signal },
    ),
  subagentEvents: (
    scope: RemoteResourceScope,
    runId: string | null,
    signal: AbortSignal,
  ) =>
    fetchWithAuth(
      `/api/v1${path(scope.connectionId)}/sessions/${encodeURIComponent(
        scope.sessionId,
      )}/subagent-runs/events?revision=${encodeURIComponent(scope.revision)}${
        runId ? `&run_id=${encodeURIComponent(runId)}` : ""
      }`,
      { signal },
    ),
  subagentConfiguration: (
    scope: RemoteResourceScope,
    runId: string,
    signal: AbortSignal,
  ) =>
    apiV1Client.get<ApiEnvelope<any>>(
      `${path(scope.connectionId)}/sessions/${encodeURIComponent(
        scope.sessionId,
      )}/subagent-runs/${encodeURIComponent(runId)}/configuration`,
      { params: { revision: scope.revision }, signal },
    ),
  fileDiff: (
    scope: RemoteResourceScope,
    filePath: string,
    source: { message_id?: string | number; run_id?: string },
  ) =>
    apiV1Client.get<ApiEnvelope<any>>(
      `${path(scope.connectionId)}/sessions/${encodeURIComponent(
        scope.sessionId,
      )}/file-diff`,
      {
        params: {
          revision: scope.revision,
          path: filePath,
          message_id: source.message_id,
          run_id: source.run_id,
        },
      },
    ),
  shellOutput: (scope: RemoteResourceScope, shellId: string, cursor: number) =>
    apiV1Client.get<ApiEnvelope<any>>(
      `${path(scope.connectionId)}/sessions/${encodeURIComponent(
        scope.sessionId,
      )}/shell-sessions/${encodeURIComponent(shellId)}/output`,
      { params: { revision: scope.revision, cursor } },
    ),
  upload: (scope: RemoteResourceScope, file: File) => {
    const data = new FormData();
    data.append("file", file);
    return apiV1Client.post<ApiEnvelope<RemoteUpload>>(
      `${path(scope.connectionId)}/sessions/${encodeURIComponent(
        scope.sessionId,
      )}/attachments`,
      data,
      { params: { revision: scope.revision } },
    );
  },
  attachment: (scope: RemoteResourceScope, id: string, signal?: AbortSignal) =>
    apiV1Client.get<Blob>(
      `${path(scope.connectionId)}/sessions/${encodeURIComponent(
        scope.sessionId,
      )}/attachments/${encodeURIComponent(id)}`,
      { params: { revision: scope.revision }, responseType: "blob", signal },
    ),
  answer: (
    scope: RemoteResourceScope,
    id: string,
    answer: { choice_id: string; free_text: string },
  ) =>
    apiV1Client.post(
      `${path(scope.connectionId)}/sessions/${encodeURIComponent(
        scope.sessionId,
      )}/human-choices/${encodeURIComponent(id)}`,
      { ...answer, revision: scope.revision },
    ),
  cancelChoice: (scope: RemoteResourceScope, id: string) =>
    apiV1Client.delete(
      `${path(scope.connectionId)}/sessions/${encodeURIComponent(
        scope.sessionId,
      )}/human-choices/${encodeURIComponent(id)}`,
      { params: { revision: scope.revision } },
    ),
  sessions: (id: string) =>
    apiV1Client.get<ApiEnvelope<RemoteSession[]>>(`${path(id)}/sessions`),
  history: (id: string, session: string, before?: number, limit = 50) =>
    apiV1Client.get<ApiEnvelope<RemoteHistory>>(
      `${path(id)}/sessions/${encodeURIComponent(session)}/history`,
      { params: { limit, before_id: before } },
    ),
};
