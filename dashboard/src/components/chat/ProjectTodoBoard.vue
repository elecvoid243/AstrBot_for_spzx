<template>
  <section class="todo-board" :aria-label="tm('board.label')">
    <div class="board-toolbar">
      <div class="completion" v-if="cards.length">
        <span>{{
          tm("board.progress", { done: completedCount, total: cards.length })
        }}</span>
        <progress
          :value="completedCount"
          :max="cards.length"
          :aria-label="tm('board.done')"
        />
      </div>
      <div class="board-actions">
        <div class="board-search" role="search">
          <Search :size="16" aria-hidden="true" />
          <input
            v-model="search"
            type="text"
            :placeholder="tm('board.search')"
            :aria-label="tm('board.search')"
          />
          <button
            v-if="search"
            type="button"
            :aria-label="tm('board.clearSearch')"
            @click="search = ''"
          >
            <X :size="14" />
          </button>
        </div>
        <button
          class="new-card"
          type="button"
          @click="edit(undefined, $event.currentTarget as HTMLElement)"
        >
          <Plus :size="16" />{{ tm("board.create") }}
        </button>
      </div>
    </div>
    <v-alert v-if="failure" type="error" variant="tonal" density="compact">{{
      failure
    }}</v-alert>
    <div
      ref="laneTrack"
      class="lanes"
      :class="{ 'is-sorting': sorting }"
      :aria-busy="loading"
      :aria-label="tm('board.label')"
    >
      <section
        v-for="stage in stages"
        :key="stage"
        class="lane"
        :class="stage"
        :data-stage="stage"
        :aria-label="tm('board.' + stage)"
      >
        <div class="lane-heading">
          <h3>
            <span class="stage-icon"
              ><component :is="stageIcons[stage]" :size="16" aria-hidden="true"
            /></span>
            {{ tm("board." + stage)
            }}<span class="stage-count">{{ grouped[stage].length }}</span>
          </h3>
          <button
            class="lane-grip"
            type="button"
            :aria-label="tm('board.reorderList') + ' · ' + tm('board.' + stage)"
            :title="tm('board.reorderList')"
            @keydown.left.prevent="shiftLane(stage, -1)"
            @keydown.right.prevent="shiftLane(stage, 1)"
          >
            <GripVertical :size="16" />
          </button>
        </div>
        <div class="lane-cards">
          <button
            v-for="card in grouped[stage]"
            :key="card.id"
            type="button"
            class="todo-card"
            :data-card="card.id"
            @click="edit(card, $event.currentTarget as HTMLElement)"
          >
            <span v-if="card.labels.length" class="task-labels">
              <span
                v-for="label in card.labels.slice(0, 3)"
                :key="label.id"
                class="task-label"
                :style="{ '--tag': labelColors[label.color] }"
                >{{ label.name }}</span
              ><small v-if="card.labels.length > 3"
                >+{{ card.labels.length - 3 }}</small
              >
            </span>
            <span class="card-heading">
              <strong>{{ card.title }}</strong>
              <GripVertical class="drag-hint" aria-hidden="true" :size="15" />
            </span>
            <span v-if="card.description" class="description">{{
              card.description
            }}</span>
            <span
              v-if="
                card.unread_at ||
                card.next_due ||
                card.session_id ||
                card.checklist.length
              "
              class="card-meta"
            >
              <span v-if="card.unread_at" class="meta-chip unread"
                ><BellRing :size="12" />{{ tm("board.unread") }}</span
              >
              <span
                v-else-if="card.next_due"
                class="meta-chip"
                :title="new Date(card.next_due * 1000).toLocaleString(locale)"
              >
                <component
                  :is="card.reminder?.kind === 'once' ? Bell : Repeat2"
                  :size="12"
                />
                <time
                  :datetime="new Date(card.next_due * 1000).toISOString()"
                  >{{ reminderDate.format(card.next_due * 1000) }}</time
                >
              </span>
              <span
                v-if="card.checklist.length"
                class="meta-chip check-count"
                :class="{ complete: card.checklist.every((item) => item.done) }"
                :aria-label="tm('board.checklist')"
              >
                <ListChecks :size="14" />{{
                  card.checklist.filter((item) => item.done).length
                }}
                / {{ card.checklist.length }}
              </span>
              <span v-if="card.session_id" class="meta-chip assigned"
                ><MessagesSquare :size="12" />{{ tm("board.assigned") }}</span
              >
            </span>
          </button>
          <div
            v-if="!grouped[stage].length && !loading && !failure"
            class="empty-lane"
          >
            <component
              :is="search.trim() ? Search : stageIcons[stage]"
              :size="20"
              aria-hidden="true"
            />
            <span>{{
              tm(search.trim() ? "board.noMatch" : "board.emptyLane")
            }}</span>
          </div>
        </div>
        <button
          type="button"
          class="lane-add"
          @click="edit(undefined, $event.currentTarget as HTMLElement, stage)"
        >
          <Plus :size="15" />{{ tm("board.create") }}
        </button>
      </section>
    </div>
    <v-dialog
      v-model="dialog"
      max-width="700"
      :target="origin"
      :transition="motion.matches ? false : undefined"
      :persistent="busy"
      scrollable
      @after-leave="
        origin?.isConnected && origin.focus({ preventScroll: true })
      "
    >
      <v-card v-if="draft" class="board-editor" rounded="xl">
        <v-card-title class="text-h3 pa-4 pb-0 pl-6 editor-title">
          <span>{{
            tm(draft.revision == null ? "board.create" : "board.details")
          }}</span>
          <v-btn
            icon
            variant="text"
            size="small"
            :aria-label="tm('board.close')"
            :disabled="busy"
            @click="dialog = false"
            ><X :size="18"
          /></v-btn>
        </v-card-title>
        <v-card-text class="fields">
          <v-text-field
            v-model="draft.title"
            :label="tm('board.title')"
            maxlength="200"
            variant="outlined"
            hide-details
            autofocus
          />
          <v-textarea
            v-model="draft.description"
            :label="tm('board.description')"
            maxlength="10000"
            variant="outlined"
            rows="3"
            hide-details
          />
          <ProjectTodoExtras
            v-model:checklist="draft.checklist"
            v-model:labels="draft.labels"
            :new-id="uuid"
          />
          <div class="field-pair">
            <v-select
              v-model="draft.status"
              :label="tm('board.status')"
              :items="stageOptions"
              variant="outlined"
              density="comfortable"
              hide-details
            />
            <v-select
              v-model="kind"
              :label="tm('board.reminder')"
              :items="reminderOptions"
              variant="outlined"
              density="comfortable"
              hide-details
            />
          </div>
          <template v-if="kind !== 'none'">
            <v-text-field
              v-model="start"
              type="datetime-local"
              :label="tm('board.start')"
              variant="outlined"
              density="comfortable"
              hide-details
            />
            <v-text-field
              v-if="kind === 'hours'"
              v-model.number="hours"
              type="number"
              min="1"
              max="8760"
              :label="tm('board.interval')"
              variant="outlined"
              density="comfortable"
              hide-details
            />
            <v-select
              v-if="kind === 'weekly'"
              v-model="days"
              multiple
              chips
              :items="weekdays"
              :label="tm('board.weekdays')"
              variant="outlined"
              density="comfortable"
              hide-details
            />
          </template>
          <div v-if="draft.revision != null" class="agent-fields">
            <div class="agent-heading">
              <span
                ><MessagesSquare :size="16" aria-hidden="true" />{{
                  tm("board.target")
                }}</span
              >
              <v-btn
                v-if="draft.session_id"
                size="small"
                variant="text"
                @click="
                  dialog = false;
                  emit('session', draft.session_id);
                "
                >{{ tm("board.viewSession") }}<ArrowUpRight :size="14"
              /></v-btn>
            </div>
            <v-autocomplete
              v-model="target"
              :items="targetOptions"
              :aria-label="tm('board.target')"
              :placeholder="tm('board.chooseSession')"
              clearable
              variant="outlined"
              density="compact"
              hide-details
            />
            <v-textarea
              v-if="target"
              v-model="guidance"
              :label="tm('board.guidance')"
              :aria-label="tm('board.guidance')"
              :placeholder="tm('board.guidancePlaceholder')"
              maxlength="10000"
              variant="outlined"
              density="comfortable"
              rows="2"
              auto-grow
              max-rows="5"
              hide-details
            />
            <div class="agent-controls">
              <v-btn
                :disabled="!target || !valid || busy"
                variant="tonal"
                @click="save(true)"
                >{{
                  tm(target === "new" ? "board.createSend" : "board.send")
                }}</v-btn
              >
            </div>
          </div>
          <v-alert
            v-if="editorError"
            density="compact"
            type="error"
            variant="tonal"
            >{{ editorError }}</v-alert
          >
        </v-card-text>
        <v-card-actions class="editor-actions">
          <v-btn
            v-if="draft.revision != null"
            variant="text"
            color="error"
            :disabled="busy"
            @click="remove"
            >{{ tm("board.delete") }}</v-btn
          ><v-spacer />
          <v-btn variant="text" :disabled="busy" @click="dialog = false">{{
            tm("board.cancel")
          }}</v-btn>
          <v-btn
            variant="tonal"
            color="primary"
            :disabled="!valid || busy"
            @click="save(false)"
            >{{ tm("board.save") }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>
<script setup lang="ts">
import {
  computed,
  onMounted,
  onBeforeUnmount,
  ref,
  shallowRef,
  watch,
} from "vue";
import ProjectTodoExtras, {
  labelColors,
  type TodoLabel,
  type TodoCheck,
} from "./ProjectTodoExtras.vue";
import {
  useProjectBoardDrag,
  type BoardStage as Stage,
} from "./useProjectBoardDrag";
import axios from "axios";
import { useToast } from "@/utils/toast";
import {
  ArrowUpRight,
  Bell,
  BellRing,
  CircleCheck,
  CircleDashed,
  CircleDot,
  GripVertical,
  ListChecks,
  MessagesSquare,
  Plus,
  Repeat2,
  Search,
  X,
} from "@lucide/vue";
import { useModuleI18n, useI18n } from "@/i18n/composables";
import { askForConfirmation, useConfirmDialog } from "@/utils/confirmDialog";
import { accountSessionRevision } from "@/utils/accountSession";

type Kind = "once" | "daily" | "weekly" | "hours";
interface Schedule {
  kind: Kind;
  start: number;
  hours: number;
  weekdays: number[];
  timezone: string;
}
interface Card {
  labels: TodoLabel[];
  checklist: TodoCheck[];
  id: string;
  title: string;
  description: string;
  status: Stage;
  revision: number | null;
  reminder: Schedule | null;
  next_due: number | null;
  unread_at: number | null;
  session_id: string | null;
}
interface Target {
  id: string;
  name: string | null;
  project: string | null;
}
const toast = useToast();
const props = defineProps<{ projectId: string; focusCard?: string }>();
const emit = defineEmits<{ session: [id: string]; sent: []; focused: [] }>();
const { tm } = useModuleI18n("features/chat"),
  { locale } = useI18n(),
  confirm = useConfirmDialog();
const stages = ref<Stage[]>(["todo", "doing", "done"]);
const origin = shallowRef<HTMLElement>();
const laneTrack = ref<HTMLElement>();
const stageIcons = { todo: CircleDashed, doing: CircleDot, done: CircleCheck };
const reminderDate = computed(
  () =>
    new Intl.DateTimeFormat(locale.value, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }),
);
const stageOptions = computed(() =>
  stages.value.map((value) => ({ value, title: tm("board." + value) })),
);
const reminderOptions = computed(() =>
  ["none", "once", "daily", "weekly", "hours"].map((value) => ({
    value,
    title: tm("board." + value),
  })),
);
const weekdays = computed(() =>
  Array.from({ length: 7 }, (_, value) => ({
    value,
    title: new Intl.DateTimeFormat(locale.value, {
      weekday: "long",
      timeZone: "UTC",
    }).format(new Date(Date.UTC(2024, 0, 1 + value))),
  })),
);
const cards = ref<Card[]>([]),
  draft = ref<Card | null>(null),
  targets = ref<Target[]>([]),
  search = ref("");
