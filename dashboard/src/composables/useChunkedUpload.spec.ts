import { describe, expect, it, vi } from "vitest";
import { useChunkedUpload, type ChunkedUploadApi } from "./useChunkedUpload";

const ok = (data: any = {}) => ({ data: { status: "ok", data } });
const file = () => new File([new Uint8Array(10)], "report.bin");

function makeApi(): ChunkedUploadApi {
  return {
    initUpload: vi.fn(async () =>
      ok({ upload_id: "original", chunk_size: 4, total_chunks: 3 }),
    ),
    uploadChunk: vi.fn(async () => ok()),
    completeUpload: vi.fn(async () => ok({ attachment_id: "attachment" })),
    abortUpload: vi.fn(async () => ok()),
    statusUpload: vi.fn(async () => ok({ received_chunks: [0, 2] })),
  };
}

describe("chunked upload scheduling", () => {
  it("awaits server status and resumes only missing chunks in the same session", async () => {
    const api = makeApi();
    let failing = true;
    vi.mocked(api.uploadChunk).mockImplementation(async ({ chunk_index }) => {
      if (failing && chunk_index === 1) throw new Error("Connection lost");
      return ok();
    });
    const uploader = useChunkedUpload(api);
    await uploader.start(file());
    expect(uploader.status.value).toBe("error");
    expect(uploader.uploadedBytes.value).toBe(6);
    expect(
      vi
        .mocked(api.uploadChunk)
        .mock.calls.filter(([p]) => p.chunk_index === 1),
    ).toHaveLength(3);

    failing = false;
    vi.mocked(api.uploadChunk).mockClear();
    await uploader.resume();

    expect(api.statusUpload).toHaveBeenCalledWith({ upload_id: "original" });
    expect(api.initUpload).toHaveBeenCalledTimes(1);
    expect(api.uploadChunk).toHaveBeenCalledTimes(1);
    expect(api.uploadChunk).toHaveBeenCalledWith(
      expect.objectContaining({ upload_id: "original", chunk_index: 1 }),
    );
    expect(uploader.uploadedBytes.value).toBe(10);
    expect(uploader.percent.value).toBe(100);
    expect(uploader.status.value).toBe("done");
  });

  it("starts a fresh session if the failed session has expired", async () => {
    const api = makeApi();
    vi.mocked(api.uploadChunk).mockRejectedValue(new Error("Connection lost"));
    const uploader = useChunkedUpload(api);
    await uploader.start(file());
    vi.mocked(api.statusUpload).mockResolvedValue({
      data: { status: "error", message: "Expired" },
    });
    vi.mocked(api.initUpload).mockResolvedValue(
      ok({ upload_id: "new", chunk_size: 4, total_chunks: 3 }),
    );
    vi.mocked(api.uploadChunk).mockClear().mockResolvedValue(ok());

    await uploader.resume();

    expect(api.initUpload).toHaveBeenCalledTimes(2);
    expect(api.uploadChunk).toHaveBeenCalledTimes(3);
    expect(
      vi
        .mocked(api.uploadChunk)
        .mock.calls.every(([p]) => p.upload_id === "new"),
    ).toBe(true);
    expect(uploader.percent.value).toBe(100);
  });

  it("bounds concurrent requests and counts the smaller final chunk", async () => {
    const api = makeApi();
    vi.mocked(api.initUpload).mockResolvedValue(
      ok({ upload_id: "original", chunk_size: 1, total_chunks: 10 }),
    );
    const pending: Array<() => void> = [];
    let active = 0;
    let peak = 0;
    vi.mocked(api.uploadChunk).mockImplementation(
      () =>
        new Promise((resolve) => {
          peak = Math.max(peak, ++active);
          pending.push(() => {
            active--;
            resolve(ok());
          });
        }),
    );
    const uploader = useChunkedUpload(api);
    const run = uploader.start(file());
    await vi.waitFor(() => expect(pending).toHaveLength(5));
    while (uploader.status.value === "uploading") {
      pending.splice(0).forEach((resolve) => resolve());
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    await run;
    expect(peak).toBe(5);
    expect(uploader.uploadedBytes.value).toBe(10);
  });

  it("aborts a session whose initialization completes after cancellation", async () => {
    const api = makeApi();
    let finishInit!: (data: any) => void;
    vi.mocked(api.initUpload).mockImplementation(
      () =>
        new Promise((resolve) => {
          finishInit = resolve;
        }),
    );
    const uploader = useChunkedUpload(api);
    const run = uploader.start(file());
    await uploader.cancel();
    finishInit(ok({ upload_id: "late", chunk_size: 4, total_chunks: 3 }));
    expect(await run).toBeUndefined();
    expect(api.abortUpload).toHaveBeenCalledWith({ upload_id: "late" });
    expect(api.uploadChunk).not.toHaveBeenCalled();
    expect(uploader.status.value).toBe("idle");
  });

  it("does not stage a late completion after cancellation", async () => {
    const api = makeApi();
    let finishComplete!: (data: any) => void;
    vi.mocked(api.completeUpload).mockImplementation(
      () =>
        new Promise((resolve) => {
          finishComplete = resolve;
        }),
    );
    const uploader = useChunkedUpload(api);
    const run = uploader.start(file());
    await vi.waitFor(() => expect(api.completeUpload).toHaveBeenCalledTimes(1));
    await uploader.cancel();
    finishComplete(ok({ attachment_id: "late" }));
    expect(await run).toBeUndefined();
    expect(uploader.status.value).toBe("idle");
    expect(api.abortUpload).toHaveBeenCalledWith({ upload_id: "original" });
  });
});
