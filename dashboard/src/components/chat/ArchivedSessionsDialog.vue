<template>
  <v-dialog
    :model-value="modelValue"
    max-width="680"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <v-card class="archived-dialog">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6 d-flex align-center">
        <span class="flex-grow-1">{{
          tm("conversation.archivedDialogTitle")
        }}</span>
        <v-btn
          size="small"
          variant="tonal"
          density="compact"
          :class="{ active: selectionMode }"
          @click="toggleSelectionMode"
        >
          {{ tm("batch.manage") }}
        </v-btn>
      </v-card-title>
      <v-card-text>
        <v-text-field
          v-model="searchInput"
          :label="tm('conversation.archivedSearchPlaceholder')"
          prepend-inner-icon="mdi-magnify"
          variant="outlined"
          density="compact"
          hide-details
          clearable
          class="mb-3"
          @update:model-value="onSearchInput"
        />

        <!-- 2026-08-13 batch actions inside the archived dialog -->
        <div v-if="selectionMode" class="archived-batch-bar">
          <v-checkbox-btn
            :model-value="allChecked"
            :indeterminate="someChecked"
            density="compact"
            class="batch-select-all"
            :title="
              allChecked ? tm('batch.deselectAll') : tm('batch.selectAll')
            "
            @update:model-value="toggleSelectAll"
          />
          <span class="archived-batch-count">
            {{ tm("batch.selected", { count: checkedIds.size }) }}
          </span>
          <v-btn
            size="x-small"
            variant="text"
            class="archived-batch-btn"
            :disabled="checkedIds.size === 0"
            :loading="batchLoading"
            @click="batchRestore"
          >
            {{ tm("conversation.batchUnarchive") }}
          </v-btn>
          <v-btn
            size="x-small"
            variant="text"
            color="error"
            class="archived-batch-btn"
            :disabled="checkedIds.size === 0"
            :loading="batchLoading"
            @click="batchDelete"
          >
            {{ tm("batch.delete") }}
          </v-btn>
          <v-btn
            size="x-small"
            variant="text"
            class="archived-batch-btn"
            @click="toggleSelectionMode"
          >
            {{ tm("batch.exit") }}
          </v-btn>
        </div>

        <div v-if="loading" class="archived-dialog-status">
          <v-progress-circular indeterminate size="18" width="2" />
        </div>

        <div v-else-if="items.length" class="archived-dialog-list">
          <div
            v-for="session in items"
            :key="session.session_id"
            class="archived-card"
            :class="{ expanded: expandedId === session.session_id }"
          >
            <div class="archived-card-head">
              <v-checkbox-btn
                v-if="selectionMode"
                :model-value="checkedIds.has(session.session_id)"
                density="compact"
                class="archived-card-checkbox"
                @click.stop
                @update:model-value="toggleChecked(session.session_id)"
              />
              <button
                type="button"
                class="archived-card-toggle"
                @click="toggleExpanded(session)"
              >
                <ChevronRight
                  :size="16"
                  class="archived-card-chevron"
                  :class="{ open: expandedId === session.session_id }"
                />
                <span class="archived-card-title">
                  {{
                    session.display_name?.trim() ||
                    tm("conversation.newConversation")
                  }}
                </span>
                <span v-if="session.project_title" class="archived-project-tag">
                  {{ session.project_title }}
                </span>
              </button>
              <div class="archived-card-actions">
                <v-btn
                  icon
                  size="small"
                  variant="text"
                  class="archived-dialog-btn"
                  :title="tm('conversation.viewFull')"
                  @click="$emit('open', session)"
                >
                  <Eye :size="16" />
                </v-btn>
                <v-btn
                  icon
                  size="small"
                  variant="text"
                  class="archived-dialog-btn"
                  :title="tm('conversation.unarchive')"
                  @click="restoreSession(session)"
                >
                  <ArchiveRestore :size="16" />
                </v-btn>
                <v-btn
                  icon
                  size="small"
                  variant="text"
                  class="archived-dialog-btn"
                  :title="tm('actions.deleteChat')"
                  @click="deleteSession(session)"
                >
                  <Trash2 :size="16" />
                </v-btn>
              </div>
            </div>
            <Transition name="archived-preview">
              <div
                v-if="expandedId === session.session_id"
                class="archived-preview"
              >
                <div
                  v-if="previewLoading[session.session_id]"
                  class="archived-preview-status"
                >
                  <v-progress-circular indeterminate size="16" width="2" />
                </div>
                <p
                  v-else-if="previews[session.session_id]"
                  class="archived-preview-text"
                >
                  {{ previews[session.session_id] }}
                </p>
                <p v-else class="archived-preview-empty">
                  {{ tm("conversation.archivedNoPreview") }}
                </p>
              </div>
            </Transition>
          </div>
        </div>

        <div v-else class="archived-empty">
          {{ tm("conversation.archivedEmpty") }}
        </div>
      </v-card-text>

      <v-card-actions>
        <span class="archived-dialog-pagination">
          {{
            tm("conversation.archivedPagination", {
              page: pagination.page,
              totalPages: pagination.total_pages,
              total: pagination.total,
            })
          }}
        </span>
        <v-select
          v-model="pageSize"
          :items="pageSizeOptions"
          density="compact"
          variant="outlined"
          hide-details
          width="88"
          class="archived-dialog-page-size"
        />
        <v-spacer />
        <v-btn
          icon
          variant="text"
          :disabled="pagination.page <= 1"
          :title="tm('search.prevPage')"
          @click="goPage(pagination.page - 1)"
        >
          <ChevronLeft :size="20" />
        </v-btn>
        <v-btn
          icon
          variant="text"
          :disabled="pagination.page >= pagination.total_pages"
          :title="tm('search.nextPage')"
          @click="goPage(pagination.page + 1)"
        >
          <ChevronRight :size="20" />
        </v-btn>
        <v-btn variant="text" @click="$emit('update:modelValue', false)">
          {{ t("core.common.close") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import {
  accountSessionRevision,
  assertCurrentAccount,
} from "@/utils/accountSession";
import { computed, reactive, ref, watch } from "vue";
import {
  ArchiveRestore,
  ChevronLeft,
  ChevronRight,
  Eye,
  Trash2,
} from "@lucide/vue";
import { useI18n, useModuleI18n } from "@/i18n/composables";
import { useToast } from "@/utils/toast";
import { askForConfirmation, useConfirmDialog } from "@/utils/confirmDialog";
import { chatApi } from "@/api/v1";
import { useSessions, type ArchivedSession } from "@/composables/useSessions";

const props = defineProps<{
  modelValue: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  /** Session(s) restored; parent refreshes sidebar/project lists. */
  restore: [session?: ArchivedSession];
  /** Session(s) deleted; parent refreshes sidebar/project lists. */
  delete: [session?: ArchivedSession];
  /** View the full session read-only in the main chat area. */
  open: [session: ArchivedSession];
}>();

const { t } = useI18n();
const { tm } = useModuleI18n("features/chat");
const toast = useToast();
const confirmDialog = useConfirmDialog();
const { getArchivedSessions, setSessionArchived } = useSessions();

const accountRevision = accountSessionRevision.value;
const items = ref<ArchivedSession[]>([]);
const pagination = ref({
  page: 1,
  page_size: 20,
  total: 0,
  total_pages: 1,
});
const loading = ref(false);
let loadRevision = 0;
const searchInput = ref("");
const searchQuery = ref("");
const pageSizeOptions = [20, 30, 50];
const pageSize = ref(20);
let searchDebounce: ReturnType<typeof setTimeout> | null = null;

// Selection mode for batch restore / batch delete.
const selectionMode = ref(false);
const checkedIds = ref<Set<string>>(new Set());
const batchLoading = ref(false);

// Expanded cards: lazy preview of the first user message.
const expandedId = ref<string | null>(null);
const previewLoading = reactive<Record<string, boolean>>({});
const previews = reactive<Record<string, string>>({});

watch(
  accountSessionRevision,
  () => {
    if (searchDebounce) clearTimeout(searchDebounce);
    items.value = [];
    checkedIds.value = new Set();
    selectionMode.value = false;
    searchInput.value = "";
    searchQuery.value = "";
    expandedId.value = null;
    for (const key of Object.keys(previews)) delete previews[key];
  },
  { flush: "sync" },
);

const allChecked = computed(
  () =>
    items.value.length > 0 &&
    items.value.every((session) => checkedIds.value.has(session.session_id)),
);
const someChecked = computed(
  () => checkedIds.value.size > 0 && !allChecked.value,
);

async function load() {
  const revision = ++loadRevision;
  loading.value = true;
  try {
    const payload = await getArchivedSessions({
      page: pagination.value.page,
      page_size: pageSize.value,
      search: searchQuery.value,
    });
    assertCurrentAccount(accountRevision);
    if (revision !== loadRevision) return;
    items.value = payload.items;
    pagination.value = {
      ...pagination.value,
      ...payload.pagination,
      page_size: pageSize.value,
    };
  } catch (error) {
    if (accountRevision === accountSessionRevision.value)
      toast.error(tm("batch.requestFailed"));
  } finally {
    if (revision === loadRevision) loading.value = false;
  }
}

function onSearchInput() {
  if (searchDebounce) clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    searchQuery.value = searchInput.value.trim();
    pagination.value.page = 1;
    void load();
  }, 250);
}

