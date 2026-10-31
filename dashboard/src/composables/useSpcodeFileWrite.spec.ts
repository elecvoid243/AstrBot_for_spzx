import { describe, expect, it, vi } from "vitest";
import { ref } from "vue";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/api/v1", () => ({ pluginExtensionApi: { post } }));
vi.mock("./useSpcodeSession", () => ({
  useSpcodeSession: () => ({ umo: ref("session") }),
}));
import { useSpcodeFileWrite } from "./useSpcodeFileWrite";

describe("manual file saves", () => {
  it("keeps host revision writes separate from legacy project parameters", async () => {
    post.mockResolvedValue({
      data: { data: { saved: true, revision: "next" } },
    });
    const writer = useSpcodeFileWrite("G:/project");
    expect(
      await writer.save({
        path: "C:/outside.txt",
        content: "draft",
        baseRevision: "base",
      }),
    ).toEqual({ ok: true, revision: "next" });
    expect(post.mock.calls.at(-1)?.slice(0, 2)).toEqual([
      "spcode/host-file-write",
      { path: "C:/outside.txt", content: "draft", base_revision: "base" },
    ]);
    await writer.save({ path: "inside.txt", content: "legacy" });
    expect(post.mock.calls.at(-1)?.slice(0, 2)).toEqual([
      "spcode/file-write",
      {
        path: "inside.txt",
        content: "legacy",
        umo: "session",
        worktree: "G:/project",
      },
    ]);
    writer.dispose();
  });

  it("requires explicit success and preserves conflict revisions", async () => {
    const writer = useSpcodeFileWrite();
    post.mockResolvedValueOnce({ data: { status: "error" } });
    expect(await writer.save({ path: "x", content: "draft" })).toEqual({
      ok: false,
      reason: "unknown",
      revision: undefined,
    });
    post.mockResolvedValueOnce({
      data: {
        data: { saved: false, reason: "conflict", revision: "new-disk" },
      },
    });
    expect(
      await writer.save({
        path: "C:/x",
        content: "draft",
        baseRevision: "old",
      }),
    ).toEqual({ ok: false, reason: "conflict", revision: "new-disk" });
    writer.dispose();
  });

  it("ignores a late response even when transport cancellation is ineffective", async () => {
    let finish!: (value: unknown) => void;
    post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const writer = useSpcodeFileWrite();
    const old = writer.save({
      path: "C:/old",
      content: "old",
      baseRevision: "old",
    });
    post.mockResolvedValueOnce({
      data: { data: { saved: true, revision: "new" } },
    });
    expect(
      (
        await writer.save({
          path: "C:/new",
          content: "new",
          baseRevision: "base",
        })
      ).ok,
    ).toBe(true);
    finish({ data: { data: { saved: true, revision: "late" } } });
    expect(await old).toEqual({ ok: false, reason: "aborted" });
    writer.dispose();
  });
});
