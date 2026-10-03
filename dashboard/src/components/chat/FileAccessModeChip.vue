<!--
  FileAccessModeChip — compact dropdown file access mode switcher
  (full / readonly / workspace). Replaces SpcodePlanModeChip: readonly
  IS plan mode, with the state owned by AstrBot core (per-umo).

  Dropdown form (was a 3-segment control): the three segments dominated
  the input status row's width, so the switcher collapses to a single
  chip-height button showing the active mode. Menu/row styling follows
  the SpcodeProjectIndicator worktree picker pattern.

  The workspace row carries a gear button opening the whitelist dialog:
  implicit roots (session workspace, loaded spcode project, temp dirs)
  are listed read-only; custom roots can be added/removed and are saved
  per-session via the core file-access-mode/roots endpoint.
-->
<script setup lang="ts">
import { computed, ref } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import {
  useFileAccessMode,
  type FileAccessMode,
} from "@/composables/useFileAccessMode";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { useSpcodeWorktrees } from "@/composables/useSpcodeWorktrees";

interface Props {
  umo: string | null;
}

const props = defineProps<Props>();

const { tm } = useModuleI18n("features/chat");
const { status, setRoots } = useFileAccessMode();
const { status: spcodeStatus } = useSpcodeProjectStatus();
const worktrees = useSpcodeWorktrees();

const emit = defineEmits<{
  (e: "change", mode: FileAccessMode): void;
}>();

const menuOpen = ref(false);

interface ModeOption {
  value: FileAccessMode;
  label: string;
  icon: string;
  /** Hover description of what the mode permits. */
  hint: string;
}

const modeValue = computed<FileAccessMode>(
  () => status.value.mode ?? status.value.defaultMode ?? "full",
);

const options = computed<ModeOption[]>(() => [
  {
    value: "full",
    label: tm("fileAccessChip.fullLabel"),
    icon: "mdi-lock-open-outline",
    hint: tm("fileAccessChip.fullHint"),
  },
  {
    value: "readonly",
    label: tm("fileAccessChip.readonlyLabel"),
    icon: "mdi-file-eye-outline",
    hint: tm("fileAccessChip.readonlyHint"),
  },
  {
    value: "workspace",
    label: tm("fileAccessChip.workspaceLabel"),
    icon: "mdi-folder-edit-outline",
    hint: tm("fileAccessChip.workspaceHint"),
  },
]);

const activeOption = computed<ModeOption>(
  () =>
    options.value.find((o) => o.value === modeValue.value) ?? options.value[0],
);

function select(mode: FileAccessMode): void {
  menuOpen.value = false;
  if (mode === modeValue.value) return;
  emit("change", mode);
}

// ── Workspace whitelist dialog ─────────────────────────────────────

type FixedKind = "workspace" | "temp" | "project" | "worktree";

interface FixedEntry {
  path: string;
  kind: FixedKind;
}

const rootsDialogOpen = ref(false);
const draftRoots = ref<string[]>([]);
const newRootInput = ref("");
const newRootError = ref("");
const savingRoots = ref(false);

// Windows drive letter, POSIX root, or UNC prefix.
const ABSOLUTE_PATH_RE = /^([a-zA-Z]:[\\/]|\/|\\\\)/;

// Active worktree from the picker singleton (kept fresh by the project
// indicator next to this chip). Enforcement-wise the plugin already
// registers it as a dynamic write root on every LLM request; this list
// only mirrors what is currently active.
const activeWorktree = computed<string | null>(() => {
  const s = worktrees.state.value;
  return s.kind === "ok" ? s.snapshot.meta.activeWorktree ?? null : null;
});

const fixedEntries = computed<FixedEntry[]>(() => {
  const entries: FixedEntry[] = status.value.fixedRoots.map((r) => ({
    path: r.path,
    kind: r.kind as FixedKind,
  }));
  const projectDir = spcodeStatus.value.directory;
  if (spcodeStatus.value.loaded && projectDir) {
    entries.push({ path: projectDir, kind: "project" });
  }
  if (activeWorktree.value) {
    entries.push({ path: activeWorktree.value, kind: "worktree" });
  }
  return entries;
});

function kindLabel(kind: FixedKind): string {
  return tm(`fileAccessChip.rootsDialog.kind_${kind}`);
}

function openRootsDialog(): void {
  menuOpen.value = false;
  draftRoots.value = [...status.value.customRoots];
  newRootInput.value = "";
  newRootError.value = "";
  rootsDialogOpen.value = true;
}