function goPage(page: number) {
  if (page < 1 || page > pagination.value.total_pages) return;
  pagination.value.page = page;
  void load();
}

function toggleSelectionMode() {
  selectionMode.value = !selectionMode.value;
  if (!selectionMode.value) checkedIds.value = new Set();
}

function toggleChecked(sessionId: string) {
  const next = new Set(checkedIds.value);
  if (next.has(sessionId)) {
    next.delete(sessionId);
  } else {
    next.add(sessionId);
  }
  checkedIds.value = next;
}

function toggleSelectAll() {
  checkedIds.value = allChecked.value
    ? new Set()
    : new Set(items.value.map((session) => session.session_id));
}

async function restoreSession(session: ArchivedSession) {
  const ok = await setSessionArchived(session.session_id, false);
  if (!ok) {
    toast.error(tm("conversation.unarchiveFailed"));
    return;
  }
  toast.success(tm("conversation.unarchiveToast"));
  emit("restore", session);
  void load();
}

async function deleteSession(session: ArchivedSession) {
  const title =
    session.display_name?.trim() || tm("conversation.newConversation");
  const message = tm("conversation.confirmDelete", { name: title });
  if (!(await askForConfirmation(message, confirmDialog))) return;
  try {
    // Direct API call: useSessions.deleteSession would reset the active
    // session id, which is wrong when removing an archived session.
    const response = await chatApi.deleteSession(session.session_id);
    assertCurrentAccount(accountRevision);
    if (response.data?.status !== "ok")
      throw new Error(response.data?.message || "Failed to delete session");
  } catch (err) {
    console.error("Failed to delete archived session:", err);
    toast.error(tm("batch.requestFailed"));
    return;
  }
  emit("delete", session);
  void load();
}

