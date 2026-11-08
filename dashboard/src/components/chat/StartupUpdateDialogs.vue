<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { MarkdownRender } from "markstream-vue";
import { pluginApi, updatesApi } from "@/api/v1";
import { useCommonStore } from "@/stores/common";
import { useAuthStore } from "@/stores/auth";
import { useI18n } from "@/i18n/composables";
import { normalizeStr } from "@/utils/pluginSearch";
import { getDesktopRuntimeInfo } from "@/utils/desktopRuntime";

const store = useCommonStore();
const auth = useAuthStore();
const { locale } = useI18n();
const zh = computed(() => locale.value === "zh-CN");
const coreOpen = ref(false);
const pluginsOpen = ref(false);
const release = ref(null);
const plugins = ref([]);
const selected = ref([]);
const busy = ref(false);
const message = ref("");
const progress = ref(null);
const coreDone = ref(false);
const desktop = ref(false);
const proxy = () =>
  localStorage.getItem("githubProxyRadioValue") === "1"
    ? localStorage.getItem("selectedGitHubProxy") || ""
    : "";

let timer;
let checking = false;
let disposed = false;
let presentedStart = null;

async function checkStartup() {
  if (
    auth.role !== "admin" ||
    checking ||
    coreOpen.value ||
    pluginsOpen.value ||
    busy.value
  )
    return;
  checking = true;
  try {
    const start = await store.fetchStartTime();
    if (
      !start ||
      start === -1 ||
      presentedStart === start ||
      localStorage.getItem("chat-update-start") === String(start)
    )
      return;
    release.value = null;
    plugins.value = [];
    coreDone.value = false;
    progress.value = null;
    message.value = "";
    desktop.value = (await getDesktopRuntimeInfo()).isDesktopRuntime;
    const [coreResult, listResult] = await Promise.allSettled([
      updatesApi.check(),
      pluginApi.list(),
    ]);
    let complete = true;
    if (
      coreResult.status === "fulfilled" &&
      coreResult.value.data.status !== "error"
    ) {
      const result = coreResult.value.data;
      if (result.data?.has_new_version) {
        const match = String(result.message || "").match(
          /\n\nVersion: (.+?) \| Published at: [^\n]*\s*$/,
        );
        if (match)
          release.value = {
            version: match[1],
            body: result.message.slice(0, match.index).trim(),
          };
        else complete = false;
      }
    } else complete = false;
    if (
      listResult.status === "fulfilled" &&
      listResult.value.data.status !== "error" &&
      Array.isArray(listResult.value.data.data)
    ) {
      const candidates = listResult.value.data.data.filter(
        (p) =>
          p.updates_enabled && p.install_source?.install_method === "market",
      );
      const sources = [
        ...new Set(
          candidates.map((p) =>
            String(p.install_source.registry_url || "")
              .trim()
              .replace(/\/+$/, ""),
          ),
        ),
      ];
      for (const source of sources) {
        try {
          const market = await store.getPluginCollections(true, source || null);
          for (const item of candidates.filter(
            (p) =>
              String(p.install_source.registry_url || "")
                .trim()
                .replace(/\/+$/, "") === source,
          )) {
            const info = item.install_source;
            const repo = String(info.repo || "")
              .trim()
              .replace(/\/+$/, "")
              .toLowerCase();
            const found =
              market.find(
                (p) =>
                  info.market_plugin_id &&
                  p.market_plugin_id === info.market_plugin_id,
              ) ||
              market.find(
                (p) =>
                  repo &&
                  String(p.repo || "")
                    .trim()
                    .replace(/\/+$/, "")
                    .toLowerCase() === repo,
              ) ||
              market.find(
                (p) =>
                  normalizeStr(p.name) === normalizeStr(item.marketplace_name),
              );
            const local = String(item.version || "").trim();
            const remote = String(found?.version || "").trim();
            if (!/^v?\d+/.test(local) || !/^v?\d+/.test(remote)) continue;
            const a = local
              .replace(/^v/i, "")
              .split(/[+-]/)[0]
              .split(".")
              .map((n) => parseInt(n, 10) || 0);
            const b = remote
              .replace(/^v/i, "")
              .split(/[+-]/)[0]
              .split(".")
              .map((n) => parseInt(n, 10) || 0);
            let cmp = 0;
            for (let i = 0; i < Math.max(a.length, b.length, 3); i++) {
              if ((a[i] || 0) !== (b[i] || 0)) {
                cmp = (a[i] || 0) - (b[i] || 0);
                break;
              }
            }
            if (
              cmp < 0 ||
              (cmp === 0 && local.includes("-") && !remote.includes("-"))
            ) {
              plugins.value.push({
                ...item,
                onlineVersion: remote,
                error: "",
                updated: false,
              });
            }
          }
        } catch (error) {
          complete = false;
          console.warn("Failed to check plugin updates:", error);
        }
      }
    } else complete = false;
    selected.value = plugins.value.map((p) => p.name);
    if (disposed) return;
    if (complete) localStorage.setItem("chat-update-start", String(start));
    if (release.value || plugins.value.length) presentedStart = start;
    if (release.value) coreOpen.value = true;
    else if (plugins.value.length) pluginsOpen.value = true;
  } catch (error) {
    console.warn("Failed to check startup updates:", error);
  } finally {
    checking = false;
  }
}

onMounted(() => {
  void checkStartup();
  timer = setInterval(checkStartup, 30000);
});
onBeforeUnmount(() => {
  disposed = true;
  clearInterval(timer);
});

