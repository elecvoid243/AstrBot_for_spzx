import { onScopeDispose, ref, watch, type Ref } from "vue";
import { chatApi } from "@/api/v1";
import {
  accountSessionRevision,
  assertCurrentAccount,
} from "@/utils/accountSession";
import { useToast } from "@/utils/toast";

// The header and ordinary composer share the active session's server state.
const selectedProviderId = ref("");
const selectedModelName = ref("");
const thinkingEffort = ref<string | null>(null);
const effortPreset = ref<string | null>(null);
const loading = ref(false);
const defaultThinkingEffort = ref("auto");
const defaultEffortPreset = ref("");
let activeSession = "";
let generation = 0;
let mutation = 0;
let ready = true;
let initialized: Promise<void> = Promise.resolve();
let writes: Promise<void> = Promise.resolve();

window.addEventListener?.("astrbot:account-changed", () => {
  generation++;
  activeSession = "";
  ready = false;
  selectedProviderId.value = "";
  selectedModelName.value = "";
  thinkingEffort.value = null;
  effortPreset.value = null;
  defaultThinkingEffort.value = "auto";
  defaultEffortPreset.value = "";
});

/** Share server-owned ordinary-session model choices with both selectors.
 *
 * Args:
 *   sessionId: Active ordinary session, bound once by Chat.vue.
 *
 * Returns:
 *   Shared choices, explicit setters and a send guard that awaits persistence.
 */
export function useProviderModelSelection(sessionId?: Ref<string>) {
  if (sessionId) {
    watch(
      sessionId,
      (id) => {
        const current = ++generation;
        const revision = accountSessionRevision.value;
        activeSession = id;
        ready = !id;
        loading.value = Boolean(id);
        selectedProviderId.value = "";
        selectedModelName.value = "";
        thinkingEffort.value = null;
        effortPreset.value = null;
        defaultThinkingEffort.value = "auto";
        defaultEffortPreset.value = "";
        initialized = (async () => {
          if (!id) return;
          try {
            // A quick switch back must read after any captured session write.
            await writes;
            assertCurrentAccount(revision);
            if (current !== generation) return;
            const response = await chatApi.getSessionSelection(id);
            assertCurrentAccount(revision);
            if (current !== generation) return;
            if (response.data.status !== "ok")
              throw new Error(
                response.data.message || "Settings are unavailable",
              );
            const selection = response.data.data;
            selectedProviderId.value = selection.selected_provider || "";
            selectedModelName.value = selection.selected_model || "";
            thinkingEffort.value = selection.thinking_effort;
            effortPreset.value = selection.effort_preset;
            defaultThinkingEffort.value =
              selection.default_thinking_effort || "auto";
            defaultEffortPreset.value = selection.default_effort_preset || "";
            ready = true;
          } catch (error: any) {
            if (
              current === generation &&
              revision === accountSessionRevision.value
            )
              useToast().error(
                `Failed to load session model settings: ${error.message}`,
              );
          } finally {
            if (current === generation) loading.value = false;
          }
        })();
      },
      { immediate: true, flush: "sync" },
    );
    onScopeDispose(() => {
      generation++;
      activeSession = "";
      ready = false;
    });
  }

  async function saveSelection(update: {
    providerId?: string;
    modelName?: string;
    effort?: string;
    preset?: string;
  }) {
    const target = activeSession;
    const current = generation;
    const revision = accountSessionRevision.value;
    await initialized;
    if (current !== generation || revision !== accountSessionRevision.value)
      return;
    const previous = {
      selected_provider: selectedProviderId.value || null,
      selected_model: selectedModelName.value || null,
      thinking_effort: thinkingEffort.value,
      effort_preset: effortPreset.value,
    };
    if (update.providerId !== undefined) {
      selectedProviderId.value = update.providerId;
      selectedModelName.value = update.modelName || "";
    }
    if (update.effort !== undefined) thinkingEffort.value = update.effort;
    if (update.preset !== undefined) effortPreset.value = update.preset;
    if (!target) return;
    const selection = {
      selected_provider: selectedProviderId.value || null,
      selected_model: selectedModelName.value || null,
      thinking_effort: thinkingEffort.value,
      effort_preset: effortPreset.value,
    };
    const change = ++mutation;
    ready = false;
    const write = writes.then(async () => {
      assertCurrentAccount(revision);
      const response = await chatApi.setSessionSelection(target, selection);
      assertCurrentAccount(revision);
      if (response.data.status !== "ok")
        throw new Error(response.data.message || "Settings are unavailable");
      if (current === generation && change === mutation) ready = true;
    });
    writes = write.catch((error: any) => {
      if (
        current === generation &&
        change === mutation &&
        revision === accountSessionRevision.value
      ) {
        selectedProviderId.value = previous.selected_provider || "";
        selectedModelName.value = previous.selected_model || "";
        thinkingEffort.value = previous.thinking_effort;
        effortPreset.value = previous.effort_preset;
        ready = false;
        useToast().error(
          `Failed to save session model settings: ${error.message}`,
        );
      }
    });
    await writes;
  }

  async function prepareSend() {
    const current = generation;
    const revision = accountSessionRevision.value;
    await initialized;
    // Setters resume after initialization and append their writes first.
    await Promise.resolve();
    await writes;
    assertCurrentAccount(revision);
    if (current !== generation) throw new Error("Chat session changed");
    if (!ready) throw new Error("Session model settings are unavailable");
  }

  return {
    selectedProviderId,
    selectedModelName,
    thinkingEffort,
    effortPreset,
    defaultThinkingEffort,
    defaultEffortPreset,
    loading,
    setSelection: (providerId: string, modelName = "") =>
      saveSelection({ providerId, modelName }),
    setThinkingEffort: (effort: string, preset: string) =>
      saveSelection({ effort, preset }),
    prepareSend,
  };
}
