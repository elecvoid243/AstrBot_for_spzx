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
    <div class="srq-skills">
      <div
        v-for="name in block.skills"
        :key="name"
        class="srq-row"
        :title="name"
      >
        <v-icon size="14" class="srq-skill-icon"
          >mdi-lightbulb-on-outline</v-icon
        >
        <span class="srq-name">{{ name }}</span>
      </div>
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
  margin-top: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(var(--v-theme-primary), 0.16);
  border-radius: 14px;
  background: linear-gradient(
    120deg,
    rgba(var(--v-theme-primary), 0.07),
    rgba(var(--v-theme-primary), 0.02)
  );
  text-align: left;
}
.srq-head {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 10px;
}
.srq-title {
  font-size: 11px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.65);
}
.srq-chip {
  display: grid;
  place-items: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 6px;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.1);
  font-size: 10px;
  font-weight: 700;
}
.srq-skills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.srq-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 5px 9px;
  border: 1px solid rgba(var(--v-theme-primary), 0.18);
  border-radius: 8px;
  background: rgba(var(--v-theme-surface), 0.75);
  color: rgb(var(--v-theme-primary));
}
.srq-skill-icon {
  flex: none;
  opacity: 0.7;
}
.srq-name {
  min-width: 0;
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  font-weight: 500;
}
</style>