function closeCore() {
  coreOpen.value = false;
  message.value = "";
  if (plugins.value.length) pluginsOpen.value = true;
}

async function updateCore() {
  busy.value = true;
  message.value = "";
  const id =
    globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  try {
    const response = await updatesApi.core({
      version: release.value.version,
      reboot: false,
      proxy: proxy(),
      progress_id: id,
    });
    if (response.data.status === "error")
      throw new Error(response.data.message);
    while (true) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const result = await updatesApi.progress(id);
      if (result.data.status === "error") throw new Error(result.data.message);
      const task = result.data.data;
      progress.value = task?.overall_percent ?? 0;
      message.value = task?.message || "";
      if (task?.status === "error") throw new Error(task.message);
      if (task?.status === "success") {
        coreDone.value = true;
        break;
      }
    }
  } catch (error) {
    message.value = error?.message || String(error);
  } finally {
    busy.value = false;
  }
}

async function updatePlugins() {
  busy.value = true;
  for (const item of plugins.value.filter(
    (p) => selected.value.includes(p.name) && !p.updated,
  )) {
    item.error = "";
    try {
      const result = await pluginApi.update(item.name, { proxy: proxy() });
      if (result.data.status === "error") throw new Error(result.data.message);
      item.updated = true;
      selected.value = selected.value.filter((name) => name !== item.name);
    } catch (error) {
      item.error = error?.message || String(error);
    }
  }
  busy.value = false;
}
</script>

<template>
  <v-dialog
    :model-value="coreOpen"
    max-width="680"
    :persistent="busy"
    @update:model-value="!$event && closeCore()"
  >
    <v-card rounded="xl">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6"
        ><v-icon color="primary" class="mr-2">mdi-rocket-launch-outline</v-icon
        >{{
          zh ? "AstrBot 有新版本" : "A new AstrBot version is available"
        }}</v-card-title
      >
      <v-card-text>
        <v-chip color="primary" variant="tonal" class="mb-4">{{
          release?.version
        }}</v-chip>
        <div class="update-notes">
          <MarkdownRender
            :content="
              release?.body || (zh ? '暂无更新日志' : 'No release notes')
            "
            :typewriter="false"
          />
        </div>
        <v-progress-linear
          v-if="busy"
          :model-value="progress"
          color="primary"
          class="mt-4"
        />
        <v-alert
          v-if="message"
          :type="coreDone ? 'success' : busy ? 'info' : 'error'"
          variant="tonal"
          class="mt-4"
          >{{ message }}</v-alert
        >
        <v-alert v-if="desktop" type="info" variant="tonal" class="mt-4">{{
          zh
            ? "桌面版请使用桌面应用的更新功能。"
            : "Use the desktop application to update."
        }}</v-alert>
      </v-card-text>
      <v-card-actions class="px-6 pb-5"
        ><v-spacer /><v-btn
          variant="text"
          :disabled="busy"
          @click="closeCore"
          >{{ zh ? "关闭" : "Close" }}</v-btn
        ><v-btn
          v-if="!desktop && !coreDone"
          color="primary"
          variant="tonal"
          :loading="busy"
          @click="updateCore"
          >{{ zh ? "立即更新" : "Update now" }}</v-btn
        ></v-card-actions
      >
    </v-card>
  </v-dialog>
  <v-dialog v-model="pluginsOpen" max-width="640" :persistent="busy">
    <v-card rounded="xl">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6"
        ><v-icon color="primary" class="mr-2">mdi-puzzle-outline</v-icon
        >{{ zh ? "插件更新" : "Plugin updates" }}</v-card-title
      >
      <v-card-text>
        <p class="text-medium-emphasis mb-3">
          {{
            zh
              ? "选择要更新的插件，将依次安装。"
              : "Select plugins to update. They will be installed one at a time."
          }}
        </p>
        <div class="plugin-list">
          <div v-for="item in plugins" :key="item.name" class="plugin-row">
            <v-checkbox
              v-model="selected"
              :value="item.name"
              :disabled="busy || item.updated"
              hide-details
              density="compact"
            />
            <div class="flex-grow-1">
              <div class="font-weight-medium">
                {{ item.display_name || item.name }}
              </div>
              <div class="text-caption text-medium-emphasis">
                {{ item.version }} → {{ item.onlineVersion }}
              </div>
              <div v-if="item.error" class="text-error text-caption">
                {{ item.error }}
              </div>
            </div>
            <v-icon v-if="item.updated" color="success"
              >mdi-check-circle</v-icon
            >
          </div>
        </div>
      </v-card-text>
      <v-card-actions class="px-6 pb-5"
        ><v-spacer /><v-btn
          variant="text"
          :disabled="busy"
          @click="pluginsOpen = false"
          >{{ zh ? "关闭" : "Close" }}</v-btn
        ><v-btn
          color="primary"
          variant="tonal"
          :disabled="!selected.length"
          :loading="busy"
          @click="updatePlugins"
          >{{
            zh
              ? `更新所选 (${selected.length})`
              : `Update selected (${selected.length})`
          }}</v-btn
        ></v-card-actions
      >
    </v-card>
  </v-dialog>
</template>

<style scoped>
.update-notes,
.plugin-list {
  max-height: 50vh;
  overflow-y: auto;
}
.update-notes {
  padding: 16px;
  border-radius: 12px;
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.plugin-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}
.plugin-row :deep(.v-checkbox) {
  flex: 0 0 auto;
}
</style>
