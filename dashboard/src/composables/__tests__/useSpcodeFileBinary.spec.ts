// Author: elecvoid243, 2026-07-22
import { describe, it, expect, vi, beforeEach } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

// Mock the spcode project status composable BEFORE importing the SUT.
vi.mock("../useSpcodeProjectStatus", () => ({
  useSpcodeProjectStatus: () => ({
    status: {
      value: { umo: "umo-test", directory: "D:/repo", loaded: true },
    },
    refresh: vi.fn(),
  }),
}));

const mockGet = vi.fn();
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

import {
  useSpcodeFileBinary,
  parseContentDisposition,
} from "../useSpcodeFileBinary";

interface MockResponse {
  status: number;
  data: Blob;
  headers: Record<string, string>;
}

function okBlob(
  data: ArrayBuffer | string,
  headers: Record<string, string> = {},
) {
  const buf =
    typeof data === "string" ? new TextEncoder().encode(data).buffer : data;
  return {
    status: 200,
    data: new Blob([buf], {
      type: headers["content-type"] ?? "application/pdf",
    }),
    headers: { ...headers },
  };
}

function notModified() {
  return { status: 304, data: new Blob(), headers: {} };
}

function withSetup<T>(fn: () => T): T {
  let result: T;
  const Comp = defineComponent({
    setup() {
      result = fn();
      return () => h("div");
    },
  });
  mount(Comp);
  return result!;
}

describe("useSpcodeFileBinary — shell", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("fetchBinary() emits a GET with responseType=blob and expected params", async () => {
    mockGet.mockResolvedValueOnce(
      okBlob(new Uint8Array([0x25, 0x50, 0x44, 0x46]), {
        "content-type": "application/pdf",
        etag: 'W/"m1"',
        "content-disposition": 'attachment; filename="doc.pdf"',
      }),
    );
    const c = withSetup(() => useSpcodeFileBinary(null));
    await c.fetchBinary("docs/doc.pdf", "");
    expect(mockGet).toHaveBeenCalledWith(
      "spcode/file-binary",
      expect.objectContaining({
        responseType: "blob",
        params: expect.objectContaining({
          umo: "umo-test",
          path: "docs/doc.pdf",
        }),
      }),
    );
    expect(c.getState("docs/doc.pdf", "").kind).toBe("ok");
    const data = c.getData("docs/doc.pdf", "");
    expect(data?.kind).toBe("pdf");
    expect(data?.filename).toBe("doc.pdf");
    expect(data?.etag).toBe('W/"m1"');
  });

  it("detects image kind from content-disposition filename (case-insensitive)", async () => {
    mockGet.mockResolvedValueOnce(
      okBlob(new Uint8Array([0x89, 0x50, 0x4e, 0x47]), {
        "content-type": "image/png",
        "content-disposition": 'inline; filename="Logo.PNG"',
      }),
    );
    const c = withSetup(() => useSpcodeFileBinary(null));
    await c.fetchBinary("assets/Logo.PNG", "");
    const data = c.getData("assets/Logo.PNG", "");
    expect(data?.kind).toBe("image");
    expect(data?.filename).toBe("Logo.PNG");
  });

  it("passes ref and worktree params when provided", async () => {
    mockGet.mockResolvedValueOnce(
      okBlob(new Uint8Array([0x25, 0x50, 0x44, 0x46]), {
        "content-type": "application/pdf",
      }),
    );
    const c = withSetup(() => useSpcodeFileBinary("feat"));
    await c.fetchBinary("docs/doc.pdf", "abc123");
    expect(mockGet).toHaveBeenCalledWith(
      "spcode/file-binary",
      expect.objectContaining({
        params: expect.objectContaining({
          umo: "umo-test",
          worktree: "feat",
          ref: "abc123",
          path: "docs/doc.pdf",
        }),
      }),
    );
  });

  it("sends If-None-Match on second call and treats 304 as notModified", async () => {
    mockGet
      .mockResolvedValueOnce(
        okBlob(new Uint8Array([0x25, 0x50, 0x44, 0x46]), {
          "content-type": "application/pdf",
          etag: 'W/"m1"',
        }),
      )
      .mockResolvedValueOnce(notModified());
    const c = withSetup(() => useSpcodeFileBinary(null));
    await c.fetchBinary("docs/doc.pdf", "");
    await c.fetchBinary("docs/doc.pdf", "");
    const lastCall = mockGet.mock.calls[1]?.[1] as
      | { headers?: Record<string, string> }
      | undefined;
    expect(lastCall?.headers?.["If-None-Match"]).toBe('W/"m1"');
    const state = c.getState("docs/doc.pdf", "");
    if (state.kind !== "ok") throw new Error("expected ok state");
    expect(state.notModified).toBe(true);
  });

  it("maps 415 response to unsupported_type reason", async () => {
    mockGet.mockRejectedValueOnce({
      response: { status: 415 },
      message: "Request failed with status 415",
    });
    const c = withSetup(() => useSpcodeFileBinary(null));
    await c.fetchBinary("docs/bad.bin", "");
    const state = c.getState("docs/bad.bin", "");
    expect(state.kind).toBe("error");
    if (state.kind !== "error") throw new Error("expected error state");
    expect(state.reason).toBe("unsupported_type");
  });

  it("maps 413 response to too_large reason", async () => {
    mockGet.mockRejectedValueOnce({
      response: { status: 413 },
      message: "Request failed with status 413",
    });
    const c = withSetup(() => useSpcodeFileBinary(null));
    await c.fetchBinary("docs/big.pdf", "");
    const state = c.getState("docs/big.pdf", "");
    if (state.kind !== "error") throw new Error("expected error state");
    expect(state.reason).toBe("too_large");
  });

  it("maps ERR_NETWORK to network reason", async () => {
    mockGet.mockRejectedValueOnce({ code: "ERR_NETWORK", message: "down" });
    const c = withSetup(() => useSpcodeFileBinary(null));
    await c.fetchBinary("docs/doc.pdf", "");
    const state = c.getState("docs/doc.pdf", "");
    if (state.kind !== "error") throw new Error("expected error state");
    expect(state.reason).toBe("network");
  });

  it("invalidateAll() clears caches and etag map (next call omits If-None-Match)", async () => {
    mockGet.mockResolvedValue(
      okBlob(new Uint8Array([0x25, 0x50, 0x44, 0x46]), {
        "content-type": "application/pdf",
        etag: 'W/"m1"',
      }),
    );
    const c = withSetup(() => useSpcodeFileBinary(null));
    await c.fetchBinary("docs/doc.pdf", "");
    c.invalidateAll();
    await c.fetchBinary("docs/doc.pdf", "");
    const lastCall = mockGet.mock.calls[1]?.[1] as
      | { headers?: Record<string, string> }
      | undefined;
    expect(lastCall?.headers?.["If-None-Match"]).toBeUndefined();
  });

  it("dispose() aborts in-flight and clears caches", async () => {
    let abortSignal: AbortSignal | undefined;
    mockGet.mockImplementationOnce(
      (_path: string, cfg?: { signal?: AbortSignal }) => {
        abortSignal = cfg?.signal;
        return new Promise(() => {
          // never resolves; abort is the only way out
        });
      },
    );
    const c = withSetup(() => useSpcodeFileBinary(null));
    void c.fetchBinary("docs/doc.pdf", "");
    expect(abortSignal?.aborted).toBe(false);
    c.dispose();
    expect(abortSignal?.aborted).toBe(true);
  });

  it("returns idle when no fetch has been issued for a key", () => {
    const c = withSetup(() => useSpcodeFileBinary(null));
    expect(c.getState("missing.pdf", "").kind).toBe("idle");
    expect(c.getData("missing.pdf", "")).toBeNull();
    expect(c.isLoading("missing.pdf", "")).toBe(false);
  });
});

