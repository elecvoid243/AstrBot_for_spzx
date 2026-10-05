// Author: elecvoid243 @ 2026-10-05
// Spec: docs/superpowers/specs/2026-10-05-git-log-graph-and-branch-tree-design.md §3.3 / §4.4
//
// 「所有分支」作用域：请求带 all=true&topo=true 且**不带** ref；两个作用域
// 必须落在不同的 ETag bucket 里，否则切换范围会 304 回放另一范围的快照。

import { describe, it, expect, vi, beforeEach } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

// Mock the spcode project status composable BEFORE importing the SUT.
vi.mock("@/composables/useSpcodeProjectStatus", () => ({
  useSpcodeProjectStatus: () => ({
    status: {
      value: { umo: "umo-test", directory: "D:/repo", loaded: true },
    },
    refresh: vi.fn(),
  }),
}));

const mockGet = vi.fn();
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
  },
}));

import { useSpcodeGitLog } from "./useSpcodeGitLog";

function okEnvelope() {
  return {
    status: 200,
    data: {
      status: "ok",
      data: {
        success: true,
        reason: null,
        loaded: true,
        elapsed_ms: 1,
        umo: "u",
        worktree: "w",
        directory: "d",
        ref: "HEAD",
        resolved_ref: "",
        count: 0,
        has_more: false,
        truncated: false,
        max_bytes: 1024,
        commits: [],
      },
    },
    headers: { etag: 'W/"e1"' },
  };
}

function withSetup<T>(fn: () => T): T {
  let result: T;
  const Comp = defineComponent({
    setup() {
      result = fn();
      return () => h("div");
    },
  });
  mount(Comp);
  return result!;
}

type RequestOptions = {
  params: Record<string, unknown>;
  headers: Record<string, string>;
};

function lastRequest(): RequestOptions {
  const call = mockGet.mock.calls.at(-1);
  return call?.[1] as RequestOptions;
}

describe("useSpcodeGitLog all-branches scope (2026-10-05)", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockGet.mockResolvedValue(okEnvelope());
  });

  it("sends all+topo and omits ref when the scope is on", async () => {
    const { filter, refresh } = withSetup(() => useSpcodeGitLog(null, true));

    filter.value.allRefs = true;
    await refresh();

    const req = lastRequest();
    expect(req.params).toMatchObject({ all: "true", topo: "true" });
    // ref=HEAD 默认值会被后端并集进 --all,不带它 query 元组更干净
    expect(req.params.ref).toBeUndefined();
  });

  it("keeps a separate ETag bucket for the all-branches scope", async () => {
    const { filter, refresh } = withSetup(() => useSpcodeGitLog(null, true));

    await refresh(); // 默认范围：200 → 缓存该 bucket 的 ETag
    filter.value.allRefs = true;
    await refresh();

    // 若两个作用域共用一个 bucket,这里会带上默认范围的 ETag 并 304
    // 回放旧快照
    expect(lastRequest().headers["If-None-Match"]).toBeUndefined();
  });

  it("still sends ref in the default (single-ref) scope", async () => {
    const { refresh } = withSetup(() => useSpcodeGitLog(null, true));

    await refresh();

    const req = lastRequest();
    expect(req.params.ref).toBe("HEAD");
    expect(req.params.all).toBeUndefined();
  });
});
