// useSpcodeWorktrees.spec.ts
//
// Regression tests (2026-09-16, elecvoid243) for the POST umo contract of
// the 4 worktree-management mutations.
//
// The backend's `_wrap` adapter reads the POST `umo` from the JSON BODY
// (the `?umo=` query string is only a fallback). Sending it exclusively in
// the query made the handler resolve the "most-recently-loaded project
// across ALL sessions", so creating a worktree in conversation A could
// create it inside conversation B's repo.
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: vi.fn(), post: vi.fn() },
}));

import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeProjectStatus } from "./useSpcodeProjectStatus";
import { useSpcodeWorktrees } from "./useSpcodeWorktrees";

const postMock = vi.mocked(pluginExtensionApi.post);

const UMO = "webchat:FriendMessage:webchat!astrbot!cid-123";
const ROOT = "C:/proj/demo";

/** Minimal success envelope ({ status: "ok", data: {...} }) for the 4
 *  worktree-management endpoints. */
function okEnvelope(extra: Record<string, unknown> = {}) {
  return {
    data: {
      status: "ok",
      data: {
        loaded: true,
        directory: ROOT,
        umo: UMO,
        worktree: ROOT,
        worktrees: [],
        reason: null,
        stderr: "",
        elapsed_ms: 1,
        ...extra,
      },
    },
  } as never;
}

/** Extract the JSON body of the Nth POST call. */
function bodyOf(call = 0): Record<string, unknown> {
  return postMock.mock.calls[call][1] as Record<string, unknown>;
}

/** Extract the axios config of the Nth POST call. */
function configOf(call = 0): { params?: Record<string, unknown> } {
  return postMock.mock.calls[call][2] as { params?: Record<string, unknown> };
}

describe("useSpcodeWorktrees POST umo contract", () => {
  beforeEach(() => {
    postMock.mockReset();
    useSpcodeProjectStatus().reset();
  });

  it("add() puts umo in the JSON body, not only in the query string", async () => {
    postMock.mockResolvedValue(okEnvelope({ created: { path: ROOT } }));
    const { add } = useSpcodeWorktrees();

    const result = await add({
      umo: UMO,
      path: `${ROOT}/.worktrees/feat-x`,
      branch: "feat-x",
      create: true,
    });

    expect(result.ok).toBe(true);
    expect(postMock).toHaveBeenCalledTimes(1);
    expect(postMock.mock.calls[0][0]).toBe("spcode/git-worktree-add");
    expect(bodyOf()).toMatchObject({
      umo: UMO,
      path: `${ROOT}/.worktrees/feat-x`,
      branch: "feat-x",
      create: true,
    });
    // Belt & braces: the query param is still sent (backend fallback path).
    expect(configOf().params?.umo).toBe(UMO);
  });

  it("remove() puts umo in the JSON body", async () => {
    postMock.mockResolvedValue(okEnvelope({ removed_path: `${ROOT}/.worktrees/x` }));
    const { remove } = useSpcodeWorktrees();

    await remove({ umo: UMO, path: `${ROOT}/.worktrees/x`, force: true });

    expect(postMock.mock.calls[0][0]).toBe("spcode/git-worktree-remove");
    expect(bodyOf()).toMatchObject({
      umo: UMO,
      path: `${ROOT}/.worktrees/x`,
      force: true,
    });
  });

  it("lock() puts umo in the JSON body", async () => {
    postMock.mockResolvedValue(okEnvelope({ lock_reason: "busy" }));
    const { lock } = useSpcodeWorktrees();

    await lock({ umo: UMO, path: `${ROOT}/.worktrees/x`, reason: "busy" });

    expect(postMock.mock.calls[0][0]).toBe("spcode/git-worktree-lock");
    expect(bodyOf()).toMatchObject({
      umo: UMO,
      path: `${ROOT}/.worktrees/x`,
      reason: "busy",
    });
  });

  it("unlock() puts umo in the JSON body", async () => {
    postMock.mockResolvedValue(okEnvelope());
    const { unlock } = useSpcodeWorktrees();

    await unlock({ umo: UMO, path: `${ROOT}/.worktrees/x` });

    expect(postMock.mock.calls[0][0]).toBe("spcode/git-worktree-unlock");
    expect(bodyOf()).toMatchObject({ umo: UMO, path: `${ROOT}/.worktrees/x` });
  });
});
