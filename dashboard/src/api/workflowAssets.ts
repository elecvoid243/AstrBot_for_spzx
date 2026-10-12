import { httpClient } from "./http";
import type {
  WorkflowSaveRequest,
  WorkflowStartRequest,
  ArtifactRevisionRequest,
  ToolResolutionRequest,
  WorkflowControlRequest,
  WorkflowRetryRequest,
  HumanAnswerRequest,
} from "./generated/openapi-v1";

export const workflowAssetsApi = {
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
  async runs() {
    return (await httpClient.get("/api/v1/workflow_runs")).data.data;
  },
  async run(runId: string) {
    return (
      await httpClient.get(`/api/v1/workflow_runs/${encodeURIComponent(runId)}`)
    ).data.data;
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
