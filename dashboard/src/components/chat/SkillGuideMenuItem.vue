<!--
  Author: elecvoid243, 2026-08-16
  Plugin: astrbot_plugin_skill_guide (routes /skill-guide/*, see plugin README)

  SkillGuideMenuItem — "手动加载 Skill" entry inside the chat input's
  "+" menu.

  The skill list is a COMPLETELY SEPARATE v-menu: its activator is the
  "+" menu item, but its content (a plain self-drawn card) is teleported
  to <body> like any other menu — the two menus never share DOM or layout.

  Interaction:
    - The activator is a regular "+" menu item (v-list-item).
    - Hovering it opens the skill menu. Open/close is self-managed via
      v-model: opening happens on hover, but the menu does NOT close
      when the pointer leaves — it stays open until an explicit dismiss
      (outside click, Escape, clicking the activator again, or the
      card's corner close button), so the list remains readable while
      the pointer is elsewhere.
    - Un-selecting ONE skill keeps the others (clear + re-queue).
    - The card has hard caps: max-height + scroll for many skills,
      max-width with ellipsis-truncated lines so it never grows with the
      descriptions.

  Queuing semantics (from the plugin README): clicking a skill POSTs
  /skill-guide/load for a ONE-SHOT nudge — the plugin injects a guidance
  prompt into the NEXT LLM request of the session and drains the queue.
  Persona and the skill's global active state are never touched.

  Show-all mode (toggle row, default off): additionally list EVERY skill
  in data/skills (core GET /skills); skills the persona does not mount
  render gray but queue the same way (the nudge only needs the SKILL.md
  path). The preference is persisted in localStorage.
-->
<template>
  <v-menu
    v-model="open"
    :close-on-content-click="false"
    location="end"
    offset="6"
    min-width="300"
    max-width="340"
    class="skill-guide-menu"
  >
    <template v-slot:activator="{ props: activatorProps }">
      <v-list-item
        v-bind="activatorProps"
        class="styled-menu-item"
        rounded="md"
        :disabled="disabled || !sessionId"
        data-test="skill-guide-menu-item"
        @mouseenter="handleEnter"
      >
        <template v-slot:prepend>
          <v-icon icon="mdi-lightbulb-on-outline" size="small"></v-icon>
        </template>
        <v-list-item-title>{{
          tm("input.skillGuide.button")
        }}</v-list-item-title>
      </v-list-item>
    </template>

    <div class="skill-guide-menu-card" data-test="skill-guide-menu-card">
      <div class="skill-guide-menu-card__header">
        <v-icon icon="mdi-lightbulb-on-outline" size="14"></v-icon>
        <span class="skill-guide-menu-card__title">{{
          tm("input.skillGuide.menuTitle")
        }}</span>
        <div class="skill-guide-menu-card__header-right">
          <span
            v-if="queued.length > 0"
            class="skill-guide-menu-card__count"
            data-test="skill-guide-pop-count"
            >{{ queued.length }}</span
          >
          <button
            type="button"
            class="skill-guide-menu-card__close"
            :aria-label="tm('input.skillGuide.close')"
            :title="tm('input.skillGuide.close')"
            data-test="skill-guide-close"
            @click="open = false"
          >
            <v-icon icon="mdi-close" size="14" />
          </button>
        </div>
      </div>

      <!--
        Show-all toggle (persisted): on = list EVERY skill in data/skills
        (core GET /skills). Skills the persona does NOT mount render gray
        but stay clickable — a manual load only carries the SKILL.md path
        hint, so unmounted skills work the same.
      -->
      <div class="skill-guide-menu-card__showall">
        <span
          class="skill-guide-menu-card__showall-label"
          :title="tm('input.skillGuide.showAllTitle')"
          >{{ tm("input.skillGuide.showAll") }}</span
        >
        <v-switch
          :model-value="showAll"
          :aria-label="tm('input.skillGuide.showAllTitle')"
          :title="tm('input.skillGuide.showAllTitle')"
          class="skill-guide-menu-card__switch"
          color="primary"
          density="compact"
          hide-details
          inset
          data-test="skill-guide-show-all"
          @update:model-value="guide.setShowAll"
        />
      </div>

      <!-- 2026-09-01 (elecvoid243): search filter for the skill list. -->
      <div class="skill-guide-menu-card__search">
        <v-icon
          icon="mdi-magnify"
          size="14"
          class="skill-guide-menu-card__search-icon"
        />
        <input
          v-model="searchQuery"
          type="text"
          class="skill-guide-menu-card__search-input"
          :placeholder="tm('input.skillGuide.searchPlaceholder')"
          :aria-label="tm('input.skillGuide.searchPlaceholder')"
          data-test="skill-guide-search"
        />
        <button
          v-if="searchQuery"
          type="button"
          class="skill-guide-menu-card__search-clear"
          :aria-label="tm('input.skillGuide.searchClear')"
          :title="tm('input.skillGuide.searchClear')"
          data-test="skill-guide-search-clear"
          @click="searchQuery = ''"
        >
          <v-icon icon="mdi-close-circle" size="14" />
        </button>
      </div>

      <div class="skill-guide-menu-card__list">
        <div
          v-if="listLoading"
          class="skill-guide-menu-card__hint"
          data-test="skill-guide-loading"
        >
          {{ tm("input.skillGuide.loading") }}
        </div>
        <div
          v-else-if="listFailed"
          class="skill-guide-menu-card__hint"
          data-test="skill-guide-load-failed"
        >
          <v-icon icon="mdi-alert-circle" size="14"></v-icon>
          {{ tm("input.skillGuide.loadFailed") }}
        </div>
        <div
          v-else-if="filteredSkills.length === 0"
          class="skill-guide-menu-card__hint"
          :data-test="
            displaySkills.length === 0
              ? 'skill-guide-empty'
              : 'skill-guide-no-match'
          "
        >
          {{
            displaySkills.length === 0
              ? tm("input.skillGuide.empty")
              : tm("input.skillGuide.noMatch")
          }}
        </div>

        <button
          v-for="skill in filteredSkills"
          v-show="!listLoading && !listFailed"
          :key="skill.name"
          type="button"
          class="skill-guide-menu-card__item"
          :class="{
            'skill-guide-menu-card__item--queued': isQueued(skill.name),
            'skill-guide-menu-card__item--unmounted': !skill.mounted,
          }"
          :data-test="`skill-guide-item-${skill.name}`"
          :title="skill.description || skill.name"
          @click="handleToggle(skill)"
        >
          <v-icon
            :icon="
              isQueued(skill.name)
                ? 'mdi-check-circle'
                : 'mdi-lightbulb-on-outline'
            "
            size="14"
            class="skill-guide-menu-card__item-icon"
          ></v-icon>
          <span class="skill-guide-menu-card__item-body">
            <span class="skill-guide-menu-card__item-name-row">
              <span class="skill-guide-menu-card__item-name">{{
                skill.name
              }}</span>
              <span
                v-if="isQueued(skill.name)"
                class="skill-guide-menu-card__item-queued"
                >{{ tm("input.skillGuide.queued") }}</span
              >
            </span>
            <span
              v-if="skill.description"
              class="skill-guide-menu-card__item-desc"
              >{{ skill.description }}</span
            >
          </span>
        </button>

        <button
          v-if="queued.length > 0"
          type="button"
          class="skill-guide-menu-card__clear"
          data-test="skill-guide-clear-all"
          @click="handleClearAll"
        >
          <v-icon icon="mdi-close-circle" size="14"></v-icon>
          <span>{{ tm("input.skillGuide.clearAll") }}</span>
        </button>
      </div>
    </div>
  </v-menu>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import {
  useSkillGuide,
  type SkillGuideSkill,
} from "@/composables/useSkillGuide";

const props = withDefaults(
  defineProps<{
    sessionId?: string | null;
    isGroup?: boolean;
    disabled?: boolean;
  }>(),
  {
    sessionId: null,
    isGroup: false,
    disabled: false,
  },
);

const { tm } = useModuleI18n("features/chat");

// Singleton state shared with ChatInput's pending-badge row — reading the
// same refs means the skill menu's ✓ marks and the badges can never diverge.
const guide = useSkillGuide();
const {
  displaySkills,
  queued,
  loading,
  loadFailed,
  showAll,
  allSkillsLoading,
  allSkillsFailed,
} = guide;

// The list renders only when BOTH sources are ready: the plugin's active
// list (loading/loadFailed) and, in show-all mode, the core /skills list.
// The plugin states take precedence — without them the whole entry is gone.
const listLoading = computed(
  () => loading.value || (showAll.value && allSkillsLoading.value),
);
const listFailed = computed(
  () =>
    !loading.value &&
    (loadFailed.value || (showAll.value && allSkillsFailed.value)),
);

// Open/close is self-managed (v-model on the v-menu): hovering the item
// opens it, and it STAYS open when the pointer leaves — dismissal is
// explicit only (outside click / Escape via Vuetify's overlay defaults,
// clicking the activator again, or the card's corner close button), so
// the list remains readable while the pointer is elsewhere.
const open = ref(false);

function handleEnter(): void {
  if (!open.value) {
    open.value = true;
    // 2026-09-01 (elecvoid243): re-fetch the session's skill list every
    // time the menu opens (close→open transition only; a hover flit
    // inside the same open cycle does not refetch). refresh() soft-fails,
    // so an unreachable plugin renders the inline load-failed state.
    void guide.refresh();
  }
}

// 2026-09-01 (elecvoid243): search filter over the rendered skill rows —
// matches skill name or description, case-insensitive.
const searchQuery = ref("");
const filteredSkills = computed(() => {
  const q = searchQuery.value.trim().toLowerCase();
  if (!q) return displaySkills.value;
  return displaySkills.value.filter(
    (skill) =>
      skill.name.toLowerCase().includes(q) ||
      skill.description.toLowerCase().includes(q),
  );
});

function isQueued(name: string): boolean {
  return queued.value.includes(name);
}

async function handleToggle(skill: SkillGuideSkill): Promise<void> {
  await guide.toggleSkill(skill.name);
}

async function handleClearAll(): Promise<void> {
  await guide.clearAll();
}
</script>

<!--
  NOT scoped on purpose: v-menu teleports its content to <body>, so
  scoped attribute selectors would never match. Class names are
  prefixed (skill-guide-*) to stay collision-free, mirroring the
  global style block of StyledMenu.vue.

  The card is a plain self-drawn div (no v-card): tests
  bind directly to it. Size caps (spec): the list scrolls beyond
  max-height, and every text line is single-line ellipsis so the menu
  width never grows with the description length.
-->
<style>
.skill-guide-menu-card {
  min-width: 300px;
  max-width: 340px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 10px;
  background: rgba(var(--v-theme-surface), 0.98);
  backdrop-filter: blur(10px);
  box-shadow: var(--astrbot-menu-shadow, 0 12px 28px rgba(0, 0, 0, 0.08));
}

.v-theme--PurpleThemeDark .skill-guide-menu-card {
  border-color: rgba(255, 255, 255, 0.12);
  background: rgba(45, 45, 45, 0.98);
}

.skill-guide-menu-card__header {
  display: flex;
  align-items: center;
  gap: 6px;
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  background: inherit;
}

.skill-guide-menu-card__title {
  font-size: 12px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.9);
}

/* Right side of the header: queued-count badge + corner close button,
   kept flush against the card's top-right edge. */
.skill-guide-menu-card__header-right {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}

.skill-guide-menu-card__count {
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: rgba(var(--v-theme-primary), 0.9);
  color: rgb(var(--v-theme-on-primary));
  font-size: 10px;
  line-height: 16px;
  text-align: center;
}

.skill-guide-menu-card__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.4);
  cursor: pointer;
}

