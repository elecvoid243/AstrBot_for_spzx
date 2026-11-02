import { describe, expect, it, vi } from "vitest";
import { draftApi, useAssetDraft } from "./useAssetDraft";

describe("draft save queue", () => {
  it("serializes snapshots without treating an earlier response as the latest save", async () => {
    vi.spyOn(draftApi, "create").mockResolvedValue({
      asset_id: "a",
      revision: 1,
      body: { text: "initial" },
      base_version: null,
    });
    let resolveFirst!: (value: any) => void;
    const first = new Promise<any>((resolve) => {
      resolveFirst = resolve;
    });
    const update = vi
      .spyOn(draftApi, "update")
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce({
        asset_id: "a",
        revision: 3,
        body: { text: "latest" },
        base_version: null,
      });
    const draft = useAssetDraft("role");
    await draft.open(null, {});
    draft.change({ text: "middle" });
    draft.change({ text: "latest" });
    expect(draft.status.value).toBe("saving");
    expect(update).toHaveBeenCalledTimes(1);
    resolveFirst({
      asset_id: "a",
      revision: 2,
      body: { text: "middle" },
      base_version: null,
    });
    const saved = await draft.flush();
    expect(saved.revision).toBe(3);
    expect(update).toHaveBeenLastCalledWith("role", "a", { text: "latest" }, 2);
    expect(draft.status.value).toBe("saved");
    vi.restoreAllMocks();
  });
  it("retains local content on conflict and does not retry automatically", async () => {
    vi.spyOn(draftApi, "create").mockResolvedValue({
      asset_id: "a",
      revision: 1,
      body: {},
      base_version: null,
    });
    const update = vi
      .spyOn(draftApi, "update")
      .mockRejectedValue({ response: { status: 409 } });
    const draft = useAssetDraft("workflow");
    await draft.open(null, {});
    draft.change({ text: "local" });
    await expect(draft.flush()).rejects.toBeDefined();
    expect(draft.status.value).toBe("conflict");
    draft.change({ text: "more local" });
    await draft.retry();
    expect(update).toHaveBeenCalledTimes(1);
    vi.restoreAllMocks();
  });
  it("does not apply a response from a previous editor to the current asset", async () => {
    const create = vi.spyOn(draftApi, "create");
    create.mockResolvedValueOnce({
      asset_id: "old",
      revision: 1,
      body: {},
      base_version: null,
    });
    create.mockResolvedValueOnce({
      asset_id: "new",
      revision: 1,
      body: {},
      base_version: null,
    });
    let finish!: (value: any) => void;
    vi.spyOn(draftApi, "update").mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const publish = vi
      .spyOn(draftApi, "publish")
      .mockResolvedValue({ version: 1 });
    const draft = useAssetDraft("autonomous");
    await draft.open(null, {});
    draft.change({ name: "old edit" });
    await draft.open(null, {});
    finish({
      asset_id: "old",
      revision: 2,
      body: { name: "old edit" },
      base_version: null,
    });
    await Promise.resolve();
    await draft.publish();
    expect(publish).toHaveBeenCalledWith("autonomous", "new", 1);
    expect(draft.status.value).toBe("saved");
    vi.restoreAllMocks();
  });
  it("keeps a failed snapshot and retries it explicitly", async () => {
    vi.spyOn(draftApi, "create").mockResolvedValue({
      asset_id: "a",
      revision: 1,
      body: {},
      base_version: null,
    });
    const update = vi
      .spyOn(draftApi, "update")
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({
        asset_id: "a",
        revision: 2,
        body: { name: "local" },
        base_version: null,
      });
    const draft = useAssetDraft("role");
    await draft.open(null, {});
    draft.change({ name: "local" });
    await expect(draft.flush()).rejects.toThrow("offline");
    expect(draft.status.value).toBe("failed");
    await draft.retry();
    expect(update).toHaveBeenLastCalledWith("role", "a", { name: "local" }, 1);
    expect(draft.status.value).toBe("saved");
    vi.restoreAllMocks();
  });

  it("saves local content before rebasing onto the version explicitly reviewed", async () => {
    vi.spyOn(draftApi, "create").mockResolvedValue({
      asset_id: "a",
      revision: 1,
      body: { name: "old" },
      base_version: 1,
    });
    vi.spyOn(draftApi, "publish").mockRejectedValue({
      response: { status: 409 },
    });
    vi.spyOn(draftApi, "latest").mockResolvedValue({
      version: 2,
      definition: { name: "remote" },
    });
    const update = vi
      .spyOn(draftApi, "update")
      .mockResolvedValue({
        asset_id: "a",
        revision: 2,
        body: { name: "local" },
        base_version: 1,
      });
    const rebase = vi
      .spyOn(draftApi, "rebase")
      .mockResolvedValue({
        asset_id: "a",
        revision: 3,
        body: { name: "local" },
        base_version: 2,
      });
    const draft = useAssetDraft("role");
    await draft.open(null, {});
    await expect(draft.publish()).rejects.toBeDefined();
    draft.change({ name: "local" });
    await draft.reviewPublished();
    expect(draft.baseVersion.value).toBe(1);
    expect(draft.reviewed.value.version).toBe(2);
    await draft.rebase();
    expect(update).toHaveBeenCalledWith("role", "a", { name: "local" }, 1);
    expect(rebase).toHaveBeenCalledWith("role", "a", 2, 2);
    expect(draft.baseVersion.value).toBe(2);
    vi.restoreAllMocks();
  });
});
