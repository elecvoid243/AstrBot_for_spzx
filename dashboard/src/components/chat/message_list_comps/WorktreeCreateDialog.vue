<!-- Author: elecvoid243
     Date: 2026-06-27
     Spec: docs/superpowers/specs/2026-06-27-git-worktree-frontend-management-design.md §2.2.A
     Form for POST /spcode/git-worktree-add. Emits 'submit' on success
     with validated params; emits 'cancel' on close.

     2026-09-17: the start point and the (force-mode) branch are now
     v-comboboxes fed by the sidebar's git-branches snapshot, so they can be
     picked instead of typed. Typing a start point that does not exist reached
     git as `fatal: invalid reference: <ref>` — the dialog should make that
     typo hard to commit in the first place. Typing stays possible because
     SHAs and other refs are valid start points. -->
<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import { useSpcodeSession } from "@/composables/useSpcodeSession";
import type { WorktreeAddParams } from "@/composables/useSpcodeWorktrees";

const { tm } = useModuleI18n("features/chat");
const session = useSpcodeSession();

const props = defineProps<{
  modelValue: boolean;
  isSubmitting?: boolean;
  /** Existing branch names of the current project (combobox items). */
  branches?: string[];
  /** Tag names — also valid start points for `git worktree add`. */
  tags?: string[];
  /** Branch the project is currently on, used as the default start point. */
  currentBranch?: string | null;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "submit", params: WorktreeAddParams): void;
  (e: "cancel"): void;
}>();

// ── Form state (spec §2.2.A table) ────────────────────────
type CreateMode = "create" | "force" | "detach";
const createMode = ref<CreateMode>("create");
const branch = ref<string>("");
const path = ref<string>("");
const base = ref<string>("");

// Reset form state every time the dialog is opened.
//
// The parent renders <WorktreeCreateDialog v-model="createDialogOpen"/>
// and toggles that ref to open/close. The component itself is mounted
// once, so without this reset, the branch/path/base refs would carry
// the previous session's input across dialog opens — leading to
// confusion (user thinks they cleared the form, but it reopens with
// last week's "feat/foo" branch pre-filled and the path field shows
// a stale absolute path that may no longer be valid).
//
// We watch for the transition false → true on modelValue. We do NOT
// reset on every change (that would clear the form mid-edit if the
// parent toggled modelValue for any other reason).
function resetForm(): void {
  createMode.value = "create";
  branch.value = "";
  path.value = "";
  // 2026-09-17: default to the project's current branch. It used to be the
  // hard-coded literal "main", which does not exist in every repo — that is
  // exactly how a user ended up sending an invalid start point to git.
  // Empty means "let git decide" (the params builder omits an empty base).
  base.value = props.currentBranch ?? "";
  userEditedPath.value = false;
}
watch(
  () => props.modelValue,
  (open, prev) => {
    if (open && !prev) resetForm();
  },
);

/** Start points are any existing ref: branches first, then tags, deduped. */
const startPointItems = computed(() => {
  const seen = new Set<string>();
  for (const name of [...(props.branches ?? []), ...(props.tags ?? [])]) {
    if (name) seen.add(name);
  }
  return [...seen];
});

/** In force mode the branch must already exist, so only branches are listed. */
const branchItems = computed(() => props.branches ?? []);

const projectRoot = computed(
  () => session.directory.value ?? "",
);

// Branch sanitization for default path suggestion.
function defaultPath(branchName: string, root: string): string {
  if (!root || !branchName) return "";
  const sep = root.includes("\\") ? "\\" : "/";
  return `${root}${sep}.worktrees${sep}${branchName.replace(/\//g, "-")}`;
}

// Track whether the user has manually edited the path field, so the
// branch→path auto-suggestion stops overwriting their choice once
// they've touched it.
//
// Important: we must NOT use `watch(path, ...)` to detect user edits,
// because that watcher also fires for our own programmatic assignments
// in the branch watcher below — which would mark the suggestion as
// "user-edited" on the very first keystroke and freeze the path. We
// detect real user input via the @update:model-value handler on the
// v-text-field instead, which only fires for DOM input events.
const userEditedPath = ref(false);
function onPathUserInput(next: string): void {
  path.value = next;
  userEditedPath.value = true;
}
watch(branch, (b) => {
  if (!userEditedPath.value && b) {
    path.value = defaultPath(b, projectRoot.value);
  }
});

// Field-level validation (aligns with backend 5-step preflight).
const errors = computed(() => {
  const errs: { branch?: string; path?: string; base?: string } = {};
  if (createMode.value !== "detach" && !branch.value.trim()) {
    errs.branch = tm(
      "spcodeProjectLoad.diffSidebar.worktreeMgmt.create.branchRequired",
    );
  }
  if (!path.value.trim()) {
    errs.path = tm(
      "spcodeProjectLoad.diffSidebar.worktreeMgmt.create.pathRequired",
    );
  }
  return errs;
});

