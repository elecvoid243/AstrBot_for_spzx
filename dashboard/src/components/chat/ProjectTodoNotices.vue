<template>
  <aside
    v-if="items.length"
    class="reminder-stack"
    :aria-label="tm('board.notices')"
    aria-live="polite"
    aria-relevant="additions"
  >
    <article
      v-for="item in items"
      :key="item.id + ':' + item.unread_at"
      class="reminder-card"
    >
      <span class="bell-tile" aria-hidden="true"
        ><BellRing :size="23" :stroke-width="1.8"
      /></span>
      <div class="reminder-copy">
        <header>
          <span>{{ tm("board.notices") }}</span>
          <time :datetime="new Date(item.unread_at * 1000).toISOString()">{{
            time.format(item.unread_at * 1000)
          }}</time>
        </header>
        <h3>{{ item.title }}</h3>
        <p v-if="item.description">{{ item.description }}</p>
        <footer>
          <span class="project"
            ><Folder :size="13" aria-hidden="true" />{{ item.project }}</span
          >
          <button class="details" type="button" @click="open(item)">
            {{ tm("board.details")
            }}<ArrowUpRight :size="14" aria-hidden="true" />
          </button>
        </footer>
        <span v-if="failed.has(item.id)" role="alert" class="close-error">{{
          tm("board.dismissFailed")
        }}</span>
      </div>
      <button
        type="button"
        class="dismiss"
        :aria-label="tm('board.close') + ' · ' + item.title"
        :disabled="closing.has(item.id)"
        @click="dismiss(item)"
      >
        <X :size="17" aria-hidden="true" />
      </button>
    </article>
  </aside>
</template>

<script setup lang="ts">
import axios from "axios";
import { ArrowUpRight, BellRing, Folder, X } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useModuleI18n, useI18n } from "@/i18n/composables";
import { accountSessionRevision } from "@/utils/accountSession";

interface Notice {
  id: string;
  title: string;
  description: string;
  project: string;
  project_id: string;
  unread_at: number;
}
const { tm } = useModuleI18n("features/chat");
const { locale } = useI18n();
const time = computed(
  () =>
    new Intl.DateTimeFormat(locale.value, {
      hour: "2-digit",
      minute: "2-digit",
    }),
);
const route = useRoute(),
  router = useRouter();
const items = ref<Notice[]>([]),
  closing = ref(new Set<string>()),
  failed = ref(new Set<string>());
const revision = accountSessionRevision.value;
let timer = 0,
  alive = true,
  seq = 0;

