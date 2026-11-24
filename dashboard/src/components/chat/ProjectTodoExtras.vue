<template>
  <section class="extras-section">
    <div class="extras-heading">
      <h4><Tags :size="17" />{{ tm("board.labels") }}</h4>
      <button
        type="button"
        class="text-action"
        :disabled="labels.length >= 32"
        @click="addLabel"
      >
        <Plus :size="15" />{{ tm("board.addLabel") }}
      </button>
    </div>
    <div v-for="label in labels" :key="label.id" class="extra-row label-row">
      <v-menu :close-on-content-click="true">
        <template #activator="{ props: button }">
          <button
            v-bind="button"
            type="button"
            class="swatch-picker"
            :style="{ '--swatch': labelColors[label.color] }"
            :aria-label="
              tm('board.color') + ' · ' + tm('board.colors.' + label.color)
            "
          >
            <i /><ChevronDown :size="13" />
          </button>
        </template>
        <v-card class="palette pa-2">
          <button
            v-for="(value, color) in labelColors"
            :key="color"
            type="button"
            :aria-pressed="label.color === color"
            :aria-label="tm('board.colors.' + color)"
            :title="tm('board.colors.' + color)"
            :style="{ '--swatch': value }"
            @click="
              emit(
                'update:labels',
                labels.map((l) => (l.id === label.id ? { ...l, color } : l)),
              )
            "
          >
            <span><Check v-if="label.color === color" :size="15" /></span>
          </button>
        </v-card>
      </v-menu>
      <input
        :id="'label-' + label.id"
        :value="label.name"
        maxlength="40"
        :placeholder="tm('board.labelName')"
        :aria-label="tm('board.labelName')"
        @input="
          emit(
            'update:labels',
            labels.map((l) =>
              l.id === label.id
                ? { ...l, name: ($event.target as HTMLInputElement).value }
                : l,
            ),
          )
        "
      />
      <button
        type="button"
        class="row-action"
        :aria-label="tm('board.delete') + ' ' + label.name"
        @click="
          emit(
            'update:labels',
            labels.filter((l) => l.id !== label.id),
          )
        "
      >
        <X :size="14" />
      </button>
    </div>
  </section>
  <section class="extras-section">
    <div class="extras-heading">
      <h4>
        <ListChecks :size="17" />{{ tm("board.checklist") }}
        <small v-if="checklist.length"
          >{{ completed }} / {{ checklist.length }}</small
        >
      </h4>
      <button
        type="button"
        class="text-action"
        :disabled="checklist.length >= 200"
        @click="addItem"
      >
        <Plus :size="15" />{{ tm("board.addItem") }}
      </button>
    </div>
    <progress
      v-if="checklist.length"
      :max="checklist.length"
      :value="completed"
      :aria-label="tm('board.checklist')"
    />
    <div
      v-for="(item, index) in checklist"
      :key="item.id"
      class="extra-row"
      :class="{ checked: item.done }"
    >
      <input
        type="checkbox"
        :checked="item.done"
        :aria-label="tm('board.done') + ' ' + item.title"
        @change="
          emit(
            'update:checklist',
            checklist.map((t) =>
              t.id === item.id ? { ...t, done: !t.done } : t,
            ),
          )
        "
      />
      <input
        :id="'check-' + item.id"
        :value="item.title"
        maxlength="200"
        :placeholder="tm('board.itemName')"
        :aria-label="tm('board.itemName')"
        @input="
          emit(
            'update:checklist',
            checklist.map((t) =>
              t.id === item.id
                ? { ...t, title: ($event.target as HTMLInputElement).value }
                : t,
            ),
          )
        "
        @keydown.enter="
          !$event.isComposing &&
            $event.keyCode !== 229 &&
            item.title.trim() &&
            checklist.length < 200 &&
            addItem()
        "
      />
      <button
        class="row-action"
        type="button"
        :disabled="index === 0"
        :aria-label="tm('board.moveUp')"
        @click="shift(index, -1)"
      >
        <ChevronUp :size="14" />
      </button>
      <button
        class="row-action"
        type="button"
        :disabled="index === checklist.length - 1"
        :aria-label="tm('board.moveDown')"
        @click="shift(index, 1)"
      >
        <ChevronDown :size="14" />
      </button>
      <button
        class="row-action"
        type="button"
        :aria-label="tm('board.delete') + ' ' + item.title"
        @click="
          emit(
            'update:checklist',
            checklist.filter((t) => t.id !== item.id),
          )
        "
      >
        <X :size="14" />
      </button>
    </div>
  </section>