const visible = computed(() =>
  cards.value.filter((card) =>
    [
      card.title,
      card.description,
      ...card.labels.map((label) => label.name),
      ...card.checklist.map((item) => item.title),
    ]
      .join(" ")
      .toLocaleLowerCase()
      .includes((search.value || "").trim().toLocaleLowerCase()),
  ),
);
const completedCount = computed(
  () => cards.value.filter((card) => card.status === "done").length,
);
const grouped = computed(() => {
  const lanes: Record<Stage, Card[]> = { todo: [], doing: [], done: [] };
  for (const card of visible.value) lanes[card.status].push(card);
  return lanes;
});
const targetOptions = computed(() => [
  { value: "new", title: tm("board.newSession") },
  ...targets.value.map((t) => ({
    value: t.id,
    title: [t.name || tm("conversation.newConversation"), t.project]
      .filter(Boolean)
      .join(" · "),
  })),
]);
const dialog = ref(false),
  busy = ref(false),
  loading = ref(false),
  failure = ref(""),
  editorError = ref(""),
  target = ref<string | null>(null);
const guidance = ref("");
const start = ref(""),
  kind = ref<Kind | "none">("none"),
  hours = ref(1),
  days = ref<number[]>([]);
const { sorting, motion } = useProjectBoardDrag(
  laneTrack,
  busy,
  moveCard,
  moveLanes,
);
const valid = computed(
  () =>
    !!draft.value?.title.trim() &&
    (kind.value === "none" ||
      (Number.isFinite(Date.parse(start.value)) &&
        (kind.value !== "hours" ||
          (Number.isInteger(hours.value) &&
            hours.value >= 1 &&
            hours.value <= 8760)) &&
        (kind.value !== "weekly" || days.value.length > 0))),
);
const url = () => "/api/project-todos/" + encodeURIComponent(props.projectId);
let version = 0,
  receipt = "",
  initial = "",
  originalStart = "";
