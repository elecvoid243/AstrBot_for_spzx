<!--
  Author: elecvoid243, 2026-11-07

  GoalBar — the active session's standing-goal strip above the composer.
  It replaces the old GoalSidebar drawer as the primary surface: the goal
  text stays visible while the turn loop runs, and pause / edit / clear are
  one click away.

  State is fed by Chat.vue from useSessionGoal's per-session cache. Mutations
  go through the REST endpoints and come back as a goal_state_changed push,
  so this component keeps no goal state of its own. The edit dialog is shared
  with the app-bar badge and the "+" menu through chatHeader.goalEditOpen.
-->
<template>
  <div
    v-if="goal"
    class="goal-bar"
    :class="[{ 'is-dark': isDark }, `goal-bar--${goal.status}`]"
  >
    <v-icon size="15" class="goal-bar__icon">mdi-target</v-icon>
    <span class="goal-bar__label">{{ barLabel }}</span>
    <span class="goal-bar__text" :title="goal.goal">{{ goal.goal }}</span>
    <span class="goal-bar__turns">
      {{
        tm("goal.turnsLine", { used: goal.turns_used, max: goal.max_turns })
      }}
    </span>
    <div v-if="!readOnly" class="goal-bar__actions">
      <button
        v-if="goal.status === 'active'"
        type="button"
        class="goal-bar__action"
        :title="tm('goal.pause')"
        :aria-label="tm('goal.pause')"
        :disabled="busy"
        @click="act('pause')"
      >
        <v-icon size="15">mdi-pause-circle-outline</v-icon>
      </button>
      <button
        v-else-if="canResume"
        type="button"
        class="goal-bar__action"
        :title="tm('goal.resume')"
        :aria-label="tm('goal.resume')"
        :disabled="busy"
        @click="act('resume')"
      >
        <v-icon size="15">mdi-play-circle-outline</v-icon>
      </button>
      <button
        type="button"
        class="goal-bar__action"
        :title="tm('goal.editTitle')"
        :aria-label="tm('goal.editTitle')"
        @click="openEdit"
      >
        <v-icon size="15">mdi-pencil-outline</v-icon>
      </button>
      <button
        type="button"
        class="goal-bar__action goal-bar__action--danger"
        :title="tm('goal.clear')"
        :aria-label="tm('goal.clear')"
        :disabled="busy"
        @click="act('clear')"
      >
        <v-icon size="15">mdi-trash-can-outline</v-icon>
      </button>
    </div>

    <v-dialog v-if="!readOnly" v-model="editOpen" max-width="460">
      <v-card class="goal-edit-card">
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">
          {{ tm("goal.editTitle") }}
        </v-card-title>
        <v-card-text>
          <textarea
            v-model="goalDraft"
            class="goal-edit-textarea"
            rows="3"
            :placeholder="tm('goal.goalPlaceholder')"
          ></textarea>
          <div class="goal-edit-budget">
            <span>{{ tm("goal.budgetLabel") }}</span>
            <span class="goal-edit-stepper">
              <button
                type="button"
                :aria-label="tm('goal.budgetDecrease')"
                :disabled="budgetDraft <= BUDGET_MIN"
                @click="stepBudget(-1)"
              >
                −
              </button>
              <span>{{ budgetDraft }}</span>
              <button
                type="button"
                :aria-label="tm('goal.budgetIncrease')"
                :disabled="budgetDraft >= BUDGET_MAX"
                @click="stepBudget(1)"
              >
                +
              </button>
            </span>
            <span class="goal-edit-budget__hint">
              {{ tm("goal.budgetHint") }}
            </span>
          </div>
          <div v-if="errorMessage" class="goal-edit-error" role="alert">
            {{ errorMessage }}
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="editOpen = false">
            {{ t("core.common.cancel") }}
          </v-btn>
          <v-btn
            color="primary"
            variant="tonal"
            :loading="saving"
            :disabled="!goalDraft.trim()"
            @click="saveGoal"
          >
            {{ t("core.common.save") }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { chatApi } from "@/api/v1";
import type { SessionGoalState } from "@/api/v1";
import { useI18n, useModuleI18n } from "@/i18n/composables";
import { useChatHeaderStore } from "@/stores/chatHeader";

const props = withDefaults(
  defineProps<{
    goal: SessionGoalState;
    sessionId: string;
    isDark?: boolean;
    readOnly?: boolean;
  }>(),
  { isDark: false },
);

const { t } = useI18n();
const { tm } = useModuleI18n("features/chat");
const chatHeader = useChatHeaderStore();

const BUDGET_MIN = 5;
const BUDGET_MAX = 100;

const barLabel = computed(() => tm(`goal.bar_${props.goal.status}`));
const canResume = computed(
  () => props.goal.status === "paused" || props.goal.status === "blocked",
);

// Actions are fire-and-forget: the resulting state arrives through the
// goal_state_changed system-stream push that updates useSessionGoal.
const busy = ref(false);

async function act(action: "pause" | "resume" | "clear") {
  if (!props.sessionId || busy.value) return;
  busy.value = true;
  try {
    await chatApi.applyGoalAction(props.sessionId, action);
  } catch (error) {
    console.error("Failed to apply goal action:", error);
  } finally {
    busy.value = false;
  }
}

const editOpen = computed({
  get: () => chatHeader.goalEditOpen,
  set: (open: boolean) => chatHeader.SET_GOAL_EDIT_OPEN(open),
});

function openEdit() {
  editOpen.value = true;
}

const goalDraft = ref("");
const budgetDraft = ref(20);
const saving = ref(false);
const errorMessage = ref("");

// Seed the form on each open so a cancelled edit leaves no draft behind.
watch(editOpen, (open) => {
  if (!open) return;
  goalDraft.value = props.goal.goal;
  budgetDraft.value = props.goal.max_turns;
  errorMessage.value = "";
});

function stepBudget(delta: number) {
  budgetDraft.value = Math.min(
    BUDGET_MAX,
    Math.max(BUDGET_MIN, budgetDraft.value + delta),
  );
}

async function saveGoal() {
  const text = goalDraft.value.trim();
  if (!text || !props.sessionId || saving.value) return;
  saving.value = true;
  errorMessage.value = "";
  try {
    await chatApi.setSessionGoal(props.sessionId, text, budgetDraft.value);
    editOpen.value = false;
  } catch (error) {
    console.error("Failed to update session goal:", error);
    errorMessage.value = tm("goal.setFailed");
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.goal-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  width: var(--chat-content-width, 80%);
  max-width: var(--chat-content-max-width, 960px);
  margin: 6px auto;
  padding: 5px 6px 5px 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  font-size: 13px;
  min-width: 0;
}

.goal-bar.is-dark {
  background: #2d2d2d;
  border-color: rgba(255, 255, 255, 0.12);
  box-shadow: none;
}

.goal-bar__icon {
  flex: 0 0 auto;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.goal-bar__label {
  flex: 0 0 auto;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.92);
}

.goal-bar__text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(var(--v-theme-on-surface), 0.68);
}

.goal-bar__turns {
  flex: 0 0 auto;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.goal-bar__actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 2px;
}

.goal-bar__action {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.58);
  cursor: pointer;
  transition:
    background-color 0.14s ease,
    color 0.14s ease;
}

