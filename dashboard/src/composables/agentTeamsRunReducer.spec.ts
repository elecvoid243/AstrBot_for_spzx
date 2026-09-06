// Author: elecvoid243
// Date: 2026-09-05
// Plan: docs/superpowers/plans/2026-09-05-agent-teams-frontend.md (Task 3)
// Leaf-module unit tests for the agent-teams run event reducer (vitest).
import { describe, expect, it } from "vitest";
import {
  applyTeamsEvent,
  createTeamsRunState,
  parseTeamsEvent,
} from "./agentTeamsRunReducer";

describe("agentTeamsRunReducer", () => {
  it("parses known SSE payloads and rejects unknown ones", () => {
    expect(
      parseTeamsEvent({
        type: "message",
        direction: "sent",
        member_id: "m1",
        text: "hi",
      })?.type,
    ).toBe("message");
    expect(parseTeamsEvent({ type: "heartbeat" })).toBeNull();
  });

  it("folds sent → stream deltas → reply into one member window", () => {
    const s = createTeamsRunState("r1");
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "message",
        direction: "sent",
        member_id: "m1",
        session_id: "c1",
        text: "任务",
      })!,
    );
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "message",
        direction: "stream",
        member_id: "m1",
        text: "你好",
      })!,
    );
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "message",
        direction: "stream",
        member_id: "m1",
        text: "，世界",
      })!,
    );
    expect(s.windows["m1"]).toMatchObject({
      sent: "任务",
      streamText: "你好，世界",
      streaming: true,
    });
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "message",
        direction: "reply",
        member_id: "m1",
        text: "你好，世界！",
      })!,
    );
    expect(s.windows["m1"]).toMatchObject({
      streamText: "你好，世界！",
      streaming: false,
    });
  });

  it("tracks node states, progress and terminal status", () => {
    const s = createTeamsRunState("r1", {
      nodeStates: {
        n1: { status: "pending" },
        n2: { status: "pending" },
      },
    });
    applyTeamsEvent(
      s,
      parseTeamsEvent({ type: "node_status", node_id: "n1", status: "running" })!,
    );
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "dag_progress",
        done: 0,
        running: 1,
        pending: 1,
        skipped: 0,
        failed: 0,
        total: 2,
      })!,
    );
    applyTeamsEvent(
      s,
      parseTeamsEvent({ type: "node_status", node_id: "n1", status: "done" })!,
    );
    expect(s.nodeStates["n1"].status).toBe("done");
    expect(s.progress).toMatchObject({ running: 0, done: 1 });
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "paused",
        reason: "node failed",
        node_id: "n2",
      })!,
    );
    expect(s.status).toBe("paused");
    expect(s.pausedNodeId).toBe("n2");
    applyTeamsEvent(
      s,
      parseTeamsEvent({ type: "stopped", reason: "done" })!,
    );
    expect(s.status).toBe("stopped");
  });

  it("creates a member window lazily on a bare stream event", () => {
    const s = createTeamsRunState("r1");
    expect(s.windows["m9"]).toBeUndefined();
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "message",
        direction: "stream",
        member_id: "m9",
        text: "直",
      })!,
    );
    expect(s.windows["m9"]).toMatchObject({
      memberId: "m9",
      sent: null,
      streamText: "直",
      streaming: true,
    });
  });

  it("sets streamText directly on a reply with no prior stream", () => {
    const s = createTeamsRunState("r1");
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "message",
        direction: "sent",
        member_id: "m2",
        text: "问题",
      })!,
    );
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "message",
        direction: "reply",
        member_id: "m2",
        text: "答案",
        parts: [{ type: "plain", text: "答案" }],
      })!,
    );
    expect(s.windows["m2"]).toMatchObject({
      sent: "问题",
      streamText: "答案",
      streaming: false,
    });
    expect(s.windows["m2"].parts).toEqual([{ type: "plain", text: "答案" }]);
  });

  it("clears reply parts on a new sent so round-2 streams are not masked", () => {
    const s = createTeamsRunState("r1");
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "message",
        direction: "reply",
        member_id: "m1",
        text: "第一轮回答",
        parts: [{ type: "plain", text: "第一轮回答" }],
      })!,
    );
    expect(s.windows["m1"].parts).toHaveLength(1);

    // Round-2 delivery: stale round-1 parts must not mask the incoming
    // stream until the round-2 reply replaces the window content.
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "message",
        direction: "sent",
        member_id: "m1",
        text: "第二轮任务",
      })!,
    );
    expect(s.windows["m1"]).toMatchObject({
      sent: "第二轮任务",
      streamText: "",
      streaming: false,
    });
    expect(s.windows["m1"].parts).toEqual([]);
  });

  it("adds busy sessions repeatedly and clears them on that member's reply", () => {
    const s = createTeamsRunState("r1");
    applyTeamsEvent(s, parseTeamsEvent({ type: "busy", session_id: "c1" })!);
    applyTeamsEvent(s, parseTeamsEvent({ type: "busy", session_id: "c2" })!);
    // Repeated busy markers for the same waiting session stay deduplicated.
    applyTeamsEvent(s, parseTeamsEvent({ type: "busy", session_id: "c1" })!);
    expect([...s.busySessionIds].sort()).toEqual(["c1", "c2"]);
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "message",
        direction: "reply",
        member_id: "m1",
        session_id: "c1",
        text: "done",
      })!,
    );
    expect([...s.busySessionIds]).toEqual(["c2"]);
  });

  it("keeps an already-terminal status on late stopped events", () => {
    const s = createTeamsRunState("r1", { status: "completed" });
    applyTeamsEvent(s, parseTeamsEvent({ type: "stopped", reason: "done" })!);
    expect(s.status).toBe("completed");
    expect(s.stoppedReason).toBe("done");
    // A non-terminal (paused) status is still replaced by stopped.
    const p = createTeamsRunState("r2", { status: "paused" });
    applyTeamsEvent(
      p,
      parseTeamsEvent({ type: "stopped", reason: "user stop" })!,
    );
    expect(p.status).toBe("stopped");
  });

  it("folds round events under both max and max_rounds keys", () => {
    const s = createTeamsRunState("r1");
    applyTeamsEvent(s, parseTeamsEvent({ type: "round", n: 2, max: 5 })!);
    expect(s.round).toEqual({ n: 2, max: 5 });
    // The auto orchestrator names the limit `max_rounds`.
    const auto = createTeamsRunState("r2");
    applyTeamsEvent(
      auto,
      parseTeamsEvent({ type: "round", n: 1, max_rounds: 20 })!,
    );
    expect(auto.round).toEqual({ n: 1, max: 20 });
    expect(parseTeamsEvent({ type: "round", n: 3 })).toBeNull();
  });

  it("returns null for malformed payloads instead of throwing", () => {
    expect(parseTeamsEvent(null)).toBeNull();
    expect(parseTeamsEvent("junk")).toBeNull();
    expect(parseTeamsEvent(42)).toBeNull();
    expect(parseTeamsEvent(["message"])).toBeNull();
    // Known event types with missing required fields are rejected too.
    expect(
      parseTeamsEvent({ type: "message", direction: "sent", text: "no member" }),
    ).toBeNull();
    expect(parseTeamsEvent({ type: "node_status", status: "running" })).toBeNull();
    expect(parseTeamsEvent({ type: "dag_progress", done: 1 })).toBeNull();
    expect(parseTeamsEvent({ type: "busy" })).toBeNull();
    expect(parseTeamsEvent({ type: "stopped" })).toBeNull();
  });
});

