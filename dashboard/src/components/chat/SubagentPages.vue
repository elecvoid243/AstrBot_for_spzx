<template>
  <div class="subagent-pages" :class="{ 'is-open': selectedRunId }">
    <Teleport :to="navigationHost || 'body'" :disabled="!navigationHost">
      <nav
        class="session-pages-nav"
        :class="{ 'in-toolbar': navigationHost }"
        :aria-label="tm('subagentPages.navigation')"
      >
        <button
          type="button"
          class="session-main-trigger"
          :class="{ 'is-active': !selectedRunId }"
          :title="tm('subagentPages.main')"
          @click="showMain"
        >
          <!-- Icon-only face on phones; the label stays for the toolbar row. -->
          <v-icon class="session-nav-icon" size="16"
            >mdi-message-text-outline</v-icon
          >
          <span class="session-nav-label">{{ tm("subagentPages.main") }}</span>
        </button>
        <v-menu
          v-model="menuOpen"
          :close-on-content-click="false"
          location="bottom start"
          :offset="8"
        >
          <template #activator="{ props: activatorProps }">
            <button
              v-bind="activatorProps"
              type="button"
              class="subagent-picker-trigger"
              :class="{ 'is-active': selectedRunId }"
              :aria-expanded="menuOpen"
              :title="selectedPart?.agent_name || tm('subagentPages.agents')"
            >
              <v-icon class="session-nav-icon" size="16"
                >mdi-robot-outline</v-icon
              >
              <span class="subagent-picker-label">{{
                selectedPart?.agent_name || tm("subagentPages.agents")
              }}</span>
              <span v-if="selectedAgent?.marker" class="instance-marker">{{
                selectedAgent.marker
              }}</span>
              <span
                v-if="
                  selectedPart?.status === 'running' ||
                  (!selectedRunId &&
                    runs.some((run) => run.status === 'running'))
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
          </template>
          <div class="subagent-picker-menu">
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
                <div
                  v-for="agent in group.items"
                  :key="agent.id"
                  class="subagent-instance"
                >
                  <div
                    class="subagent-picker-row"
                    :class="{ 'is-selected': selectedAgent?.id === agent.id }"
                  >
                    <button
                      type="button"
                      class="subagent-picker-item subagent-agent-toggle"
                      :aria-expanded="
                        agent.runs.length > 1
                          ? Boolean(expandedAgents[agent.id])
                          : undefined
                      "
                      @click="
                        agent.runs.length > 1
                          ? (expandedAgents[agent.id] =
                              !expandedAgents[agent.id])
                          : openRun(agent.runs[0])
                      "
                    >
                      <v-icon
                        v-if="agent.runs.length > 1"
                        size="14"
                        class="instance-chevron"
                        >{{
                          expandedAgents[agent.id]
                            ? "mdi-chevron-down"
                            : "mdi-chevron-right"
                        }}</v-icon
                      >
                      <span
                        class="run-dot"
                        :class="{ running: agent.running }"
                      />
                      <span class="run-name">
                        <span class="agent-name-line"
                          ><span class="agent-display-name">{{
                            agent.name
                          }}</span
                          ><span
                            v-if="agent.marker"
                            class="instance-marker"
                            :title="
                              tm('subagentPages.instance', { id: agent.id })
                            "
                            >{{ agent.marker }}</span
                          ></span
                        >
                        <small>{{
                          tm("subagentPages.invocationCount", {
                            count: agent.runs.length,
                          })
                        }}</small>
                      </span>
                      <span v-if="agent.background" class="run-badge">{{
                        tm("subagentPages.background")
                      }}</span>
                      <span class="run-state">{{
                        tm(
                          `subagentStatus.${
                            agent.running ? "running" : agent.runs[0].status
                          }`,
                        )
                      }}</span>
                    </button>
                    <button
                      type="button"
                      class="subagent-info-button"
                      :title="tm('subagentConfig.latest')"
                      :aria-label="
                        tm('subagentConfig.view', {
                          name:
                            agent.name +
                            (agent.marker ? ` ${agent.marker}` : ''),
                        })
                      "
                      @click.stop="
                        configurationRun = agent.runs[0];
                        menuOpen = false;
                      "
                    >
                      <v-icon size="17">mdi-information-outline</v-icon>
                    </button>
                  </div>
                  <div
                    v-if="agent.runs.length > 1 && expandedAgents[agent.id]"
                    class="subagent-invocations"
                  >
                    <div
                      v-for="run in agent.runs"
                      :key="run.subagent_run_id"
                      class="subagent-picker-row"
                      :class="{
                        'is-selected': selectedRunId === run.subagent_run_id,
                      }"
                    >
                      <button
                        type="button"
                        class="subagent-picker-item invocation-item"
                        @click="openRun(run)"
                      >
                        <span
                          class="run-dot"
                          :class="{ running: run.status === 'running' }"
                        />
                        <span class="invocation-details">
                          <span class="invocation-meta">
                            <code>{{ run.subagent_run_id }}</code>
                            <time
                              :datetime="
                                invocationTimes[run.subagent_run_id]?.iso
                              "
                              >{{
                                invocationTimes[run.subagent_run_id]?.text
                              }}</time
                            >
                          </span>
                          <span
                            class="invocation-preview"
                            :title="
                              run.input_preview || tm('subagentPages.noPreview')
                            "
                            >{{
                              run.input_preview || tm("subagentPages.noPreview")
                            }}</span
                          >
                          <span class="invocation-status">
                            <span v-if="run.background" class="run-badge">{{
                              tm("subagentPages.background")
                            }}</span>
                            <span class="run-state">{{
                              tm(`subagentStatus.${run.status}`)
                            }}</span>
                          </span>
                        </span>
                      </button>
                      <button
                        type="button"
                        class="subagent-info-button"
                        :title="tm('subagentConfig.title')"
                        :aria-label="
                          tm('subagentConfig.view', {
                            name: run.subagent_run_id,
                          })
                        "
                        @click.stop="
                          configurationRun = run;
                          menuOpen = false;
                        "
                      >
                        <v-icon size="17">mdi-information-outline</v-icon>
                      </button>
                    </div>
                  </div>
                </div>
              </template>
            </template>
          </div>
        </v-menu>
      </nav>
    </Teleport>
    <div v-if="selectedRunId" class="subagent-page-body">
      <section
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
        :model-value="Boolean(reasoningTarget)"
        :parts="reasoningParts"
        :is-dark="isDark"
        :is-streaming="selectedPart?.status === 'running'"
        :focus-call-id="reasoningTarget?.callId"
        @update:model-value="!$event && (reasoningTarget = null)"
      />
    </div>
    <SubagentConfigurationDialog
      v-if="configurationRun"
      :session-id="sessionId"
      :run="configurationRun"
      @close="configurationRun = null"
    />
  </div>
