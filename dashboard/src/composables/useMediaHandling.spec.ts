import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fileApi } from "@/api/v1";
import { useMediaHandling } from "./useMediaHandling";
import { resetAccountSession } from "@/utils/accountSession";

vi.mock("@/api/v1", () => ({
  fileApi: {
    upload: vi.fn(),
    initUpload: vi.fn(),
    uploadChunk: vi.fn(),
    completeUpload: vi.fn(),
    abortUpload: vi.fn(),
    statusUpload: vi.fn(),
  },
}));

const ok = (data: any = {}): any => ({ data: { status: "ok", data } });

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("crypto", {});
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:preview");
  vi.mocked(fileApi.abortUpload).mockResolvedValue(ok());
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("staged attachment uploads", () => {
  it("clears previews and rejects an upload that finishes after account switch", async () => {
    let finish!: (value: any) => void;
    const media = useMediaHandling();
    vi.mocked(fileApi.upload).mockResolvedValueOnce(
      ok({ attachment_id: "first", filename: "first.txt", type: "file" }),
    );
    await media.processAndUploadFile(new File(["one"], "first.txt"));
    vi.mocked(fileApi.upload).mockImplementationOnce(
      () => new Promise((resolve) => { finish = resolve; }),
    );
    const pending = media.processAndUploadFile(new File(["two"], "second.txt"));
    await vi.waitFor(() => expect(fileApi.upload).toHaveBeenCalledTimes(2));
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    resetAccountSession();
    expect(media.stagedFiles.value).toEqual([]);
    expect(revoke).toHaveBeenCalledWith("blob:preview");
    finish(ok({ attachment_id: "second", filename: "second.txt", type: "file" }));
    expect(await pending).toBeUndefined();
    expect(media.stagedFiles.value).toEqual([]);
    expect(useMediaHandling().stagedFiles.value).toEqual([]);
  });

  it("does not turn a rejected upload envelope into a staged draft", async () => {
    vi.mocked(fileApi.upload).mockResolvedValue({ data: { status: "error", message: "Not available" } } as any);
    const media = useMediaHandling();
    expect(await media.processAndUploadFile(new File(["x"], "x.txt"))).toBeUndefined();
    expect(media.stagedFiles.value).toEqual([]);
  });

  it("keeps small-file upload, image previews and duplicate detection", async () => {
    vi.mocked(fileApi.upload).mockResolvedValue(
      ok({ attachment_id: "image", filename: "image.png", type: "image" }),
    );
    const media = useMediaHandling();
    const image = new File(["image"], "image.png", { type: "image/png" });
    const result = await media.processAndUploadImage(image);
    expect(result).toMatchObject({
      attachment_id: "image",
      original_name: "image.png",
      url: "blob:preview",
    });
    expect(media.stagedImagesUrl.value).toEqual(["blob:preview"]);
    await media.processAndUploadImage(image);
    expect(fileApi.upload).toHaveBeenCalledTimes(1);
    expect(fileApi.initUpload).not.toHaveBeenCalled();
  });

  it("hashes large files in blocks and preserves previews after resuming", async () => {
    const digest = vi.fn(
      async (_algorithm: string, _data: BufferSource) => new ArrayBuffer(32),
    );
    vi.stubGlobal("crypto", { subtle: { digest } });
    vi.mocked(fileApi.initUpload).mockResolvedValue(
      ok({
        upload_id: "original",
        chunk_size: 16 * 1024 * 1024,
        total_chunks: 2,
      }),
    );
    let failing = true;
    vi.mocked(fileApi.uploadChunk).mockImplementation(async (payload) => {
      if (failing && "chunk_index" in payload && payload.chunk_index === 1)
        throw new Error("Offline");
      return ok();
    });
    vi.mocked(fileApi.statusUpload).mockResolvedValue(
      ok({ received_chunks: [0] }),
    );
    vi.mocked(fileApi.completeUpload).mockResolvedValue(
      ok({ attachment_id: "large", filename: "image.png", type: "image" }),
    );
    const media = useMediaHandling();
    const image = new File([new Uint8Array(32 * 1024 * 1024)], "image.png", {
      type: "image/png",
    });
    const wholeFileRead = vi.spyOn(image, "arrayBuffer");
    await media.processAndUploadImage(image);
    expect(wholeFileRead).not.toHaveBeenCalled();
    expect(digest).toHaveBeenCalledTimes(5);
    expect(
      digest.mock.calls
        .slice(0, 4)
        .every((call) => call[1].byteLength === 8 * 1024 * 1024),
    ).toBe(true);
    expect(media.failedUploadViews.value).toMatchObject([
      { name: "image.png", error: "Offline" },
    ]);
    expect(media.stagedImagesUrl.value).toEqual([]);

    failing = false;
    vi.mocked(fileApi.uploadChunk).mockClear();
    await media.retryFailedUpload(0);
    expect(fileApi.initUpload).toHaveBeenCalledTimes(1);
    expect(fileApi.uploadChunk).toHaveBeenCalledTimes(1);
    expect(fileApi.uploadChunk).toHaveBeenCalledWith(
      expect.objectContaining({ upload_id: "original", chunk_index: 1 }),
    );
    expect(media.failedUploadViews.value).toEqual([]);
    expect(media.activeUploadViews.value).toEqual([]);
    expect(media.stagedImagesUrl.value).toEqual(["blob:preview"]);
    expect(media.stagedFiles.value[0].original_name).toBe("image.png");
    expect(fileApi.upload).not.toHaveBeenCalled();
  });

  it("discards failed sessions during component cleanup", async () => {
    vi.mocked(fileApi.initUpload).mockResolvedValue(
      ok({
        upload_id: "failed",
        chunk_size: 32 * 1024 * 1024,
        total_chunks: 1,
      }),
    );
    vi.mocked(fileApi.uploadChunk).mockRejectedValue(new Error("Offline"));
    const media = useMediaHandling();
    await media.processAndUploadFile(
      new File([new Uint8Array(32 * 1024 * 1024)], "large.bin"),
    );
    media.cleanupMediaCache();
    expect(fileApi.abortUpload).toHaveBeenCalledWith({ upload_id: "failed" });
    expect(media.failedUploadViews.value).toEqual([]);
    expect(media.stagedFiles.value).toEqual([]);
  });
});
