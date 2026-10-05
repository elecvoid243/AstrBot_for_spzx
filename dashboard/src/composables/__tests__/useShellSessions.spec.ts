import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";

vi.mock("@/api/v1", () => ({
  chatApi: {
    getSessionShellSessions: vi.fn(),
  },
}));

import { chatApi } from "@/api/v1";
import { useShellSessions } from "../useShellSessions";
import type { ShellSessionListItem } from "@/components/chat/message_list_comps/shell_session_tools/format";

const getSessionShellSessions = vi.mocked(chatApi.getSessionShellSessions);

function makeItem(
  overrides: Partial<ShellSessionListItem> = {},
): ShellSessionListItem {
  return {
    session_id: "sh_1",
    pid: 100,
    status: "running",
    exit_code: null,
    started_at: 1759656000.0,
    sandboxed: false,
    unread_output_bytes: 0,
    ...overrides,
  };
}

function okResponse(sessions: ShellSessionListItem[]) {
  return { data: { data: { sessions } } };
}

describe("useShellSessions", () => {
  beforeEach(() => {
    getSessionShellSessions.mockReset();
    // Default to an empty list so the immediate watcher does not log
    // fetch errors in tests that never touch the network path.
    getSessionShellSessions.mockResolvedValue(okResponse([]) as never);
  });

  it("applyPushedShellSessions authoritatively replaces the session cache", () => {
    const currentSessionId = ref<string | undefined>("s1");
    const { currentSessions, applyPushedShellSessions } =
      useShellSessions(currentSessionId);

    applyPushedShellSessions("s1", [makeItem(), makeItem({ session_id: "sh_2" })]);
    expect(currentSessions.value).toHaveLength(2);

    applyPushedShellSessions("s1", [makeItem({ status: "completed", exit_code: 0 })]);
    expect(currentSessions.value).toHaveLength(1);
    expect(currentSessions.value[0].status).toBe("completed");

    applyPushedShellSessions("s1", []);
    expect(currentSessions.value).toEqual([]);
  });

  it("refreshShellSessions fills the cache on session entry", async () => {
    getSessionShellSessions.mockResolvedValue(okResponse([makeItem()]) as never);
    const currentSessionId = ref<string | undefined>(undefined);
    const { currentSessions } = useShellSessions(currentSessionId);

    currentSessionId.value = "s1";
    await nextTick();
    await vi.waitFor(() => expect(currentSessions.value).toHaveLength(1));
    expect(getSessionShellSessions).toHaveBeenCalledWith("s1");
  });

  it("refreshShellSessions dedupes inflight requests per session", async () => {
    let resolve: (v: unknown) => void;
    getSessionShellSessions.mockReturnValue(
      new Promise((r) => (resolve = r)) as never,
    );
    const currentSessionId = ref<string | undefined>("s1");
    const { refreshShellSessions } = useShellSessions(currentSessionId);
    await nextTick();

    const p1 = refreshShellSessions("s1");
    const p2 = refreshShellSessions("s1");
    resolve!(okResponse([]));
    await Promise.all([p1, p2]);

    expect(getSessionShellSessions).toHaveBeenCalledTimes(1);
  });

  it("keeps the stale cache when the fetch fails", async () => {
    const currentSessionId = ref<string | undefined>("s1");
    const { currentSessions, applyPushedShellSessions, refreshShellSessions } =
      useShellSessions(currentSessionId);
    applyPushedShellSessions("s1", [makeItem()]);

    getSessionShellSessions.mockRejectedValue(new Error("network"));
    await refreshShellSessions("s1");

    expect(currentSessions.value).toHaveLength(1);
  });

  it("runningCount counts running; finishedCount counts the rest", () => {
    const currentSessionId = ref<string | undefined>("s1");
    const { runningCount, finishedCount, applyPushedShellSessions } =
      useShellSessions(currentSessionId);

    applyPushedShellSessions("s1", [
      makeItem({ session_id: "sh_1", status: "running" }),
      makeItem({ session_id: "sh_2", status: "running" }),
      makeItem({ session_id: "sh_3", status: "completed", exit_code: 0 }),
      makeItem({ session_id: "sh_4", status: "timed_out", exit_code: null }),
    ]);

    expect(runningCount.value).toBe(2);
    expect(finishedCount.value).toBe(2);
  });

  it("unknown session yields empty sessions and zero counts", () => {
    const currentSessionId = ref<string | undefined>("s-unknown");
    const { currentSessions, runningCount, finishedCount } =
      useShellSessions(currentSessionId);

    expect(currentSessions.value).toEqual([]);
    expect(runningCount.value).toBe(0);
    expect(finishedCount.value).toBe(0);
  });
});
