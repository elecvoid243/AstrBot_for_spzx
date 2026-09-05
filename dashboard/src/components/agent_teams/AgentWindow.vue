<template>
  <div class="agent-window" :style="windowVars">
    <div class="agent-window-header">
      <span class="agent-window-dot" />
      <span class="agent-window-name">{{ memberName }}</span>
      <v-chip
        v-if="nodeStatus"
        size="x-small"
        variant="tonal"
        class="agent-window-node-chip"
        :style="{ color: nodeChipColor }"
      >
        {{ tm('monitor.node.' + nodeStatus) }}
      </v-chip>
      <v-spacer />
      <span v-if="busy" class="agent-window-busy-dot" />
    </div>
    <div ref="bodyEl" class="agent-window-body">
      <template v-if="hasContent">
        <div v-if="window?.sent" class="agent-window-sent">{{ window.sent }}</div>
        <template v-for="(block, bi) in blocks" :key="bi">
          <ReasoningBlock
            v-if="block.kind === 'thinking'"
            :parts="block.parts"
            :is-dark="isDark"
            :initial-expanded="false"
          />
          <template v-else>
            <template v-for="(part, pi) in block.parts" :key="`${bi}-${pi}`">
              <MarkdownMessagePart
                v-if="part.type === 'plain'"
                :content="String(part.text || '')"
                :refs="null"
                :is-dark="isDark"
                :custom-html-tags="CHAT_MARKDOWN_CUSTOM_TAGS"
                :is-streaming="window?.streaming ?? false"
              />
            </template>
          </template>
        </template>
      </template>
      <div v-else class="agent-window-empty">{{ tm('monitor.empty') }}</div>
    </div>
    <div v-if="busy" class="agent-window-busy">{{ tm('monitor.busy') }}</div>
  </div>
</template>

<script setup lang="ts">
// Per-member live window for the run monitor (Task 8): the task sent to the
// member, its streaming reply and the final structured parts. Rendering uses
// messageBlocks grouping over ReasoningBlock / MarkdownMessagePart and the
// member-color CSS-var theming.
import { computed, nextTick, ref, watch } from 'vue';
import ReasoningBlock from '@/components/chat/message_list_comps/ReasoningBlock.vue';
import MarkdownMessagePart from '@/components/chat/message_list_comps/MarkdownMessagePart.vue';
import { CHAT_MARKDOWN_CUSTOM_TAGS } from '@/components/chat/chatMarkdownComponents';
import { messageBlocks, type ChatContent } from '@/composables/useMessages';
import { collabMemberColor, collabWithAlpha } from '@/utils/memberColors';
import type { MemberWindowState } from '@/composables/agentTeamsRunReducer';
import { useModuleI18n } from '@/i18n/composables';
import { useCustomizerStore } from '@/stores/customizer';

const props = defineProps<{
  /** Team member this window tracks. */
  member: { member_id: string; name?: string; session_id?: string; [key: string]: unknown };
  /** Live window state folded by the run reducer; null before the first event. */
  window: MemberWindowState | null;
  /** Status of the member's active node (`monitor.node.*` key suffix). */
  nodeStatus?: string;
  /** True while the member's session is waiting for its reply. */
  busy?: boolean;
}>();

const { tm } = useModuleI18n('features/agent-teams');
const customizer = useCustomizerStore();
const bodyEl = ref<HTMLElement | null>(null);

const isDark = computed(() => customizer.isDark);

const memberName = computed(() => String(props.member.name || props.member.member_id));
const memberColor = computed(() => collabMemberColor(memberName.value));

// Member-color CSS vars: the header band and the quoted inbound block take
// tints of the member color.
const windowVars = computed(() => ({
  '--member-color': memberColor.value,
  '--member-header-bg': collabWithAlpha(memberColor.value, 0.12),
  '--member-sent-bg': collabWithAlpha(memberColor.value, 0.14),
}));

// Matches the TeamsFlowCanvas monitor-mode node palette.
const NODE_CHIP_COLORS: Record<string, string> = {
  pending: '#94a3b8',
  running: '#60a5fa',
  done: '#4ade80',
  failed: '#f87171',
  skipped: '#facc15',
};

const nodeChipColor = computed(() => NODE_CHIP_COLORS[props.nodeStatus ?? ''] ?? undefined);

// Thinking/content grouping identical to the chat message list. When the
// window carries no structured parts yet, the stream buffer renders as one
// plain markdown part (streaming while deltas arrive).
const blocks = computed(() => {
  const win = props.window;
  if (!win) return [];
  const parts = win.parts?.length
    ? win.parts
    : win.streamText
      ? [{ type: 'plain', text: win.streamText }]
      : [];
  if (!parts.length) return [];
  return messageBlocks({ type: 'bot', message: parts } as ChatContent);
});

const hasContent = computed(() => !!(props.window?.sent || blocks.value.length));

function scrollToBottom() {
  void nextTick(() => {
    if (bodyEl.value) bodyEl.value.scrollTop = bodyEl.value.scrollHeight;
  });
}

// Autoscroll on any content growth (sent swap, stream deltas, parts swap).
watch(
  () =>
    [
      props.window?.sent,
      props.window?.streamText,
      props.window?.parts?.length,
      props.nodeStatus,
    ].join('|'),
  scrollToBottom,
  { immediate: true },
);
</script>

<style scoped>
.agent-window {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 10px;
  background: var(--dashboard-surface, rgba(128, 128, 128, 0.04));
}

.agent-window-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
  padding: 8px 12px;
  border-bottom: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.2));
  background: var(--member-header-bg);
}

.agent-window-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--member-color);
  flex: none;
}

.agent-window-name {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--member-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Busy pulse dot in the header while the member session is waiting. */
.agent-window-busy-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--member-color);
  flex: none;
  animation: agent-window-pulse 1.2s ease-in-out infinite;
}

@keyframes agent-window-pulse {
  0%,
  100% {
    opacity: 0.25;
  }
  50% {
    opacity: 1;
  }
}

.agent-window-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 12px;
  font-size: 0.8125rem;
  line-height: 1.6;
}

/* Quoted inbound block: the task delivered to this member. */
.agent-window-sent {
  margin-bottom: 8px;
  padding: 6px 10px;
  border-left: 3px solid var(--member-color);
  border-radius: 4px 6px 6px 4px;
  background: var(--member-sent-bg);
  color: rgba(var(--v-theme-on-surface), 0.75);
  word-break: break-word;
}

.agent-window-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.7));
  font-size: 0.8125rem;
  text-align: center;
}

.agent-window-busy {
  flex: none;
  padding: 4px 12px;
  border-top: 1px dashed var(--dashboard-border, rgba(128, 128, 128, 0.25));
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.75));
  font-size: 0.6875rem;
}
</style>
