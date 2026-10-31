<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { SpcodeGitWorktree } from "@/composables/parseSpcodeWorktrees";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  worktrees: SpcodeGitWorktree[];
  selectedPath: string | null;
}>();
const emit = defineEmits<{
  change: [path: string | null];
  create: [];
  contextmenu: [event: MouseEvent, worktree: SpcodeGitWorktree];
}>();
const { tm } = useModuleI18n("features/chat");
const tabs = ref<HTMLElement | null>(null);
const useDropdown = ref(false);
const menuOpen = ref(false);
const items = computed(() =>
  props.worktrees.map((wt) => ({
    worktree: wt,
    label:
      wt.branch ??
      (wt.isMain
        ? tm("spcodeProjectLoad.diffSidebar.worktreeTabs.mainBadge")
        : wt.headSha.slice(0, 7)),
    icon: wt.isMain
      ? "mdi-home"
      : wt.prunable
      ? "mdi-alert"
      : wt.locked
      ? "mdi-lock"
      : "mdi-source-branch",
    badge: wt.prunable
      ? tm("spcodeProjectLoad.diffSidebar.worktreeTabs.prunableBadge")
      : !wt.branch
      ? tm("spcodeProjectLoad.diffSidebar.worktreeTabs.detachedBadge")
      : "",
    active:
      props.selectedPath === wt.path || (!props.selectedPath && wt.isMain),
  })),
);
const activeItem = computed(() => items.value.find((item) => item.active));

watch(tabs, (element, _, onCleanup) => {
  if (!element) return;
  const observer = new ResizeObserver(() => {
    if (!element.clientWidth || !element.firstElementChild) return;
    const tabHeight = (element.firstElementChild as HTMLElement).offsetHeight;
    const gap = parseFloat(getComputedStyle(element).rowGap) || 0;
    // Keep measuring the full button strip in dropdown mode so widening the
    // sidebar or removing worktrees can restore buttons without oscillation.
    useDropdown.value = element.offsetHeight > tabHeight * 2 + gap + 1;
    if (!useDropdown.value) menuOpen.value = false;
  });
  observer.observe(element);
  onCleanup(() => observer.disconnect());
});
</script>

<template>
  <div class="worktree-switcher">
    <span class="worktree-switcher-label">
      {{ tm("spcodeProjectLoad.diffSidebar.worktreeTabs.label") }}
    </span>
    <div class="worktree-switcher-content">
      <div
        ref="tabs"
        class="worktree-tabs"
        :class="{ 'worktree-tabs--measuring': useDropdown }"
        :aria-hidden="useDropdown || undefined"
        :inert="useDropdown ? '' : undefined"
        role="tablist"
        :aria-label="tm('spcodeProjectLoad.diffSidebar.worktreeTabs.ariaLabel')"
      >
        <button
          v-for="item in items"
          :key="item.worktree.path"
          type="button"
          role="tab"
          class="worktree-tab"
          :class="{
            'worktree-tab--active': item.active,
            'worktree-tab--prunable': item.worktree.prunable,
          }"
          :aria-selected="item.active"
          :tabindex="useDropdown ? -1 : 0"
          :title="
            item.worktree.prunable
              ? tm('spcodeProjectLoad.diffSidebar.worktreeTabs.prunableTooltip')
              : item.worktree.path
          "
          @click="
            emit('change', item.worktree.isMain ? null : item.worktree.path)
          "
          @contextmenu.prevent="emit('contextmenu', $event, item.worktree)"
        >
          <v-icon v-if="item.icon !== 'mdi-source-branch'" size="12">{{
            item.icon
          }}</v-icon>
          <span class="worktree-tab-label">{{ item.label }}</span>
          <span v-if="item.badge" class="worktree-tab-badge">{{
            item.badge
          }}</span>
        </button>
      </div>
      <v-menu
        v-if="useDropdown"
        v-model="menuOpen"
        location="bottom start"
        max-width="400"
      >
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            type="button"
            class="worktree-menu-button"
            :aria-label="
              tm('spcodeProjectLoad.diffSidebar.worktreeTabs.ariaLabel')
            "
            :title="activeItem?.worktree.path"
          >
            <v-icon size="14">{{
              activeItem?.icon ?? "mdi-source-branch"
            }}</v-icon>
            <span class="worktree-tab-label">{{ activeItem?.label }}</span>
            <span v-if="activeItem?.badge" class="worktree-tab-badge">{{
              activeItem.badge
            }}</span>
            <v-icon size="12">mdi-menu-down</v-icon>
          </button>
        </template>
        <v-list density="compact" class="worktree-menu">
          <v-list-item
            v-for="item in items"
            :key="item.worktree.path"
            :active="item.active"
            :title="item.label"
            :subtitle="item.worktree.path"
            :prepend-icon="item.icon"
            @click="
              emit('change', item.worktree.isMain ? null : item.worktree.path)
            "
            @contextmenu.prevent="emit('contextmenu', $event, item.worktree)"
          >
            <template #append>
              <span v-if="item.badge" class="worktree-tab-badge">{{
                item.badge
              }}</span>
              <v-icon v-if="item.active" size="14">mdi-check</v-icon>
            </template>
          </v-list-item>
        </v-list>
      </v-menu>
    </div>
    <button
      type="button"
      class="worktree-add"
      :aria-label="
        tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.addButtonAria')
      "
      :title="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.addButton')"
      @click="emit('create')"
    >
      <v-icon size="14">mdi-plus</v-icon>
    </button>
  </div>
