<!--
  Author: elecvoid243, 2026-10-25
  GoalMenuItem — "设定目标" entry inside the chat input's "+" menu.

  Discovers the kernel goal loop (/goal): without a standing goal the item
  opens the set-goal card (goal text + turn budget); with one active it
  opens the GoalSidebar drawer instead — the single home for status,
  budget, pause/clear. Setting a goal hits POST /chat/sessions/{id}/goal;
  the resulting state reaches the header badge and this item through the
  goal_state_changed system-stream push (useSessionGoal), so no local
  state is kept here.
-->
<template>
  <v-menu
    v-model="menuOpen"
    :close-on-content-click="false"
    location="end"
    offset="6"
    min-width="340"
    max-width="420"
    class="goal-menu"
  >
    <template v-slot:activator="{ props: activatorProps }">
      <v-list-item
        v-bind="{ ...activatorProps, onClick: handleActivatorClick }"
        class="styled-menu-item"
        rounded="md"
        :disabled="disabled || !sessionId"
        data-test="goal-menu-item"
      >
        <template v-slot:prepend>
          <v-icon icon="mdi-target" size="small"></v-icon>
        </template>
        <v-list-item-title data-test="goal-menu-label">{{
          menuLabel
        }}</v-list-item-title>
        <template v-slot:append>
          <span v-if="badge" class="goal-menu-item-trail" data-test="goal-menu-trail">
            <span class="goal-menu-item-dot" :class="`goal-menu-item-dot--${badge.status}`"></span>
            <span class="goal-menu-item-count">
              {{ badge.turnsUsed }}/{{ badge.maxTurns }}
            </span>
          </span>
        </template>
      </v-list-item>
    </template>

    <div class="goal-menu-card" data-test="goal-menu-card">
      <div class="goal-menu-card__header">
        <v-icon icon="mdi-target" size="16" class="goal-menu-card__icon"></v-icon>
        <span class="goal-menu-card__title">{{ tm("goal.popoverTitle") }}</span>
        <v-btn
          icon="mdi-close"
          size="x-small"
          variant="text"
          class="goal-menu-card__close"
          :aria-label="tm('goal.close')"
          data-test="goal-card-close"
          @click="menuOpen = false"
        ></v-btn>
      </div>

      <div class="goal-menu-card__desc">{{ tm("goal.popoverDesc") }}</div>

      <textarea
        v-model="goalText"
        class="goal-menu-card__textarea"
        rows="2"
        :placeholder="tm('goal.goalPlaceholder')"
        data-test="goal-textarea"
      ></textarea>

      <div class="goal-menu-card__budget">
        <span class="goal-menu-card__budget-label">
          {{ tm("goal.budgetLabel") }}
        </span>
        <span class="goal-menu-card__stepper">
          <button
            type="button"
            :aria-label="tm('goal.budgetDecrease')"
            :disabled="budget <= BUDGET_MIN"
            data-test="goal-budget-minus"
            @click="stepBudget(-1)"
          >
            −
          </button>
          <span class="goal-menu-card__budget-value">{{ budget }}</span>
          <button
            type="button"
            :aria-label="tm('goal.budgetIncrease')"
            :disabled="budget >= BUDGET_MAX"
            data-test="goal-budget-plus"
            @click="stepBudget(1)"
          >
            +
          </button>
        </span>
        <span class="goal-menu-card__budget-hint">
          {{ tm("goal.budgetHint") }}
        </span>
      </div>

      <div v-if="errorMessage" class="goal-menu-card__error" role="alert">
        {{ errorMessage }}
      </div>

      <div class="goal-menu-card__foot">
        <span class="goal-menu-card__cmd-hint">
          {{ tm("goal.cmdHint") }} <code>/goal set</code>
        </span>
        <v-btn
          color="primary"
          size="small"
          variant="tonal"
          rounded="md"
          :loading="setting"
          :disabled="!goalText.trim()"
          data-test="goal-start"
          @click="startGoal"
        >
          {{ tm("goal.start") }}
        </v-btn>
      </div>
    </div>
  </v-menu>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { chatApi } from "@/api/v1";
import { useChatHeaderStore } from "@/stores/chatHeader";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  sessionId?: string | null;
  disabled?: boolean;
}>();

const { tm } = useModuleI18n("features/chat");
const chatHeader = useChatHeaderStore();

const BUDGET_MIN = 5;
const BUDGET_MAX = 100;

const menuOpen = ref(false);
const goalText = ref("");
const budget = ref(20);
const budgetDirty = ref(false);
const setting = ref(false);
const errorMessage = ref("");

