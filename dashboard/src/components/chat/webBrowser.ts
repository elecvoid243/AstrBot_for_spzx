// Author: elecvoid243 @ 2026-10-22
// Pure helpers for the workbench "Web preview" tab: URL normalization,
// iframe sandbox policy and per-project persistence. Kept free of Vue so
// the behaviour can be unit-tested without mounting the component.

import { fnv1aHex } from "@/composables/useRecentFiles";

export type WidthPreset = "fill" | 375 | 768 | 1280;

export const WIDTH_PRESETS: readonly WidthPreset[] = ["fill", 375, 768, 1280];

export const MAX_BOOKMARKS = 12;

/** Quick-start addresses for the empty state: common local dev ports. */
export const QUICK_URLS: readonly string[] = [
  "http://localhost:3000",
  "http://localhost:5173",
  "http://localhost:8000",
];

export type NormalizeUrlResult =
  | { ok: true; url: string }
  | { ok: false; reason: "empty" | "invalid" | "scheme" };

// `localhost[:port]`, loopback literals — dev servers are plain http.
const LOOPBACK_HOST_RE =
  /^(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?(?:[/?#]|$)/i;
// Any IPv4 literal, e.g. 192.168.1.10:8080 — also treated as http.
const IPV4_HOST_RE = /^\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?(?:[/?#]|$)/;
const HAS_SCHEME_RE = /^[a-z][a-z0-9+.-]*:/i;
// `host:port` (intranet hostname) looks like a scheme; only digits after
// the colon are treated as a port rather than an unsupported scheme.
const HOST_PORT_RE = /^[a-z][a-z0-9+.-]*:\d+(?:[/?#]|$)/i;

/**
 * Turn user input into an embeddable http(s) URL.
 *
 * Args:
 *   raw: Whatever the user typed into the address bar.
 *
 * Returns:
 *   `{ ok: true, url }` with a canonical href, or `{ ok: false, reason }`
 *   where reason is `empty`, `invalid` or `scheme` (non-http(s)).
 */
export function normalizeBrowserUrl(raw: string): NormalizeUrlResult {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: false, reason: "empty" };

  let candidate: string;
  if (/^https?:\/\//i.test(trimmed)) {
    candidate = trimmed;
  } else if (trimmed.startsWith("//")) {
    candidate = `https:${trimmed}`;
  } else if (LOOPBACK_HOST_RE.test(trimmed) || IPV4_HOST_RE.test(trimmed)) {
    candidate = `http://${trimmed}`;
  } else if (HAS_SCHEME_RE.test(trimmed)) {
    if (HOST_PORT_RE.test(trimmed)) {
      candidate = `http://${trimmed}`;
    } else {
      return { ok: false, reason: "scheme" };
    }
  } else {
    candidate = `https://${trimmed}`;
  }

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return { ok: false, reason: "invalid" };
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { ok: false, reason: "scheme" };
  }
  if (!parsed.hostname) return { ok: false, reason: "invalid" };
  return { ok: true, url: parsed.href };
}

/** Whether `url` shares an origin with the dashboard itself. */
export function isSameOrigin(url: string, origin: string): boolean {
  try {
    return new URL(url).origin === origin;
  } catch {
    return false;
  }
}

const SANDBOX_TOKENS = [
  "allow-scripts",
  "allow-forms",
  "allow-popups",
  "allow-popups-to-escape-sandbox",
  "allow-downloads",
  "allow-modals",
  "allow-same-origin",
] as const;

/**
 * Sandbox attribute for the preview iframe. Mirrors the plugin-page
 * baseline, but drops `allow-same-origin` for dashboard-origin targets so
 * an embedded dashboard route cannot reach the parent window.
 *
 * Args:
 *   url: Target URL being embedded.
 *   dashboardOrigin: `window.location.origin` of the dashboard.
 *
 * Returns:
 *   Space-separated sandbox tokens.
 */
export function buildIframeSandbox(
  url: string,
  dashboardOrigin: string,
): string {
  const tokens = isSameOrigin(url, dashboardOrigin)
    ? SANDBOX_TOKENS.filter((token) => token !== "allow-same-origin")
    : [...SANDBOX_TOKENS];
  return tokens.join(" ");
}

/** Compact chip label for a bookmark: `host + path` without the scheme. */
export function bookmarkLabel(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === "/" ? "" : parsed.pathname;
    return `${parsed.host}${path}`;
  } catch {
    return url;
  }
}

export interface BrowserState {
  url: string;
  width: WidthPreset;
  bookmarks: string[];
}

export type BrowserStorage = Pick<Storage, "getItem" | "setItem">;

export const EMPTY_BROWSER_STATE: BrowserState = {
  url: "",
  width: "fill",
  bookmarks: [],
};

/**
 * Per-project localStorage bucket. Mirrors `projectStorageKey()` in
 * docsRootStorage: Windows paths are case-insensitive, so the same repo
 * must not split into two buckets when separators or casing differ.
 */
export function webBrowserStorageKey(
  projectRoot: string | null | undefined,
): string {
  const normalized = (projectRoot ?? "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/\/+$/, "")
    .toLowerCase();
  if (!normalized) return "";
  return `spcode.webBrowser.${fnv1aHex(normalized)}`;
}

function sanitizeBookmarks(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string") continue;
    const trimmed = entry.trim();
    if (!trimmed || seen.has(trimmed)) continue;
    seen.add(trimmed);
    out.push(trimmed);
    if (out.length >= MAX_BOOKMARKS) break;
  }
  return out;
}

function sanitizeWidth(value: unknown): WidthPreset {
  return WIDTH_PRESETS.includes(value as WidthPreset)
    ? (value as WidthPreset)
    : "fill";
}

/**
 * Read the stored state for `projectRoot`. Corrupt JSON, missing keys and
 * disabled storage all fall back to an empty state instead of throwing.
 */
export function loadBrowserState(
  projectRoot: string | null | undefined,
  storage: BrowserStorage | null = defaultStorage(),
): BrowserState {
  const key = webBrowserStorageKey(projectRoot);
  if (!key || !storage) return { ...EMPTY_BROWSER_STATE };
  let raw: string | null = null;
  try {
    raw = storage.getItem(key);
  } catch {
    return { ...EMPTY_BROWSER_STATE };
  }
  if (!raw) return { ...EMPTY_BROWSER_STATE };

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ...EMPTY_BROWSER_STATE };
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ...EMPTY_BROWSER_STATE };
  }
  const record = parsed as Record<string, unknown>;
  const rawUrl = typeof record.url === "string" ? record.url.trim() : "";
  const normalized = rawUrl ? normalizeBrowserUrl(rawUrl) : null;
  return {
    url: normalized && normalized.ok ? normalized.url : "",
    width: sanitizeWidth(record.width),
    bookmarks: sanitizeBookmarks(record.bookmarks),
  };
}

/** Persist `state` for `projectRoot`. Storage failures are non-fatal. */
export function saveBrowserState(
  projectRoot: string | null | undefined,
  state: BrowserState,
  storage: BrowserStorage | null = defaultStorage(),
): void {
  const key = webBrowserStorageKey(projectRoot);
  if (!key || !storage) return;
  const normalized =
    typeof state.url === "string" && state.url
      ? normalizeBrowserUrl(state.url)
      : null;
  const payload: BrowserState = {
    url: normalized && normalized.ok ? normalized.url : "",
    width: sanitizeWidth(state.width),
    bookmarks: sanitizeBookmarks(state.bookmarks),
  };
  try {
    storage.setItem(key, JSON.stringify(payload));
  } catch {
    /* quota / private mode — non-fatal */
  }
}

function defaultStorage(): BrowserStorage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}
