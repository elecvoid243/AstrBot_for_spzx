<template>
  <div class="subagent-pages" :class="{ 'is-open': selectedRunId }">
    <header class="session-pages-header">
      <h1 class="session-pages-title">{{ title }}</h1>
      <nav
        class="session-pages-nav"
        :aria-label="tm('subagentPages.navigation')"
      >
        <button
          type="button"
          :class="{ 'is-active': !selectedRunId }"
          @click="showMain"
        >
          {{ tm("subagentPages.main") }}
        </button>
        <div class="subagent-picker" @keydown.esc="menuOpen = false">
          <button
            type="button"
            :class="{ 'is-active': selectedRunId }"
            :aria-expanded="menuOpen"
            @click="menuOpen = !menuOpen"
          >
            <span class="subagent-picker-label">{{
              selectedPart?.agent_name || tm("subagentPages.agents")
            }}</span>
            <span
              v-if="
                selectedPart?.status === 'running' ||
                (!selectedRunId && runs.some((run) => run.status === 'running'))
              "
              class="run-dot running"
              :title="tm('subagentStatus.running')"
            />
            <span v-if="selectedPart?.background" class="run-badge">{{
              tm("subagentPages.background")
            }}</span>
            <span v-if="selectedPart?.cleaned" class="run-badge">{{
              tm("subagentPages.cleaned")
            }}</span>
            <v-icon size="16">mdi-chevron-down</v-icon>
          </button>
          <div v-if="menuOpen" class="subagent-picker-menu">
            <div v-if="!runs.length" class="picker-empty">
              {{ tm("subagentPages.empty") }}
            </div>
            <template
              v-for="group in groups"
              :key="group.cleaned ? 'cleaned' : 'current'"
            >
              <button
                v-if="group.cleaned && group.items.length"
                type="button"
                class="cleaned-toggle"
                :aria-expanded="cleanedOpen"
                @click="cleanedOpen = !cleanedOpen"
              >
                <v-icon size="14">{{
                  cleanedOpen ? "mdi-chevron-down" : "mdi-chevron-right"
                }}</v-icon>
                {{ tm("subagentPages.cleaned") }} · {{ group.items.length }}
              </button>
              <template v-if="!group.cleaned || cleanedOpen">
                <button
                  v-for="run in group.items"
                  :key="run.subagent_run_id"
                  type="button"
                  class="subagent-picker-item"
                  :class="{
                    'is-selected': selectedRunId === run.subagent_run_id,
                  }"
                  @click="openRun(run)"
                >
                  <span
                    class="run-dot"
                    :class="{
                      running: run.status === 'running',
                    }"
                  />
                  <span class="run-name"
                    >{{ run.agent_name
                    }}<small
                      >{{ tm("subagentPages.invocation") }}
                      {{ run.subagent_run_id.slice(-6) }}</small
                    ></span
                  >
                  <span v-if="run.background" class="run-badge">{{
                    tm("subagentPages.background")
                  }}</span>
                  <span class="run-state">{{
                    tm(`subagentStatus.${run.status}`)
                  }}</span>
                </button>
              </template>
            </template>
          </div>
        </div>
      </nav>
    </header>
    <section
      v-if="selectedRunId"
      ref="panel"
      class="subagent-messages"
      tabindex="0"
      @scroll="handleScroll"
      @wheel.passive="handleIntent"
      @keydown="handleIntent"
      @pointerdown="handleIntent"
      @touchstart.passive="handleIntent"
    >
      <div ref="content" class="subagent-message-shell">
        <div v-if="error" role="alert" class="subagent-page-notice">
          {{ error }}
        </div>
        <div v-if="loading" role="status" class="subagent-page-notice">
          {{ tm("subagentPages.loading") }}
        </div>
        <ChatMessageList
          v-if="selectedPart"
          :messages="messages"
          :is-dark="isDark"
          :is-streaming="selectedPart.status === 'running'"
          :session-id="detailSessionId"
          @open-reasoning="reasoningTarget = $event"
        />
      </div>
    </section>
    <ReasoningSidebar
      v-if="selectedRunId"
      :model-value="Boolean(reasoningTarget)"
      :parts="reasoningParts"
      :is-dark="isDark"
      :is-streaming="selectedPart?.status === 'running'"
      :focus-call-id="reasoningTarget?.callId"
      @update:model-value="!$event && (reasoningTarget = null)"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { chatApi } from "@/api/v1";
