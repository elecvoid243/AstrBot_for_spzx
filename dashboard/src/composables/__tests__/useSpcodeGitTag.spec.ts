// Author: elecvoid243 @ 2026-09-12
// Tests for useSpcodeGitTag — the commit-dialog "tag this commit" flow
// (POST /spcode/git-tag-create). Mirrors useSpcodeGitCommitAmend.spec.ts.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

const mockPost = vi.fn();
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

import { useSpcodeGitTag } from "../useSpcodeGitTag";

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

describe("useSpcodeGitTag", () => {
  beforeEach(() => {
    mockPost.mockReset();
  });

  it("createTag() posts the tag body and parses success", async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        status: "ok",
        data: {
          created: true,
          tag: "v1.0.0",
          rev: "a".repeat(40),
          sha: "a".repeat(40),
          umo: "umo-test",
          worktree: "D:/repo",
          directory: "D:/repo",
          reason: null,
          stderr: "",
          elapsed_ms: 4,
        },
      },
    });
    const { createTag, isCreating } = withSetup(() => useSpcodeGitTag());
    const p = createTag({
      tag: "v1.0.0",
      rev: "a".repeat(40),
      worktree: "D:/repo",
      umo: "umo-test",
    });
    expect(isCreating.value).toBe(true);
    const r = await p;
    expect(isCreating.value).toBe(false);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.snapshot.created).toBe(true);
      expect(r.snapshot.tag).toBe("v1.0.0");
      expect(r.snapshot.sha).toBe("a".repeat(40));
    }
    const [endpoint, body] = mockPost.mock.calls[0];
    expect(endpoint).toBe("spcode/git-tag-create");
    expect(body).toEqual({
      tag: "v1.0.0",
      rev: "a".repeat(40),
      worktree: "D:/repo",
      umo: "umo-test",
    });
  });

  it("omits rev/worktree/umo from the body when nullish", async () => {
    mockPost.mockResolvedValueOnce({
      data: { data: { created: true, tag: "v1", reason: null } },
    });
    const { createTag } = withSetup(() => useSpcodeGitTag());
    await createTag({ tag: "v1" });
    const [, body] = mockPost.mock.calls[0];
    expect(body).toEqual({ tag: "v1" });
  });

  it("passes failure reason + stderr through", async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        status: "ok",
        data: {
          created: false,
          reason: "tag_already_exists",
          stderr: "fatal: tag 'v1' already exists",
        },
      },
    });
    const { createTag } = withSetup(() => useSpcodeGitTag());
    const r = await createTag({ tag: "v1", umo: "u" });
    expect(r).toEqual({
      ok: false,
      reason: "tag_already_exists",
      stderr: "fatal: tag 'v1' already exists",
    });
  });

  it("maps network errors to 'network'", async () => {
    mockPost.mockRejectedValueOnce({
      code: "ERR_NETWORK",
      message: "Network Error",
    });
    const { createTag } = withSetup(() => useSpcodeGitTag());
    const r = await createTag({ tag: "v1", umo: "u" });
    expect(r).toEqual({ ok: false, reason: "network" });
  });

  it("dispose() makes subsequent calls return 'aborted'", async () => {
    const { createTag, dispose } = withSetup(() => useSpcodeGitTag());
    dispose();
    const r = await createTag({ tag: "v1", umo: "u" });
    expect(r).toEqual({ ok: false, reason: "aborted" });
    expect(mockPost).not.toHaveBeenCalled();
  });
});
