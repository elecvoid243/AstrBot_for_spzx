// Author: impl_task9
// Date: 2026-11-15
//
// Developer-mode LLM request snapshots: the leaf parser/merge is the wire
// contract for both the live `llm_request_snapshots` event and the REST
// list/detail routes. The composable spec pins the API mapping (expired,
// limits, detail cache) and the live fold proves history-free telemetry
// lands on the bot record instead of rendering as text.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

vi.mock("@/api/v1", () => ({
  llmSnapshotsApi: {
    list: vi.fn(),
    get: vi.fn(),
    getDeveloperMode: vi.fn(),
    setDeveloperMode: vi.fn(),
  },
  chatApi: {
    sendStreamUrl: vi.fn(() => "/api/v1/chat"),
    getSession: vi.fn(),
    getHistory: vi.fn(),
    getMarkers: vi.fn(),
    systemStreamUrl: vi.fn(() => "/api/v1/chat/sessions/s1/system-stream"),
    resumeRunStreamUrl: vi.fn(() => "/api/v1/chat/runs/run-1/stream"),
  },
  fileApi: {
    contentUrl: vi.fn(() => ""),
  },
}));

vi.mock("@/api/http", () => ({
  fetchWithAuth: vi.fn(),
}));

import { chatApi, llmSnapshotsApi } from "@/api/v1";
import { fetchWithAuth } from "@/api/http";
import {
  mergeLlmRequestSnapshots,
  parseLlmRequestSnapshots,
  type LlmRequestSnapshotEnvelope,
  type LlmSnapshotLimits,
} from "./llmRequestSnapshots";
import { useLlmSnapshots } from "./useLlmSnapshots";
import { useMessages } from "./useMessages";

const LIMITS: LlmSnapshotLimits = {
  max_bytes_per_snapshot: 1572864,
  max_snapshots_per_session: 128,
  max_bytes_per_session: 67108864,
  max_total_bytes: 268435456,
  max_message_bindings: 1024,
  ttl_seconds: 7200,
};

function envelope(
  snapshotId: string,
  extra: Record<string, unknown> = {},
): LlmRequestSnapshotEnvelope {
  return {
    snapshot_id: snapshotId,
    session_id: "s1",
    status: "ok",
    ...extra,
  } as LlmRequestSnapshotEnvelope;
}

function listPayload(items: LlmRequestSnapshotEnvelope[]) {
  return {
    data: {
      data: {
        enabled: true,
        expired: false,
        items,
        limits: LIMITS,
        dropped: 0,
      },
    },
  };
}

describe("parseLlmRequestSnapshots", () => {
  it("accepts the list envelope and keeps only string snapshot ids", () => {
    const parsed = parseLlmRequestSnapshots({
      items: [
        envelope("a"),
        { snapshot_id: 7 },
        { bytes: 1 },
        null,
        "nope",
        envelope("b", { bytes: 3 }),
      ],
    });

    expect(parsed.map((item) => item.snapshot_id)).toEqual(["a", "b"]);
  });

  it("accepts a bare array", () => {
    expect(parseLlmRequestSnapshots([envelope("a")])).toEqual([envelope("a")]);
  });

  it("ignores malformed payloads", () => {
    expect(parseLlmRequestSnapshots({ nope: true })).toEqual([]);
    expect(parseLlmRequestSnapshots({ items: "not-an-array" })).toEqual([]);
    expect(parseLlmRequestSnapshots(null)).toEqual([]);
    expect(parseLlmRequestSnapshots(undefined)).toEqual([]);
    expect(parseLlmRequestSnapshots("{broken")).toEqual([]);
  });

  it("keeps optional injections on detail envelopes and leaves list items without them", () => {
    const [listItem, detailItem] = parseLlmRequestSnapshots({
      items: [
        envelope("list"),
        envelope("detail", {
          injections: [
            {
              plugin: "tc_memory",
              handler: "decorate_llm_req",
              changes: [
                {
                  field: "system_prompt",
                  delta: 412,
                  lossy: false,
                  preview: "## memory",
                },
              ],
            },
          ],
        }),
      ],
    });

    expect(listItem.injections).toBeUndefined();
    expect(detailItem.injections?.[0].plugin).toBe("tc_memory");
    expect(detailItem.injections?.[0].changes[0].delta).toBe(412);
  });
});

