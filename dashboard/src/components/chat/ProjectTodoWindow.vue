<template>
  <section
    ref="panel"
    class="project-todo-window"
    role="dialog"
    aria-modal="false"
    :aria-label="project.title + ' · ' + tm('board.label')"
    :style="{ left: pos.x + 'px', top: pos.y + 'px', zIndex: layer }"
    @pointerdown="emit('focus')"
  >
    <header @pointerdown="begin">
      <GripVertical :size="16" /><strong
        >{{ project.title }} · {{ tm("board.label") }}</strong
      >
      <v-btn
        variant="text"
        size="small"
        icon="mdi-close"
        :aria-label="tm('board.close')"
        @pointerdown.stop
        @click="emit('close')"
      />
    </header>
    <div class="body">
      <ProjectTodoBoard
        :project-id="project.project_id"
        @session="emit('session', $event)"
        @sent="emit('sent')"
      />
    </div>
  </section>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { GripVertical } from "@lucide/vue";
import { useModuleI18n } from "@/i18n/composables";
import type { Project } from "./ProjectList.vue";
import ProjectTodoBoard from "./ProjectTodoBoard.vue";
const props = defineProps<{ project: Project; layer: number }>();
const emit = defineEmits<{
  close: [];
  focus: [];
  session: [id: string];
  sent: [];
}>();
const { tm } = useModuleI18n("features/chat"),
  panel = ref<HTMLElement>();
const pos = ref({
  x: Math.max(8, (innerWidth - 760) / 2 + 12 * (props.layer % 5)),
  y: Math.min(112, innerHeight / 6),
});
function place(x = pos.value.x, y = pos.value.y) {
  pos.value = {
    x: Math.max(0, Math.min(x, innerWidth - (panel.value?.offsetWidth ?? 0))),
    y: Math.max(0, Math.min(y, innerHeight - (panel.value?.offsetHeight ?? 0))),
  };
}
function begin(e: PointerEvent) {
  if (e.button !== 0) return;
  const head = e.currentTarget as HTMLElement,
    dx = e.clientX - pos.value.x,
    dy = e.clientY - pos.value.y;
  head.setPointerCapture(e.pointerId);
  head.onpointermove = (move) => {
    if (move.pointerId === e.pointerId)
      place(move.clientX - dx, move.clientY - dy);
  };
  head.onlostpointercapture = () => {
    head.onpointermove = null;
    head.onlostpointercapture = null;
  };
}
let observer: ResizeObserver | undefined;
const resize = () => place();
onMounted(() => {
  place();
  window.addEventListener("resize", resize);
  if (window.ResizeObserver && panel.value) {
    observer = new ResizeObserver(resize);
    observer.observe(panel.value);
  }
});
onBeforeUnmount(() => {
  window.removeEventListener("resize", resize);
  observer?.disconnect();
});
</script>
<style scoped>
.project-todo-window {
  position: fixed;
  width: min(760px, calc(100vw - 16px));
  max-height: calc(100dvh - 16px);
  display: flex;
  flex-direction: column;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid var(--sp-chip-border);
  border-radius: 12px;
  box-shadow: 0 8px 30px #0003;
  overflow: hidden;
}
header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border-bottom: 1px solid var(--sp-chip-border);
  cursor: grab;
  user-select: none;
  touch-action: none;
}
header:active {
  cursor: grabbing;
}
header strong {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
}
.body {
  padding: 14px;
  overflow: auto;
  min-height: 0;
}
</style>