function addDraftRoot(): void {
  const text = newRootInput.value.trim();
  if (!ABSOLUTE_PATH_RE.test(text)) {
    newRootError.value = tm("fileAccessChip.rootsDialog.invalidPath");
    return;
  }
  if (draftRoots.value.some((r) => r.toLowerCase() === text.toLowerCase())) {
    newRootInput.value = "";
    newRootError.value = "";
    return;
  }
  draftRoots.value.push(text);
  newRootInput.value = "";
  newRootError.value = "";
}

function removeDraftRoot(index: number): void {
  draftRoots.value.splice(index, 1);
}

async function saveRoots(): Promise<void> {
  if (!props.umo || savingRoots.value) return;
  savingRoots.value = true;
  const ok = await setRoots(props.umo, draftRoots.value);
  savingRoots.value = false;
  if (ok) {
    rootsDialogOpen.value = false;
  }
}
</script>

<template>
  <v-menu v-model="menuOpen" location="bottom end" transition="none">
    <template #activator="{ props: menuProps }">
      <v-tooltip location="bottom" :open-delay="200">
        <template #activator="{ props: tipProps }">
          <button
            v-bind="{ ...tipProps, ...menuProps }"
            type="button"
            class="fa-chip-btn"
            :class="{ 'fa-chip-btn--open': menuOpen }"
            :aria-label="tm('fileAccessChip.menuTitle')"
          >
            <v-icon
              size="14"
              class="fa-chip-btn__icon"
              :class="`fa-chip-btn__icon--${activeOption.value}`"
            >
              {{ activeOption.icon }}
            </v-icon>
            <span class="fa-chip-btn__label">{{ activeOption.label }}</span>
            <v-icon size="12" class="fa-chip-btn__chevron"
              >mdi-menu-down</v-icon
            >
          </button>
        </template>
        <span>{{ tm("fileAccessChip.tooltip") }}</span>
      </v-tooltip>
    </template>
    <v-card>
      <v-card-text>
        <div class="fa-chip-title">{{ tm("fileAccessChip.menuTitle") }}</div>
        <div v-for="opt in options" :key="opt.value" class="fa-chip-row">
          <v-tooltip location="top" :open-delay="200">
            <template #activator="{ props: hintProps }">
              <button
                v-bind="hintProps"
                type="button"
                class="fa-chip-row__main"
                :class="{
                  'fa-chip-row__main--selected': opt.value === modeValue,
                }"
                @click="select(opt.value)"
              >
                <v-icon
                  size="14"
                  class="fa-chip-row__icon"
                  :class="`fa-chip-row__icon--${opt.value}`"
                >
                  {{ opt.icon }}
                </v-icon>
                <span class="fa-chip-row__label">{{ opt.label }}</span>
                <v-icon
                  v-if="opt.value === modeValue"
                  size="14"
                  class="fa-chip-row__check"
                >
                  mdi-check
                </v-icon>
              </button>
            </template>
            <span>{{ opt.hint }}</span>
          </v-tooltip>
          <v-tooltip
            v-if="opt.value === 'workspace'"
            location="top"
            :open-delay="200"
          >
            <template #activator="{ props: tipProps }">
              <button
                v-bind="tipProps"
                type="button"
                class="fa-chip-row__gear"
                :aria-label="tm('fileAccessChip.rootsDialog.gearTooltip')"
                @click.stop="openRootsDialog"
              >
                <v-icon size="14">mdi-cog-outline</v-icon>
              </button>
            </template>
            <span>{{ tm("fileAccessChip.rootsDialog.gearTooltip") }}</span>
          </v-tooltip>
        </div>
      </v-card-text>
    </v-card>
  </v-menu>

  <v-dialog v-model="rootsDialogOpen" max-width="560">
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("fileAccessChip.rootsDialog.title") }}
      </v-card-title>
      <v-card-text class="pt-2">
        <div class="fa-roots-section">
          {{ tm("fileAccessChip.rootsDialog.autoSection") }}
        </div>
        <div v-for="(entry, i) in fixedEntries" :key="i" class="fa-roots-row">
          <v-icon size="14" class="fa-roots-row__lock">mdi-lock-outline</v-icon>
          <span class="fa-roots-row__path" :title="entry.path">
            {{ entry.path }}
          </span>
          <span class="fa-roots-row__badge">{{ kindLabel(entry.kind) }}</span>
        </div>

        <div class="fa-roots-section">
          {{ tm("fileAccessChip.rootsDialog.customSection") }}
        </div>
        <div v-if="!draftRoots.length" class="fa-roots-empty">
          {{ tm("fileAccessChip.rootsDialog.emptyHint") }}
        </div>
        <div v-for="(root, i) in draftRoots" :key="root" class="fa-roots-row">
          <v-icon size="14" class="fa-roots-row__lock"
            >mdi-folder-outline</v-icon
          >
          <span class="fa-roots-row__path" :title="root">{{ root }}</span>
          <button
            type="button"
            class="fa-roots-row__remove"
            :aria-label="tm('fileAccessChip.rootsDialog.removeLabel')"
            @click="removeDraftRoot(i)"
          >
            <v-icon size="14">mdi-close</v-icon>
          </button>
        </div>
        <div class="fa-roots-add">
          <v-text-field
            v-model="newRootInput"
            density="compact"
            variant="outlined"
            :hide-details="!newRootError"
            :error-messages="newRootError || undefined"
            :placeholder="tm('fileAccessChip.rootsDialog.addPlaceholder')"
            @keydown.enter.prevent="addDraftRoot"
          />
          <v-btn variant="tonal" size="small" @click="addDraftRoot">
            {{ tm("fileAccessChip.rootsDialog.addButton") }}
          </v-btn>
        </div>
      </v-card-text>
      <v-card-actions class="px-6 pb-4">
        <v-spacer />
        <v-btn variant="text" @click="rootsDialogOpen = false">
          {{ tm("fileAccessChip.rootsDialog.cancelButton") }}
        </v-btn>
        <v-btn
          variant="tonal"
          :loading="savingRoots"
          :disabled="!umo"
          @click="saveRoots"
        >
          {{ tm("fileAccessChip.rootsDialog.saveButton") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
/* Resting state carries a faint 1px border so the capsule reads as a
   clickable control; hover/open strengthen it and reveal a wash. */
.fa-chip-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  box-sizing: border-box;
  height: 28px;
  padding: 0 9px;
  border: 1px solid var(--sp-chip-border);
  border-radius: 8px;
  background: transparent;
  color: var(--sp-text-muted);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color 150ms ease,
    border-color 150ms ease,
    color 150ms ease;
}

.fa-chip-btn:hover {
  background: var(--sp-ghost-hover-bg, rgba(var(--v-theme-on-surface), 0.055));
  border-color: var(--sp-chip-border-strong);
  color: var(--sp-text-primary);
}

.fa-chip-btn--open {
  background: var(--sp-ghost-open-bg, rgba(var(--v-theme-on-surface), 0.07));
  border-color: var(--sp-chip-border-strong);
  color: var(--sp-text-primary);
}

.fa-chip-btn:active {
  background: var(--sp-chip-active-bg);
}

.fa-chip-btn:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

/* Longer mode labels ("Read-only mode") still fit without stretching
   the status row. */
.fa-chip-btn__label {
  max-width: 110px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fa-chip-btn__chevron {
  opacity: 0.7;
}

/* Mode colors read at a glance: full is a broad grant (red = caution),
   readonly is safe (green), workspace is scoped writes (yellow). */
.fa-chip-btn__icon--full {
  color: rgb(var(--v-theme-error));
}

.fa-chip-btn__icon--readonly {
  color: rgb(var(--v-theme-success));
}

.fa-chip-btn__icon--workspace {
  color: rgb(var(--v-theme-warning));
}

.fa-chip-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--sp-text-primary);
  margin-bottom: 2px;
}

