// dashboard/src/composables/__tests__/useSpcodeProjectAutoLoad.spec.ts

import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  useSpcodeProjectAutoLoad,
  ProjectLoadError,
} from "../useSpcodeProjectAutoLoad";
import type { Project } from "@/components/chat/ProjectList.vue";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    post: vi.fn(),
  },
}));

import { pluginExtensionApi } from "@/api/v1";

const baseProject: Project = {
  project_id: "p-1",
  title: "T",
  // spcode auto-load binds to the custom workspace type (2026-08-07).
  workspace_type: "custom",
  workspace_path: "/abs/repo",
  spcode_auto_load: true,
  spcode_no_agentsmd: false,
  spcode_no_codegraph: false,
  created_at: "",
  updated_at: "",
};

/**
 * Wrap a spcode-style flat envelope in the ApiEnvelope that
 * pluginExtensionApi.post actually returns.
 *
 * spcode's _make_envelope returns { status: "ok", data: { success, loaded,
 * directory, reason, ... } } — all fields are flat inside `data`, there is
 * NO nested `data` sub-object.  This helper mirrors that shape so the
 * composable's reshape logic in postLoad is exercised correctly.
 */
function mockPost(flatPayload: Record<string, unknown>): void {
  (pluginExtensionApi.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
    data: { status: "ok", data: flatPayload },
  });
}

/** Build a success flat payload matching spcode _make_envelope output. */
function successPayload(overrides: Record<string, unknown> = {}) {
  return {
    success: true,
    loaded: true,
    directory: "/abs/repo",
    loaded_at: 1700000000,
    umo: "u1",
    skipped_substeps: [],
    substep_messages: [],
    reason: null,
    stderr: "",
    elapsed_ms: 100,
    ...overrides,
  };
}

/** Build a failure flat payload matching spcode _make_envelope output. */
function failurePayload(
  reason: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    success: false,
    loaded: false,
    directory: "/abs/repo",
    umo: "u1",
    skipped_substeps: [],
    substep_messages: [],
    reason,
    stderr: "",
    elapsed_ms: 1,
    ...overrides,
  };
}

beforeEach(() => {
  (pluginExtensionApi.post as ReturnType<typeof vi.fn>).mockReset();
});

