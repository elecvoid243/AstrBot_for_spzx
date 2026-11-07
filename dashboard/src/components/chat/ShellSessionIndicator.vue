<!--
  ShellSessionIndicator
  ─────────────────────────────────────────────────────────────────────
  App-bar entry for managed background shell sessions. Reads the active
  session's list from the chatHeader store (pushed by Chat.vue /
  useShellSessions), badges the total with a pulse while anything is alive,
  and lists sessions in a popover on click. Finished sessions can be
  discarded one by one or all at once from that popover.

  Hidden entirely when there are no sessions — zero noise by default.

  Author: elecvoid243 | 2026-10-05
-->
<template>
  <v-menu
    v-if="sessions.length"
    v-model="menuOpen"
    location="top end"
    :close-on-content-click="false"
    transition="none"
  >
    <template #activator="{ props: menuProps }">
      <v-btn
        v-bind="menuProps"
        class="chat-action-btn shell-session-trigger"
        variant="text"
        size="small"
        rounded="sm"
        :title="summary"
        :aria-label="summary"
      >
        <v-icon
          size="18"
          :class="{ 'shell-pulse': runningCount > 0 }"
          :color="runningCount > 0 ? '#2da44e' : undefined"
        >
          mdi-console
        </v-icon>
        <span
          class="shell-trigger-count"
          :class="{ dimmed: runningCount === 0 }"
        >
          {{ sessions.length }}
        </span>
      </v-btn>
    </template>

    <v-card class="shell-session-popover" elevation="8" rounded="lg">
      <div class="popover-header">
        <div class="popover-title">
          {{ tm("shellSession.indicator.title") }}
        </div>
        <button
          v-if="finishedCount > 0"
          type="button"
          class="clear-all"
          :class="{ confirm: cleanupConfirmId === CLEAR_ALL_KEY }"
          :disabled="clearing || !conversationId"
          :title="
            cleanupConfirmId === CLEAR_ALL_KEY
              ? tm('shellSession.labels.cleanupAllConfirm', {
                  count: finishedCount,
                })
              : tm('shellSession.labels.cleanupAll')
          "
          @click="onClearFinished"
        >
          <v-icon size="14">
            {{
              cleanupConfirmId === CLEAR_ALL_KEY
                ? "mdi-check"
                : "mdi-delete-sweep-outline"
            }}
          </v-icon>
          <span>
            {{
              cleanupConfirmId === CLEAR_ALL_KEY
                ? tm("shellSession.labels.cleanupAllConfirm", {
                    count: finishedCount,
                  })
                : tm("shellSession.labels.cleanupAll")
            }}
          </span>
        </button>
      </div>
      <div
        v-for="s in sessions"
        :key="s.session_id"
        class="session-row"
        role="button"
        :title="tm('shellSession.window.view')"
        @click="openWindow(s.session_id)"
      >
        <div class="session-row-main">
          <span
            class="status-dot"
            :class="{ 'shell-pulse': statusMeta(s.status).pulse }"
            :style="{ backgroundColor: statusMeta(s.status).color }"
            :title="
              tm(`shellSession.stateLabels.${statusMeta(s.status).i18nKey}`)
            "
          />
          <span class="session-id" @click.stop>
            <CopyableText
              :value="s.session_id"
              :title="s.session_id"
              mode="code"
            />
          </span>
          <span
            class="status-label"
            :style="{ color: statusMeta(s.status).color }"
          >
            {{ tm(`shellSession.stateLabels.${statusMeta(s.status).i18nKey}`) }}
          </span>
          <span
            v-if="s.exit_code !== null && s.exit_code !== undefined"
            class="exit-code"
            :class="s.exit_code === 0 ? 'success' : 'error'"
            >exit {{ s.exit_code }}</span
          >
          <button
            v-if="s.status !== 'running'"
            class="row-cleanup"
            :class="{ confirm: cleanupConfirmId === s.session_id }"
            :title="
              cleanupConfirmId === s.session_id
                ? tm('shellSession.labels.cleanupConfirm')
                : tm('shellSession.labels.cleanup')
            "
            :disabled="!conversationId"
            @click.stop="onCleanup(s.session_id)"
          >
            <v-icon size="13">
              {{
                cleanupConfirmId === s.session_id
                  ? "mdi-check"
                  : "mdi-delete-outline"
              }}
            </v-icon>
          </button>
        </div>
        <div v-if="s.command" class="session-command" :title="s.command">
          {{ s.command }}
        </div>
        <div class="session-row-meta">
          <span>pid {{ s.pid }}</span>
          <template v-if="s.started_at">
            <span class="meta-sep">·</span>
            <span>{{ formatRelativeTime(s.started_at) }}</span>
          </template>
          <template v-if="s.unread_output_bytes > 0">
            <span class="meta-sep">·</span>
            <span
              >{{ tm("shellSession.labels.unread") }}
              {{ formatBytes(s.unread_output_bytes) }}</span
            >
          </template>
        </div>
      </div>
    </v-card>
  </v-menu>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { chatApi } from "@/api/v1";
