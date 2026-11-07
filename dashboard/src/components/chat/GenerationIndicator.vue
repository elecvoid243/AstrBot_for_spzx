<template>
  <svg
    class="generation-indicator"
    :class="{ 'is-slow': state === 'streaming', 'is-off': state === 'done' }"
    viewBox="0 0 18 18"
    aria-hidden="true"
  >
    <circle class="track" cx="9" cy="9" r="7.3" />
    <circle class="arc" cx="9" cy="9" r="7.3" pathLength="100" />
  </svg>
</template>

<script setup lang="ts">
// Author: elecvoid243
// Date: 2026-10-17
// Turn-activity mark for assistant messages. It replaces the avatar spinner
// the pre-ChatUI list used: the mark lives in the existing 20 px message rail,
// so it adds no column and shifts no text. Waiting and streaming are the same
// shape at two speeds — a fast orbit says "blocked on the first token", while
// a slow, dimmed orbit is a heartbeat next to text that is already flowing.
type GenerationState = "waiting" | "streaming" | "done";

withDefaults(defineProps<{ state?: GenerationState }>(), { state: "done" });
</script>

<style scoped>
.generation-indicator {
  display: block;
  width: 18px;
  height: 18px;
  color: rgb(var(--v-theme-primary));
  /* Keep the live activity signal animated, like the sidebar progress ring. */
  animation: generation-orbit 1.05s linear infinite;
}

.generation-indicator .track {
  fill: none;
  stroke: rgba(var(--v-theme-on-surface), 0.16);
  stroke-width: 2;
  animation: generation-breathe 2.1s ease-in-out infinite;
}

/* 25 of 100 path units: a 90° arc with round caps, so the leading cap reads
   as the head of the orbit. */
.generation-indicator .arc {
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-dasharray: 25 100;
}

/* Streaming: same shape, slower and dimmer (18 units = 65°). The rotation has
   to stop meaning "still waiting" the moment text starts arriving, otherwise
   it competes with the reply for attention. */
.generation-indicator.is-slow {
  animation-duration: 2.6s;
  opacity: 0.72;
}

.generation-indicator.is-slow .arc {
  stroke-dasharray: 18 100;
}

.generation-indicator.is-slow .track {
  animation: none;
}

/* Finished or failed turn: fall dark in place. The rail keeps its width, so
   the message text never shifts when the mark leaves. */
.generation-indicator.is-off {
  animation: none;
  opacity: 0;
  transition: opacity 0.2s ease-out;
}

@keyframes generation-orbit {
  to {
    transform: rotate(360deg);
  }
}

@keyframes generation-breathe {
  0%,
  100% {
    opacity: 0.5;
  }
  50% {
    opacity: 1;
  }
}
</style>
