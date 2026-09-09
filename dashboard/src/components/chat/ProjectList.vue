<template>
  <section class="sidebar-section project-list-shell">
    <div class="sidebar-section-header">
      <span>{{ tm("project.title") }}</span>
      <v-btn
        icon
        size="x-small"
        variant="text"
        class="section-action-btn"
        :title="tm('project.create')"
        @click="$emit('createProject')"
      >
        <Plus :size="18" />
      </v-btn>
    </div>

    <div class="project-list-wrap">
      <div v-for="project in projects" :key="project.project_id">
        <div
          class="project-row project-item"
          :class="{
            active: selectedProjectId === project.project_id,
            'drag-over': dragOverProjectId === project.project_id,
          }"
          role="button"
          tabindex="0"
          @click="handleProjectClick(project)"
          @keydown.enter="handleProjectClick(project)"
          @keydown.space.prevent="handleProjectClick(project)"
          @dragover.prevent="emit('dragOverProject', project.project_id)"
          @dragleave="onProjectRowDragLeave(project.project_id, $event)"
          @drop.prevent="emit('dropOnProject', project.project_id)"
        >
          <span class="project-emoji">{{ project.emoji || "📁" }}</span>
          <span class="project-title-wrap">
            <span class="project-title">{{ project.title }}</span>
            <ChevronDown
              v-if="isProjectExpanded(project.project_id)"
              :size="16"
              class="project-chevron"
            />
            <ChevronRight v-else :size="16" class="project-chevron" />
          </span>
          <span class="project-actions" @click.stop>
            <v-btn
              icon
              size="x-small"
              variant="text"
              class="project-action-btn"
              :title="tm('project.edit')"
              @click="$emit('editProject', project)"
            >
              <Pencil :size="15" />
            </v-btn>
            <v-btn
              icon
              size="x-small"
              variant="text"
              class="project-action-btn"
              :title="tm('actions.deleteChat')"
              @click="handleDeleteProject(project)"
            >
              <Trash2 :size="15" />
            </v-btn>
          </span>
        </div>

        <Transition name="project-session-fade">
          <div
            v-if="isProjectExpanded(project.project_id)"
            class="project-session-list"
          >
            <div
              v-if="loadingProjectIds.includes(project.project_id)"
              class="project-session-empty"
            >
              {{ tm("project.loadingSessions") }}
            </div>
            <template v-else-if="projectSessionList(project.project_id).length">
              <div
                v-for="session in projectSessionList(project.project_id)"
                :key="session.session_id"
                class="project-session-row"
                :class="{
                  active: activeSessionId === session.session_id,
                  selection: selectionMode,
                  checked:
                    selectionMode &&
                    checkedSessionIds.has(session.session_id),
                  'needs-choice': choiceAttention.hasAttention(
                    session.session_id,
                  ),
                  'has-finished-run':
                    !choiceAttention.hasAttention(session.session_id) &&
                    finishedAttention.hasFinished(session.session_id),
                  'has-branch-meta':
                    !selectionMode &&
                    (Boolean(session.branches?.length) ||
                      Boolean(session.branch_source)),
                  'drag-over': dragOverSessionId === session.session_id,
                  'drag-before':
                    dragOverSessionId === session.session_id &&
                    dragInsertBefore,
                  'drag-after':
                    dragOverSessionId === session.session_id &&
                    !dragInsertBefore,
                }"
                role="button"
                tabindex="0"
                :draggable="!selectionMode"
                @click="handleSessionRowClick(session.session_id)"
                @keydown.enter="handleSessionRowClick(session.session_id)"
                @keydown.space.prevent="
                  handleSessionRowClick(session.session_id)
                "
                @dragstart="
                  onProjectSessionDragStart(
                    project.project_id,
                    session.session_id,
                    $event,
                  )
                "
                @dragend="emit('dragSessionEnd')"
                @dragover.prevent="
                  onSessionRowDragOver(
                    project.project_id,
                    session.session_id,
                    $event,
                  )
                "
                @dragleave="
                  onSessionRowDragLeave(project.project_id, $event)
                "
                @drop.prevent="
                  onSessionRowDrop(project.project_id, session.session_id, $event)
                "
              >
                <v-checkbox-btn
                  v-if="selectionMode"
                  :model-value="checkedSessionIds.has(session.session_id)"
                  density="compact"
                  class="project-session-select-checkbox"
                  @click.stop
                  @update:model-value="
                    emit('toggleSessionChecked', session.session_id)
                  "
                />
                <span
                  v-if="choiceAttention.hasAttention(session.session_id)"
                  class="project-session-choice-dot"
                  aria-hidden="true"
                />
                <span
                  v-else-if="
                    finishedAttention.hasFinished(session.session_id)
                  "
                  class="project-session-finished-dot"
                  aria-hidden="true"
                />
                <span class="project-session-title">
                  {{ sessionTitle(session) }}
                </span>
                <div
                  v-if="
                    !selectionMode &&
                    (session.branches?.length || session.branch_source)
                  "
                  class="project-session-branch-meta"
                  @click.stop
                >
                  <StyledMenu
                    v-if="session.branches?.length"
                    location="bottom start"
                    transition="none"
                    no-border
                  >
                    <template #activator="{ props: branchMenuProps }">
                      <button
                        v-bind="branchMenuProps"
                        class="project-session-branch-badge"
                        type="button"
                        :title="tm('branch.branches')"
                      >
                        <GitBranch :size="12" />
                        <span>{{ session.branches.length }}</span>
                      </button>
                    </template>
                    <v-list-item
                      v-for="branch in session.branches"
                      :key="branch.session_id"
                      class="styled-menu-item"
                      rounded="md"
                      @click="$emit('selectSession', branch.session_id)"
                    >
                      <template #prepend>
                        <GitBranch :size="14" />
                      </template>
                      <v-list-item-title>
                        {{
                          branch.display_name?.trim() ||
                          tm("conversation.newConversation")
                        }}
                      </v-list-item-title>
                    </v-list-item>
                  </StyledMenu>
                  <v-btn
                    v-if="session.branch_source"
                    icon
                    size="x-small"
                    variant="text"
                    class="project-session-action-btn"
                    :title="tm('branch.jumpToSource')"
                    @click="
                      $emit('selectSession', session.branch_source!.session_id)
                    "
                  >
                    <CornerUpLeft :size="14" />
                  </v-btn>
                </div>
                <span
                  v-if="!selectionMode"
                  class="project-session-actions"
                  @click.stop
                >
                  <v-btn
                    icon
                    size="x-small"
                    variant="text"
                    class="project-action-btn"
                    :title="tm('conversation.editDisplayName')"
                    @click="
                      $emit(
                        'editSessionTitle',
                        session.session_id,
                        session.display_name || '',
                      )
                    "
                  >
                    <Pencil :size="15" />
                  </v-btn>
                  <v-btn
                    icon
                    size="x-small"
                    variant="text"
                    class="project-action-btn"
                    :title="tm('conversation.archive')"
                    @click="
                      $emit('archiveSession', session.session_id, project.project_id)
                    "
                  >
                    <Archive :size="15" />
                  </v-btn>
                  <v-btn
                    icon
                    size="x-small"
                    variant="text"
                    class="project-action-btn"
                    :title="tm('actions.deleteChat')"
                    @click="handleDeleteSession(project.project_id, session)"
                  >
                    <Trash2 :size="15" />
                  </v-btn>
                </span>
                <v-progress-circular
                  v-if="sessionRunning(session.session_id)"
                  class="project-session-progress"
                  indeterminate
                  size="14"
                  width="2"
                />
              </div>
            </template>
            <div v-else class="project-session-empty">
              {{ tm("project.noSessions") }}
            </div>
          </div>
        </Transition>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import {
  Archive,
  ChevronDown,
  ChevronRight,
  CornerUpLeft,
  GitBranch,
  Pencil,
  Plus,
  Trash2,
} from "@lucide/vue";
import { useModuleI18n } from "@/i18n/composables";
import { askForConfirmation, useConfirmDialog } from "@/utils/confirmDialog";
import StyledMenu from "@/components/shared/StyledMenu.vue";
import { useInteractiveChoiceAttentionStore } from "@/stores/interactiveChoiceAttention";
import { useRunFinishedAttentionStore } from "@/stores/runFinishedAttention";

