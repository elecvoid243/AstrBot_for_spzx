import { httpClient } from "./http";
import type {
  WorkflowSaveRequest,
  WorkflowStartRequest,
  ArtifactRevisionRequest,
  ToolResolutionRequest,
  WorkflowControlRequest,
  WorkflowRetryRequest,
  HumanAnswerRequest,
  WorkflowImportRequest,
  ChatAccessRequest,
  TrialRunRequest,
} from "./generated/openapi-v1";

export const workflowAssetsApi = {
  async delete(assetId: string) {
    return (
      await httpClient.delete(
        `/api/v1/workflow_assets/${encodeURIComponent(assetId)}`,
      )
    ).data.data;
  },
  async history(offset = 0) {
    return (
      await httpClient.get("/api/v1/agent_history", { params: { offset } })
    ).data.data;
  },
  async chatAccess(assetId: string): Promise<{ grants: ChatAccessRequest[] }> {
    return (
      await httpClient.get(
        `/api/v1/workflow_assets/${encodeURIComponent(assetId)}/chat_access`,
      )
    ).data.data;
  },
  async saveChatAccess(assetId: string, payload: ChatAccessRequest) {
    return (
      await httpClient.put(
        `/api/v1/workflow_assets/${encodeURIComponent(assetId)}/chat_access`,
        payload,
      )
    ).data.data;
  },
  async revokeChatAccess(
    assetId: string,
    platformId: string,
    senderId: string,
  ) {
    return (
      await httpClient.delete(
        `/api/v1/workflow_assets/${encodeURIComponent(
          assetId,
        )}/chat_access/${encodeURIComponent(platformId)}/${encodeURIComponent(
          senderId,
        )}`,
      )
    ).data.data;
  },
  async exportPackage(assetId: string, version: number) {
    return (
      await httpClient.get(
        `/api/v1/workflow_assets/${encodeURIComponent(assetId)}/export`,
        { params: { version } },
      )
    ).data.data;
  },
  async previewImport(payload: WorkflowImportRequest) {
    return (
      await httpClient.post("/api/v1/workflow_assets/import/preview", payload)
    ).data.data;
  },
  async importPackage(payload: WorkflowImportRequest) {
    return (await httpClient.post("/api/v1/workflow_assets/import", payload))
      .data.data;
  },
  async list() {
    return (await httpClient.get("/api/v1/workflow_assets")).data.data;
  },
  async get(assetId: string, version?: number) {
    return (
      await httpClient.get(
        `/api/v1/workflow_assets/${encodeURIComponent(assetId)}`,
        { params: { version } },
      )
    ).data.data;
  },
  async save(assetId: string | null, payload: WorkflowSaveRequest) {
    return (
      assetId
        ? await httpClient.put(
            `/api/v1/workflow_assets/${encodeURIComponent(assetId)}`,
            payload,
          )
        : await httpClient.post("/api/v1/workflow_assets", payload)
    ).data.data;
  },
  async start(assetId: string, payload: WorkflowStartRequest) {
    return (
      await httpClient.post(
        `/api/v1/workflow_assets/${encodeURIComponent(assetId)}/runs`,
        payload,
      )
    ).data.data;
  },
  async previewTrial(assetId: string, payload: TrialRunRequest) {
    return (
      await httpClient.post(
        `/api/v1/workflow_assets/${encodeURIComponent(assetId)}/trials/preview`,
        payload,
      )
    ).data.data;
  },
  async startTrial(assetId: string, payload: TrialRunRequest) {
    return (
      await httpClient.post(
        `/api/v1/workflow_assets/${encodeURIComponent(assetId)}/trials`,
        payload,
      )
    ).data.data;
  },
  async humanRequests() {
    return (await httpClient.get("/api/v1/workflow_human_requests")).data.data;
  },
  async runs() {
    return (await httpClient.get("/api/v1/workflow_runs")).data.data;
  },
  async readiness(assetId: string, version: number) {
    return (
      await httpClient.get(
        `/api/v1/workflow_assets/${encodeURIComponent(assetId)}/readiness`,
        { params: { version } },
      )
    ).data.data;
  },
  async run(runId: string) {
    return (
      await httpClient.get(`/api/v1/workflow_runs/${encodeURIComponent(runId)}`)
    ).data.data;
  },
  async runHistoryPage(runId: string, taskRunId: string, kind: "events" | "messages" | "usage" | "attempts", before?: string | null) {
    return (await httpClient.get(
      `/api/v1/workflow_runs/${encodeURIComponent(runId)}/history`,
      { params: { task_run_id: taskRunId, kind, before } },
    )).data.data;
  },
  async processPage(runId: string, taskRunId: string, before: number) {
    return (await httpClient.get(
      `/api/v1/workflow_runs/${encodeURIComponent(runId)}/process`,
      { params: { task_run_id: taskRunId, before } },
    )).data.data;
  },
  async events(runId: string, after: number) {
    return (
      await httpClient.get(
        `/api/v1/workflow_runs/${encodeURIComponent(runId)}/events`,
        { params: { after } },
      )
    ).data.data;
  },
  async recover(runId: string) {
    return (
      await httpClient.post(
        `/api/v1/workflow_runs/${encodeURIComponent(runId)}/recover`,
      )
    ).data.data;
  },
  async control(runId: string, payload: WorkflowControlRequest) {
    return (
      await httpClient.post(
        `/api/v1/workflow_runs/${encodeURIComponent(runId)}/control`,
        payload,
      )
    ).data.data;
  },
  async retry(runId: string, payload: WorkflowRetryRequest) {
    return (
      await httpClient.post(
        `/api/v1/workflow_runs/${encodeURIComponent(runId)}/retry`,
        payload,
      )
    ).data.data;
  },
  async answer(runId: string, todoId: string, payload: HumanAnswerRequest) {
    return (
      await httpClient.post(
        `/api/v1/workflow_runs/${encodeURIComponent(
          runId,
        )}/todos/${encodeURIComponent(todoId)}/answer`,
        payload,
      )
    ).data.data;
  },
  async export(artifactId: string, version: number) {
    return (
      await httpClient.get(
        `/api/v1/workflow_artifacts/${encodeURIComponent(
          artifactId,
        )}/versions/${version}/export`,
      )
    ).data.data;
  },
  async artifact(artifactId: string, version: number) {
    return (
      await httpClient.get(
        `/api/v1/workflow_artifacts/${encodeURIComponent(
          artifactId,
        )}/versions/${version}`,
      )
    ).data.data;
  },
  async resolveTool(
    runId: string,
    recordId: string,
    payload: ToolResolutionRequest,
  ) {
    return (
      await httpClient.post(
        `/api/v1/workflow_runs/${encodeURIComponent(
          runId,
        )}/tool_calls/${encodeURIComponent(recordId)}/resolve`,
        payload,
      )
    ).data.data;
  },
  async revise(artifactId: string, payload: ArtifactRevisionRequest) {
    return (
      await httpClient.post(
        `/api/v1/workflow_artifacts/${encodeURIComponent(artifactId)}/versions`,
        payload,
      )
    ).data.data;
  },
  async storeFile(runId: string, file: File) {
    return (
      await httpClient.post(
        `/api/v1/workflow_runs/${encodeURIComponent(runId)}/files`,
        file,
        {
          params: { filename: file.name },
          headers: { "Content-Type": "application/octet-stream" },
        },
      )
    ).data.data;
  },
  async file(
    artifactId: string,
    version: number,
    fileId: string,
    download: boolean,
  ) {
    return await httpClient.get(
      `/api/v1/workflow_artifacts/${encodeURIComponent(
        artifactId,
      )}/versions/${version}/files/${encodeURIComponent(fileId)}`,
      { params: { download }, responseType: "blob" },
    );
  },
};
