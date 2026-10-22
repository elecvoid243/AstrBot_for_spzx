// Author: elecvoid243 @ 2026-10-22
// Pure-function coverage for the Web preview tab: URL normalization,
// sandbox policy and per-project persistence.

import { describe, expect, it } from "vitest";
import {
  MAX_BOOKMARKS,
  bookmarkLabel,
  buildIframeSandbox,
  loadBrowserState,
  normalizeBrowserUrl,
  saveBrowserState,
  webBrowserStorageKey,
  type BrowserStorage,
} from "./webBrowser";

function memoryStorage(): BrowserStorage & { map: Map<string, string> } {
  const map = new Map<string, string>();
  return {
    map,
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
  };
}

describe("normalizeBrowserUrl", () => {
  it.each([
    ["localhost:3000", "http://localhost:3000/"],
    ["localhost:3000/app?tab=1#top", "http://localhost:3000/app?tab=1#top"],
    ["127.0.0.1:5173", "http://127.0.0.1:5173/"],
    ["[::1]:8000", "http://[::1]:8000/"],
    ["192.168.1.10:8080", "http://192.168.1.10:8080/"],
    ["example.com", "https://example.com/"],
    ["example.com/docs", "https://example.com/docs"],
    ["https://example.com/a", "https://example.com/a"],
    ["//example.com/x", "https://example.com/x"],
    ["myserver:8080", "http://myserver:8080/"],
    ["  localhost:4173  ", "http://localhost:4173/"],
  ])("%s → %s", (raw, expected) => {
    expect(normalizeBrowserUrl(raw)).toEqual({ ok: true, url: expected });
  });

  it("reports an empty address bar as empty, not invalid", () => {
    expect(normalizeBrowserUrl("   ")).toEqual({ ok: false, reason: "empty" });
  });

  it.each([
    "javascript:alert(1)",
    "data:text/html,<h1>x</h1>",
    "file:///C:/Windows/win.ini",
    "ftp://example.com/file",
    "ws://localhost:3000",
  ])("rejects unsupported scheme %s", (raw) => {
    expect(normalizeBrowserUrl(raw)).toEqual({ ok: false, reason: "scheme" });
  });

  it.each(["http://", "https://", "http://exa mple.com"])(
    "rejects malformed %s",
    (raw) => {
      expect(normalizeBrowserUrl(raw)).toEqual({
        ok: false,
        reason: "invalid",
      });
    },
  );
});

describe("buildIframeSandbox", () => {
  const origin = "http://localhost:6185";

  it("keeps allow-same-origin for cross-origin targets", () => {
    const sandbox = buildIframeSandbox("http://localhost:3000/", origin);
    expect(sandbox.split(" ")).toContain("allow-same-origin");
    expect(sandbox.split(" ")).toContain("allow-scripts");
  });

  it("drops allow-same-origin when embedding the dashboard itself", () => {
    const sandbox = buildIframeSandbox("http://localhost:6185/chat", origin);
    expect(sandbox.split(" ")).not.toContain("allow-same-origin");
    expect(sandbox.split(" ")).toContain("allow-scripts");
  });
});

describe("bookmarkLabel", () => {
  it("strips the scheme and the trailing slash", () => {
    expect(bookmarkLabel("http://localhost:3000/")).toBe("localhost:3000");
    expect(bookmarkLabel("https://example.com/docs/intro")).toBe(
      "example.com/docs/intro",
    );
  });

  it("falls back to the raw string when parsing fails", () => {
    expect(bookmarkLabel("not a url")).toBe("not a url");
  });
});

describe("per-project persistence", () => {
  it("keys the same repo identically across case and separators", () => {
    const a = webBrowserStorageKey("F:\\github\\Astrbot\\");
    const b = webBrowserStorageKey("f:/github/astrbot");
    expect(a).toBe(b);
    expect(a.startsWith("spcode.webBrowser.")).toBe(true);
  });

  it("returns an empty key without a project root", () => {
    expect(webBrowserStorageKey(null)).toBe("");
    expect(webBrowserStorageKey("  ")).toBe("");
  });

  it("round-trips state and normalizes the stored URL", () => {
    const storage = memoryStorage();
    saveBrowserState(
      "F:/repo",
      {
        url: "localhost:3000",
        bookmarks: ["https://example.com/"],
      },
      storage,
    );
    expect(loadBrowserState("F:/repo", storage)).toEqual({
      url: "http://localhost:3000/",
      bookmarks: ["https://example.com/"],
    });
  });

  it("does not leak state across projects", () => {
    const storage = memoryStorage();
    saveBrowserState(
      "F:/repo-a",
      { url: "http://localhost:3000/", bookmarks: [] },
      storage,
    );
    expect(loadBrowserState("F:/repo-b", storage).url).toBe("");
  });

  it("falls back to an empty state on corrupt payloads", () => {
    const storage = memoryStorage();
    const key = webBrowserStorageKey("F:/repo");
    storage.setItem(key, "{not json");
    expect(loadBrowserState("F:/repo", storage).url).toBe("");
    storage.setItem(key, JSON.stringify({ url: "javascript:alert(1)" }));
    expect(loadBrowserState("F:/repo", storage)).toEqual({
      url: "",
      bookmarks: [],
    });
  });

  it("caps and dedupes bookmarks", () => {
    const storage = memoryStorage();
    const key = webBrowserStorageKey("F:/repo");
    const bookmarks = Array.from(
      { length: MAX_BOOKMARKS + 4 },
      (_, i) => `http://localhost:${3000 + i}/`,
    );
    bookmarks.push(bookmarks[0]);
    storage.setItem(key, JSON.stringify({ url: "", bookmarks }));
    const state = loadBrowserState("F:/repo", storage);
    expect(state.bookmarks).toHaveLength(MAX_BOOKMARKS);
    expect(new Set(state.bookmarks).size).toBe(MAX_BOOKMARKS);
  });

  it("never writes without a project root", () => {
    const storage = memoryStorage();
    saveBrowserState(
      null,
      { url: "http://localhost:3000/", bookmarks: [] },
      storage,
    );
    expect(storage.map.size).toBe(0);
  });
});
