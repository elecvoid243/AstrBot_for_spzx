<template>
  <v-menu v-if="items.length" location="top end">
    <template #activator="{ props }">
      <v-btn v-bind="props" class="todo-notices" size="small" variant="tonal">
        <template #prepend><Bell :size="16"/></template>
        {{ tm("board.notices") }} · {{ items.length }}
      </v-btn>
    </template>
    <v-list density="compact"
      ><v-list-item
        v-for="item in items"
        :key="item.id"
        :title="item.title"
        :subtitle="item.project"
        @click="open(item)"
    /></v-list>
  </v-menu>
  <v-snackbar v-model="show" :timeout="7000"
    >{{ latest?.title }}
    <template #actions
      ><v-btn variant="text" @click="latest && open(latest)">{{
        tm("board.details")
      }}</v-btn></template
    >
  </v-snackbar>
</template>
<script setup lang="ts">
import axios from "axios";
import { Bell } from "@lucide/vue";
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import { accountSessionRevision } from "@/utils/accountSession";
interface Notice {
  id: string;
  title: string;
  project: string;
  project_id: string;
  unread_at: number;
}
const emit = defineEmits<{ open: [project: string, card: string] }>();
const { tm } = useModuleI18n("features/chat"),
  items = ref<Notice[]>([]),
  latest = ref<Notice>(),
  show = ref(false);
const revision = accountSessionRevision.value;
let timer = 0,
  alive = true,
  seq = 0;
async function refresh() {
  const current = ++seq;
  try {
    const { data } = await axios.get("/api/project-todos/reminders");
    if (!alive || current !== seq || revision !== accountSessionRevision.value)
      return;
    const fresh: Notice[] = data.data;
    const next = fresh.find(
      (n) =>
        !items.value.some(
          (old) => old.id === n.id && old.unread_at === n.unread_at,
        ),
    );
    items.value = fresh;
    if (next) {
      latest.value = next;
      show.value = true;
    } else if (!fresh.length) show.value = false;
  } catch {
    /* A later poll recovers the durable unread list. */
  }
}
function open(n: Notice) {
  show.value = false;
  emit("open", n.project_id, n.id);
}
onMounted(() => {
  void refresh();
  timer = window.setInterval(refresh, 10000);
  window.addEventListener("astrbot-board-change", refresh);
});
onBeforeUnmount(() => {
  alive = false;
  window.clearInterval(timer);
  window.removeEventListener("astrbot-board-change", refresh);
});
</script>
<style scoped>
.todo-notices {
  position: fixed;
  right: 18px;
  bottom: 12px;
  z-index: 100;
  background: rgb(var(--v-theme-surface));
}
</style>