</template>

<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  ref,
  shallowRef,
  watch,
} from "vue";
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
import { useI18n, useModuleI18n } from "@/i18n/composables";
import ChatMessageList from "./ChatMessageList.vue";
import ReasoningSidebar from "./ReasoningSidebar.vue";
import SubagentConfigurationDialog from "./SubagentConfigurationDialog.vue";

type RunEntry = Pick<
  SubAgentRunPart,
  "subagent_run_id" | "agent_name" | "status" | "started_ts"
> & {
  background?: boolean;
  cleaned?: boolean;
  revision?: number;
  agent_instance_id?: string;
  input_preview?: string;
};
type AgentEntry = {
  id: string;
  name: string;
  runs: RunEntry[];
  marker: string;
  cleaned: boolean;
  running: boolean;
  background: boolean;
};
const props = defineProps<{
  sessionId: string;
  toolbarTarget?: string;
  beforeNavigate?: () => Promise<boolean>;
  isDark?: boolean;
}>();
const emit = defineEmits<{ viewChange: [open: boolean] }>();
const { tm } = useModuleI18n("features/chat");
const { locale } = useI18n();
const entries = ref<Record<string, RunEntry>>({});
const selectedRunId = ref("");
const detailSessionId = ref("");
const selectedPart = ref<SubAgentRunPart | null>(null);
const navigationHost = shallowRef<HTMLElement | null>(null);
const configurationRun = ref<RunEntry | null>(null);
const menuOpen = ref(false);
watch(
  () => props.toolbarTarget,
  async (target) => {
    menuOpen.value = false;
    configurationRun.value = null;
    await nextTick();
    navigationHost.value = target ? document.querySelector(target) : null;
  },
  { immediate: true, flush: "post" },
);
const cleanedOpen = ref(false);
const expandedAgents = ref<Record<string, boolean>>({});
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
const agents = computed(() => {
  const instances = new Map<string, AgentEntry>();
  const names = new Map<string, AgentEntry[]>();
  for (const run of runs.value) {
    // Unknown historical lifetimes stay separate instead of merging by name.
    const id = run.agent_instance_id || `legacy:${run.subagent_run_id}`;
    let agent = instances.get(id);
    if (!agent) {
      agent = {
        id,
        name: run.agent_name,
        runs: [],
        marker: "",
        cleaned: true,
        running: false,
        background: Boolean(run.background),
      };
      instances.set(id, agent);
      const sameName = names.get(agent.name) || [];
      sameName.push(agent);
      names.set(agent.name, sameName);
    }
    agent.runs.push(run);
    agent.cleaned &&= Boolean(run.cleaned);
    agent.running ||= run.status === "running";
    agent.background ||= run.status === "running" && Boolean(run.background);
  }
  for (const sameName of names.values()) {
    if (sameName.length < 2) continue;
    for (const agent of sameName) {
      let length = 6;
      while (
        sameName.some(
          (other) =>
            other.id !== agent.id &&
            other.id.slice(-length) === agent.id.slice(-length),
        )
      )
        length += 2;
      agent.marker = `#${agent.id.slice(-length)}`;
    }
  }
  return [...instances.values()];
});
const selectedAgent = computed(() =>
  detailSessionId.value === props.sessionId
    ? agents.value.find((agent) =>
        agent.runs.some((run) => run.subagent_run_id === selectedRunId.value),
      )
    : undefined,
);
const groups = computed(() => [
  { cleaned: false, items: agents.value.filter((agent) => !agent.cleaned) },
  { cleaned: true, items: agents.value.filter((agent) => agent.cleaned) },
]);
const invocationTimes = computed(() => {
  const formatter = new Intl.DateTimeFormat(locale.value, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  return Object.fromEntries(
    runs.value.map((run) => {
      const date = new Date(Number(run.started_ts) * 1000);
      return [
        run.subagent_run_id,
        run.started_ts && Number.isFinite(date.getTime())
          ? { text: formatter.format(date), iso: date.toISOString() }
          : { text: tm("subagentPages.unknownTime"), iso: undefined },
      ];
    }),
  );
});

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

async function openRun(
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
  if (props.beforeNavigate && !(await props.beforeNavigate())) return;
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
    configurationRun.value = null;
    cleanedOpen.value = false;
    expandedAgents.value = {};
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
.session-pages-nav {
  display: flex;
  align-items: center;
  gap: 3px;
  width: fit-content;
  max-width: 100%;
  margin: 10px 16px;
  padding: 3px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 10px;
  background: rgba(var(--v-theme-on-surface), 0.035);
}
.session-pages-nav.in-toolbar {
  margin: 0;
  /* 2026-11-07 (elecvoid243): the 3.5% tint disappeared into the app-bar
     background in dark mode; use the same solid chip surface as the composer
     capsules so the segmented control reads as one control. */
  background: var(--sp-chip-bg);
  border-color: var(--sp-chip-border);
}
.session-pages-nav button {
  color: inherit;
  font: inherit;
  font-size: 13px;
}
.session-pages-nav > button,
.subagent-picker-trigger {
  min-height: 30px;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  border-radius: 7px;
  white-space: nowrap;
  transition:
    background 0.15s ease,
    color 0.15s ease;
}
.session-pages-nav button:hover {
  background: rgba(var(--v-theme-on-surface), 0.05);
}
.session-pages-nav .is-active {
  color: rgb(var(--v-theme-primary));
  background: rgb(var(--v-theme-surface));
  box-shadow: 0 1px 3px #0000000d;
  font-weight: 600;
}
.session-main-trigger {
  flex-shrink: 0;
}
.subagent-picker-label {
  min-width: 0;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Only rendered below the phone breakpoint (see the media query at the end):
   the labels are what squeeze the toolbar title at that width. */
.session-nav-icon {
  display: none;
  flex: 0 0 auto;
}

.subagent-picker-menu {
  width: min(430px, calc(100vw - 24px));
  max-height: 60vh;
  overflow-y: auto;
  padding: 8px;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 10px;
  box-shadow: 0 8px 24px #0002;
}
.subagent-picker-row {
  width: 100%;
  display: flex;
  align-items: center;
  border-radius: 8px;
}
.subagent-instance + .subagent-instance {
  margin-top: 3px;
}
.agent-name-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.agent-display-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.instance-marker {
  flex-shrink: 0;
  padding: 1px 4px;
  border-radius: 4px;
  background: rgba(var(--v-theme-primary), 0.09);
  color: rgb(var(--v-theme-primary));
  font-size: 10px;
  font-weight: 500;
}
.instance-chevron {
  opacity: 0.55;
}
.subagent-invocations {
  margin: 2px 0 8px 16px;
  padding-left: 5px;
  border-left: 1px solid rgba(var(--v-theme-on-surface), 0.1);
}
.invocation-details {
  flex: 1;
  min-width: 0;
}
.invocation-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 3px 10px;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.invocation-meta code {
  color: rgb(var(--v-theme-on-surface));
  overflow-wrap: anywhere;
}
.invocation-preview {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow-wrap: anywhere;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
}
.invocation-status {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}
.subagent-picker-item {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px;
  border-radius: 7px;
  text-align: left;
}
.subagent-picker-row:hover,
.subagent-picker-row.is-selected {
  background: rgba(var(--v-theme-primary), 0.08);
}
.subagent-info-button {
  display: grid;
  place-items: center;
  flex: 0 0 30px;
  height: 30px;
  margin-right: 4px;
  border-radius: 7px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}
.subagent-info-button:hover {
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.1);
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
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  overflow-anchor: none;
  scrollbar-gutter: stable;
  padding-top: 24px;
}
.subagent-page-body {
  --chat-panel-top-offset: 0px;
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.subagent-message-shell {
  width: var(--chat-content-width, 80%);
  max-width: var(--chat-content-max-width, 960px);
  margin: 0 auto;
  padding-bottom: 24px;
}
@media (max-width: 760px) {
  .session-main-trigger {
    max-width: 105px;
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .session-pages-nav > button,
  .subagent-picker-trigger {
    padding: 0 7px;
    font-size: 12px;
  }
  .subagent-picker-label {
    max-width: 90px;
  }
  .session-pages-nav .run-badge {
    display: none;
  }
  .subagent-message-shell {
    width: calc(100% - 20px);
  }
}

/* Phone width: the toolbar title needs the room, so both tabs fall back to
   their icons. Titles carry the meaning for pointer + screen readers. */
@media (max-width: 600px) {
  .session-nav-icon {
    display: inline-flex;
  }

  .session-nav-label,
  .subagent-picker-label {
    display: none;
  }

  .session-main-trigger {
    display: inline-flex;
    max-width: none;
  }
}
</style>