import { fetchWithAuth } from "@/api/http";
import { accountSessionRevision } from "@/utils/accountSession";
import {
  applySubAgentEvent,
  type SubAgentRunPart,
} from "@/composables/subagentRunReducer";
import {
  messageBlocks as buildMessageBlocks,
  readSseStream,
  type ChatRecord,
  type MessagePart,
} from "@/composables/useMessages";
import { useModuleI18n } from "@/i18n/composables";
import ChatMessageList from "./ChatMessageList.vue";
import ReasoningSidebar from "./ReasoningSidebar.vue";

type RunEntry = Pick<
  SubAgentRunPart,
  "subagent_run_id" | "agent_name" | "status" | "started_ts"
> & {
  background?: boolean;
  cleaned?: boolean;
  revision?: number;
};
const props = defineProps<{
  sessionId: string;
  title: string;
  isDark?: boolean;
}>();
const emit = defineEmits<{ viewChange: [open: boolean] }>();
const { tm } = useModuleI18n("features/chat");
const entries = ref<Record<string, RunEntry>>({});
const selectedRunId = ref("");
const detailSessionId = ref("");
const selectedPart = ref<SubAgentRunPart | null>(null);
const menuOpen = ref(false);
const cleanedOpen = ref(false);
const loading = ref(false);
const error = ref("");
const panel = ref<HTMLElement | null>(null);
const content = ref<HTMLElement | null>(null);
const reasoningTarget = ref<{
  message: ChatRecord;
  blockIndex: number;
  callId?: string;
} | null>(null);
const following = ref(true);
let listAbort: AbortController | null = null;
let detailAbort: AbortController | null = null;
let observer: ResizeObserver | null = null;
let mutations: MutationObserver | null = null;
let scrollFrame = 0;
let pointerActive = false;
let previousTop = 0;
let direction = 0;
let detailRevision = 0;
const runs = computed(() =>
  Object.values(entries.value).sort(
    (a, b) => (b.started_ts || 0) - (a.started_ts || 0),
  ),
);
const groups = computed(() => [
  { cleaned: false, items: runs.value.filter((run) => !run.cleaned) },
  { cleaned: true, items: runs.value.filter((run) => run.cleaned) },
]);