const canSubmit = computed(
  () => Object.keys(errors.value).length === 0 && !props.isSubmitting,
);

function onCancel(): void {
  if (props.isSubmitting) return;
  emit("update:modelValue", false);
  emit("cancel");
}

function onSubmit(): void {
  if (!canSubmit.value) return;
  const params: WorktreeAddParams = {
    path: path.value.trim(),
    umo: session.umo.value,
  };
  if (createMode.value !== "detach") {
    params.branch = branch.value.trim();
  }
  if (createMode.value === "create") {
    params.create = true;
    if (base.value.trim()) params.base = base.value.trim();
  } else if (createMode.value === "force") {
    params.force = true;
  } else if (createMode.value === "detach") {
    params.detach = true;
  }
  emit("submit", params);
}
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', $event)"
    persistent
    max-width="520"
  >
    <v-card>
      <v-card-title class="text-h6">
        {{ tm("spcodeProjectLoad.diffSidebar.worktreeMgmt.create.title") }}
      </v-card-title>
      <v-card-text>
        <!-- Mode radio group (mutually exclusive) -->
        <div class="worktree-create-mode">
          <v-radio-group v-model="createMode" inline density="compact">
            <v-radio
              value="create"
              :label="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.create.modeCreate')"
            />
            <v-radio
              value="force"
              :label="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.create.modeForce')"
            />
            <v-radio
              value="detach"
              :label="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.create.modeDetach')"
            />
          </v-radio-group>
          <v-chip
            v-if="createMode === 'force'"
            size="x-small"
            color="warning"
            variant="tonal"
            class="ml-2"
          >
            <v-icon start size="12">mdi-alert</v-icon>
            {{ tm("spcodeProjectLoad.diffSidebar.worktreeMgmt.create.modeForceWarning") }}
          </v-chip>
        </div>

        <!-- Branch. In create mode this is the NEW branch name, so it stays a
             free-text field. In force mode the branch must already exist, so
             it becomes a combobox over the existing branches (picking beats
             typing here). Disabled in detach mode — no branch is involved.
             autocomplete="off" + a stable name disables Chrome/Edge's
             autofill heuristics, which can otherwise prefill the field with a
             branch name from a previous session even after our resetForm()
             above. Vuetify forwards the `autocomplete` prop to the inner
             <input>. -->
        <v-combobox
          v-if="createMode === 'force'"
          v-model="branch"
          :items="branchItems"
          :label="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.create.branch')"
          :hint="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.create.branchHint')"
          :error-messages="errors.branch ? [errors.branch] : []"
          density="comfortable"
          variant="outlined"
          class="mt-3"
          autocomplete="off"
          name="wtc-branch"
        />
        <v-text-field
          v-else
          v-model="branch"
          :label="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.create.branch')"
          :hint="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.create.branchHint')"
          :error-messages="errors.branch ? [errors.branch] : []"
          :disabled="createMode === 'detach'"
          density="comfortable"
          variant="outlined"
          class="mt-3"
          autocomplete="off"
          name="wtc-branch"
        />

        <!-- Path (absolute). Use @update:model-value instead of
             v-model so we can distinguish user typing from programmatic
             writes done by the branch→path watcher. See comment on
             onPathUserInput. autocomplete="off" prevents the browser
             from saving / restoring absolute filesystem paths from a
             previous session, which would be a security/UX problem
             (paths can reveal project structure). -->
        <v-text-field
          :model-value="path"
          :label="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.create.path')"
          :hint="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.create.pathHint')"
          :error-messages="errors.path ? [errors.path] : []"
          density="comfortable"
          variant="outlined"
          class="mt-2"
          autocomplete="off"
          name="wtc-path"
          @update:model-value="onPathUserInput"
        />

        <!-- Base (only in create mode): the start point of the new branch.
             A combobox over the project's branches + tags, defaulting to the
             current branch. Free text stays allowed (SHAs / any ref), so an
             unknown value is still possible — the backend answers with
             `cannot_checkout_missing` and the sidebar renders that text. -->
        <v-combobox
          v-if="createMode === 'create'"
          v-model="base"
          :items="startPointItems"
          :label="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.create.base')"
          :hint="tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.create.baseHint')"
          density="comfortable"
          variant="outlined"
          class="mt-2"
          autocomplete="off"
          name="wtc-base"
        />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="isSubmitting" @click="onCancel">
          {{ tm("spcodeProjectLoad.diffSidebar.worktreeMgmt.create.cancel") }}
        </v-btn>
        <v-btn
          variant="flat"
          color="primary"
          :loading="isSubmitting"
          :disabled="!canSubmit"
          @click="onSubmit"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.worktreeMgmt.create.submit") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.worktree-create-mode {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
</style>
