<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { useI18n } from "@/i18n/composables";
import { pluginExtensionApi } from "@/api/v1";

const props = defineProps<{ chatId?: string | null; visible: boolean }>();
const { t } = useI18n();
const key = "features.chat.agentBrowser";

interface BrowserState {
  available: boolean;
  generation?: string;
  browser_session_id?: string;
  tab_id?: string;
  sequence?: number;
  url?: string;
  title?: string;
  width?: number;
  height?: number;
  image?: string;
  mime_type?: string;
}

const state = ref<BrowserState | null>(null);
const error = ref("");
const loading = ref(false);
let pending: AbortController | null = null;

async function refresh(): Promise<void> {
  pending?.abort();
  state.value = null;
  error.value = "";
  if (!props.chatId || !props.visible) return;
  const controller = new AbortController();
  pending = controller;
  loading.value = true;
  const chatId = props.chatId;
  try {
    const initial = await pluginExtensionApi.get<BrowserState>(
      "browser-use/status",
      {
        params: { chat_id: chatId },
        signal: controller.signal,
      },
    );
    if (controller.signal.aborted) return;
    if (initial.data.status !== "ok")
      throw new Error(initial.data.message || undefined);
    const current = initial.data.data;
    if (!current.available) {
      state.value = current;
      return;
    }
    const snapshot = await pluginExtensionApi.get<BrowserState>(
      "browser-use/snapshot",
      {
        params: {
          chat_id: chatId,
          generation: current.generation,
          browser_session_id: current.browser_session_id,
        },
        signal: controller.signal,
      },
    );
    if (controller.signal.aborted) return;
    if (snapshot.data.status !== "ok")
      throw new Error(snapshot.data.message || undefined);
    if (
      snapshot.data.data.generation !== current.generation ||
      snapshot.data.data.browser_session_id !== current.browser_session_id
    ) {
      throw new Error(t(`${key}.expired`));
    }
    state.value = snapshot.data.data;
  } catch (err: any) {
    if (!controller.signal.aborted) {
      error.value =
        err.response?.data?.message || err.message || t(`${key}.unavailable`);
    }
  } finally {
    if (pending === controller) loading.value = false;
  }
}

watch(
  () => [props.chatId, props.visible],
  () => {
    void refresh();
  },
  { immediate: true },
);
onBeforeUnmount(() => pending?.abort());
</script>

<template>
  <section class="agent-browser-view" :aria-label="t(`${key}.title`)">
    <div class="agent-browser-toolbar">
      <div class="agent-browser-page">
        <strong>{{ state?.title || t(`${key}.title`) }}</strong>
        <div class="agent-browser-url">{{ state?.url }}</div>
      </div>
      <v-btn
        variant="text"
        size="small"
        :loading="loading"
        :disabled="!chatId"
        @click="refresh"
      >
        {{ t(`${key}.refresh`) }}
      </v-btn>
    </div>
    <div v-if="error" role="alert" class="pa-4">{{ error }}</div>
    <div v-else-if="!state?.image" role="status" class="pa-4">
      {{ loading ? t(`${key}.loading`) : t(`${key}.empty`) }}
    </div>
    <div v-else class="agent-browser-image">
      <img
        :src="`data:${state.mime_type};base64,${state.image}`"
        :width="state.width"
        :height="state.height"
        :alt="state.title || t(`${key}.title`)"
      />
    </div>
  </section>
</template>

<style scoped>
.agent-browser-view {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.agent-browser-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12);
}
.agent-browser-page {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}
.agent-browser-url {
  font-size: 12px;
  overflow-wrap: anywhere;
}
.agent-browser-image {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.agent-browser-image img {
  display: block;
  width: 100%;
  height: auto;
  pointer-events: none;
}
</style>
