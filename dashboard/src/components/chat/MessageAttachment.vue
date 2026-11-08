<template>
  <button
    v-if="part.type === 'image' && src"
    :class="sent ? 'sent-attachment-card sent-image-card' : 'image-part'"
    type="button"
    @click="emit('preview', src)"
  >
    <img :src="src" :alt="attachmentName(part)" />
  </button>
  <audio
    v-else-if="!sent && part.type === 'record' && src"
    class="audio-part"
    controls
    :src="src"
  />
  <video
    v-else-if="!sent && part.type === 'video' && src"
    class="video-part"
    controls
    :src="src"
  />
  <div
    v-else
    :class="
      sent
        ? [
            'sent-attachment-card',
            'sent-file-card',
            {
              'sent-media-card':
                part.type === 'record' || part.type === 'video',
            },
          ]
        : 'file-part'
    "
    :style="{ '--attachment-color': presentation.color }"
  >
    <div v-if="sent" class="sent-attachment-icon">
      <v-icon
        class="sent-attachment-icon-symbol"
        :icon="presentation.icon"
        size="24"
      />
      <span class="sent-attachment-ext">{{ presentation.label }}</span>
    </div>
    <v-icon v-else class="file-part-icon" :icon="presentation.icon" size="24" />
    <span v-if="sent" class="sent-attachment-name">{{
      attachmentName(part)
    }}</span>
    <div v-else class="file-part-meta">
      <span class="file-part-name">{{ attachmentName(part) }}</span>
      <span class="file-part-kind">{{ presentation.label }}</span>
    </div>
    <v-btn
      :class="{ 'file-part-action': !sent }"
      icon="mdi-download"
      :aria-label="tm('workspaceFiles.download')"
      size="x-small"
      variant="text"
      :loading="busy"
      :disabled="disabled || busy"
      @click="emit('download')"
    />
    <audio
      v-if="sent && part.type === 'record' && src"
      class="audio-part"
      controls
      preload="metadata"
      :src="src"
    />
    <video
      v-else-if="sent && part.type === 'video' && src"
      class="video-part"
      controls
      preload="metadata"
      :src="src"
    />
  </div>
</template>
<script setup lang="ts">
import { computed } from "vue";
import type { MessagePart } from "@/composables/useMessages";
import {
  attachmentName,
  attachmentPresentation,
} from "./attachmentPresentation";
import { useModuleI18n } from "@/i18n/composables";
const props = defineProps<{
  part: MessagePart;
  src?: string;
  sent?: boolean;
  busy?: boolean;
  disabled?: boolean;
}>();
const emit = defineEmits<{ preview: [url: string]; download: [] }>();
const { tm } = useModuleI18n("features/chat");
const presentation = computed(() => attachmentPresentation(props.part));
</script>
<style scoped>
.sent-attachment-card {
  --attachment-color: #607d8b;
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  height: 60px;
  overflow: hidden;
  border: 0;
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.055);
  color: rgb(var(--v-theme-on-surface));
}

.sent-image-card {
  width: 64px;
  padding: 0;
  border: 0;
  cursor: zoom-in;
}

.sent-image-card img {
  width: 100%;
  height: 100%;
  border-radius: 8px;
  object-fit: cover;
}

.sent-attachments.images-only .sent-image-card {
  width: 180px;
  height: 180px;
}

.sent-attachments.images-only .sent-image-card img {
  object-fit: cover;
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.sent-file-card {
  width: 236px;
  padding: 8px 10px;
  background: rgba(var(--v-theme-on-surface), 0.055);
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--attachment-color) 14%, transparent),
    rgba(var(--v-theme-on-surface), 0.055) 62%
  );
}

.sent-file-card.sent-media-card {
  display: grid;
  align-self: flex-start;
  grid-template-columns: auto minmax(0, 1fr) auto;
  width: 320px;
  max-width: 100%;
  height: auto;
}

.sent-media-card .audio-part,
.sent-media-card .video-part {
  grid-column: 1 / -1;
  width: 100%;
  margin-top: 0;
}

.sent-attachment-icon {
  display: inline-flex;
  flex-shrink: 0;
  min-width: 36px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  color: var(--attachment-color);
}

.sent-attachment-icon-symbol {
  color: var(--attachment-color);
}

.sent-attachment-ext {
  max-width: 58px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
  font-weight: 700;
  line-height: 12px;
  color: var(--attachment-color);
}

.sent-attachment-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 18px;
}

.image-part {
  display: block;
  width: fit-content;
  max-width: 100%;
  border: 0;
  padding: 0;
  margin-top: 8px;
  background: transparent;
  cursor: zoom-in;
  text-align: left;
}

.image-part img {
  max-width: min(420px, 100%);
  max-height: 360px;
  border-radius: 8px;
  object-fit: contain;
}

.audio-part,
.video-part {
  display: block;
  max-width: 100%;
  margin-top: 8px;
}

.video-part {
  max-height: 360px;
  border-radius: 8px;
}

.file-part {
  --attachment-color: #607d8b;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  width: min(420px, 100%);
  margin-top: 8px;
  padding: 9px 8px 9px 10px;
  border: 0;
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.055);
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--attachment-color) 13%, transparent),
    rgba(var(--v-theme-on-surface), 0.055) 58%
  );
}

.file-part-icon {
  color: var(--attachment-color);
}

.file-part-meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.file-part-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 500;
  line-height: 20px;
}

.file-part-kind {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--attachment-color);
  font-size: 11px;
  font-weight: 700;
  line-height: 14px;
}

.file-part-action {
  color: rgb(var(--v-theme-on-surface));
  opacity: 0.72;
}

.file-part:hover .file-part-action {
  opacity: 1;
}

@media (max-width: 560px) {
  .sent-file-card {
    width: min(220px, calc(100vw - 28px));
    height: 58px;
  }
  .sent-image-card {
    width: 58px;
    height: 58px;
  }
  .sent-attachments.images-only .sent-image-card {
    width: min(180px, calc(100vw - 52px));
    height: min(180px, calc(100vw - 52px));
  }
  .image-part img {
    max-width: min(100%, calc(100vw - 36px));
    max-height: 300px;
  }
}
</style>
