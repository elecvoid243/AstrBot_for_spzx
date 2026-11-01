// 2026-10-02 (elecvoid243): shared chat config-profile selection state.
// Extracted from ConfigSelector.vue so the "+" menu entry and the
// always-visible composer chip render (and mutate) ONE selection.
//
// 2026-10-23 (elecvoid243): one instance per ChatInput. The main chat and a
// side-chat panel can be mounted together, so each composer binds its own UMO
// instead of fighting over a module-level singleton. ConfigSelector injects
// the instance provided by its owning ChatInput.
import {
  computed,
  inject,
  provide,
  ref,
  watch,
  type InjectionKey,
  type Ref,
} from "vue";
import { chatApi } from "@/api/v1";
import {
  accountSessionRevision,
  assertCurrentAccount,
} from "@/utils/accountSession";
import { useToast } from "@/utils/toast";
import {
  getStoredSelectedChatConfigId,
  setStoredSelectedChatConfigId,
} from "@/utils/chatConfigBinding";

export interface ConfigInfo {
  id: string;
  name: string;
  agent_runner_type?: string;
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
  /** Side chats resolve their own route but must not rewrite the global default. */
  persistGlobal?: boolean;
}

export function createChatConfigSelection() {
  const configOptions = ref<ConfigInfo[]>([]);
  const loadingConfigs = ref(false);
  const selectedConfigId = ref(getStoredSelectedChatConfigId());
  const saving = ref(false);
  const context = ref<ConfigSelectionContext | null>(null);
  const listeners = new Set<(payload: ConfigChangedPayload) => void>();
  let sequence = 0;
  let initialized: Promise<void> | null = null;
  let selectionWrite: Promise<void> = Promise.resolve();
  let bindingReady = false;

  const selectedConfigLabel = computed(
    () =>
      configOptions.value.find((item) => item.id === selectedConfigId.value)
        ?.name || selectedConfigId.value,
  );

  function setSelection(configId: string) {
    selectedConfigId.value = configId;
    const option = configOptions.value.find((item) => item.id === configId);
    listeners.forEach((cb) =>
      cb({
        configId,
        agentRunnerType: option?.agent_runner_type || "local",
      }),
    );
  }

  async function syncSession() {
    const revision = accountSessionRevision.value;
    const current = ++sequence;
    const sessionId = context.value?.sessionId;
    loadingConfigs.value = true;
    saving.value = false;
    selectionWrite = Promise.resolve();
    bindingReady = false;
    selectedConfigId.value = "";
    try {
      const response = await chatApi.listConfigs();
      assertCurrentAccount(revision);
      if (current !== sequence) return;
      configOptions.value = response.data.data?.info_list || [];
      if (!configOptions.value.length)
        throw new Error("Configuration is unavailable");
      let configId = getStoredSelectedChatConfigId();
      if (sessionId) {
        const binding = await chatApi.getSessionConfig(sessionId);
        assertCurrentAccount(revision);
        if (current !== sequence) return;
        if (binding.data.status !== "ok")
          throw new Error(
            binding.data.message || "Configuration is unavailable",
          );
        configId = binding.data.data.config_id;
      }
      if (!configOptions.value.some((item) => item.id === configId)) {
        if (sessionId) throw new Error("Configuration is unavailable");
        configId = "default";
      }
      setSelection(configId);
      bindingReady = true;
      if (context.value?.persistGlobal !== false)
        setStoredSelectedChatConfigId(configId);
    } catch (error) {
      if (revision === accountSessionRevision.value && current === sequence)
        console.error("Failed to load chat configuration:", error);
    } finally {
      if (current === sequence) loadingConfigs.value = false;
    }
  }

  function bindContext(ctx: Ref<ConfigSelectionContext>) {
    watch(
      ctx,
      (value) => {
        context.value = value;
        initialized = syncSession();
      },
      { immediate: true, deep: true, flush: "sync" },
    );
  }

  async function selectConfig(configId: string): Promise<void> {
    if (!configOptions.value.some((item) => item.id === configId)) return;
    const revision = accountSessionRevision.value;
    const current = ++sequence;
    const sessionId = context.value?.sessionId;
    const previous = getStoredSelectedChatConfigId();
    bindingReady = false;
    setSelection(configId);
    if (context.value?.persistGlobal !== false)
      setStoredSelectedChatConfigId(configId);
    saving.value = true;
    selectionWrite = selectionWrite
      .catch(() => {})
      .then(async () => {
        assertCurrentAccount(revision);
        if (sessionId) {
          const response = await chatApi.setSessionConfig(sessionId, configId);
          assertCurrentAccount(revision);
          if (response.data.status !== "ok")
            throw new Error(
              response.data.message || "Configuration is unavailable",
            );
        }
        if (current === sequence) {
          bindingReady = true;
          if (context.value?.persistGlobal !== false)
            setStoredSelectedChatConfigId(configId);
        }
      });
    try {
      await selectionWrite;
    } catch (error) {
      if (revision === accountSessionRevision.value && current === sequence) {
        setSelection("");
        if (context.value?.persistGlobal !== false)
          setStoredSelectedChatConfigId(previous);
        useToast().error("Failed to apply chat configuration");
      }
    } finally {
      if (current === sequence) saving.value = false;
    }
  }

  async function prepareSend(): Promise<string> {
    const revision = accountSessionRevision.value;
    const initialization = initialized;
    await initialization;
    await selectionWrite;
    assertCurrentAccount(revision);
    if (initialization !== initialized) throw new Error("Chat session changed");
    if (!bindingReady) throw new Error("Configuration is unavailable");
    return selectedConfigId.value;
  }

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
    prepareSend,
  };
}

export type ChatConfigSelection = ReturnType<typeof createChatConfigSelection>;

const CHAT_CONFIG_SELECTION_KEY: InjectionKey<ChatConfigSelection> = Symbol(
  "chatConfigSelection",
);
let fallbackSelection: ChatConfigSelection | null = null;
window.addEventListener?.("astrbot:account-changed", () => {
  fallbackSelection = null;
});

/** Provide a per-composer config selection to the ConfigSelector descendants. */
export function provideChatConfigSelection(): ChatConfigSelection {
  const selection = createChatConfigSelection();
  provide(CHAT_CONFIG_SELECTION_KEY, selection);
  return selection;
}

/**
 * Resolve the config selection of the owning ChatInput. Falls back to a
 * process-wide instance when called outside a ChatInput subtree (older
 * consumers / standalone mounts).
 */
export function useChatConfigSelection(): ChatConfigSelection {
  const injected = inject(CHAT_CONFIG_SELECTION_KEY, null);
  if (injected) return injected;
  if (!fallbackSelection) fallbackSelection = createChatConfigSelection();
  return fallbackSelection;
}
