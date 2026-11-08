<template>
  <v-tooltip location="top" max-width="320">
    <template #activator="{ props: compressionTipProps }">
      <span v-bind="compressionTipProps" class="compression-readout-slot">
        <Transition name="compression-readout">
          <span
            v-if="compressionChipVisible && compressionChip"
            class="compression-readout"
            :class="{
              'compression-readout--lossy': compressionChip.lossy,
            }"
            role="status"
            aria-live="polite"
            :aria-label="compressionChip.description"
          >
            <span class="compression-readout__label">
              {{ compressionChip.label }}
            </span>
            <span class="compression-readout__delta">
              {{ compressionChip.delta }}
            </span>
            <span
              class="compression-readout__divider"
              aria-hidden="true"
            ></span>
          </span>
        </Transition>
      </span>
    </template>
    <span class="compression-readout-tip">
      {{ compressionChip?.description }}
    </span>
  </v-tooltip>
  <v-tooltip v-if="tokenUsageVisible" location="top" max-width="320">
    <template #activator="{ props: tokenTooltipProps }">
      <span
        v-bind="tokenTooltipProps"
        ref="tokenRingRef"
        class="token-usage-indicator"
        :class="{ 'token-usage-indicator--flash': tokenRingFlashing }"
        :style="{ '--token-usage-color': tokenUsageColor }"
      >
        <v-progress-circular
          :model-value="tokenUsagePercent"
          size="24"
          width="2.5"
          class="token-usage-progress"
        />
        <!-- 2026-10-18 (elecvoid243): the pre-compression percentage
                       stays as a tick for the readout window, so the gap to the
                       shrunken arc reads as "how much was lost" without any
                       number. -->
        <svg
          v-if="compressionTickPercent !== null"
          class="token-usage-tick"
          :class="{
            'token-usage-tick--on': compressionChipVisible,
            'token-usage-tick--lossy': compressionChip?.lossy,
          }"
          viewBox="0 0 24 24"
          width="24"
          height="24"
          aria-hidden="true"
        >
          <circle
            cx="12"
            cy="12"
            r="10.75"
            pathLength="100"
            :style="{ strokeDashoffset: -compressionTickPercent }"
          />
        </svg>
      </span>
    </template>
    <span class="token-usage-tooltip">{{ tokenUsageTooltip }}</span>
  </v-tooltip>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type { ContextCompressionEntry } from "@/composables/contextCompressionNotice";
import { useContextCompressionChip } from "@/composables/useContextCompressionChip";
const props = defineProps<{
  tokenUsage?: {
    used: number;
    limit: number;
    percent: number;
    tooltip: string;
  } | null;
  compressionNotice?: ContextCompressionEntry | null;
  isDark?: boolean;
}>();
const { tm } = useModuleI18n("features/chat");
const tokenUsageVisible = computed(() => {
  const usage = props.tokenUsage;
  return Boolean(
    usage &&
      Number.isFinite(usage.used) &&
      Number.isFinite(usage.limit) &&
      usage.used > 0 &&
      usage.limit > 0,
  );
});

const tokenUsagePercent = computed(() => {
  const percent = props.tokenUsage?.percent || 0;
  if (!Number.isFinite(percent)) return 0;
  return Math.min(100, Math.max(0, percent));
});

const tokenUsageColor = computed(() =>
  props.isDark
    ? "rgba(var(--v-theme-on-surface), 0.82)"
    : "rgba(var(--v-theme-on-surface), 0.72)",
);

// 2026-10-08 (elecvoid243): compression notice. It renders as a readout left
// of the token-usage ring — the ring is the control whose numbers the
// compression just changed, so the notice belongs next to it; a toast floats
// over the page with no relation to the value it explains. The readout is
// transient (10s), while `compressionHistoryLine` stays in the ring tooltip so
// the numbers remain checkable after it fades.
// 2026-10-18: the ring itself flashes for 3s and keeps a tick at the old
// percentage; colour is an event signal here, not decoration.
const {
  chip: compressionChip,
  visible: compressionChipVisible,
  historyLine: compressionHistoryLine,
} = useContextCompressionChip(() => props.compressionNotice, tm);

/** Ring tooltip: token usage, then the last compression as its second line. */
const tokenUsageTooltip = computed(() =>
  [props.tokenUsage?.tooltip, compressionHistoryLine.value]
    .filter(Boolean)
    .join("\n"),
);

/** Matches the ring-alert CSS animation length. */
const TOKEN_RING_FLASH_MS = 3000;
const tokenRingFlashing = ref(false);
const tokenRingRef = ref<HTMLElement | null>(null);
let tokenRingFlashTimer: ReturnType<typeof setTimeout> | undefined;

/**
 * Old context percentage, drawn as a tick on the ring.
 *
 * The notice carries the pre-compression token count and the ring carries the
 * window limit, so the tick lands where the arc used to end. Null when either
 * side is unknown — a tick at a guessed position would be worse than none.
 */
const compressionTickPercent = computed(() => {
  const notice = props.compressionNotice?.notice;
  const limit = props.tokenUsage?.limit ?? 0;
  if (!notice || !Number.isFinite(limit) || limit <= 0) return null;
  const percent = (notice.tokensBefore / limit) * 100;
  if (!Number.isFinite(percent) || percent <= 0) return null;
  return Math.min(100, percent);
});

