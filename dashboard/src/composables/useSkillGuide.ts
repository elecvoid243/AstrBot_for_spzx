// src/composables/useSkillGuide.ts
//
// Singleton state for the Skill Guide plugin
// (`astrbot_plugin_skill_guide`, routes under `/skill-guide/*`).
//
// Mirrors the pattern of `useSpcodePlanMode.ts`:
//
//   - one module-level ref set so every consumer (the hover menu
//     button, the pending badges row in ChatInput) reads the same
//     state without coordinating fetches;
//   - explicit mutation methods (`setSession` / `toggleSkill` /
//     `clearAll` / `consumeQueued` / `reset`) as the only writers;
//   - a soft-fail `refresh` that never throws to callers.
//
// Semantics (from the plugin README, v1):
//
//   - The plugin exposes a per-session (umo) queue of one-shot skill
//     "nudges". When the next LLM request for that session fires, the
//     plugin injects a guidance prompt into `extra_user_content_parts`
//     and drains the queue — persona and the skill's global active
//     state are NOT touched.
//   - The frontend only *queues* (POST /skill-guide/load). The actual
//     injection happens server-side on the next request, so the
//     pending badges are cleared optimistically when the message is
//     sent (see `consumeQueued`).
//   - "Show all skills" (default off) widens the menu list to EVERY
//     skill in data/skills (core GET /skills). Skills not mounted by
//     the persona render gray but remain loadable — the plugin accepts
//     any skill that exists on disk (the nudge only carries the
//     SKILL.md path).
//
// Author: elecvoid243
// Last-Modified: 2026-08-16

import { computed, ref } from 'vue'
import { pluginExtensionApi, skillApi } from '@/api/v1'

/** One entry of GET /skill-guide/active -> data.skills[]. */
export interface SkillGuideSkill {
  name: string
  description: string
  path: string
  source_type: string
  /** Global activation flag (core GET /skills). Absent on
   *  /skill-guide/active entries, which are already session-effective. */
  active?: boolean
  /** Plugin activation flag for source_type === "plugin" (core
   *  GET /skills). Absent on /skill-guide/active entries. */
  plugin_active?: boolean
}

/** One rendered row of the skill menu: a skill plus its persona state. */
export interface SkillGuideDisplaySkill extends SkillGuideSkill {
  /** False = present in data/skills but NOT mounted by the persona (gray). */
  mounted: boolean
}

/** GET /skill-guide/active -> data. */
export interface SkillGuideActivePayload {
  persona: { id: string; name: string } | null
  skills: SkillGuideSkill[]
}

/** POST /skill-guide/load -> data. */
export interface SkillGuideLoadPayload {
  skill_name: string
  queued: boolean
}

/** POST /skill-guide/clear -> data. */
export interface SkillGuideClearPayload {
  cleared: string[]
}

const ACTIVE_PATH = 'skill-guide/active'
const LOAD_PATH = 'skill-guide/load'
const CLEAR_PATH = 'skill-guide/clear'

// localStorage key for the "show every skill in data/skills" preference
// (chat.* prefix, same family as chat.transportMode in Chat.vue).
const SHOW_ALL_KEY = 'chat.skillGuide.showAll'

// --- module-level singleton state -------------------------------------------

/** Current session's unified_msg_origin (null = no session). */
const sessionUmo = ref<string | null>(null)

/** True once the plugin answered /skill-guide/active successfully. Used as
 *  the visibility gate for the whole UI (plugin not installed/disabled
 *  -> the button simply never appears). */
const available = ref(false)

const loading = ref(false)
const loadFailed = ref(false)

/** Resolved persona (if any) for the current session. */
const persona = ref<{ id: string; name: string } | null>(null)

/** Skills effective for the current session (plugin/persona filtered). */
const skills = ref<SkillGuideSkill[]>([])

/** Skill names queued for the NEXT LLM request of this session. */
const queued = ref<string[]>([])

/**
 * "Show all skills" toggle (default off = persona-mounted only). When on,
 * the menu additionally lists every skill scanned from data/skills via the
 * core GET /skills API — skills not mounted by the persona render gray but
 * stay clickable (a manual load only needs the SKILL.md path hint).
 */
const showAll = ref(false)
try {
  showAll.value = localStorage.getItem(SHOW_ALL_KEY) === '1'
} catch {
  showAll.value = false
}

