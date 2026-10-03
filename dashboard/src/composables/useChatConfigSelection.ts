// 2026-10-02 (elecvoid243): shared chat config-profile selection state.
// Extracted from ConfigSelector.vue so the "+" menu entry and the
// always-visible composer chip render (and mutate) ONE selection — the
// chip label is therefore always the live current config profile.
import { computed, ref, watch, type Ref } from "vue";
import { configProfileApi, configRouteApi } from "@/api/v1";
import { useToast } from "@/utils/toast";
import {
  getStoredDashboardUsername,
  getStoredSelectedChatConfigId,
  setStoredSelectedChatConfigId,
} from "@/utils/chatConfigBinding";

export interface ConfigInfo {
  id: string;
  name: string;
}

export interface ConfigChangedPayload {
  configId: string;
  agentRunnerType: string;
}

export interface ConfigSelectionContext {
  sessionId: string | null;
  platformId: string;
  isGroup: boolean;
  initialConfigId?: string | null;
}

// Module-level singletons: every consumer (menu item, composer chip, the
// ChatInput context binding) shares this exact state.
const configOptions = ref<ConfigInfo[]>([]);
const loadingConfigs = ref(false);
const selectedConfigId = ref("default");
const agentRunnerType = ref("local");
const saving = ref(false);
const pendingSync = ref(false);
const routingEntries = ref<Array<{ pattern: string; confId: string }>>([]);
const configCache: Record<string, string> = {};
const context = ref<ConfigSelectionContext | null>(null);

const listeners = new Set<(payload: ConfigChangedPayload) => void>();

const selectedConfigLabel = computed(() => {
  const target = configOptions.value.find(
    (item) => item.id === selectedConfigId.value,
  );
  return target?.name || selectedConfigId.value || "default";
});

const sessionKey = computed(() => {
  const ctx = context.value;
  const id = ctx?.sessionId?.trim();
  if (!ctx || !id) return null;
  return `${ctx.platformId}!${getStoredDashboardUsername()}!${id}`;
});

const targetUmo = computed(() => {
  const ctx = context.value;
  if (!ctx || !sessionKey.value) return null;
  const messageType = ctx.isGroup ? "GroupMessage" : "FriendMessage";
  return `${ctx.platformId}:${messageType}:${sessionKey.value}`;
});

async function fetchConfigList() {
  loadingConfigs.value = true;
  try {
    const res = await configProfileApi.list();
    configOptions.value = (res.data.data?.info_list || []).map(
      (item: any) => ({
        id: String(item.id || ""),
        name: String(item.name || item.id || "default"),
      }),
    );
  } catch (error) {
    console.error("加载配置文件列表失败", error);
    configOptions.value = [];
  } finally {
    loadingConfigs.value = false;
  }
}

async function fetchRoutingEntries() {
  try {
    const res = await configRouteApi.list();
    const routing = res.data.data?.routing || {};
    routingEntries.value = Object.entries(routing).map(([pattern, confId]) => ({
      pattern,
      confId: confId as string,
    }));
  } catch (error) {
    console.error("获取配置路由失败", error);
    routingEntries.value = [];
  }
}

function matchesPattern(pattern: string, target: string): boolean {
  const parts = pattern.split(":");
  const targetParts = target.split(":");
  if (parts.length !== 3 || targetParts.length !== 3) {
    return false;
  }
  return parts.every(
    (part, index) => part === "" || part === "*" || part === targetParts[index],
  );
}

function resolveConfigId(umo: string | null): string {
  if (!umo) {
    return "default";
  }
  for (const entry of routingEntries.value) {
    if (matchesPattern(entry.pattern, umo)) {
      return entry.confId;
    }
  }
  return "default";
}

async function getAgentRunnerType(confId: string): Promise<string> {
  if (configCache[confId]) {
    return configCache[confId];
  }
  try {
    const res = await configProfileApi.get(confId);
    const config = ((res.data.data as any).config || {}) as any;
    const type = config?.agent_runner?.runner_type || "local";
    configCache[confId] = type;
    return type;
  } catch (error) {
    console.error("获取配置文件详情失败", error);
    return "local";
  }
}