export interface Project {
  project_id: string;
  title: string;
  emoji?: string;
  description?: string;
  workspace_type?: "session" | "project" | "custom";
  workspace_path?: string | null;
  resolved_workspace_path?: string | null;
  /** Legacy silent-load switch; always on since 2026-09-09 (not read). */
  spcode_auto_load?: boolean;
  spcode_no_agentsmd?: boolean;
  spcode_no_codegraph?: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProjectSession {
  session_id: string;
  display_name?: string | null;
  updated_at: string;
  /** 2026-08-13: branch relations mirroring the flat Session type, so
   * project rows can render the branch badge / jump-to-source. */
  branch_source?: { session_id: string; message_id: number } | null;
  branches?: Array<{ session_id: string; display_name: string | null }>;
}

interface Props {
  projects: Project[];
  projectSessions: Record<string, ProjectSession[]>;
  loadingProjectIds: string[];
  selectedProjectId?: string | null;
  activeSessionId?: string | null;
  isSessionRunning?: (sessionId: string) => boolean;
  /** 2026-08-09 sidebar batch delete (elecvoid243): when true, session
   * rows render checkboxes and toggle selection instead of opening. */
  selectionMode?: boolean;
  checkedSessionIds?: Set<string>;
  /** 2026-08-13 session drag: id of the session currently being dragged
   * (used to keep drop affordances visible), or null. */
  draggingSessionId?: string | null;
  /** 2026-08-13 session drag: id of the project row currently hovered
   * by a session drag (highlight), or null. */
  dragOverProjectId?: string | null;
  /** 2026-08-14 session drag: id of the project session row currently
   * hovered by a session drag (drop-position indicator), or null. */
  dragOverSessionId?: string | null;
  /** 2026-08-14 session drag: when true the pending drop inserts before
   * the hovered session row, otherwise after it. */
  dragInsertBefore?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  selectedProjectId: null,
  activeSessionId: null,
  selectionMode: false,
  checkedSessionIds: () => new Set<string>(),
  draggingSessionId: null,
  dragOverProjectId: null,
  dragOverSessionId: null,
  dragInsertBefore: true,
});

