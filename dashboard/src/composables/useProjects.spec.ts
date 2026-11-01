import { describe, expect, it, vi } from "vitest";
import { effectScope } from "vue";
import { accountSessionRevision } from "@/utils/accountSession";
const { listProjects, listProjectSessions } = vi.hoisted(() => ({
  listProjects: vi.fn(),
  listProjectSessions: vi.fn(),
}));
vi.mock("@/api/v1", () => ({ chatApi: { listProjects, listProjectSessions } }));
import { useProjects } from "./useProjects";

describe("account-owned projects", () => {
  it("clears projects and rejects late account responses before publishing rows", async () => {
    const scope = effectScope();
    const state = scope.run(() => useProjects())!;
    listProjects.mockResolvedValueOnce({
      data: { status: "ok", data: [{ project_id: "private" }] },
    });
    await state.getProjects();
    state.selectedProjectId.value = "private";
    let resolve!: (value: unknown) => void;
    listProjects.mockReturnValueOnce(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const pending = state.getProjects();
    accountSessionRevision.value += 1;
    expect(state.projects.value).toEqual([]);
    expect(state.selectedProjectId.value).toBeNull();
    resolve({ data: { status: "ok", data: [{ project_id: "late-private" }] } });
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(state.projects.value).toEqual([]);
    scope.stop();
  });
});