/** Every skill in data/skills (session-independent, cached after fetch). */
const allSkills = ref<SkillGuideSkill[]>([])
const allSkillsLoading = ref(false)
const allSkillsFailed = ref(false)
let allSkillsFetched = false

/** Rows actually rendered by the menu (all rows mounted when showAll off). */
const displaySkills = computed<SkillGuideDisplaySkill[]>(() => {
  if (!showAll.value) {
    return skills.value.map((skill) => ({ ...skill, mounted: true }))
  }
  const mountedNames = new Set(skills.value.map((skill) => skill.name))
  return allSkills.value
    .map((skill) => ({ ...skill, mounted: mountedNames.has(skill.name) }))
    .sort(
      (a, b) =>
        Number(b.mounted) - Number(a.mounted) || a.name.localeCompare(b.name),
    )
})

/**
 * Candidate skills for the "/" command palette: session-effective skills
 * (persona-mounted, always included) unioned with the globally enabled
 * ones from core GET /skills. With "show all" on, every skill on disk is
 * listed too. Session-effective entries come first, then alphabetical.
 */
const candidateSkills = computed<SkillGuideDisplaySkill[]>(() => {
  const byName = new Map<string, SkillGuideDisplaySkill>()
  for (const skill of skills.value) {
    byName.set(skill.name, { ...skill, mounted: true })
  }
  for (const skill of allSkills.value) {
    if (byName.has(skill.name)) continue
    const globallyEnabled =
      skill.active !== false &&
      (skill.source_type !== "plugin" || skill.plugin_active !== false)
    if (!showAll.value && !globallyEnabled) continue
    byName.set(skill.name, { ...skill, mounted: false })
  }
  return [...byName.values()].sort(
    (a, b) =>
      Number(b.mounted) - Number(a.mounted) || a.name.localeCompare(b.name),
  )
})

/**
 * Composable returning the Skill Guide singleton.
 *
 * The dashboard only writes to the refs through the explicit methods
 * below. `ChatInput.vue` is the single owner of `setSession` (driven
 * by the sessionId watcher); the button and the badges row only read.
 */
