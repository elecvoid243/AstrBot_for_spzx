import { httpClient } from "./http";
import type {
  GraphDefinition,
  WorkflowComponent,
  WorkflowDraftGenerationRequest,
  WorkflowSaveRequest,
  WorkflowStartRequest,
  ArtifactRevisionRequest,
  ToolResolutionRequest,
  WorkflowControlRequest,
  WorkflowDataLine,
  WorkflowDebugForkAttempt,
  WorkflowDebugForkLocation,
  WorkflowDebugForkPreview,
  WorkflowDebugForkRequest,
  WorkflowDebugForkStarted,
  WorkflowMemberAttempt,
  WorkflowRerunRequest,
  WorkflowRetryRequest,
  HumanAnswerRequest,
  WorkflowImportRequest,
  ChatAccessRequest,
  TrialRunRequest,
} from "./generated/openapi-v1";

export const workflowAssetsApi = {
  async listComponents(): Promise<WorkflowComponent[]> {
    return (await httpClient.get("/api/v1/workflow_components")).data.data;
  },
  async generateDraft(payload: WorkflowDraftGenerationRequest): Promise<GraphDefinition> {
    return (await httpClient.post("/api/v1/workflow_assets/generate_draft", payload)).data.data;
  },
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
  async list(includeExample: boolean | "team" = false, summaryOnly = false) {
    return (await httpClient.get("/api/v1/workflow_assets", {
      params: { include_example: includeExample === true || undefined, include_team_example: includeExample === "team" || undefined, summary_only: summaryOnly || undefined },
    })).data.data;
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
  async runs(includeArtifacts = false, signal?: AbortSignal) {
    return (await httpClient.get("/api/v1/workflow_runs", { params: { summary_only: true, include_artifacts: includeArtifacts || undefined }, signal })).data.data;
  },
  async readiness(assetId: string, version: number) {
    return (
      await httpClient.get(
        `/api/v1/workflow_assets/${encodeURIComponent(assetId)}/readiness`,
        { params: { version } },
      )
    ).data.data;
  },
  async run(runId: string, signal?: AbortSignal) {
    return (
      await httpClient.get(`/api/v1/workflow_runs/${encodeURIComponent(runId)}`, { signal })
    ).data.data;
  },
  async dataLine(
    runId: string,
    params: { scope_run_id: string; task_run_id: string; input_name: string; attempt_id?: string },
    signal: AbortSignal,
  ): Promise<WorkflowDataLine> {
    return (await httpClient.get(
      `/api/v1/workflow_runs/${encodeURIComponent(runId)}/data_line`,
      { params, signal },
    )).data.data;
  },
  async failedChildDebugAttempts(
    runId: string,
    params: WorkflowDebugForkAttempt,
    signal: AbortSignal,
  ): Promise<WorkflowDebugForkLocation[]> {
    return (await httpClient.get(
      `/api/v1/workflow_runs/${encodeURIComponent(runId)}/debug_forks/failed_child`,
      { params, signal },
    )).data.data;
  },
  async previewDebugFork(runId: string, attempt: WorkflowDebugForkAttempt, signal: AbortSignal): Promise<WorkflowDebugForkPreview> {
    // Serialize draft bindings before Axios merges the config so JSON keys are preserved.
    return (await httpClient.post(
      `/api/v1/workflow_runs/${encodeURIComponent(runId)}/debug_forks/preview`,
      JSON.stringify(attempt), { signal, headers: { "Content-Type": "application/json" } },
    )).data.data;
  },
  async startDebugFork(runId: string, request: WorkflowDebugForkRequest): Promise<WorkflowDebugForkStarted> {
    return (await httpClient.post(
      `/api/v1/workflow_runs/${encodeURIComponent(runId)}/debug_forks`,
      JSON.stringify(request), { headers: { "Content-Type": "application/json" } },
    )).data.data;
  },
  async memberAttempt(runId: string, taskId: string, attemptId: string, signal: AbortSignal): Promise<WorkflowMemberAttempt> {
    return (await httpClient.get(
      `/api/v1/workflow_runs/${encodeURIComponent(runId)}/member_attempt`,
      { params: { task_run_id: taskId, attempt_id: attemptId }, signal },
    )).data.data;
  },
  async runHistoryPage(runId: string, taskRunId: string, kind: "events" | "messages" | "usage" | "attempts", before?: string | null, signal?: AbortSignal) {
    return (await httpClient.get(
      `/api/v1/workflow_runs/${encodeURIComponent(runId)}/history`,
      { params: { task_run_id: taskRunId, kind, before }, signal },
    )).data.data;
  },
  async processPage(runId: string, taskRunId: string, before: number, signal?: AbortSignal) {
    return (await httpClient.get(
      `/api/v1/workflow_runs/${encodeURIComponent(runId)}/process`,
      { params: { task_run_id: taskRunId, before }, signal },
    )).data.data;
  },
  async events(runId: string, after: number, signal?: AbortSignal) {
    return (
      await httpClient.get(
        `/api/v1/workflow_runs/${encodeURIComponent(runId)}/events`,
        { params: { after }, signal },
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
  async rerun(runId: string, payload: WorkflowRerunRequest) {
    return (
      await httpClient.post(
        `/api/v1/workflow_runs/${encodeURIComponent(runId)}/rerun`,
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
  async artifact(artifactId: string, version: number, signal?: AbortSignal) {
    return (
      await httpClient.get(
        `/api/v1/workflow_artifacts/${encodeURIComponent(
          artifactId,
        )}/versions/${version}`,
        { signal },
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
    signal?: AbortSignal,
  ) {
    return await httpClient.get(
      `/api/v1/workflow_artifacts/${encodeURIComponent(
        artifactId,
      )}/versions/${version}/files/${encodeURIComponent(fileId)}`,
      { params: { download }, responseType: "blob", signal },
    );
  },
};
