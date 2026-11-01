import { ref, watch, type Ref } from "vue";
import { accountSessionRevision } from "@/utils/accountSession";

function storageKey(key: string): string {
  const accountId = localStorage.getItem("account_id");
  return accountId ? `${key}:${accountId}` : key;
}

const SELECTED_PROVIDER_KEY = "selectedProvider";
const SELECTED_PROVIDER_MODEL_KEY = "selectedProviderModel";
const SESSION_PROVIDER_STORAGE_PREFIX = "chat.sessionProvider.";

// Module-level state so every ProviderModelMenu instance (header, input)
// shares the same selection.
const selectedProviderId = ref("");
const selectedModelName = ref("");
let syncedFromStorage = false;
window.addEventListener?.("astrbot:account-changed", () => {
  syncedFromStorage = false;
  selectedProviderId.value = "";
  selectedModelName.value = "";
});

function syncFromStorage() {
  if (syncedFromStorage || typeof window === "undefined") return;
  syncedFromStorage = true;
  try {
    selectedProviderId.value =
      localStorage.getItem(storageKey(SELECTED_PROVIDER_KEY)) || "";
    selectedModelName.value =
      localStorage.getItem(storageKey(SELECTED_PROVIDER_MODEL_KEY)) || "";
  } catch {
    // Storage restrictions must not prevent opening the chat.
  }
}

/**
 * Share model selection, optionally remembering it for the active chat.
 *
 * Args:
 *   sessionId: Active session ref owned by Chat.vue. Other consumers omit it.
 *
 * Returns:
 *   Shared model refs, selection setter, and explicit session persistence.
 */
export function useProviderModelSelection(sessionId?: Ref<string>) {
  syncFromStorage();
  const revision = accountSessionRevision.value;

  function setSelection(providerId: string, modelName = "") {
    selectedProviderId.value = providerId;
    selectedModelName.value = modelName;
    try {
      localStorage.setItem(storageKey(SELECTED_PROVIDER_KEY), providerId);
      localStorage.setItem(storageKey(SELECTED_PROVIDER_MODEL_KEY), modelName);
    } catch {
      // Selection persistence must not block the UI.
    }
  }

  function writeSessionProviderSelection(
    targetSessionId: string,
    selection: { providerId?: string; modelName?: string } | null,
  ) {
    if (
      revision !== accountSessionRevision.value ||
      !targetSessionId ||
      !selection?.providerId
    )
      return;
    try {
      localStorage.setItem(
        storageKey(SESSION_PROVIDER_STORAGE_PREFIX + targetSessionId),
        JSON.stringify({
          providerId: selection.providerId,
          modelName: selection.modelName || "",
        }),
      );
    } catch {
      // Session model persistence must not block chat flows.
    }
  }

  if (sessionId) {
    watch(
      sessionId,
      (id) => {
        if (!id) return;
        try {
          const raw = localStorage.getItem(
            storageKey(SESSION_PROVIDER_STORAGE_PREFIX + id),
          );
          if (!raw) return;
          const parsed = JSON.parse(raw);
          if (
            parsed &&
            typeof parsed.providerId === "string" &&
            parsed.providerId
          ) {
            setSelection(
              parsed.providerId,
              typeof parsed.modelName === "string" ? parsed.modelName : "",
            );
          }
        } catch {
          // Ignore corrupted entries and keep the current selection.
        }
      },
      // Restore before async message loading or a same-tick send can run.
      { immediate: true, flush: "sync" },
    );
    watch(
      [selectedProviderId, selectedModelName],
      ([providerId, modelName]) => {
        writeSessionProviderSelection(sessionId.value, {
          providerId,
          modelName,
        });
      },
      // Attribute a choice to its session before another switch can occur.
      { flush: "sync" },
    );
  }

  return {
    selectedProviderId,
    selectedModelName,
    setSelection,
    writeSessionProviderSelection,
  };
}