describe("useSpcodeProjectAutoLoad", () => {
  it("FT-1: returns null for workspace_type=session and workspace_type=project", async () => {
    const { silentLoad } = useSpcodeProjectAutoLoad();
    for (const workspace_type of ["session", "project"] as const) {
      const r = await silentLoad({
        project: { ...baseProject, workspace_type },
        umo: "u1",
      });
      expect(r).toBeNull();
    }
    expect(pluginExtensionApi.post).not.toHaveBeenCalled();
  });

  it("FT-2: loads even when the legacy spcode_auto_load flag is false", async () => {
    // Silent loading is always on since 2026-09-09; the switch was
    // removed from the UI and the composable no longer reads it.
    mockPost(successPayload());
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const r = await silentLoad({
      project: { ...baseProject, spcode_auto_load: false },
      umo: "u1",
    });
    expect(r?.loaded).toBe(true);
    expect(pluginExtensionApi.post).toHaveBeenCalledTimes(1);
  });

  it("FT-2b: forwards no_agentsmd / no_codegraph from the project config", async () => {
    mockPost(successPayload());
    const { silentLoad } = useSpcodeProjectAutoLoad();
    await silentLoad({
      project: {
        ...baseProject,
        spcode_no_agentsmd: true,
        spcode_no_codegraph: true,
      },
      umo: "u1",
    });
    expect(pluginExtensionApi.post).toHaveBeenCalledWith(
      "spcode/project-load",
      {
        directory: "/abs/repo",
        umo: "u1",
        force: false,
        no_agentsmd: true,
        no_codegraph: true,
      },
      expect.anything(),
    );
  });

  it("FT-3: success returns data and posts exactly once", async () => {
    mockPost(successPayload());
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const r = await silentLoad({ project: baseProject, umo: "u1" });
    expect(r?.loaded).toBe(true);
    expect(pluginExtensionApi.post).toHaveBeenCalledTimes(1);
  });

  it("FT-4: no_project_loaded + matching directory is treated as success", async () => {
    mockPost(
      failurePayload("no_project_loaded", {
        previous_directory: "/abs/repo",
      }),
    );
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const r = await silentLoad({ project: baseProject, umo: "u1" });
    expect(r?.loaded).toBe(true);
    expect(pluginExtensionApi.post).toHaveBeenCalledTimes(1);
  });

  it("FT-5/6: no_project_loaded + mismatch retries once with force=true", async () => {
    // 2026-08-07: binding is the source of truth — the force retry is
    // unconditional (the spcode_force flag was removed as dead state).
    mockPost(
      failurePayload("no_project_loaded", {
        previous_directory: "/old",
      }),
    );
    mockPost(successPayload());
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const r = await silentLoad({ project: baseProject, umo: "u1" });
    expect(r?.loaded).toBe(true);
    expect(pluginExtensionApi.post).toHaveBeenCalledTimes(2);
    const secondCall = (
      pluginExtensionApi.post as ReturnType<typeof vi.fn>
    ).mock.calls[1];
    expect(secondCall[1].force).toBe(true);
  });

  it("FT-7: git_error throws ProjectLoadError with reason git_error", async () => {
    mockPost(
      failurePayload("git_error", {
        substep_messages: ["ERR [2/3] codegraph init failed"],
      }),
    );
    const { silentLoad } = useSpcodeProjectAutoLoad();
    await expect(
      silentLoad({ project: baseProject, umo: "u1" }),
    ).rejects.toMatchObject({ reason: "git_error" });
  });

  it("FT-8: feature_disabled throws ProjectLoadError with that reason", async () => {
    mockPost(failurePayload("feature_disabled"));
    const { silentLoad } = useSpcodeProjectAutoLoad();
    await expect(
      silentLoad({ project: baseProject, umo: "u1" }),
    ).rejects.toMatchObject({ reason: "feature_disabled" });
  });

  it("FT-9: inflight serializes same (project, umo) — concurrent calls share one promise", async () => {
    let resolveFirst!: (v: unknown) => void;
    (
      pluginExtensionApi.post as ReturnType<typeof vi.fn>
    ).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const p1 = silentLoad({ project: baseProject, umo: "u1" });
    const p2 = silentLoad({ project: baseProject, umo: "u1" });
    expect(pluginExtensionApi.post).toHaveBeenCalledTimes(1);
    resolveFirst({
      data: {
        status: "ok",
        data: successPayload(),
      },
    });
    const [r1, r2] = await Promise.all([p1, p2]);
    expect(r1).toBe(r2);
  });

  it("FT-10: inflight isolated by (project, umo) — different keys do not block each other", async () => {
    let resolveFirst!: (v: unknown) => void;
    (
      pluginExtensionApi.post as ReturnType<typeof vi.fn>
    ).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );
    mockPost(successPayload({ directory: "/b" }));
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const p1 = silentLoad({ project: baseProject, umo: "u1" });
    const p2 = silentLoad({
      project: { ...baseProject, project_id: "p-2" },
      umo: "u1",
    });
    expect(pluginExtensionApi.post).toHaveBeenCalledTimes(2);
    resolveFirst({
      data: {
        status: "ok",
        data: successPayload({ directory: "/a" }),
      },
    });
    await Promise.all([p1, p2]);
  });

  it("FT-11: network timeout throws ProjectLoadError(network_timeout)", async () => {
    (
      pluginExtensionApi.post as ReturnType<typeof vi.fn>
    ).mockImplementationOnce(
      (
        _path: string,
        _data: unknown,
        config?: { signal?: AbortSignal },
      ) =>
        new Promise((_resolve, reject) => {
          config?.signal?.addEventListener("abort", () => {
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        }),
    );
    const { silentLoad } = useSpcodeProjectAutoLoad();
    await expect(
      silentLoad({ project: baseProject, umo: "u1", timeoutMs: 50 }),
    ).rejects.toMatchObject({ reason: "network_timeout" });
  });
});
