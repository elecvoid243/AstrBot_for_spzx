<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { MarkdownRender } from "markstream-vue";
import { chatApi, pluginApi, updatesApi } from "@/api/v1";
import { useCommonStore } from "@/stores/common";
import { useAuthStore } from "@/stores/auth";
import { useI18n } from "@/i18n/composables";
import { normalizeStr } from "@/utils/pluginSearch";
import { getDesktopRuntimeInfo } from "@/utils/desktopRuntime";

// 2026-11-08 (elecvoid243): dismissals are scoped to one backend process and
// one release, so a restart re-announces the same version while a newer
// release surfaces even if the older one was closed.
const DISMISSED_KEY = "astrbot:update:dismissed";
// Long enough that a burst of clicks or typing never gets a modal on top of
// it, short enough that an idle reader still learns about the update.
const IDLE_MS = 30_000;
// While a chat run is in flight the prompt only re-arms; it must never
// interrupt the task the user is waiting on.
const ACTIVE_RUN_RECHECK_MS = 15_000;
// Release checks hit GitHub and the plugin market, so a running instance
// refreshes at this cadence instead of on every start_time tick.
const CHECK_INTERVAL_MS = 10 * 60_000;
// A reboot:true update replaces the process; the page reloads once the new
// process reports a different start_time. Allow a cold start this long.
const RESTART_TIMEOUT_MS = 90_000;
const ACTIVITY_EVENTS = [
  "pointerdown",
  "pointermove",
  "keydown",
  "wheel",
  "touchstart",
];
const ACTIVITY_OPTIONS = { passive: true, capture: true };

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
const restarting = ref(false);
const desktop = ref(false);
const runtime = ref(null);
const dismissed = ref({ core: "", plugins: {} });
const proxy = () =>
  localStorage.getItem("githubProxyRadioValue") === "1"
    ? localStorage.getItem("selectedGitHubProxy") || ""
    : "";

let timer;
let openTimer;
let checking = false;
let disposed = false;
let checkedStart = null;
let lastCheckAt = 0;
let lastActivity = Date.now();
// The first dashboard entry after a backend start announces immediately
// instead of waiting for the idle window. Consumed by the first completed
// check so a later release still respects the idle gate.
let initialEntryPending = true;
let promptImmediately = false;

function loadDismissed(start) {
  try {
    const saved = JSON.parse(localStorage.getItem(DISMISSED_KEY) || "null");
    if (saved && String(saved.runtime) === String(start))
      return { core: String(saved.core || ""), plugins: saved.plugins || {} };
  } catch {
    // Corrupted entry: behave as if nothing was dismissed.
  }
  return { core: "", plugins: {} };
}

function rememberDismissed() {
  if (runtime.value === null) return;
  localStorage.setItem(
    DISMISSED_KEY,
    JSON.stringify({
      runtime: String(runtime.value),
      core: dismissed.value.core,
      plugins: dismissed.value.plugins,
    }),
  );
}

async function tryOpen() {
  if (disposed || busy.value || coreOpen.value || pluginsOpen.value) return;
  if (!release.value && !plugins.value.length) return;
  if (!promptImmediately && Date.now() - lastActivity < IDLE_MS)
    return scheduleOpen();
  // A hidden tab is not "idle but present": wait for visibilitychange so the
  // modal does not ambush the user when they come back.
  if (document.visibilityState !== "visible") return;
  try {
    const result = await chatApi.activeRuns();
    if (result.data?.data?.running) return scheduleOpen(ACTIVE_RUN_RECHECK_MS);
  } catch (error) {
    console.warn("Failed to check active chat runs:", error);
    return scheduleOpen(ACTIVE_RUN_RECHECK_MS);
  }
  if (disposed || busy.value || coreOpen.value || pluginsOpen.value) return;
  if (!promptImmediately && Date.now() - lastActivity < IDLE_MS)
    return scheduleOpen();
  promptImmediately = false;
  if (release.value) coreOpen.value = true;
  else if (plugins.value.length) pluginsOpen.value = true;
}

function scheduleOpen(delay = 0) {
  clearTimeout(openTimer);
  openTimer = null;
  if (disposed || (!release.value && !plugins.value.length)) return;
  const wait = promptImmediately
    ? delay
    : Math.max(delay, IDLE_MS - (Date.now() - lastActivity));
  openTimer = setTimeout(() => {
    openTimer = null;
    void tryOpen();
  }, wait);
}

function markActivity() {
  lastActivity = Date.now();
  if (openTimer) scheduleOpen();
}