async function batchRestore() {
  const ids = [...checkedIds.value];
  if (!ids.length) return;
  const message = tm("conversation.batchUnarchiveConfirm", {
    count: ids.length,
  });
  if (!(await askForConfirmation(message, confirmDialog))) return;

  batchLoading.value = true;
  try {
    const response = await chatApi.batchUnarchiveSessions({ session_ids: ids });
    if (response.data?.status !== "ok") {
      throw new Error(response.data?.message || "Failed to batch restore");
    }
    const data = response.data?.data || {};
    const failedCount = data.failed_count || 0;
    if (failedCount > 0) {
      toast.error(
        tm("batch.partialFailure", { failed: failedCount, total: ids.length }),
      );
    } else {
      toast.success(
        tm("conversation.batchUnarchiveSuccess", {
          count: data.unarchived_count,
        }),
      );
    }
    // Parent refreshes sidebar/project lists for the restored set.
    emit("restore");
    toggleSelectionMode();
    void load();
  } catch (err) {
    console.error("Failed to batch restore sessions:", err);
    toast.error(tm("batch.requestFailed"));
  } finally {
    batchLoading.value = false;
  }
}

async function batchDelete() {
  const ids = [...checkedIds.value];
  if (!ids.length) return;
  const message = tm("batch.confirmDelete", { count: ids.length });
  if (!(await askForConfirmation(message, confirmDialog))) return;

  batchLoading.value = true;
  try {
    const response = await chatApi.batchDeleteSessions({ session_ids: ids });
    if (response.data?.status !== "ok") {
      throw new Error(response.data?.message || "Failed to batch delete");
    }
    const data = response.data?.data || {};
    const failedCount = data.failed_count || 0;
    if (failedCount > 0) {
      toast.error(
        tm("batch.partialFailure", { failed: failedCount, total: ids.length }),
      );
    } else {
      toast.success(tm("batch.deleteSuccess", { count: data.deleted_count }));
    }
    emit("delete");
    toggleSelectionMode();
    void load();
  } catch (err) {
    console.error("Failed to batch delete sessions:", err);
    toast.error(tm("batch.requestFailed"));
  } finally {
    batchLoading.value = false;
  }
}

async function toggleExpanded(session: ArchivedSession) {
  if (expandedId.value === session.session_id) {
    expandedId.value = null;
    return;
  }
  expandedId.value = session.session_id;
  if (previews[session.session_id] || previewLoading[session.session_id])
    return;
  previewLoading[session.session_id] = true;
  try {
    const response = await chatApi.getSession(session.session_id);
    assertCurrentAccount(accountRevision);
    if (response.data?.status === "error")
      throw new Error(response.data?.message || "History is unavailable");
    const history = response.data?.data?.history || [];
    previews[session.session_id] = truncatePreview(
      extractFirstUserText(history),
      200,
    );
  } catch (err) {
    console.error("Failed to load archived session preview:", err);
    if (accountRevision === accountSessionRevision.value)
      previews[session.session_id] = "";
  } finally {
    previewLoading[session.session_id] = false;
  }
}

