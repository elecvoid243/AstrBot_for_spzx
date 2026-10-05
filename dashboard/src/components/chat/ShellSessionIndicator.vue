<!--
  ShellSessionIndicator
  ─────────────────────────────────────────────────────────────────────
  App-bar entry for managed background shell sessions (Scope A: awareness
  only, read-only). Reads the active session's session list from the
  chatHeader store (pushed by Chat.vue / useShellSessions), shows a running
  count badge with a pulse while anything is alive, and lists sessions in a
  popover on click.

  Hidden entirely when there are no sessions — zero noise by default.

  Author: elecvoid243 | 2026-10-05
-->
<template>
  <v-menu
    v-if="sessions.length"
    location="bottom end"
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
        :title="tm('shellSession.indicator.title')"
        :aria-label="tm('shellSession.indicator.title')"
      >
        <v-icon size="18" :class="{ 'shell-pulse': runningCount > 0 }" :color="runningCount > 0 ? '#2da44e' : undefined">
          mdi-console
        </v-icon>
        <span class="shell-trigger-count">{{ sessions.length }}</span>
      </v-btn>
    </template>

    <v-card class="shell-session-popover" elevation="8" rounded="lg">
      <div class="popover-title">{{ tm("shellSession.indicator.title") }}</div>
      <div
        v-for="s in sessions"
        :key="s.session_id"
        class="session-row"
      >
        <div class="session-row-main">
          <span
            class="status-dot"
            :class="{ 'shell-pulse': statusMeta(s.status).pulse }"
            :style="{ backgroundColor: statusMeta(s.status).color }"
            :title="tm(`shellSession.stateLabels.${statusMeta(s.status).i18nKey}`)"
          />
          <CopyableText
            :value="s.session_id"
            :display-value="shortId(s.session_id)"
            :title="s.session_id"
            mode="code"
            class="session-id"
          />
          <span class="status-label" :style="{ color: statusMeta(s.status).color }">
            {{ tm(`shellSession.stateLabels.${statusMeta(s.status).i18nKey}`) }}
          </span>
          <span
            v-if="s.exit_code !== null && s.exit_code !== undefined"
            class="exit-code"
            :class="s.exit_code === 0 ? 'success' : 'error'"
          >exit {{ s.exit_code }}</span>
        </div>
        <div class="session-row-meta">
          <span>pid {{ s.pid }}</span>
          <template v-if="s.started_at">
            <span class="meta-sep">·</span>
            <span>{{ formatRelativeTime(s.started_at) }}</span>
          </template>
          <template v-if="s.unread_output_bytes > 0">
            <span class="meta-sep">·</span>
            <span>{{ tm("shellSession.labels.unread") }} {{ formatBytes(s.unread_output_bytes) }}</span>
          </template>
        </div>
      </div>
    </v-card>
  </v-menu>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useChatHeaderStore } from "@/stores/chatHeader";
import { useModuleI18n } from "@/i18n/composables";
import CopyableText from "./message_list_comps/__shared__/CopyableText.vue";
import { formatRelativeTime } from "./message_list_comps/inta_shell_tools/format";
import { formatBytes } from "./message_list_comps/shell_session_tools/format";
import { getShellSessionStatusMeta } from "./message_list_comps/shell_session_tools/icons";

const chatHeader = useChatHeaderStore();
const { tm } = useModuleI18n("features/chat");

const sessions = computed(() => chatHeader.shellSessions ?? []);
const runningCount = computed(
  () => sessions.value.filter((s) => s.status === "running").length,
);

const statusMeta = getShellSessionStatusMeta;

/** session_id renders as an 8-char prefix (hover/copy gives the full id). */
function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}
</script>

<style scoped>
.shell-trigger-count {
  margin-left: 4px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.shell-session-popover {
  min-width: 320px;
  max-width: 420px;
  padding: 8px;
}

.popover-title {
  font-size: 12px;
  font-weight: 600;
  opacity: 0.65;
  padding: 4px 8px 8px;
}

.session-row {
  padding: 6px 8px;
  border-radius: 6px;
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

.status-label {
  font-size: 12px;
  flex-shrink: 0;
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
</style>
