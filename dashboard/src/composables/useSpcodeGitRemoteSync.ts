// Author: elecvoid243 @ 2026-08-12
// API doc: astrbot_plugin_spcode_toolkit docs/api/webapi-git-pull-push-remote-api.md
//
// Vue composable wrapping POST /spcode/git-pull, POST /spcode/git-push
// and POST /spcode/git-remote-set-url. Stateless-mutation pattern
// mirroring useSpcodeGitMerge: one in-flight call per operation, a
// busy ref per operation, reason codes surfaced verbatim for the
// caller to classify.

import { ref, type Ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeSession } from "@/composables/useSpcodeSession";
import {
  parseSpcodeGitPull,
  parseSpcodeGitPush,
  parseSpcodeGitRemoteSetUrl,
  parseSpcodeGitRemotes,
  parseSpcodeGitRemoteRemove,
  buildPullBody,
  buildPushBody,
  buildRemoteSetUrlBody,
  type SpcodePullParams,
  type SpcodePullResult,
  type SpcodePushParams,
  type SpcodePushResult,
  type SpcodeRemoteSetUrlParams,
  type SpcodeRemoteSetUrlResult,
  type SpcodeRemotesResult,
  type SpcodeRemoteRemoveResult,
} from "./parseSpcodeGitRemoteSync";

export interface UseSpcodeGitRemoteSync {
  isPulling: Ref<boolean>;
  isPushing: Ref<boolean>;
  isSettingRemote: Ref<boolean>;
  isListingRemotes: Ref<boolean>;
  isRemovingRemote: Ref<boolean>;
  pull: (params: SpcodePullParams) => Promise<SpcodePullResult>;
  push: (params: SpcodePushParams) => Promise<SpcodePushResult>;
  setRemoteUrl: (
    params: SpcodeRemoteSetUrlParams,
  ) => Promise<SpcodeRemoteSetUrlResult>;
  /** 2026-08-16: GET /spcode/git-remotes (name + url 列表)。 */
  listRemotes: () => Promise<SpcodeRemotesResult>;
  /** 2026-08-16: POST /spcode/git-remote-remove。 */
  removeRemote: (remote: string) => Promise<SpcodeRemoteRemoveResult>;
  dispose: () => void;
}

function classifyThrown(err: unknown): string {
  if ((err as { name?: string })?.name === "CanceledError") return "aborted";
  const anyErr = err as { code?: string; message?: string };
  if (anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")) {
    return "network";
  }
  return "unknown";
}

const PULL_FALLBACK: SpcodePullResult = {
  ok: false,
  reason: "aborted",
  conflict: false,
  conflictedFiles: [],
};
const PUSH_FALLBACK: SpcodePushResult = { ok: false, reason: "aborted" };
const REMOTE_FALLBACK: SpcodeRemoteSetUrlResult = {
  ok: false,
  reason: "aborted",
};
const REMOTES_FALLBACK: SpcodeRemotesResult = { ok: false, reason: "aborted" };
const REMOTE_REMOVE_FALLBACK: SpcodeRemoteRemoveResult = {
  ok: false,
  reason: "aborted",
};