const badge = computed(() => chatHeader.goalBadge);

const menuLabel = computed(() =>
  badge.value
    ? `${tm("goal.menuLabel")} · ${tm(`goal.status_${badge.value.status}`)}`
    : tm("goal.menuSet"),
);

// Reset the form whenever the card opens so a re-open starts clean.
watch(menuOpen, (open) => {
  if (open) {
    goalText.value = "";
    budget.value = 20;
    budgetDirty.value = false;
    errorMessage.value = "";
  }
});

function stepBudget(delta: number) {
  budget.value = Math.min(BUDGET_MAX, Math.max(BUDGET_MIN, budget.value + delta));
  budgetDirty.value = true;
}

/**
 * Activator click: with a standing goal open the GoalSidebar (status,
 * budget, pause/clear live there); without one open the set-goal card.
 *
 * Overrides the v-menu activator's own onClick via the props spread in
 * the template, so the popover only opens for the no-goal case.
 */
function handleActivatorClick() {
  if (badge.value) {
    menuOpen.value = false;
    chatHeader.SET_GOAL_SIDEBAR_OPEN(true);
    return;
  }
  menuOpen.value = true;
}

async function startGoal() {
  const text = goalText.value.trim();
  if (!text || !props.sessionId || setting.value) return;
  setting.value = true;
  errorMessage.value = "";
  try {
    await chatApi.setSessionGoal(
      props.sessionId,
      text,
      budgetDirty.value ? budget.value : undefined,
    );
    menuOpen.value = false;
    // State converges via the goal_state_changed push (header badge +
    // this item's label); no local state to update.
  } catch (error) {
    console.error("Failed to set session goal:", error);
    errorMessage.value = tm("goal.setFailed");
  } finally {
    setting.value = false;
  }
}
</script>

<style scoped>
.goal-menu-item-trail {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.goal-menu-item-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: rgb(var(--v-theme-success));
}

.goal-menu-item-dot--paused,
.goal-menu-item-dot--blocked {
  background: rgb(var(--v-theme-warning));
}

.goal-menu-item-dot--done {
  background: rgba(var(--v-theme-on-surface), 0.4);
}

.goal-menu-item-count {
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-primary), 0.9);
}

.goal-menu-card {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 12px;
  background: rgba(var(--v-theme-surface), 0.98);
  backdrop-filter: blur(10px);
  box-shadow: var(--astrbot-menu-shadow, 0 12px 28px rgba(0, 0, 0, 0.08));
  padding: 12px 14px;
}

.v-theme--PurpleThemeDark .goal-menu-card {
  border-color: rgba(255, 255, 255, 0.12);
  background: rgba(45, 45, 45, 0.98);
}

.goal-menu-card__header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.goal-menu-card__icon {
  color: rgba(var(--v-theme-primary), 0.9);
}

.goal-menu-card__title {
  font-size: 13.5px;
  font-weight: 500;
}

.goal-menu-card__close {
  margin-left: auto;
}

.goal-menu-card__desc {
  font-size: 12px;
  line-height: 1.5;
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-bottom: 10px;
}

.goal-menu-card__textarea {
  width: 100%;
  box-sizing: border-box;
  resize: none;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 10px;
  background: rgba(var(--v-theme-on-surface), 0.04);
  color: rgba(var(--v-theme-on-surface), 0.9);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.5;
  padding: 9px 11px;
  min-height: 58px;
  outline: none;
}

.goal-menu-card__textarea:focus {
  border-color: rgba(var(--v-theme-primary), 0.7);
}

.goal-menu-card__budget {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}

.goal-menu-card__budget-label {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.goal-menu-card__stepper {
  display: inline-flex;
  align-items: center;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 8px;
  overflow: hidden;
}

.goal-menu-card__stepper button {
  width: 24px;
  height: 24px;
  border: 0;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.6);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
}

.goal-menu-card__stepper button:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.goal-menu-card__stepper button:disabled {
  opacity: 0.35;
  cursor: default;
}

.goal-menu-card__budget-value {
  min-width: 26px;
  text-align: center;
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}

.goal-menu-card__budget-hint {
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.goal-menu-card__error {
  margin-top: 8px;
  font-size: 12px;
  color: rgb(var(--v-theme-error));
}

.goal-menu-card__foot {
  display: flex;
  align-items: center;
  margin-top: 12px;
}

.goal-menu-card__cmd-hint {
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.goal-menu-card__cmd-hint code {
  font-size: 11px;
  padding: 1px 5px;
  border-radius: 5px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}
</style>