function onVisibilityChange() {
  if (document.visibilityState === "visible") scheduleOpen();
}

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
    if (!start || start === -1) return;
    // The 30s tick exists to notice a backend restart; full release checks
    // are throttled because they call GitHub and the plugin market.
    if (start === checkedStart && Date.now() - lastCheckAt < CHECK_INTERVAL_MS)
      return;
    lastCheckAt = Date.now();
    runtime.value = start;
    dismissed.value = loadDismissed(start);
    desktop.value = (await getDesktopRuntimeInfo()).isDesktopRuntime;
    const [coreResult, listResult] = await Promise.allSettled([
      updatesApi.check(),
      pluginApi.list(),
    ]);
    let nextRelease = null;
    const nextPlugins = [];
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
        if (match && dismissed.value.core !== match[1])
          nextRelease = {
            version: match[1],
            body: result.message.slice(0, match.index).trim(),
          };
        else if (!match) complete = false;
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
              (cmp < 0 ||
                (cmp === 0 && local.includes("-") && !remote.includes("-"))) &&
              dismissed.value.plugins[item.name] !== remote
            ) {
              nextPlugins.push({
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
    if (disposed) return;
    if (complete || nextRelease || nextPlugins.length) checkedStart = start;
    // First entry into the dashboard for this backend process: skip the idle
    // wait. A dismissal stored for this runtime means the prompt already
    // surfaced once, so the usual idle gate applies instead.
    const alreadyPrompted =
      !!dismissed.value.core || Object.keys(dismissed.value.plugins).length > 0;
    promptImmediately =
      initialEntryPending &&
      !alreadyPrompted &&
      !!(nextRelease || nextPlugins.length);
    initialEntryPending = false;
    release.value = nextRelease;
    plugins.value = nextPlugins;
    selected.value = nextPlugins.map((p) => p.name);
    coreDone.value = false;
    progress.value = null;
    message.value = "";
    scheduleOpen();
  } catch (error) {
    console.warn("Failed to check startup updates:", error);
  } finally {
    checking = false;
  }
}

onMounted(() => {
  for (const type of ACTIVITY_EVENTS)
    window.addEventListener(type, markActivity, ACTIVITY_OPTIONS);
  document.addEventListener("visibilitychange", onVisibilityChange);
  void checkStartup();
  timer = setInterval(checkStartup, 30000);
});
onBeforeUnmount(() => {
  disposed = true;
  clearInterval(timer);
  clearTimeout(openTimer);
  for (const type of ACTIVITY_EVENTS)
    window.removeEventListener(type, markActivity, ACTIVITY_OPTIONS);
  document.removeEventListener("visibilitychange", onVisibilityChange);
});

function closeCore() {
  if (release.value) {
    dismissed.value.core = release.value.version;
    rememberDismissed();
  }
  coreOpen.value = false;
  message.value = "";
  // Drop the pending candidate so focus/visibility changes cannot re-arm a
  // prompt the user just closed.
  release.value = null;
  if (plugins.value.length) pluginsOpen.value = true;
}

function closePlugins() {
  for (const item of plugins.value) {
    dismissed.value.plugins[item.name] = item.onlineVersion;
  }
  rememberDismissed();
  pluginsOpen.value = false;
  plugins.value = [];
  selected.value = [];
}

// Poll the new process until it reports a different start_time, then reload so
// the freshly written dashboard assets are actually served.
async function waitForRestart(initialStartTime) {
  restarting.value = true;
  message.value = zh.value
    ? "更新完成，正在重启 AstrBot..."
    : "Update applied. Restarting AstrBot...";
  const deadline = Date.now() + RESTART_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    try {
      const next = await store.fetchStartTime();
      if (next && next !== -1 && String(next) !== String(initialStartTime)) {
        const url = new URL(window.location.href);
        url.searchParams.set("_r", Date.now().toString());
        window.location.replace(url.toString());
        return;
      }
    } catch (_error) {
      // The backend is still shutting down or booting; keep waiting.
    }
  }
  // Nothing left to do automatically: hand the restart back to the user.
  coreDone.value = false;
  throw new Error(
    zh.value
      ? "更新已完成，但后端未在预期时间内重启，请手动重启 AstrBot。"
      : "The update was applied, but the backend did not restart in time. Restart AstrBot manually.",
  );
}

async function updateCore() {
  busy.value = true;
  restarting.value = false;
  message.value = "";
  const initialStartTime = runtime.value;
  const id =
    globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  try {
    const response = await updatesApi.core({
      version: release.value.version,
      // Apply the new files immediately instead of asking for a manual restart.
      reboot: true,
      proxy: proxy(),
      progress_id: id,
    });
    if (response.data.status === "error")
      throw new Error(response.data.message);
    let sawProgress = false;
    let waitForReboot = false;
    while (true) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      let result;
      try {
        result = await updatesApi.progress(id);
      } catch (error) {
        // The backend drops HTTP while it replaces the process.
        if (!sawProgress) throw error;
        waitForReboot = true;
        break;
      }
      if (result.data.status === "error") throw new Error(result.data.message);
      const task = result.data.data;
      // A backend restart drops the progress entry, so polling would answer
      // "idle" forever and `busy` would never clear — leaving the dialog with
      // a permanently disabled close button.
      if (task?.status === "idle" && sawProgress) {
        waitForReboot = true;
        break;
      }
      sawProgress = true;
      progress.value = task?.overall_percent ?? 0;
      message.value = task?.message || "";
      if (task?.status === "error") throw new Error(task.message);
      if (task?.stage === "restart") {
        waitForReboot = true;
        break;
      }
      if (task?.status === "success") {
        // The new files are on disk but the process still reports the old
        // version until it restarts; remember the dismissal so a re-check
        // while it is still running does not offer the same release again.
        if (release.value) {
          dismissed.value.core = release.value.version;
          rememberDismissed();
        }
        waitForReboot = true;
        break;
      }
    }
    if (waitForReboot) {
      coreDone.value = true;
      await waitForRestart(initialStartTime);
    }
  } catch (error) {
    message.value = error?.message || String(error);
  } finally {
    busy.value = false;
    restarting.value = false;
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
      dismissed.value.plugins[item.name] = item.onlineVersion;
      rememberDismissed();
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
    persistent
    scrollable
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
          v-if="restarting"
          indeterminate
          color="primary"
          class="mt-4"
        />
        <v-progress-linear
          v-else-if="busy"
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
  <v-dialog
    :model-value="pluginsOpen"
    max-width="640"
    persistent
    scrollable
  >
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
        <div>
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
          @click="closePlugins"
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