const emit = defineEmits<{
  selectProject: [projectId: string];
  createProject: [];
  editProject: [project: Project];
  deleteProject: [projectId: string];
  toggleProject: [projectId: string, expanded: boolean];
  selectSession: [sessionId: string];
  editSessionTitle: [sessionId: string, title: string];
  deleteSession: [sessionId: string, projectId: string];
  toggleSessionChecked: [sessionId: string];
  /** 2026-08-13 session archive: a project session row asked to archive. */
  archiveSession: [sessionId: string, projectId: string];
  /** 2026-08-13 session drag events (bubbled to Chat.vue, the owner of
   * sessions/projects and the move logic). */
  dragSessionStart: [sessionId: string, projectId: string];
  dragSessionEnd: [];
  dragOverProject: [projectId: string];
  dragLeaveProject: [projectId: string];
  dropOnProject: [projectId: string];
  /** 2026-08-14 session drag: dropping onto a project session row inserts
   * the dragged session into that project at the hovered position. */
  dragOverSession: [projectId: string, sessionId: string, before: boolean];
  dragLeaveSession: [projectId: string];
  dropOnSession: [projectId: string, sessionId: string, before: boolean];
}>();

const { tm } = useModuleI18n("features/chat");
const confirmDialog = useConfirmDialog();

// 2026-09-01 (elecvoid243): read the ask_user_choice attention store
// directly instead of prop-drilling it — the flat session list excludes
// project sessions, so this component is their only sidebar renderer and
// must mirror Chat.vue's `needs-choice` highlight on its own.
const choiceAttention = useInteractiveChoiceAttentionStore();
// Same story for the calmer run-finished marker: project sessions would
// never show it unless this component consumes the store itself.
const finishedAttention = useRunFinishedAttentionStore();

const expandedProjectIds = ref<Set<string>>(readExpandedProjectIds());

watch(
  () => props.selectedProjectId,
  (projectId) => {
    if (projectId) {
      setProjectExpanded(projectId, true);
    }
  },
);

watch(
  () => props.projects.map((project) => project.project_id).join(","),
  () => {
    const validProjectIds = new Set(
      props.projects.map((project) => project.project_id),
    );
    expandedProjectIds.value.forEach((projectId) => {
      if (validProjectIds.has(projectId)) {
        emit("toggleProject", projectId, true);
      }
    });
  },
  { immediate: true },
);

function readExpandedProjectIds() {
  try {
    const raw = localStorage.getItem("chat.projectExpandedIds");
    const parsed = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter(Boolean) : []);
  } catch {
    return new Set<string>();
  }
}

function persistExpandedProjectIds() {
  localStorage.setItem(
    "chat.projectExpandedIds",
    JSON.stringify([...expandedProjectIds.value]),
  );
}

function isProjectExpanded(projectId: string) {
  return expandedProjectIds.value.has(projectId);
}

function setProjectExpanded(projectId: string, expanded: boolean) {
  const next = new Set(expandedProjectIds.value);
  if (expanded) {
    next.add(projectId);
  } else {
    next.delete(projectId);
  }
  expandedProjectIds.value = next;
  persistExpandedProjectIds();
  emit("toggleProject", projectId, expanded);
}

