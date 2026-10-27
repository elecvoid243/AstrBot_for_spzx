// Author: elecvoid243
// Date: 2026-10-25
//
// useSpcodeFileDiff — on-demand per-file patch cache for the GitDiff
// sidebar (v2.32.0 progressive loading). The list endpoint no longer
// ships patch text; expanding a file row calls `get(path)` which
// fetches GET /spcode/git-diff/file once and caches the slice.
//
// Cache semantics:
// - Key: `${path}#${scope}#${worktree ?? ""}#${etag}` — any dimension
//   change (scope switch, worktree switch, worktree state moved) is a
//   new key; the snapshot's etag is threaded in by the consumer, so a
//   poll that observes a new etag naturally invalidates old entries.
// - LRU cap: entries evicted oldest-first beyond MAX_CACHED_SLICES.
// - In-flight dedup: concurrent expands of the same file share one
//   request (the entry is created synchronously in `loading` state).
//
// Binary files: the endpoint returns is_binary=true with diff=null —
// cached as `ok` with a null slice; GitDiffFileItem renders the binary
// stub off `isBinary` and never needs the slice.

import { ref, type Ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";

/** Shape of GET /spcode/git-diff/file's data envelope. */
interface SpcodeGitFileDiffRawResponse {
  loaded: boolean;
  path: string;
  scope: string;
  is_binary: boolean;
  diff: string | null;
  truncated: boolean;
  truncated_at_bytes: number;
  max_bytes: number;
  elapsed_ms: number;
  reason: string | null;
}

export interface FileDiffEntry {
  status: "loading" | "ok" | "error";
  /** Unified patch; null for binary files and while loading. */
  slice: string | null;
  isBinary: boolean;
  /** The per-file patch was cut off at the server cap (256KB). */
  truncated: boolean;
  reason?: string;
}

export interface UseSpcodeFileDiff {
  /** Reactive version counter — bumps on every cache mutation so Vue
   *  consumers re-render rows whose entry changed. */
  version: Ref<number>;
  /** Read a cache entry (undefined = never fetched). */
  get: (key: string) => FileDiffEntry | undefined;
  /** Build the cache key for one file row. */
  keyFor: (
    path: string,
    scope: string,
    worktree: string | null,
    etag: string | null,
  ) => string;
  /** Fetch (once) and cache the patch for a file; returns the entry. */
  load: (
    path: string,
    scope: string,
    worktree: string | null,
    etag: string | null,
  ) => Promise<FileDiffEntry>;
  /** Drop every cached entry (worktree state moved). */
  clear: () => void;
}

const MAX_CACHED_SLICES = 40;

export function useSpcodeFileDiff() {
  const cache = new Map<string, FileDiffEntry>();
  const version = ref(0);

  function touch(): void {
    version.value += 1;
  }

  function keyFor(
    path: string,
    scope: string,
    worktree: string | null,
    etag: string | null,
  ): string {
    return `${path}#${scope}#${worktree ?? ""}#${etag ?? ""}`;
  }

  function get(key: string): FileDiffEntry | undefined {
    return cache.get(key);
  }

  async function load(
    path: string,
    scope: string,
    worktree: string | null,
    etag: string | null,
  ): Promise<FileDiffEntry> {
    const key = keyFor(path, scope, worktree, etag);
    const existing = cache.get(key);
    if (existing && existing.status !== "error") return existing;

    const entry: FileDiffEntry = {
      status: "loading",
      slice: null,
      isBinary: false,
      truncated: false,
    };
    cache.set(key, entry);
    touch();

    try {
      const resp = await pluginExtensionApi.get<SpcodeGitFileDiffRawResponse>(
        "spcode/git-diff/file",
        {
          params: {
            path,
            scope,
            ...(worktree ? { worktree } : {}),
          },
        },
      );
      const data = resp.data?.data;
      if (!data || data.loaded === false) {
        throw new Error(data?.reason ?? "empty response data");
      }
      entry.status = "ok";
      entry.isBinary = data.is_binary === true;
      entry.slice = data.diff ?? null;
      entry.truncated = data.truncated === true;
    } catch (err) {
      entry.status = "error";
      entry.reason =
        (err as { message?: string })?.message ?? "unknown";
    } finally {
      // LRU: refresh recency, evict beyond the cap.
      cache.delete(key);
      cache.set(key, entry);
      while (cache.size > MAX_CACHED_SLICES) {
        const oldest = cache.keys().next().value;
        if (oldest === undefined) break;
        cache.delete(oldest);
      }
      touch();
    }
    return entry;
  }

  function clear(): void {
    cache.clear();
    touch();
  }

  return { version, get, keyFor, load, clear };
}