describe("mergeLlmRequestSnapshots", () => {
  it("merges envelopes by snapshot_id without reordering", () => {
    const merged = mergeLlmRequestSnapshots(
      [{ snapshot_id: "a", bytes: 1 } as any],
      [
        { snapshot_id: "a", bytes: 2 } as any,
        { snapshot_id: "b", bytes: 3 } as any,
      ],
    );

    expect(merged.map((x) => x.snapshot_id)).toEqual(["a", "b"]);
    expect(merged[0].bytes).toBe(2);
  });

  it("preserves first-seen order for new ids and lets the last incoming copy win", () => {
    const current = [envelope("b", { bytes: 1 })];
    const incoming = [
      envelope("a", { bytes: 2 }),
      envelope("b", { bytes: 3 }),
      envelope("a", { bytes: 4 }),
      envelope("c", { bytes: 5 }),
    ];

    const merged = mergeLlmRequestSnapshots(current, incoming);

    expect(merged.map((item) => item.snapshot_id)).toEqual(["b", "a", "c"]);
    expect(merged[0].bytes).toBe(3);
    expect(merged[1].bytes).toBe(4);
    expect(merged[2].bytes).toBe(5);
    // The caller's live array is not rewritten in place.
    expect(current[0].bytes).toBe(1);
  });

  it("returns the incoming batch when nothing has been folded yet", () => {
    expect(
      mergeLlmRequestSnapshots([], [envelope("a")]).map(
        (item) => item.snapshot_id,
      ),
    ).toEqual(["a"]);
  });
});

describe("useLlmSnapshots", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads a filtered list and surfaces the API's expired flag and limits", async () => {
    vi.mocked(llmSnapshotsApi.list).mockResolvedValue({
      data: {
        data: {
          enabled: true,
          expired: true,
          items: [envelope("a")],
          limits: LIMITS,
          dropped: 0,
        },
      },
    } as never);

    const snapshots = useLlmSnapshots(ref("s1"));
    await snapshots.loadList({ messageId: 7, turnId: "turn-1" });

    expect(llmSnapshotsApi.list).toHaveBeenCalledWith("s1", {
      message_id: 7,
      turn_id: "turn-1",
    });
    expect(snapshots.items.value.map((item) => item.snapshot_id)).toEqual(["a"]);
    expect(snapshots.expired.value).toBe(true);
    expect(snapshots.limits.value?.ttl_seconds).toBe(7200);
    expect(snapshots.error.value).toBeNull();
  });

  it("caches detail envelopes so repeated reads hit the API once", async () => {
    const detail = {
      envelope: envelope("a", {
        injections: [
          {
            plugin: "tc_memory",
            handler: "decorate_llm_req",
            changes: [],
          },
        ],
      }),
      raw: { messages: [] },
      view: { sections: [] },
    };
    vi.mocked(llmSnapshotsApi.get).mockResolvedValue({
      data: { data: detail },
    } as never);

    const snapshots = useLlmSnapshots(() => "s1");
    const first = await snapshots.loadDetail("a");
    const second = await snapshots.loadDetail("a");

    expect(llmSnapshotsApi.get).toHaveBeenCalledTimes(1);
    expect(first).toBe(second);
    expect(second?.envelope.injections?.[0].plugin).toBe("tc_memory");
    expect(second?.raw).toEqual({ messages: [] });
  });

  it("reports list failures on error without throwing", async () => {
    vi.mocked(llmSnapshotsApi.list).mockRejectedValue(new Error("boom"));

    const snapshots = useLlmSnapshots("s1");
    await expect(snapshots.loadList()).resolves.toBeUndefined();

    expect(snapshots.error.value).toContain("boom");
  });

  it("re-reads a forced detail instead of answering from the cache", async () => {
    const detail = { envelope: envelope("a"), raw: {}, view: {} };
    vi.mocked(llmSnapshotsApi.get).mockResolvedValue({
      data: { data: detail },
    } as never);

    const snapshots = useLlmSnapshots("s1");
    await snapshots.loadDetail("a");
    await snapshots.loadDetail("a");
    expect(llmSnapshotsApi.get).toHaveBeenCalledTimes(1);

    await snapshots.loadDetail("a", { force: true });
    expect(llmSnapshotsApi.get).toHaveBeenCalledTimes(2);
  });

  it("refreshes with the last list filters", async () => {
    vi.mocked(llmSnapshotsApi.list).mockResolvedValue({
      data: {
        data: {
          enabled: true,
          expired: false,
          items: [envelope("a")],
          limits: LIMITS,
          dropped: 0,
        },
      },
    } as never);

    const snapshots = useLlmSnapshots("s1");
    await snapshots.loadList({ turnId: "turn-1" });
    vi.mocked(llmSnapshotsApi.list).mockResolvedValue({
      data: {
        data: {
          enabled: true,
          expired: false,
          items: [envelope("a"), envelope("b")],
          limits: LIMITS,
          dropped: 0,
        },
      },
    } as never);
    await snapshots.refresh();

    expect(llmSnapshotsApi.list).toHaveBeenLastCalledWith("s1", {
      message_id: undefined,
      turn_id: "turn-1",
    });
    expect(snapshots.items.value.map((item) => item.snapshot_id)).toEqual([
      "a",
      "b",
    ]);
  });

  it("ignores a list response that resolves after the session switched", async () => {
    const pending: Array<(value: unknown) => void> = [];
    vi.mocked(llmSnapshotsApi.list).mockImplementation(
      () =>
        new Promise((resolve) => {
          pending.push(resolve);
        }) as never,
    );

    const session = ref("s1");
    const snapshots = useLlmSnapshots(session);
    const pendingA = snapshots.loadList();
    session.value = "s2";
    const pendingB = snapshots.loadList();

    expect(llmSnapshotsApi.list).toHaveBeenNthCalledWith(1, "s1", {
      message_id: undefined,
      turn_id: undefined,
    });
    expect(llmSnapshotsApi.list).toHaveBeenNthCalledWith(2, "s2", {
      message_id: undefined,
      turn_id: undefined,
    });

    // B resolves first, then the slow A response arrives and must be dropped.
    pending[1](listPayload([envelope("b")]));
    await pendingB;
    pending[0](listPayload([envelope("a")]));
    await pendingA;

    expect(snapshots.items.value.map((item) => item.snapshot_id)).toEqual([
      "b",
    ]);
  });

  it("does not reuse a detail cached for another session", async () => {
    const detailFor = (sessionId: string) => ({
      envelope: envelope("a", { session_id: sessionId }),
      raw: {},
      view: {},
    });
    vi.mocked(llmSnapshotsApi.get)
      .mockResolvedValueOnce({ data: { data: detailFor("s1") } } as never)
      .mockResolvedValueOnce({ data: { data: detailFor("s2") } } as never);

    const session = ref("s1");
    const snapshots = useLlmSnapshots(session);
    expect((await snapshots.loadDetail("a"))?.envelope.session_id).toBe("s1");

    session.value = "s2";
    expect((await snapshots.loadDetail("a"))?.envelope.session_id).toBe("s2");
    expect(llmSnapshotsApi.get).toHaveBeenCalledTimes(2);
  });
});

