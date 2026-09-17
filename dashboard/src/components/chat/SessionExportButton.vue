<!-- Author: elecvoid243, 2026-09-17
     Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md §5.5
     Single-purpose export affordance shared by the flat session list, the
     session context menu and the project session list. -->
<script setup lang="ts">
import { Download } from "@lucide/vue";
import { useModuleI18n } from "@/i18n/composables";

const props = withDefaults(
  defineProps<{
    sessionId: string;
    /** Icon size; the context menu uses 16, the row actions 15. */
    size?: number;
    /**
     * Row actions get the host's own row-button class so hover/colour match
     * their siblings; the context menu uses the styled menu item.
     */
    variant?: "icon" | "project-icon" | "menu-item";
    /** Row-button class forwarded by the host (e.g. `project-action-btn`). */
    actionClass?: string;
  }>(),
  { size: 15, variant: "icon", actionClass: "session-action-btn" },
);

const emit = defineEmits<{ export: [sessionId: string] }>();

const { tm } = useModuleI18n("features/chat");

function onActivate(event: MouseEvent | KeyboardEvent) {
  event.stopPropagation();
  emit("export", props.sessionId);
}
</script>

<template>
  <v-btn
    v-if="variant !== 'menu-item'"
    icon
    size="x-small"
    variant="text"
    :class="actionClass"
    :title="tm('conversation.export')"
    @click="onActivate"
  >
    <Download :size="size" />
  </v-btn>
  <v-list-item v-else class="styled-menu-item" rounded="md" @click="onActivate">
    <template #prepend>
      <Download :size="size" />
    </template>
    <v-list-item-title>{{ tm("conversation.export") }}</v-list-item-title>
  </v-list-item>
</template>