</template>

<style scoped>
.worktree-switcher {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 6px 14px 8px 16px;
  border-bottom: 1px solid
    var(--chat-border, rgba(var(--v-theme-on-surface), 0.08));
}
.worktree-switcher-label {
  flex-shrink: 0;
  margin-top: 5px;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.6);
}
.worktree-switcher-content {
  position: relative;
  flex: 1;
  min-width: 0;
  overflow: hidden;
}
.worktree-tabs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.worktree-tabs--measuring {
  position: absolute;
  inset: 0 0 auto;
  visibility: hidden;
  pointer-events: none;
}
.worktree-tab,
.worktree-menu-button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: 1px solid var(--chat-border, rgba(var(--v-theme-on-surface), 0.16));
  border-radius: 999px;
  background: transparent;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.62));
  font: inherit;
  font-size: 12px;
  line-height: 18px;
  cursor: pointer;
  max-width: min(180px, 100%);
}
.worktree-tab:hover,
.worktree-menu-button:hover,
.worktree-add:hover {
  background: var(
    --chat-session-active-bg,
    rgba(var(--v-theme-on-surface), 0.06)
  );
  border-color: rgb(var(--v-theme-primary));
}
.worktree-tab--active {
  background: rgba(var(--v-theme-primary), 0.12);
  border-color: rgba(var(--v-theme-primary), 0.45);
  color: rgb(var(--v-theme-primary));
  font-weight: 500;
}
.worktree-tab :deep(.v-icon),
.worktree-menu-button :deep(.v-icon) {
  flex-shrink: 0;
}
.worktree-tab-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.worktree-menu-button {
  width: 100%;
  max-width: 220px;
  border-radius: 6px;
  padding: 3px 8px;
  color: rgb(var(--v-theme-on-surface));
}
.worktree-menu-button .worktree-tab-label {
  flex: 1;
}
.worktree-menu {
  max-height: 360px;
  overflow-y: auto;
}
.worktree-menu :deep(.v-list-item-title) {
  font-size: 12px;
}
.worktree-menu :deep(.v-list-item-subtitle) {
  font-size: 11px;
}
.worktree-tab-badge {
  flex-shrink: 0;
  font-size: 10px;
  line-height: 14px;
  padding: 1px 5px;
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface), 0.08);
}
.worktree-tab--prunable {
  border-color: rgba(255, 152, 0, 0.4);
  background: rgba(255, 152, 0, 0.08);
}
.worktree-add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  margin-top: 2px;
  border-radius: 999px;
  border: 1px solid var(--chat-border, rgba(var(--v-theme-on-surface), 0.18));
  background: transparent;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.62));
  cursor: pointer;
}
@media (max-width: 760px) {
  .worktree-tab {
    font-size: 11px;
    padding: 3px 8px;
    max-width: min(140px, 100%);
  }
}
</style>