// Reuse the main renderer while retaining chronological thought/tool/text blocks.
const messages = computed<ChatRecord[]>(() => {
  const part = selectedPart.value;
  if (!part) return [];
  const records: ChatRecord[] = [
    {
      id: `${part.subagent_run_id}-task`,
      content: {
        type: "user",
        message: [
          { type: "plain", text: part.input_full || part.input_preview || "" },
        ],
      },
    },
  ];
  let bot: ChatRecord | null = null;
  const activity = part.activity?.length
    ? part.activity
    : [
        ...(part.reasoning
          ? [{ kind: "think" as const, text: part.reasoning }]
          : []),
        ...(part.tool_calls || []).map((call) => ({
          kind: "tool_call" as const,
          call,
        })),
      ];
  for (const entry of activity) {
    if (entry.kind === "user_message") {
      records.push({
        id: `${part.subagent_run_id}-user-${entry.seq}`,
        content: {
          type: "user",
          message: [{ type: "plain", text: entry.text }],
        },
      });
      bot = null;
      continue;
    }
    if (!bot) {
      bot = {
        id: `${part.subagent_run_id}-reply-${records.length}`,
        content: { type: "bot", message: [] },
      };
      records.push(bot);
    }
    if (entry.kind === "tool_call")
      bot.content.message.push({ type: "tool_call", tool_calls: [entry.call] });
    else if (entry.kind === "think")
      bot.content.message.push({ type: "think", think: entry.text });
    else bot.content.message.push({ type: "plain", text: entry.text });
  }
  if (!bot) {
    bot = {
      id: `${part.subagent_run_id}-reply-${records.length}`,
      content: { type: "bot", message: [] },
    };
    records.push(bot);
  }
  if (part.text) bot.content.message.push({ type: "plain", text: part.text });
  if (part.error)
    bot.content.message.push({ type: "plain", text: String(part.error) });
  bot.content.isLoading =
    part.status === "running" && bot.content.message.length === 0;
  return records;
});
const reasoningParts = computed(() => {
  const target = reasoningTarget.value;
  if (!target) return [];
  const current =
    messages.value.find((record) => record.id === target.message.id) ||
    target.message;
  const block = buildMessageBlocks(current.content)[target.blockIndex];
  return block?.kind === "thinking" ? block.parts : [];
});

function openRun(
  run: RunEntry | Record<string, any>,
  sourceSessionId = props.sessionId,
) {
  menuOpen.value = false;
  if (
    selectedRunId.value === run.subagent_run_id &&
    detailSessionId.value === sourceSessionId &&
    !error.value
  )
    return;
  detailAbort?.abort();
  selectedRunId.value = run.subagent_run_id;
  detailSessionId.value = sourceSessionId;
  selectedPart.value = null;
  detailRevision = 0;
  reasoningTarget.value = null;
  following.value = true;
  pointerActive = false;
  direction = 0;
  previousTop = 0;
  loading.value = true;
  error.value = "";
  emit("viewChange", true);
  detailAbort = new AbortController();
  void listen(sourceSessionId, run.subagent_run_id, detailAbort);
}

function showMain() {
  menuOpen.value = false;
  detailAbort?.abort();
  detailAbort = null;
  selectedRunId.value = "";
  selectedPart.value = null;
  reasoningTarget.value = null;
  emit("viewChange", false);
}

async function listen(
  sessionId: string,
  runId: string | null,
  abort: AbortController,
) {
  let attempt = 0;
  while (!abort.signal.aborted) {
    try {
      const response = await fetchWithAuth(
        chatApi.subagentRunsStreamUrl(sessionId, runId || undefined),
        {
          headers: { Accept: "text/event-stream" },
          signal: abort.signal,
        },
      );
      if (
        !response.ok ||
        !response.body ||
        !response.headers.get("content-type")?.includes("text/event-stream")
      ) {
        if (runId && !abort.signal.aborted) {
          loading.value = false;
          error.value = tm("subagentPages.unavailable");
        }
        return;
      }
      attempt = 0;
      await readSseStream(response.body, (payload) => {
        if (abort.signal.aborted) return;
        if (!runId) {
          if (payload.type === "snapshot" && Array.isArray(payload.data)) {
            entries.value = Object.fromEntries(
              payload.data.map((run: RunEntry) => [run.subagent_run_id, run]),
            );
          } else if (
            payload.type === "changed" &&
            payload.data?.subagent_run_id
          ) {
            const previous = entries.value[payload.data.subagent_run_id];
            if (
              !previous ||
              Number(payload.data.revision || 0) >=
                Number(previous.revision || 0)
            ) {
              entries.value = {
                ...entries.value,
                [payload.data.subagent_run_id]: payload.data,
              };
            }
          }
          return;
        }
        if (payload.type === "snapshot") {
          selectedPart.value = payload.data;
          detailRevision = Number(payload.data?.revision || 0);
          loading.value = false;
          error.value = payload.data ? "" : tm("subagentPages.unavailable");
        } else if (payload.type === "metadata" && selectedPart.value) {
          if (
            Number(payload.data.revision || 0) >=
            Number(selectedPart.value.revision || 0)
          )
            Object.assign(selectedPart.value, payload.data);
          else if (payload.data.cleaned) selectedPart.value.cleaned = true;
        } else if (
          payload.type === "event" &&
          selectedPart.value &&
          payload.revision > detailRevision
        ) {
          applySubAgentEvent(
            [selectedPart.value as unknown as MessagePart],
            payload.data,
          );
          // Lifecycle metadata can arrive before a persisted terminal event.
          // Only applied trace events advance the replay cursor.
          detailRevision = payload.revision;
          selectedPart.value.revision = Math.max(
            Number(selectedPart.value.revision || 0),
            detailRevision,
          );
        }
        nextTick(followBottom);
      });
    } catch {
      if (abort.signal.aborted) return;
    }
    // Reconnect with a fresh authoritative snapshot, also after bounded-buffer loss.
    await new Promise<void>((resolve) => {
      const finish = () => {
        clearTimeout(timer);
        abort.signal.removeEventListener("abort", finish);
        resolve();
      };
      const timer = setTimeout(finish, Math.min(5000, 500 * ++attempt));
      abort.signal.addEventListener("abort", finish, { once: true });
    });
  }
}

