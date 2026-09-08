import { describe, it, expect } from "vitest";
import {
  parseSpcodeGitBranches,
  type SpcodeGitBranchesRawResponse,
} from "../parseSpcodeGitBranches";

describe("parseSpcodeGitBranches", () => {
  it("converts snake_case raw to camelCase snapshot", () => {
    const raw: SpcodeGitBranchesRawResponse = {
      loaded: true,
      directory: "D:/repo",
      umo: "umo-1",
      branches: [
        {
          name: "main",
          sha: "abc123",
          upstream: "origin/main",
          upstream_track: "ahead 1",
          current: true,
          remote: false,
        },
        {
          name: "origin/feature/x",
          sha: "def456",
          upstream: "",
          upstream_track: "",
          current: false,
          remote: true,
        },
      ],
      total: 2,
      current: "main",
      detached: false,
      reason: null,
      stderr: "",
      elapsed_ms: 42,
    };
    const snap = parseSpcodeGitBranches(raw);
    expect(snap.meta.directory).toBe("D:/repo");
    expect(snap.meta.umo).toBe("umo-1");
    expect(snap.meta.loaded).toBe(true);
    expect(snap.meta.elapsedMs).toBe(42);
    expect(snap.branches).toHaveLength(2);
    expect(snap.branches[0]).toEqual({
      name: "main",
      sha: "abc123",
      upstream: "origin/main",
      upstreamTrack: "ahead 1",
      current: true,
      remote: false,
    });
    expect(snap.branches[1].remote).toBe(true);
    expect(snap.total).toBe(2);
    expect(snap.current).toBe("main");
    expect(snap.detached).toBe(false);
  });

  it("passes through configured remotes", () => {
    const raw: SpcodeGitBranchesRawResponse = {
      loaded: true,
      directory: "D:/repo",
      umo: "umo-1",
      branches: [],
      remotes: ["origin", "upstream"],
      total: 0,
      current: null,
      detached: false,
      reason: null,
      stderr: "",
      elapsed_ms: 0,
    };
    const snap = parseSpcodeGitBranches(raw);
    expect(snap.remotes).toEqual(["origin", "upstream"]);
  });

  it("handles empty branches array", () => {
    const raw: SpcodeGitBranchesRawResponse = {
      loaded: true,
      directory: "D:/repo",
      umo: "umo-1",
      branches: [],
      total: 0,
      current: null,
      detached: false,
      reason: null,
      stderr: "",
      elapsed_ms: 0,
    };
    const snap = parseSpcodeGitBranches(raw);
    expect(snap.branches).toEqual([]);
    expect(snap.total).toBe(0);
  });

  it("handles detached HEAD (no current)", () => {
    const raw: SpcodeGitBranchesRawResponse = {
      loaded: true,
      directory: "D:/repo",
      umo: "umo-1",
      branches: [
        {
          name: "abc1234",
          sha: "abc1234",
          upstream: "",
          upstream_track: "",
          current: false,
          remote: false,
        },
      ],
      total: 1,
      current: null,
      detached: true,
      reason: null,
      stderr: "",
      elapsed_ms: 0,
    };
    const snap = parseSpcodeGitBranches(raw);
    expect(snap.detached).toBe(true);
    expect(snap.current).toBeNull();
  });

  it("coerces missing/null fields to safe defaults", () => {
    // Backend may send partial data on preflight failures.
    const raw = {
      loaded: false,
      directory: null,
      umo: null,
      branches: null, // not an array
      total: 0,
      current: null,
      detached: false,
    } as unknown as SpcodeGitBranchesRawResponse;
    const snap = parseSpcodeGitBranches(raw);
    expect(snap.meta.directory).toBeNull();
    expect(snap.meta.loaded).toBe(false);
    expect(snap.branches).toEqual([]);
  });

  it("maps tags and defaults to [] when absent", () => {
    const withTags = parseSpcodeGitBranches({
      loaded: true,
      directory: "d",
      umo: "u",
      branches: [],
      tags: [{ name: "v1.0.0", sha: "a".repeat(40), annotated: true }],
      total: 0,
      current: null,
      detached: false,
      reason: null,
      stderr: "",
      elapsed_ms: 1,
    } as never);
    expect(withTags.tags).toEqual([
      { name: "v1.0.0", sha: "a".repeat(40), annotated: true },
    ]);

    const withoutTags = parseSpcodeGitBranches({
      loaded: true,
      directory: "d",
      umo: "u",
      branches: [],
      total: 0,
      current: null,
      detached: false,
      reason: null,
      stderr: "",
      elapsed_ms: 1,
    } as never);
    expect(withoutTags.tags).toEqual([]);
  });
});