function handleProjectClick(project: Project) {
  const nextExpanded = !isProjectExpanded(project.project_id);
  setProjectExpanded(project.project_id, nextExpanded);
  emit("selectProject", project.project_id);
}

function projectSessionList(projectId: string) {
  return props.projectSessions[projectId] || [];
}

function sessionRunning(sessionId: string) {
  return props.isSessionRunning?.(sessionId) || false;
}

function sessionTitle(session: ProjectSession) {
  return session.display_name?.trim() || tm("conversation.newConversation");
}

/** Row click dispatcher: in selection mode a click toggles the checkbox
 * instead of opening the session. */
function handleSessionRowClick(sessionId: string) {
  if (props.selectionMode) {
    emit("toggleSessionChecked", sessionId);
    return;
  }
  emit("selectSession", sessionId);
}

async function handleDeleteProject(project: Project) {
  const message = tm("project.confirmDelete", { title: project.title });
  if (await askForConfirmation(message, confirmDialog)) {
    emit("deleteProject", project.project_id);
  }
}

async function handleDeleteSession(projectId: string, session: ProjectSession) {
  const message = tm("conversation.confirmDelete", {
    name: sessionTitle(session),
  });
  if (await askForConfirmation(message, confirmDialog)) {
    emit("deleteSession", session.session_id, projectId);
  }
}

/** 2026-08-13 session drag: only clear the row highlight once the pointer
 * actually left the row — dragleave fires when moving between child
 * elements, which would flicker the highlight. */
function onProjectRowDragLeave(projectId: string, event: DragEvent) {
  const related = event.relatedTarget as Node | null;
  const el = event.currentTarget as HTMLElement | null;
  if (el && related && el.contains(related)) return;
  emit("dragLeaveProject", projectId);
}

/** 2026-08-13 session drag: set the dataTransfer here (Firefox refuses to
 * start a drag without setData) and bubble the session id + owning project
 * to Chat.vue so it can tell an in-project reorder from a flat-session move. */
function onProjectSessionDragStart(
  projectId: string,
  sessionId: string,
  event: DragEvent,
) {
  if (event.dataTransfer) {
    event.dataTransfer.setData("text/plain", sessionId);
    event.dataTransfer.effectAllowed = "move";
  }
  emit("dragSessionStart", sessionId, projectId);
}

/** 2026-08-14 session drag: whether the pointer is over the top half of the
 * hovered row (insert before it) rather than the bottom half (after). */
function insertBeforeRow(event: MouseEvent | DragEvent) {
  const el = event.currentTarget as HTMLElement | null;
  if (!el) return true;
  const rect = el.getBoundingClientRect();
  return event.clientY < rect.top + rect.height / 2;
}

/** 2026-08-14 session drag: project session rows are now drop targets too,
 * so sessions can join a project at a specific position without having to
 * reach the (possibly scrolled-out) project row. */
function onSessionRowDragOver(
  projectId: string,
  sessionId: string,
  event: DragEvent,
) {
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  emit("dragOverSession", projectId, sessionId, insertBeforeRow(event));
}

function onSessionRowDragLeave(projectId: string, event: DragEvent) {
  const related = event.relatedTarget as Node | null;
  const el = event.currentTarget as HTMLElement | null;
  if (el && related && el.contains(related)) return;
  emit("dragLeaveSession", projectId);
}

function onSessionRowDrop(
  projectId: string,
  sessionId: string,
  event: DragEvent,
) {
  emit("dropOnSession", projectId, sessionId, insertBeforeRow(event));
}

</script>

<style scoped>
.project-list-shell {
  margin-top: 2px;
}

.sidebar-section-header {
  min-height: 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px 4px;
  color: var(--chat-section-label);
  font-size: 12px;
  font-weight: 500;
}

.section-action-btn,
.project-action-btn {
  color: var(--chat-muted);
}

.section-action-btn :deep(svg),
.project-action-btn :deep(svg),
.project-chevron {
  flex: 0 0 auto;
  stroke-width: 2;
}

.section-action-btn {
  width: 36px;
  height: 36px;
  min-width: 36px;
}

.section-action-btn:hover,
.project-action-btn:hover {
  color: rgb(var(--v-theme-on-surface));
}

.project-list-wrap,
.project-session-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.project-row,
.project-session-row {
  width: 100%;
  min-height: 30px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 56px 4px 10px;
  position: relative;
  box-sizing: border-box;
  cursor: pointer;
  text-align: left;
}

/* 2026-08-13: project session rows gained an archive action (3 hover
   buttons), so they need more right padding than project rows. */
.project-session-row {
  padding-right: 88px;
}