function followBottom() {
  if (!following.value || scrollFrame) return;
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = 0;
    if (following.value && panel.value) {
      panel.value.scrollTop = panel.value.scrollHeight;
      previousTop = panel.value.scrollTop;
    }
  });
}

function handleIntent(event: Event) {
  if (event.type === "pointerdown" && event.target !== panel.value) return;
  if (event.type === "wheel") {
    const wheel = event as WheelEvent;
    if (!wheel.deltaY) return;
    direction = Math.sign(wheel.deltaY);
    if (direction < 0) following.value = false;
  } else if (event.type === "keydown") {
    const key = (event as KeyboardEvent).key;
    if (
      event.target instanceof HTMLElement &&
      (event.target.closest("input, textarea, select, [contenteditable]") ||
        (key === " " && event.target.closest("button, a")))
    )
      return;
    if (
      ![
        "ArrowUp",
        "ArrowDown",
        "PageUp",
        "PageDown",
        "Home",
        "End",
        " ",
      ].includes(key)
    )
      return;
    direction =
      ["ArrowUp", "PageUp", "Home"].includes(key) ||
      (key === " " && (event as KeyboardEvent).shiftKey)
        ? -1
        : 1;
    if (direction < 0) following.value = false;
  } else {
    pointerActive = true;
    direction = 0;
    following.value = false;
  }
  previousTop = panel.value?.scrollTop || 0;
  if (
    direction > 0 &&
    panel.value &&
    panel.value.scrollHeight -
      panel.value.scrollTop -
      panel.value.clientHeight <=
      2
  )
    following.value = true;
}

function handleScroll() {
  if (!panel.value) return;
  const top = panel.value.scrollTop;
  if (pointerActive && Math.abs(top - previousTop) > 1)
    direction = Math.sign(top - previousTop);
  if (
    !following.value &&
    !pointerActive &&
    direction > 0 &&
    panel.value.scrollHeight - top - panel.value.clientHeight <= 2
  )
    following.value = true;
  previousTop = top;
}

function endGesture() {
  if (!pointerActive) return;
  pointerActive = false;
  if (
    panel.value &&
    direction >= 0 &&
    panel.value.scrollHeight -
      panel.value.scrollTop -
      panel.value.clientHeight <=
      2
  )
    following.value = true;
}

watch(
  [() => props.sessionId, accountSessionRevision],
  () => {
    listAbort?.abort();
    showMain();
    entries.value = {};
    cleanedOpen.value = false;
    menuOpen.value = false;
    if (props.sessionId) {
      listAbort = new AbortController();
      void listen(props.sessionId, null, listAbort);
    }
  },
  { immediate: true },
);

