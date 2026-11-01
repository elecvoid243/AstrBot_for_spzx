// useSpcodeProjectAutoLoad.spec.ts
// Covers the 2026-08-07 auto-load fixes: normalized idempotent compare
// and unconditional force-retry on true mismatch (binding is the source
// of truth).
// Author: elecvoid243
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { post: vi.fn() },
}));

import { pluginExtensionApi } from "@/api/v1";
import {
  ProjectLoadError,
  useSpcodeProjectAutoLoad,
  isSessionLoadedTag,
  markSessionLoadedTag,
  clearSessionLoadedTag,
} from "./useSpcodeProjectAutoLoad";
import type { Project } from "@/components/chat/ProjectList.vue";

const postMock = () => pluginExtensionApi.post as ReturnType<typeof vi.fn>;

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    project_id: "p1",
    title: "demo",
    emoji: "📁",
    description: "",
    workspace_type: "custom",
    workspace_path: "F:/proj/",
    spcode_auto_load: true,
    spcode_no_codegraph: false,
    created_at: "",
    updated_at: "",
    ...overrides,
  } as Project;
}

/** Envelope-shaped reply mirroring the backend (success inside data). */
function envelope(extra: Record<string, unknown>) {
  return { data: { data: { elapsed_ms: 1, ...extra } } };
}

describe("useSpcodeProjectAutoLoad silentLoad", () => {
  beforeEach(() => postMock().mockReset());

  it("uses the server-resolved personal project directory", async () => {
    postMock().mockResolvedValueOnce(
      envelope({ success: true, loaded: true, directory: "/owned/workspace" }),
    );
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const result = await silentLoad({
      project: makeProject({
        workspace_type: "project",
        workspace_path: null,
        resolved_workspace_path: "/owned/workspace",
      }),
      umo: "owned-chat",
    });
    expect(result?.loaded).toBe(true);
    expect(postMock().mock.calls[0][1].directory).toBe("/owned/workspace");
  });

  it("treats same-directory path variants as idempotent success", async () => {
    // Backend resolved "F:\proj"; workspace_path is "f:/proj/" — same dir.
    postMock().mockResolvedValueOnce(
      envelope({
        success: false,
        reason: "no_project_loaded",
        loaded: false,
        directory: "",
        previous_directory: "F:\\proj",
        substep_messages: [],
      }),
    );
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const data = await silentLoad({ project: makeProject(), umo: "u1" });
    expect(data?.loaded).toBe(true);
    // No force retry: exactly one POST.
    expect(postMock()).toHaveBeenCalledTimes(1);
    expect(postMock().mock.calls[0][1].force).toBe(false);
  });

  it("retries with force=true on true mismatch", async () => {
    postMock()
      .mockResolvedValueOnce(
        envelope({
          success: false,
          reason: "no_project_loaded",
          loaded: false,
          directory: "",
          previous_directory: "F:\\other",
          substep_messages: [],
        }),
      )
      .mockResolvedValueOnce(
        envelope({
          success: true,
          reason: null,
          loaded: true,
          directory: "F:\\proj",
          skipped_substeps: [],
          substep_messages: [],
        }),
      );
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const data = await silentLoad({ project: makeProject(), umo: "u1" });
    expect(data?.loaded).toBe(true);
    expect(postMock()).toHaveBeenCalledTimes(2);
    expect(postMock().mock.calls[1][1].force).toBe(true);
  });

  it("throws ProjectLoadError when the force retry also fails", async () => {
    const fail = envelope({
      success: false,
      reason: "no_project_loaded",
      loaded: false,
      directory: "",
      previous_directory: "F:\\other",
      substep_messages: [],
    });
    postMock().mockResolvedValue(fail);
    const { silentLoad } = useSpcodeProjectAutoLoad();
    await expect(
      silentLoad({ project: makeProject(), umo: "u1" }),
    ).rejects.toBeInstanceOf(ProjectLoadError);
    expect(postMock()).toHaveBeenCalledTimes(2);
  });

  it("returns null for non-custom workspaces without any POST", async () => {
    const { silentLoad } = useSpcodeProjectAutoLoad();
    for (const workspace_type of ["session", "project"] as const) {
      const data = await silentLoad({
        project: makeProject({ workspace_type }),
        umo: "u1",
      });
      expect(data).toBeNull();
    }
    expect(postMock()).not.toHaveBeenCalled();
  });
});

// ── Dirty tag (2026-09-01) ─────────────────────────────────────────
describe("spcode session dirty tag", () => {
  beforeEach(() => {
    markSessionLoadedTag("s1", "p1", "boot-1");
  });

  it("reports loaded for the same session+project+bootId", () => {
    expect(isSessionLoadedTag("s1", "p1", "boot-1")).toBe(true);
  });

  it("misses when the session has no tag", () => {
    expect(isSessionLoadedTag("s-unknown", "p1", "boot-1")).toBe(false);
  });

  it("misses on project rebinding (projectId changed)", () => {
    expect(isSessionLoadedTag("s1", "p2", "boot-1")).toBe(false);
  });

  it("misses after a backend restart (bootId changed)", () => {
    expect(isSessionLoadedTag("s1", "p1", "boot-2")).toBe(false);
  });

  it("applies the tag when the current bootId is still unknown (null)", () => {
    // Old backend without boot_id: cannot distinguish restart, keep the
    // fast path (backend idempotent rejection stays as safety net).
    expect(isSessionLoadedTag("s1", "p1", null)).toBe(true);
  });

  it("clearSessionLoadedTag drops the tag", () => {
    clearSessionLoadedTag("s1");
    expect(isSessionLoadedTag("s1", "p1", "boot-1")).toBe(false);
  });
});