.skill-guide-menu-card__close:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgba(var(--v-theme-on-surface), 0.8);
}

/* Show-all toggle row: slim, sits between the header and the list. */
.skill-guide-menu-card__showall {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 2px 12px 4px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

/* 2026-09-01 search row: sits under show-all, above the list. */
.skill-guide-menu-card__search {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 8px 2px;
  padding: 0 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.skill-guide-menu-card__search-icon {
  flex: none;
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.skill-guide-menu-card__search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-size: 12px;
  line-height: 26px;
}

.skill-guide-menu-card__search-input::placeholder {
  color: rgba(var(--v-theme-on-surface), 0.4);
}

.skill-guide-menu-card__search-clear {
  display: inline-flex;
  align-items: center;
  flex: none;
  padding: 0;
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.4);
  cursor: pointer;
}

.skill-guide-menu-card__search-clear:hover {
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.skill-guide-menu-card__showall-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10.5px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  cursor: default;
}

/* Compact switch: shrink Vuetify's default track/handle margins so the
   row stays within the header's visual weight. */
/* One size smaller than the stock inset switch (32px track): shrink the
   layout box via Vuetify's own CSS vars (avoids specificity fights) and
   scale the pill itself — the thumb's absolute-position slide geometry
   must stay untouched, so uniform scaling is the safe way down. */
.skill-guide-menu-card__switch {
  flex: none;
  --v-input-control-height: 26px;
  --v-input-padding-top: 0px;
  transform: scale(0.75);
  transform-origin: center;
}

/* Scroll past max-height when the session exposes many skills. */
.skill-guide-menu-card__list {
  max-height: min(48vh, 320px);
  overflow-y: auto;
  padding: 4px;
}

.skill-guide-menu-card__hint {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.5;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

/* Compact rows; every text line is single-line + ellipsis so the menu
   width never grows with the description length. */
.skill-guide-menu-card__item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  text-align: left;
  cursor: pointer;
}

.skill-guide-menu-card__item:hover {
  background: rgba(var(--v-theme-primary), 0.08);
}

.skill-guide-menu-card__item--queued {
  background: rgba(var(--v-theme-primary), 0.12);
}

.skill-guide-menu-card__item-icon {
  flex: none;
  margin-top: 2px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.skill-guide-menu-card__item--queued .skill-guide-menu-card__item-icon {
  color: rgb(var(--v-theme-primary));
}

.skill-guide-menu-card__item-body {
  flex: 1;
  min-width: 0;
}

.skill-guide-menu-card__item-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.skill-guide-menu-card__item-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.5;
}

.skill-guide-menu-card__item-queued {
  flex: none;
  font-size: 9.5px;
  font-weight: 500;
  color: rgb(var(--v-theme-primary));
  border: 1px solid rgba(var(--v-theme-primary), 0.35);
  border-radius: 5px;
  padding: 0 4px;
  line-height: 14px;
  white-space: nowrap;
}

.skill-guide-menu-card__item-desc {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10.5px;
  line-height: 1.4;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

/* Unmounted skills: gray but still clickable (queued state keeps its
   highlight so a loaded unmounted skill stays recognizable). */
.skill-guide-menu-card__item--unmounted .skill-guide-menu-card__item-name,
.skill-guide-menu-card__item--unmounted .skill-guide-menu-card__item-desc {
  color: rgba(var(--v-theme-on-surface), 0.38);
}

.skill-guide-menu-card__item--unmounted .skill-guide-menu-card__item-icon {
  color: rgba(var(--v-theme-on-surface), 0.3);
}

.skill-guide-menu-card__clear {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  margin-top: 2px;
  padding: 6px 8px;
  border: none;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 0 0 8px 8px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.65);
  font-size: 11px;
  cursor: pointer;
}

.skill-guide-menu-card__clear:hover {
  background: rgba(var(--v-theme-error), 0.08);
  color: rgb(var(--v-theme-error));
}
</style>