import { useChatHeaderStore } from "@/stores/chatHeader";
import { useModuleI18n } from "@/i18n/composables";
import CopyableText from "./message_list_comps/__shared__/CopyableText.vue";
import { formatRelativeTime } from "./message_list_comps/inta_shell_tools/format";
import { formatBytes } from "./message_list_comps/shell_session_tools/format";
import { getShellSessionStatusMeta } from "./message_list_comps/shell_session_tools/icons";

const props = defineProps<{
  /** Active conversation id; cleanup calls are scoped to it. */
  conversationId: string | null;
}>();

const chatHeader = useChatHeaderStore();
const { tm } = useModuleI18n("features/chat");

const menuOpen = ref(false);

const sessions = computed(() => chatHeader.shellSessions ?? []);
const runningCount = computed(
  () => sessions.value.filter((s) => s.status === "running").length,
);
const finishedCount = computed(
  () => sessions.value.length - runningCount.value,
);

// The badge is the total session count. Switching it to the running count
// while anything was alive made the number jump (3 → 1 → 3) as sessions
// were reaped; liveness stays a separate signal (pulse + accent icon) and
// the exact running count lives in the tooltip.
const summary = computed(() =>
  tm("shellSession.indicator.summary", {
    total: sessions.value.length,
    running: runningCount.value,
  }),
);

const statusMeta = getShellSessionStatusMeta;

/** Open (or raise) the floating output window for one session. */
function openWindow(shellSessionId: string) {
  chatHeader.SET_SHELL_WINDOW_OPEN(shellSessionId, true);
  menuOpen.value = false;
}

// ── Cleanup of finished sessions (early reap) ─────────────────────
// Two-click confirm, mirroring the window's terminate button. The list
// itself refreshes through the shell_sessions_changed push that the
// removal fires — no local state surgery.
const CLEAR_ALL_KEY = "__clear_all__";
const cleanupConfirmId = ref<string | null>(null);
const clearing = ref(false);
let cleanupTimer: ReturnType<typeof setTimeout> | null = null;

// An emptied session list unmounts the menu (see the root `v-if`) but not
// this component, so `menuOpen` and an armed confirm would survive. The
// next short-lived session would then remount the popover already open —
// the whole panel flashes for as long as that process lives. Collapse the
// transient UI state the moment the list empties.
watch(
  () => sessions.value.length,
  (count) => {
    if (count > 0) return;
    menuOpen.value = false;
    cleanupConfirmId.value = null;
    if (cleanupTimer) {
      clearTimeout(cleanupTimer);
      cleanupTimer = null;
    }
  },
);

/**
 * Arm a two-click confirmation for one cleanup target.
 *
 * Returns:
 *   True when this click confirmed an already-armed target, false when it
 *   only armed it (or re-armed a different target).
 */
function armCleanup(key: string): boolean {
  if (cleanupConfirmId.value === key) {
    cleanupConfirmId.value = null;
    if (cleanupTimer) {
      clearTimeout(cleanupTimer);
      cleanupTimer = null;
    }
    return true;
  }
  cleanupConfirmId.value = key;
  if (cleanupTimer) clearTimeout(cleanupTimer);
  cleanupTimer = setTimeout(() => {
    cleanupConfirmId.value = null;
    cleanupTimer = null;
  }, 3000);
  return false;
}

async function onCleanup(shellSessionId: string) {
  if (!props.conversationId) return;
  if (!armCleanup(shellSessionId)) return;
  try {
    const resp = await chatApi.discardShellSession(
      props.conversationId,
      shellSessionId,
    );
    // Business errors arrive as HTTP 200 {status:"error"} envelopes.
    const envelope = resp.data as { status?: string } | undefined;
    if (!envelope || envelope.status !== "ok") {
      console.error("Failed to clean up shell session:", envelope);
    }
  } catch (error) {
    console.error("Failed to clean up shell session:", error);
  }
}

