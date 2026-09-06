<template>
  <v-dialog
    :model-value="modelValue"
    :fullscreen="isFullscreen"
    max-width="900px"
    scrollable
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <v-card :class="{ fullscreen: isFullscreen }">
      <v-card-title class="d-flex align-center">
        <span>{{ memberName }}</span>
        <v-spacer />
        <v-btn
          icon
          size="small"
          data-test="fullscreen-toggle"
          @click="isFullscreen = !isFullscreen"
        >
          <v-icon>{{ isFullscreen ? 'mdi-fullscreen-exit' : 'mdi-fullscreen' }}</v-icon>
        </v-btn>
        <v-btn icon size="small" @click="$emit('update:modelValue', false)">
          <v-icon>mdi-close</v-icon>
        </v-btn>
      </v-card-title>

      <v-divider />

      <v-card-text
        ref="scrollContainer"
        data-test="timeline-scroll"
        class="timeline-scroll"
        @scroll="onScroll"
      >
        <div v-if="hasMore" class="text-center py-2">
          <v-btn size="small" variant="text" data-test="load-more" @click="$emit('loadMore')">
            {{ tm('dialog.loadMore') }}
          </v-btn>
        </div>

        <div v-for="(entry, idx) in timeline" :key="entry.turnId + '-' + idx">
          <div v-if="entry.kind === 'turn'" data-test="timeline-turn" class="turn-entry mb-3">
            <div class="turn-direction">{{ entry.direction === 'sent' ? 'Sent' : 'Reply' }}</div>
            <div class="turn-text">{{ entry.text }}</div>
            <div v-if="entry.streaming" class="streaming-indicator">...</div>
          </div>

          <div v-else-if="entry.kind === 'choice'" data-test="choice-entry" class="choice-entry mb-3">
            <InteractiveChoiceBox
              v-if="entry.parts && entry.parts[0]"
              :umo="umo"
              :part="entry.parts[0]"
            />
            <div v-if="entry.direction === 'resolved'" class="choice-resolved">
              {{ entry.text }}
            </div>
          </div>

          <div v-else-if="entry.kind === 'system'" data-test="system-entry" class="system-entry mb-2">
            <v-chip size="small" color="grey">{{ entry.text }}</v-chip>
          </div>
        </div>

        <div v-if="timeline.length === 0" class="text-center text-grey pa-4">
          {{ tm('dialog.empty') }}
        </div>
      </v-card-text>

      <v-divider />

      <v-card-actions class="pa-3">
        <v-text-field
          v-model="inputText"
          data-test="input-field"
          :placeholder="tm('dialog.sendPlaceholder')"
          hide-details
          density="compact"
          @keydown.enter.prevent="handleSend"
        />
        <v-btn
          data-test="send-button"
          color="primary"
          :disabled="!inputText.trim()"
          @click="handleSend"
        >
          {{ tm('dialog.send') }}
        </v-btn>
        <v-btn
          data-test="interrupt-button"
          color="warning"
          @click="handleInterrupt"
        >
          {{ tm('dialog.interrupt') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { ref, watch, onUnmounted } from 'vue';
import { useModuleI18n } from '@/i18n/composables';
import InteractiveChoiceBox from '@/components/chat/message_list_comps/InteractiveChoiceBox.vue';
import { useMemberRunStream } from '@/composables/useMemberRunStream';
import type { MemberTimelineEntry } from '@/composables/agentTeamsRunReducer';

interface Props {
  modelValue: boolean;
  runId: string;
  memberId: string;
  memberName: string;
  umo: string;
  timeline: MemberTimelineEntry[];
  hasMore?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  hasMore: false,
});

const emit = defineEmits<{
  'update:modelValue': [value: boolean];
  send: [text: string];
  interrupt: [];
  loadMore: [];
}>();

const { tm } = useModuleI18n('features/agent-teams');
const isFullscreen = ref(false);
const inputText = ref('');
const scrollContainer = ref<HTMLElement>();

const stream = useMemberRunStream();
let streamState: ReturnType<typeof stream.attach> | null = null;

// Attach the member's chat run stream only while the dialog is open: it is the
// live-fidelity channel (choice boxes, follow-up echoes) alongside the reducer
// timeline, and holding it open for a closed dialog would leak a connection.
watch(
  () => props.modelValue,
  (open) => {
    if (open && !streamState) {
      streamState = stream.attach(props.umo, props.runId);
    } else if (!open && streamState) {
      streamState.detach();
      streamState = null;
    }
  },
  { immediate: true },
);

onUnmounted(() => {
  streamState?.detach();
});

function handleSend() {
  const text = inputText.value.trim();
  if (!text) return;
  emit('send', text);
  inputText.value = '';
}

function handleInterrupt() {
  if (confirm(tm('dialog.interruptConfirm'))) {
    emit('interrupt');
  }
}

function onScroll(event: Event) {
  const target = event.target as HTMLElement;
  if (target.scrollTop === 0 && props.hasMore) {
    emit('loadMore');
  }
}
</script>

<style scoped>
.fullscreen {
  height: 100vh;
}

.timeline-scroll {
  max-height: 600px;
  overflow-y: auto;
}

.turn-entry {
  padding: 8px 12px;
  border-left: 3px solid #ccc;
}

.turn-direction {
  font-size: 12px;
  color: #666;
  font-weight: 600;
  text-transform: uppercase;
}

.turn-text {
  margin-top: 4px;
  white-space: pre-wrap;
}

.streaming-indicator {
  color: #999;
  font-style: italic;
  margin-top: 4px;
}

.choice-entry {
  padding: 8px;
  background: #f9f9f9;
  border-radius: 4px;
}

.choice-resolved {
  margin-top: 8px;
  font-size: 13px;
  color: #555;
}

.system-entry {
  text-align: center;
}
</style>
