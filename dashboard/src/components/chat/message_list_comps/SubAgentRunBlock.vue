<template>
  <button
    class="subagent-run-block"
    :class="{ 'is-dark': isDark }"
    type="button"
    @click="open"
  >
    <span
      class="status-dot"
      :class="{
        running: displayPart.status === 'running' && !displayPart.cleaned,
      }"
    />
    <v-icon size="18">mdi-robot-outline</v-icon>
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
    <v-icon size="18">mdi-chevron-right</v-icon>
  </button>
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
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 10px;
  color: inherit;
  background: transparent;
  text-align: left;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.subagent-run-block:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.agent-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.status-label,
.duration {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 12px;
  white-space: nowrap;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgba(var(--v-theme-on-surface), 0.3);
}
.status-dot.running {
  background: rgb(var(--v-theme-primary));
  box-shadow: 0 0 0 3px rgba(var(--v-theme-primary), 0.12);
}
</style>
