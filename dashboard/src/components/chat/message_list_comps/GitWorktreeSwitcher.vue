<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { SpcodeGitWorktree } from "@/composables/parseSpcodeWorktrees";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  worktrees: SpcodeGitWorktree[];
  selectedPath: string | null;
  dropdownTarget?: HTMLElement | null;
}>();
const emit = defineEmits<{
  change: [path: string | null];
  create: [];
  contextmenu: [event: MouseEvent, worktree: SpcodeGitWorktree];
  lock: [worktree: SpcodeGitWorktree];
  remove: [worktree: SpcodeGitWorktree];
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

// 2026-11-08 (elecvoid243): per-row management actions. The guards live
// here (not only in the disabled styling) so a mis-wired click can never
// reach the parent's destructive dialogs. Closing the menu first keeps the
// v-menu overlay from covering the confirmation dialog it just opened.
function onLockClick(wt: SpcodeGitWorktree): void {
  if (wt.isMain) return;
  menuOpen.value = false;
  emit("lock", wt);
}

function onRemoveClick(wt: SpcodeGitWorktree): void {
  if (wt.isMain || wt.locked) return;
  menuOpen.value = false;
  emit("remove", wt);
}

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
  <div
    class="worktree-switcher"
    :class="{ 'worktree-switcher--inline': useDropdown && dropdownTarget }"
    :aria-hidden="useDropdown && dropdownTarget ? true : undefined"
    :inert="useDropdown && dropdownTarget ? '' : undefined"
  >
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
      <!-- Keep the measuring strip at full width while the selector joins the branch row. -->
      <Teleport :to="dropdownTarget || 'body'" :disabled="!dropdownTarget">
        <div v-if="useDropdown" class="worktree-dropdown">
          <span v-if="dropdownTarget" class="worktree-switcher-label">
            {{ tm("spcodeProjectLoad.diffSidebar.worktreeTabs.label") }}
          </span>
          <v-menu v-model="menuOpen" location="bottom start" max-width="352">
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
                class="worktree-create-menu-item"
                prepend-icon="mdi-plus"
                :title="
                  tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.addButton')
                "
                @click="
                  menuOpen = false;
                  emit('create');
                "
              />
              <v-divider />
              <!-- 2026-11-08 (elecvoid243) menu-unify: rows mirror the branch
                   menu — prepended check for the active entry, inline
                   management icons in the append slot, disabled states only
                   for the main worktree and for removal of a locked one. -->
              <v-list-item
                v-for="item in items"
                :key="item.worktree.path"
                class="worktree-menu-item"
                :active="item.active"
                :title="item.label"
                :subtitle="item.worktree.path"
                @click="
                  emit(
                    'change',
                    item.worktree.isMain ? null : item.worktree.path,
                  )
                "
                @contextmenu.prevent="
                  emit('contextmenu', $event, item.worktree)
                "
              >
                <template #prepend>
                  <v-icon v-if="item.active" size="14" color="primary"
                    >mdi-check</v-icon
                  >
                  <v-icon v-else size="14">{{ item.icon }}</v-icon>
                </template>
                <template #append>
                  <span v-if="item.badge" class="worktree-tab-badge">{{
                    item.badge
                  }}</span>
                  <span class="worktree-menu-actions">
                    <v-icon
                      size="14"
                      class="worktree-menu-action"
                      :class="{ 'is-disabled': item.worktree.isMain }"
                      :title="
                        item.worktree.isMain
                          ? tm(
                              'spcodeProjectLoad.diffSidebar.worktreeMgmt.contextMenu.mainDisabled',
                            )
                          : item.worktree.locked
                            ? tm(
                                'spcodeProjectLoad.diffSidebar.worktreeMgmt.contextMenu.unlock',
                              )
                            : tm(
                                'spcodeProjectLoad.diffSidebar.worktreeMgmt.contextMenu.lock',
                              )
                      "
                      @click.stop="onLockClick(item.worktree)"
                      >{{
                        item.worktree.locked
                          ? "mdi-lock-open-variant"
                          : "mdi-lock"
                      }}</v-icon
                    >
                    <v-icon
                      size="14"
                      class="worktree-menu-action worktree-menu-action--danger"
                      :class="{
                        'is-disabled':
                          item.worktree.isMain || item.worktree.locked,
                      }"
                      :title="
                        item.worktree.isMain
                          ? tm(
                              'spcodeProjectLoad.diffSidebar.worktreeMgmt.contextMenu.mainDisabled',
                            )
                          : item.worktree.locked
                            ? tm(
                                'spcodeProjectLoad.diffSidebar.worktreeMgmt.rowActions.removeLocked',
                              )
                            : tm(
                                'spcodeProjectLoad.diffSidebar.worktreeMgmt.contextMenu.remove',
                              )
                      "
                      @click.stop="onRemoveClick(item.worktree)"
                      >mdi-trash-can-outline</v-icon
                    >
                  </span>
                </template>
              </v-list-item>
            </v-list>
          </v-menu>
        </div>
      </Teleport>
    </div>
    <button
      type="button"
      class="worktree-add"
      :class="{ 'worktree-add--measuring': useDropdown }"
      :aria-hidden="useDropdown || undefined"
      :tabindex="useDropdown ? -1 : 0"
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
.worktree-switcher--inline {
  height: 0;
  padding-top: 0;
  padding-bottom: 0;
  border-bottom: 0;
  overflow: hidden;
}
.worktree-dropdown {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  width: 100%;
}
.worktree-dropdown .worktree-switcher-label {
  margin-top: 0;
}
.worktree-dropdown .worktree-menu-button {
  flex: 1;
  min-width: 0;
}
.worktree-add--measuring {
  visibility: hidden;
  pointer-events: none;
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
/* 2026-11-08 (elecvoid243) dropdown-unify: the compact trigger mirrors
   .git-diff-sidebar-branch-mgmt-btn in GitDiffSidebar.vue, which stays the
   source of truth for this row (12px text, 3px/8px padding, 6px radius,
   1px theme border). Only the pill styling inherited from .worktree-tab
   is overridden here. */
.worktree-menu-button {
  width: 100%;
  max-width: 220px;
  border-color: rgba(var(--v-border-color), var(--v-border-opacity, 1));
  border-radius: 6px;
  padding: 3px 8px;
  gap: 4px;
  line-height: 1.2;
  color: rgb(var(--v-theme-on-surface));
}
.worktree-menu-button .worktree-tab-label {
  flex: 1;
}
/* Same list metrics as .git-diff-sidebar-branch-menu. Vuetify's compact
   two-line item is 56px tall by default, which is what made this menu read
   as a different component: 1px + 16px title + 12px path + 1px = 30px. */
.worktree-menu {
  max-height: 360px;
  overflow-y: auto;
  font-size: 12px;
}
.worktree-menu :deep(.v-list-item.v-list-item--one-line),
.worktree-menu :deep(.v-list-item.v-list-item--two-line) {
  min-height: 30px;
  padding-top: 1px;
  padding-bottom: 1px;
}
.worktree-menu :deep(.v-list-item-title) {
  font-size: 12px;
  line-height: 16px;
}
.worktree-menu :deep(.v-list-item-subtitle) {
  /* Path line: secondary to the 12px title, single line, clamped. */
  font-size: 10.5px;
  line-height: 12px;
  -webkit-line-clamp: 1;
}
.worktree-menu :deep(.v-list-item__content) {
  padding-inline: 8px;
}
/* Per-row actions follow the branch menu's inline icons: 60% opacity at
   rest, full on hover, error tint for removal. */
.worktree-menu-actions {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: 2px;
}
.worktree-menu :deep(.worktree-menu-action) {
  opacity: 0.6;
  transition:
    opacity 0.12s,
    color 0.12s;
}
.worktree-menu :deep(.worktree-menu-action:hover) {
  opacity: 1;
}
.worktree-menu :deep(.worktree-menu-action--danger:hover) {
  color: rgb(var(--v-theme-error));
}
.worktree-menu :deep(.worktree-menu-action.is-disabled) {
  opacity: 0.22;
  cursor: default;
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
