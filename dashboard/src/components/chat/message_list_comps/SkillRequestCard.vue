<!-- Author: elecvoid243, 2026-10-01
     Renders a parsed "[Requested skills]" block (utils/parseSkillRequests)
     as a static card: header (icon + title + count chip) + one row per
     queued skill. No collapse — request lists are short (YAGNI).
     Theme tokens only, same approach as FileReferencesCard. Icon matches
     the SkillGuideMenuItem trigger (mdi-lightbulb-on-outline) so the card
     reads as the same feature. -->
<template>
  <div class="srq">
    <div class="srq-head">
      <v-icon size="15" color="primary">mdi-lightbulb-on-outline</v-icon>
      <span class="srq-title">{{
        tm("spcodeProjectLoad.skillRequestCard.title")
      }}</span>
      <span class="srq-chip">{{ block.skills.length }}</span>
    </div>
    <div v-for="(name, i) in block.skills" :key="i" class="srq-row">
      <v-icon size="14" class="srq-skill-icon">mdi-lightbulb-on-outline</v-icon>
      <span class="srq-name">{{ name }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useModuleI18n } from "@/i18n/composables";
import type { SkillRequestBlock } from "@/utils/parseSkillRequests";

defineProps<{ block: SkillRequestBlock }>();

const { tm } = useModuleI18n("features/chat");
</script>

<style scoped>
.srq {
  width: 100%;
  margin-top: 4px;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-left: 3px solid rgb(var(--v-theme-primary));
  border-radius: 12px;
  overflow: hidden;
  text-align: left;
}
.srq-head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 14px;
  background: rgb(var(--v-theme-mcpCardBg));
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}
.srq-title {
  font-size: 13px;
  font-weight: 700;
  color: rgb(var(--v-theme-on-surface));
}
.srq-chip {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10.5px;
  font-weight: 600;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.12);
  border-radius: 99px;
  padding: 2px 9px;
  white-space: nowrap;
}
.srq-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}
.srq-row:last-child {
  border-bottom: 0;
}
.srq-skill-icon {
  flex: none;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.srq-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12.5px;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