</template>
<script lang="ts">
export const labelColors = {
  blue: "#387fd2",
  teal: "#178d91",
  green: "#319452",
  amber: "#bd8d16",
  orange: "#c76b2d",
  red: "#c9555e",
  purple: "#8d60c2",
  slate: "#66768e",
} as const;
export interface TodoLabel {
  id: string;
  name: string;
  color: keyof typeof labelColors;
}
export interface TodoCheck {
  id: string;
  title: string;
  done: boolean;
}
</script>
<script setup lang="ts">
import { computed, nextTick } from "vue";
import {
  Check,
  ChevronDown,
  ChevronUp,
  ListChecks,
  Plus,
  Tags,
  X,
} from "@lucide/vue";
import { useModuleI18n } from "@/i18n/composables";
const props = defineProps<{
  labels: TodoLabel[];
  checklist: TodoCheck[];
  newId: () => string;
}>();
const emit = defineEmits<{
  "update:labels": [TodoLabel[]];
  "update:checklist": [TodoCheck[]];
}>();
const { tm } = useModuleI18n("features/chat");
const completed = computed(() => props.checklist.filter((t) => t.done).length);
async function addLabel() {
  const id = props.newId();
  emit("update:labels", [...props.labels, { id, name: "", color: "blue" }]);
  await nextTick();
  document.getElementById("label-" + id)?.focus();
}
async function addItem() {
  const id = props.newId();
  emit("update:checklist", [
    ...props.checklist,
    { id, title: "", done: false },
  ]);
  await nextTick();
  document.getElementById("check-" + id)?.focus();
}
function shift(index: number, delta: number) {
  const next = [...props.checklist];
  const [item] = next.splice(index, 1);
  next.splice(index + delta, 0, item);
  emit("update:checklist", next);
}
</script>
<style scoped>
.extras-section {
  border-top: 1px solid var(--sp-chip-border);
  padding-top: 12px;
  display: grid;
  gap: 6px;
}
.extras-heading,
.extras-heading h4,
.text-action,
.extra-row,
.row-action,
.swatch-picker {
  display: flex;
  align-items: center;
  gap: 6px;
}
.extras-heading {
  justify-content: space-between;
}
.extras-heading h4 {
  font-size: 14px;
  font-weight: 650;
  min-width: 0;
}
.extras-heading h4 svg {
  color: var(--sp-text-muted);
}
.extras-heading small {
  font-weight: 500;
  color: var(--sp-text-muted);
  font-variant-numeric: tabular-nums;
}
.text-action {
  font-size: 12px;
  color: rgb(var(--v-theme-primary));
  padding: 4px 6px;
  border-radius: 6px;
}
.extra-row {
  min-width: 0;
  background: var(--sp-chip-hover-bg);
  border-radius: 8px;
  padding: 3px 6px;
}
.extra-row input:not([type="checkbox"]) {
  flex: 1;
  min-width: 0;
  padding: 5px 4px;
  font-size: 14px;
  border-radius: 4px;
}
input[type="checkbox"] {
  width: 16px;
  height: 16px;
  margin: 0 3px;
  accent-color: rgb(var(--v-theme-primary));
}
.checked input:not([type="checkbox"]) {
  text-decoration: line-through;
  color: var(--sp-text-muted);
}
.row-action,
.swatch-picker {
  min-width: 27px;
  min-height: 28px;
  justify-content: center;
  border-radius: 6px;
  color: var(--sp-text-muted);
}
button:hover {
  background: var(--sp-chip-hover-bg);
}
button:disabled {
  opacity: 0.32;
}
.swatch-picker i,
.palette span {
  background: var(--swatch);
  border-radius: 5px;
  display: grid;
  place-items: center;
  width: 22px;
  height: 18px;
}
.palette {
  display: grid;
  grid-template-columns: repeat(4, 32px);
}
.palette button {
  padding: 4px;
  border-radius: 6px;
}
.palette span {
  width: 24px;
  height: 24px;
  color: white;
}
progress {
  width: 100%;
  height: 5px;
  accent-color: rgb(var(--v-theme-primary));
  margin-bottom: 4px;
}
:is(button, input):focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}
@media (pointer: coarse) {
  .row-action,
  .text-action,
  .swatch-picker {
    min-height: 40px;
    min-width: 36px;
  }
  .extra-row input:not([type="checkbox"]) {
    font-size: 16px;
  }
}
</style>
