<!--
  Author: impl_task10
  Date: 2026-11-16

  Developer-mode snapshot inspector shell. Chat.vue owns the settings write
  and the store sync; this panel only reads the current session's snapshots
  and emits `toggle` when the header switch is flipped. The three empty
  states (disabled / empty / expired) are deliberately distinct, and the
  detail hand-off keeps the previous chat step so LlmSnapshotDetail can show
  a diff.
-->
<template>
  <transition name="slide-left">
    <aside
      v-if="modelValue"
      ref="panelRef"
      class="devmode-inspector"
      :style="panelWidth !== null ? { width: `${panelWidth}px` } : undefined"
    >
      <div class="inspector-resizer" @mousedown="startResize" />

      <header class="inspector-head">
        <div class="inspector-heading">
          <div class="inspector-title">{{ tm("devMode.title") }}</div>
          <div class="inspector-subtitle">{{ subtitle }}</div>
        </div>
        <div class="inspector-actions">
          <label v-if="canEdit" class="inspector-switch">
            <v-switch
              class="devmode-switch"
              :model-value="enabled"
              density="compact"
              hide-details
              color="primary"
              @update:model-value="emit('toggle')"
            />
            <span class="inspector-switch-label mono">{{
              tm("devMode.settings.switch")
            }}</span>
          </label>
          <button
            type="button"
            class="inspector-action inspector-refresh"
            :disabled="!enabled"
            @click="handleRefresh"
          >
            {{ tm("devMode.action.refresh") }}
          </button>
          <button
            type="button"
            class="inspector-action inspector-close"
            @click="close"
          >
            {{ tm("devMode.action.close") }}
          </button>
        </div>
      </header>

      <div class="inspector-settings">
        <span class="inspector-notice">{{ tm("devMode.notice.memory") }}</span>
        <span v-if="limitsCopy" class="inspector-limits mono">{{
          limitsCopy
        }}</span>
      </div>

      <div v-if="error" class="inspector-error">
        {{ tm("devMode.error", { message: error ?? "" }) }}
      </div>

      <div v-if="!enabled" class="inspector-state state-disabled">
        <p>{{ tm("devMode.state.disabled") }}</p>
      </div>
      <div v-else-if="expired" class="inspector-state state-expired">
        <p>{{ tm("devMode.state.expired") }}</p>
      </div>
      <div v-else-if="!items.length" class="inspector-state state-empty">
        <p>{{ tm("devMode.state.empty") }}</p>
      </div>
      <div v-else class="inspector-body">
        <nav class="inspector-rail">
          <div v-if="chatItems.length" class="rail-group">
            <div class="rail-group-title mono">
              {{ tm("devMode.group.turn") }}
            </div>
            <button
              v-for="item in chatItems"
              :key="item.snapshot_id"
              type="button"
              class="rail-item"
              :class="{ 'is-active': selectedId === item.snapshot_id }"
              :aria-current="
                selectedId === item.snapshot_id ? 'true' : undefined
              "
              @click="selectSnapshot(item.snapshot_id)"
            >
              <span class="rail-line">
                <span class="rail-kind">{{ kindLabel(item) }}</span>
                <span v-if="item.step != null" class="rail-step mono"
                  >step {{ item.step }}</span
                >
                <span class="rail-status mono">{{ item.status }}</span>
              </span>
              <span class="rail-line rail-sub">
                <span class="rail-model mono">{{ modelOf(item) }}</span>
                <span class="rail-duration mono">{{
                  formatDuration(item.duration_ms)
                }}</span>
              </span>
              <span v-if="badges(item).length" class="rail-badges">
                <span
                  v-for="badge in badges(item)"
                  :key="badge.text"
                  class="rail-badge mono"
                  :class="badge.tone ? `is-${badge.tone}` : undefined"
                  >{{ badge.text }}</span
                >
              </span>
            </button>
          </div>

          <div v-if="auxItems.length" class="rail-group">
            <div class="rail-group-title mono">
              {{ tm("devMode.group.aux") }}
            </div>
            <button
              v-for="item in auxItems"
              :key="item.snapshot_id"
              type="button"
              class="rail-item"
              :class="{ 'is-active': selectedId === item.snapshot_id }"
              :aria-current="
                selectedId === item.snapshot_id ? 'true' : undefined
              "
              @click="selectSnapshot(item.snapshot_id)"
            >
              <span class="rail-line">
                <span class="rail-kind">{{ kindLabel(item) }}</span>
                <span v-if="item.step != null" class="rail-step mono"
                  >step {{ item.step }}</span
                >
                <span class="rail-status mono">{{ item.status }}</span>
              </span>
              <span class="rail-line rail-sub">
                <span class="rail-model mono">{{ modelOf(item) }}</span>
                <span class="rail-duration mono">{{
                  formatDuration(item.duration_ms)
                }}</span>
              </span>
              <span v-if="badges(item).length" class="rail-badges">
                <span
                  v-for="badge in badges(item)"
                  :key="badge.text"
                  class="rail-badge mono"
                  :class="badge.tone ? `is-${badge.tone}` : undefined"
                  >{{ badge.text }}</span
                >
              </span>
            </button>
          </div>
        </nav>

        <div class="inspector-detail">
          <LlmSnapshotDetailView
            v-if="selectedDetail"
            :snapshot="selectedDetail"
            :previous="previousDetail ?? undefined"
          />
          <div v-else-if="detailLoading" class="detail-loading mono">…</div>
          <div v-else class="detail-empty">
            {{ tm("devMode.state.select") }}
          </div>
        </div>
      </div>
    </aside>
  </transition>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import LlmSnapshotDetailView from "@/components/chat/LlmSnapshotDetail.vue";
