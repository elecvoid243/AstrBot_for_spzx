<template>
  <div class="subagent-run-block" :class="{ 'is-dark': isDark }">
    <button class="subagent-run-header" type="button" @click="open">
      <span
        class="status-dot"
        :class="{
          'status-running': displayPart.status === 'running',
          'status-completed': displayPart.status === 'completed',
          'status-failed': ['failed', 'timeout'].includes(displayPart.status),
        }"
      />
      <v-icon size="18" class="agent-icon">mdi-robot-outline</v-icon>
      <span class="agent-name">{{ part.agent_name || "subagent" }}</span>
      <span class="status-label">{{
        displayPart.cleaned
          ? tm("subagentPages.cleaned")
          : tm(`subagentStatus.${displayPart.status || "running"}`)
      }}</span>
      <span v-if="part.background" class="status-label">{{
        tm("subagentPages.background")
      }}</span>
      <span v-if="part.execution_time != null" class="duration"
        >{{ Number(part.execution_time).toFixed(1) }}s</span
      >
      <v-icon size="18" class="expand-icon">mdi-chevron-right</v-icon>
    </button>
  </div>
</template>

<script setup>
import { computed, inject } from "vue";
import { useModuleI18n } from "@/i18n/composables";
const props = defineProps({
  part: { type: Object, required: true },
  isDark: Boolean,
  sessionId: { type: String, default: "" },
});
const emit = defineEmits(["open"]);
const { tm } = useModuleI18n("features/chat");
const openSubagentRun = inject("openSubagentRun", null);
const metadata = inject("subagentRunMetadata", null);
const displayPart = computed(() => ({
  ...props.part,
  ...(typeof metadata === "function"
    ? metadata(props.part.subagent_run_id)
    : {}),
}));
function open() {
  if (typeof openSubagentRun === "function")
    openSubagentRun(props.part, props.sessionId);
  else emit("open", props.part);
}
</script>

<style scoped>
.subagent-run-block {
  margin: 6px 0;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 8px;
  overflow: clip;
}
.subagent-run-block.is-dark {
  border-color: rgba(255, 255, 255, 0.12);
}
.subagent-run-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  color: inherit;
  background: transparent;
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.subagent-run-header:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.agent-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 600;
  font-size: 0.9em;
}
.status-label,
.duration {
  font-size: 0.82em;
  opacity: 0.65;
  white-space: nowrap;
}
.status-dot {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: 50%;
  background: rgba(var(--v-theme-on-surface), 0.3);
}
.status-running {
  background: #4caf50;
  animation: pulse 1.2s ease-in-out infinite;
}
.status-completed {
  background: #4caf50;
}
.status-failed {
  background: #e57373;
}
.agent-icon {
  opacity: 0.7;
}
.duration {
  opacity: 0.55;
}
.expand-icon {
  margin-left: auto;
  opacity: 0.5;
}
@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
</style>
