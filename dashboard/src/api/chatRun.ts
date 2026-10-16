import axios from "axios";
import type { HumanAnswerRequest } from "./generated/openapi-v1";

// Source-browser sessions must not inherit Dashboard login interceptors.
const client = axios.create();

export interface ChatRunConnection {
  session_id: string;
  token: string;
  command: string;
  expires_at: number;
}

export interface ChatRunView {
  run_id: string;
  status: string;
  control_status: string;
  result: unknown;
  todos: any[];
}

export const chatRunApi = {
  async connection(runId: string): Promise<ChatRunConnection> {
    return (
      await client.post(
        `/api/v1/workflow_chat_runs/${encodeURIComponent(runId)}/connection`,
      )
    ).data.data;
  },
  async status(
    runId: string,
    credential: string,
  ): Promise<{ confirmed: boolean }> {
    return (
      await client.get(
        `/api/v1/workflow_chat_runs/${encodeURIComponent(runId)}/connection`,
        {
          headers: { "X-AstrBot-Chat-Session": credential },
        },
      )
    ).data.data;
  },
  async run(runId: string, credential: string): Promise<ChatRunView> {
    return (
      await client.get(
        `/api/v1/workflow_chat_runs/${encodeURIComponent(runId)}`,
        {
          headers: { "X-AstrBot-Chat-Session": credential },
        },
      )
    ).data.data;
  },
  async answer(
    runId: string,
    credential: string,
    todoId: string,
    payload: HumanAnswerRequest,
  ): Promise<ChatRunView> {
    return (
      await client.post(
        `/api/v1/workflow_chat_runs/${encodeURIComponent(
          runId,
        )}/todos/${encodeURIComponent(todoId)}/answer`,
        payload,
        {
          headers: { "X-AstrBot-Chat-Session": credential },
        },
      )
    ).data.data;
  },
  async preview(runId: string, credential: string, todoId: string) {
    return (
      await client.get(
        `/api/v1/workflow_chat_runs/${encodeURIComponent(
          runId,
        )}/todos/${encodeURIComponent(todoId)}/artifact`,
        {
          headers: { "X-AstrBot-Chat-Session": credential },
        },
      )
    ).data.data;
  },
  async file(
    runId: string,
    credential: string,
    todoId: string,
    fileId: string,
  ) {
    return client.get<Blob>(
      `/api/v1/workflow_chat_runs/${encodeURIComponent(
        runId,
      )}/todos/${encodeURIComponent(
        todoId,
      )}/artifact/files/${encodeURIComponent(fileId)}`,
      {
        headers: { "X-AstrBot-Chat-Session": credential },
        responseType: "blob",
      },
    );
  },
};