import { useModuleI18n } from "@/i18n/composables";
import { useLlmSnapshots } from "@/composables/useLlmSnapshots";
import type {
  LlmRequestSnapshotEnvelope,
  LlmSnapshotDetail,
} from "@/composables/llmRequestSnapshots";

const props = defineProps<{
  modelValue: boolean;
  sessionId: string;
  enabled: boolean;
  canEdit: boolean;
  initialTurnId?: string;
}>();

const emit = defineEmits<{
  (event: "update:modelValue", value: boolean): void;
  (event: "toggle"): void;
}>();

const { tm } = useModuleI18n("features/chat");
const { items, expired, limits, error, loadList, loadDetail } = useLlmSnapshots(
  () => props.sessionId,
);

const selectedId = ref<string | null>(null);
const selectedDetail = ref<LlmSnapshotDetail | null>(null);
const previousDetail = ref<LlmSnapshotDetail | null>(null);
const listLoading = ref(false);
const detailLoading = ref(false);

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function str(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function num(record: Record<string, unknown>, key: string): number | null {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatDuration(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(value < 10000 ? 2 : 1)} s`;
}

function formatSize(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  if (value < 1024) return `${value} B`;
  const kb = value / 1024;
  if (kb < 1024) {
    return `${kb >= 100 ? Math.round(kb) : kb.toFixed(1)} KB`;
  }
  return `${(kb / 1024).toFixed(1)} MB`;
}

function formatTtl(seconds: number): string {
  if (seconds > 0 && seconds % 3600 === 0) {
    return `${seconds / 3600} h`;
  }
  return `${seconds}s`;
}

const KIND_KEYS: Record<string, string> = {
  chat: "devMode.kind.chat",
  compression: "devMode.kind.compression",
  title: "devMode.kind.title",
  aux: "devMode.kind.aux",
};

function kindLabel(item: LlmRequestSnapshotEnvelope): string {
  const kind = item.kind ?? "chat";
  const key = KIND_KEYS[kind];
  return key ? tm(key) : kind;
}

function sequence(item: LlmRequestSnapshotEnvelope): number {
  return typeof item.step === "number" ? item.step : Number.MAX_SAFE_INTEGER;
}

const orderedItems = computed(() =>
  items.value
    .slice()
    .sort(
      (a, b) =>
        sequence(a) - sequence(b) ||
        (a.created_at ?? 0) - (b.created_at ?? 0),
    ),
);
const chatItems = computed(() =>
  orderedItems.value.filter((item) => (item.kind ?? "chat") === "chat"),
);
const auxItems = computed(() =>
  orderedItems.value.filter((item) => (item.kind ?? "chat") !== "chat"),
);

const subtitle = computed(() =>
  tm("devMode.subtitle", {
    count: chatItems.value.length,
    aux: auxItems.value.length,
  }),
);

const limitsCopy = computed(() => {
  const value = limits.value;
  if (!value) return "";
  return tm("devMode.settings.limits", {
    count: value.max_snapshots_per_session,
    size: formatSize(value.max_bytes_per_snapshot),
    ttl: formatTtl(value.ttl_seconds),
  });
});

interface RailBadge {
  text: string;
  tone: "warn" | "error" | null;
}

function modelOf(item: LlmRequestSnapshotEnvelope): string {
  const provider = asRecord(item.provider);
  return (
    str(provider, "model") ??
    str(provider, "returned_model") ??
    str(asRecord(item.response), "returned_model") ??
    "—"
  );
}

function badges(item: LlmRequestSnapshotEnvelope): RailBadge[] {
  const result: RailBadge[] = [];
  const diagnostics = asRecord(item.diagnostics);
  const cache = asRecord(diagnostics.cache);
  const ratio = num(cache, "hit_ratio");
  if (ratio !== null) {
    result.push({
      text: tm("devMode.rail.cache", { percent: Math.round(ratio * 100) }),
      tone: null,
    });
  }
  if (diagnostics.compression) {
    result.push({ text: tm("devMode.rail.compression"), tone: "warn" });
  }
  if (item.status === "error") {
    result.push({ text: tm("devMode.rail.error"), tone: "error" });
  }
  const retries = asRecord(diagnostics.retries);
  if ((num(retries, "count") ?? 0) > 0) {
    result.push({ text: tm("devMode.rail.retry"), tone: "warn" });
  }
  const toolCount = asArray(item.tool_executions).length;
  if (toolCount > 0) {
    result.push({
      text: tm("devMode.rail.tools", { count: toolCount }),
      tone: null,
    });
  }
  return result;
}

async function selectSnapshot(snapshotId: string): Promise<void> {
  selectedId.value = snapshotId;
  previousDetail.value = null;
  detailLoading.value = true;
  const detail = await loadDetail(snapshotId);
  if (selectedId.value !== snapshotId) return;
  selectedDetail.value = detail;
  detailLoading.value = false;
  if (!detail) return;

  const index = chatItems.value.findIndex(
    (item) => item.snapshot_id === snapshotId,
  );
  const step =
    typeof detail.envelope.step === "number" ? detail.envelope.step : null;
  if (index > 0 && step !== null && step >= 2) {
    const previousItem = chatItems.value[index - 1];
    const previous = await loadDetail(previousItem.snapshot_id);
    if (selectedId.value === snapshotId) {
      previousDetail.value = previous;
    }
  }
}

async function loadListAndSelect(
  turnId: string | undefined,
  options: { keepSelection?: boolean } = {},
): Promise<void> {
  if (!props.modelValue || !props.enabled) return;
  listLoading.value = true;
  const previousSelection = options.keepSelection ? selectedId.value : null;
  selectedId.value = previousSelection;
  selectedDetail.value = null;
  previousDetail.value = null;
  await loadList({ turnId });
  listLoading.value = false;
  if (!props.modelValue || !props.enabled) return;

  const keep =
    previousSelection !== null &&
    orderedItems.value.some((item) => item.snapshot_id === previousSelection);
  const target = keep
    ? previousSelection
    : (orderedItems.value[0]?.snapshot_id ?? null);
  if (target) {
    await selectSnapshot(target);
  } else {
    selectedId.value = null;
  }
}

function handleRefresh(): void {
  if (!props.enabled) return;
  void loadListAndSelect(props.initialTurnId, { keepSelection: true });
}

function close(): void {
  emit("update:modelValue", false);
}

watch(
  () =>
    [
      props.modelValue,
      props.sessionId,
      props.initialTurnId,
      props.enabled,
    ] as const,
  ([open, , turnId, enabled]) => {
    if (!open) return;
    if (!enabled) {
      selectedId.value = null;
      selectedDetail.value = null;
      previousDetail.value = null;
      return;
    }
    void loadListAndSelect(turnId);
  },
  { immediate: true },
);

// ── Drag resize (same behaviour as ReasoningSidebar) ────────────────

const panelRef = ref<HTMLElement | null>(null);
const panelWidth = ref<number | null>(null);
let resizing = false;

function startResize(event: MouseEvent): void {
  event.preventDefault();
  resizing = true;
  document.body.style.cursor = "ew-resize";
  document.body.style.userSelect = "none";
  document.addEventListener("mousemove", onMouseMove);
  document.addEventListener("mouseup", endResize);
}

function onMouseMove(event: MouseEvent): void {
  if (!resizing || !panelRef.value) return;
  const rect = panelRef.value.getBoundingClientRect();
  const width = rect.right - event.clientX;
  panelWidth.value = Math.min(1800, Math.max(360, width));
}

function endResize(): void {
  if (!resizing) return;
  resizing = false;
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
  document.removeEventListener("mousemove", onMouseMove);
  document.removeEventListener("mouseup", endResize);
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && props.modelValue) close();
}

onMounted(() => {
  document.addEventListener("keydown", onKeydown);
});

onBeforeUnmount(() => {
  document.removeEventListener("keydown", onKeydown);
  endResize();
});
</script>

<style scoped>
.devmode-inspector {
  --di-border: var(--chat-border, rgba(var(--v-theme-on-surface), 0.14));
  --di-muted: rgba(var(--v-theme-on-surface), 0.56);
  --di-faint: rgba(var(--v-theme-on-surface), 0.06);
  width: min(820px, 66vw);
  height: calc(100% - var(--chat-panel-top-offset, 0px));
  margin-top: var(--chat-panel-top-offset, 0px);
  border-left: 1px solid var(--di-border);
  background: var(--chat-page-bg, rgb(var(--v-theme-surface)));
  color: rgb(var(--v-theme-on-surface));
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  position: relative;
}

.inspector-resizer {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 6px;
  cursor: ew-resize;
  z-index: 10;
  transition: background 0.15s ease;
}

.inspector-resizer:hover,
.inspector-resizer:active {
  background: rgba(var(--v-theme-primary), 0.2);
}

.inspector-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px 9px;
  border-bottom: 1px solid var(--di-border);
}

.inspector-heading {
  min-width: 0;
}

.inspector-title {
  font-size: 13.5px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.inspector-subtitle {
  margin-top: 2px;
  color: var(--di-muted);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 10.5px;
}

.inspector-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
  flex-shrink: 0;
}

.inspector-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.inspector-switch-label {
  color: var(--di-muted);
  font-size: 10px;
}

.inspector-action {
  padding: 3px 8px;
  border: 1px solid var(--di-border);
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.inspector-action:hover:not(:disabled) {
  background: var(--di-faint);
}

.inspector-action:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.inspector-settings {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  padding: 6px 14px;
  border-bottom: 1px solid var(--di-border);
  color: var(--di-muted);
  font-size: 10px;
  line-height: 1.5;
}

.inspector-limits {
  margin-left: auto;
}

.inspector-error {
  padding: 7px 14px;
  border-bottom: 1px solid var(--di-border);
  color: rgb(var(--v-theme-warning));
  font-size: 11px;
}

.inspector-state {
  padding: 28px 18px;
  color: var(--di-muted);
  font-size: 12px;
  line-height: 1.7;
}

.inspector-state p {
  margin: 0;
  max-width: 520px;
}

.inspector-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.inspector-rail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 212px;
  flex-shrink: 0;
  padding: 8px;
  border-right: 1px solid var(--di-border);
  overflow-y: auto;
}

.rail-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.rail-group-title {
  padding: 3px 6px;
  color: var(--di-muted);
  font-size: 9.5px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.rail-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 6px 8px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.rail-item:hover {
  background: var(--di-faint);
}

.rail-item.is-active {
  background: rgba(var(--v-theme-primary), 0.1);
  box-shadow: inset 2px 0 0 rgb(var(--v-theme-primary));
}

.rail-line {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.rail-kind {
  font-size: 11.5px;
  font-weight: 600;
  white-space: nowrap;
}

.rail-step,
.rail-status {
  color: var(--di-muted);
  font-size: 9.5px;
  white-space: nowrap;
}

.rail-status {
  margin-left: auto;
}

.rail-sub {
  color: var(--di-muted);
  font-size: 10.5px;
}

.rail-model {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rail-duration {
  margin-left: auto;
  flex-shrink: 0;
}

.rail-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  margin-top: 2px;
}

.rail-badge {
  padding: 0 4px;
  border: 1px solid var(--di-border);
  border-radius: 3px;
  color: var(--di-muted);
  font-size: 9px;
  line-height: 1.5;
}

.rail-badge.is-warn {
  border-color: rgba(var(--v-theme-warning), 0.45);
  color: rgb(var(--v-theme-warning));
}

.rail-badge.is-error {
  border-color: rgba(var(--v-theme-error), 0.5);
  color: rgb(var(--v-theme-error));
}

.inspector-detail {
  flex: 1;
  min-width: 0;
  padding: 12px 14px 20px;
  overflow-y: auto;
}

.detail-loading,
.detail-empty {
  padding: 12px 2px;
  color: var(--di-muted);
  font-size: 11px;
}

.inspector-body .mono,
.inspector-rail .mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}

/* ── Transition (mirrors ReasoningSidebar) ─────────────────────── */

.slide-left-enter-active,
.slide-left-leave-active {
  transition: all 0.2s ease;
}

.slide-left-enter-from,
.slide-left-leave-to {
  transform: translateX(100%);
  opacity: 0;
}

/* ── Narrow screens: full-screen, horizontal rail ──────────────── */

@media (max-width: 999px) {
  .devmode-inspector {
    position: fixed;
    inset: 0;
    z-index: 1300;
    width: 100vw !important;
    height: 100dvh;
    margin-top: 0;
    border-left: 0;
  }

  .inspector-resizer {
    display: none;
  }

  .inspector-head {
    padding: calc(10px + env(safe-area-inset-top)) 12px 8px;
  }

  .inspector-settings {
    padding: 6px 12px;
  }

  .inspector-body {
    flex-direction: column;
  }

  .inspector-rail {
    flex-direction: row;
    width: auto;
    max-height: 148px;
    border-right: 0;
    border-bottom: 1px solid var(--di-border);
    overflow-x: auto;
    overflow-y: hidden;
  }

  .rail-group {
    flex-direction: row;
    align-items: flex-start;
    flex-shrink: 0;
    gap: 3px;
  }

  .rail-group-title {
    padding-top: 8px;
    flex-shrink: 0;
  }

  .rail-item {
    min-width: 152px;
  }

  .inspector-detail {
    padding: 10px 12px calc(16px + env(safe-area-inset-bottom));
  }
}
</style>
