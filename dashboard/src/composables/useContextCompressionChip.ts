// Author: elecvoid243
// Date: 2026-10-08
//
// Owns the life of the composer's compression chip: the notice is an event,
// the chip is a transient view of it, and the ring tooltip keeps the numbers
// reachable after the chip is gone.
//
// Kept as a composable rather than inline in ChatInput because the timing is
// the contract here (fresh event → 6s window, duplicate delivery → no restart,
// leaving the session → gone) and mounting the composer to test it would drag
// in the whole input surface.

import {
  computed,
  onScopeDispose,
  ref,
  watch,
  type ComputedRef,
  type Ref,
} from "vue";

import {
  contextCompressionChip,
  contextCompressionHistoryLine,
  type CompressionTranslate,
  type ContextCompressionChip,
  type ContextCompressionEntry,
} from "./contextCompressionNotice";

/** How long the chip stays on screen before fading out. */
const CHIP_HOLD_MS = 6000;

export interface ContextCompressionChipApi {
  /** Chip to render; null while the session has no compression to show. */
  chip: ComputedRef<ContextCompressionChip | null>;
  /** True while the chip is inside its display window. */
  visible: Ref<boolean>;
  /** Ring tooltip line, kept after the chip fades. */
  historyLine: ComputedRef<string | null>;
}

/**
 * Track the current session's last compression notice.
 *
 * @param source Getter for the notice of the session on screen; null when the
 *   session changed or nothing was ever compressed.
 * @param translate Module-scoped translation function.
 * @returns Chip state, visibility flag and the ring tooltip line.
 */
export function useContextCompressionChip(
  source: () => ContextCompressionEntry | null | undefined,
  translate: CompressionTranslate,
): ContextCompressionChipApi {
  const visible = ref(false);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let shownKey: string | null = null;

  function cancel() {
    if (timer !== undefined) {
      clearTimeout(timer);
      timer = undefined;
    }
  }

  watch(
    source,
    (entry) => {
      if (!entry) {
        cancel();
        shownKey = null;
        visible.value = false;
        return;
      }
      // A replay of the same event (a snapshot re-attaching to a live stream)
      // must not extend a window the user is already watching.
      if (entry.key === shownKey) return;

      shownKey = entry.key;
      cancel();
      visible.value = true;
      timer = setTimeout(() => {
        timer = undefined;
        visible.value = false;
      }, CHIP_HOLD_MS);
    },
    { immediate: true },
  );

  onScopeDispose(cancel);

  const entry = computed(() => source() ?? null);

  return {
    chip: computed(() =>
      entry.value
        ? contextCompressionChip(entry.value.notice, translate)
        : null,
    ),
    visible,
    historyLine: computed(() =>
      entry.value
        ? contextCompressionHistoryLine(entry.value.notice, translate)
        : null,
    ),
  };
}