describe("agentTeamsRunReducer run mode", () => {
  it("seeds mode with a 'dag' default", () => {
    expect(createTeamsRunState("r1").mode).toBe("dag");
    expect(createTeamsRunState("r1", {}).mode).toBe("dag");
    expect(createTeamsRunState("r1", { mode: "auto" }).mode).toBe("auto");
    expect(createTeamsRunState("r1", { mode: "dag" }).mode).toBe("dag");
  });

  it("starts with an empty dispatch log", () => {
    expect(createTeamsRunState("r1").dispatches).toEqual([]);
  });
});

describe("agentTeamsRunReducer dispatch folding", () => {
  it("appends valid dispatch events with round, assignments and notes", () => {
    const s = createTeamsRunState("r1");
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "dispatch",
        round: 1,
        assignments: [
          { member: "m1", task: "实现登录接口" },
          { member: "m2", task: "编写测试" },
        ],
        notes: "先做后端",
      })!,
    );
    applyTeamsEvent(
      s,
      parseTeamsEvent({
        type: "dispatch",
        round: 2,
        assignments: [{ member: "m1", task: "修复评审意见" }],
      })!,
    );
    expect(s.dispatches).toEqual([
      {
        round: 1,
        assignments: [
          { member: "m1", task: "实现登录接口" },
          { member: "m2", task: "编写测试" },
        ],
        notes: "先做后端",
      },
      { round: 2, assignments: [{ member: "m1", task: "修复评审意见" }] },
    ]);
  });

  it("rejects malformed dispatch payloads (assignments must be an array of {member, task})", () => {
    // The legacy Record shape is no longer accepted.
    expect(
      parseTeamsEvent({ type: "dispatch", round: 1, assignments: { n1: "m1" } }),
    ).toBeNull();
    // Non-array assignments hit the deferred Array.isArray guard.
    expect(
      parseTeamsEvent({ type: "dispatch", round: 1, assignments: "junk" }),
    ).toBeNull();
    expect(parseTeamsEvent({ type: "dispatch", round: 1 })).toBeNull();
    // Items missing member/task strings are rejected.
    expect(
      parseTeamsEvent({ type: "dispatch", round: 1, assignments: [{ member: "m1" }] }),
    ).toBeNull();
    expect(
      parseTeamsEvent({
        type: "dispatch",
        round: 1,
        assignments: [{ member: 3, task: "x" }],
      }),
    ).toBeNull();
    // The round number is part of the folded record, so it is required.
    expect(
      parseTeamsEvent({
        type: "dispatch",
        assignments: [{ member: "m1", task: "t" }],
      }),
    ).toBeNull();
  });

  describe("timeline folding (Plan 3 T6)", () => {
    const msg = (extra: Record<string, unknown>) =>
      parseTeamsEvent({
        type: "message",
        session_id: "s1",
        member_id: "m1",
        ...extra,
      })!;

    it("merges sent/stream/reply into one turn entry by turn_id", () => {
      const state = createTeamsRunState("r1");
      applyTeamsEvent(
        state,
        msg({ direction: "sent", text: "task A", turn_id: "t1", run_id: "r1" }),
      );
      expect(state.windows.m1.timeline).toHaveLength(1);
      expect(state.windows.m1.timeline[0]).toMatchObject({
        turnId: "t1",
        kind: "turn",
        direction: "sent",
        text: "task A",
        streaming: false,
      });
      // The flat projection AgentWindow reads stays unchanged.
      expect(state.windows.m1.sent).toBe("task A");

      applyTeamsEvent(
        state,
        msg({ direction: "stream", text: "delta", turn_id: "t1", run_id: "r1" }),
      );
      expect(state.windows.m1.timeline).toHaveLength(1);
      expect(state.windows.m1.timeline[0].text).toBe("task Adelta");
      expect(state.windows.m1.timeline[0].streaming).toBe(true);
      expect(state.windows.m1.streamText).toBe("delta");

      applyTeamsEvent(
        state,
        msg({
          direction: "reply",
          text: "answer",
          parts: [{ type: "plain", text: "answer" }],
          turn_id: "t1",
          run_id: "r1",
        }),
      );
      expect(state.windows.m1.timeline).toHaveLength(1);
      expect(state.windows.m1.timeline[0]).toMatchObject({
        turnId: "t1",
        text: "answer",
        parts: [{ type: "plain", text: "answer" }],
        streaming: false,
      });
      expect(state.windows.m1.streamText).toBe("answer");
    });

    it("pushes choice entries carrying the parsed spec and the resolve reason", () => {
      const state = createTeamsRunState("r1");
      applyTeamsEvent(
        state,
        parseTeamsEvent({
          type: "choice",
          direction: "shown",
          session_id: "s1",
          member_id: "m1",
          data: { prompt: "Pick one", options: [] },
        })!,
      );
      expect(state.windows.m1.timeline).toHaveLength(1);
      expect(state.windows.m1.timeline[0]).toMatchObject({
        kind: "choice",
        direction: "shown",
        text: "Pick one",
        parts: [
          { type: "interactive_choice", spec: { prompt: "Pick one", options: [] } },
        ],
      });

      applyTeamsEvent(
        state,
        parseTeamsEvent({
          type: "choice",
          direction: "resolved",
          session_id: "s1",
          member_id: "m1",
          reason: "user cancelled",
        })!,
      );
      expect(state.windows.m1.timeline).toHaveLength(2);
      expect(state.windows.m1.timeline[1]).toMatchObject({
        kind: "choice",
        direction: "resolved",
        text: "user cancelled",
      });
    });

    it("synthesizes a legacy turn id for replays without turn_id", () => {
      const state = createTeamsRunState("r1");
      applyTeamsEvent(state, msg({ direction: "sent", text: "old" }));
      expect(state.windows.m1.timeline[0].turnId).toMatch(/^legacy-/);
    });

    it("keeps the flat projection compatible with AgentWindow", () => {
      const state = createTeamsRunState("r1");
      applyTeamsEvent(
        state,
        msg({ direction: "sent", text: "task", turn_id: "t1", run_id: "r1" }),
      );
      applyTeamsEvent(
        state,
        msg({ direction: "stream", text: "delta", turn_id: "t1", run_id: "r1" }),
      );
      expect(state.windows.m1.sent).toBe("task");
      expect(state.windows.m1.streamText).toBe("delta");
      expect(state.windows.m1.streaming).toBe(true);
    });
  });
});
