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

  it("folds round and recognizes dispatch events", () => {
    const s = createTeamsRunState("r1");
    applyTeamsEvent(s, parseTeamsEvent({ type: "round", n: 2, max: 5 })!);
    expect(s.round).toEqual({ n: 2, max: 5 });
    expect(
      parseTeamsEvent({ type: "dispatch", assignments: { n1: "m1" } })?.type,
    ).toBe("dispatch");
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