.goal-bar__action:hover {
  background: rgba(var(--v-theme-on-surface), 0.07);
  color: rgba(var(--v-theme-on-surface), 0.92);
}

.goal-bar__action:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

.goal-bar__action:disabled {
  opacity: 0.5;
  cursor: default;
}

.goal-bar__action--danger:hover {
  background: rgba(var(--v-theme-error), 0.1);
  color: rgb(var(--v-theme-error));
}

@media (max-width: 600px) {
  .goal-bar__label,
  .goal-bar__turns {
    display: none;
  }
}

.goal-edit-textarea {
  width: 100%;
  box-sizing: border-box;
  min-height: 74px;
  padding: 8px 10px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.16);
  border-radius: 8px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
}

.goal-edit-textarea:focus {
  outline: 2px solid rgba(var(--v-theme-primary), 0.5);
  outline-offset: -1px;
}

.goal-edit-budget {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.65);
}

.goal-edit-stepper {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.16);
  border-radius: 7px;
}

.goal-edit-stepper button {
  width: 24px;
  height: 24px;
  border: 0;
  background: transparent;
  color: inherit;
  font-size: 14px;
  cursor: pointer;
}

.goal-edit-stepper button:disabled {
  opacity: 0.4;
  cursor: default;
}

.goal-edit-stepper span {
  min-width: 28px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.goal-edit-budget__hint {
  margin-left: auto;
  font-size: 11px;
  opacity: 0.7;
}

.goal-edit-error {
  margin-top: 10px;
  font-size: 12px;
  color: rgb(var(--v-theme-error));
}
</style>