const account = accountSessionRevision.value;
function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(
    "",
  );
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}
function message(e: unknown) {
  return axios.isAxiosError(e)
    ? e.response?.data?.message || e.message
    : String(e);
}
async function load() {
  const seq = ++version;
  loading.value = true;
  try {
    const { data } = await axios.get(url());
    if (seq !== version || account !== accountSessionRevision.value) return;
    if (sorting.value || busy.value) {
      deferredLoad = true;
      return;
    }
    cards.value = data.data.map((card: Card) => ({
      ...card,
      labels: card.labels ?? [],
      checklist: card.checklist ?? [],
    }));
    stages.value = data.lane_order ?? ["todo", "doing", "done"];
    failure.value = "";
    if (props.focusCard) {
      const item = cards.value.find((c) => c.id === props.focusCard);
      emit("focused");
      if (item) edit(item);
      else failure.value = tm("board.missing");
    }
  } catch (e) {
    if (seq === version) failure.value = message(e);
  } finally {
    if (seq === version) loading.value = false;
  }
}
function changed() {
  window.dispatchEvent(
    new CustomEvent("astrbot-board-change", { detail: props.projectId }),
  );
}
let deferredLoad = false;
function onChange(event: Event) {
  if ((event as CustomEvent).detail !== props.projectId) return;
  if (sorting.value || busy.value) deferredLoad = true;
  else void load();
}
watch([sorting, busy], ([drag, working]) => {
  if (!drag && !working && deferredLoad) {
    deferredLoad = false;
    void load();
  }
});
onMounted(() => window.addEventListener("astrbot-board-change", onChange));
onBeforeUnmount(() => {
  version++;
  window.removeEventListener("astrbot-board-change", onChange);
});
watch(
  () => [props.projectId, props.focusCard],
  ([project], previous) => {
    if (project !== previous?.[0]) {
      dialog.value = false;
      cards.value = [];
    }
    void load();
  },
  { immediate: true },
);
function payload() {
  const d = draft.value!,
    old = d.reminder;
  const reminder =
    kind.value === "none"
      ? null
      : {
          kind: kind.value,
          start:
            old && start.value === originalStart
              ? old.start
              : Math.floor(Date.parse(start.value) / 1000),
          timezone:
            old && start.value === originalStart
              ? old.timezone
              : Intl.DateTimeFormat().resolvedOptions().timeZone,
          hours: Number(hours.value),
          weekdays: days.value,
        };
  return {
    labels: d.labels
      .filter((l) => l.name.trim())
      .map((l) => ({ ...l, name: l.name.trim() })),
    checklist: d.checklist
      .filter((t) => t.title.trim())
      .map((t) => ({ ...t, title: t.title.trim() })),
    title: d.title.trim(),
    description: d.description,
    status: d.status,
    reminder,
    revision: d.revision,
  };
}
function edit(card?: Card, source?: HTMLElement, stage: Stage = "todo") {
  if (sorting.value || busy.value) return;
  origin.value =
    source ??
    (card
      ? laneTrack.value?.querySelector<HTMLElement>(
          `[data-card="${CSS.escape(card.id)}"]`,
        ) ?? undefined
      : undefined);
  draft.value = card
    ? {
        ...card,
        labels: card.labels.map((l) => ({ ...l })),
        checklist: card.checklist.map((t) => ({ ...t })),
      }
    : {
        id: uuid(),
        title: "",
        description: "",
        status: stage,
        labels: [],
        checklist: [],
        revision: null,
        reminder: null,
        next_due: null,
        unread_at: null,
        session_id: null,
      };
  const r = card?.reminder,
    t = new Date(
      (r?.start ?? Math.ceil(Date.now() / 60000) * 60 + 3600) * 1000,
    );
  start.value = originalStart = new Date(
    t.getTime() - t.getTimezoneOffset() * 60000,
  )
    .toISOString()
    .slice(0, 16);
  kind.value = r?.kind ?? "none";
  hours.value = r?.hours ?? 1;
  days.value = r?.weekdays?.length ? [...r.weekdays] : [(t.getDay() + 6) % 7];
  target.value = null;
  guidance.value = "";
  receipt = uuid();
  editorError.value = "";
  dialog.value = true;
  initial = JSON.stringify(payload());
  if (card) {
    void axios
      .get("/api/project-todos/targets")
      .then(({ data }) => {
        if (account === accountSessionRevision.value) targets.value = data.data;
      })
      .catch((e) => {
        editorError.value = message(e);
      });
  }
}
async function save(send: boolean) {
  if (!valid.value || busy.value || !draft.value) return;
  busy.value = true;
  editorError.value = "";
  const d = draft.value,
    path = url() + "/" + d.id;
  try {
    const body = payload();
    if (d.revision == null || JSON.stringify(body) !== initial) {
      await axios.put(path, body);
      d.revision = (d.revision ?? 0) + 1;
      initial = JSON.stringify(payload());
      changed();
    }
    if (send) {
      await axios.post(path + "/send", {
        request_id: receipt,
        revision: d.revision,
        session_id: target.value === "new" ? null : target.value,
        guidance: guidance.value.trim(),
      });
      emit("sent");
      changed();
    }
    dialog.value = false;
  } catch (e) {
    editorError.value = message(e);
    void load();
  } finally {
    busy.value = false;
  }
}
async function remove() {
  if (
    !draft.value ||
    !(await askForConfirmation(tm("board.confirmDelete"), confirm))
  )
    return;
  busy.value = true;
  try {
    await axios.delete(url() + "/" + draft.value.id, {
      params: { revision: draft.value.revision },
    });
    dialog.value = false;
    changed();
  } catch (e) {
    editorError.value = message(e);
  } finally {
    busy.value = false;
  }
}
async function moveCard(id: string, stage: Stage, before: string | null) {
  const card = cards.value.find((c) => c.id === id);
  if (!card || busy.value) return;
  const previous = cards.value,
    remaining = previous.filter((c) => c.id !== id);
  const index = before ? remaining.findIndex((c) => c.id === before) : -1;
  remaining.splice(index < 0 ? remaining.length : index, 0, {
    ...card,
    status: stage,
  });
  cards.value = remaining;
  const project = props.projectId;
  busy.value = true;
  try {
    await axios.post(url() + "/" + id + "/move", {
      status: stage,
      before,
      revision: card.revision,
    });
    if (project === props.projectId) changed();
  } catch (e) {
    if (project === props.projectId) {
      cards.value = previous;
      deferredLoad = true;
      toast.error(message(e));
    }
  } finally {
    busy.value = false;
  }
}
async function moveLanes(order: Stage[]) {
  const previous = stages.value,
    project = props.projectId;
  if (busy.value || order.every((stage, i) => stage === previous[i])) return;
  stages.value = order;
  busy.value = true;
  try {
    await axios.post(url() + "/layout", { order, previous });
    if (project === props.projectId) changed();
  } catch (e) {
    if (project === props.projectId) {
      stages.value = previous;
      deferredLoad = true;
      toast.error(message(e));
    }
  } finally {
    busy.value = false;
  }
}
function shiftLane(stage: Stage, delta: number) {
  const order = [...stages.value],
    from = order.indexOf(stage),
    to = from + delta;
  if (to < 0 || to >= order.length) return;
  order.splice(from, 1);
  order.splice(to, 0, stage);
  void moveLanes(order);
}
</script>
<style scoped>
.todo-board {
  container-type: inline-size;
  display: flex;
  flex-direction: column;
  --board-ink: rgb(var(--v-theme-on-surface));
  --board-muted: rgba(var(--v-theme-on-surface), 0.68);
  --board-edge: rgba(var(--v-theme-on-surface), 0.12);
  --board-surface: rgb(var(--v-theme-surface));
  width: 100%;
  min-width: 0;
  color: var(--board-ink);
  padding-bottom: 4px;
}
.board-toolbar,
.board-actions,
.completion,
.new-card,
.board-search,
.lane h3,
.stage-icon,
.stage-count,
.card-heading,
.card-meta,
.meta-chip {
  display: flex;
  align-items: center;
}
.board-toolbar {
  flex: none;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.board-actions {
  gap: 10px;
  margin-inline-start: auto;
  min-width: 0;
}
.completion {
  flex-wrap: wrap;
  gap: 8px;
  color: var(--board-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.completion progress {
  appearance: none;
  border: 0;
  border-radius: 8px;
  width: 60px;
  height: 4px;
  overflow: hidden;
  background: rgba(var(--v-theme-on-surface), 0.1);
  color: rgb(var(--v-theme-primary));
}
.completion progress::-webkit-progress-bar {
  background: rgba(var(--v-theme-on-surface), 0.1);
  border-radius: inherit;
}
.completion progress::-webkit-progress-value {
  background: rgb(var(--v-theme-primary));
  border-radius: inherit;
}
.completion progress::-moz-progress-bar {
  background: rgb(var(--v-theme-primary));
  border-radius: inherit;
}
.board-search,
.new-card {
  min-height: 36px;
  border-radius: 9px;
  gap: 7px;
  font-size: 13px;
}
.board-search {
  width: clamp(132px, 20cqi, 220px);
  padding: 0 10px;
  color: var(--board-muted);
  border: 1px solid var(--board-edge);
  background: var(--board-surface);
}
.board-search input {
  flex: 1;
  width: 0;
  min-width: 0;
  outline: 0;
  color: var(--board-ink);
}
.board-search input::placeholder {
  color: var(--board-muted);
  opacity: 1;
}
.board-search button {
  display: grid;
  place-items: center;
  min-width: 24px;
  min-height: 26px;
  border-radius: 6px;
}
.board-search:focus-within {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}
.new-card {
  justify-content: center;
  padding: 0 13px;
  border: 1px dashed rgba(var(--v-theme-primary), 0.55);
  background: rgba(var(--v-theme-primary), 0.09);
  color: var(--board-ink);
  font-weight: 600;
  white-space: nowrap;
}
.new-card svg {
  color: rgb(var(--v-theme-primary));
}
.new-card:hover {
  background: rgba(var(--v-theme-primary), 0.16);
}
.lanes {
  --canvas: color-mix(
    in srgb,
    rgb(var(--v-theme-primary)) 7%,
    rgb(var(--v-theme-background))
  );
  display: flex;
  gap: 12px;
  align-items: flex-start;
  min-width: 0;
  min-height: 0;
  padding: 12px;
  overflow-x: auto;
  overflow-y: hidden;
  overscroll-behavior-x: contain;
  background: linear-gradient(
    118deg,
    var(--canvas),
    color-mix(
      in srgb,
      rgb(var(--v-theme-secondary)) 4%,
      rgb(var(--v-theme-background))
    )
  );
  border: 1px solid var(--board-edge);
  border-radius: 16px;
  scrollbar-width: thin;
}
.lane {
  --lane-accent: color-mix(in srgb, var(--board-ink) 53%, var(--board-surface));
  display: flex;
  flex-direction: column;
  flex: 1 0 220px;
  min-width: 0;
  min-height: 0;
  max-height: var(--lane-cap, min(56dvh, 620px));
  border: 1px solid
    color-mix(in srgb, var(--lane-accent) 21%, var(--board-surface));
  border-radius: 12px;
  background: color-mix(in srgb, var(--lane-accent) 5%, var(--board-surface));
  box-shadow: 0 2px 5px rgba(0, 0, 0, 0.04);
}
.lane.doing {
  --lane-accent: rgb(var(--v-theme-primary));
}
.lane.done {
  --lane-accent: color-mix(
    in srgb,
    rgb(var(--v-theme-success)) 65%,
    var(--board-muted)
  );
}
.lane-heading {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
  padding: 10px;
  min-height: 44px;
  cursor: grab;
  user-select: none;
  touch-action: none;
}
.lane-heading:active {
  cursor: grabbing;
}
.lane h3 {
  flex: 1;
  min-width: 0;
  gap: 7px;
  font-size: 14px;
  font-weight: 650;
}
.lane-grip {
  display: grid;
  place-items: center;
  width: 24px;
  height: 26px;
  color: var(--board-muted);
  border-radius: 6px;
  cursor: grab;
}
.lane-grip:hover,
.lane-add:hover {
  background: rgba(var(--v-theme-on-surface), 0.06);
}
.lane-add {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  margin: 4px 5px;
  flex: none;
  border-radius: 7px;
  font-size: 12px;
  font-weight: 550;
  color: var(--board-muted);
  text-align: start;
}
.lane-add:focus-visible,
.lane-grip:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}
.stage-icon {
  width: 26px;
  height: 26px;
  flex: none;
  justify-content: center;
  border-radius: 8px;
  background: color-mix(in srgb, var(--lane-accent) 15%, var(--board-surface));
  color: color-mix(in srgb, var(--lane-accent) 73%, var(--board-ink));
}
.stage-count {
  justify-content: center;
  min-width: 24px;
  padding: 0 6px;
  height: 21px;
  border: 1px solid var(--board-edge);
  border-radius: 7px;
  color: var(--board-muted);
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.lane-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 30px;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--v-theme-on-surface), 0.23) transparent;
  padding: 2px 8px 3px;
  overscroll-behavior: contain;
}
.lane-cards > * {
  flex: none;
}
.todo-card {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 7px;
  min-width: 0;
  border: 1px solid var(--board-edge);
  border-radius: 11px;
  padding: 13px 14px;
  background: var(--board-surface);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  text-align: start;
  overflow-wrap: anywhere;
  cursor: grab;
  transition:
    background-color 0.15s,
    border-color 0.15s,
    box-shadow 0.15s;
}
.todo-card:hover,
.todo-card:focus-visible {
  border-color: color-mix(
    in srgb,
    var(--lane-accent) 55%,
    var(--board-surface)
  );
  box-shadow: 0 3px 9px rgba(0, 0, 0, 0.08);
}
.todo-card:active {
  cursor: grabbing;
}
.task-labels {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
  min-width: 0;
}
.task-label {
  display: block;
  max-width: 100%;
  padding: 2px 8px;
  border-radius: 5px;
  background: color-mix(in srgb, var(--tag) 20%, var(--board-surface));
  color: color-mix(in srgb, var(--tag) 52%, var(--board-ink));
  font-size: 11px;
  font-weight: 650;
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.check-count.complete {
  background: rgba(var(--v-theme-success), 0.12);
}
.board-drop-space {
  background: rgba(var(--v-theme-primary), 0.11) !important;
  border: 1px dashed rgba(var(--v-theme-primary), 0.45) !important;
  box-shadow: none !important;
}
.board-drop-space > * {
  visibility: hidden;
}
.board-drag-mirror {
  opacity: 0.96 !important;
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.19) !important;
  pointer-events: none !important;
  cursor: grabbing;
}
.is-sorting {
  user-select: none;
}
.card-heading {
  justify-content: space-between;
  gap: 7px;
  align-items: flex-start;
}
.card-heading strong {
  font-size: 15px;
  font-weight: 620;
  line-height: 1.5;
}
.drag-hint {
  flex: none;
  margin-top: 4px;
  opacity: 0;
  color: var(--board-muted);
}
.todo-card:is(:hover, :focus-visible) .drag-hint {
  opacity: 1;
}
.description {
  color: var(--board-muted);
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-line;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-meta {
  flex-wrap: wrap;
  gap: 5px;
  padding-top: 4px;
}
.meta-chip {
  gap: 4px;
  max-width: 100%;
  padding: 3px 7px;
  border-radius: 6px;
  color: var(--board-muted);
  background: rgba(var(--v-theme-on-surface), 0.045);
  font-size: 11.5px;
  line-height: 1.45;
}
.meta-chip svg {
  flex: none;
}
.meta-chip.assigned {
  background: rgba(var(--v-theme-primary), 0.09);
}
.meta-chip.unread {
  background: rgba(var(--v-theme-warning), 0.15);
  color: var(--board-ink);
}
.empty-lane {
  display: grid;
  justify-items: center;
  align-content: center;
  gap: 8px;
  min-height: 64px;
  padding: 12px;
  border: 1px dashed var(--board-edge);
  border-radius: 10px;
  font-size: 12px;
  color: var(--board-muted);
  text-align: center;
}
.empty-lane svg {
  opacity: 0.65;
}
.fields {
  display: grid;
  gap: 14px;
}
.editor-title,
.agent-heading,
.agent-heading span,
.agent-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}
.editor-title,
.agent-heading {
  justify-content: space-between;
}
.field-pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.agent-fields {
  display: grid;
  gap: 10px;
  padding: 14px;
  background: rgba(var(--v-theme-primary), 0.045);
  border: 1px solid rgba(var(--v-theme-primary), 0.16);
  border-radius: 12px;
}
.agent-heading {
  min-height: 24px;
  font-size: 13px;
  font-weight: 600;
}
.agent-heading span svg {
  color: rgb(var(--v-theme-primary));
}
.agent-controls {
  justify-content: flex-end;
}
.editor-actions {
  border-top: 1px solid var(--sp-chip-border);
  padding: 10px 16px;
}
@media (max-width: 575px) {
  .agent-controls {
    align-items: stretch;
    flex-direction: column;
  }
  .field-pair {
    grid-template-columns: 1fr;
  }
}
.todo-card:focus-visible,
.new-card:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}
@container (max-width:740px) {
  .lanes {
    gap: 9px;
    padding: 8px;
  }
  .lane {
    flex-basis: 195px;
  }
}
@container (max-width:600px) {
  .lanes {
    scroll-padding-inline: 8px;
  }
  .lane {
    flex: 0 0 min(82cqw, 300px);
  }
}
@container (max-width:450px) {
  .board-toolbar {
    gap: 10px;
  }
  .board-actions {
    width: 100%;
    margin: 0;
  }
  .board-search {
    flex: 1;
  }
  .lane h3 {
    min-height: 46px;
  }
}
@media (pointer: coarse) {
  .new-card,
  .board-search {
    min-height: 44px;
  }
  .board-search input {
    font-size: 16px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .todo-card {
    transition: none;
  }
}
</style>