function notifyChanged() {
  const payload: ConfigChangedPayload = {
    configId: selectedConfigId.value,
    agentRunnerType: agentRunnerType.value,
  };
  listeners.forEach((cb) => cb(payload));
}

async function setSelection(confId: string) {
  const normalized = confId || "default";
  selectedConfigId.value = normalized;
  agentRunnerType.value = await getAgentRunnerType(normalized);
  notifyChanged();
}

async function applySelectionToBackend(confId: string): Promise<boolean> {
  if (!targetUmo.value) {
    // No active session yet: remember to push the selection once one exists.
    pendingSync.value = true;
    return true;
  }
  saving.value = true;
  try {
    await configRouteApi.upsert(targetUmo.value, { config_id: confId });
    const filtered = routingEntries.value.filter(
      (entry) => entry.pattern !== targetUmo.value,
    );
    if (confId !== "default") {
      filtered.push({ pattern: targetUmo.value, confId });
    }
    routingEntries.value = filtered;
    return true;
  } catch (error) {
    const err = error as any;
    console.error("更新配置文件失败", err);
    useToast().error(err?.response?.data?.message || "配置文件应用失败");
    return false;
  } finally {
    saving.value = false;
  }
}

async function syncSelectionForSession() {
  if (!targetUmo.value) {
    pendingSync.value = true;
    return;
  }
  if (pendingSync.value) {
    pendingSync.value = false;
    await applySelectionToBackend(selectedConfigId.value);
    return;
  }
  await fetchRoutingEntries();
  const resolved = resolveConfigId(targetUmo.value);
  await setSelection(resolved);
  setStoredSelectedChatConfigId(resolved);
}

let initPromise: Promise<void> | null = null;

function ensureInitialized(): Promise<void> {
  if (!initPromise) {
    initPromise = (async () => {
      await fetchConfigList();
      const stored =
        context.value?.initialConfigId || getStoredSelectedChatConfigId();
      selectedConfigId.value = stored;
      await setSelection(stored);
      await syncSelectionForSession();
    })();
  }
  return initPromise;
}

let lastContextKey = "";

function contextKey(value: ConfigSelectionContext | null): string {
  return value ? `${value.sessionId}|${value.platformId}|${value.isGroup}` : "";
}

export function useChatConfigSelection() {
  /**
   * Bind the session context (ChatInput owns this). The first bind performs
   * one-time initialization; later context changes re-resolve the config
   * profile from the routing table.
   */
  function bindContext(ctx: Ref<ConfigSelectionContext>) {
    watch(
      ctx,
      (value) => {
        context.value = value;
        const firstBind = !initPromise;
        // Dedupe: multiple mounted ChatInputs bind the same identity.
        if (!firstBind && contextKey(value) === lastContextKey) return;
        lastContextKey = contextKey(value);
        if (firstBind) {
          // ensureInitialized() already runs the initial session sync.
          void ensureInitialized();
        } else {
          void ensureInitialized().then(() => syncSelectionForSession());
        }
      },
      { immediate: true },
    );
  }

  /** Apply a config profile immediately (chip one-click / dialog confirm). */
  async function selectConfig(confId: string): Promise<void> {
    if (!confId) return;
    const previousId = selectedConfigId.value;
    await setSelection(confId);
    setStoredSelectedChatConfigId(confId);
    const applied = await applySelectionToBackend(confId);
    if (!applied) {
      setStoredSelectedChatConfigId(previousId);
      await setSelection(previousId);
    }
  }

  /** Subscribe to selection changes; returns an unsubscribe function. */
  function onConfigChanged(
    cb: (payload: ConfigChangedPayload) => void,
  ): () => void {
    listeners.add(cb);
    return () => listeners.delete(cb);
  }

  return {
    configOptions,
    loadingConfigs,
    selectedConfigId,
    selectedConfigLabel,
    saving,
    bindContext,
    selectConfig,
    onConfigChanged,
  };
}