.project-row:hover,
.project-row.active,
.project-session-row:hover,
.project-session-row.active {
  background: var(--chat-session-active-bg);
}

/* 2026-08-13 session drag: project row as a drop target for sessions. */
.project-row.drag-over {
  background: rgba(var(--v-theme-primary), 0.12);
  outline: 1.5px dashed rgba(var(--v-theme-primary), 0.6);
  outline-offset: -1px;
}

/* 2026-08-14 session drag: project session rows are drop targets too; the
   accent line marks the exact insertion point (before / after the row). */
.project-session-row.drag-over {
  background: rgba(var(--v-theme-primary), 0.12);
}

.project-session-row.drag-before::before,
.project-session-row.drag-after::after {
  content: "";
  position: absolute;
  left: 8px;
  right: 8px;
  height: 2px;
  border-radius: 1px;
  background: rgb(var(--v-theme-primary));
  pointer-events: none;
}

.project-session-row.drag-before::before {
  top: -1px;
}

.project-session-row.drag-after::after {
  bottom: -1px;
}

.project-emoji {
  width: 18px;
  flex: 0 0 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
}

.project-title-wrap {
  min-width: 0;
  flex: 1;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.project-title,
.project-session-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 500;
}

.project-title {
  flex: 0 1 auto;
}

.project-session-title {
  flex: 1;
}

.project-chevron {
  flex: 0 0 auto;
  color: var(--chat-muted);
}

.project-actions,
.project-session-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  opacity: 0;
  pointer-events: none;
  position: absolute;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  visibility: hidden;
}

.project-row:hover .project-actions,
.project-row:focus-within .project-actions,
.project-session-row:hover .project-session-actions,
.project-session-row:focus-within .project-session-actions {
  opacity: 1;
  pointer-events: auto;
  visibility: visible;
}

.project-session-list {
  padding: 2px 0 4px 26px;
}

/* 2026-08-13: branch badge / jump-to-source on project session rows,
   mirroring the flat session list styles in Chat.vue. */
.project-session-row.has-branch-meta {
  padding-right: 132px;
}

.project-session-branch-meta {
  position: absolute;
  right: 92px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  gap: 2px;
}

.project-session-branch-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 16px;
  color: var(--chat-muted);
  cursor: pointer;
  white-space: nowrap;
}

.project-session-branch-badge:hover {
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgb(var(--v-theme-on-surface));
}

/* 2026-08-09 sidebar batch delete (elecvoid243): selection mode styles. */
.project-session-row.selection {
  padding-right: 10px;
}

.project-session-row.checked {
  background: var(--chat-session-active-bg);
}

/* 2026-09-01 (elecvoid243): ask_user_choice highlight for project session
   rows, mirroring the flat session list styles in Chat.vue. Declared after
   hover/active/checked so the amber tint wins when they coincide. */
.project-session-row.needs-choice {
  background: rgba(245, 158, 11, 0.14);
}

.project-session-choice-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f59e0b;
  flex-shrink: 0;
  animation: project-session-choice-pulse 1.2s ease-in-out infinite;
}

@keyframes project-session-choice-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

/* 2026-09-01 (elecvoid243): run-finished marker for project session rows,
   mirroring the flat session list in Chat.vue — steady green dot + faint
   tint, distinct from the pulsing amber pending-choice highlight. */
.project-session-row.has-finished-run {
  background: rgba(16, 185, 129, 0.1);
}

.project-session-finished-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #10b981;
  flex-shrink: 0;
}

.project-session-select-checkbox {
  flex: 0 0 auto;
}

.project-session-select-checkbox :deep(.v-selection-control) {
  min-height: 24px;
}

/* 2026-08-09 (elecvoid243): smaller per-row checkboxes. */
.project-session-select-checkbox :deep(.v-selection-control__input) {
  width: 16px;
  height: 16px;
}

.project-session-select-checkbox :deep(.v-icon) {
  font-size: 16px;
}

.project-session-fade-enter-active {
  transition: opacity 0.1s ease-out;
}

.project-session-fade-leave-active {
  transition: opacity 0.08s ease-in;
}

.project-session-fade-enter-from,
.project-session-fade-leave-to {
  opacity: 0;
}

.project-session-progress {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  flex-shrink: 0;
  transition:
    opacity 0.14s ease,
    visibility 0.14s ease;
}

.project-session-row:hover .project-session-progress,
.project-session-row:focus-within .project-session-progress {
  opacity: 0;
  visibility: hidden;
}

.project-session-empty {
  padding: 5px 10px;
  color: var(--chat-muted);
  font-size: 13px;
}
</style>