/** Restart the ring flash; the CSS animation drives the 3s pulse. */
async function flashTokenRing() {
  if (tokenRingFlashTimer !== undefined) clearTimeout(tokenRingFlashTimer);
  tokenRingFlashing.value = false;
  await nextTick();
  // Force a style flush so re-adding the class replays the animation.
  void tokenRingRef.value?.offsetWidth;
  tokenRingFlashing.value = true;
  tokenRingFlashTimer = setTimeout(() => {
    tokenRingFlashTimer = undefined;
    tokenRingFlashing.value = false;
  }, TOKEN_RING_FLASH_MS);
}

watch(
  () => props.compressionNotice?.key,
  (key) => {
    if (!key) return;
    void flashTokenRing();
  },
);

onBeforeUnmount(() => {
  if (tokenRingFlashTimer !== undefined) clearTimeout(tokenRingFlashTimer);
});
</script>
<style scoped>
.token-usage-indicator {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 24px;
  border-radius: 50%;
  color: var(--token-usage-color);
  position: relative;
}

/* Compression readout (2026-10-08, elecvoid243; reworked 2026-10-18): the
   notice used to be a filled 22px pill, which read as a second config chip.
   It is a status readout, not a control: borderless 11px text on the ring's
   baseline, one 1px divider tying it to the ring. A truncation only raises
   weight and brightness — colour is reserved for the ring flash and the
   history tick. */
.compression-readout-slot {
  display: inline-flex;
  align-items: center;
}

.compression-readout {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 24px;
  color: rgba(var(--v-theme-on-surface), 0.44);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.01em;
  white-space: nowrap;
  cursor: default;
}

.compression-readout__label {
  font-weight: 400;
}

.compression-readout__delta {
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), 0.58);
}

.compression-readout__divider {
  width: 1px;
  height: 11px;
  margin-left: 1px;
  background: var(--sp-chip-divider);
}

.compression-readout--lossy .compression-readout__label {
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), 0.82);
}

.compression-readout--lossy .compression-readout__delta {
  color: rgba(var(--v-theme-on-surface), 0.62);
}

.compression-readout-enter-active {
  transition:
    opacity 0.15s ease-out,
    transform 0.15s ease-out;
}

.compression-readout-leave-active {
  transition: opacity 0.3s ease-in;
}

.compression-readout-enter-from {
  opacity: 0;
  transform: translateX(2px);
}

.compression-readout-leave-to {
  opacity: 0;
}

/* Motion here is decoration — the numbers carry the meaning. */
@media (prefers-reduced-motion: reduce) {
  .compression-readout-enter-from {
    transform: none;
  }
}

/* Narrow composers drop the readout entirely: the ring flash, the history
   tick and the tooltip still carry the event. */
@media (max-width: 480px) {
  .compression-readout-slot {
    display: none;
  }
}

/* Token usage tooltip carries an explicit newline before the cache-hit
   line; pre-line is what makes it render as a second line. */
.token-usage-tooltip {
  white-space: pre-line;
}

/* 2026-10-18 (elecvoid243): the readout tooltip also has two lines (verdict +
   strategy, then the token delta). */
.compression-readout-tip {
  white-space: pre-line;
}

.token-usage-progress {
  color: currentColor;
}

.token-usage-progress :deep(.v-progress-circular__underlay) {
  color: rgba(var(--v-theme-on-surface), 0.18);
  stroke: currentColor;
  opacity: 0.24;
}

.token-usage-progress :deep(.v-progress-circular__overlay) {
  stroke: currentColor;
}

/* 2026-10-18 (elecvoid243): the ring itself is the alert — three 1s pulses in
   the theme primary with a slightly heavier stroke, then back to the plain
   progress ring. No extra element, so nothing in the toolbar shifts. The
   3.2px stroke overhangs the 24px viewBox by 0.35px, hence overflow. */
.token-usage-progress :deep(svg) {
  overflow: visible;
}

.token-usage-indicator--flash :deep(.v-progress-circular__overlay) {
  animation: token-ring-alert 3s ease-in-out forwards;
}

@keyframes token-ring-alert {
  0% {
    stroke: currentColor;
    stroke-width: 2.5;
    opacity: 1;
  }
  6% {
    stroke: rgb(var(--v-theme-primary));
    stroke-width: 3.2;
    opacity: 1;
  }
  20% {
    opacity: 0.42;
  }
  34% {
    opacity: 1;
  }
  48% {
    opacity: 0.42;
  }
  62% {
    opacity: 1;
  }
  76% {
    opacity: 0.42;
  }
  90% {
    opacity: 1;
  }
  94% {
    stroke: rgb(var(--v-theme-primary));
    stroke-width: 3.2;
    opacity: 1;
  }
  100% {
    stroke: currentColor;
    stroke-width: 2.5;
    opacity: 1;
  }
}

/* Pre-compression percentage, kept for the readout window. */
.token-usage-tick {
  position: absolute;
  inset: 0;
  fill: none;
  stroke: var(--token-usage-color);
  stroke-width: 2.5;
  stroke-linecap: round;
  stroke-dasharray: 1 100;
  transform: rotate(-90deg);
  opacity: 0;
  transition: opacity 0.5s ease;
  pointer-events: none;
}

.token-usage-tick--on {
  opacity: 0.3;
}

.token-usage-tick--lossy {
  stroke: rgb(var(--v-theme-warning));
}

.token-usage-tick--lossy.token-usage-tick--on {
  opacity: 0.55;
}

/* No pulsing: a static primary ring still marks the event without motion. */
@media (prefers-reduced-motion: reduce) {
  .token-usage-indicator--flash :deep(.v-progress-circular__overlay) {
    animation: none;
    stroke: rgb(var(--v-theme-primary));
    stroke-width: 3.2;
  }
}
</style>