describe("parseContentDisposition", () => {
  it("decodes RFC 5987 filename* with UTF-8 percent-encoded", () => {
    const cd =
      "attachment; filename*=UTF-8''%E4%B8%AD%E6%96%87%E6%96%87%E4%BB%B6.pdf";
    expect(parseContentDisposition(cd, "fallback")).toBe("中文文件.pdf");
  });

  it("falls back to legacy filename= when RFC 5987 absent", () => {
    const cd = 'attachment; filename="plain.pdf"';
    expect(parseContentDisposition(cd, "fallback")).toBe("plain.pdf");
  });

  it("returns defaultName when header is empty / unparseable", () => {
    expect(parseContentDisposition(null, "fb.pdf")).toBe("fb.pdf");
    expect(parseContentDisposition("", "fb.pdf")).toBe("fb.pdf");
    expect(parseContentDisposition("attachment", "fb.pdf")).toBe("fb.pdf");
  });
});

describe("host binary lifecycle", () => {
  beforeEach(() => mockGet.mockReset());
  it("uses explicit host scope without project or Git parameters and rereads current bytes", async () => {
    mockGet.mockResolvedValue(
      okBlob("current", { etag: "disk", "content-type": "application/pdf" }),
    );
    const c = withSetup(() => useSpcodeFileBinary("ignored", true));
    await c.fetchBinary("G:/中文 space.pdf", "");
    await c.fetchBinary("G:/中文 space.pdf", "");
    expect(mockGet.mock.calls[1][1].params).toEqual({
      scope: "host",
      path: "G:/中文 space.pdf",
    });
    expect(mockGet.mock.calls[1][1].headers).toEqual({});
    expect(c.getState("G:/中文 space.pdf", "").kind).toBe("ok");
  });
  it("ignores an older same-path response even when transport ignores abort", async () => {
    let finish!: (value: unknown) => void;
    mockGet.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    mockGet.mockResolvedValueOnce(okBlob("latest"));
    const c = withSetup(() => useSpcodeFileBinary(null, true));
    const old = c.fetchBinary("G:/file.pdf", "");
    await c.fetchBinary("G:/file.pdf", "");
    const current = c.getData("G:/file.pdf", "");
    finish(okBlob("outdated"));
    await old;
    expect(c.getData("G:/file.pdf", "")).toBe(current);
    expect(mockGet.mock.calls[0][1].signal.aborted).toBe(true);
  });
  it("recognizes an actual disabled-plugin JSON envelope instead of rendering it", async () => {
    const payload = JSON.stringify({
      status: "error",
      message: "spcode is disabled",
      data: null,
    });
    const blob = new Blob([payload], { type: "application/json" });
    blob.text = async () => payload;
    mockGet.mockResolvedValueOnce({
      status: 200,
      data: blob,
      headers: { "content-type": "application/json" },
    });
    const c = withSetup(() => useSpcodeFileBinary(null, true));
    await c.fetchBinary("G:/file.pdf", "");
    expect(c.getState("G:/file.pdf", "")).toEqual({
      kind: "error",
      reason: "plugin_unavailable",
    });
    expect(c.getData("G:/file.pdf", "")).toBeNull();
  });
  it("discards requests invalidated while waiting for bytes", async () => {
    let finish!: (value: unknown) => void;
    mockGet.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const c = withSetup(() => useSpcodeFileBinary(null, true));
    const pending = c.fetchBinary("G:/file.pdf", "");
    c.invalidateAll();
    finish(okBlob("stale"));
    await pending;
    expect(c.getState("G:/file.pdf", "").kind).toBe("idle");
  });
});