/**
 * Discard every finished session in one go.
 *
 * Reuses the per-session endpoint so the backend reap rules stay in one
 * place; each removal pushes its own snapshot, which keeps the list in
 * sync without local state surgery. Running sessions are never included.
 */
async function onClearFinished() {
  const conversationId = props.conversationId;
  if (!conversationId || clearing.value) return;
  const targets = sessions.value.filter((s) => s.status !== "running");
  if (!targets.length) return;
  if (!armCleanup(CLEAR_ALL_KEY)) return;

  clearing.value = true;
  let failed = 0;
  try {
    await Promise.all(
      targets.map(async (s) => {
        try {
          const resp = await chatApi.discardShellSession(
            conversationId,
            s.session_id,
          );
          // Business errors arrive as HTTP 200 {status:"error"} envelopes.
          const envelope = resp.data as { status?: string } | undefined;
          if (!envelope || envelope.status !== "ok") failed += 1;
        } catch {
          failed += 1;
        }
      }),
    );
  } finally {
    clearing.value = false;
  }
  if (failed) {
    console.error(
      `Failed to clean up ${failed}/${targets.length} shell sessions.`,
    );
  }
}

onUnmounted(() => {
  if (cleanupTimer) clearTimeout(cleanupTimer);
});
</script>

<style scoped>
/* 2026-11-07 (elecvoid243): match the status-row capsule family — the plain
   text button used to blend into the page background in dark mode. */
.shell-session-trigger.chat-action-btn {
  height: 28px;
  min-width: 28px;
  padding: 0 8px;
  border: 1px solid var(--sp-chip-border);
  border-radius: 8px;
  background: var(--sp-chip-bg);
  color: var(--sp-text-muted);
  box-shadow: none;
}

.shell-session-trigger.chat-action-btn:hover {
  background: var(--sp-ghost-hover-bg, rgba(var(--v-theme-on-surface), 0.055));
  border-color: var(--sp-chip-border-strong);
  color: var(--sp-text-primary);
}

.shell-trigger-count {
  margin-left: 4px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.shell-trigger-count.dimmed {
  opacity: 0.55;
}

.shell-session-popover {
  min-width: 400px;
  max-width: 480px;
  padding: 8px;
}

.popover-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 8px 8px;
}

.popover-title {
  font-size: 12px;
  font-weight: 600;
  opacity: 0.65;
}

/* Bulk cleanup: quiet text button in the popover header. The confirm
   click turns it green (check icon), matching the per-row button. */
.clear-all {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: none;
  padding: 3px 6px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  color: inherit;
  opacity: 0.55;
  white-space: nowrap;
}

.clear-all:hover:not(:disabled) {
  opacity: 1;
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.clear-all.confirm {
  opacity: 1;
  color: #2da44e;
}

.clear-all:disabled {
  cursor: default;
  opacity: 0.35;
}

.session-row {
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
}

.session-row:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.session-row-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

/* Hug the id text: the span must NOT stretch across the row's free space,
   otherwise its copy-only click zone becomes an invisible dead spot for
   the row's open-window click. It shrinks (with ellipsis) only when the
   id itself is too long. */
.session-id {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-label {
  font-size: 12px;
  flex-shrink: 0;
  margin-left: auto;
}

.exit-code {
  font-size: 11px;
  font-family: monospace;
  flex-shrink: 0;
}

.exit-code.success {
  color: #2da44e;
}

.exit-code.error {
  color: #cf222e;
}

/* Cleanup button for finished sessions: quiet until hovered; the confirm
   click turns it green (check icon). */
.row-cleanup {
  border: none;
  background: none;
  padding: 1px 3px;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0.45;
  color: inherit;
  flex-shrink: 0;
}

.row-cleanup:hover {
  opacity: 1;
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.row-cleanup.confirm {
  opacity: 1;
  color: #2da44e;
}

.session-row-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  padding-left: 16px;
  font-size: 11px;
  opacity: 0.6;
  font-family: monospace;
}

.meta-sep {
  opacity: 0.6;
}

.shell-pulse {
  animation: shell-pulse 1.6s ease-in-out infinite;
}

@keyframes shell-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
.session-command {
  margin: 5px 0 5px 16px;
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font:
    12px/1.5 ui-monospace,
    SFMono-Regular,
    Menlo,
    Consolas,
    monospace;
  color: rgba(var(--v-theme-on-surface), 0.8);
}
</style>