watch([panel, content], ([viewport, body]) => {
  observer?.disconnect();
  mutations?.disconnect();
  if (!viewport || !body) return;
  observer = new ResizeObserver(followBottom);
  observer.observe(viewport);
  observer.observe(body);
  mutations = new MutationObserver(followBottom);
  mutations.observe(body, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
  });
  for (const type of ["pointerup", "pointercancel", "touchend", "touchcancel"])
    window.addEventListener(type, endGesture);
  followBottom();
});
onBeforeUnmount(() => {
  listAbort?.abort();
  detailAbort?.abort();
  observer?.disconnect();
  mutations?.disconnect();
  cancelAnimationFrame(scrollFrame);
  for (const type of ["pointerup", "pointercancel", "touchend", "touchcancel"])
    window.removeEventListener(type, endGesture);
});
defineExpose({ openRun, getRun: (runId: string) => entries.value[runId] });
</script>

<style scoped>
.subagent-pages {
  flex: 0 0 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.subagent-pages.is-open {
  flex: 1 1 0;
}
.session-pages-header {
  padding: 16px
    max(20px, calc((100% - var(--chat-content-max-width, 960px)) / 2)) 0;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.1);
}
.session-pages-title {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-pages-nav {
  display: flex;
  gap: 16px;
}
.session-pages-nav button {
  color: inherit;
  font: inherit;
  font-size: 13px;
}
.session-pages-nav > button,
.subagent-picker > button {
  min-height: 36px;
  display: flex;
  align-items: center;
  gap: 7px;
  border-bottom: 2px solid transparent;
}
.session-pages-nav .is-active {
  color: rgb(var(--v-theme-primary));
  border-bottom-color: currentColor;
}
.subagent-picker {
  position: relative;
  min-width: 0;
  flex: 0 1 auto;
}
.subagent-picker > button {
  max-width: 100%;
}
.session-pages-nav > button {
  flex-shrink: 0;
}
.subagent-picker-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.subagent-picker-menu {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 50;
  width: min(430px, calc(100vw - 60px));
  max-height: 60vh;
  overflow-y: auto;
  padding: 8px;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 10px;
  box-shadow: 0 8px 24px #0002;
}
.subagent-picker-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px;
  border-radius: 7px;
  text-align: left;
}
.subagent-picker-item:hover,
.subagent-picker-item.is-selected {
  background: rgba(var(--v-theme-primary), 0.08);
}
.run-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.run-name small {
  display: block;
  opacity: 0.55;
  font-size: 11px;
}
.run-dot {
  flex: 0 0 6px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgba(var(--v-theme-on-surface), 0.3);
}
.run-dot.running {
  background: rgb(var(--v-theme-primary));
  box-shadow: 0 0 0 3px rgba(var(--v-theme-primary), 0.12);
}
.run-badge,
.run-state {
  font-size: 11px;
  white-space: nowrap;
  opacity: 0.7;
}
.run-badge {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.06);
}
.cleaned-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 10px 4px 5px;
  opacity: 0.65;
}
.picker-empty,
.subagent-page-notice {
  padding: 16px;
  opacity: 0.6;
  font-size: 13px;
}
.subagent-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-anchor: none;
  scrollbar-gutter: stable;
  padding-top: 24px;
}
.subagent-message-shell {
  width: var(--chat-content-width, 80%);
  max-width: var(--chat-content-max-width, 960px);
  margin: 0 auto;
  padding-bottom: 24px;
}
@media (max-width: 760px) {
  .subagent-picker {
    flex: 1 1 0;
  }
  .subagent-picker-menu {
    left: auto;
    right: 0;
    width: calc(100vw - 40px);
    max-width: 430px;
  }
  .session-pages-header {
    padding: 12px 20px 0;
  }
  .subagent-message-shell {
    width: calc(100% - 20px);
  }
}
</style>
