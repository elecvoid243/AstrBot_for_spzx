import { describe, expect, it, vi } from "vitest";
import { ref } from "vue";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/api/v1", () => ({ pluginExtensionApi: { post } }));
vi.mock("./useSpcodeSession", () => ({
  useSpcodeSession: () => ({ umo: ref("session") }),
}));
import { useSpcodeFileRename } from "./useSpcodeFileRename";
import { useSpcodeFileRemove } from "./useSpcodeFileRemove";

describe("explicit manual file management", () => {
  it("separates absolute management from project context and consumes the actual destination", async () => {
    const worktree = ref("G:/A");
    const rename = useSpcodeFileRename(worktree);
    post.mockResolvedValueOnce({
      data: {
        data: { renamed: true, path: "C:/outside.txt", new_path: "C:/new.txt" },
      },
    });
    expect(
      await rename.rename({
        path: "C:/outside.txt",
        newName: "new.txt",
        host: true,
      }),
    ).toEqual({ ok: true, path: "C:/outside.txt", newPath: "C:/new.txt" });
    expect(post.mock.calls.at(-1)?.slice(0, 2)).toEqual([
      "spcode/host-file-manage",
      {
        path: "C:/outside.txt",
        operation: "rename",
        new_name: "new.txt",
        umo: undefined,
        worktree: undefined,
      },
    ]);
    rename.dispose();
  });
  it("retains the legacy relative request contract", async () => {
    const rename = useSpcodeFileRename("G:/A");
    post.mockResolvedValueOnce({ data: { data: { renamed: true } } });
    await rename.rename({ path: "src/old.txt", newName: "new.txt" });
    expect(post.mock.calls.at(-1)?.slice(0, 2)).toEqual([
      "spcode/file-rename",
      {
        path: "src/old.txt",
        new_name: "new.txt",
        umo: "session",
        worktree: "G:/A",
      },
    ]);
    rename.dispose();
  });
  it("never treats an empty or unavailable-plugin response as deletion success", async () => {
    const remove = useSpcodeFileRemove();
    for (const payload of [
      {},
      { status: "error" },
      { data: { deleted: false, reason: "permission_denied" } },
    ]) {
      post.mockResolvedValueOnce({ data: payload });
      expect(
        (await remove.remove({ path: "C:/outside.txt", host: true })).ok,
      ).toBe(false);
    }
    remove.dispose();
  });
  it("requires a host target as well as an explicit success marker", async () => {
    const remove = useSpcodeFileRemove();
    post.mockResolvedValueOnce({ data: { data: { deleted: true } } });
    expect(
      (await remove.remove({ path: "C:/outside.txt", host: true })).ok,
    ).toBe(false);
    post.mockResolvedValueOnce({
      data: { data: { deleted: true, path: "C:/outside.txt" } },
    });
    expect(
      (await remove.remove({ path: "C:/outside.txt", host: true })).ok,
    ).toBe(true);
    remove.dispose();
  });
});