export function useSpcodeGitRemoteSync(): UseSpcodeGitRemoteSync {
  const isPulling = ref(false);
  const isPushing = ref(false);
  const isSettingRemote = ref(false);
  const isListingRemotes = ref(false);
  const isRemovingRemote = ref(false);
  const session = useSpcodeSession({ requireScoped: true });
  let pullAbort: AbortController | null = null;
  let pushAbort: AbortController | null = null;
  let remoteAbort: AbortController | null = null;
  let listAbort: AbortController | null = null;
  let removeAbort: AbortController | null = null;
  let isMounted = true;

  function currentUmo(): string | null {
    return session.umo.value;
  }

  async function pull(params: SpcodePullParams): Promise<SpcodePullResult> {
    if (!isMounted) return PULL_FALLBACK;
    const umo = currentUmo();
    if (!umo)
      return {
        ok: false,
        reason: "no_project_loaded",
        conflict: false,
        conflictedFiles: [],
      };
    pullAbort?.abort();
    pullAbort = new AbortController();
    isPulling.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-pull",
        {
          umo,
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...buildPullBody(params),
        },
        { signal: pullAbort.signal },
      );
      if (!isMounted) return PULL_FALLBACK;
      return parseSpcodeGitPull(resp.data);
    } catch (err) {
      if (!isMounted) return PULL_FALLBACK;
      return {
        ok: false,
        reason: classifyThrown(err),
        conflict: false,
        conflictedFiles: [],
      };
    } finally {
      if (isMounted) isPulling.value = false;
    }
  }

  async function push(params: SpcodePushParams): Promise<SpcodePushResult> {
    if (!isMounted) return PUSH_FALLBACK;
    const umo = currentUmo();
    if (!umo) return { ok: false, reason: "no_project_loaded" };
    pushAbort?.abort();
    pushAbort = new AbortController();
    isPushing.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-push",
        {
          umo,
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...buildPushBody(params),
        },
        { signal: pushAbort.signal },
      );
      if (!isMounted) return PUSH_FALLBACK;
      return parseSpcodeGitPush(resp.data);
    } catch (err) {
      if (!isMounted) return PUSH_FALLBACK;
      return { ok: false, reason: classifyThrown(err) };
    } finally {
      if (isMounted) isPushing.value = false;
    }
  }

  async function setRemoteUrl(
    params: SpcodeRemoteSetUrlParams,
  ): Promise<SpcodeRemoteSetUrlResult> {
    if (!isMounted) return REMOTE_FALLBACK;
    const umo = currentUmo();
    if (!umo) return { ok: false, reason: "no_project_loaded" };
    remoteAbort?.abort();
    remoteAbort = new AbortController();
    isSettingRemote.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-remote-set-url",
        {
          umo,
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...buildRemoteSetUrlBody(params),
        },
        { signal: remoteAbort.signal },
      );
      if (!isMounted) return REMOTE_FALLBACK;
      return parseSpcodeGitRemoteSetUrl(resp.data);
    } catch (err) {
      if (!isMounted) return REMOTE_FALLBACK;
      return { ok: false, reason: classifyThrown(err) };
    } finally {
      if (isMounted) isSettingRemote.value = false;
    }
  }

  async function listRemotes(): Promise<SpcodeRemotesResult> {
    if (!isMounted) return REMOTES_FALLBACK;
    const umo = currentUmo();
    if (!umo) return { ok: false, reason: "no_project_loaded" };
    listAbort?.abort();
    listAbort = new AbortController();
    isListingRemotes.value = true;
    try {
      const resp = await pluginExtensionApi.get<unknown>("spcode/git-remotes", {
        params: { umo },
        signal: listAbort.signal,
      });
      if (!isMounted) return REMOTES_FALLBACK;
      return parseSpcodeGitRemotes(resp.data);
    } catch (err) {
      if (!isMounted) return REMOTES_FALLBACK;
      return { ok: false, reason: classifyThrown(err) };
    } finally {
      if (isMounted) isListingRemotes.value = false;
    }
  }

  async function removeRemote(remote: string): Promise<SpcodeRemoteRemoveResult> {
    if (!isMounted) return REMOTE_REMOVE_FALLBACK;
    const umo = currentUmo();
    if (!umo) return { ok: false, reason: "no_project_loaded" };
    removeAbort?.abort();
    removeAbort = new AbortController();
    isRemovingRemote.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-remote-remove",
        { umo, remote },
        { signal: removeAbort.signal },
      );
      if (!isMounted) return REMOTE_REMOVE_FALLBACK;
      return parseSpcodeGitRemoteRemove(resp.data);
    } catch (err) {
      if (!isMounted) return REMOTE_REMOVE_FALLBACK;
      return { ok: false, reason: classifyThrown(err) };
    } finally {
      if (isMounted) isRemovingRemote.value = false;
    }
  }

  function dispose(): void {
    isMounted = false;
    pullAbort?.abort();
    pullAbort = null;
    pushAbort?.abort();
    pushAbort = null;
    remoteAbort?.abort();
    remoteAbort = null;
    listAbort?.abort();
    listAbort = null;
    removeAbort?.abort();
    removeAbort = null;
  }

  return {
    isPulling,
    isPushing,
    isSettingRemote,
    isListingRemotes,
    isRemovingRemote,
    pull,
    push,
    setRemoteUrl,
    listRemotes,
    removeRemote,
    dispose,
  };
}