function sseResponse(frames: string) {
  return new Response(frames, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function sseFrame(payload: unknown) {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

const NON_SSE = {
  ok: false,
  headers: { get: () => null },
} as unknown as Response;

describe("useMessages llm_request_snapshots fold", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(chatApi.getMarkers).mockResolvedValue({
      data: { data: { markers: [], total_messages: 0 } },
    } as never);
  });

  it("folds live envelopes onto the bot record and merges repeats by id", async () => {
    vi.mocked(chatApi.getSession).mockResolvedValue({
      data: {
        data: {
          history: [],
          threads: [],
          total_messages: 0,
          has_more: false,
          active_runs: [],
        },
      },
    } as never);
    vi.mocked(fetchWithAuth).mockImplementation(async (url: unknown) =>
      String(url).includes("system-stream")
        ? NON_SSE
        : sseResponse(
            sseFrame({
              type: "llm_request_snapshots",
              data: { items: [envelope("a", { bytes: 1 })] },
              message_id: "m1",
            }) +
              sseFrame({
                type: "llm_request_snapshots",
                data: {
                  items: [
                    envelope("a", { bytes: 2 }),
                    { snapshot_id: 9 },
                    envelope("b", { bytes: 3 }),
                  ],
                },
                message_id: "m1",
              }) +
              sseFrame({
                type: "end",
                data: "",
                streaming: false,
                message_id: "m1",
              }),
          ),
    );

    const composable = useMessages({ currentSessionId: ref("s1") });
    const parts = [{ type: "plain" as const, text: "hi" }];
    const { userRecord, botRecord } = composable.createLocalExchange({
      sessionId: "s1",
      messageId: "m1",
      parts,
    });
    composable.sendMessageStream({
      sessionId: "s1",
      messageId: "m1",
      parts,
      transport: "sse",
      userRecord,
      botRecord,
    });

    await vi.waitFor(() =>
      expect(
        botRecord.content.llmRequestSnapshots?.map((item) => item.snapshot_id),
      ).toEqual(["a", "b"]),
    );
    expect(botRecord.content.llmRequestSnapshots?.[0].bytes).toBe(2);
    expect(botRecord.content.llmRequestSnapshots).toHaveLength(2);

    // Telemetry is state, not chat text: no rendered plain part may carry
    // the snapshot payload (e.g. an accidental `snapshot_id` blob).
    const plainText = [
      ...userRecord.content.message,
      ...botRecord.content.message,
    ].flatMap((part) =>
      "text" in part && typeof part.text === "string" ? [part.text] : [],
    );
    expect(plainText.filter((text) => text.includes("snapshot_id"))).toEqual(
      [],
    );
  });

  it("does not resurrect the ephemeral field from history", async () => {
    vi.mocked(chatApi.getSession).mockResolvedValue({
      data: {
        status: "ok",
        data: {
          history: [
            {
              id: 1,
              content: {
                type: "bot",
                message: [{ type: "plain", text: "saved" }],
                llm_request_snapshots: [{ snapshot_id: "a" }],
              },
            },
          ],
          threads: [],
          total_messages: 1,
          has_more: false,
          active_runs: [],
        },
      },
    } as never);

    const composable = useMessages({ currentSessionId: ref("s1") });
    await composable.loadSessionMessages("s1", false);

    const record = composable.messagesBySession["s1"]?.[0];
    expect(record?.content.llmRequestSnapshots).toBeUndefined();
  });
});
