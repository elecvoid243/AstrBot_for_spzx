// useSpcodeSilentOps.spec.ts
// Author: elecvoid243 @ 2026-08-06
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { post: vi.fn() },
}));

import { pluginExtensionApi } from "@/api/v1";
import {
  ProjectLoadError,
  useSpcodeSilentOps,
} from "./useSpcodeProjectAutoLoad";

const postMock = () => pluginExtensionApi.post as ReturnType<typeof vi.fn>;

function mockPostOk(extra: Record<string, unknown> = {}) {
  postMock().mockResolvedValue({
    data: { data: { success: true, reason: null, ...extra } },
  });
}

function mockPostFail(reason: string, messages: string[] = []) {
  postMock().mockResolvedValue({
    data: {
      data: { success: false, reason, substep_messages: messages },
    },
  });
}

describe("useSpcodeSilentOps", () => {
  beforeEach(() => postMock().mockReset());

  it("silentLoadDirectory posts full flag set", async () => {
    mockPostOk({ loaded: true, directory: "C:/p", umo: "u1" });
    const { silentLoadDirectory } = useSpcodeSilentOps();
    const data = await silentLoadDirectory({
      umo: "u1",
      directory: "C:/p",
      noAgentsmd: true,
      noCodegraph: true,
      force: true,
      create: true,
      gitInit: true,
    });
    expect(data.loaded).toBe(true);
    expect(postMock()).toHaveBeenCalledWith(
      "spcode/project-load",
      {
        directory: "C:/p",
        umo: "u1",
        force: true,
        no_agentsmd: true,
        no_codegraph: true,
        create: true,
        git_init: true,
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("silentLoadDirectory omits false flags", async () => {
    mockPostOk({ loaded: true, directory: "C:/p", umo: "u1" });
    const { silentLoadDirectory } = useSpcodeSilentOps();
    await silentLoadDirectory({ umo: "u1", directory: "C:/p" });
    expect(postMock()).toHaveBeenCalledWith(
      "spcode/project-load",
      { directory: "C:/p", umo: "u1", force: false },
      expect.anything(),
    );
  });

  it("silentLoadDirectory throws ProjectLoadError on failure", async () => {
    mockPostFail("path_unsafe", ["❌ path unsafe"]);
    const { silentLoadDirectory } = useSpcodeSilentOps();
    await expect(
      silentLoadDirectory({ umo: "u1", directory: "C:/x" }),
    ).rejects.toMatchObject({ reason: "path_unsafe" });
  });

  it("silentUnload posts umo only", async () => {
    mockPostOk({ unloaded: true, directory: "C:/p" });
    const { silentUnload } = useSpcodeSilentOps();
    await silentUnload("u1");
    expect(postMock()).toHaveBeenCalledWith(
      "spcode/project-unload",
      { umo: "u1" },
      expect.anything(),
    );
  });

  it("silentUnload throws on failure", async () => {
    mockPostFail("feature_disabled");
    const { silentUnload } = useSpcodeSilentOps();
    await expect(silentUnload("u1")).rejects.toBeInstanceOf(ProjectLoadError);
  });

  it("silentCodegraphSet posts umo + directory", async () => {
    mockPostOk({ set: true, directory: "C:/p", mcp_restarted: true });
    const { silentCodegraphSet } = useSpcodeSilentOps();
    await silentCodegraphSet("u1", "C:/p");
    expect(postMock()).toHaveBeenCalledWith(
      "spcode/codegraph-set",
      { umo: "u1", directory: "C:/p" },
      expect.anything(),
    );
  });

  it("silentCodegraphInit posts umo + directory", async () => {
    mockPostOk({ initialized: true, directory: "C:/p" });
    const { silentCodegraphInit } = useSpcodeSilentOps();
    await silentCodegraphInit("u1", "C:/p");
    expect(postMock()).toHaveBeenCalledWith(
      "spcode/codegraph-init",
      { umo: "u1", directory: "C:/p" },
      expect.anything(),
    );
  });

  it("silentCodegraphInit throws on failure", async () => {
    mockPostFail("tool_unavailable");
    const { silentCodegraphInit } = useSpcodeSilentOps();
    await expect(
      silentCodegraphInit("u1", "C:/p"),
    ).rejects.toBeInstanceOf(ProjectLoadError);
  });
});