export function useSkillGuide() {
  /**
   * Point the singleton at a session and (re)fetch its active skills.
   * Passing `null` detaches (queue + list are wiped).
   */
  async function setSession(umo: string | null): Promise<void> {
    if (umo === sessionUmo.value && umo !== null) {
      return // same session — nothing to reload
    }
    sessionUmo.value = umo
    queued.value = []
    persona.value = null
    skills.value = []
    loadFailed.value = false
    if (umo) {
      await refresh(umo)
      // The "/" command palette needs the global skill inventory even
      // when the "show all" toggle is off (candidateSkills unions it in),
      // so warm the cache on session mount. Gated on the plugin being
      // available (no point querying /skills without it); soft-fail.
      if (available.value && !allSkillsFetched && !allSkillsLoading.value) {
        void refreshAllSkills()
      }
    } else {
      available.value = false
    }
  }

  /**
   * Soft-fail fetch of the session's active skills. On success the
   * plugin is deemed available (the UI gate flips on). On failure we
   * keep the last known list and set `loadFailed` so the menu can
   * render an inline retry affordance.
   */
  async function refresh(umo: string | null = sessionUmo.value): Promise<void> {
    if (!umo) return
    loading.value = true
    loadFailed.value = false
    try {
      const res = await pluginExtensionApi.get<SkillGuideActivePayload>(
        ACTIVE_PATH,
        { params: { umo } },
      )
      const data = res.data?.data
      if (!data || !Array.isArray(data.skills)) {
        throw new Error('malformed /skill-guide/active payload')
      }
      persona.value = data.persona ?? null
      skills.value = data.skills
      available.value = true
    } catch {
      // Soft-fail: plugin missing/disabled or transient network error.
      loadFailed.value = true
    } finally {
      loading.value = false
    }
  }

  /**
   * Toggle "show all skills" and lazily fetch the full data/skills list
   * (core GET /skills) on first enable. The preference is persisted in
   * localStorage; the cached list survives across sessions.
   */
  async function setShowAll(value: boolean): Promise<void> {
    showAll.value = value
    try {
      localStorage.setItem(SHOW_ALL_KEY, value ? '1' : '0')
    } catch {
      // Ignore localStorage failures (private mode etc.).
    }
    if (value && !allSkillsFetched && !allSkillsLoading.value) {
      await refreshAllSkills()
    }
  }

  /**
   * Soft-fail fetch of every skill in data/skills via the core GET /skills
   * API (same source the Skills management page uses). Accepts both the
   * legacy array and the {skills: []} envelope shapes.
   */
  async function refreshAllSkills(): Promise<void> {
    allSkillsLoading.value = true
    allSkillsFailed.value = false
    try {
      const res = await skillApi.list()
      const payload = res?.data?.data
      const list = Array.isArray(payload) ? payload : payload?.skills
      if (!Array.isArray(list)) {
        throw new Error('malformed /skills payload')
      }
      allSkills.value = list
        .map(
          (skill: {
            name?: unknown
            description?: unknown
            path?: unknown
            source_type?: unknown
            active?: unknown
            plugin_active?: unknown
          }) => ({
            name: String(skill?.name ?? ''),
            description: String(skill?.description ?? ''),
            path: String(skill?.path ?? ''),
            source_type: String(skill?.source_type ?? ''),
            // Missing flags default to enabled so legacy payload shapes
            // keep listing their skills in the "/" palette.
            active: skill?.active !== false,
            plugin_active:
              skill?.plugin_active === undefined
                ? undefined
                : Boolean(skill.plugin_active),
          }),
        )
        .filter((skill) => skill.name !== '')
      allSkillsFetched = true
    } catch {
      // Soft-fail: the menu keeps showing the persona-mounted list state.
      allSkillsFailed.value = true
    } finally {
      allSkillsLoading.value = false
    }
  }

  /**
   * Toggle a skill on/off the one-shot queue.
   *
   * Queuing POSTs /skill-guide/load. Un-queuing a SINGLE skill works
   * around the plugin's v1 API (POST /skill-guide/clear only drops the
   * WHOLE session queue): we clear first, then re-queue the remaining
   * selection so only the clicked skill is removed.
   */
  async function toggleSkill(name: string): Promise<boolean> {
    const umo = sessionUmo.value
    const known =
      skills.value.some((s) => s.name === name) ||
      allSkills.value.some((s) => s.name === name)
    if (!umo || !known) return false
    if (queued.value.includes(name)) {
      const remaining = queued.value.filter((n) => n !== name)
      await clearAll()
      for (const n of remaining) {
        await queueSkill(n)
      }
      return false
    }
    return queueSkill(name)
  }

  /**
   * Queue one skill via POST /skill-guide/load and mirror it locally.
   * Shared by the select path and the re-queue step of single un-select.
   */
  async function queueSkill(name: string): Promise<boolean> {
    const umo = sessionUmo.value
    if (!umo) return false
    try {
      const res = await pluginExtensionApi.post<SkillGuideLoadPayload>(
        LOAD_PATH,
        { umo, skill_name: name },
      )
      if (res.data?.status !== "ok" || !res.data?.data?.queued) {
        throw new Error(res.data?.message ?? 'load failed')
      }
      if (!queued.value.includes(name)) {
        queued.value.push(name)
      }
      return true
    } catch {
      return false
    }
  }

  /** Drop every pending nudge for the session. */
  async function clearAll(): Promise<void> {
    const umo = sessionUmo.value
    queued.value = []
    if (!umo) return
    try {
      await pluginExtensionApi.post<SkillGuideClearPayload>(CLEAR_PATH, { umo })
    } catch {
      // Soft-fail: the queue is in-memory on the plugin side and will
      // be drained (or vanish on restart) anyway; local state wins.
    }
  }

  /**
   * Optimistically clear the local queue when a message is sent.
   * The plugin drains the session queue on the next LLM request, so
   * the badges have served their purpose by the time the request
   * fires. (One-shot semantics — the nudge does NOT apply twice.)
   */
  function consumeQueued(): void {
    queued.value = []
  }

  /**
   * Wipe the SESSION state (e.g. logout / ChatInput unmount). The showAll
   * preference and its cached data/skills list are deliberately kept —
   * they are session-independent and persist across sessions.
   */
  function reset(): void {
    sessionUmo.value = null
    available.value = false
    loading.value = false
    loadFailed.value = false
    persona.value = null
    skills.value = []
    queued.value = []
  }

  return {
    // state
    sessionUmo,
    available,
    loading,
    loadFailed,
    persona,
    skills,
    queued,
    showAll,
    allSkills,
    allSkillsLoading,
    allSkillsFailed,
    displaySkills,
    candidateSkills,
    // methods
    setSession,
    refresh,
    setShowAll,
    refreshAllSkills,
    queueSkill,
    toggleSkill,
    clearAll,
    consumeQueued,
    reset,
  }
}
