import { fetchWithAuth } from "@/api/http";
import {
  accountSessionRevision,
  assertCurrentAccount,
} from "@/utils/accountSession";

export interface SearchMatch {
  message_index: number;
  role: string;
  snippet: string;
  match_offset: number;
}

export interface SearchConversation {
  session_id: string;
  title: string;
  updated_at: number;
  matches: SearchMatch[];
}

export interface MessageSearchResponse {
  results: SearchConversation[];
  pagination: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  };
}

export const messageApi = {
  async searchMessages(
    q: string,
    page: number = 1,
    pageSize: number = 20,
  ): Promise<MessageSearchResponse> {
    const revision = accountSessionRevision.value;
    const url = new URL("/api/v1/messages/search", window.location.origin);
    url.searchParams.set("q", q);
    url.searchParams.set("page", String(page));
    url.searchParams.set("page_size", String(pageSize));

    const token = localStorage.getItem("token") || "";
    const response = await fetchWithAuth(url.toString(), {
      method: "GET",
      headers: {
        Authorization: token ? `Bearer ${token}` : "",
      },
    });

    if (!response.ok) {
      // 2026-07-28 (elecvoid243): surface the backend error message
      // (ApiError -> {status, message}) instead of the generic HTTP
      // statusText, so the search dialog shows a useful reason. Fall back to
      // statusText for non-JSON bodies.
      let detail = response.statusText;
      try {
        const errData = await response.json();
        if (errData && typeof errData.message === "string" && errData.message) {
          detail = errData.message;
        }
      } catch {
        // ignore non-json error body
      }
      throw new Error(`Search failed: ${detail}`);
    }

    const data = await response.json();
    assertCurrentAccount(revision);
    if (data.status === "ok" && data.data) {
      return data.data as MessageSearchResponse;
    }
    throw new Error("Invalid response format");
  },
};