async function refresh() {
  const current = ++seq;
  try {
    const { data } = await axios.get("/api/project-todos/reminders");
    if (alive && current === seq && revision === accountSessionRevision.value)
      items.value = data.data;
  } catch {
    /* A later poll recovers the durable unread list. */
  }
}
function open(item: Notice) {
  void router.push({
    path: route.path.startsWith("/chatbox") ? "/chatbox" : "/chat",
    query: { boardProject: item.project_id, boardCard: item.id },
  });
}
async function dismiss(item: Notice) {
  if (closing.value.has(item.id)) return;
  closing.value.add(item.id);
  try {
    await axios.post(
      `/api/project-todos/${encodeURIComponent(
        item.project_id,
      )}/${encodeURIComponent(item.id)}/read`,
      null,
      { params: { at: item.unread_at } },
    );
    if (!alive || revision !== accountSessionRevision.value) return;
    ++seq;
    items.value = items.value.filter(
      (n) => n.id !== item.id || n.unread_at !== item.unread_at,
    );
    failed.value.delete(item.id);
    window.dispatchEvent(
      new CustomEvent("astrbot-board-change", { detail: item.project_id }),
    );
  } catch {
    if (alive) failed.value.add(item.id);
  } finally {
    closing.value.delete(item.id);
  }
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
.reminder-stack {
  position: fixed;
  inset-inline-end: max(16px, env(safe-area-inset-right));
  bottom: max(16px, env(safe-area-inset-bottom));
  width: min(390px, calc(100vw - 32px));
  max-height: min(60dvh, 660px);
  overflow: auto;
  overscroll-behavior: contain;
  display: grid;
  gap: 10px;
  padding: 4px;
  z-index: 2200;
  scrollbar-width: thin;
}
.reminder-card {
  --ink: rgb(var(--v-theme-on-surface));
  --paper: rgb(var(--v-theme-surface));
  --amber: color-mix(in srgb, #db9b31 78%, var(--ink));
  position: relative;
  isolation: isolate;
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr) 24px;
  gap: 10px;
  padding: 15px 11px 12px 14px;
  border: 1px solid color-mix(in srgb, var(--amber) 36%, var(--paper));
  border-radius: 15px;
  background: linear-gradient(
    118deg,
    color-mix(in srgb, var(--amber) 8%, var(--paper)),
    var(--paper) 65%
  );
  color: var(--ink);
  box-shadow: 0 3px 12px #231a0d20;
}
.reminder-card::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  border-radius: inherit;
  box-shadow: inset 0 0 0 2px var(--amber);
  opacity: 0;
  animation: reminder-pulse 2s ease-in-out 5;
}
.bell-tile {
  display: grid;
  place-items: center;
  width: 41px;
  height: 41px;
  border-radius: 13px;
  background: color-mix(in srgb, var(--amber) 17%, var(--paper));
  color: color-mix(in srgb, var(--amber) 80%, var(--ink));
}
.bell-tile svg {
  transform-origin: 50% 18%;
  animation: reminder-ring 1s ease-in-out 10;
}
.reminder-copy {
  min-width: 0;
}
.reminder-copy header,
.reminder-copy footer,
.details,
.project {
  display: flex;
  align-items: center;
  gap: 6px;
}
.reminder-copy header {
  font-size: 11.5px;
  font-weight: 650;
  color: color-mix(in srgb, var(--amber) 61%, var(--ink));
  flex-wrap: wrap;
}
.reminder-copy time {
  color: var(--sp-text-muted);
  font-variant-numeric: tabular-nums;
  font-weight: 400;
}
.reminder-copy h3 {
  margin: 4px 0;
  font-size: 16px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}
.reminder-copy p {
  font-size: 13px;
  line-height: 1.5;
  color: var(--sp-text-muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 8em;
  overflow: auto;
}
.reminder-copy footer {
  margin-top: 9px;
  justify-content: space-between;
  font-size: 12px;
  gap: 10px;
}
.project {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--sp-text-muted);
}
.project svg,
.details svg {
  flex: none;
}
.details,
.dismiss {
  border-radius: 6px;
  min-height: 28px;
}
.details {
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
  white-space: nowrap;
}
.dismiss {
  display: grid;
  place-items: center;
  align-self: start;
  color: var(--sp-text-muted);
}
.dismiss:hover,
.details:hover {
  background: var(--sp-chip-hover-bg);
}
button:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}
.close-error {
  display: block;
  color: rgb(var(--v-theme-error));
  font-size: 12px;
}
@keyframes reminder-ring {
  0%,
  65%,
  100% {
    transform: rotate(0);
  }
  10% {
    transform: rotate(-19deg);
  }
  23% {
    transform: rotate(16deg);
  }
  36% {
    transform: rotate(-11deg);
  }
  48% {
    transform: rotate(7deg);
  }
}
@keyframes reminder-pulse {
  0%,
  100% {
    opacity: 0;
  }
  45% {
    opacity: 0.65;
  }
}
@media (prefers-reduced-motion: reduce) {
  .reminder-card::before,
  .bell-tile svg {
    animation: none;
  }
}
@media (pointer: coarse) {
  .dismiss {
    min-height: 38px;
  }
  .details {
    min-height: 36px;
  }
}
</style>