.fa-chip-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
}

.fa-chip-row__main {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  padding: 4px 6px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--sp-text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.fa-chip-row__main:hover {
  background: var(--sp-chip-hover-bg);
}

.fa-chip-row__gear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--sp-text-primary);
  opacity: 0.75;
  cursor: pointer;
}

.fa-chip-row__gear:hover {
  background: var(--sp-chip-hover-bg);
  opacity: 1;
}

.fa-chip-row__icon--full {
  color: rgb(var(--v-theme-error));
}

.fa-chip-row__icon--readonly {
  color: rgb(var(--v-theme-success));
}

.fa-chip-row__icon--workspace {
  color: rgb(var(--v-theme-warning));
}

.fa-chip-row__check {
  margin-left: auto;
}

/* ── Whitelist dialog ── */
.fa-roots-section {
  font-size: 12px;
  font-weight: 600;
  color: var(--sp-text-primary);
  margin: 10px 0 4px;
}

.fa-roots-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 2px;
  font-size: 12px;
  color: var(--sp-text-primary);
}

.fa-roots-row__lock {
  opacity: 0.6;
  flex-shrink: 0;
}

.fa-roots-row__path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: ltr;
}

.fa-roots-row__badge {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 6px;
  background: var(--sp-chip-bg);
  border: 1px solid var(--sp-chip-border);
  font-size: 11px;
  opacity: 0.85;
}

.fa-roots-row__remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  opacity: 0.6;
  cursor: pointer;
}

.fa-roots-row__remove:hover {
  background: var(--sp-chip-hover-bg);
  opacity: 1;
}

.fa-roots-empty {
  font-size: 12px;
  opacity: 0.6;
  padding: 2px 2px 4px;
}

.fa-roots-add {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
</style>
