// useSpcodeSession.ts
//
// Session-scoped identity for every spcode surface that talks to a
// repository (2026-09-16, elecvoid243).
//
// Why: the spcode status singleton used to be read directly by 71 call
// sites, so any code path that could make the singleton hold another
// session's project would silently address the wrong repository. The
// context makes "which session is this component acting for" explicit
// and injectable, while keeping composable signatures unchanged.
import {
  computed,
  getCurrentInstance,
  hasInjectionContext,
  inject,
  provide,
  type ComputedRef,
  type InjectionKey,
} from "vue";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";

export interface SpcodeSessionContext {
  /** This session's umo, or null when it cannot be resolved yet. */
  umo: ComputedRef<string | null>;
  /** This session's loaded project root, or null. */
  directory: ComputedRef<string | null>;
  /** False when there is no provider (fallback to the shared status). */
  scoped: boolean;
}

export const SPCODE_SESSION_KEY: InjectionKey<SpcodeSessionContext> =
  Symbol("spcode:session");

/** Provide the session context from a page-level component (Chat.vue). */
export function provideSpcodeSession(ctx: {
  umo: ComputedRef<string | null>;
  directory: ComputedRef<string | null>;
}): void {
  provide(SPCODE_SESSION_KEY, { ...ctx, scoped: true });
}

/**
 * Read the session context.
 *
 * Args:
 *   options.requireScoped: set by composables that mutate a repository —
 *     a dev warning then flags the (unexpected) fallback path. The
 *     warning fires only INSIDE a component instance (a real "someone
 *     forgot to provide" mistake); callers outside a component (specs,
 *     standalone utilities) stay silent on purpose.
 *
 * Returns:
 *   The injected context, or a shared-status-backed fallback.
 */
export function useSpcodeSession(
  options: { requireScoped?: boolean } = {},
): SpcodeSessionContext {
  // hasInjectionContext() keeps this callable from specs (no component).
  const injected = hasInjectionContext()
    ? inject(SPCODE_SESSION_KEY, null)
    : null;
  if (injected) return injected;

  const { status } = useSpcodeProjectStatus();
  // The warning targets a component tree that forgot to provide. Specs
  // are excluded via MODE: several existing suites mount a throwaway
  // component (getCurrentInstance() !== null) purely to host the
  // composable, and warning there would spam test output without any
  // real misuse to report.
  if (
    options.requireScoped &&
    import.meta.env.DEV &&
    import.meta.env.MODE !== "test" &&
    getCurrentInstance() !== null
  ) {
    console.warn(
      "[useSpcodeSession] no session context: a repository operation will " +
        "fall back to the shared status. Render this composable inside the " +
        "chat page (provideSpcodeSession) to scope it to a session.",
    );
  }
  return {
    umo: computed(() => status.value.umo),
    directory: computed(() => status.value.directory),
    scoped: false,
  };
}
