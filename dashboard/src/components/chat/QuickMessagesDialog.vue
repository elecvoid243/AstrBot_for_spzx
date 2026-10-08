<!--
  QuickMessagesDialog — editor for the ChatUI click-to-send phrases.

  Only content is editable (no titles): the menu truncates long text and
  reveals it on hover, so a title would just be a second thing to keep in
  sync. Reordering is done with the up/down buttons — a drag handle was
  considered and dropped, since a 24-item list does not earn the extra
  machinery. The draft is handed to ChatInput on save, which persists it
  through /api/chat/ui-settings/quick-messages.
-->
<template>
  <v-dialog
    :model-value="modelValue"
    max-width="560"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card class="quick-messages-dialog">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("input.editQuickMessages") }}
      </v-card-title>
      <!-- Plain div (not v-card-subtitle, which is nowrap/ellipsis) so the
           hint wraps fully across lines. -->
      <div class="quick-messages-hint pa-4 pt-2 pb-0 pl-6">
        {{ tm("input.quickMessagesHint") }}
      </div>

      <v-card-text class="pa-4">
        <div
          v-for="(item, index) in localItems"
          :key="item.id"
          class="quick-message-row"
        >
          <v-textarea
            v-model="item.content"
            rows="1"
            auto-grow
            :max-rows="3"
            density="compact"
            variant="outlined"
            hide-details
            :placeholder="tm('input.quickMessageContent')"
            class="quick-message-input"
          />
          <div class="quick-message-actions">
            <v-btn
              icon
              variant="text"
              size="small"
              :disabled="index === 0"
              :aria-label="tm('input.quickMessageMoveUp')"
              @click="moveUp(index)"
            >
              <v-icon icon="mdi-arrow-up" size="18"></v-icon>
            </v-btn>
            <v-btn
              icon
              variant="text"
              size="small"
              :disabled="index === localItems.length - 1"
              :aria-label="tm('input.quickMessageMoveDown')"
              @click="moveDown(index)"
            >
              <v-icon icon="mdi-arrow-down" size="18"></v-icon>
            </v-btn>
            <v-btn
              icon
              variant="text"
              size="small"
              color="error"
              :aria-label="tm('input.quickMessageDelete')"
              @click="removeItem(index)"
            >
              <v-icon icon="mdi-delete" size="18"></v-icon>
            </v-btn>
          </div>
        </div>

        <div v-if="!localItems.length" class="quick-message-empty">
          {{ tm("input.quickMessagesEmpty") }}
        </div>

        <div v-if="validationError" class="quick-message-error">
          {{ validationError }}
        </div>
      </v-card-text>

      <v-card-actions class="pa-4 pt-0">
        <v-btn
          variant="tonal"
          size="small"
          :disabled="localItems.length >= MAX_QUICK_MESSAGES"
          @click="addItem"
        >
          <v-icon icon="mdi-plus" size="small"></v-icon>
          {{ tm("input.addQuickMessage") }}
        </v-btn>
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">
          {{ tm("input.cancel") }}
        </v-btn>
        <v-btn
          variant="tonal"
          color="primary"
          :disabled="!isValid"
          @click="save"
        >
          {{ tm("input.save") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import {
  MAX_QUICK_MESSAGES,
  MAX_QUICK_MESSAGE_LENGTH,
  createQuickMessageId,
  type QuickMessage,
} from "@/composables/quickMessages";

export interface QuickMessagesEditorPayload {
  items: QuickMessage[];
}

const props = defineProps<{
  modelValue: boolean;
  items: QuickMessage[];
}>();

const emit = defineEmits<{
  "update:modelValue": [open: boolean];
  save: [payload: QuickMessagesEditorPayload];
}>();

const { tm } = useModuleI18n("features/chat");

// Draft copy; the parent list is only replaced on save.
const localItems = ref<QuickMessage[]>([]);

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return;
    localItems.value = props.items.map((item) => ({ ...item }));
  },
  { immediate: true },
);

const validationError = computed(() => {
  for (const item of localItems.value) {
    const content = item.content.trim();
    if (!content) return tm("input.quickMessageContentRequired");
    if (content.length > MAX_QUICK_MESSAGE_LENGTH) {
      return tm("input.quickMessageContentTooLong");
    }
  }
  return "";
});

const isValid = computed(() => validationError.value === "");

function moveUp(index: number): void {
  if (index <= 0) return;
  const [item] = localItems.value.splice(index, 1);
  localItems.value.splice(index - 1, 0, item);
}

function moveDown(index: number): void {
  if (index >= localItems.value.length - 1) return;
  const [item] = localItems.value.splice(index, 1);
  localItems.value.splice(index + 1, 0, item);
}

function removeItem(index: number): void {
  localItems.value.splice(index, 1);
}

function addItem(): void {
  if (localItems.value.length >= MAX_QUICK_MESSAGES) return;
  localItems.value.push({ id: createQuickMessageId(), content: "" });
}

function save(): void {
  if (!isValid.value) return;
  emit("save", {
    items: localItems.value.map((item) => ({
      id: item.id,
      content: item.content.trim(),
    })),
  });
  emit("update:modelValue", false);
}
</script>

<style scoped>
.quick-messages-dialog {
  background-color: rgb(var(--v-theme-surface));
}

/* Hint note below the title — wraps across lines (v-card-subtitle would
   truncate it with an ellipsis). */
.quick-messages-hint {
  color: rgba(
    var(--v-theme-on-surface),
    var(--v-medium-emphasis-opacity, 0.62)
  );
  font-size: 12px;
  line-height: 1.4;
  white-space: normal;
}

.quick-message-row {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: 8px;
}

.quick-message-input {
  flex: 1;
  min-width: 0;
}

.quick-message-actions {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  padding-top: 2px;
}

.quick-message-empty {
  color: rgba(
    var(--v-theme-on-surface),
    var(--v-medium-emphasis-opacity, 0.62)
  );
  font-size: 12px;
  line-height: 1.45;
  white-space: normal;
}

.quick-message-error {
  color: rgb(var(--v-theme-error));
  font-size: 12px;
  margin-top: 4px;
}
</style>