function extractFirstUserText(history: unknown[]): string {
  for (const entry of history) {
    const content = (entry as Record<string, any>)?.content;
    if (!content || content.type !== "user") continue;
    const parts = content.message;
    if (typeof parts === "string") return parts;
    if (Array.isArray(parts)) {
      const texts = parts
        .filter((part) => part && typeof part.text === "string")
        .map((part) => part.text);
      if (texts.length) return texts.join(" ");
    }
  }
  return "";
}

function truncatePreview(text: string, limit: number): string {
  const cleaned = text.replace(/\s+/g, " ").trim();
  if (cleaned.length <= limit) return cleaned;
  return `${cleaned.slice(0, limit)}…`;
}

watch(
  () => pageSize.value,
  () => {
    pagination.value.page = 1;
    void load();
  },
);

// Reset query + paging + selection every time the dialog opens.
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return;
    searchInput.value = "";
    searchQuery.value = "";
    pagination.value.page = 1;
    selectionMode.value = false;
    checkedIds.value = new Set();
    expandedId.value = null;
    void load();
  },
);
</script>

<style scoped>
/* 2026-08-28: the dialog previously grew with its content and could
   overflow the viewport (pushing the pagination row off-screen), while
   inside the list the cards' overflow:hidden zeroed their flex minimum
   size — so the clamped max-height SQUASHED the cards to fit instead of
   scrolling (card height visually depended on the page size). Now the
   card is a viewport-capped flex column, .v-card-text may shrink, and
   the list is the single scroll container; cards keep their natural
   height no matter how many items a page holds. */
.archived-dialog {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 96px);
}

.archived-dialog > :deep(.v-card-text) {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.archived-dialog-status {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}

.archived-dialog-list {
  flex: 1 1 auto;
  min-height: 0;
  /* Raised from the old 50vh — the dialog itself is taller now; the
     card-level cap below still keeps everything on short viewports. */
  max-height: 62vh;
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
}

.archived-card {
  /* Natural height, immune to flex squeeze — the list scrolls instead
     (its overflow-y above only kicks in because items no longer shrink
     to fit). */
  flex: 0 0 auto;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 10px;
  overflow: hidden;
}

.archived-card.expanded {
  border-color: rgba(var(--v-theme-primary), 0.35);
}

.archived-card-head {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px 6px 4px 10px;
}

.archived-card-checkbox {
  flex: 0 0 auto;
}

.archived-card-checkbox :deep(.v-selection-control) {
  min-height: 24px;
}

.archived-card-toggle {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 4px 0;
  text-align: left;
}

.archived-card-chevron {
  flex: 0 0 auto;
  transition: transform 0.12s ease;
}

.archived-card-chevron.open {
  transform: rotate(90deg);
}

.archived-card-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 500;
}

.archived-project-tag {
  flex: 0 0 auto;
  max-width: 110px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
  font-size: 11px;
  line-height: 18px;
}

.archived-card-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.archived-dialog-btn {
  color: var(--chat-muted);
}

.archived-dialog-btn:hover {
  color: rgb(var(--v-theme-on-surface));
}

.archived-preview {
  padding: 4px 12px 10px 40px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
  margin-top: 2px;
}

.archived-preview-status {
  display: flex;
  justify-content: center;
  padding: 8px 0;
}

.archived-preview-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: rgba(var(--v-theme-on-surface), 0.72);
  white-space: pre-wrap;
  word-break: break-word;
}

.archived-preview-empty {
  margin: 0;
  padding: 8px 0;
  font-size: 12px;
  color: var(--chat-muted);
}

.archived-preview-enter-active,
.archived-preview-leave-active {
  transition: opacity 0.12s ease;
}
.archived-preview-enter-from,
.archived-preview-leave-to {
  opacity: 0;
}

.archived-empty {
  padding: 24px 0;
  text-align: center;
  color: var(--chat-muted);
  font-size: 13px;
}

.archived-batch-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
  padding: 2px 8px;
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.05);
}

.archived-batch-count {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--chat-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.archived-batch-btn {
  font-size: 13px;
}

.archived-dialog-pagination {
  font-size: 12px;
  color: var(--chat-muted);
  margin-right: 8px;
}

.archived-dialog-page-size {
  flex: 0 0 auto;
}
</style>
