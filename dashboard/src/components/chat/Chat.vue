<template>
  <div
    v-if="props.active"
    class="chat-ui"
    :class="{ 'is-dark': isDark, 'sidebar-collapsed': isSidebarCollapsed }"
  >
    <!-- 2026-07-21 chatui sidebar resize (elecvoid243): replaced the
         Vuetify v-navigation-drawer with a custom <aside> so the user
         can drag the right edge to resize. Mobile still behaves as a
         fixed drawer with a backdrop and ESC-to-close. -->
    <div
      v-if="!lgAndUp && customizer.chatSidebarOpen"
      class="chat-sidebar-backdrop"
      @click="closeMobileSidebar"
    />
    <aside
      ref="chatSidebarRef"
      class="chat-sidebar"
      :class="{
        collapsed: isSidebarCollapsed,
        'is-resizing': isResizingSidebar,
        'is-mobile-drawer': !lgAndUp,
        'is-mobile-drawer-open': !lgAndUp && customizer.chatSidebarOpen,
        'is-mobile-drawer-closed': !lgAndUp && !customizer.chatSidebarOpen,
      }"
      :style="chatSidebarStyle"
      :aria-hidden="!lgAndUp && !customizer.chatSidebarOpen"
    >
      <!-- 2026-07-21 chatui sidebar resize (elecvoid243): drag handle
           for the right edge. Only visible on desktop in expanded
           mode — hidden in rail (56px) mode and on mobile where the
           sidebar is a fixed drawer. -->
      <div
        v-if="lgAndUp && !isSidebarCollapsed"
        class="chat-sidebar-resizer"
        :aria-label="tm('conversation.resizeSidebar')"
        role="separator"
        aria-orientation="vertical"
        @mousedown="startSidebarResize"
      />
      <div class="sidebar-top">
        <div
          class="chat-sidebar-brand"
          :class="{ collapsed: isSidebarCollapsed }"
        >
          <div
            v-if="!isSidebarCollapsed"
            class="chat-sidebar-brand-title Outfit"
          >
            <ChatUILogo class="chat-sidebar-brand-logo" />
            <span class="chat-sidebar-brand-copy">
              <span class="chat-sidebar-brand-name">AstrBot</span>
              <span class="chat-sidebar-brand-mode">ChatUI</span>
            </span>
          </div>
          <button
            v-if="isSidebarCollapsed"
            class="chat-sidebar-brand-toggle chat-sidebar-rail-btn"
            type="button"
            aria-label="Toggle sidebar"
            @click.stop="toggleChatSidebar"
          >
            <span class="chat-sidebar-rail-icon-stack">
              <ChatUILogo
                class="chat-sidebar-brand-logo chat-sidebar-brand-logo--collapsed"
              />
              <PanelLeft :size="20" class="sidebar-panel-toggle-icon" />
            </span>
          </button>
          <v-btn
            v-else
            class="chat-sidebar-brand-toggle"
            aria-label="Toggle sidebar"
            icon
            rounded="sm"
            variant="text"
            @click.stop="toggleChatSidebar"
          >
            <PanelLeft :size="20" class="sidebar-panel-toggle-icon" />
          </v-btn>
        </div>

        <button
          v-if="!isOrdinaryUser && isSidebarCollapsed"
          class="new-chat-btn sidebar-provider-btn icon-only chat-sidebar-rail-btn"
          :class="{ 'sidebar-workspace-btn--active': isProviderWorkspace }"
          type="button"
          :title="tm('actions.providerConfig')"
          @click="openProviderWorkspace"
        >
          <Box :size="18" class="sidebar-action-icon" />
        </button>
        <v-btn
          v-else-if="!isOrdinaryUser"
          class="new-chat-btn sidebar-provider-btn"
          :class="{ 'sidebar-workspace-btn--active': isProviderWorkspace }"
          variant="text"
          @click="openProviderWorkspace"
        >
          <Box :size="18" class="sidebar-action-icon mr-2" />
          <span>{{ tm("actions.providerConfig") }}</span>
        </v-btn>

        <button
          v-if="isSidebarCollapsed"
          class="new-chat-btn icon-only chat-sidebar-rail-btn"
          type="button"
          :title="tm('actions.newChat')"
          @click="startNewChat"
        >
          <SquarePen :size="18" class="sidebar-action-icon" />
        </button>
        <v-btn v-else class="new-chat-btn" variant="text" @click="startNewChat">
          <SquarePen :size="18" class="sidebar-action-icon mr-2" />
          <span>{{ tm("actions.newChat") }}</span>
        </v-btn>

        <!-- 2026-08-13 (elecvoid243): batch-manage and message-search moved
             to the top of the sidebar (above the project section). -->
        <div v-if="!isSidebarCollapsed" class="sidebar-top-actions">
          <button
            type="button"
            class="sidebar-top-action-btn"
            :class="{ active: selectionMode }"
            :title="tm('batch.manage')"
            @click="toggleSelectionMode"
          >
            <ListChecks :size="16" />
            <span>{{ tm("batch.manage") }}</span>
          </button>
          <button
            type="button"
            class="sidebar-top-action-btn"
            :title="tm('search.title')"
            @click="searchDialogOpen = true"
          >
            <Search :size="16" />
            <span>{{ tm("search.title") }}</span>
          </button>
          <button
            type="button"
            class="sidebar-top-action-btn"
            :title="tm('import.entry')"
            @click="importDialogOpen = true"
          >
            <Upload :size="16" />
            <span>{{ tm("import.entry") }}</span>
          </button>
        </div>
      </div>

      <div v-if="!isSidebarCollapsed" class="sidebar-content">
        <!-- 2026-08-09 sidebar batch delete (elecvoid243): batch action
             bar shown while selection mode is active. -->
        <div v-if="selectionMode" class="batch-select-bar">
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
          <span class="batch-select-count">
            {{ tm("batch.selected", { count: checkedSessionIds.size }) }}
          </span>
          <v-btn
            size="x-small"
            variant="text"
            class="batch-select-archive"
            :disabled="checkedSessionIds.size === 0"
            :loading="archivingCheckedSessions"
            @click="archiveCheckedSessions"
          >
            {{ tm("batch.archive") }}
          </v-btn>
          <v-btn
            size="x-small"
            variant="text"
            color="error"
            class="batch-select-delete"
            :disabled="checkedSessionIds.size === 0"
            :loading="deletingCheckedSessions"
            @click="deleteCheckedSessions"
          >
            {{ tm("batch.delete") }}
          </v-btn>
          <v-btn
            size="x-small"
            variant="text"
            class="batch-select-exit"
            @click="toggleSelectionMode"
          >
            {{ tm("batch.exit") }}
          </v-btn>
        </div>

        <ProjectList
          ref="projectListRef"
          :projects="projects"
          :project-sessions="projectSessionsById"
          :loading-project-ids="loadingProjectSessionIds"
          :selected-project-id="selectedProjectId"
          :active-session-id="currSessionId"
          :is-session-running="isSessionRunning"
          :selection-mode="selectionMode"
          :checked-session-ids="checkedSessionIds"
          :dragging-session-id="draggingSessionId"
          :drag-over-project-id="dragOverProjectId"
          :drag-over-session-id="dragOverSessionId"
          :drag-insert-before="dragInsertBefore"
          @create-project="openCreateProjectDialog"
          @create-session="createProjectSession"
          @edit-project="openEditProjectDialog"
          @delete-project="handleDeleteProject"
          @toggle-project="handleProjectToggle"
          @select-project="selectProject"
          @select-session="selectProjectSession"
          @edit-session-title="editProjectSessionTitle"
          @export-session="exportSidebarSession"
          @delete-session="deleteProjectSession"
          @toggle-session-checked="toggleSessionChecked"
          @archive-session="archiveProjectSession"
          @toggle-session-star="toggleSessionStarred"
          @drag-session-start="onSessionDragStart"
          @drag-session-end="onSessionDragEnd"
          @drag-over-project="onProjectDragOver"
          @drag-leave-project="onProjectDragLeave"
          @drop-on-project="onProjectDrop"
          @drag-over-session="onSessionDragOver"
          @drag-leave-session="onSessionDragLeave"
          @drop-on-session="onSessionDrop"
        />

        <section
          class="sidebar-section session-list"
          :class="{
            'drop-unsorted': dragOverUnsorted && draggingSessionProjectId,
          }"
          @dragover.prevent="onUnsortedDragOver"
          @dragleave="onUnsortedDragLeave"
          @drop.prevent="onUnsortedDrop"
        >
          <div class="sidebar-section-header">
            <span>{{ tm("conversation.title") }}</span>
          </div>
          <Transition name="drag-hint">
            <div
              v-if="
                dragOverUnsorted &&
                draggingSessionId &&
                draggingSessionProjectId
              "
              class="session-list-drop-hint"
            >
              <v-icon size="14">mdi-tray-arrow-up</v-icon>
              <span>{{ tm("project.dropToRemoveHint") }}</span>
            </div>
          </Transition>
          <div
            v-for="session in sessions"
            :key="session.session_id"
            class="session-item"
            :class="{
              active:
                !isProviderWorkspace && currSessionId === session.session_id,
              selection: selectionMode,
              checked:
                selectionMode && checkedSessionIds.has(session.session_id),
              'needs-choice': choiceAttention.hasAttention(session.session_id),
              'has-finished-run':
                !choiceAttention.hasAttention(session.session_id) &&
                sessionHasUnreadMarker(session.session_id),
              'has-branch-meta':
                !selectionMode &&
                (Boolean(session.branches?.length) ||
                  Boolean(session.branch_source)),
            }"
            role="button"
            tabindex="0"
            :draggable="!selectionMode"
            @click="handleSidebarSessionClick(session.session_id)"
            @contextmenu.prevent="openSessionContextMenu(session, $event)"
            @dragstart="onSidebarSessionDragStart(session.session_id, $event)"
            @dragend="onSessionDragEnd"
            @keydown.enter="handleSidebarSessionClick(session.session_id)"
            @keydown.space.prevent="
              handleSidebarSessionClick(session.session_id)
            "
          >
            <v-checkbox-btn
              v-if="selectionMode"
              :model-value="checkedSessionIds.has(session.session_id)"
              density="compact"
              class="session-select-checkbox"
              @click.stop
              @update:model-value="toggleSessionChecked(session.session_id)"
            />
            <span
              v-if="choiceAttention.hasAttention(session.session_id)"
              class="session-choice-dot"
              aria-hidden="true"
            />
            <span
              v-else-if="sessionHasUnreadMarker(session.session_id)"
              class="session-finished-dot"
              aria-hidden="true"
            />
            <!-- 2026-09-18 (elecvoid243): persisted star marker, kept ahead
               of the title so starred conversations are quick to spot. -->
            <Star
              v-if="session.starred"
              :size="13"
              class="session-star-badge"
              :aria-label="tm('conversation.starred')"
            />
            <span class="session-title">{{ sessionTitle(session) }}</span>
            <div
              v-if="
                !selectionMode &&
                (session.branches?.length || session.branch_source)
              "
              class="session-branch-meta"
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
                    class="session-branch-badge"
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
                  @click="selectSession(branch.session_id)"
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
                class="session-action-btn"
                :title="tm('branch.jumpToSource')"
                @click="selectSession(session.branch_source.session_id)"
              >
                <CornerUpLeft :size="14" />
              </v-btn>
            </div>
            <!-- 2026-10-04 (elecvoid243): row actions keep only the two that
               earn their space — rename (frequent, inline) and the overflow
               menu. Star / export / archive / delete / mark-unread already
               exist in sessionContextMenu, so the overflow button routes to
               that same state instead of duplicating the item list. -->
            <div v-if="!selectionMode" class="session-actions" @click.stop>
              <v-btn
                icon
                size="x-small"
                variant="text"
                class="session-action-btn"
                :title="tm('conversation.editDisplayName')"
                @click="editSidebarSessionTitle(session)"
              >
                <Pencil :size="15" />
              </v-btn>
              <v-btn
                icon
                size="x-small"
                variant="text"
                class="session-action-btn"
                :title="tm('conversation.moreActions')"
                @click="openSessionContextMenu(session, $event)"
              >
                <MoreHorizontal :size="16" />
              </v-btn>
            </div>
            <v-progress-circular
              v-if="
                isSessionRunning(session.session_id) ||
                hasLiveSystemRecord(session.session_id)
              "
              class="session-progress"
              indeterminate
              size="16"
              width="2"
            />
          </div>

          <!-- 2026-09-14 (elecvoid243): right-click menu on a sidebar
             session row; opened with pointer coordinates from
             openSessionContextMenu(). -->
          <StyledMenu
            v-model="sessionContextMenu.show"
            :target="sessionContextMenu.target"
            location="end"
          >
            <v-list-item
              class="styled-menu-item"
              rounded="md"
              @click="editSidebarSessionTitle(sessionContextMenu.session!)"
            >
              <template #prepend>
                <Pencil :size="16" />
              </template>
              <v-list-item-title>
                {{ tm("conversation.editDisplayName") }}
              </v-list-item-title>
            </v-list-item>
            <!-- 2026-10-04 (elecvoid243): the menu is split into lifetime
               actions (rename / export / archive / delete) above, and the two
               lightweight markers (star / unread) below the divider. -->
            <SessionExportButton
              :session-id="sessionContextMenu.session!.session_id"
              variant="menu-item"
              :size="16"
              @export="exportSidebarSession"
            />
            <v-list-item
              class="styled-menu-item"
              rounded="md"
              @click="archiveSidebarSession(sessionContextMenu.session!)"
            >
              <template #prepend>
                <Archive :size="16" />
              </template>
              <v-list-item-title>
                {{ tm("conversation.archive") }}
              </v-list-item-title>
            </v-list-item>
            <v-list-item
              class="styled-menu-item text-error"
              rounded="md"
              @click="deleteSidebarSession(sessionContextMenu.session!)"
            >
              <template #prepend>
                <Trash2 :size="16" />
              </template>
              <v-list-item-title>
                {{ tm("actions.deleteChat") }}
              </v-list-item-title>
            </v-list-item>
            <v-divider class="my-1" />
            <!-- 2026-09-18 (elecvoid243): star toggle; the mark is persisted
               server-side so it survives reloads and other browsers.
               2026-10-04: grouped with "mark unread" — both write a marker on
               the session rather than acting on its content. -->
            <v-list-item
              class="styled-menu-item"
              rounded="md"
              @click="toggleSessionStarred(sessionContextMenu.session!)"
            >
              <template #prepend>
                <StarOff
                  v-if="sessionContextMenu.session!.starred"
                  :size="16"
                />
                <Star v-else :size="16" />
              </template>
              <v-list-item-title>
                {{
                  sessionContextMenu.session!.starred
                    ? tm("conversation.removeStar")
                    : tm("conversation.addStar")
                }}
              </v-list-item-title>
            </v-list-item>
            <v-list-item
              class="styled-menu-item"
              rounded="md"
              @click="
                toggleSessionUnread(sessionContextMenu.session!.session_id)
              "
            >
              <template #prepend>
                <MailOpen
                  v-if="
                    sessionHasUnreadMarker(
                      sessionContextMenu.session!.session_id,
                    )
                  "
                  :size="16"
                />
                <Mail v-else :size="16" />
              </template>
              <v-list-item-title>
                {{
                  sessionHasUnreadMarker(sessionContextMenu.session!.session_id)
                    ? tm("conversation.markRead")
                    : tm("conversation.markUnread")
                }}
              </v-list-item-title>
            </v-list-item>
          </StyledMenu>
        </section>
      </div>

      <div class="sidebar-footer">
        <button
          type="button"
          class="archive-toggle-btn"
          :class="{
            active: archivedDialogOpen,
            'icon-only': isSidebarCollapsed,
          }"
          :title="tm('conversation.archived')"
          @click="archivedDialogOpen = true"
        >
          <Archive
            :size="20"
            :class="['sidebar-action-icon', { 'mr-2': !isSidebarCollapsed }]"
          />
          <span v-if="!isSidebarCollapsed">
            {{ tm("conversation.archived") }}
          </span>
        </button>

        <StyledMenu
          location="top start"
          offset="10"
          :close-on-content-click="false"
        >
          <template #activator="{ props: menuProps }">
            <v-btn
              v-bind="menuProps"
              class="settings-btn"
              :class="{ 'icon-only': isSidebarCollapsed }"
              variant="text"
              :icon="isSidebarCollapsed"
            >
              <Settings
                :size="20"
                :class="[
                  'sidebar-action-icon',
                  { 'mr-2': !isSidebarCollapsed },
                ]"
              />
              <span v-if="!isSidebarCollapsed">{{
                t("core.common.settings")
              }}</span>
            </v-btn>
          </template>

          <div class="settings-menu-content">
            <v-menu
              location="end"
              offset="8"
              :open-on-hover="!isTouchDevice"
              :open-on-click="isTouchDevice"
              :close-on-content-click="true"
            >
              <template #activator="{ props: transportMenuProps }">
                <v-list-item
                  v-bind="transportMenuProps"
                  class="styled-menu-item settings-menu-item"
                  rounded="md"
                >
                  <template #prepend>
                    <Cable :size="18" class="styled-menu-lucide-icon" />
                  </template>
                  <v-list-item-title>{{
                    tm("transport.title")
                  }}</v-list-item-title>
                  <template #append>
                    <span class="settings-menu-value">{{
                      currentTransportLabel
                    }}</span>
                    <ChevronRight :size="18" class="styled-menu-lucide-icon" />
                  </template>
                </v-list-item>
              </template>

              <v-card class="styled-menu-card" elevation="8" rounded="lg">
                <v-list density="compact" class="styled-menu-list pa-1">
                  <v-list-item
                    v-for="item in transportOptions"
                    :key="item.value"
                    class="styled-menu-item"
                    :class="{
                      'styled-menu-item-active': transportMode === item.value,
                    }"
                    rounded="md"
                    @click="transportMode = item.value"
                  >
                    <v-list-item-title>{{
                      tm(item.labelKey)
                    }}</v-list-item-title>
                    <template #append>
                      <Check
                        v-if="transportMode === item.value"
                        :size="18"
                        class="styled-menu-lucide-icon"
                      />
                    </template>
                  </v-list-item>
                </v-list>
              </v-card>
            </v-menu>

            <v-menu
              location="end"
              offset="8"
              :open-on-hover="!isTouchDevice"
              :open-on-click="isTouchDevice"
              :close-on-content-click="true"
            >
              <template #activator="{ props: languageMenuProps }">
                <v-list-item
                  v-bind="languageMenuProps"
                  class="styled-menu-item settings-menu-item"
                  rounded="md"
                >
                  <template #prepend>
                    <Languages :size="18" class="styled-menu-lucide-icon" />
                  </template>
                  <v-list-item-title>{{
                    t("core.common.language")
                  }}</v-list-item-title>
                  <template #append>
                    <span class="settings-menu-value">{{
                      currentLanguage?.label || locale
                    }}</span>
                    <ChevronRight :size="18" class="styled-menu-lucide-icon" />
                  </template>
                </v-list-item>
              </template>

              <v-card class="styled-menu-card" elevation="8" rounded="lg">
                <v-list density="compact" class="styled-menu-list pa-1">
                  <v-list-item
                    v-for="lang in languageOptions"
                    :key="lang.value"
                    class="styled-menu-item"
                    :class="{
                      'styled-menu-item-active': locale === lang.value,
                    }"
                    rounded="md"
                    @click="switchLanguage(lang.value as Locale)"
                  >
                    <template #prepend>
                      <span class="language-flag">{{ lang.flag }}</span>
                    </template>
                    <v-list-item-title>{{ lang.label }}</v-list-item-title>
                    <template #append>
                      <Check
                        v-if="locale === lang.value"
                        :size="18"
                        class="styled-menu-lucide-icon"
                      />
                    </template>
                  </v-list-item>
                </v-list>
              </v-card>
            </v-menu>

            <v-list-item
              class="styled-menu-item settings-menu-item"
              rounded="md"
              @click="toggleTheme"
            >
              <template #prepend>
                <Sun v-if="isDark" :size="18" class="styled-menu-lucide-icon" />
                <Moon v-else :size="18" class="styled-menu-lucide-icon" />
              </template>
              <v-list-item-title>{{
                isDark ? tm("modes.lightMode") : tm("modes.darkMode")
              }}</v-list-item-title>
            </v-list-item>
          </div>
        </StyledMenu>
      </div>
    </aside>

    <main
      class="chat-main"
      :class="{ 'empty-chat': isEmptyChat }"
      v-on="dragEvents"
    >
      <transition name="drop-fade">
        <div
          v-if="isDragging && !isProviderWorkspace"
          class="chat-drop-overlay"
        >
          <div class="chat-drop-overlay-content">
            <v-icon size="48" color="primary">mdi-cloud-upload</v-icon>
            <span class="chat-drop-text">{{ tm("input.dropToUpload") }}</span>
          </div>
        </div>
      </transition>
      <section v-if="isProviderWorkspace" class="provider-workspace-shell">
        <ProviderChatCompletionPanel
          class="provider-workspace-page"
          :show-border="false"
        />
      </section>

      <ProjectView
        v-else-if="selectedProject"
        :project="selectedProject"
        :sessions="projectSessions"
        :umo="currentUmo"
        @select-session="selectProjectSession"
        @edit-session-title="editProjectSessionTitle"
        @delete-session="deleteProjectSession"
        @create-session="createProjectSession"
      />

      <div
        v-else
        class="conversation-stack"
        :class="{ 'is-empty': isEmptyChat }"
      >
        <SubagentPages
          v-if="currSessionId"
          ref="subagentPagesRef"
          :session-id="currSessionId"
          :toolbar-target="props.active ? '#chat-session-navigation' : undefined"
          :before-navigate="confirmFileNavigation"
          :is-dark="isDark"
          @view-change="onSubagentViewChange"
        />
        <!-- Persistent side chats, started empty or from a selected excerpt.
             The entry sits in the top-right margin, clear of the 28px
             scroll-marker rail; hovering it lists this session's side chats. -->
        <div
          v-if="currSessionId && !isReadonlySession && !subagentPageOpen"
          class="side-chat-entry"
          @mouseenter="openSideChatMenu"
          @mouseleave="sideChatMenuOpen = false"
        >
          <button
            type="button"
            class="side-chat-fab"
            :class="{ 'is-busy': creatingSideChat }"
            :disabled="creatingSideChat"
            :title="tm('thread.newSideChat')"
            :aria-label="tm('thread.newSideChat')"
            :aria-expanded="sideChatMenuOpen"
            @click="openSideChatMenu"
          >
            <v-icon size="18">{{
              creatingSideChat ? "mdi-loading" : "mdi-comment-plus-outline"
            }}</v-icon>
          </button>
          <transition name="side-chat-menu">
            <div
              v-if="sideChatMenuOpen"
              class="side-chat-menu"
              role="menu"
              @mouseenter="sideChatMenuOpen = true"
            >
              <button
                type="button"
                class="side-chat-menu-new"
                @click="openSideChat()"
              >
                <v-icon size="14">mdi-plus</v-icon>
                <span>{{ tm("thread.newSideChat") }}</span>
              </button>
              <div v-if="loadingSideChats" class="side-chat-menu-state">
                {{ tm("thread.sideLoading") }}
              </div>
              <template v-else>
                <div
                  v-for="item in sideChats"
                  :key="item.thread_id"
                  class="side-chat-menu-item"
                  :class="{
                    'is-active': item.thread_id === sideChatThreadId,
                  }"
                  role="menuitem"
                >
                  <button
                    type="button"
                    class="side-chat-menu-title"
                    @click="openSideChatThread(item)"
                  >
                    <v-icon size="13">mdi-chat-outline</v-icon>
                    <span>{{
                      item.title?.trim() || tm("thread.sideTitle")
                    }}</span>
                  </button>
                  <button
                    type="button"
                    class="side-chat-menu-delete"
                    :title="tm('thread.delete')"
                    :aria-label="tm('thread.delete')"
                    @click.stop="deleteThread(item)"
                  >
                    <v-icon size="13">mdi-delete-outline</v-icon>
                  </button>
                </div>
              </template>
              <div
                v-if="!loadingSideChats && !sideChats.length"
                class="side-chat-menu-state"
              >
                {{ tm("thread.sideEmpty") }}
              </div>
            </div>
          </transition>
        </div>

        <section
          v-if="!subagentPageOpen"
          ref="messagesContainer"
          class="messages-panel"
          @scroll="handleMessagesScroll"
          @wheel.passive="handleMessagesScrollIntent"
          @touchstart.passive="handleMessagesScrollIntent"
          @pointerdown="handleMessagesScrollIntent"
          @keydown="handleMessagesScrollIntent"
        >
          <!-- 加载中 / 消息流 / 欢迎区 主体内容 -->
          <v-alert v-if="chatError" type="error" variant="tonal" class="ma-4">{{
            chatError
          }}</v-alert>
          <div v-if="loadingMessages" class="center-state">
            <v-progress-circular indeterminate size="32" width="3" />
          </div>
          <div v-else-if="!activeMessages.length" class="welcome-state">
            <div class="welcome-title">{{ tm("welcome.title") }}</div>
          </div>

          <div
            v-else-if="activeMessages.length"
            ref="messagesListShell"
            class="messages-list-shell"
          >
            <ChatMessageList
              v-model:edit-draft="messageEditDraft"
              :messages="activeMessages"
              :history-has-more="Boolean(historyPaging?.hasMore)"
              :history-loading-older="Boolean(historyPaging?.loadingOlder)"
              :history-error="historyPaging?.error || null"
              :history-offset="historyOffset"
              :reveal-work-index="revealWorkIndex"
              :reveal-work-query="revealWorkQuery"
              :current-umo="currentUmo ?? undefined"
              :session-id="currSessionId ?? undefined"
              :is-dark="isDark"
              :is-streaming="
                Boolean(
                  currSessionId &&
                    (isSessionRunning(currSessionId) ||
                      hasLiveSystemRecord(currSessionId)),
                )
              "
              :enable-edit="
                !Boolean(
                  currSessionId &&
                    (isSessionRunning(currSessionId) ||
                      hasLiveSystemRecord(currSessionId)),
                )
              "
              :enable-regenerate="true"
              enable-branch
              enable-thread-selection
              :manage-refs-sidebar="false"
              :editing-message-id="editingMessage?.id || null"
              :saving-edit="savingMessageEdit"
              @open-edit="openMessageEdit"
              @cancel-edit="cancelMessageEdit"
              @save-edit="saveMessageEdit"
              @regenerate="handleRegenerateMessage"
              @regenerate-with-model="handleRegenerateMessage"
              @branch="handleBranch"
              @branch-toggle="onBranchToggle"
              @select-bot-text="handleBotTextSelection"
              @open-thread="openThreadPanel"
              @open-reasoning="openReasoningPanel"
              @open-refs="openRefsSidebar"
              @load-older="loadOlderHistory(true)"
            />
          </div>

          <!-- 空消息时的欢迎区 (非 sticky,自然居中) -->
          <div v-else-if="!loadingMessages" class="welcome-state">
            <div class="welcome-title">{{ tm("welcome.title") }}</div>
          </div>
        </section>

        <ChatScrollMarkers
          v-if="scrollMarkers.length"
          :key="currSessionId ?? 'scroll-markers'"
          :markers="scrollMarkers"
          :container="messagesContainer"
          :height="stripHeight"
          :right="stripRightOffset"
          :loading="jumpInProgress"
          :is-dark="isDark"
          @jump="onDotClick"
        />

        <!-- Return to the live tail (2026-10-22, elecvoid243): a long
             conversation read from the middle should not need a scrollbar drag
             to get back to output that may still be streaming in. The row is a
             zero-height flex item at the composer's top edge, so the button
             floats over the panel without taking scroll space or needing the
             composer's measured height. -->
        <transition name="jump-to-bottom">
          <div
            v-if="showJumpToBottom && !subagentPageOpen"
            class="jump-to-bottom-row"
          >
            <button
              type="button"
              class="jump-to-bottom"
              :class="{ 'jump-to-bottom--live': streamingWhileAway }"
              :title="tm('actions.scrollToBottom')"
              :aria-label="tm('actions.scrollToBottom')"
              @click="jumpToBottom"
            >
              <v-icon class="jump-to-bottom-arrow" size="20"
                >mdi-chevron-down</v-icon
              >
              <span
                v-if="streamingWhileAway"
                class="jump-to-bottom-typing"
                aria-hidden="true"
              >
                <span></span><span></span><span></span>
              </span>
            </button>
          </div>
        </transition>

        <section v-if="!subagentPageOpen" ref="composerShell" class="composer-shell">
          <template v-if="!isReadonlySession">
            <ChatInput
              ref="inputRef"
              v-model:prompt="draft"
              :staged-images-url="stagedImagesUrl"
              :staged-audio-url="stagedAudioUrl"
              :staged-files="stagedNonImageFiles"
              :failed-uploads="failedUploadViews"
              :active-uploads="activeUploadViews"
              :disabled="sending || (isOrdinaryUser && !!chatError)"
              :enable-streaming="enableStreaming"
              :is-recording="isRecording"
              :is-running="
                Boolean(
                  currSessionId &&
                    (isSessionRunning(currSessionId) ||
                      hasLiveSystemRecord(currSessionId)),
                )
              "
              :token-usage="isOrdinaryUser ? null : tokenUsageIndicator"
              :compression-notice="
                isOrdinaryUser ? null : compressionNoticeEntry
              "
              :session-id="currSessionId || null"
              :current-session="currentSession"
              :ensure-project-session="ensureProjectSession"
              @project-changed="syncLoadedProject"
              :reply-to="chatInputReplyTarget"
              :send-shortcut="sendShortcut"
              :show-provider-selector="false"
              :workspace-panel-open="gitDiffSidebarOpen"
              :placeholder="
                activeProject ? tm('input.projectPlaceholder') : undefined
              "
              @open-live-mode="openLiveMode"
              @send="sendCurrentMessage"
              @send-command="sendSystemCommand"
              @stop="stopCurrentSession"
              @flush-pending="flushPendingFromInput"
              @flush-pending-oldest="flushOldestPendingFromInput"
              @interrupt-pending="interruptPendingFromInput"
              @toggle-streaming="toggleStreaming"
              @remove-image="removeImage"
              @remove-audio="removeAudio"
              @remove-file="removeFile"
              @retry-failed-upload="retryFailedUpload"
              @discard-failed-upload="discardFailedUpload"
              @cancel-active-upload="cancelActiveUpload"
              @start-recording="startRecording"
              @stop-recording="stopRecording"
              @paste-image="handlePaste"
              @file-select="handleFilesSelected"
              @file-reference-drop="handleSidebarFileDrop"
              @clear-reply="replyTarget = null"
              @toggle-diff-sidebar="toggleGitDiffSidebar"
            />
          </template>
          <!-- 2026-08-13 (elecvoid243): archived sessions open read-only. -->
          <div v-else class="readonly-session-bar">
            <ArchiveRestore :size="16" />
            <span class="readonly-session-text">
              {{ tm("conversation.readonlyHint") }}
            </span>
            <v-btn
              size="small"
              variant="tonal"
              density="compact"
              @click="restoreReadonlySession"
            >
              {{ tm("conversation.unarchive") }}
            </v-btn>
          </div>
          <!-- Floating shell-session output windows (2026-10-05): one per
               entry in chatHeader.openShellWindows; open order is the
               stacking order, so the last entry gets the highest z-index. -->
          <template v-if="currSessionId">
            <ShellSessionWindow
              v-for="(shid, idx) in chatHeader.openShellWindows"
              :key="shid"
              :session-id="currSessionId"
              :shell-session-id="shid"
              :z-index="2000 + idx"
            />
          </template>
        </section>
      </div>
    </main>

    <div
      v-if="threadSelection.visible"
      class="thread-selection-action"
      :style="{
        left: `${threadSelection.left}px`,
        top: `${threadSelection.top}px`,
      }"
    >
      <button
        class="thread-selection-button"
        type="button"
        :disabled="creatingSideChat"
        @click="createThreadFromSelection"
      >
        {{ tm("thread.askInThread") }}
      </button>
    </div>

    <ProjectDialog
      v-model="projectDialogOpen"
      :project="editingProject"
      :error-message="projectDialogError"
      :saving="savingProject"
      @save="saveProject"
    />
    <v-dialog v-model="sessionTitleDialogOpen" max-width="420">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">
          {{ tm("conversation.editDisplayName") }}
        </v-card-title>
        <v-card-text>
          <v-text-field
            v-model="sessionTitleDraft"
            :label="tm('conversation.displayName')"
            variant="outlined"
            density="comfortable"
            hide-details
            autofocus
            @keydown.enter="saveSessionTitleDialog"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="sessionTitleDialogOpen = false">
            {{ t("core.common.cancel") }}
          </v-btn>
          <v-btn
            color="primary"
            variant="tonal"
            :loading="savingSessionTitle"
            @click="saveSessionTitleDialog"
          >
            {{ t("core.common.save") }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    <ThreadPanel
      :model-value="threadPanelOpen"
      :thread="activeThread"
      :is-dark="isDark"
      :deleting="deletingThread"
      :title="activeSideChatTitle"
      :main-session="currentSession"
      @update:model-value="onThreadPanelToggle"
      @delete="deleteThread"
    />
    <ReasoningSidebar
      v-model="reasoningPanelOpen"
      :parts="activeReasoningParts"
      :is-streaming="activeReasoningIsStreaming"
      :is-dark="isDark"
      :focus-call-id="activeReasoningTarget?.callId ?? null"
      :focus-query="activeReasoningTarget?.query ?? null"
    />
    <RefsSidebar
      v-if="!isOrdinaryUser"
      v-model="refsSidebarOpen"
      :refs="selectedRefs"
      @update:model-value="onRefsToggle"
    />
    <v-dialog v-model="liveModeOpen" fullscreen>
      <LiveMode
        v-if="liveModeOpen && liveSelection"
        v-bind="liveSelection"
        @close="
          liveModeOpen = false;
          getSessions();
        "
      />
    </v-dialog>
    <GoalSidebar
      v-model="goalSidebarOpen"
      :goal="currentGoal"
      :session-id="currSessionId"
    />
    <GitDiffSidebar
      ref="gitDiffSidebarRef"
      v-model="gitDiffSidebarOpen"
      :file-reference="fileReferenceNavigation"
      :is-dark="isDark"
      :chat-id="currSessionId"
    />
    <WorkspaceFilesPanel
      :model-value="chatHeader.workspaceFilesOpen"
      :project-id="activeProject?.project_id || ''"
      :project-title="activeProject?.title || ''"
      @update:model-value="chatHeader.SET_WORKSPACE_FILES_OPEN"
    />
  </div>

  <ChatMessageSearchDialog v-model="searchDialogOpen" />
  <ArchivedSessionsDialog
    v-model="archivedDialogOpen"
    @restore="onArchivedRestored"
    @delete="onArchivedDeleted"
    @open="openArchivedSession"
  />
  <ImportSessionsDialog
    v-model="importDialogOpen"
    @imported="onSessionsImported"
  />
</template>

<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  provide,
  reactive,
  ref,
  watch,
} from "vue";
import {
  useRoute,
  useRouter,
  onBeforeRouteLeave,
  onBeforeRouteUpdate,
} from "vue-router";
import { useDisplay } from "vuetify";
import { useAuthStore } from "@/stores/auth";
import {
  accountSessionRevision,
  assertCurrentAccount,
} from "@/utils/accountSession";
const chatAccountRevision = accountSessionRevision.value;
const auth = useAuthStore();
const isOrdinaryUser = computed(() => auth.role === "user");
const chatError = ref("");
import { isAxiosError } from "axios";
import {
  Archive,
  ArchiveRestore,
  Box,
  Cable,
  Check,
  ChevronRight,
  CornerUpLeft,
  GitBranch,
  Languages,
  ListChecks,
  Mail,
  MailOpen,
  Moon,
  MoreHorizontal,
  PanelLeft,
  Pencil,
  Search,
  Settings,
  SquarePen,
  Star,
  StarOff,
  Sun,
  Trash2,
  Upload,
} from "@lucide/vue";
import {
  chatApi,
  pluginExtensionApi,
  providerApi,
  type SessionGoalState,
} from "@/api/v1";
import { useSessionGoal } from "@/composables/useSessionGoal";
import { useShellSessions } from "@/composables/useShellSessions";
import ShellSessionWindow from "@/components/chat/ShellSessionWindow.vue";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { provideSpcodeSession } from "@/composables/useSpcodeSession";
import {
  useSpcodeProjectAutoLoad,
  ProjectLoadError,
} from "@/composables/useSpcodeProjectAutoLoad";
import {
  isSessionLoadedTag,
  markSessionLoadedTag,
  clearSessionLoadedTag,
} from "@/composables/useSpcodeProjectAutoLoad";
import { useSpcodeCodegraphStatus } from "@/composables/useSpcodeCodegraphStatus";
import { useSpcodeVivadoStatus } from "@/composables/useSpcodeVivadoStatus";
import { useSpcodePlanMode } from "@/composables/useSpcodePlanMode";
import { useFileAccessMode } from "@/composables/useFileAccessMode";
// Author: elecvoid243
// Date: 2026-10-08
// Live-only context compression notice: identity for duplicate suppression
// plus the entry handed to ChatInput, which renders the chip and its tooltip.
import {
  contextCompressionNoticeKey,
  type ContextCompressionEntry,
} from "@/composables/contextCompressionNotice";
import StyledMenu from "@/components/shared/StyledMenu.vue";
import ProjectDialog, {
  type ProjectFormData,
} from "@/components/chat/ProjectDialog.vue";
import ProjectList, { type Project } from "@/components/chat/ProjectList.vue";
import SessionExportButton from "@/components/chat/SessionExportButton.vue";
import ProjectView from "@/components/chat/ProjectView.vue";
import ChatInput from "@/components/chat/ChatInput.vue";
import LiveMode from "@/components/chat/LiveMode.vue";
const liveModeOpen = ref(false);
const liveSelection = ref<{
  sessionId: string;
  configId: string;
  selectedProvider?: string;
  selectedModel?: string;
  thinkingEffort?: string;
}>();
import ChatMessageList from "@/components/chat/ChatMessageList.vue";
import SubagentPages from "@/components/chat/SubagentPages.vue";
import ChatScrollMarkers, {
  type ChatScrollMarker,
} from "@/components/chat/ChatScrollMarkers.vue";
import ChatUILogo from "@/components/chat/ChatUILogo.vue";
import type { RegenerateModelSelection } from "@/components/chat/RegenerateMenu.vue";
import ReasoningSidebar from "@/components/chat/ReasoningSidebar.vue";
import ThreadPanel from "@/components/chat/ThreadPanel.vue";
import WorkspaceFilesPanel from "@/components/chat/WorkspaceFilesPanel.vue";
import RefsSidebar from "@/components/chat/message_list_comps/RefsSidebar.vue";
import GoalSidebar from "@/components/chat/message_list_comps/GoalSidebar.vue";
import GitDiffSidebar from "@/components/chat/GitDiffSidebar.vue";
import {
  OPEN_FILE_REFERENCE,
  type FileReferenceNavigation,
  type FileReferenceTarget,
} from "@/composables/fileReference";
import ChatMessageSearchDialog from "@/components/chat/ChatMessageSearchDialog.vue";
import ArchivedSessionsDialog from "@/components/chat/ArchivedSessionsDialog.vue";
import ImportSessionsDialog from "@/components/chat/ImportSessionsDialog.vue";
import {
  useSessions,
  type ArchivedSession,
  type Session,
} from "@/composables/useSessions";
import { useFileComments } from "@/composables/useFileComments";
import { useFileReferences } from "@/composables/useFileReferences";
import { useInlineAnnotations } from "@/composables/useInlineAnnotations";
import { NEW_CHAT_DRAFT_KEY, useChatDrafts } from "@/composables/useChatDrafts";
import { usePendingFollowUps } from "@/composables/usePendingFollowUps";
import { resolveSessionUmo } from "@/utils/resolveSessionUmo";
import {
  messageBlocks as buildMessageBlocks,
  useMessages,
  type ChatRecord,
  type ChatThread,
  type ToolCall,
  type MessagePart,
  type ThinkingEffort,
  type TransportMode,
} from "@/composables/useMessages";
import { useMediaHandling } from "@/composables/useMediaHandling";
import { useRecording } from "@/composables/useRecording";
import { useProjects } from "@/composables/useProjects";
import { useDragUpload } from "@/composables/useDragUpload";
import { useProviderModelSelection } from "@/composables/useProviderModelSelection";
import { useChatHeaderStore } from "@/stores/chatHeader";
import { projectRelativePathStrict } from "@/composables/pathUtils";
import { useCustomizerStore } from "@/stores/customizer";
import ProviderChatCompletionPanel from "@/components/provider/ProviderChatCompletionPanel.vue";
import {
  useI18n,
  useLanguageSwitcher,
  useModuleI18n,
} from "@/i18n/composables";
import type { Locale } from "@/i18n/types";
import { askForConfirmation, useConfirmDialog } from "@/utils/confirmDialog";
import {
  contextLimit,
  formatTokenCount,
  type ProviderModelMetadata,
  type ProviderMetadataSource,
} from "@/utils/providerMetadata";
import { useToast } from "@/utils/toast";
import { useInteractiveChoiceAttentionStore } from "@/stores/interactiveChoiceAttention";
import { useRunFinishedAttentionStore } from "@/stores/runFinishedAttention";
import { useSessionUnreadStore } from "@/stores/sessionUnread";
import {
  clearChoiceAttention,
  markChoiceAttention,
  markRunFinishedAttention,
} from "@/composables/useChoiceReminder";

const props = withDefaults(
  defineProps<{ chatboxMode?: boolean; active?: boolean }>(),
  {
    chatboxMode: false,
    active: true,
  },
);

const route = useRoute();
const router = useRouter();
const { lgAndUp } = useDisplay();
const chatHeader = useChatHeaderStore();
const customizer = useCustomizerStore();
const { t } = useI18n();
const { tm } = useModuleI18n("features/chat");

// Spcode project state is shared via a module-level ref in the
// composable; just grab a handle so the chat-stream watcher below can
// apply updates and so the refresh-on-session-change handler can call
// the plugin's HTTP API.
const spcodeStatus = useSpcodeProjectStatus();
const { silentLoad } = useSpcodeProjectAutoLoad();
const codegraphStatus = useSpcodeCodegraphStatus();
const vivadoStatus = useSpcodeVivadoStatus();
// Plan/build mode singleton. Mirrors the spcodeStatus lifecycle so
// both chips stay in sync across session switches and stream-ends.
const spcodePlanMode = useSpcodePlanMode();
// File access mode singleton (core-owned, per-umo). Refreshed in
// lockstep with spcodePlanMode so the chip stays in sync across
// session switches and stream-ends.
const fileAccessMode = useFileAccessMode();
const confirmDialog = useConfirmDialog();
const toast = useToast();
// Author: elecvoid243
// Date: 2026-10-08
// Current session's last context compression notice, handed to ChatInput,
// which renders it as a chip beside the token-usage ring and times its
// display. One slot on purpose: it suppresses a repeat of the same notice
// (same turn, same token delta) without keeping a growing history of keys.
// Cleared on session switch — a notice from another session must not surface
// under this session's composer.
const compressionNoticeEntry = ref<ContextCompressionEntry | null>(null);
// Sessions with an unanswered ask_user_choice prompt — drives the sidebar
// highlight and the browser attention signals.
const choiceAttention = useInteractiveChoiceAttentionStore();
// Sessions whose run finished while the user was elsewhere — calmer
// steady-dot marker, cleared as soon as the session is opened.
const finishedAttention = useRunFinishedAttentionStore();
// Sessions the user manually marked unread from the sidebar context menu —
// renders with the same green marker as `finishedAttention`.
const unreadAttention = useSessionUnreadStore();

/** A session shows the green unread marker when its run finished unseen
 * or the user marked it unread — both share one visual state. */
function sessionHasUnreadMarker(sessionId: string): boolean {
  return (
    finishedAttention.hasFinished(sessionId) ||
    unreadAttention.isUnread(sessionId)
  );
}

/** Context-menu read/unread toggle: follows the visible marker, so
 * "mark read" also acknowledges an unseen run-finished flag. */
function toggleSessionUnread(sessionId: string) {
  if (sessionHasUnreadMarker(sessionId)) {
    unreadAttention.markRead(sessionId);
    finishedAttention.clear(sessionId);
  } else {
    unreadAttention.markUnread(sessionId);
  }
}

/** 2026-09-18 (elecvoid243): context-menu star toggle. The mark is persisted
 * server-side, so it survives reloads instead of living in an in-memory
 * store like the unread marker above.
 *
 * Takes the minimal row shape because both the flat session list (`Session`)
 * and `ProjectList`'s `ProjectSession` rows reach this handler. */
async function toggleSessionStarred(session: {
  session_id: string;
  starred?: boolean;
}) {
  const next = !session.starred;
  const ok = await setSessionStarred(session.session_id, next);
  if (!ok) {
    toast.error(tm("conversation.starFailed"));
    return;
  }
  // setSessionStarred patches the flat session list; project rows live in
  // their own list passed down to ProjectList, so mirror the flag on the
  // row object itself (a no-op for flat rows, which are the same object).
  session.starred = next;
}
const { languageOptions, currentLanguage, switchLanguage, locale } =
  useLanguageSwitcher();
const {
  sessions,
  currSessionId,
  getSessions,
  newSession,
  deleteSession,
  batchDeleteSessions,
  getArchivedSessions,
  setSessionArchived,
  setSessionStarred,
  updateSessionTitle,
} = useSessions(props.chatboxMode);

// ── goal 循环状态 (右上角 Goal 按钮 + GoalSidebar) ─────────────
// Push-first: the backend emits goal_state_changed over the system stream
// on every mutation; GET /goal is only the cold-start path on session switch.
const { currentGoal, applyPushedGoal } = useSessionGoal(currSessionId);

// ── Background shell session awareness (app-bar indicator) ──────
// Push-first: the backend emits shell_sessions_changed over the system
// stream on every session lifecycle transition; GET /shell-sessions is only
// the cold-start path on session switch.
const { currentSessions: shellSessions, applyPushedShellSessions } =
  useShellSessions(currSessionId);
const goalSidebarOpen = computed({
  get: () => chatHeader.goalSidebarOpen,
  set: (open: boolean) => chatHeader.SET_GOAL_SIDEBAR_OPEN(open),
});

const {
  projects,
  selectedProjectId,
  getProjects,
  createProject,
  updateProject,
  deleteProject: deleteProjectById,
  addSessionToProject,
  removeSessionFromProject,
  getProjectSessions,
} = useProjects();

const {
  stagedFiles,
  stagedImagesUrl,
  stagedAudioUrl,
  stagedNonImageFiles,
  failedUploadViews,
  activeUploadViews,
  processAndUploadImage,
  processAndUploadFile,
  handlePaste,
  removeImage,
  removeAudio,
  removeFile,
  retryFailedUpload,
  discardFailedUpload,
  cancelActiveUpload,
  clearStaged,
  cleanupMediaCache,
} = useMediaHandling();

const { isDragging, dragEvents } = useDragUpload((files) => {
  if (isProviderWorkspace.value) return;
  handleFilesSelected(files);
});

type WorkspaceView = "chat" | "providers";

interface TokenProviderConfig extends ProviderMetadataSource {
  id: string;
  enable?: boolean;
}

const activeWorkspace = ref<WorkspaceView>("chat");
const projectDialogOpen = ref(false);
const editingProject = ref<Project | null>(null);
const projectDialogError = ref("");
const savingProject = ref(false);
const sessionTitleDialogOpen = ref(false);
const searchDialogOpen = ref(false);
const importDialogOpen = ref(false);
const sessionTitleDraft = ref("");
const editingSessionTitleId = ref("");
const refreshProjectSessionsAfterTitleSave = ref(false);
const savingSessionTitle = ref(false);
const messageEditDraft = ref("");
const editingMessage = ref<ChatRecord | null>(null);
const savingMessageEdit = ref(false);
const scrollMarkers = ref<ChatScrollMarker[]>([]);
const stripHeight = ref(0);
const stripRightOffset = ref(0); // Native scrollbar width; the marker rail sits alongside it.
// Jump-to-bottom affordance (2026-10-22, elecvoid243). The per-session follow
// map is deliberately non-reactive (see shouldStickToBottom), but the button is
// template state: `followActive` mirrors the active session's intent and
// `distanceToBottom` is how far the viewport sits above the tail. The button
// shows only when both say "the reader is away and there is something below",
// so simply clicking the panel background never raises it.
const followActive = ref(true);
const distanceToBottom = ref(0);
// A marker jump that has to page older history is in flight — suppress
// duplicate clicks and show the strip loading hint.
const jumpInProgress = ref(false);
// Mirror of ChatMessageList's collapsed branch history (default collapsed
// there too): inherited scroll markers stay hidden while collapsed.
const branchHistoryCollapsed = ref(true);
// Absolute history index of the message a message-search jump must land on
// with its agent-work capsule opened. The matched text often sits in the work
// trail that capsule hides, so landing on the row alone would still leave the
// user unable to spot the hit. ChatMessageList adopts the index once the row
// is rendered; clearing it after a successful landing lets a repeated search
// to the same index reveal again.
const revealWorkIndex = ref<number | null>(null);
// Keyword of that same jump. The list forwards it to the reasoning sidebar
// when the hit sits in the thinking text, so the sidebar opens on the matched
// thought instead of its top.
const revealWorkQuery = ref("");
// While a programmatic scroll animation runs, keep the scroll-top auto-load
// from firing mid-animation (it would prepend + anchor and cancel the
// smooth scroll, leaving the viewport off the target).
const suppressHistoryAutoLoad = ref(false);
const projectSessions = ref<Session[]>([]);
const projectSessionsById = ref<Record<string, Session[]>>({});
const loadingProjectSessionIds = ref<string[]>([]);
const loadingSessions = ref(false);
const draft = ref("");
// Per-session draft persistence (2026-09-13): every edit is mirrored into
// the session-keyed store (localStorage-backed) and selectSession swaps in
// the target session's saved draft, so unsent text is isolated per session
// and survives page navigation / reload. Text typed while no session
// exists yet is kept under NEW_CHAT_DRAFT_KEY and adopted by the session
// created on send / "new chat".
const chatDrafts = useChatDrafts();
watch(draft, (text) => {
  chatDrafts.setDraft(currSessionId.value || NEW_CHAT_DRAFT_KEY, text);
});
const tokenProviderConfigs = ref<TokenProviderConfig[]>([]);
const tokenModelMetadata = ref<Record<string, ProviderModelMetadata>>({});
const {
  selectedProviderId: selectedTokenProviderId,
  selectedModelName: currentModelName,
  thinkingEffort: sessionThinkingEffort,
  effortPreset: sessionEffortPreset,
  setSelection: setSessionModelSelection,
  setThinkingEffort: setSessionThinkingEffort,
  prepareSend: prepareModelSelection,
} = useProviderModelSelection(currSessionId);
const commandSending = ref(false);
const messagesContainer = ref<HTMLElement | null>(null);
const subagentPageOpen = ref(false);
const subagentPagesRef = ref<InstanceType<typeof SubagentPages> | null>(null);
const messagesListShell = ref<HTMLElement | null>(null);
const composerShell = ref<HTMLElement | null>(null);
const inputRef = ref<InstanceType<typeof ChatInput> | null>(null);
const projectListRef = ref<InstanceType<typeof ProjectList> | null>(null);
// Per-session viewport state (2026-09-15, elecvoid243): every conversation
// renders into the same `.messages-panel` node, so the browser preserves its
// scrollTop across a session switch and the target silently opens at the
// outgoing session's offset. Both the stick-to-bottom intent and the viewport
// anchor are therefore keyed by session id — see restoreSessionViewport().
const shouldStickToBottomBySession = new Map<string, boolean>();
// 2026-10-18 (elecvoid243): anchor = the row under the middle of the viewport
// plus how far into that row the middle sat. Not a bare scrollTop, because the
// history window resets to the newest page on every open (see
// loadSessionMessages): the same offset then describes a shorter document, the
// assignment clamped, and the viewport stopped at the bottom of the loaded
// window. `index` survives that reset; `id` keeps the anchor on the same record
// when messages arrive while the conversation is closed and shift every
// absolute index; `offset` (pixels from the row's top, negative inside the gap
// above it) is what re-centring a row used to discard, leaving the restored
// viewport up to half a row off.
type ViewportAnchor = {
  index: number;
  id: string | null;
  // null = centre the whole row (marker and search jumps); a number is a
  // remembered reading point inside the row.
  offset: number | null;
};
const viewportAnchorBySession = new Map<string, ViewportAnchor>();
// The conversation whose viewport is still being installed ("" when idle).
// Scroll events in that window still carry the outgoing conversation's
// geometry, so they must not be recorded against (or auto-page older history
// for) the incoming one. Keyed by session rather than a bare flag: a restore
// can still be paging history when the user switches again, and it must not
// release the gate the newer switch has taken over in the meantime.
let pendingViewportSessionId = "";
// Session generations also reject A -> B -> A responses from the first open.
let viewportSessionToken = 0;
let viewportInteractionToken = 0;
let bottomScrollFrame = 0;
// Gestures suspend follow before the browser dispatches their scroll events.
// Offset comparisons only filter our own echoes; layout changes are not intent.
let selfScrollTop = -1;
let lastMessagesScrollTop = 0;
let scrollGestureDirection = 0;
let readingViewportAnchor: {
  target: HTMLElement | Text;
  offset: number;
  top: number;
  scrollTop: number;
  container: HTMLElement;
  sessionToken: number;
} | null = null;
let userScrollPending = false;
let scrollPointerActive = false;
let userScrollSettleTimer = 0;

watch(
  currSessionId,
  () => {
    viewportSessionToken += 1;
    viewportInteractionToken += 1;
    pendingViewportSessionId = "";
    userScrollPending = false;
    scrollPointerActive = false;
    selfScrollTop = -1;
    lastMessagesScrollTop = 0;
    scrollGestureDirection = 0;
    readingViewportAnchor = null;
    window.clearTimeout(userScrollSettleTimer);
    cancelAnimationFrame(bottomScrollFrame);
    bottomScrollFrame = 0;
    endJumpLanding();
    endJumpScrollLock();
    jumpInProgress.value = false;
    // New conversation: the outgoing distance and intent describe nothing here.
    followActive.value = shouldStickToBottom(currSessionId.value);
    distanceToBottom.value = 0;
  },
  { flush: "sync" },
);
const replyTarget = ref<ChatRecord | null>(null);
const threadPanelOpen = ref(false);
const activeThread = ref<ChatThread | null>(null);
// Both empty and quoted side chats share the same panel and saved-chat menu.
const sideChatThreadId = ref<string | null>(null);
const creatingSideChat = ref(false);
let sideChatGeneration = 0;
type SideChatEntry = ChatThread & { title?: string };
const sideChats = ref<SideChatEntry[]>([]);
const sideChatMenuOpen = ref(false);
const loadingSideChats = ref(false);
const activeSideChatTitle = computed(() => {
  const id = sideChatThreadId.value;
  if (!id) return null;
  return sideChats.value.find((item) => item.thread_id === id)?.title || null;
});
const reasoningPanelOpen = ref(false);
const activeReasoningTarget = ref<{
  message: ChatRecord;
  blockIndex: number;
  callId?: string;
  // Message-search reveal: keyword for the sidebar to land on inside the
  // thinking block it was opened for.
  query?: string;
} | null>(null);
const deletingThread = ref(false);
const refsSidebarOpen = ref(false);
// 2026-11-07 (elecvoid243): the todo summary lives in the app bar now, next
// to the goal entry. The snapshot and the menu's open state are shared through
// the chatHeader store (same push-based pattern as goalBadge); Chat.vue keeps a
// writable alias because opening any right-hand panel must close the menu.
const todoMenuOpen = computed({
  get: () => chatHeader.todoMenuOpen,
  set: (open: boolean) => chatHeader.SET_TODO_MENU_OPEN(open),
});

function closeTodoMenu() {
  chatHeader.SET_TODO_MENU_OPEN(false);
}

const gitDiffSidebarRef = ref<{
  confirmLeaveEditing: () => Promise<boolean>;
  prepareAgentBrowser: () => Promise<boolean>;
  prepareSessionFiles: () => Promise<void>;
} | null>(null);
async function confirmFileNavigation(): Promise<boolean> {
  return (await gitDiffSidebarRef.value?.confirmLeaveEditing?.()) ?? true;
}
onBeforeRouteLeave(() => confirmFileNavigation());
onBeforeRouteUpdate(() => confirmFileNavigation());
const gitDiffSidebarOpen = ref(false);
const fileReferenceNavigation = ref<FileReferenceNavigation | null>(null);
let fileReferenceRequestId = 0;
/** Commit citation navigation only after the current editor permits leaving. */
async function openFileReference(target: FileReferenceTarget): Promise<void> {
  const requestId = ++fileReferenceRequestId;
  const sessionId = currSessionId.value;
  const generation = sideChatGeneration;
  if (!(await confirmFileNavigation())) return;
  if (
    requestId !== fileReferenceRequestId ||
    sessionId !== currSessionId.value ||
    generation !== sideChatGeneration
  )
    return;
  // Content pointerdown clears the anchor. Capture it again before the sidebar
  // changes the conversation width and reflows the text the reader is viewing.
  captureReadingAnchor();
  chatHeader.SET_WORKSPACE_FILES_OPEN(false);
  closeThreadPanel();
  reasoningPanelOpen.value = false;
  activeReasoningTarget.value = null;
  refsSidebarOpen.value = false;
  selectedRefs.value = null;
  closeTodoMenu();
  chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
  fileReferenceNavigation.value = { ...target, requestId };
  gitDiffSidebarOpen.value = true;
}
provide(OPEN_FILE_REFERENCE, openFileReference);
let lastAgentBrowserRun = "";

/** Reveal live browser work once per reply, respecting existing and dismissed panels. */
async function autoOpenAgentBrowser(
  sessionId: string,
  tool: ToolCall,
  runId: string,
): Promise<void> {
  if (
    sessionId !== currSessionId.value ||
    !/^browser_(navigate|click|type|get_state|get_html|screenshot|scroll|go_back|list_tabs|switch_tab|close_tab)$/.test(
      tool.name ?? "",
    )
  )
    return;
  const runKey = JSON.stringify([sessionId, runId]);
  if (lastAgentBrowserRun === runKey) return;
  lastAgentBrowserRun = runKey;
  if (gitDiffSidebarOpen.value) return;
  const navigationGeneration = sideChatGeneration;
  try {
    // Check that Browser Use is enabled and accessible to this chat. This status
    // request never starts a browser, including the first idle navigation.
    const response = await pluginExtensionApi.get("browser-use/status", {
      params: { chat_id: sessionId },
    });
    if (
      response.data?.status !== "ok" ||
      typeof response.data?.data?.available !== "boolean" ||
      sessionId !== currSessionId.value ||
      navigationGeneration !== sideChatGeneration ||
      runKey !== lastAgentBrowserRun ||
      gitDiffSidebarOpen.value ||
      !(await gitDiffSidebarRef.value?.prepareAgentBrowser()) ||
      sessionId !== currSessionId.value ||
      navigationGeneration !== sideChatGeneration ||
      runKey !== lastAgentBrowserRun ||
      gitDiffSidebarOpen.value
    )
      return;
    chatHeader.SET_WORKSPACE_FILES_OPEN(false);
    closeThreadPanel();
    reasoningPanelOpen.value = false;
    activeReasoningTarget.value = null;
    refsSidebarOpen.value = false;
    selectedRefs.value = null;
    closeTodoMenu();
    chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
    gitDiffSidebarOpen.value = true;
  } catch {
    // Optional observation must not interrupt the agent or show missing-plugin errors.
  }
}
const selectedRefs = ref<Record<string, unknown> | null>(null);
const threadSelection = reactive<{
  visible: boolean;
  left: number;
  top: number;
  message: ChatRecord | null;
  selectedText: string;
}>({
  visible: false,
  left: 0,
  top: 0,
  message: null,
  selectedText: "",
});
const enableStreaming = ref(true);
const sendShortcut = ref<"enter" | "shift_enter">("enter");
let composerResizeObserver: ResizeObserver | null = null;
const {
  isRecording,
  startRecording: startRecorder,
  stopRecording: stopRecorder,
} = useRecording();
// 2026-07-21 chatui sidebar resize (elecvoid243): the previous
// chatSidebarDrawer computed that bridged v-navigation-drawer's
// v-model is no longer needed — the new <aside> + class-based show
// reads from customizer.chatSidebarOpen directly. The visibility
// helpers (closeMobileSidebar, ESC handler) below also go straight
// to the store.
const isSidebarCollapsed = computed(() =>
  lgAndUp.value ? customizer.chatSidebarCollapsed : !customizer.chatSidebarOpen,
);
const isProviderWorkspace = computed(
  () => activeWorkspace.value === "providers",
);

function toggleChatSidebar() {
  if (lgAndUp.value) {
    customizer.SET_CHAT_SIDEBAR_COLLAPSED(!customizer.chatSidebarCollapsed);
    return;
  }
  customizer.TOGGLE_CHAT_SIDEBAR();
}

/* ── sidebar drag resize ─────────────────────────────────────
 * 桌面端通过拖拽 right edge 调整 sidebar 宽度; 宽度持久化在
 * customizer.chatSidebarWidth (见 stores/customizer.ts)。移动端
 * sidebar 是 fixed 抽屉, 不参与拖拽。
 * 模式沿用 sidebar 拖拽: dragState 单例 + 模块级 flag,
 * mousemove/mouseup 注册在 window 而不是 document.body (避免被
 * 拖出页面外丢失事件)。 */
const CHAT_SIDEBAR_RAIL_WIDTH = 56;
const CHAT_SIDEBAR_MIN_WIDTH = 200;
const CHAT_SIDEBAR_MAX_WIDTH = 480;
const chatSidebarRef = ref<HTMLElement | null>(null);
const isResizingSidebar = ref(false);
let sidebarDragState: {
  mouseStartX: number;
  startWidth: number;
  didMove: boolean;
} | null = null;

const chatSidebarStyle = computed(() => {
  // 移动端 fixed 抽屉占满整个屏幕宽度, 不受用户调整的宽度影响。
  if (!lgAndUp.value) {
    return { width: "min(86vw, 360px)" };
  }
  // 桌面 rail 模式宽度固定 56px, 走原本的折叠/展开语义。
  if (isSidebarCollapsed.value) {
    return { width: `${CHAT_SIDEBAR_RAIL_WIDTH}px` };
  }
  return { width: `${customizer.chatSidebarWidth}px` };
});

function startSidebarResize(e: MouseEvent) {
  if (!lgAndUp.value || isSidebarCollapsed.value) return;
  e.preventDefault();
  sidebarDragState = {
    mouseStartX: e.clientX,
    startWidth: customizer.chatSidebarWidth,
    didMove: false,
  };
  isResizingSidebar.value = true;
  window.addEventListener("mousemove", onSidebarResizeMove);
  window.addEventListener("mouseup", onSidebarResizeEnd);
}

function onSidebarResizeMove(e: MouseEvent) {
  const state = sidebarDragState;
  if (!state) return;
  // 拖拽方向: 鼠标右移 → sidebar 变宽。deltaX 为正即加宽。
  const deltaX = e.clientX - state.mouseStartX;
  // 超过 3px 才算 "真的在拖", 否则只视作 mousedown (避免误触)。
  if (!state.didMove && Math.abs(deltaX) < 3) return;
  state.didMove = true;
  const desired = state.startWidth + deltaX;
  const clamped = Math.min(
    CHAT_SIDEBAR_MAX_WIDTH,
    Math.max(CHAT_SIDEBAR_MIN_WIDTH, Math.round(desired)),
  );
  // 拖拽过程中直接写 store, 触发响应式 width 实时变化。
  customizer.SET_CHAT_SIDEBAR_WIDTH(clamped);
}

function onSidebarResizeEnd() {
  if (sidebarDragState) {
    sidebarDragState = null;
  }
  isResizingSidebar.value = false;
  window.removeEventListener("mousemove", onSidebarResizeMove);
  window.removeEventListener("mouseup", onSidebarResizeEnd);
}

// 2026-07-21 chatui sidebar resize (elecvoid243): ESC 关闭移动端抽屉,
// 镜像 Vuetify v-navigation-drawer (temporary) 原本的行为。桌面端无
// 遮罩, 不需要 ESC 关闭 (用户可点击 brand 区域的 toggle 按钮)。
function onSidebarKeydown(e: KeyboardEvent) {
  if (e.key !== "Escape") return;
  if (lgAndUp.value) return;
  if (!customizer.chatSidebarOpen) return;
  customizer.SET_CHAT_SIDEBAR(false);
}

onMounted(() => {
  window.addEventListener("keydown", onSidebarKeydown);
});

// `blockIndex` is an index into this message's FULL block list: the list
// components translate their rendered position before emitting (a collapsed
// agent-work capsule renders a filtered list). Resolving a filtered position
// here silently opened a different block — usually an empty panel.
const activeReasoningParts = computed<MessagePart[]>(() => {
  if (!activeReasoningTarget.value) return [];
  const blocks = buildMessageBlocks(
    activeReasoningTarget.value.message.content || { type: "bot", message: [] },
  );
  const block = blocks[activeReasoningTarget.value.blockIndex];
  return block?.kind === "thinking" ? block.parts : [];
});

const activeReasoningIsStreaming = computed(() => {
  const message = activeReasoningTarget.value?.message;
  const latest = [...activeMessages.value].reverse().find(
    (record) => record.content?.type === "bot",
  );
  return Boolean(
    message &&
    latest &&
    String(message.id) === String(latest.id) &&
    currSessionId.value &&
    (isSessionRunning(currSessionId.value) ||
      hasLiveSystemRecord(currSessionId.value)),
  );
});

watch(reasoningPanelOpen, (open) => {
  if (!open) {
    activeReasoningTarget.value = null;
  }
});

/**
 * Apply live goal state and reveal a new activation when no sidebar is open.
 *
 * Args:
 *   sessionId: Session owning the pushed goal state.
 *   goal: Current goal, or null after it has been cleared.
 */
function onGoalStateChanged(sessionId: string, goal: SessionGoalState | null) {
  const previous = currentGoal.value;
  applyPushedGoal(sessionId, goal);
  if (
    sessionId !== currSessionId.value ||
    !props.active ||
    goal?.status !== "active" ||
    (previous?.goal_id === goal.goal_id && previous.status === "active") ||
    goalSidebarOpen.value ||
    reasoningPanelOpen.value ||
    gitDiffSidebarOpen.value ||
    chatHeader.workspaceFilesOpen ||
    refsSidebarOpen.value ||
    threadPanelOpen.value ||
    creatingSideChat.value
  )
    return;
  chatHeader.SET_GOAL_SIDEBAR_OPEN(true);
}

const {
  loadingMessages,
  sending,
  loadedSessions,
  sessionProjects,
  sessionArchivedFlags,
  historyPagingBySession,
  historyOffsetBySession,
  sessionMarkersBySession,
  activeMessages,
  activeMessagePreviews,
  isSessionRunning,
  hasLiveSystemRecord,
  isUserMessage,
  messageParts,
  loadSessionMessages,
  loadOlderMessages,
  createLocalExchange,
  sendMessageStream,
  editMessage,
  continueEditedMessage,
  regenerateMessage,
  stopSession,
  latestTodoSnapshotBySession,
} = useMessages({
  currentSessionId: currSessionId,
  accountRole: auth.role,
  onError: (message, sessionId) => {
    if (sessionId === currSessionId.value) chatError.value = message;
  },
  onToolCall: (sessionId, tool, runId) => {
    void autoOpenAgentBrowser(sessionId, tool, runId);
  },
  onSessionsChanged: async (projectId) => {
    // A background run may finish after its source system stream was closed.
    // Refresh cached projects too when no creation notice identified the target.
    await Promise.all([
      getSessions(),
      ...(projectId ? [projectId] : Object.keys(projectSessionsById.value)).map(
        (id) => loadProjectSessions(id),
      ),
    ]);
  },
  onGoalStateChanged,
  onShellSessionsChanged: applyPushedShellSessions,
  onInteractiveChoice: (sessionId) => {
    markChoiceAttention(
      sessionId,
      {
        title: tm("interactiveChoice.attentionTitle"),
        body: tm("interactiveChoice.attentionBody"),
      },
      sessionId === currSessionId.value,
    );
  },
  onStreamUpdate: (sessionId) => {
    if (sessionId === currSessionId.value && shouldStickToBottom(sessionId)) {
      scrollToBottom();
    }
  },
  // Author: elecvoid243
  // Date: 2026-10-08
  // The runner compressed this turn's context. The notice is live-only
  // telemetry about the run (never persisted), so it is handed to ChatInput,
  // which shows it as a short-lived chip beside the token ring — and only for
  // the session on screen: a compression in a background session must not
  // interrupt what the user is reading.
  onContextCompression: (sessionId, notice) => {
    if (sessionId !== currSessionId.value) return;
    const key = contextCompressionNoticeKey(sessionId, notice);
    if (key === compressionNoticeEntry.value?.key) return;
    compressionNoticeEntry.value = { key, notice };
  },
  // Refresh the spcode "currently loaded project" chip every time a
  // bot response finishes, so commands like `/project load <dir>` or
  // `/project unload` show their effect on the chip immediately
  // without forcing the user to refresh the page.
  //
  // The previous design parsed bot text for hidden JSON markers or
  // plain-text patterns; that was abandoned because some markdown
  // renderers leaked the marker into the chat. We now let the bot
  // respond with pure prose and re-fetch the authoritative state from
  // the plugin's HTTP endpoint after every response.
  //
  // Filtered to the active session: if the user navigates away while
  // a stream is in flight, the new session's state is already covered
  // by the `currSessionId` watcher above.
  onStreamEnd: (sessionId) => {
    // 2026-09-01 (elecvoid243): surface a bounded "reply finished" notice
    // (title flash + steady sidebar dot) when a session's run ends while
    // the user is viewing a different session. The inline message list is
    // the reminder when the session itself is open.
    markRunFinishedAttention(
      sessionId,
      tm("runFinished.title"),
      sessionId === currSessionId.value,
    );
    // Run finished (or was stopped) without consuming the follow-up
    // queue — dispatch queued items as normal messages so they start
    // new runs, mirroring how the backend activates unconsumed
    // follow-up tickets. Runs for any session, not just the visible
    // one.
    void flushPendingFollowUps(sessionId);
    if (sessionId === currSessionId.value) {
      // Bug fix (2026-06-23, elecvoid243): spcodeStatus.refresh()
      // must receive the full umo (not be called bare). Without it,
      // backend's fallback branch returns the most-recently-loaded
      // project across ALL umos, so the indicator can display another
      // session's project when several umos have projects loaded
      // concurrently. Plan mode already passes umo (see below);
      // project status now mirrors the same pattern.
      //
      // Bug fix (2026-08-15, elecvoid243): same null-umo guard as the
      // currSessionId watcher. A bare refresh() would hit the
      // "most-recently-loaded across all umos" fallback and display
      // the previous session's project on the chip.
      const resolvedUmo = resolveCurrentUmo(currSessionId.value);
      if (resolvedUmo) {
        void spcodeStatus.refresh(resolvedUmo);
      } else {
        spcodeStatus.reset();
      }
      // Plan/build has the same race: an `/plan` or `/build`
      // response is processed during stream-end, so the chip needs
      // to be refreshed in lockstep to stay in sync with the bot's
      // state of record. We re-query with the FULL unified_msg_origin
      // (not the bare session id) so the backend's `_plan_mode[umo]`
      // lookup actually hits the key the bot just wrote.
      void spcodePlanMode.refresh(resolveCurrentUmo(currSessionId.value));
      // File access mode is also per-umo and may have been changed by
      // commands processed during the stream (e.g. /plan), so refresh
      // it in lockstep with the plan/build state above. Unlike the
      // plugin endpoints, the core route requires a umo — no bare
      // refresh fallback — so guard with the resolved value.
      if (resolvedUmo && !isOrdinaryUser.value) {
        void fileAccessMode.refresh(resolvedUmo);
      } else {
        fileAccessMode.reset();
      }
      // Codegraph MCP state is global (not per-umo), so no umo arg.
      // Mirrors project/plan: stream-end is the authoritative sync
      // point — the bot has just finished processing any
      // `/codegraph start|stop|set` command the user dispatched.
      if (!isOrdinaryUser.value) void codegraphStatus.refresh();
      // Vivado MCP state is also global. Refresh on stream-end so the
      // chip catches up after the bot processes any `/vivado` command.
      if (!isOrdinaryUser.value) void vivadoStatus.refresh();
    }
  },
});

// Inline file-review comments (Chunk 4). The store is a module-level
// singleton (see useFileComments.ts header); FileBrowserFilePreview
// (inside GitDiffSidebar → FileBrowserView) and this ChatInput share
// the same instance. resetForSession() drops the current session's
// comments so they don't leak across sessions (spec §2).
const fileComments = useFileComments();
// 2026-10-23 direct-send: the file comment editor asks the chat to send a
// single comment without putting it into the composer chip.
const stopCommentSend = fileComments.onSendRequest(sendCommentMessage);
onBeforeUnmount(stopCommentSend);
// 2026-08-09 drag-reference (elecvoid243): sidebar file drops become
// references. Same singleton pattern as fileComments.
const fileReferences = useFileReferences();
const inlineAnnotations = useInlineAnnotations();
watch(currSessionId, (newId, oldId) => {
  chatError.value = "";
  if (oldId && newId !== oldId) {
    fileComments.resetForSession();
    fileReferences.resetForSession();
    inlineAnnotations.resetForSession();
    // The compression chip belongs to the conversation that produced it.
    compressionNoticeEntry.value = null;
  }
});

// 2026-10-23 side-chat: switching sessions hides the panel (the side chat
// persists for the old session); a branch panel closes with its session too.
watch(
  currSessionId,
  (newId, oldId) => {
    if (oldId && newId !== oldId) {
      closeThreadPanel();
      sideChats.value = [];
      sideChatMenuOpen.value = false;
    }
  },
  { flush: "sync" },
);

// 2026-09-01 (elecvoid243): opening a session acknowledges its
// run-finished marker — the output is right there in the message list.
// 2026-09-14: it reads the session, so a manual unread mark (context
// menu) is dropped the same way.
watch(currSessionId, (sessionId) => {
  if (!sessionId) return;
  finishedAttention.clear(sessionId);
  unreadAttention.markRead(sessionId);
});

const transportStorageKey = `chat.transportMode:${
  localStorage.getItem("account_id") || "legacy"
}`;
const storedTransportMode =
  localStorage.getItem(transportStorageKey) ??
  (!isOrdinaryUser.value ? localStorage.getItem("chat.transportMode") : null);
const transportMode = ref<TransportMode>(
  storedTransportMode === "websocket" ? "websocket" : "sse",
);

const pointerMediaQuery = window.matchMedia("(pointer: coarse)");
const isTouchDevice = ref<boolean>(pointerMediaQuery.matches);
const handlePointerChange = (e: MediaQueryListEvent) => {
  isTouchDevice.value = e.matches;
};
pointerMediaQuery.addEventListener("change", handlePointerChange);
onBeforeUnmount(() => {
  pointerMediaQuery.removeEventListener("change", handlePointerChange);
});

const transportOptions: Array<{ value: TransportMode; labelKey: string }> = [
  { value: "sse", labelKey: "transport.sse" },
  { value: "websocket", labelKey: "transport.websocket" },
];
const currentTransportLabel = computed(() =>
  tm(
    transportOptions.find((item) => item.value === transportMode.value)
      ?.labelKey || "transport.sse",
  ),
);

watch(transportMode, (mode) => {
  localStorage.setItem(transportStorageKey, mode);
});

const isDark = computed(() => customizer.uiTheme === "PurpleThemeDark");
const canSend = computed(
  () =>
    Boolean(draft.value.trim() || stagedFiles.value.length) && !sending.value,
);
const currentSession = computed(
  () =>
    sessions.value.find(
      (session) => session.session_id === currSessionId.value,
    ) ||
    projectSessions.value.find(
      (session) => session.session_id === currSessionId.value,
    ) ||
    Object.values(projectSessionsById.value)
      .flat()
      .find((session) => session.session_id === currSessionId.value) ||
    null,
);
const sessionProject = computed(() =>
  currSessionId.value ? sessionProjects[currSessionId.value] : null,
);
const currentSessionTitle = computed(() =>
  currentSession.value ? sessionTitle(currentSession.value) : "",
);
// History windowing state for the active session (hasMore, loadingOlder,
// and the absolute index of the first loaded record for data-message-index).
const historyPaging = computed(() =>
  currSessionId.value
    ? historyPagingBySession[currSessionId.value] || null
    : null,
);
const historyOffset = computed(() =>
  currSessionId.value ? historyOffsetBySession[currSessionId.value] || 0 : 0,
);
// Return-to-tail button: visible once the reader is scrolled away from the
// newest message. A small threshold keeps the one-frame follow lag and
// sub-pixel rounding from flickering it in and out while a reply streams in.
const showJumpToBottom = computed(
  () => !followActive.value && distanceToBottom.value > 24,
);
// A reply is being written while the reader is parked in history — the case
// the button exists for, so its face switches to the still-writing dots and
// tells them output is still arriving below.
const streamingWhileAway = computed(
  () =>
    showJumpToBottom.value &&
    Boolean(
      currSessionId.value &&
        (isSessionRunning(currSessionId.value) ||
          hasLiveSystemRecord(currSessionId.value)),
    ),
);
const selectedProject = computed(
  () =>
    projects.value.find(
      (project) => project.project_id === selectedProjectId.value,
    ) || null,
);
const activeProject = computed(() => {
  if (isProviderWorkspace.value) return null;
  if (selectedProject.value) return selectedProject.value;
  const projectId = sessionProject.value?.project_id;
  return (
    projects.value.find((project) => project.project_id === projectId) || null
  );
});
const isEmptyChat = computed(
  () =>
    !isProviderWorkspace.value &&
    !selectedProject.value &&
    !loadingMessages.value &&
    !activeMessages.value.length,
);
const chatHeaderTitle = computed(
  () => currentSessionTitle.value || selectedProject.value?.title || "",
);
const chatHeaderSubtitle = computed(() =>
  currentSessionTitle.value
    ? sessionProject.value?.title || selectedProject.value?.title || ""
    : "",
);
const chatInputReplyTarget = computed(() =>
  replyTarget.value?.id == null
    ? null
    : {
        messageId: replyTarget.value.id,
        selectedText: replyPreview(replyTarget.value.id, ""),
      },
);
const currentTokenProvider = computed(() => {
  const selectedProvider = tokenProviderConfigs.value.find(
    (provider) => provider.id === selectedTokenProviderId.value,
  );
  return selectedProvider || tokenProviderConfigs.value[0] || null;
});
const currentTokenMetadata = computed(() => {
  const model = currentTokenProvider.value?.model;
  return model ? tokenModelMetadata.value[model] || null : null;
});
const latestTokenStats = computed(() => {
  for (let index = activeMessages.value.length - 1; index >= 0; index -= 1) {
    const message = activeMessages.value[index];
    if (isUserMessage(message)) continue;
    const stats = message.content?.agentStats;
    if (!stats) continue;
    const usage = stats.token_usage;
    if (stats.current_context_tokens != null) {
      return {
        used: readTokenCount(stats.current_context_tokens),
        usage: usage || null,
      };
    }
    if (!usage) continue;
    return {
      used:
        readTokenCount(usage.input_other) +
        readTokenCount(usage.input_cached) +
        readTokenCount(usage.output),
      usage,
    };
  }
  return { used: 0, usage: null };
});
const latestContextTokens = computed(() => latestTokenStats.value.used);
const tokenCacheHitPercent = computed(() => {
  const usage = latestTokenStats.value.usage;
  if (!usage) return null;
  const cached = readTokenCount(usage.input_cached);
  // Only show the hit rate when the provider actually reports cached
  // tokens; providers without cache support leave input_cached at 0.
  if (cached <= 0) return null;
  return (cached / (cached + readTokenCount(usage.input_other))) * 100;
});
const tokenUsageIndicator = computed(() => {
  const used = latestContextTokens.value;
  const limit = contextLimit(
    currentTokenProvider.value,
    currentTokenMetadata.value,
  );
  if (used <= 0 || limit <= 0) return null;

  const percent = (used / limit) * 100;
  const cacheHitPercent = tokenCacheHitPercent.value;
  const tooltipParams = {
    used: formatTokenCount(used),
    limit: formatTokenCount(limit),
    percent: formatUsagePercent(percent),
  };
  return {
    used,
    limit,
    percent: Math.min(100, Math.max(0, percent)),
    tooltip:
      cacheHitPercent != null
        ? tm("tokenUsage.tooltipWithCache", {
            ...tooltipParams,
            // Cache hit rate is a measured statistic, so keep 0.01% resolution;
            // the context percentage above stays coarse (it is an estimate).
            cachePercent: cacheHitPercent.toFixed(2),
          })
        : tm("tokenUsage.tooltip", tooltipParams),
  };
});

function getSelectedProviderSelection() {
  const inputSelection = inputRef.value?.getCurrentSelection();
  if (inputSelection?.providerId) {
    return inputSelection;
  }
  if (isOrdinaryUser.value || typeof window === "undefined") {
    return { providerId: "", modelName: "" };
  }
  return {
    providerId: selectedTokenProviderId.value,
    modelName: currentModelName.value,
  };
}

/** Per-message thinking-effort override from the chat input (auto by default). */
function getCurrentThinkingEffort(): ThinkingEffort {
  return inputRef.value?.getThinkingEffort() ?? "auto";
}

provide("isDark", isDark);
// Scroll coordinator for in-message cards: a card that folds asks the panel to
// centre it (see centerInMessagesPanel) instead of writing scrollTop itself,
// so stick-to-bottom and the reading anchor stay in one owner.
provide("centerInMessagesPanel", centerInMessagesPanel);
provide("openSubagentRun", (part: Record<string, any>, sessionId?: string) => {
  subagentPagesRef.value?.openRun(part, sessionId || currSessionId.value);
});
provide("subagentRunMetadata", (runId: string) => subagentPagesRef.value?.getRun(runId));

async function onSubagentViewChange(open: boolean) {
  if (open === subagentPageOpen.value) return;
  if (open) {
    recordViewportAnchor(currSessionId.value);
    viewportInteractionToken += 1;
    endJumpLanding();
    endJumpScrollLock();
    jumpInProgress.value = false;
    closeThreadPanel();
    reasoningPanelOpen.value = false;
    activeReasoningTarget.value = null;
    refsSidebarOpen.value = false;
    gitDiffSidebarOpen.value = false;
    closeTodoMenu();
    chatHeader.SET_WORKSPACE_FILES_OPEN(false);
    chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
  }
  subagentPageOpen.value = open;
  if (open) return;
  await nextTick();
  if (pendingViewportSessionId) return;
  if (shouldStickToBottom(currSessionId.value)) scrollToBottom();
  else {
    const anchor = viewportAnchorBySession.get(currSessionId.value);
    if (anchor) void jumpToViewport(anchor, false);
  }
}

async function scrollToMessageFromQuery() {
  const idxStr = route.query.scrollToIndex as string | undefined;
  if (!idxStr) return;
  const target = parseInt(idxStr, 10);
  if (isNaN(target) || target < 0) return;
  // Ask the list to open the target message's agent-work capsule before the
  // landing is measured: a match inside the collapsed work trail is invisible
  // otherwise. The list adopts the index whenever the row appears, so this
  // also covers a target that still has to be paged in. The keyword goes with
  // it — the capsule is not enough when the hit is in the thinking text.
  revealWorkIndex.value = target;
  revealWorkQuery.value = (route.query.scrollToKeyword as string) || "";
  await nextTick();
  await new Promise((r) => setTimeout(r, 300));
  // Windowed history: a search result may live outside the loaded window —
  // jumpToIndex pages older history until the target is rendered. If the
  // target cannot be reached (no more history), leave the query so the next
  // session open retries.
  if (await jumpToIndex(target)) {
    // Consumed: the capsule is expanded by now and stays that way, while the
    // cleared index keeps a later jump to the same row able to reveal again.
    revealWorkIndex.value = null;
    revealWorkQuery.value = "";
    const {
      scrollToIndex: _index,
      scrollToKeyword: _keyword,
      ...rest
    } = route.query;
    router.replace({ query: rest });
  }
}

watch(
  [chatHeaderTitle, chatHeaderSubtitle, activeProject, currSessionId],
  ([title, subtitle, project, sessionId]) => {
    chatHeader.SET_CONTEXT({
      title,
      subtitle,
      projectId: project?.project_id,
      sessionId,
    });
  },
  { immediate: true },
);

watch(
  () => chatHeader.workspaceFilesOpen,
  async (open) => {
    if (!open) return;
    if (!(await confirmFileNavigation())) {
      chatHeader.SET_WORKSPACE_FILES_OPEN(false);
      return;
    }
    threadSelection.visible = false;
    closeThreadPanel();
    reasoningPanelOpen.value = false;
    activeReasoningTarget.value = null;
    refsSidebarOpen.value = false;
    selectedRefs.value = null;
    closeTodoMenu();
    chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
    gitDiffSidebarOpen.value = false;
  },
);

watch(
  accountSessionRevision,
  () => {
    fileComments.resetForSession();
    fileReferences.resetForSession();
    closeThreadPanel();
    sideChats.value = [];
    projectSessions.value = [];
    projectSessionsById.value = {};
    selectedProjectId.value = null;
    projectDialogOpen.value = false;
    editingProject.value = null;
    onSessionDragEnd();
  },
  { flush: "sync" },
);

onMounted(async () => {
  window.addEventListener("pointerup", endMessagesScrollGesture);
  window.addEventListener("pointercancel", endMessagesScrollGesture);
  window.addEventListener("touchend", endMessagesScrollGesture);
  window.addEventListener("touchcancel", endMessagesScrollGesture);
  if (typeof ResizeObserver !== "undefined") {
    composerResizeObserver = new ResizeObserver(([entry]) => {
      const container = messagesContainer.value;
      if (!entry || !container) return;
      const height = Math.ceil(entry.target.getBoundingClientRect().height);
      container.style.setProperty("--chat-composer-height", `${height}px`);
      if (shouldStickToBottom(currSessionId.value)) scrollToBottom();
    });
    if (composerShell.value) {
      composerResizeObserver.observe(composerShell.value);
    }
  }

  loadingSessions.value = true;
  try {
    await Promise.all([
      getSessions(),
      getArchivedSessions().catch(() => undefined),
      getProjects(),
      ...(isOrdinaryUser.value ? [] : [loadTokenProviders()]),
    ]);
    const routeSessionId = getRouteSessionId();
    if (routeSessionId === "models") {
      activeWorkspace.value = "providers";
    } else if (routeSessionId) {
      await selectSession(routeSessionId, false);
      await scrollToMessageFromQuery();
    } else {
      // Fresh "/chat" landing: restore the no-session draft slot so a
      // half-typed message survives a reload.
      draft.value = chatDrafts.draftFor(NEW_CHAT_DRAFT_KEY);
    }
  } finally {
    loadingSessions.value = false;
  }
});

onBeforeUnmount(() => {
  viewportSessionToken += 1;
  viewportInteractionToken += 1;
  // Leaving the page only hides the panel; side chats persist on the server.
  closeThreadPanel();
  cancelAnimationFrame(bottomScrollFrame);
  window.clearTimeout(userScrollSettleTimer);
  endJumpLanding();
  endJumpScrollLock();
  historyLayoutObserver?.disconnect();
  historyLayoutObserver = null;
  readingViewportAnchor = null;
  window.removeEventListener("pointerup", endMessagesScrollGesture);
  window.removeEventListener("pointercancel", endMessagesScrollGesture);
  window.removeEventListener("touchend", endMessagesScrollGesture);
  window.removeEventListener("touchcancel", endMessagesScrollGesture);
  composerResizeObserver?.disconnect();
  chatHeader.CLEAR_CONTEXT();
  cleanupMediaCache();
  // 2026-07-21 chatui sidebar resize (elecvoid243): make sure no
  // stray global listeners survive the component. onSidebarResizeEnd
  // is a no-op when there's no active drag.
  onSidebarResizeEnd();
  window.removeEventListener("keydown", onSidebarKeydown);
  if (markerResizeObserver) {
    markerResizeObserver.disconnect();
    markerResizeObserver = null;
  }
});

watch(composerShell, (element, previousElement) => {
  if (!composerResizeObserver) return;
  if (previousElement) composerResizeObserver.unobserve(previousElement);
  if (element) composerResizeObserver.observe(element);
});

watch(
  () => route.params.conversationId,
  async () => {
    const routeSessionId = getRouteSessionId();
    if (routeSessionId === "models") {
      activeWorkspace.value = "providers";
      return;
    }
    if (routeSessionId && routeSessionId !== currSessionId.value) {
      showChatWorkspace();
      selectedProjectId.value = null;
      await selectSession(routeSessionId, false);
      await scrollToMessageFromQuery();
    } else if (!routeSessionId && currSessionId.value) {
      showChatWorkspace();
      currSessionId.value = "";
      // No-session landing: show the fresh-chat draft slot, not the
      // previous session's text — otherwise editing here would live-save
      // that session's text into the new-chat slot.
      draft.value = chatDrafts.draftFor(NEW_CHAT_DRAFT_KEY);
    }
  },
);

// 2026-07-28 (elecvoid243): also react to the scrollToIndex query alone.
// The params watcher above only fires when the conversation changes, so
// clicking a search result that lives in the *currently open* session would
// update the query but never scroll. This watcher closes that gap. It is
// safe alongside the params watcher because scrollToMessageFromQuery() is a
// no-op when there is no index and clears the query when done (so removing
// the index re-triggers this watcher with an empty value and returns early,
// with no recursion or double-scroll).
watch(
  () => route.query.scrollToIndex,
  (idx) => {
    if (idx != null && idx !== "") {
      void scrollToMessageFromQuery();
    }
  },
);

watch(
  activeMessages,
  () => {
    if (shouldStickToBottom(currSessionId.value)) {
      scrollToBottom();
    }
    nextTick(() => updateScrollMarkers());
  },
  { deep: true },
);

// Observe content as well as the panel: async code blocks and media can change
// scrollHeight without resizing the scrolling viewport itself.
let markerResizeObserver: ResizeObserver | null = null;
let historyLayoutObserver: MutationObserver | null = null;

watch(
  [messagesContainer, messagesListShell],
  ([container, content]) => {
    historyLayoutObserver?.disconnect();
    historyLayoutObserver = null;
    if (markerResizeObserver) {
      markerResizeObserver.disconnect();
      markerResizeObserver = null;
    }
    if (container) {
      updateScrollMarkers();
      syncDistanceToBottom();
      markerResizeObserver = new ResizeObserver(() => {
        updateScrollMarkers();
        syncDistanceToBottom();
        // A switch still installing its viewport owns the panel: the fresh
        // window has just re-rendered (rows dropped or re-keyed), and the
        // remembered anchor must go back on screen before the browser paints
        // the clamped offset. restoreSessionViewport() takes over once its
        // landing starts (jumpInProgress / jumpLandingContainer).
        if (pendingViewportSessionId) {
          installPendingViewport(pendingViewportSessionId);
          return;
        }
        if (shouldStickToBottom(currSessionId.value)) scrollToBottom();
        // 2026-10-22 (elecvoid243): while the reader drags the native
        // scrollbar the browser owns the offset, so a content-driven anchor
        // write here fights the pointer and the panel shakes. The anchor is
        // retained until another native scroll captures the reader's new
        // position. Releasing the gesture repairs any remaining displacement,
        // including a prepend that completed while the pointer was held.
        else if (!scrollPointerActive) preserveReadingAnchor();
      });
      markerResizeObserver.observe(container);
      if (content) markerResizeObserver.observe(content);
      if (content) {
        // Correct deferred rendering before paint, including changes whose
        // total height cancels out and therefore produces no resize event.
        historyLayoutObserver = new MutationObserver(() => {
          // Async markdown, media decode and prepends resize the document
          // without scrolling, so the tail distance is refreshed here too.
          syncDistanceToBottom();
          if (pendingViewportSessionId) {
            installPendingViewport(pendingViewportSessionId);
            return;
          }
          const following = shouldStickToBottom(currSessionId.value);
          if (following) scrollToBottom();
          else if (!scrollPointerActive) preserveReadingAnchor();
        });
        historyLayoutObserver.observe(content, {
          childList: true,
          subtree: true,
          characterData: true,
          attributes: true,
          attributeFilter: [
            "style",
            "class",
            "src",
            "width",
            "height",
            "hidden",
            "open",
          ],
        });
      }
    }
  },
  { immediate: true },
);

// Scroll marker strip: once the session marker index arrives, switch the
// strip from the loaded-window fallback to the full-session positions.
watch(
  () =>
    currSessionId.value ? sessionMarkersBySession[currSessionId.value] : null,
  () => nextTick(() => updateScrollMarkers()),
  { deep: true },
);

// Re-fetch the spcode status when the active session changes. Each
// session has its own loaded project so the chip must refresh.
//
// This is the only code path that updates the chip from chat activity:
// the dashboard deliberately does NOT parse bot message text for
// status markers. Status is always pulled via the plugin's HTTP
// endpoint (`spcode/project-status`) so the bot's `/project *`
// responses are free to be pure prose without hidden side channels.
watch(
  currSessionId,
  async (next) => {
    if (!next) {
      spcodeStatus.reset();
      // Plan/build is strictly per-umo; clearing the session wipes
      // its associated plan state too. We pass null so the backend
      // returns the default build status (active=false), which the
      // chip displays correctly until the next session is selected.
      spcodePlanMode.reset();
      return;
    }
    // Close the Git Diff sidebar on session switch: the new session's
    // project status (if any) is fetched async, but the sidebar would
    // otherwise keep showing the previous session's diff. Other sidebars
    // retain their current behavior (the spec's E13 "close together"
    // wording is inaccurate — only the git-diff sidebar closes here).
    gitDiffSidebarOpen.value = false;
    // Bug fix (2026-06-23, elecvoid243): pass the resolved umo so the
    // backend queries THIS session's loaded project. The bare
    // refresh() hit the "most-recently-loaded project across all
    // umos" fallback branch — wrong in the multi-umo case. See the
    // matching fix in onStreamEnd above and in ChatInput.vue's
    // showSpcodeIndicator watcher.
    //
    // Bug fix (2026-08-15, elecvoid243): a session id that does not
    // (yet) resolve to a umo must NOT reach refresh() bare. This
    // happens right after newSession() sets currSessionId but before
    // getSessions() populates the sessions list: resolveCurrentUmo
    // returns null, refresh(null) hits the same "most-recently-loaded
    // across all umos" fallback, and the chip would display the
    // previous session's project while the new conversation has no
    // umo yet. Reset to the empty state in that window.
    const resolvedUmo = resolveCurrentUmo(next);
    if (resolvedUmo) {
      // 2026-09-16 (elecvoid243): pin the shared status to THIS session
      // before refreshing, so a late response for another session can
      // no longer overwrite the chip / sidebar state.
      spcodeStatus.setActiveUmo(resolvedUmo);
      await spcodeStatus.refresh(resolvedUmo);
    } else {
      spcodeStatus.reset();
    }
    // Same lifecycle for plan/build: the chip is per-umo, so it
    // MUST be re-fetched on every session switch. We do not
    // optimistically carry over the previous session's flag because
    // plan/build is intentionally NOT shared between sessions (a
    // user might want plan mode in one project while building in
    // another).
    //
    // CRITICAL: refresh() requires the full unified_msg_origin string
    // the backend keys per-session state on, NOT the bare session id
    // (webchat conversation id). See :func:`resolveCurrentUmo` for the
    // exact format.
    await spcodePlanMode.refresh(resolveCurrentUmo(next));
    // File access mode: same per-umo lifecycle — refetch on every
    // session switch so the chip matches the core backend. The core
    // route requires a umo, so mirror the guard above: reset in the
    // unresolved-umo window rather than refresh bare.
    if (resolvedUmo && !isOrdinaryUser.value) {
      await fileAccessMode.refresh(resolvedUmo);
    } else {
      fileAccessMode.reset();
    }
  },
  { immediate: true },
);

// Resolve the full Project that owns `sessionId`, suitable for driving the
// silent spcode load. The sidebar's `selectedProject` is cleared the instant
// a session opens (selectSession sets selectedProjectId=null), so it is NOT a
// reliable source here. Instead we map the session back to its project via the
// sessionProjects reverse-map (populated by loadSessionMessages and by the
// new-session send path) and look the full Project up in the sidebar list —
// the only place carrying workspace_type / workspace_path / spcode_* fields.
// selectedProject is kept only as a last-resort id source for the narrow
// window where a brand-new session is created inside a project context.
function resolveProjectForAutoLoad(sessionId: string): Project | null {
  const projectId =
    sessionProjects[sessionId]?.project_id ??
    selectedProject.value?.project_id ??
    null;
  if (!projectId) return null;
  return projects.value.find((p) => p.project_id === projectId) ?? null;
}

// spcode auto-load: when the active session belongs to a 'project'-type
// project, silently trigger spcode load (does not pollute chat history).
// On success, refresh the chip; on failure, toast only — never block chat.
// Triggered both from the currSessionId watcher and, as a timing-safe
// backstop, right after loadSessionMessages resolves in selectSession (the
// watcher can fire before the session→project reverse-map is populated).
async function tryAutoLoadSpcodeForSession(
  umo: string,
  sessionId: string,
): Promise<void> {
  assertCurrentAccount(chatAccountRevision);
  const project = resolveProjectForAutoLoad(sessionId);
  if (!project) return;
  if (project.workspace_type !== "custom" && project.workspace_type !== "project") return;
  const directory = project.workspace_type === "project"
    ? project.resolved_workspace_path : project.workspace_path;
  if (!directory) return;

  // Dirty tag fast path (2026-09-01): if this session already completed a
  // project load for the same project under the SAME backend process,
  // do nothing at all — no webapi call, no AGENTS.md re-injection, no
  // codegraph re-init. A backend restart (detected via boot id) or a
  // project rebinding invalidates the tag and falls through to reload.
  //
  // await refresh() first: the boot id must be observed AFTER the
  // session switch (refresh shares the in-flight GET with the switch
  // watcher, so normally this costs zero extra requests). Without it a
  // backend restarted between the two switches would keep the stale
  // boot id and wrongly skip the reload.
  const statusBeforeLoad = await spcodeStatus.refresh(umo);
  assertCurrentAccount(chatAccountRevision);
  if (
    projectRelativePathStrict(statusBeforeLoad.directory ?? "", directory) === "" &&
    isSessionLoadedTag(sessionId, project.project_id, statusBeforeLoad.bootId)
  ) {
    return;
  }

  try {
    const data = await silentLoad({ project, umo });
    assertCurrentAccount(chatAccountRevision);
    if (data?.loaded) {
      const statusAfterLoad = await spcodeStatus.refresh(umo);
      assertCurrentAccount(chatAccountRevision);
      markSessionLoadedTag(
        sessionId,
        project.project_id,
        statusAfterLoad.bootId,
      );
    }
  } catch (err) {
    if (chatAccountRevision !== accountSessionRevision.value) return;
    if (err instanceof ProjectLoadError) {
      const reason = err.reason;
      let msg = tm("project.spcode.errorGeneric", { reason });
      if (reason === "feature_disabled") {
        msg = tm("project.spcode.errorFeatureDisabled");
      } else if (reason === "no_project_loaded") {
        msg = tm("project.spcode.errorAlreadyLoaded", {
          previous: err.data.previous_directory ?? "?",
          current: err.data.directory ?? "?",
        });
      } else if (reason === "path_unsafe") {
        msg = tm("project.spcode.errorPathUnsafe");
      } else if (reason === "git_error") {
        msg = tm("project.spcode.errorGitError");
      } else if (reason === "network_timeout") {
        msg = tm("project.spcode.errorNetwork");
      }
      toast.error(err.serverMessage || msg);
    } else {
      console.warn("[spcode auto-load] unexpected error:", err);
      toast.error(tm("project.spcode.errorNetwork"));
    }
  }
}

watch(currSessionId, (next) => {
  if (!next) return;
  const umo = resolveCurrentUmo(next);
  if (!umo) return;
  void tryAutoLoadSpcodeForSession(umo, next);
});

/**
 * Build the unified message origin (umo) string for a session id,
 * matching the format the backend uses to key its per-session state
 * (e.g. spcode's ``_plan_mode[umo]`` dict).
 *
 * Why this exists:
 *   - The backend's webchat adapter sets
 *     ``abm.session_id = f"webchat!{username}!{cid}"``, so the umo
 *     that ``MessageSession.__str__()`` produces is
 *     ``webchat:{FriendMessage|GroupMessage}:webchat!{user}!{cid}``.
 *   - The frontend's ``currentSession.session_id`` is the bare
 *     ``cid`` (the conversation id), not the full umo.
 *   - Passing the bare cid to ``spcodePlanMode.refresh()`` makes the
 *     backend's ``_plan_mode.get(bareCid, False)`` always miss, so
 *     ``active`` comes back ``False`` regardless of what /plan did.
 *     That is why the chip flashed plan-active for a frame then
 *     snapped back to build mode after every toggle.
 *
 * Returns the full umo, or ``null`` if the session is unknown (the
 * refresh caller treats ``null`` as "no umo → backend returns
 * build-state", which is the correct fallback for an unmapped
 * session).
 */
function resolveCurrentUmo(sessionId: string): string | null {
  // 2026-08-11 bug fix (elecvoid243): delegate to the shared resolver,
  // which also searches the projectSessionsById cache. The flat list
  // excludes project sessions server-side and projectSessions only
  // covers the currently selected project, so sessions under other
  // folders previously resolved to null — leaving the spcode chip on
  // the stale "most-recently-loaded" backend fallback.
  return resolveSessionUmo(sessionId, {
    sessions: sessions.value,
    projectSessions: projectSessions.value,
    projectSessionsById: projectSessionsById.value,
  });
}

/**
 * Current conversation's full unified_msg_origin.
 *
 * `ChatMessageList` receives this and passes it to the InteractiveChoice
 * Pinia store's `reconcile(currentUmo)` action on mount and whenever
 * the value changes, so a tab-switch picks up server-side pending
 * interactive choices without depending on a fresh SSE event.
 *
 * Format mirrors the backend's webchat adapter:
 *   `webchat:{FriendMessage|GroupMessage}:webchat!{user}!{cid}`.
 * `resolveCurrentUmo()` does the actual construction.
 */
const currentUmo = computed(() => resolveCurrentUmo(currSessionId.value));

// 2026-09-16 (elecvoid243): the git-diff sidebar subtree acts on the
// ACTIVE conversation's repository. Provide its identity + project root
// explicitly instead of letting every consumer read the shared status
// (which another session's late response could have written).
provideSpcodeSession({
  umo: currentUmo,
  directory: computed(() => spcodeStatus.statusFor(currentUmo).value.directory),
});

function getRouteSessionId() {
  const raw = route.params.conversationId;
  return Array.isArray(raw) ? raw[0] : raw || "";
}

function basePath() {
  return props.chatboxMode ? "/chatbox" : "/chat";
}

// 2026-07-21 chatui sidebar resize (elecvoid243): close the mobile
// drawer (called by the backdrop click + ESC). Desktop has no
// overlay so this is a no-op for lgAndUp.
function closeMobileSidebar() {
  if (lgAndUp.value) return;
  customizer.SET_CHAT_SIDEBAR(false);
}

function closeSecondaryPanels() {
  threadSelection.visible = false;
  closeThreadPanel();
  reasoningPanelOpen.value = false;
  activeReasoningTarget.value = null;
  refsSidebarOpen.value = false;
  selectedRefs.value = null;
  chatHeader.SET_WORKSPACE_FILES_OPEN(false);
}

function showChatWorkspace() {
  activeWorkspace.value = "chat";
}

async function openProviderWorkspace() {
  if (!(await confirmFileNavigation())) return;
  closeSecondaryPanels();
  activeWorkspace.value = "providers";
  const targetPath = `${basePath()}/models`;
  if (route.path !== targetPath) {
    await router.push(targetPath);
  }
  closeMobileSidebar();
}

function sessionTitle(session: Session) {
  return session.display_name?.trim() || tm("conversation.newConversation");
}

async function loadTokenProviders() {
  try {
    const response = await providerApi.listByProviderType("chat_completion");
    if (response.data.status === "ok") {
      tokenModelMetadata.value = ((response.data as any).model_metadata ||
        {}) as Record<string, ProviderModelMetadata>;
      tokenProviderConfigs.value = (
        (response.data.data || []) as unknown as TokenProviderConfig[]
      ).filter((provider) => provider.enable !== false);
    }
  } catch (error) {
    console.error("Failed to load provider context metadata:", error);
  }
}

function readTokenCount(value: unknown) {
  const count = Number(value || 0);
  return Number.isFinite(count) && count > 0 ? count : 0;
}

function formatUsagePercent(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0";
  if (value >= 10) return String(Math.round(value));
  if (value >= 1) return String(Math.round(value * 10) / 10);
  return String(Math.round(value * 100) / 100);
}

async function startNewChat() {
  chatError.value = "";
  if (!(await confirmFileNavigation())) return;
  showChatWorkspace();
  selectedProjectId.value = null;
  replyTarget.value = null;
  // 2026-09-01 (elecvoid243): create the session immediately so the umo
  // is available right after clicking "new chat". UMO-dependent state
  // chips (spcode / plan mode / file access) then show this session's
  // own defaults instead of the previous session's residue.
  //
  // If the current session is already empty (no messages and not still
  // loading), reuse it instead of creating a fresh one: creating on
  // every click makes each click delete-and-recreate the "new
  // conversation" entry (the session just created is the current empty
  // session), which makes the sidebar flicker and piles up blank
  // sessions. Reuse keeps the list untouched until the first message is
  // sent.
  const oldSessionId = currSessionId.value;
  const isCurrentSessionEmpty =
    Boolean(oldSessionId) &&
    !loadingMessages.value &&
    activeMessages.value.length === 0;
  if (!isCurrentSessionEmpty) {
    await newSession();
    await getSessions();
    // The text currently in the composer becomes the new session's draft
    // (today's behavior: it stays visible), and the no-session slot is
    // consumed so it cannot reappear on the landing view.
    chatDrafts.setDraft(currSessionId.value, draft.value);
    chatDrafts.clearDraft(NEW_CHAT_DRAFT_KEY);
  }
  closeMobileSidebar();
  await focusChatInput();
}

async function openCreateProjectDialog() {
  if (isOrdinaryUser.value) {
    editingProject.value = null;
    projectDialogError.value = "";
    projectDialogOpen.value = true;
    return;
  }
  showChatWorkspace();
  await nextTick();
  inputRef.value?.openLoadDialog();
}

function openEditProjectDialog(project: Project) {
  editingProject.value = project;
  projectDialogError.value = "";
  projectDialogOpen.value = true;
}

async function selectProject(projectId: string) {
  if (!(await confirmFileNavigation())) return;
  showChatWorkspace();
  selectedProjectId.value = projectId;
  currSessionId.value = "";
  replyTarget.value = null;
  await router.push(basePath());
  await loadProjectSessions(projectId);
  closeMobileSidebar();
  await focusChatInput();
}

async function loadProjectSessions(projectId = selectedProjectId.value) {
  if (!projectId) {
    projectSessions.value = [];
    return [];
  }
  const sessions = await getProjectSessions(projectId);
  assertCurrentAccount(chatAccountRevision);
  projectSessionsById.value = {
    ...projectSessionsById.value,
    [projectId]: sessions,
  };
  if (projectId === selectedProjectId.value) {
    projectSessions.value = sessions;
  }
  return sessions;
}

async function handleProjectToggle(projectId: string, expanded: boolean) {
  if (!expanded || projectSessionsById.value[projectId]) return;
  if (loadingProjectSessionIds.value.includes(projectId)) return;
  loadingProjectSessionIds.value = [
    ...loadingProjectSessionIds.value,
    projectId,
  ];
  try {
    await loadProjectSessions(projectId);
  } finally {
    loadingProjectSessionIds.value = loadingProjectSessionIds.value.filter(
      (item) => item !== projectId,
    );
  }
}

async function handleDeleteProject(projectId: string) {
  await deleteProjectById(projectId);
  const nextSessionsById = { ...projectSessionsById.value };
  delete nextSessionsById[projectId];
  projectSessionsById.value = nextSessionsById;
  loadingProjectSessionIds.value = loadingProjectSessionIds.value.filter(
    (item) => item !== projectId,
  );
  if (selectedProjectId.value === projectId) {
    selectedProjectId.value = null;
    projectSessions.value = [];
  }
}

// 2026-08-13 session drag to/from projects (HTML5 DnD). Drag state lives
// here (owner of sessions/projects); ProjectList and the unsorted session
// list bubble drag events up, and moveSessionToProject performs the move.
const draggingSessionId = ref<string | null>(null);
const draggingSessionProjectId = ref<string | null>(null);
const dragOverProjectId = ref<string | null>(null);
const dragOverSessionId = ref<string | null>(null);
const dragInsertBefore = ref(true);
const dragOverUnsorted = ref(false);
const movingSession = ref(false);

/** ProjectList session rows carry their owning project so the drag knows
 * whether it is an in-project reorder (vs. a flat-session move). */
function onSessionDragStart(sessionId: string, projectId: string): void {
  if (isOrdinaryUser.value) return;
  draggingSessionId.value = sessionId;
  draggingSessionProjectId.value = projectId;
}

/** Sidebar (unsorted) session rows set the dataTransfer themselves — the
 * ProjectList rows do it in their own component, so this is only needed
 * here. Unsorted sessions are never inside a project. */
function onSidebarSessionDragStart(sessionId: string, event: DragEvent): void {
  draggingSessionId.value = sessionId;
  draggingSessionProjectId.value = null;
  if (event.dataTransfer) {
    event.dataTransfer.setData("text/plain", sessionId);
    event.dataTransfer.effectAllowed = "move";
  }
}

function onSessionDragEnd(): void {
  draggingSessionId.value = null;
  draggingSessionProjectId.value = null;
  dragOverProjectId.value = null;
  dragOverSessionId.value = null;
  dragOverUnsorted.value = false;
}

function onProjectDragOver(projectId: string): void {
  dragOverProjectId.value = projectId;
}

function onProjectDragLeave(projectId: string): void {
  if (dragOverProjectId.value === projectId) dragOverProjectId.value = null;
}

function onProjectDrop(projectId: string): void {
  void moveSessionToProject(draggingSessionId.value, projectId);
}

function onSessionDragOver(
  projectId: string,
  sessionId: string,
  before: boolean,
): void {
  dragOverProjectId.value = projectId;
  dragOverSessionId.value = sessionId;
  dragInsertBefore.value = before;
}

function onSessionDragLeave(projectId: string): void {
  if (dragOverSessionId.value) {
    dragOverSessionId.value = null;
  }
  if (dragOverProjectId.value === projectId) {
    dragOverProjectId.value = null;
  }
}

/** Compute the 0-based insertion index for dropping next to ``sessionId``.
 *
 * The index is relative to the project list *after* removing the dragged
 * session (the backend removes any existing relation before re-inserting),
 * so an in-project reorder subtracts one when the dragged row sits above
 * the target.
 */
function projectInsertIndex(
  projectId: string,
  sessionId: string,
  before: boolean,
) {
  const list = projectSessionsById.value[projectId] || [];
  const targetIndex = list.findIndex((s) => s.session_id === sessionId);
  let index = before ? targetIndex : targetIndex + 1;
  if (index < 0) index = 0;
  if (draggingSessionId.value) {
    const draggedIndex = list.findIndex(
      (s) => s.session_id === draggingSessionId.value,
    );
    if (draggedIndex >= 0 && draggedIndex < index) {
      index -= 1;
    }
  }
  return index;
}

function onSessionDrop(
  projectId: string,
  sessionId: string,
  before: boolean,
): void {
  // The `drop` event can land on a stale row when the session list
  // re-renders mid-drag, while the visible insertion line is driven by
  // dragOverSessionId / dragInsertBefore (refreshed on every dragover).
  // Prefer the tracked values so the inserted position matches the line
  // the user actually sees; fall back to the event's row when no session
  // row is currently hovered.
  const targetSessionId = dragOverSessionId.value ?? sessionId;
  const insertBefore = dragOverSessionId.value
    ? dragInsertBefore.value
    : before;
  const index = projectInsertIndex(projectId, targetSessionId, insertBefore);
  void moveSessionToProject(draggingSessionId.value, projectId, index);
}

function onUnsortedDragOver(event: DragEvent): void {
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  dragOverUnsorted.value = true;
}

function onUnsortedDragLeave(event: DragEvent): void {
  const related = event.relatedTarget as Node | null;
  const el = event.currentTarget as HTMLElement | null;
  if (el && related && el.contains(related)) return;
  dragOverUnsorted.value = false;
}

function onUnsortedDrop(): void {
  void moveSessionToProject(draggingSessionId.value, null);
}

/** Move a session into (targetProjectId) or out of (null) a project.
 *
 * ``position`` is the 0-based index in the target project's session list;
 * ``null`` (e.g. dropping onto the project row) prepends the session.
 */
async function moveSessionToProject(
  sessionId: string | null,
  targetProjectId: string | null,
  position: number | null = null,
): Promise<void> {
  if (!sessionId || movingSession.value) return;
  const currentProjectId = draggingSessionProjectId.value;
  // Dropping onto the same project's tag (no explicit position) is a no-op,
  // but dropping at a specific position reorders within that project.
  if (currentProjectId === targetProjectId && position === null) return;

  const isReorder =
    currentProjectId === targetProjectId && targetProjectId !== null;
  movingSession.value = true;
  try {
    const ok = targetProjectId
      ? await addSessionToProject(
          sessionId,
          targetProjectId,
          position ?? undefined,
        )
      : await removeSessionFromProject(sessionId);
    if (!ok) {
      toast.error(tm("project.moveFailed"));
      return;
    }

    // Keep the reverse map (used for project resolution + spcode
    // auto-load) in sync with the new relation.
    if (targetProjectId) {
      const targetProject =
        projects.value.find((p) => p.project_id === targetProjectId) ?? null;
      sessionProjects[sessionId] = targetProject
        ? {
            project_id: targetProject.project_id,
            title: targetProject.title,
            emoji: targetProject.emoji,
          }
        : { project_id: targetProjectId, title: "", emoji: "" };
    } else {
      delete sessionProjects[sessionId];
    }

    // Refresh the flat session list (project_id field) and the affected
    // project session lists (source refreshed only when it differs from the
    // target — a same-project reorder refreshes one list, not twice).
    await Promise.allSettled([
      getSessions(),
      ...(targetProjectId ? [loadProjectSessions(targetProjectId)] : []),
      ...(currentProjectId && currentProjectId !== targetProjectId
        ? [loadProjectSessions(currentProjectId)]
        : []),
    ]);

    // Reordering within the same project is silent; cross-project moves
    // and removals surface a confirmation toast.
    if (!isReorder) {
      toast.success(
        targetProjectId
          ? tm("project.sessionAdded", {
              title:
                projects.value.find((p) => p.project_id === targetProjectId)
                  ?.title || targetProjectId,
            })
          : tm("project.sessionRemoved"),
      );
    }
  } catch {
    toast.error(tm("project.moveFailed"));
  } finally {
    movingSession.value = false;
    draggingSessionId.value = null;
    dragOverProjectId.value = null;
    dragOverSessionId.value = null;
    dragOverUnsorted.value = false;
  }
}

function openSessionTitleDialog(
  sessionId: string,
  title: string,
  refreshProjectSessions = false,
) {
  editingSessionTitleId.value = sessionId;
  sessionTitleDraft.value = title;
  refreshProjectSessionsAfterTitleSave.value = refreshProjectSessions;
  sessionTitleDialogOpen.value = true;
}

async function saveSessionTitleDialog() {
  if (!editingSessionTitleId.value) return;

  savingSessionTitle.value = true;
  try {
    const sessionId = editingSessionTitleId.value;
    const displayName = sessionTitleDraft.value.trim();
    const response = await chatApi.updateSession(sessionId, {
      display_name: displayName,
    });
    if (response.data?.status !== "ok") {
      throw new Error(response.data?.message || "Failed to rename session");
    }
    updateSessionTitle(sessionId, displayName);
    const projectSession = projectSessions.value.find(
      (session) => session.session_id === sessionId,
    );
    if (projectSession) {
      projectSession.display_name = displayName;
    }
    Object.values(projectSessionsById.value).forEach((projectSessionList) => {
      const cachedProjectSession = projectSessionList.find(
        (session) => session.session_id === sessionId,
      );
      if (cachedProjectSession) {
        cachedProjectSession.display_name = displayName;
      }
    });
    if (refreshProjectSessionsAfterTitleSave.value) {
      await loadProjectSessions();
    }
    sessionTitleDialogOpen.value = false;
  } catch (error) {
    toast.error(String((error as Error)?.message || tm("batch.requestFailed")));
  } finally {
    savingSessionTitle.value = false;
  }
}

function editSidebarSessionTitle(session: Session) {
  openSessionTitleDialog(session.session_id, session.display_name || "");
}

// 2026-09-14 (elecvoid243): right-click context menu on sidebar session
// rows — rename / archive / delete / mark-unread, mirroring the hover
// actions so they stay reachable without hovering the row first.
const sessionContextMenu = reactive({
  show: false,
  target: [0, 0] as [number, number],
  session: null as Session | null,
});

function openSessionContextMenu(session: Session, event: MouseEvent) {
  // Keyboard activation reports (0, 0), which would pin the menu to the
  // viewport corner; anchor to the activator box instead.
  const hasPointerCoords = event.clientX !== 0 || event.clientY !== 0;
  const anchor = event.currentTarget as HTMLElement | null;
  if (hasPointerCoords || !anchor) {
    sessionContextMenu.target = [event.clientX, event.clientY];
  } else {
    const rect = anchor.getBoundingClientRect();
    sessionContextMenu.target = [rect.right, rect.top + rect.height];
  }
  sessionContextMenu.session = session;
  sessionContextMenu.show = true;
}

// 2026-08-09 sidebar batch delete (elecvoid243): selection mode lets the
// user check multiple conversations (sidebar list + project sessions) and
// delete or archive them via the batch endpoints in one request.
const selectionMode = ref(false);
const checkedSessionIds = ref<Set<string>>(new Set());
const deletingCheckedSessions = ref(false);
const archivingCheckedSessions = ref(false);

/** All session ids the batch bar can act on: sidebar list plus every
 * project session list that has been loaded so far. */
const selectableSessionIds = computed(() => {
  const ids = sessions.value.map((session) => session.session_id);
  for (const projectSessionList of Object.values(projectSessionsById.value)) {
    for (const session of projectSessionList) {
      ids.push(session.session_id);
    }
  }
  return ids;
});

const allChecked = computed(
  () =>
    selectableSessionIds.value.length > 0 &&
    selectableSessionIds.value.every((id) => checkedSessionIds.value.has(id)),
);

const someChecked = computed(
  () => checkedSessionIds.value.size > 0 && !allChecked.value,
);

function toggleSelectionMode() {
  selectionMode.value = !selectionMode.value;
  if (!selectionMode.value) {
    checkedSessionIds.value = new Set();
  }
}

function toggleSessionChecked(sessionId: string) {
  const next = new Set(checkedSessionIds.value);
  if (next.has(sessionId)) {
    next.delete(sessionId);
  } else {
    next.add(sessionId);
  }
  checkedSessionIds.value = next;
}

function toggleSelectAll() {
  checkedSessionIds.value = allChecked.value
    ? new Set()
    : new Set(selectableSessionIds.value);
}

/** Row click dispatcher: in selection mode a click toggles the checkbox
 * instead of opening the session. */
function handleSidebarSessionClick(sessionId: string) {
  if (selectionMode.value) {
    toggleSessionChecked(sessionId);
    return;
  }
  selectSession(sessionId);
}

async function deleteCheckedSessions() {
  if (!(await confirmFileNavigation())) return;
  const ids = [...checkedSessionIds.value];
  if (!ids.length) return;
  const message = tm("batch.confirmDelete", { count: ids.length });
  if (!(await askForConfirmation(message, confirmDialog))) return;

  deletingCheckedSessions.value = true;
  try {
    const result = await batchDeleteSessions(ids);

    // 2026-09-01: batch delete — drop dirty tags for the removed sessions.
    const failedIds = new Set(
      result.failed_items.map((item) => item.session_id),
    );
    for (const id of ids) {
      if (!failedIds.has(id)) clearSessionLoadedTag(id);
    }
    const deletedIds = new Set(ids.filter((id) => !failedIds.has(id)));
    const affectedProjectIds = Object.keys(projectSessionsById.value).filter(
      (projectId) =>
        (projectSessionsById.value[projectId] || []).some((session) =>
          deletedIds.has(session.session_id),
        ),
    );
    await Promise.all(
      affectedProjectIds.map((projectId) => loadProjectSessions(projectId)),
    );

    if (result.currentSessionDeleted) {
      selectedProjectId.value = null;
      await router.push(basePath());
    }

    if (result.failed_count > 0) {
      toast.error(
        tm("batch.partialFailure", {
          failed: result.failed_count,
          total: ids.length,
        }),
      );
    } else {
      toast.success(tm("batch.deleteSuccess", { count: result.deleted_count }));
    }

    selectionMode.value = false;
    checkedSessionIds.value = new Set();
  } catch (error) {
    console.error("Failed to batch delete sessions:", error);
    toast.error(tm("batch.requestFailed"));
  } finally {
    deletingCheckedSessions.value = false;
  }
}

/** 2026-08-13 batch archive: moves the checked sessions into the archive
 * via the batch endpoint, then refreshes the lists and badge. */
async function archiveCheckedSessions() {
  if (!(await confirmFileNavigation())) return;
  const ids = [...checkedSessionIds.value];
  if (!ids.length) return;
  const message = tm("batch.confirmArchive", { count: ids.length });
  if (!(await askForConfirmation(message, confirmDialog))) return;

  archivingCheckedSessions.value = true;
  try {
    const response = await chatApi.batchArchiveSessions({ session_ids: ids });
    if (response.data?.status !== "ok") {
      throw new Error(
        response.data?.message || "Failed to batch archive sessions",
      );
    }
    const data = response.data?.data || {};
    const failedIds = new Set(
      (data.failed_items || []).map(
        (item: { session_id: string }) => item.session_id,
      ),
    );
    const archivedIds = new Set(ids.filter((id) => !failedIds.has(id)));

    // Refresh the flat list, the archive badge and any affected project
    // session lists.
    await Promise.all([
      getSessions(),
      getArchivedSessions().catch(() => undefined),
      ...Object.keys(projectSessionsById.value)
        .filter((projectId) =>
          (projectSessionsById.value[projectId] || []).some((session) =>
            archivedIds.has(session.session_id),
          ),
        )
        .map((projectId) => loadProjectSessions(projectId)),
    ]);

    // Archiving the active session leaves the chat view.
    if (currSessionId.value && archivedIds.has(currSessionId.value)) {
      selectedProjectId.value = null;
      await router.push(basePath());
    }

    if ((data.failed_count || 0) > 0) {
      toast.error(
        tm("batch.partialFailure", {
          failed: data.failed_count,
          total: ids.length,
        }),
      );
    } else {
      toast.success(tm("batch.archiveSuccess", { count: data.archived_count }));
    }

    selectionMode.value = false;
    checkedSessionIds.value = new Set();
  } catch (error) {
    console.error("Failed to batch archive sessions:", error);
    toast.error(tm("batch.requestFailed"));
  } finally {
    archivingCheckedSessions.value = false;
  }
}

// 2026-08-13 session archive (elecvoid243): the footer button opens the
// archived-conversations dialog.
const archivedDialogOpen = ref(false);

/** Archived sessions open read-only: hide the composer and show a restore
 * bar instead of the input. */
const isReadonlySession = computed(() =>
  Boolean(currSessionId.value && sessionArchivedFlags[currSessionId.value]),
);

async function restoreReadonlySession() {
  const sessionId = currSessionId.value;
  if (!sessionId) return;
  const ok = await setSessionArchived(sessionId, false);
  if (!ok) {
    toast.error(tm("conversation.unarchiveFailed"));
    return;
  }
  // Flip the local flag directly instead of reloading the whole history.
  sessionArchivedFlags[sessionId] = false;
  const projectId = sessionProjects[sessionId]?.project_id ?? null;
  if (projectId) {
    await loadProjectSessions(projectId);
  }
  toast.success(tm("conversation.unarchiveToast"));
}

/** Dialog "view full session": close the dialog and open the session in the
 * main chat area (read-only because it is still archived). */
function openArchivedSession(session: ArchivedSession) {
  archivedDialogOpen.value = false;
  selectSession(session.session_id);
}

async function archiveSidebarSession(session: Session | ArchivedSession) {
  if (!(await confirmFileNavigation())) return;
  const message = tm("conversation.archiveConfirm", {
    name: sessionTitle(session),
  });
  if (!(await askForConfirmation(message, confirmDialog))) return;
  const ok = await setSessionArchived(session.session_id, true);
  if (!ok) {
    toast.error(tm("conversation.archiveFailed"));
    return;
  }
  // Refresh the project list when the archived session lived in a project.
  const projectId =
    sessionProjects[session.session_id]?.project_id ??
    (session as ArchivedSession).project_id ??
    null;
  if (projectId) {
    await loadProjectSessions(projectId);
  }
  // Archiving the active session leaves the chat view.
  if (currSessionId.value === session.session_id) {
    selectedProjectId.value = null;
    await router.push(basePath());
  }
  toast.success(tm("conversation.archivedToast"));
}

/** Dialog restored session(s): refresh sidebar/project lists (dialog toasts). */
async function onArchivedRestored(session?: ArchivedSession) {
  await getSessions();
  await refreshArchivedAffectedProjects(session);
}

/** Dialog deleted archived session(s): refresh sidebar/project lists. */
async function onArchivedDeleted(session?: ArchivedSession) {
  await getSessions();
  await refreshArchivedAffectedProjects(session);
}

/**
 * 2026-09-17 session import: refresh the sidebar and open the first
 * imported session so the user lands on the migrated conversation.
 */
async function onSessionsImported(newSessionIds: string[]) {
  assertCurrentAccount(chatAccountRevision);
  await getSessions();
  assertCurrentAccount(chatAccountRevision);
  const first = newSessionIds[0];
  if (first) {
    await selectSession(first);
  }
}

/** Batch operations emit without a session; refresh every loaded project. */
async function refreshArchivedAffectedProjects(session?: ArchivedSession) {
  if (session?.project_id) {
    await loadProjectSessions(session.project_id);
    return;
  }
  await Promise.all(
    Object.keys(projectSessionsById.value).map((projectId) =>
      loadProjectSessions(projectId),
    ),
  );
}

/** Archive a project session row (from ProjectList). */
async function archiveProjectSession(sessionId: string, projectId: string) {
  if (!(await confirmFileNavigation())) return;
  const projectSession = (projectSessionsById.value[projectId] || []).find(
    (session) => session.session_id === sessionId,
  );
  const message = tm("conversation.archiveConfirm", {
    name:
      projectSession?.display_name?.trim() ||
      tm("conversation.newConversation"),
  });
  if (!(await askForConfirmation(message, confirmDialog))) return;
  const ok = await setSessionArchived(sessionId, true);
  if (!ok) {
    toast.error(tm("conversation.archiveFailed"));
    return;
  }
  await loadProjectSessions(projectId);
  // Archiving the active session leaves the chat view.
  if (currSessionId.value === sessionId) {
    selectedProjectId.value = null;
    await router.push(basePath());
  }
  toast.success(tm("conversation.archivedToast"));
}

async function deleteSidebarSession(session: Session) {
  if (!(await confirmFileNavigation())) return;
  const title = sessionTitle(session);
  const message = tm("conversation.confirmDelete", { name: title });
  if (!(await askForConfirmation(message, confirmDialog))) return;

  // 2026-09-01: session deleted — drop its "already loaded" dirty tag.
  clearSessionLoadedTag(session.session_id);
  const wasCurrent = currSessionId.value === session.session_id;
  if (!(await deleteSession(session.session_id))) {
    toast.error(tm("batch.requestFailed"));
    return;
  }
  if (wasCurrent) {
    selectedProjectId.value = null;
    await router.push(basePath());
  }
}

/**
 * 2026-09-18 I2: blob responses carry a Blob on the error path too, so the
 * server's message has to be decoded before it can be shown.
 */
async function exportErrorMessage(error: any): Promise<string | null> {
  const data = error?.response?.data;
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text());
      return parsed?.message || null;
    } catch {
      return null;
    }
  }
  return data?.message || error?.message || null;
}

/**
 * 2026-09-17 session export: download one ChatUI session package.
 * Mirrors the conversation export flow in ConversationWorkspacePage.
 */
async function exportSidebarSession(sessionId: string) {
  try {
    assertCurrentAccount(chatAccountRevision);
    const response = await chatApi.exportSession(sessionId);
    assertCurrentAccount(chatAccountRevision);
    if (!response.data?.size) throw new Error(tm("conversation.exportFailed"));
    const url = URL.createObjectURL(response.data);
    const link = document.createElement("a");
    link.href = url;
    const timestamp = new Date()
      .toISOString()
      .replace(/[:.]/g, "-")
      .slice(0, -5);
    link.setAttribute("download", `astrbot_chatui_export_${timestamp}.zip`);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
    toast.success(tm("conversation.exportSuccess"));
  } catch (error: any) {
    if (chatAccountRevision !== accountSessionRevision.value) return;
    const message = await exportErrorMessage(error);
    if (chatAccountRevision !== accountSessionRevision.value) return;
    console.error("Failed to export session:", error);
    toast.error(message || tm("conversation.exportFailed"));
  }
}

async function selectProjectSession(sessionId: string) {
  selectedProjectId.value = null;
  await selectSession(sessionId);
}

/** 2026-09-01 (elecvoid243): project "create new session" action —
 * creates a session immediately, links it to the target project and jumps
 * into the new conversation. Mirrors the send-message creation path so the
 * reverse map / spcode auto-load behave identically.
 *
 * 2026-09-15 (elecvoid243): also reached from the sidebar quick-create
 * button (ProjectList), which passes its row's project id instead of the
 * currently opened project — the parameter is optional so the project page
 * button keeps sending none. */
async function createProjectSession(projectId = selectedProjectId.value) {
  if (!(await confirmFileNavigation())) return;
  if (!projectId) return;
  // The sidebar shortcut is reachable while the provider workspace is open;
  // leaving `activeWorkspace` on "providers" would create the session without
  // ever showing it. Same reset as `startNewChat` / `selectProject`.
  showChatWorkspace();
  try {
    const sessionId = await newSession();
    // The composer text visible in the project compose view becomes the
    // new session's draft (behavior parity with the "new chat" button).
    chatDrafts.setDraft(sessionId, draft.value);
    chatDrafts.clearDraft(NEW_CHAT_DRAFT_KEY);
    // Refresh the flat session list first, otherwise the new session briefly
    // shows up in the "conversations" sidebar and then disappears.
    await getSessions();
    const alreadyLinked =
      projectSessionsById.value[projectId]?.some(
        (s) => s.session_id === sessionId,
      ) ?? false;
    if (!alreadyLinked) {
      await addSessionToProject(sessionId, projectId);
      // Resolve by id instead of `selectedProject`: the sidebar shortcut
      // can target a project row that is not the currently opened project.
      const targetProject =
        projects.value.find((project) => project.project_id === projectId) ||
        null;
      sessionProjects[sessionId] = targetProject
        ? {
            project_id: targetProject.project_id,
            title: targetProject.title,
            emoji: targetProject.emoji,
          }
        : null;
      await loadProjectSessions(projectId);
      // spcode auto-load backstop: the currSessionId watcher fires inside
      // newSession() before the sessions list / reverse map are populated,
      // so it misses. Explicit trigger mirrors the selectSession backstop.
      const umo = resolveCurrentUmo(sessionId);
      if (umo) void tryAutoLoadSpcodeForSession(umo, sessionId);
    }
    selectedProjectId.value = null;
    closeMobileSidebar();
    await focusChatInput();
  } catch (error) {
    toast.error(
      isAxiosError(error)
        ? error.response?.data?.message || error.message
        : tm("project.createSessionFailed"),
    );
    console.error("Failed to create session in project:", error);
  }
}

async function editProjectSessionTitle(sessionId: string, title: string) {
  openSessionTitleDialog(sessionId, title, true);
}

async function deleteProjectSession(
  sessionId: string,
  projectId = selectedProjectId.value,
) {
  if (!(await confirmFileNavigation())) return;
  // 2026-09-01: session deleted — drop its "already loaded" dirty tag.
  clearSessionLoadedTag(sessionId);
  await deleteSession(sessionId);
  if (projectId) {
    await loadProjectSessions(projectId);
  } else {
    await loadProjectSessions();
  }
}

async function ensureProjectSession() {
  if (!currSessionId.value) selectedProjectId.value = null;
  return currSessionId.value || await newSession();
}

async function syncLoadedProject(sessionId: string, path: string | null) {
  const previousId = sessionProjects[sessionId]?.project_id;
  try {
    if (!path) {
      if (!sessionId || currSessionId.value === sessionId) selectedProjectId.value = null;
      if (sessionId && previousId) {
        if (!(await removeSessionFromProject(sessionId))) throw new Error("Failed to remove session from project");
        delete sessionProjects[sessionId];
      }
    } else {
      let project = projects.value.find((item) => {
        const existingPath = item.workspace_type === "project" ? item.resolved_workspace_path : item.workspace_path;
        return existingPath && projectRelativePathStrict(existingPath, path) === "";
      });
      if (!project) {
        const title = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;
        const created = await createProject(title, "📁", "", "custom", path);
        project = projects.value.find((item) => item.project_id === created.project_id);
      }
      if (!project) throw new Error("Failed to find created project");
      if (previousId !== project.project_id) {
        if (!(await addSessionToProject(sessionId, project.project_id))) throw new Error("Failed to add session to project");
        sessionProjects[sessionId] = { project_id: project.project_id, title: project.title, emoji: project.emoji };
      }
      if (currSessionId.value === sessionId) selectedProjectId.value = null;
      projectListRef.value?.setProjectExpanded(project.project_id, true);
      await loadProjectSessions(project.project_id);
    }
    await Promise.all([getSessions(), ...(previousId ? [loadProjectSessions(previousId)] : [])]);
  } catch (error) {
    console.error("Failed to sync project association:", error);
    toast.error(tm("project.moveFailed"));
  }
}

async function saveProject(formData: ProjectFormData, projectId?: string) {
  savingProject.value = true;
  projectDialogError.value = "";
  try {
    if (projectId) {
      await updateProject(
        projectId,
        formData.title,
        formData.emoji,
        formData.description,
        formData.workspace_type,
        formData.workspace_path,
      );
    } else {
      await createProject(
        formData.title,
        formData.emoji,
        formData.description,
        formData.workspace_type,
        formData.workspace_path,
      );
    }
    projectDialogOpen.value = false;
    editingProject.value = null;
  } catch (error) {
    projectDialogError.value =
      error instanceof Error ? error.message : "Failed to save project";
  } finally {
    savingProject.value = false;
  }
}

watch(projectDialogOpen, (open) => {
  if (!open) {
    projectDialogError.value = "";
    savingProject.value = false;
  }
});

async function selectSession(sessionId: string, pushRoute = true) {
  if (!(await confirmFileNavigation())) return;
  showChatWorkspace();
  clearChoiceAttention(sessionId);
  selectedProjectId.value = null;
  // Capture the outgoing conversation's viewport before currSessionId flips:
  // from that point on the container renders the incoming session's content,
  // so this is the last trustworthy reading of where the outgoing one was.
  const outgoingSessionId = currSessionId.value;
  if (outgoingSessionId && outgoingSessionId !== sessionId) {
    recordViewportAnchor(outgoingSessionId);
  }
  // The incoming session owns the viewport from here: a landing that is
  // still settling would otherwise keep gliding the outgoing row.
  endJumpLanding();
  endJumpScrollLock();
  jumpInProgress.value = false;
  viewportInteractionToken += 1;
  userScrollPending = false;
  scrollPointerActive = false;
  window.clearTimeout(userScrollSettleTimer);
  cancelAnimationFrame(bottomScrollFrame);
  bottomScrollFrame = 0;
  currSessionId.value = sessionId;
  const sessionToken = ++viewportSessionToken;
  const interactionToken = viewportInteractionToken;
  pendingViewportSessionId = sessionId;
  // Per-session drafts: swap in the target session's saved composer text
  // ("" when it has none) — unsent text never leaks across sessions.
  draft.value = chatDrafts.draftFor(sessionId);
  replyTarget.value = null;
  // The incoming conversation may still be cached from an earlier visit, so
  // its rows are already in the panel at this point — only at the outgoing
  // scrollTop. Install the remembered anchor on this same tick, before the
  // route change or the history refresh can yield to a paint; otherwise the
  // reader sees the wrong place first and then a jump (the reported flash).
  await nextTick();
  installPendingViewport(sessionId);
  if (pushRoute && route.path !== `${basePath()}/${sessionId}`) {
    await router.push(`${basePath()}/${sessionId}`);
  }
  if (sessionToken !== viewportSessionToken) return;
  // 2026-08-27: always refresh, even for already-loaded sessions. The
  // loadedSessions skip left stale content when background turns (goal-loop,
  // primary runs from another device) advanced or finished
  // while the user was in another session — switching back showed a frozen
  // bubble plus everything from the switch moment onward. The merge inside
  // loadSessionMessages reconciles live records with the persisted snapshot.
  chatError.value = "";
  await loadSessionMessages(sessionId, true, false);
  if (
    sessionToken !== viewportSessionToken ||
    interactionToken !== viewportInteractionToken
  ) {
    return;
  }
  // Timing-safe backstop for spcode auto-load: the currSessionId watcher can
  // fire before loadSessionMessages populates the session→project reverse-map,
  // so resolveProjectForAutoLoad would miss on that tick. Re-evaluate now that
  // the map is filled. silentLoad is idempotent, so a duplicate trigger (when
  // the watcher already succeeded) is a harmless no-op on the server.
  {
    const umo = resolveCurrentUmo(sessionId);
    if (umo) void tryAutoLoadSpcodeForSession(umo, sessionId);
  }
  // Replaces the old unconditional scrollToBottom(): that call was gated by
  // the shared stick flag, so a session left scrolled-up blocked the snap and
  // this session kept the previous one's offset. The restore below decides per
  // session — bottom when it was following the bottom, its recorded anchor
  // otherwise — and is not awaited: it may page history, and neither the
  // sidebar nor the focus work below depends on where the viewport lands.
  void restoreSessionViewport(sessionId, sessionToken);
  closeMobileSidebar();
  await focusChatInput();
}

async function sendCurrentMessage(skillNames: string[] = []) {
  if (isOrdinaryUser.value && (sending.value || !!chatError.value)) return;
  // D13 guard: allow sending when draft is empty if there are staged
  // files OR file-review comments OR queued skill nudges (2026-10-01:
  // ChatInput already consumed the queue before this runs, so blocking
  // here would silently drop the one-shot nudge). Otherwise return.
  if (
    !canSend.value &&
    !stagedFiles.value.length &&
    fileComments.totalCount.value === 0 &&
    fileReferences.totalCount.value === 0 &&
    skillNames.length === 0
  ) {
    return;
  }

  sending.value = true;
  try {
    const configId = await inputRef.value?.prepareConfig?.();
    let sessionId = currSessionId.value;
    const targetProjectId = selectedProjectId.value;
    const targetProject = selectedProject.value;
    if (!sessionId) {
      const pendingModel = getSelectedProviderSelection();
      const pendingEffort = sessionThinkingEffort.value;
      const pendingPreset = sessionEffortPreset.value;
      sessionId = await newSession();
      await prepareModelSelection();
      if (pendingModel?.providerId)
        await setSessionModelSelection(pendingModel.providerId, pendingModel.modelName);
      if (pendingEffort !== null)
        await setSessionThinkingEffort(pendingEffort, pendingPreset || "");
      await prepareModelSelection();
      // 关联项目后再刷新，否则新会话会短暂出现在"对话"列表
      await getSessions();
      // The text being sent was typed in the no-session state and lives in
      // the fresh-chat draft slot — consumed by this send, so clear it.
      chatDrafts.clearDraft(NEW_CHAT_DRAFT_KEY);
    }
    // 2026-09-01 (elecvoid243): the "new chat" button now creates the
    // session immediately, so `sessionId` may already exist by the time
    // the user picks a project and sends the first message. Still link
    // such a session to the target project so the behavior matches the
    // "created on send" path. Sessions already in the project's loaded
    // list are skipped.
    if (targetProjectId) {
      const alreadyLinked =
        projectSessionsById.value[targetProjectId]?.some(
          (s) => s.session_id === sessionId,
        ) ?? false;
      if (!alreadyLinked) {
        await addSessionToProject(sessionId, targetProjectId);
        sessionProjects[sessionId] = targetProject
          ? {
              project_id: targetProject.project_id,
              title: targetProject.title,
              emoji: targetProject.emoji,
            }
          : null;
        await loadProjectSessions(targetProjectId);
        selectedProjectId.value = null;
        // spcode auto-load trigger for the creation path (2026-08-07):
        // the currSessionId watcher fires inside newSession() before the
        // sessions list / reverse map are populated, so it misses. This
        // explicit call mirrors the selectSession backstop — at this
        // point both resolveCurrentUmo and resolveProjectForAutoLoad
        // have their inputs ready.
        const umo = resolveCurrentUmo(sessionId);
        if (umo) void tryAutoLoadSpcodeForSession(umo, sessionId);
      }
    }

    const userText = draft.value.trim();
    const commentText = fileComments.formatForLLM();
    // 2026-08-09 drag-reference: references block goes last (after the
    // comments block) — UserPlainMessagePart parses them back in this
    // same order.
    const referenceText = fileReferences.formatForLLM();
    // 2026-10-01 skill-guide: queued one-shot skill nudges (consumed by
    // ChatInput's send funnel) ride along as a final "[Requested skills]"
    // block so the history view shows what was queued — the plugin's
    // guidance injection lives in the LLM conversation history, which
    // the dashboard history never renders. The block shape is the
    // contract parsed back by utils/parseSkillRequests.ts — keep the two
    // byte-for-byte aligned.
    const skillText =
      skillNames.length
        ? [
            "[Requested skills]",
            "The user explicitly queued the following skill(s) for this request.",
            ...skillNames.map((name) => `- \`${name}\``),
          ].join("\n")
        : "";
    // Concatenate user text + comment block + references block + skills
    // block with blank lines. The bot's first message will show the block
    // headers even when userText is empty.
    const text = [userText, commentText, referenceText, skillText]
      .filter(Boolean)
      .join("\n\n");

    // ZCode-style follow-up queue (2026-09-01): while an agent run is
    // active, hold text-only messages in the pending list above the
    // input instead of dispatching them. The card's "send" / "force
    // interrupt" buttons dispatch them later, as does stream end; see
    // flushPendingFollowUps below. Messages with staged attachments
    // keep the legacy immediate-send path so media keeps flowing
    // through the backend's own capture pipeline.
    if (
      isSessionRunning(sessionId) &&
      !stagedImagesUrl.value.length &&
      !stagedAudioUrl.value &&
      !stagedNonImageFiles.value.length
    ) {
      pendingFollowUps.enqueue(sessionId, text);
      draft.value = "";
      replyTarget.value = null;
      // References were baked into the queued text — consume them like
      // a normal send would. Comments persist by design (standing
      // block), so they are left alone.
      fileReferences.clearAll();
      return;
    }

    const messageId = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    const outgoingParts: MessagePart[] = buildOutgoingParts(text);
    const selection = getSelectedProviderSelection();
    const { userRecord, botRecord } = createLocalExchange({
      sessionId,
      messageId,
      parts: outgoingParts,
    });
    if (!isOrdinaryUser.value) updateTitleFromText(sessionId, text);

    draft.value = "";
    replyTarget.value = null;
    clearStaged({ revokeUrls: false });
    // Spec D3: references are per-message attachments — clear on send,
    // unlike comments which persist until the user deletes them.
    fileReferences.clearAll();
    scrollToBottom();

    sendMessageStream({
      sessionId,
      messageId,
      configId,
      parts: outgoingParts,
      transport: transportMode.value,
      enableStreaming: enableStreaming.value,
      selectedProvider: selection?.providerId || "",
      selectedModel: selection?.modelName || "",
      thinkingEffort: getCurrentThinkingEffort(),
      userRecord,
      botRecord,
    });
  } catch (error) {
    console.error("Failed to send message:", error);
  } finally {
    sending.value = false;
    await focusChatInput();
  }
}

/**
 * Send a system command (e.g. /plan, /build) as a chat message without
 * touching the user's draft, reply target, or staged attachments.
 *
 * The chip emits "send-command" so the parent can dispatch the toggle
 * command while the user's current input remains untouched. This mirrors
 * the existing sendMessageStream pattern used in sendCurrentMessage
 * but intentionally skips:
 *   - draft.value = ""
 *   - replyTarget reset
 *   - clearStaged({ revokeUrls: false })
 *   - updateTitleFromText
 *   - focusChatInput (let the user keep their cursor)
 *
 * Args:
 *   command: The command text to send (e.g. "/plan", "/build").
 */
async function sendSystemCommand(command: string) {
  if (!command.trim()) return;

  // Prevent overlapping system-command sends. The chip remains disabled
  // via the parent's `:disabled="sending"` binding on ChatInput, but
  // direct double-click on the chip edge can still reach here.
  if (commandSending.value) return;
  commandSending.value = true;

  try {
    let sessionId = currSessionId.value;
    if (!sessionId) {
      sessionId = await newSession();
    }

    const configId = await inputRef.value?.prepareConfig?.();
    const messageId = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;

    const outgoingParts: MessagePart[] = [{ type: "plain", text: command }];

    const selection = inputRef.value?.getCurrentSelection();

    const { userRecord, botRecord } = createLocalExchange({
      sessionId,
      messageId,
      parts: outgoingParts,
    });

    scrollToBottom();

    sendMessageStream({
      sessionId,
      configId,
      messageId,
      parts: outgoingParts,
      transport: transportMode.value,
      enableStreaming: enableStreaming.value,
      selectedProvider: selection?.providerId || "",
      selectedModel: selection?.modelName || "",
      thinkingEffort: getCurrentThinkingEffort(),
      userRecord,
      botRecord,
    });
  } catch (error) {
    console.error("Failed to send system command:", error);
  } finally {
    commandSending.value = false;
  }
}

/**
 * 2026-10-23 direct-send: send a single review comment as its own user
 * message. The temporary store entry is formatted and removed synchronously
 * so it never reaches the composer's comment chip; other pending comments
 * are untouched.
 */
function sendCommentMessage(commentId?: string): void {
  const sid = currSessionId.value;
  const sessionBusy = Boolean(sid && isSessionRunning(sid));
  if (sending.value || sessionBusy) {
    toast.error(tm("spcodeProjectLoad.fileBrowser.comment.sendBusy"));
    return;
  }
  const text = fileComments.formatForLLM(commentId);
  if (commentId) fileComments.deleteComment(commentId);
  if (!text.trim()) return;
  void sendCommentText(text);
}

async function sendCommentText(text: string): Promise<void> {
  sending.value = true;
  try {
    let sessionId = currSessionId.value;
    if (!sessionId) {
      sessionId = await newSession();
    }
    const configId = await inputRef.value?.prepareConfig?.();
    const messageId = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    const outgoingParts: MessagePart[] = [{ type: "plain", text }];
    const selection = getSelectedProviderSelection();
    const { userRecord, botRecord } = createLocalExchange({
      sessionId,
      messageId,
      parts: outgoingParts,
    });
    scrollToBottom();
    sendMessageStream({
      sessionId,
      configId,
      messageId,
      parts: outgoingParts,
      transport: transportMode.value,
      enableStreaming: enableStreaming.value,
      selectedProvider: selection?.providerId || "",
      selectedModel: selection?.modelName || "",
      thinkingEffort: getCurrentThinkingEffort(),
      userRecord,
      botRecord,
    });
  } catch (error) {
    console.error("Failed to send comment message:", error);
  } finally {
    sending.value = false;
  }
}

function buildOutgoingParts(text: string): MessagePart[] {
  const parts: MessagePart[] = [];
  if (replyTarget.value?.id != null) {
    parts.push({
      type: "reply",
      message_id: replyTarget.value.id,
      selected_text: "",
    });
  }
  if (text) {
    parts.push({ type: "plain", text });
  }
  stagedFiles.value.forEach((file) => {
    parts.push({
      type: file.type,
      attachment_id: file.attachment_id,
      filename: file.filename,
      embedded_url: file.url,
    });
  });
  return parts;
}

function updateTitleFromText(sessionId: string, text: string) {
  const session = sessions.value.find((item) => item.session_id === sessionId);
  const projectSession = projectSessions.value.find(
    (item) => item.session_id === sessionId,
  );
  const cachedProjectSessions = Object.values(projectSessionsById.value)
    .flat()
    .filter((item) => item.session_id === sessionId);
  if (
    (!session && !projectSession && !cachedProjectSessions.length) ||
    session?.display_name ||
    projectSession?.display_name ||
    cachedProjectSessions.some((item) => item.display_name) ||
    !text
  ) {
    return;
  }
  updateSessionTitle(sessionId, text.slice(0, 40));
  if (projectSession) {
    projectSession.display_name = text.slice(0, 40);
  }
  cachedProjectSessions.forEach((item) => {
    item.display_name = text.slice(0, 40);
  });
}

function replyPreview(messageId?: string | number, fallback?: string) {
  if (fallback) return truncate(fallback, 80);
  const found = activeMessages.value.find(
    (message) => String(message.id) === String(messageId),
  );
  const text = found ? plainTextFromMessage(found) : "";
  return text ? truncate(text, 80) : tm("reply.replyTo");
}

function plainTextFromMessage(message: ChatRecord) {
  return messageParts(message)
    .filter((part) => part.type === "plain" && part.text)
    .map((part) => part.text)
    .join("\n");
}

function truncate(value: string, max: number) {
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function scrollToMessage(messageId?: string | number) {
  if (!messageId) return;
  const index = activeMessages.value.findIndex(
    (message) => String(message.id) === String(messageId),
  );
  if (index < 0) return;
  // Same intent as a marker jump: the viewport is about to leave the
  // bottom, so live stick-to-bottom snaps must not fight the landing.
  setStickToBottom(currSessionId.value, false);
  const rows = messagesContainer.value?.querySelectorAll(".message-row");
  const row = rows?.[index] as HTMLElement | undefined;
  if (row) scrollRowToCenter(row);
}

// Jump scroll lock: a marker jump moves the viewport away from the bottom
// and (when paging older history) prepends pages mid-flight. Both would
// otherwise be fought by the stick-to-bottom snaps and the scroll-top
// auto-load. The lock is released once the landing has actually settled (no
// scroll events for a moment) — a fixed delay released it mid-settle on long
// jumps and deflected the landing. The re-pin loop emits those scroll events,
// so this keeps tracking the real end of the jump.
const JUMP_SCROLL_SETTLE_MS = 160;
const JUMP_SCROLL_MAX_LOCK_MS = 2500;
let jumpScrollSettleTimer = 0;
let jumpScrollCapTimer = 0;
let jumpScrollContainer: HTMLElement | null = null;
let jumpScrollOnScroll: (() => void) | null = null;

// Landing (see scrollRowToCenter): the jump glides so the eye can follow the
// travel, then holds the row centred while the freshly inserted rows finish
// growing. The glide shortens with the distance so a hop inside the viewport
// does not crawl for a full second; the hold's settle window is time-based,
// not frame-based, so its stop condition does not shift with the display
// refresh rate, and its cap bounds a jump whose target keeps being pushed
// around by media still loading above it.
const JUMP_GLIDE_MS = 1000;
const JUMP_GLIDE_MIN_MS = 300;
const JUMP_LANDING_STABLE_MS = 120;
const JUMP_LANDING_MAX_MS = 1800;
// Gestures that hand the viewport back to the user. `pointerdown` covers a
// native scrollbar drag, which fires no `wheel` event.
const JUMP_LANDING_CANCEL_EVENTS = [
  "wheel",
  "touchstart",
  "pointerdown",
] as const;
// Generation counter: bumped by endJumpLanding() so a queued frame of a
// superseded loop returns instead of re-applying a stale target.
let jumpLandingToken = 0;
let jumpLandingContainer: HTMLElement | null = null;
let jumpLandingOnGesture: (() => void) | null = null;

function endJumpScrollLock() {
  if (jumpScrollOnScroll && jumpScrollContainer) {
    jumpScrollContainer.removeEventListener("scroll", jumpScrollOnScroll);
  }
  window.clearTimeout(jumpScrollSettleTimer);
  window.clearTimeout(jumpScrollCapTimer);
  jumpScrollOnScroll = null;
  jumpScrollContainer = null;
  suppressHistoryAutoLoad.value = false;
}

function beginJumpScrollLock(container: HTMLElement) {
  endJumpScrollLock();
  // A new jump supersedes a landing that may still be settling: if this
  // attempt ends up failing, that older loop must not keep pulling the
  // viewport back to its own target.
  endJumpLanding();
  suppressHistoryAutoLoad.value = true;
  jumpScrollContainer = container;
  jumpScrollOnScroll = () => {
    window.clearTimeout(jumpScrollSettleTimer);
    jumpScrollSettleTimer = window.setTimeout(() => {
      // Never release while the paging loop is still running — page
      // fetches can be quiet for longer than the settle window.
      if (jumpInProgress.value) {
        jumpScrollOnScroll?.();
        return;
      }
      endJumpScrollLock();
    }, JUMP_SCROLL_SETTLE_MS);
  };
  container.addEventListener("scroll", jumpScrollOnScroll);
  jumpScrollOnScroll();
  // Hard cap: image loads above the viewport can keep nudging the scroll
  // position; never hold the auto-load off longer than this. Like the
  // settle timer, it waits out the paging loop — which always terminates
  // (guard counter, hasMore, no-progress bail).
  jumpScrollCapTimer = window.setTimeout(() => {
    if (jumpInProgress.value) {
      jumpScrollCapTimer = window.setTimeout(
        endJumpScrollLock,
        JUMP_SCROLL_MAX_LOCK_MS,
      );
      return;
    }
    endJumpScrollLock();
  }, JUMP_SCROLL_MAX_LOCK_MS);
}

/**
 * Stop the landing: detach its gesture listeners and invalidate the frame it
 * may already have queued. The token check inside the loop is what actually
 * stops it.
 */
function endJumpLanding() {
  jumpLandingToken += 1;
  const wasLanding = Boolean(jumpLandingContainer);
  if (jumpLandingOnGesture && jumpLandingContainer) {
    for (const type of JUMP_LANDING_CANCEL_EVENTS) {
      jumpLandingContainer.removeEventListener(type, jumpLandingOnGesture);
    }
  }
  jumpLandingOnGesture = null;
  jumpLandingContainer = null;
  if (wasLanding) captureReadingAnchor();
}

/**
 * Offset that centres `row` inside `container`, measured from live rects.
 *
 * Rect math rather than `offsetTop`: the row sits inside nested layout
 * wrappers, so its distance from the panel is not a single parent chain sum.
 *
 * @param container: The scrolling messages panel.
 * @param row: Target row.
 * @returns The scrollTop that puts the row in the middle of the panel.
 */
function centerScrollTopFor(container: HTMLElement, row: HTMLElement): number {
  const containerRect = container.getBoundingClientRect();
  const rowRect = row.getBoundingClientRect();
  return (
    container.scrollTop +
    (rowRect.top + rowRect.height / 2) -
    (containerRect.top + container.clientHeight / 2)
  );
}

/**
 * Offset that puts a remembered point of `row` at the panel's centre.
 *
 * @param container: The scrolling messages panel.
 * @param row: Row that owned the reading point.
 * @param offset: Distance from the row's top to that point; negative when the
 *   point sat in the gap above the row.
 * @returns The scrollTop that replays the same reading point.
 */
function anchorScrollTopFor(
  container: HTMLElement,
  row: HTMLElement,
  offset: number,
): number {
  const containerRect = container.getBoundingClientRect();
  const rowRect = row.getBoundingClientRect();
  return (
    container.scrollTop +
    rowRect.top +
    offset -
    (containerRect.top + container.clientHeight / 2)
  );
}

/**
 * Drive the panel to a live target offset, then hold it there.
 *
 * Two phases share one loop. During the glide the target is recomputed every
 * frame, so content that grows mid-flight — image decode, Shiki highlighting,
 * collapsed agent-work pills — retargets the glide instead of leaving it aimed
 * at an offset measured before it settled. Once the glide is over the loop
 * keeps re-applying the offset until the target stops moving, which covers
 * content that only finishes settling after the travel; without that second
 * phase a jump toward unloaded history landed wherever the half-laid-out page
 * happened to leave the row.
 *
 * Yields to the user and is bounded, so a page that keeps loading media can
 * never hold the viewport hostage.
 *
 * @param resolveTarget: Live target scrollTop, re-evaluated every frame.
 *   Returning null abandons the landing because its target is gone.
 * @param animate: Glide to the target instead of jumping straight at it. Off
 *   for callers where the travel distance carries no meaning.
 */
function runJumpLanding(
  resolveTarget: (container: HTMLElement) => number | null,
  animate = true,
) {
  const container = messagesContainer.value;
  if (!container) return;
  endJumpLanding();
  const token = jumpLandingToken;
  const sessionToken = viewportSessionToken;
  const startTop = container.scrollTop;
  const startedAt = performance.now();
  const initialTarget = resolveTarget(container);
  if (initialTarget == null) return;
  // Duration follows the distance: a hop inside the viewport should not crawl
  // for a full second, while a jump across the session takes the full glide.
  const span = Math.min(
    1,
    Math.abs(initialTarget - startTop) / Math.max(1, container.clientHeight),
  );
  const reduceMotion =
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const duration =
    !animate || reduceMotion
      ? 0
      : JUMP_GLIDE_MIN_MS + (JUMP_GLIDE_MS - JUMP_GLIDE_MIN_MS) * span;
  let lastTarget = initialTarget;
  let lastChangeAt = startedAt;
  // A scroll gesture is authoritative: the user owns the viewport from that
  // moment, so the landing drops instead of fighting them back to its target.
  // `pointerdown` is what catches a native scrollbar drag — that gesture
  // fires no `wheel` event, so without it the glide would yank the viewport
  // back mid-drag.
  jumpLandingContainer = container;
  readingViewportAnchor = null;
  jumpLandingOnGesture = () => endJumpLanding();
  for (const type of JUMP_LANDING_CANCEL_EVENTS) {
    container.addEventListener(type, jumpLandingOnGesture, { passive: true });
  }
  // Land a non-animated jump now rather than in the first frame: this is the
  // path that restores a viewport, and the frame it used to wait for was
  // exactly the wrong-position flash the reader saw on every session switch.
  // Reading scrollTop back keeps the echo compared by handleMessagesScroll()
  // equal to the clamped value (see the declaration note).
  const apply = (value: number) => {
    if (Math.abs(container.scrollTop - value) <= 0.5) return;
    container.scrollTop = value;
    selfScrollTop = container.scrollTop;
    lastMessagesScrollTop = container.scrollTop;
  };
  if (duration === 0 && props.active) apply(initialTarget);
  const step = () => {
    if (
      token !== jumpLandingToken ||
      sessionToken !== viewportSessionToken ||
      messagesContainer.value !== container ||
      !props.active
    ) {
      if (token === jumpLandingToken) endJumpLanding();
      return;
    }
    const target = resolveTarget(container);
    if (target == null) {
      endJumpLanding();
      return;
    }
    const now = performance.now();
    const progress =
      duration > 0 ? Math.min(1, (now - startedAt) / duration) : 1;
    // Ease-in-out cubic: leaves and arrives gently, so neither end of the
    // travel reads as the snap this jump used to be.
    const eased =
      progress < 0.5
        ? 4 * progress * progress * progress
        : 1 - Math.pow(-2 * progress + 2, 3) / 2;
    apply(startTop + (target - startTop) * eased);
    if (Math.abs(target - lastTarget) >= 1) {
      lastTarget = target;
      lastChangeAt = now;
    }
    // Past the glide `eased` is 1, so these frames are a plain hold.
    if (
      (progress >= 1 && now - lastChangeAt >= JUMP_LANDING_STABLE_MS) ||
      now - startedAt >= JUMP_LANDING_MAX_MS
    ) {
      endJumpLanding();
      return;
    }
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/**
 * Glide a row to the centre of the messages panel, then hold it there.
 *
 * @param row: Target row, already rendered in the messages panel.
 * @param animate: Glide to the row; false lands on it immediately, which is
 *   what a restored viewport wants — the target is where the conversation
 *   already was, not a place the user asked to travel to.
 */
function scrollRowToCenter(row: HTMLElement, animate = true) {
  setStickToBottom(currSessionId.value, false);
  runJumpLanding(
    (container) =>
      // The row can leave the document while the glide runs — a session reload
      // replaces the window, an edit truncates it. A detached row reports a zero
      // rect, which would aim the glide at a meaningless offset, so abandon the
      // landing rather than fly somewhere arbitrary.
      row.isConnected ? centerScrollTopFor(container, row) : null,
    animate,
  );
}

/**
 * Glide a row's remembered point to the centre of the panel, then hold it.
 *
 * @param row: Row that owned the reading point, already rendered.
 * @param offset: Distance from the row's top to that point.
 * @param animate: Glide to the row; false lands on it immediately, which is
 *   what a restored viewport wants.
 */
function scrollRowToAnchor(row: HTMLElement, offset: number, animate = true) {
  setStickToBottom(currSessionId.value, false);
  runJumpLanding(
    (container) =>
      row.isConnected ? anchorScrollTopFor(container, row, offset) : null,
    animate,
  );
}

/**
 * Centre an element that lives inside the messages panel, then hold it there.
 *
 * In-message cards that can fold (the subagent run block) call this after
 * collapsing. The fold shrinks the document under the reader, so the browser
 * clamps scrollTop to the shorter range and the card ends up wherever the
 * clamp left it — the reader loses the place they were reading. Landing the
 * card in the middle hands that place back, and the card flashes so it can be
 * named among its neighbours.
 *
 * Follow-the-bottom is switched off for the same reason a search jump
 * switches it off: the reader is locating a spot in the history, not
 * following the tail. The reading anchor is dropped as well — it may point
 * inside the subtree that just folded.
 *
 * @param element: Card element rendered inside the messages panel.
 * @returns True when this panel owns the element and the landing started;
 *   false for cards rendered by another scroll owner (a thread panel or the
 *   standalone web chat), which then scroll their own container.
 */
function centerInMessagesPanel(element: HTMLElement | null): boolean {
  const container = messagesContainer.value;
  if (!element || !container?.contains(element)) return false;
  setStickToBottom(currSessionId.value, false);
  readingViewportAnchor = null;
  runJumpLanding((panel) =>
    element.isConnected ? centerScrollTopFor(panel, element) : null,
  );
  return true;
}

/**
 * Find the rendered row a remembered anchor names.
 *
 * The id wins over the index: messages arriving while the conversation was
 * closed shift every absolute index, so the same index then points at an
 * earlier record. The index is the fallback for rows without an id and for
 * records that were re-keyed while away.
 *
 * @param container: The scrolling messages panel.
 * @param anchor: Remembered viewport anchor.
 * @param matchIndex: Whether the index fallback may be used. False while the
 *   index is known to sit outside the loaded window.
 * @returns The visible row, or null when it is not rendered.
 */
function findAnchorRow(
  container: HTMLElement,
  anchor: ViewportAnchor,
  matchIndex = true,
): HTMLElement | null {
  const rows = container.querySelectorAll<HTMLElement>(".message-row");
  const isVisible = (row: HTMLElement) => {
    const rect = row.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0;
  };
  if (anchor.id) {
    for (const row of rows) {
      if (row.dataset.messageId === anchor.id && isVisible(row)) return row;
    }
  }
  if (!matchIndex) return null;
  const index = String(anchor.index);
  for (const row of rows) {
    if (row.dataset.messageIndex === index && isVisible(row)) return row;
  }
  return null;
}

/**
 * Land on a history anchor. When its row sits outside the loaded window, page
 * older history until it is covered, then bring the row (or the remembered
 * point inside it) to the centre. Every direction waits out an in-flight
 * older-page load first: its prepend re-anchors scrollTop and re-keys the rows,
 * which would otherwise deflect a landing started before it.
 *
 * @param anchor: Target row, its stable record id, and the point inside it.
 * @param animate: Glide across the travel instead of landing on it. False when
 *   the call restores a viewport rather than answering a navigation request —
 *   switching conversations should feel immediate.
 * @returns True when the target row was found and the landing was started.
 */
async function jumpToViewport(
  anchor: ViewportAnchor,
  animate = true,
): Promise<boolean> {
  const sessionId = currSessionId.value;
  if (!sessionId || jumpInProgress.value) return false;
  const sessionToken = viewportSessionToken;
  const interactionToken = ++viewportInteractionToken;
  setStickToBottom(sessionId, false);
  const container = messagesContainer.value;
  if (container) beginJumpScrollLock(container);
  let scrolled = false;
  jumpInProgress.value = true;
  try {
    let guard = 0;
    while (guard++ < 100) {
      if (
        sessionToken !== viewportSessionToken ||
        interactionToken !== viewportInteractionToken
      ) {
        return false;
      }
      const paging = historyPagingBySession[sessionId];
      const offset = historyOffsetBySession[sessionId] ?? 0;
      if (paging?.loadingOlder) {
        await new Promise((r) => setTimeout(r, 120));
        continue;
      }
      const indexLoaded = anchor.index >= offset;
      // An index inside the window must be judged against the rendered list,
      // not the array it is about to become: wait out the pending update.
      // The id needs the same when the index alone says the row is not loaded,
      // because arrival of newer messages may have moved the record up.
      if (indexLoaded || anchor.id) {
        await nextTick();
        if (
          sessionToken !== viewportSessionToken ||
          interactionToken !== viewportInteractionToken
        ) {
          return false;
        }
      }
      const container = messagesContainer.value;
      const row = container
        ? findAnchorRow(container, anchor, indexLoaded)
        : null;
      if (row) {
        if (anchor.offset == null) scrollRowToCenter(row, animate);
        else scrollRowToAnchor(row, anchor.offset, animate);
        scrolled = true;
        return true;
      }
      // The index is already inside the loaded window but neither the id nor
      // the row is on screen (record deleted, branch collapsed): paging older
      // history cannot reveal it.
      if (indexLoaded) return false;
      // A page that already failed stays paused: jumping further would only
      // re-request it and bail out of the no-progress check below.
      if (!paging?.hasMore || paging?.error) return false;
      await loadOlderMessages(sessionId);
      if (
        sessionToken !== viewportSessionToken ||
        interactionToken !== viewportInteractionToken
      ) {
        return false;
      }
      // Let the prepend re-render flush so the new rows and the re-keyed
      // data-message-index attributes are visible to the next lookup.
      await nextTick();
      if (
        sessionToken !== viewportSessionToken ||
        interactionToken !== viewportInteractionToken
      ) {
        return false;
      }
      // No progress (e.g. failed request) — stop instead of spinning.
      if ((historyOffsetBySession[sessionId] ?? 0) >= offset) return false;
    }
    return false;
  } finally {
    if (interactionToken === viewportInteractionToken) {
      jumpInProgress.value = false;
      // On success the lock is owned by the settle listener; a failed
      // attempt (row missing, history exhausted, session switched) must
      // release it right away.
      if (!scrolled) endJumpScrollLock();
    }
  }
}

/**
 * Bring the row whose absolute `data-message-index` is target to the centre.
 *
 * @param targetIndex: Absolute history index to land on.
 * @param animate: Glide across the travel instead of landing on it.
 * @returns True when the target row was found and the landing was started.
 */
function jumpToIndex(targetIndex: number, animate = true): Promise<boolean> {
  return jumpToViewport(
    { index: targetIndex, id: null, offset: null },
    animate,
  );
}

function onDotClick(marker: { id: string | number; index: number }) {
  if (marker.index >= 0) {
    void jumpToIndex(marker.index);
  } else {
    // Fallback markers (no marker index loaded) — scroll over the window.
    scrollToMessage(marker.id);
  }
}

function updateScrollMarkers() {
  const container = messagesContainer.value;
  if (!container) {
    scrollMarkers.value = [];
    stripHeight.value = 0;
    stripRightOffset.value = 0;
    return;
  }
  stripHeight.value = container.clientHeight;
  // Keep the rail beside the native scrollbar. offsetWidth includes the
  // scrollbar; clientWidth does not.
  const scrollbarWidth = container.offsetWidth - container.clientWidth;
  stripRightOffset.value = scrollbarWidth > 0 ? scrollbarWidth : 0;
  const scrollable = container.scrollHeight - container.clientHeight;
  if (scrollable <= 0) {
    scrollMarkers.value = [];
    return;
  }
  // Session-wide marker index: one dot per user message, positioned by its
  // absolute index so the strip covers the whole session even while the
  // history window only holds the newest pages. Inherited markers stay
  // hidden while the branch history is collapsed, matching the visible rows.
  const markerIndex = currSessionId.value
    ? sessionMarkersBySession[currSessionId.value]
    : null;
  if (markerIndex?.loaded && markerIndex.markers.length) {
    const total = Math.max(
      1,
      markerIndex.totalMessages,
      historyOffset.value + activeMessages.value.length,
    );
    const rows = container.querySelectorAll(".message-row");
    const markers: ChatScrollMarker[] = markerIndex.markers.map((m) => ({
      id: m.id,
      index: m.index,
      messageIndex: m.index,
      ordinal: 0,
      topPct: total > 1 ? (m.index / (total - 1)) * 100 : 0,
      preview:
        activeMessagePreviews.value.get(String(m.id))?.preview ||
        m.snippet ||
        "…",
      replyPreview:
        activeMessagePreviews.value.get(String(m.id))?.replyPreview ??
        m.replySnippet,
      replyLoading:
        activeMessagePreviews.value.get(String(m.id))?.replyLoading ?? false,
      inherited: m.inherited,
    }));
    const indexedIds = new Set(markers.map((m) => String(m.id)));
    // Include newly sent questions and older loaded turns beyond the index cap.
    for (const [id, preview] of activeMessagePreviews.value) {
      if (indexedIds.has(id)) continue;
      const localIndex = preview.messageIndex - historyOffset.value;
      const message = activeMessages.value[localIndex];
      if (message?.id == null) continue;
      markers.push({
        ...preview,
        id: message.id,
        index: preview.messageIndex,
        ordinal: 0,
        topPct: total > 1 ? (preview.messageIndex / (total - 1)) * 100 : 0,
        inherited:
          rows[localIndex]?.classList.contains("inherited-row") ?? false,
      });
    }
    scrollMarkers.value = markers
      .sort((a, b) => a.messageIndex - b.messageIndex)
      .map((m, i) => ({ ...m, ordinal: i + 1 }))
      .filter((m) => !m.inherited || !branchHistoryCollapsed.value);
    return;
  }
  // Fallback: measure the loaded rows (marker index failed to load).
  const rows = container.querySelectorAll(".message-row");
  const containerRect = container.getBoundingClientRect();
  const markers: ChatScrollMarker[] = [];
  for (let i = 0; i < activeMessages.value.length; i++) {
    const msg = activeMessages.value[i];
    if (!isUserMessage(msg) || msg.id == null) continue;
    const row = rows[i];
    if (!row) continue;
    const rowRect = row.getBoundingClientRect();
    // Skip rows hidden by the collapsed branch divider (display:none
    // reports a zero rect, which would yield a meaningless position).
    if (rowRect.width === 0 && rowRect.height === 0) continue;
    const offsetTop = rowRect.top - containerRect.top + container.scrollTop;
    markers.push({
      id: msg.id,
      index: -1,
      messageIndex:
        (historyOffsetBySession[currSessionId.value ?? ""] ?? 0) + i,
      ordinal: markers.length + 1,
      topPct: (offsetTop / scrollable) * 100,
      preview: truncate(plainTextFromMessage(msg), 160),
      replyPreview:
        activeMessagePreviews.value.get(String(msg.id))?.replyPreview || "",
      replyLoading:
        activeMessagePreviews.value.get(String(msg.id))?.replyLoading ?? false,
      inherited: row.classList.contains("inherited-row"),
    });
  }
  scrollMarkers.value = markers;
}

function onBranchToggle(collapsed: boolean) {
  readingViewportAnchor = null;
  // The reader is anchoring the divider; follow must not undo the child's
  // position compensation when the content ResizeObserver fires.
  setStickToBottom(currSessionId.value, false);
  viewportInteractionToken += 1;
  pendingViewportSessionId = "";
  endJumpLanding();
  endJumpScrollLock();
  jumpInProgress.value = false;
  branchHistoryCollapsed.value = collapsed;
  nextTick(() => {
    const container = messagesContainer.value;
    if (container) {
      selfScrollTop = container.scrollTop;
      lastMessagesScrollTop = container.scrollTop;
      captureReadingAnchor();
    }
    updateScrollMarkers();
  });
}

function openMessageEdit(message: ChatRecord) {
  messageEditDraft.value = plainTextFromMessage(message);
  editingMessage.value = message;
  nextTick(() => scrollToMessage(message.id));
}

function cancelMessageEdit() {
  editingMessage.value = null;
  messageEditDraft.value = "";
}

async function saveMessageEdit() {
  if (!currSessionId.value || !editingMessage.value) return;
  savingMessageEdit.value = true;
  try {
    const target = editingMessage.value;
    const configId = await inputRef.value?.prepareConfig?.();
    const result = await editMessage(
      currSessionId.value,
      target,
      messageEditDraft.value,
    );
    cancelMessageEdit();

    if (result.needsRegenerate && result.truncatedAfterMessage) {
      const selection = getSelectedProviderSelection();
      continueEditedMessage({
        configId,
        sessionId: currSessionId.value,
        sourceRecord: target,
        enableStreaming: enableStreaming.value,
        selectedProvider: selection?.providerId || "",
        selectedModel: selection?.modelName || "",
        thinkingEffort: getCurrentThinkingEffort(),
      });
      scrollToBottom();
    } else if (result.needsRegenerate) {
      const index = activeMessages.value.findIndex(
        (message) => String(message.id) === String(target.id),
      );
      const nextBot = activeMessages.value
        .slice(index + 1)
        .find((message) => !isUserMessage(message));
      if (nextBot) {
        await handleRegenerateMessage(nextBot);
      }
    }
  } catch (error) {
    console.error("Failed to edit message:", error);
  } finally {
    savingMessageEdit.value = false;
  }
}

async function handleRegenerateMessage(
  message: ChatRecord,
  selection?: RegenerateModelSelection,
) {
  if (!currSessionId.value || isUserMessage(message)) return;
  const effectiveSelection = selection ?? getSelectedProviderSelection();
  try {
    const configId = await inputRef.value?.prepareConfig?.();
    await regenerateMessage(
      currSessionId.value,
      message,
      effectiveSelection?.providerId || "",
      effectiveSelection?.modelName || "",
      enableStreaming.value,
      getCurrentThinkingEffort(),
      configId,
    );
  } catch (error) {
    toast.error(String((error as Error)?.message || tm("batch.requestFailed")));
  }
}

async function handleBranch(message: ChatRecord) {
  const sessionId = currSessionId.value;
  if (!sessionId || message.id == null) return;
  try {
    const response = await chatApi.branchMessage(sessionId, message.id);
    assertCurrentAccount(chatAccountRevision);
    if (response.data?.status !== "ok") {
      toast.error(response.data?.message || tm("branch.failed"));
      return;
    }
    const newSessionId = response.data?.data?.session_id;
    if (!newSessionId) {
      toast.error(tm("branch.failed"));
      return;
    }
    toast.success(tm("branch.success"));
    await getSessions();
    // 2026-08-13 fix: the flat session list excludes project sessions
    // (exclude_project_sessions=True), so a branch created inside a
    // project must refresh that project's session list — otherwise the
    // new branch only exists server-side and never shows in the sidebar.
    const sourceProjectId =
      sessionProjects[sessionId]?.project_id ??
      Object.entries(projectSessionsById.value).find(([, list]) =>
        list.some((s) => s.session_id === sessionId),
      )?.[0] ??
      null;
    if (sourceProjectId) {
      await loadProjectSessions(sourceProjectId);
    }
    await selectSession(newSessionId);
  } catch (error) {
    toast.error(
      isAxiosError(error)
        ? error.response?.data?.message || error.message
        : tm("branch.failed"),
    );
    console.error("Failed to branch session:", error);
  }
}

function handleBotTextSelection(event: MouseEvent, message: ChatRecord) {
  if (message.id == null || String(message.id).startsWith("local-")) return;
  const container = event.currentTarget as HTMLElement | null;
  window.setTimeout(() => {
    const selection = window.getSelection();
    const selectedText = selection?.toString().trim() || "";
    if (!selection || !selectedText) {
      threadSelection.visible = false;
      return;
    }
    if (
      !container ||
      !container.contains(selection.anchorNode) ||
      !container.contains(selection.focusNode)
    ) {
      threadSelection.visible = false;
      return;
    }
    const range = selection.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    threadSelection.message = message;
    threadSelection.selectedText = selectedText;
    threadSelection.left = Math.min(
      window.innerWidth - 180,
      Math.max(12, rect.left + rect.width / 2 - 70),
    );
    threadSelection.top = Math.max(12, rect.top - 42);
    threadSelection.visible = true;
  }, 0);
}

async function createThreadFromSelection() {
  const message = threadSelection.message;
  if (!currSessionId.value || !message?.id || !threadSelection.selectedText)
    return;
  try {
    await openSideChat({ message, selectedText: threadSelection.selectedText });
  } finally {
    threadSelection.visible = false;
  }
}

/**
 * Hide the panel. Side chats persist and can be reopened from the saved menu.
 */
function closeThreadPanel() {
  // Invalidate a pending creation even before its thread ID is known, so a
  // late response cannot reopen a panel the user already dismissed.
  sideChatGeneration += 1;
  creatingSideChat.value = false;
  threadPanelOpen.value = false;
  activeThread.value = null;
  sideChatThreadId.value = null;
}

/** ThreadPanel ✕: hide the panel; the thread stays on disk. */
function onThreadPanelToggle(open: boolean) {
  if (!open) closeThreadPanel();
}

/** Show a side chat, displacing the other right-hand sidebars. */
async function openSideChatPanel(thread: ChatThread) {
  const sessionId = currSessionId.value;
  const generation = sideChatGeneration;
  if (!(await confirmFileNavigation())) return;
  if (currSessionId.value !== sessionId || sideChatGeneration !== generation)
    return;
  chatHeader.SET_WORKSPACE_FILES_OPEN(false);
  reasoningPanelOpen.value = false;
  activeReasoningTarget.value = null;
  refsSidebarOpen.value = false;
  selectedRefs.value = null;
  closeTodoMenu();
  chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
  gitDiffSidebarOpen.value = false;
  sideChatThreadId.value = thread.thread_id;
  activeThread.value = thread;
  threadPanelOpen.value = true;
}

/** Load the persistent side chats of the active main session. */
async function loadSideChats(): Promise<void> {
  const sessionId = currSessionId.value;
  if (!sessionId) {
    sideChats.value = [];
    return;
  }
  loadingSideChats.value = true;
  try {
    const response = await chatApi.listSideThreads(sessionId);
    assertCurrentAccount(chatAccountRevision);
    if (currSessionId.value !== sessionId) return;
    sideChats.value = (response.data?.data?.threads || []) as SideChatEntry[];
  } catch (error) {
    console.error("Failed to load side chats:", error);
  } finally {
    loadingSideChats.value = false;
  }
}

function openSideChatMenu() {
  if (creatingSideChat.value) return;
  sideChatMenuOpen.value = true;
  void loadSideChats();
}

function closeSideChatMenuOnOutsideClick(event: PointerEvent) {
  if (!sideChatMenuOpen.value) return;
  const target = event.target as Element | null;
  if (target?.closest?.(".side-chat-entry")) return;
  sideChatMenuOpen.value = false;
}

onMounted(() => {
  document.addEventListener("pointerdown", closeSideChatMenuOnOutsideClick);
});

onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", closeSideChatMenuOnOutsideClick);
});

/**
 * Create an empty side chat or one quoting a reply and its checkpoint context.
 *
 * Args:
 *   selection: Optional source reply and selected text to continue discussing.
 *
 * Returns:
 *   Resolves after creation is handled or a stale response is discarded.
 */
async function openSideChat(selection?: {
  message: ChatRecord;
  selectedText: string;
}): Promise<void> {
  const sessionId = currSessionId.value;
  if (!sessionId || creatingSideChat.value) return;
  sideChatMenuOpen.value = false;
  const generation = ++sideChatGeneration;
  creatingSideChat.value = true;
  try {
    const response = await chatApi.createThread({
      session_id: sessionId,
      ...(selection
        ? {
            parent_message_id: selection.message.id,
            selected_text: selection.selectedText,
          }
        : { side_chat: true }),
    });
    assertCurrentAccount(chatAccountRevision);
    const thread = response.data?.data as ChatThread | undefined;
    if (
      generation !== sideChatGeneration ||
      sessionId !== currSessionId.value
    ) {
      if (response.data?.status === "ok" && thread) {
        void chatApi.deleteThread(thread.thread_id).catch((error) => {
          console.error("Failed to delete side chat thread:", error);
        });
      }
      return;
    }
    if (response.data?.status !== "ok") {
      toast.error(response.data?.message || tm("thread.createFailed"));
      return;
    }
    if (!thread) {
      toast.error(tm("thread.createFailed"));
      return;
    }
    if (selection) {
      const message = selection.message;
      message.threads = message.threads || [];
      if (
        !message.threads.some((item) => item.thread_id === thread.thread_id)
      ) {
        message.threads.push(thread);
      }
      window.getSelection()?.removeAllRanges();
    }
    await openSideChatPanel(thread);
    void loadSideChats();
  } catch (error) {
    if (generation !== sideChatGeneration || sessionId !== currSessionId.value)
      return;
    toast.error(
      isAxiosError(error)
        ? error.response?.data?.message || error.message
        : tm("thread.createFailed"),
    );
    console.error("Failed to create side chat:", error);
  } finally {
    if (generation === sideChatGeneration) creatingSideChat.value = false;
  }
}

async function openSideChatThread(thread: SideChatEntry) {
  sideChatMenuOpen.value = false;
  await openSideChatPanel(thread);
}

async function openThreadPanel(thread: ChatThread) {
  if (!(await confirmFileNavigation())) return;
  // Opening a saved quote also invalidates any pending side-chat creation.
  closeThreadPanel();
  await openSideChatPanel(thread);
  void loadSideChats();
}

async function openRefsSidebar(refs: unknown) {
  if (!(await confirmFileNavigation())) return;
  chatHeader.SET_WORKSPACE_FILES_OPEN(false);
  closeThreadPanel();
  reasoningPanelOpen.value = false;
  activeReasoningTarget.value = null;
  selectedRefs.value =
    refs && typeof refs === "object" ? (refs as Record<string, unknown>) : null;
  refsSidebarOpen.value = true;
}

async function openReasoningPanel(payload: {
  message: ChatRecord;
  blockIndex: number;
  // 2026-08-11 file-change visibility: optional locate target when the
  // user clicked a file-change chip on the reasoning bar.
  callId?: string;
  // Message-search reveal: keyword to land on when the search hit sits in
  // the thinking text of this block.
  query?: string;
}) {
  if (!(await confirmFileNavigation())) return;
  chatHeader.SET_WORKSPACE_FILES_OPEN(false);
  closeThreadPanel();
  refsSidebarOpen.value = false;
  selectedRefs.value = null;
  closeTodoMenu();
  chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
  gitDiffSidebarOpen.value = false;
  activeReasoningTarget.value = payload;
  reasoningPanelOpen.value = true;
}

/** Status-row workspace capsule: first click opens, second click closes. */
async function toggleGitDiffSidebar(): Promise<void> {
  if (!(await confirmFileNavigation())) return;
  if (gitDiffSidebarOpen.value) {
    gitDiffSidebarOpen.value = false;
    return;
  }
  // Mutual exclusion: close every other sidebar before opening the
  // workbench. Its Agent browser remains available without a code project.
  chatHeader.SET_WORKSPACE_FILES_OPEN(false);
  closeThreadPanel();
  reasoningPanelOpen.value = false;
  activeReasoningTarget.value = null;
  refsSidebarOpen.value = false;
  selectedRefs.value = null;
  closeTodoMenu();
  chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
  const sessionId = currSessionId.value;
  gitDiffSidebarOpen.value = true;
  await nextTick();
  if (!gitDiffSidebarOpen.value || currSessionId.value !== sessionId) return;
  void gitDiffSidebarRef.value?.prepareSessionFiles();
}

// 之前基于 reactive 追踪的 parseTodoToolResult / extractLatestTodoSnapshot
// 已经被 useMessages 层主动 emit 替代(见 useMessages.ts 的 latestTodoSnapshot)。

/** todo 快照按当前会话隔离。
 *
 * useMessages 暴露 `latestTodoSnapshotBySession` 是 ref<Record<sessionId, snapshot>>,
 * 每次 finishToolCall 时整体替换 `value = {...current, [sid]: snap}`,
 * ref.set 100% 触发响应 → 本 computed 重算 → ChatUI 实时刷新。
 *
 * key 可能是 undefined (新会话还没 todo) → 读出 undefined → 转为 null 给 UI。
 */
const currentTodoSnapshot = computed(() => {
  const sid = currSessionId.value;
  if (!sid) return null;
  return latestTodoSnapshotBySession.value[sid] ?? null;
});

// Push the active session's todo snapshot into the app-bar store. The trigger
// and the expandable menu are header concerns now; the store closes the menu
// by itself when the snapshot disappears (todo_clear / every item removed).
watch(
  currentTodoSnapshot,
  (snap) => chatHeader.SET_TODO_SNAPSHOT(snap),
  { immediate: true },
);

// 与 RefsSidebar 互斥: 展开 todo 菜单 / 打开 Goal 抽屉时收起 refs;
// 反向亦然。todo 菜单与 Goal 抽屉互不相干 (浮层 vs 侧栏), 不互斥。
watch(todoMenuOpen, (open) => {
  if (open) {
    if (creatingSideChat.value) closeThreadPanel();
    refsSidebarOpen.value = false;
  }
});
watch(goalSidebarOpen, (open) => {
  if (open) {
    if (creatingSideChat.value) closeThreadPanel();
    refsSidebarOpen.value = false;
  }
});
watch(refsSidebarOpen, (open) => {
  if (open) {
    closeTodoMenu();
    chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
  }
});

// Push the current session's goal state into the header store so the
// app-bar entry button shows a live turns badge while the sidebar is
// closed. The badge disappears when the goal record is gone (never set,
// session switch to a goal-less session, or a live /goal clear), and an
// open sidebar is closed with it — it would only show an empty panel.
watch(
  currentGoal,
  (goal) => {
    chatHeader.SET_GOAL_BADGE(
      goal
        ? {
            status: goal.status,
            turnsUsed: goal.turns_used,
            maxTurns: goal.max_turns,
          }
        : null,
    );
    if (!goal) chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
  },
  { immediate: true },
);

// Mirror the active session's managed shell sessions into the header store
// so the app-bar indicator can badge and list them. An empty list collapses
// to null (indicator hidden).
watch(
  shellSessions,
  (sessions) => {
    chatHeader.SET_SHELL_SESSIONS(sessions.length ? [...sessions] : null);
  },
  { immediate: true },
);

/** RefsSidebar 的 modelValue 变化回调:关闭时由用户主动操作,无需特别处理;
 *  开启时由于 watch 已自动收起 todo,这里只作为占位以保持事件链可读。
 */
function onRefsToggle(open: boolean) {
  if (!open) return;
  // 互斥由 watch(refsSidebarOpen) 自动处理 todo 侧
}

async function deleteThread(thread: ChatThread) {
  if (deletingThread.value) return;
  if (!(await askForConfirmation(tm("thread.confirmDelete"), confirmDialog)))
    return;
  deletingThread.value = true;
  try {
    await chatApi.deleteThread(thread.thread_id);
    removeThreadFromMessages(thread.thread_id);
    if (activeThread.value?.thread_id === thread.thread_id) {
      closeThreadPanel();
    }
    if (sideChats.value.some((item) => item.thread_id === thread.thread_id)) {
      void loadSideChats();
    }
  } catch (error) {
    console.error("Failed to delete thread:", error);
  } finally {
    deletingThread.value = false;
  }
}

function removeThreadFromMessages(threadId: string) {
  for (const message of activeMessages.value) {
    if (!message.threads?.length) continue;
    message.threads = message.threads.filter(
      (thread) => thread.thread_id !== threadId,
    );
  }
}

async function handleFilesSelected(files: FileList | File[]) {
  const selectedFiles = Array.from(files || []);
  for (const file of selectedFiles) {
    if (file.type.startsWith("image/")) {
      await processAndUploadImage(file);
    } else {
      await processAndUploadFile(file);
    }
  }
}

// 2026-08-09 drag-reference (elecvoid243): a file dropped from the
// sidebar file browser (workspace / document manager) onto the chat
// input becomes a *reference*, not an upload. The path is appended to
// the outgoing message at send time (see sendCurrentMessage) so the
// agent reads the file itself. Deduped by path inside the store, so
// repeat drags are no-ops.
function handleSidebarFileDrop(payload: { path: string; name: string }) {
  if (!payload?.path || !payload?.name) return;
  fileReferences.addReference(payload.path, payload.name);
}

function toggleStreaming() {
  enableStreaming.value = !enableStreaming.value;
}

async function startRecording() {
  try {
    await startRecorder();
  } catch (error) {
    console.error("Failed to start recording:", error);
    toast.error(tm("voice.error"));
  }
}

async function stopRecording() {
  try {
    const audioFile = await stopRecorder();
    const uploaded = await processAndUploadFile(audioFile);
    if (!uploaded) {
      toast.error(tm("voice.error"));
    }
  } catch (error) {
    console.error("Failed to stop recording:", error);
    toast.error(tm("voice.error"));
  }
}

/**
 * Hand the viewport to the reader before native scrolling starts.
 *
 * Args:
 *   event: Wheel, touch, scrollbar pointer, or scroll-key gesture on the panel.
 */
function handleMessagesScrollIntent(event: Event) {
  const container = messagesContainer.value;
  if (!container) return;
  // Content clicks do not drag the native scrollbar. Touch starts are handled
  // separately so a finger drag can pause follow before its first scroll event.
  if (event.type === "pointerdown" && event.target !== container) {
    // A content interaction can intentionally collapse or reveal a block.
    readingViewportAnchor = null;
    return;
  }
  let scrollingDown = false;
  if (event.type === "wheel") {
    const wheel = event as WheelEvent;
    if (wheel.deltaY === 0) return;
    scrollingDown = wheel.deltaY > 0;
  } else if (event.type === "keydown") {
    const keyboard = event as KeyboardEvent;
    if (
      ![
        "ArrowUp",
        "ArrowDown",
        "PageUp",
        "PageDown",
        "Home",
        "End",
        " ",
      ].includes(keyboard.key) ||
      keyboard.defaultPrevented ||
      (event.target instanceof HTMLElement &&
        (event.target.closest("input, textarea, select, [contenteditable]") ||
          (keyboard.key === " " && event.target.closest("button, a"))))
    ) {
      return;
    }
    scrollingDown =
      ["ArrowDown", "PageDown", "End"].includes(keyboard.key) ||
      (keyboard.key === " " && !keyboard.shiftKey);
  }
  viewportInteractionToken += 1;
  pendingViewportSessionId = "";
  endJumpLanding();
  endJumpScrollLock();
  jumpInProgress.value = false;
  scrollPointerActive =
    event.type === "pointerdown" || event.type === "touchstart";
  scrollGestureDirection = scrollPointerActive ? 0 : scrollingDown ? 1 : -1;
  lastMessagesScrollTop = Math.max(0, container.scrollTop);
  userScrollPending = true;
  const distance =
    container.scrollHeight - container.scrollTop - container.clientHeight;
  // 2026-10-22 (elecvoid243): follow is deliberately one frame behind the
  // stream — scrollToBottom() coalesces its writes into a rAF — so `distance`
  // is not evidence that the reader left the bottom. Judging the intent by it
  // switched follow off for a wheel that arrived in that gap and re-armed it
  // on the next one within 2px, which read as a stutter while a reply streamed
  // in. A wheel keeps an armed follow armed; only an upward gesture, or a
  // wheel from a viewport that was already scrolled away, clears it.
  const following = shouldStickToBottom(currSessionId.value);
  setStickToBottom(
    currSessionId.value,
    scrollingDown && (following || distance <= 2),
  );
  preserveReadingAnchor();
  window.clearTimeout(userScrollSettleTimer);
  userScrollSettleTimer = window.setTimeout(() => {
    if (!scrollPointerActive) userScrollPending = false;
  }, JUMP_SCROLL_SETTLE_MS);
}

/** Release a scrollbar drag or touch gesture, including releases off-panel. */
function endMessagesScrollGesture() {
  if (!scrollPointerActive) return;
  scrollPointerActive = false;
  const container = messagesContainer.value;
  if (container) {
    const distance =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    // A layout clamp during an upward drag is not a return to the bottom.
    setStickToBottom(
      currSessionId.value,
      distance <= 2 && scrollGestureDirection >= 0,
    );
    preserveReadingAnchor();
  }
  window.clearTimeout(userScrollSettleTimer);
  userScrollSettleTimer = window.setTimeout(() => {
    userScrollPending = false;
  }, JUMP_SCROLL_SETTLE_MS);
}

function handleMessagesScroll() {
  threadSelection.visible = false;
  const container = messagesContainer.value;
  if (!container) return;
  // A switch in flight still renders the outgoing conversation. Its scroll
  // events belong to neither session: recording them would overwrite the
  // incoming session's remembered offset, and the auto-load below would page
  // older history for the incoming session from the outgoing position.
  if (pendingViewportSessionId) return;
  const sessionId = currSessionId.value;
  const maxScrollTop = Math.max(
    0,
    container.scrollHeight - container.clientHeight,
  );
  const top = Math.max(0, container.scrollTop);
  // One read the handler already needs: keeps the return-to-tail button in step
  // with every scroll, including the clamps a shrinking document produces.
  distanceToBottom.value = Math.max(0, maxScrollTop - top);
  // Shrinking content can clamp scrollTop without the reader moving at all.
  const previousTop = Math.min(lastMessagesScrollTop, maxScrollTop);
  const movedUp = top < previousTop - 1;
  const movedDown = top > previousTop + 1;
  const isSelfScroll =
    Math.abs(top - selfScrollTop) <= 1 &&
    Math.abs(top - lastMessagesScrollTop) <= 1;
  // A clamp can arrive after content has regrown to or beyond its old height.
  // A queued follow covers that intermediate layout change even when the old
  // bottom fits again. Explicit gestures still take ownership before scrolling.
  const followLayoutPending =
    shouldStickToBottom(sessionId) &&
    !userScrollPending &&
    (selfScrollTop > maxScrollTop + 1 || bottomScrollFrame !== 0);
  if (followLayoutPending) scrollToBottom();
  if (!isSelfScroll && !followLayoutPending) {
    if ((movedUp || movedDown) && jumpLandingContainer) {
      // A native scroll without a preceding gesture must also cancel a landing.
      viewportInteractionToken += 1;
      endJumpLanding();
      endJumpScrollLock();
      jumpInProgress.value = false;
    }
    // Scroll events are the fallback for inputs that deliver no wheel/pointer
    // event. Explicit wheel/key direction remains authoritative during layout.
    if (scrollPointerActive || !userScrollPending) {
      if (movedUp) scrollGestureDirection = -1;
      else if (movedDown) scrollGestureDirection = 1;
    }
    if (movedUp) {
      setStickToBottom(sessionId, false);
    } else if (
      movedDown &&
      maxScrollTop - top <= 2 &&
      scrollGestureDirection > 0 &&
      !scrollPointerActive
    ) {
      setStickToBottom(sessionId, true);
    }
  }
  lastMessagesScrollTop = top;
  if (!shouldStickToBottom(sessionId)) {
    preserveReadingAnchor();
    if (!isSelfScroll && (movedUp || movedDown)) captureReadingAnchor();
  }
  if (userScrollPending) {
    window.clearTimeout(userScrollSettleTimer);
    userScrollSettleTimer = window.setTimeout(() => {
      if (!scrollPointerActive) userScrollPending = false;
    }, JUMP_SCROLL_SETTLE_MS);
  }
  // Keep the conversation's anchor fresh so switching back restores it instead
  // of the neighbouring session's offset. The rect reads share the layout pass
  // this handler already forces above.
  if (sessionId) recordViewportAnchor(sessionId);
  // History windowing: reached the top boundary while older history exists
  // → load the next page (the loadOlderHistory anchor keeps the viewport).
  // Suppressed while a programmatic jump scrolls (see suppressHistoryAutoLoad).
  //
  // 2026-10-22 (elecvoid243): one automatic page per entry into the band. The
  // trigger is a position, not an intent: a held scrollbar thumb keeps
  // reporting scrollTop < 80 for every pointer-driven event, so each page was
  // requested again the moment its anchor write was clamped back to the top —
  // pages chained and the panel bounced by a page height each round. The arm
  // flag is cleared when a page starts and only restored after the reader has
  // moved clear of the band (hysteresis), so an explicit second tier (the
  // load-older button) remains the only way to page from a parked viewport.
  const paging = currSessionId.value
    ? historyPagingBySession[currSessionId.value]
    : null;
  if (paging?.autoLoadArmed === false && container.scrollTop > 160) {
    paging.autoLoadArmed = true;
  }
  if (
    container.scrollTop < 80 &&
    !shouldStickToBottom(sessionId) &&
    !suppressHistoryAutoLoad.value &&
    paging?.hasMore &&
    !paging.loadingOlder &&
    paging?.autoLoadArmed !== false &&
    // A failed page stays paused until the user retries (see
    // loadOlderMessages) — otherwise every scroll event re-fires it.
    !paging.error
  ) {
    void loadOlderHistory();
  }
}

/**
 * Capture a visible reading point for subsequent layout corrections.
 */
function captureReadingAnchor() {
  readingViewportAnchor = null;
  const container = messagesContainer.value;
  if (
    !container ||
    !props.active ||
    pendingViewportSessionId ||
    jumpLandingContainer ||
    shouldStickToBottom(currSessionId.value)
  )
    return;
  const panelRect = container.getBoundingClientRect();
  const y = panelRect.top + container.clientHeight / 3;
  const caret = document.caretRangeFromPoint?.(
    panelRect.left + container.clientWidth / 2,
    y,
  );
  if (
    caret?.startContainer.nodeType === Node.TEXT_NODE &&
    container.contains(caret.startContainer)
  ) {
    const text = caret.startContainer as Text;
    // Inner code/media panes own their own scrolling. Anchor their box so
    // scrolling inside one cannot be mistaken for a main-panel layout shift.
    let owner = text.parentElement;
    while (owner && owner !== container) {
      if (/^(auto|scroll)$/.test(getComputedStyle(owner).overflowY)) {
        const rect = owner.getBoundingClientRect();
        if (
          rect.height <= 0 ||
          rect.bottom <= panelRect.top ||
          rect.top >= panelRect.bottom
        )
          break;
        readingViewportAnchor = {
          target: owner,
          offset: 0,
          top: rect.top,
          scrollTop: container.scrollTop,
          container,
          sessionToken: viewportSessionToken,
        };
        return;
      }
      owner = owner.parentElement;
    }
    const offset = Math.min(caret.startOffset, text.length - 1);
    if (offset >= 0) {
      caret.setStart(text, offset);
      caret.setEnd(text, offset + 1);
      const rect = caret.getBoundingClientRect();
      if (
        rect.height > 0 &&
        rect.bottom > panelRect.top &&
        rect.top < panelRect.bottom
      ) {
        readingViewportAnchor = {
          target: text,
          offset,
          top: rect.top,
          scrollTop: container.scrollTop,
          container,
          sessionToken: viewportSessionToken,
        };
        return;
      }
    }
  }
  const row = Array.from(
    container.querySelectorAll<HTMLElement>(".message-row"),
  ).find((row) => {
    const rect = row.getBoundingClientRect();
    return rect.height > 0 && rect.bottom > y && rect.top < panelRect.bottom;
  });
  if (row)
    readingViewportAnchor = {
      target: row,
      offset: 0,
      top: row.getBoundingClientRect().top,
      scrollTop: container.scrollTop,
      container,
      sessionToken: viewportSessionToken,
    };
}

/** Preserve the reading point while accounting for the reader's own scrolling. */
function preserveReadingAnchor() {
  const container = messagesContainer.value;
  if (
    !container ||
    !props.active ||
    pendingViewportSessionId ||
    jumpLandingContainer ||
    shouldStickToBottom(currSessionId.value)
  ) {
    readingViewportAnchor = null;
    return;
  }
  // All callers must yield to a native drag, including scroll events and
  // completed history requests, not just the layout observers.
  if (scrollPointerActive) return;
  const anchor = readingViewportAnchor;
  if (
    !anchor ||
    anchor.container !== container ||
    anchor.sessionToken !== viewportSessionToken ||
    !anchor.target.isConnected
  ) {
    captureReadingAnchor();
    return;
  }
  let rect: DOMRect;
  if (anchor.target instanceof Text) {
    if (!anchor.target.length) {
      captureReadingAnchor();
      return;
    }
    const range = document.createRange();
    const offset = Math.min(anchor.offset, anchor.target.length - 1);
    range.setStart(anchor.target, offset);
    range.setEnd(anchor.target, offset + 1);
    rect = range.getBoundingClientRect();
  } else rect = anchor.target.getBoundingClientRect();
  // A folded card hides its body with display:none, and an anchor left inside
  // it reports an all-zero rect. Measuring from a point that is no longer
  // rendered can pull the panel to the very top of the loaded history (a small
  // remaining range turns the computed offset negative and it clamps to 0),
  // so drop the stale anchor and pick a visible one instead.
  if (rect.width === 0 && rect.height === 0) {
    captureReadingAnchor();
    return;
  }
  const top = Math.max(0, container.scrollTop);
  const max = Math.max(0, container.scrollHeight - container.clientHeight);
  // Exclude range clamping from user movement. Layout displacement and the
  // reader's scroll can happen in the same frame and must remain independent.
  const expectedTop = anchor.top - (top - Math.min(anchor.scrollTop, max));
  const idealTop = top + rect.top - expectedTop;
  const target = Math.max(0, Math.min(max, idealTop));
  if (Math.abs(target - top) > 0.5) {
    container.scrollTop = target;
    selfScrollTop = container.scrollTop;
    lastMessagesScrollTop = container.scrollTop;
  }
  // Store the clamped result so an impossible offset does not cause a loop.
  anchor.top =
    target === idealTop ? expectedTop : rect.top - (container.scrollTop - top);
  anchor.scrollTop = container.scrollTop;
}

/**
 * Prepend older history while keeping the latest visible reading point.
 *
 * Args:
 *   retry: Retry a failed page when the reader uses the load-older button.
 */
async function loadOlderHistory(retry = false) {
  const sessionId = currSessionId.value;
  if (!sessionId || historyPagingBySession[sessionId]?.loadingOlder) return;
  // Disarmed before the fetch, not after it: scroll events keep arriving while
  // the page is in flight, and the automatic path must not queue a second one.
  // An explicit retry (the button) never disarms — the reader asked for it.
  if (!retry) {
    const paging = historyPagingBySession[sessionId];
    if (paging) paging.autoLoadArmed = false;
  }
  const sessionToken = viewportSessionToken;
  const container = messagesContainer.value;
  if (retry) {
    setStickToBottom(sessionId, false);
    captureReadingAnchor();
  }
  await loadOlderMessages(sessionId, {
    retry,
    beforePrepend: () => {
      if (
        sessionToken === viewportSessionToken &&
        container === messagesContainer.value
      )
        captureReadingAnchor();
    },
  });
  await nextTick();
  if (
    sessionToken === viewportSessionToken &&
    container === messagesContainer.value
  )
    preserveReadingAnchor();
}

/**
 * Stick-to-bottom intent of a conversation.
 *
 * Keyed per session because the messages panel is a single DOM node shared by
 * every conversation: one global flag leaked the scrolled-up state of the
 * session being left into the session being opened, which made
 * scrollToBottom() bail out while switching back.
 *
 * @param sessionId: Conversation whose intent is queried.
 * @returns Whether live updates should keep snapping this one to the bottom.
 */
function shouldStickToBottom(sessionId: string): boolean {
  return shouldStickToBottomBySession.get(sessionId) ?? true;
}

/**
 * Record the stick-to-bottom intent of a conversation.
 *
 * @param sessionId: Conversation the intent belongs to.
 * @param stick: New intent value.
 */
function setStickToBottom(sessionId: string, stick: boolean): void {
  if (sessionId) shouldStickToBottomBySession.set(sessionId, stick);
  // The map is the source of truth; the button reads the mirrored ref for the
  // active conversation. Reading it back keeps the mirror correct even when
  // this is called for a session that is not the one on screen.
  followActive.value = shouldStickToBottom(currSessionId.value);
}

/**
 * Refresh the reactive distance to the tail used by the jump-to-bottom button.
 *
 * The scroll controller keeps its own non-reactive bookkeeping; the button is
 * template state, so the distance needs a value Vue can watch. Content can grow
 * or shrink without a scroll event (async markdown, media decode, a prepend), so
 * both layout observers refresh it as well.
 */
function syncDistanceToBottom(): void {
  const container = messagesContainer.value;
  distanceToBottom.value = container
    ? Math.max(
        0,
        container.scrollHeight - container.scrollTop - container.clientHeight,
      )
    : 0;
}

/**
 * Return the viewport to the newest message and follow what comes next.
 *
 * The affordance exists for one intent: the reader scrolled back through a long
 * (or still streaming) conversation and wants the live tail without dragging the
 * scrollbar. Follow is re-armed first, so once the landing settles the stream
 * keeps the panel at the bottom; the travel itself uses the shared landing,
 * which retargets while content settles and yields to any interrupting gesture.
 */
function jumpToBottom() {
  const sessionId = currSessionId.value;
  const container = messagesContainer.value;
  if (!sessionId || !container) return;
  viewportInteractionToken += 1;
  endJumpScrollLock();
  jumpInProgress.value = false;
  setStickToBottom(sessionId, true);
  runJumpLanding(
    (panel) => Math.max(0, panel.scrollHeight - panel.clientHeight),
    true,
  );
}

function scrollToBottom() {
  if (
    bottomScrollFrame ||
    !props.active ||
    !shouldStickToBottom(currSessionId.value)
  ) {
    return;
  }
  const sessionId = currSessionId.value;
  const sessionToken = viewportSessionToken;
  // Vue flushes pending DOM updates before the frame. Stream callbacks, message
  // watches and ResizeObserver notifications share this single follow write.
  bottomScrollFrame = requestAnimationFrame(() => {
    bottomScrollFrame = 0;
    const container = messagesContainer.value;
    if (
      !container ||
      !props.active ||
      sessionToken !== viewportSessionToken ||
      pendingViewportSessionId ||
      jumpInProgress.value ||
      jumpLandingContainer ||
      !shouldStickToBottom(sessionId)
    ) {
      return;
    }
    const target = Math.max(0, container.scrollHeight - container.clientHeight);
    if (Math.abs(container.scrollTop - target) > 1)
      container.scrollTop = target;
    // Read back: the write is clamped to the scrollable range and the echo this
    // later compares against is the clamped value (see the declaration note).
    selfScrollTop = container.scrollTop;
    lastMessagesScrollTop = container.scrollTop;
    // Hide the return-to-tail button in the same frame as the snap instead of
    // waiting for the scroll echo, which is coalesced into a later task.
    distanceToBottom.value = Math.max(
      0,
      container.scrollHeight - container.scrollTop - container.clientHeight,
    );
  });
}

/**
 * Remember where a conversation is being left: the row under the middle of the
 * viewport, and how far into that row the middle sat.
 *
 * Not a bare scrollTop, because the panel resets to the newest history window on
 * every open — a scrollTop measured before a switch does not describe the same
 * content when the session is reopened (see the anchor note at the
 * declaration). `restoreSessionViewport` replays both parts, which re-pages the
 * history the fresh window dropped and keeps the exact line the reader left.
 *
 * @param sessionId: Conversation whose viewport is being recorded.
 */
function recordViewportAnchor(sessionId: string) {
  const container = messagesContainer.value;
  if (!sessionId || !container) return;
  const center =
    container.getBoundingClientRect().top + container.clientHeight / 2;
  for (const row of container.querySelectorAll<HTMLElement>(".message-row")) {
    const rect = row.getBoundingClientRect();
    // A collapsed branch row reports a zero rect: it cannot anchor anything.
    if (rect.height <= 0) continue;
    if (rect.bottom < center) continue;
    const index = Number(row.dataset.messageIndex);
    if (!Number.isFinite(index)) continue;
    viewportAnchorBySession.set(sessionId, {
      index,
      id: row.dataset.messageId || null,
      // Negative when the centre fell in the gap above this row; capping at
      // the row's height keeps a measurement taken mid-reflow sane.
      offset: Math.min(rect.height, center - rect.top),
    });
    return;
  }
  // Nothing addressable on screen (empty session, or every row hidden).
  viewportAnchorBySession.delete(sessionId);
}

/**
 * Re-apply the incoming conversation's anchor while its window is settling.
 *
 * A switch paints several intermediate windows: the cached one, the refreshed
 * newest page, then whatever older pages the restore has to fetch. The browser
 * clamps scrollTop on each of them, so without this the reader sees the wrong
 * place first and a jump afterwards. Bottom-following conversations are held
 * at their tail; the rest at their remembered row. Called from the layout
 * observers (before paint) and once directly when the session id flips; the
 * landing started by restoreSessionViewport() takes over from the first frame
 * it owns.
 *
 * @param sessionId: Conversation whose viewport is still being installed.
 */
function installPendingViewport(sessionId: string) {
  if (
    sessionId !== currSessionId.value ||
    pendingViewportSessionId !== sessionId ||
    jumpInProgress.value ||
    jumpLandingContainer
  ) {
    return;
  }
  const container = messagesContainer.value;
  if (!container) return;
  const max = Math.max(0, container.scrollHeight - container.clientHeight);
  let target: number;
  if (shouldStickToBottom(sessionId)) {
    // The conversation was left at its tail: hold the newest message on
    // screen while the cached and then the refreshed window replace each
    // other, instead of exposing the outgoing session's offset.
    target = max;
  } else {
    const anchor = viewportAnchorBySession.get(sessionId);
    if (!anchor || anchor.offset == null) return;
    const row = findAnchorRow(container, anchor);
    if (!row) return;
    target = Math.min(max, anchorScrollTopFor(container, row, anchor.offset));
  }
  target = Math.max(0, target);
  if (Math.abs(container.scrollTop - target) <= 0.5) return;
  container.scrollTop = target;
  selfScrollTop = container.scrollTop;
  lastMessagesScrollTop = container.scrollTop;
}

/**
 * Install the viewport of the conversation that just became active.
 *
 * The messages panel keeps its DOM node across a switch, so the browser
 * preserves the outgoing session's scrollTop and the incoming one would
 * silently open at that offset. A session that was following the bottom
 * reopens at the bottom; otherwise the remembered point inside its anchor row
 * is replayed through the jump path, which re-pages the history the fresh
 * window dropped and holds the row in place while the rows around it settle.
 *
 * Both landings are deliberately unanimated: this restores a viewport rather
 * than travelling somewhere the user asked for, so it should be over as soon
 * as the switch is.
 *
 * Args:
 *   sessionId: Conversation just loaded into the messages panel.
 *   sessionToken: Generation of the selection that requested this restore.
 */
async function restoreSessionViewport(
  sessionId: string,
  sessionToken = viewportSessionToken,
) {
  if (
    currSessionId.value !== sessionId ||
    sessionToken !== viewportSessionToken
  ) {
    return;
  }
  const interactionToken = viewportInteractionToken;
  try {
    // A conversation that is producing output has no useful remembered
    // position: what the user came back for is the reply being written, and
    // the in-flight record is appended at the end of the list. Restoring an
    // anchor taken further up left the viewport parked in history while the
    // output streamed in below the fold — the "shows only history, no live
    // message" report.
    const running =
      isSessionRunning(sessionId) || hasLiveSystemRecord(sessionId);
    const anchor = viewportAnchorBySession.get(sessionId);
    if (shouldStickToBottom(sessionId) || anchor == null || running) {
      // No memory (never scrolled, or nothing addressable was on screen):
      // opening at the newest message is the only sane default, and it still
      // beats inheriting the leftover offset of the previous conversation.
      await nextTick();
      if (
        sessionToken !== viewportSessionToken ||
        interactionToken !== viewportInteractionToken
      ) {
        return;
      }
      // Landing at the bottom means following it. Without re-arming the intent
      // a session whose follow was switched off earlier (a marker jump, a
      // scroll up before the switch) would open at the bottom and then let the
      // output grow straight back out of view.
      setStickToBottom(sessionId, true);
      endJumpLanding();
      scrollToBottom();
      return;
    }
    await jumpToViewport(anchor, false);
  } finally {
    // Released last, so the jump above runs while the panel still holds
    // pre-restore geometry, and only if this restore still owns the gate: a
    // newer switch may have taken it over while this one was paging.
    if (
      sessionToken === viewportSessionToken &&
      pendingViewportSessionId === sessionId
    ) {
      pendingViewportSessionId = "";
    }
  }
}

async function focusChatInput() {
  await nextTick();
  window.requestAnimationFrame(() => {
    inputRef.value?.focusInput();
  });
}

async function openLiveMode() {
  try {
    if (!currSessionId.value) await newSession();
    await nextTick();
    const sessionId = currSessionId.value;
    const configId = await inputRef.value?.prepareConfig?.();
    assertCurrentAccount(chatAccountRevision);
    if (!configId || sessionId !== currSessionId.value) {
      throw new Error("Chat session changed");
    }
    const selection = getSelectedProviderSelection();
    liveSelection.value = {
      sessionId,
      configId,
      selectedProvider: selection?.providerId,
      selectedModel: selection?.modelName,
      thinkingEffort: getCurrentThinkingEffort(),
    };
    liveModeOpen.value = true;
  } catch (error) {
    console.error("Failed to open live chat:", error);
  }
}

async function stopCurrentSession() {
  if (!currSessionId.value) return;
  try {
    await stopSession(currSessionId.value);
  } catch (error) {
    console.error("Failed to stop session:", error);
  }
}

// ── Pending follow-up queue (ZCode-style, 2026-09-01) ──────────────
// While an agent run is active, text messages are held locally above
// the input (usePendingFollowUps) instead of being dispatched. Dispatch
// is explicit (2026-09-17 — the tool-call-boundary auto-flush was
// dropped because it made the pending card flash by unread):
// 1. The "追加" button on a pending card — dispatch the whole queue now;
//    the backend captures the messages into the running turn and injects
//    them at the end of its next tool call (the legacy follow-up timing).
//    The composer's Enter key does the same when the input is empty, but
//    confirms only the OLDEST message (see flushOldestPendingFollowUp).
// 2. The "强制打断" button on a pending card — stop the active run, then
//    dispatch; Ctrl/Cmd+Enter on an empty composer does the same.
// 3. onStreamEnd — the run ended with items still queued; they start
//    new runs as normal messages (mirrors how the backend activates
//    unconsumed follow-up tickets), so a message is never stranded.
const pendingFollowUps = usePendingFollowUps();
const flushingFollowUps = new Set<string>();

/** Dispatch the queued follow-ups as normal messages. The backend
    captures them into the active run (injected at the end of its next
    tool call) when one is running, and starts fresh runs otherwise. */
async function flushPendingFollowUps(sessionId: string) {
  if (!sessionId || flushingFollowUps.has(sessionId)) return;
  const items = [...pendingFollowUps.itemsFor(sessionId)];
  if (!items.length) return;
  flushingFollowUps.add(sessionId);
  try {
    for (const item of items) {
      await sendQueuedFollowUp(sessionId, item.text);
      pendingFollowUps.remove(sessionId, item.id);
    }
  } catch (error) {
    console.error("Failed to send queued follow-up:", error);
  } finally {
    flushingFollowUps.delete(sessionId);
  }
}

/** "Force interrupt" card action: interrupt the active run and
    immediately dispatch the queued follow-ups as fresh requests
    (equivalent to /stop + resend). */
async function flushPendingFollowUpsNow(sessionId: string | null) {
  if (!sessionId) return;
  const items = [...pendingFollowUps.itemsFor(sessionId)];
  if (!items.length) return;
  flushingFollowUps.add(sessionId);
  try {
    if (isSessionRunning(sessionId)) {
      try {
        await stopSession(sessionId);
      } catch (error) {
        console.error("Failed to stop session:", error);
      }
      // Wait briefly for the aborted stream to tear down so the queued
      // messages start a clean new run instead of racing the dying
      // runner's capture window.
      const deadline = Date.now() + 3000;
      while (isSessionRunning(sessionId) && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
    }
    for (const item of items) {
      await sendQueuedFollowUp(sessionId, item.text);
      pendingFollowUps.remove(sessionId, item.id);
    }
  } catch (error) {
    console.error("Failed to send queued follow-up:", error);
  } finally {
    flushingFollowUps.delete(sessionId);
  }
}

async function sendQueuedFollowUp(sessionId: string, text: string) {
  if (chatAccountRevision !== accountSessionRevision.value || !text.trim())
    return;
  const currentTarget = sessionId === currSessionId.value;
  const configId = currentTarget
    ? await inputRef.value?.prepareConfig?.()
    : undefined;
  assertCurrentAccount(chatAccountRevision);
  if (currentTarget && (sessionId !== currSessionId.value || !configId)) {
    throw new Error("Chat session changed");
  }
  const messageId = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const outgoingParts = isOrdinaryUser.value
    ? [{ type: "plain", text }]
    : buildOutgoingParts(text);
  const selection = currentTarget ? getSelectedProviderSelection() : null;
  const { userRecord, botRecord } = createLocalExchange({
    sessionId,
    messageId,
    parts: outgoingParts,
  });
  if (!isOrdinaryUser.value) updateTitleFromText(sessionId, text);
  sendMessageStream({
    sessionId,
    messageId,
    configId,
    parts: outgoingParts,
    transport: transportMode.value,
    enableStreaming: enableStreaming.value,
    selectedProvider: selection?.providerId || "",
    selectedModel: selection?.modelName || "",
    thinkingEffort: currentTarget ? getCurrentThinkingEffort() : undefined,
    userRecord,
    botRecord,
  });
}

/** "Send" card action: hand the queued follow-ups to the running turn
    right away (see the queue comment above for the landing rules). */
function flushPendingFromInput() {
  void flushPendingFollowUps(currSessionId.value);
}

/**
 * Composer Enter shortcut: confirm the OLDEST queued follow-up only and
 * leave the rest queued, so repeated Enter presses walk the queue one
 * message at a time. Anything newer stays visible in the card list.
 *
 * Args:
 *   sessionId: Session owning the queue; ignored when null.
 */
async function flushOldestPendingFollowUp(sessionId: string | null) {
  if (!sessionId || flushingFollowUps.has(sessionId)) return;
  // The store keeps items in arrival order, so index 0 is the oldest.
  const oldest = pendingFollowUps.itemsFor(sessionId)[0];
  if (!oldest) return;
  flushingFollowUps.add(sessionId);
  try {
    await sendQueuedFollowUp(sessionId, oldest.text);
    pendingFollowUps.remove(sessionId, oldest.id);
  } catch (error) {
    console.error("Failed to send queued follow-up:", error);
  } finally {
    flushingFollowUps.delete(sessionId);
  }
}

function flushOldestPendingFromInput() {
  void flushOldestPendingFollowUp(currSessionId.value);
}

/** "Force interrupt" card action: stop the run, then resend. */
function interruptPendingFromInput() {
  void flushPendingFollowUpsNow(currSessionId.value);
}

function toggleTheme() {
  customizer.SET_UI_THEME(isDark.value ? "PurpleTheme" : "PurpleThemeDark");
}
</script>

<style scoped>
.chat-ui {
  --chat-panel-top-offset: 50px;
  --chat-sidebar-bg: rgb(var(--v-theme-surface));
  --chat-session-active-bg: #efefef;
  --chat-page-bg: #fdfcfc;
  --chat-border: #f2f2f2;
  --chat-muted: rgba(var(--v-theme-on-surface), 0.62);
  --chat-section-label: rgba(var(--v-theme-on-surface), 0.48);
  --chat-content-width: 80%;
  /* 2026-10-03 widen-chat-column (elecvoid243): raised the chat column
       from 76% / 860 px to 80% / 960 px. This shell var — not the inner
       bubble cap — is the real bottleneck: .messages-list-shell and the
       input box both consume it, so widening only .from-bot
       .message-stack has no visible effect on the chat bar. 960 px keeps
       the outer shell aligned with the inner bubble target so long
       assistant replies (code blocks, tables) get the extra room. */
  --chat-content-max-width: 960px;
  display: flex;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--chat-page-bg);
  color: rgb(var(--v-theme-on-surface));
  font-family:
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    Roboto,
    Oxygen,
    Ubuntu,
    Cantarell,
    "Open Sans",
    "Helvetica Neue",
    sans-serif;
}

.chat-ui.is-dark {
  --chat-sidebar-bg: #2d2d2d;
  --chat-session-active-bg: rgba(255, 255, 255, 0.08);
  --chat-page-bg: rgb(var(--v-theme-background));
  --chat-border: rgba(255, 255, 255, 0.1);
  --chat-section-label: rgba(255, 255, 255, 0.5);
}

/* 2026-07-21 chatui sidebar resize (elecvoid243): replaced the
   Vuetify v-navigation-drawer wrapper with a plain <aside> so the
   width can be set via inline style. Desktop is a flex item in
   .chat-ui; mobile switches to position: fixed drawer (see
   media query below). */
.chat-sidebar {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
  height: 100%;
  background: var(--chat-sidebar-bg);
  border-right: 1px solid var(--chat-border);
  /* 2026-07-21 chatui toolbar align (elecvoid243): explicit
     border-box so the 1px right border consumes from `width`
     instead of extending past it. Without this, the sidebar's
     visual extent is `width + 1`, mismatching VerticalHeader's
     `chatHeaderStyle` (which positions the v-app-bar at exactly
     `chatSidebarWidth`). Matches .chat-sidebar.collapsed below. */
  box-sizing: border-box;
  /* 拖拽时关闭过渡, 否则 width 跟不上鼠标; 非拖拽时给 0.18s 平滑过渡。 */
  transition: width 0.18s ease;
  will-change: width;
}

.chat-sidebar.is-resizing {
  transition: none;
  user-select: none;
}

.chat-sidebar.collapsed {
  background: var(--chat-sidebar-bg);
  border-right: 1px solid var(--chat-border);
}

/* 拖拽手柄: 6px 宽, 绝对定位在 aside 右边缘。 */
.chat-sidebar-resizer {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 6px;
  margin-right: -3px;
  cursor: ew-resize;
  z-index: 10;
  background: transparent;
  transition: background 0.15s ease;
}

.chat-sidebar-resizer:hover,
.chat-sidebar-resizer:active {
  background: rgba(var(--v-theme-primary), 0.2);
}

/* 移动端: 抽屉 + 遮罩 */
.chat-sidebar-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.42);
  z-index: 1099;
  animation: chat-sidebar-fade-in 0.18s ease;
}

.chat-sidebar.is-mobile-drawer {
  position: fixed;
  top: 0;
  left: 0;
  height: 100vh;
  z-index: 1100;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  animation: chat-sidebar-slide-in 0.22s ease;
}

/* 移动端关闭时: 直接 display:none, 不占布局位置。
   桌面端不应用此 class, 因此不受影响。 */
.chat-sidebar.is-mobile-drawer-closed {
  display: none;
}

@keyframes chat-sidebar-fade-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@keyframes chat-sidebar-slide-in {
  from {
    transform: translateX(-100%);
  }
  to {
    transform: translateX(0);
  }
}

.sidebar-top {
  padding: 0 16px 2px;
}

.chat-sidebar.collapsed .sidebar-top {
  width: 56px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 0 0 2px;
}

.chat-sidebar-brand {
  min-height: 50px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 0 10px 2px;
}

.chat-sidebar-brand.collapsed {
  width: 36px;
  justify-content: center;
  padding: 0 0 2px;
}

.chat-sidebar-brand-title {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  color: rgb(var(--v-theme-on-surface));
  line-height: 1.05;
}

.chat-sidebar-brand-logo {
  width: 22px;
  height: 22px;
  flex: 0 0 22px;
  display: block;
}

.chat-sidebar-brand-title .chat-sidebar-brand-logo {
  transform: translateX(-2px);
}

.chat-sidebar-brand-copy {
  min-width: 0;
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
}

.chat-sidebar-brand-name {
  font-size: 18px;
  font-weight: 800;
}

.chat-sidebar-brand-mode {
  color: var(--chat-muted);
  font-size: 18px;
  font-weight: 500;
}

.chat-sidebar-brand-toggle {
  width: 36px;
  height: 36px;
  min-width: 36px;
  color: var(--chat-muted);
}

.chat-sidebar-rail-btn {
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  cursor: pointer;
  display: grid;
  place-items: center;
  padding: 0;
}

.chat-sidebar-brand-toggle:hover {
  background: var(--chat-session-active-bg);
  color: rgb(var(--v-theme-on-surface));
}

.chat-sidebar-rail-icon-stack {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
}

.chat-sidebar-rail-icon-stack > * {
  grid-area: 1 / 1;
}

.chat-sidebar-brand-logo--collapsed {
  width: 20px;
  height: 20px;
  transition:
    opacity 0.14s ease,
    visibility 0.14s ease;
  opacity: 1;
  visibility: visible;
}

.chat-sidebar-rail-icon-stack .sidebar-panel-toggle-icon {
  transition:
    opacity 0.14s ease,
    visibility 0.14s ease;
  opacity: 0;
  visibility: hidden;
}

.chat-sidebar-brand-toggle:hover .chat-sidebar-brand-logo--collapsed,
.chat-sidebar-brand-toggle:focus-visible .chat-sidebar-brand-logo--collapsed {
  opacity: 0;
  visibility: hidden;
}

.chat-sidebar-brand-toggle:hover .sidebar-panel-toggle-icon,
.chat-sidebar-brand-toggle:focus-visible .sidebar-panel-toggle-icon {
  opacity: 1;
  visibility: visible;
}

.sidebar-panel-toggle-icon {
  flex: 0 0 auto;
}

.new-chat-btn,
.settings-btn {
  color: rgb(var(--v-theme-on-surface));
  border-radius: 8px;
}

.sidebar-action-icon {
  color: currentcolor;
  flex: 0 0 auto;
  stroke-width: 2;
}

.new-chat-btn:not(.icon-only) .sidebar-action-icon {
  margin-right: 12px !important;
}

.new-chat-btn,
.settings-btn {
  width: 100%;
  min-height: 36px;
  height: 36px;
  justify-content: flex-start;
  border-radius: 8px;
  text-transform: none;
  letter-spacing: 0;
  font-size: 14px;
  font-weight: 500;
}

.sidebar-provider-btn {
  margin-bottom: 2px;
}

.new-chat-btn:not(.icon-only),
.settings-btn:not(.icon-only) {
  padding-inline: 10px;
}

.new-chat-btn.icon-only,
.settings-btn.icon-only {
  width: 36px !important;
  height: 36px !important;
  min-width: 36px !important;
  margin-inline: auto;
  padding: 0 !important;
  justify-content: center;
}

.chat-sidebar.collapsed .new-chat-btn.icon-only :deep(.v-btn__content),
.chat-sidebar.collapsed .settings-btn.icon-only :deep(.v-btn__content) {
  display: flex;
  align-items: center;
  justify-content: center;
}

/* 2026-08-13 session archive (elecvoid243): footer toggle + archive
   section styles. */
.archive-toggle-btn {
  width: 100%;
  min-height: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  padding-inline: 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-size: 14px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  box-sizing: border-box;
}

.archive-toggle-btn .sidebar-action-icon {
  margin-right: 12px;
}

.archive-toggle-btn.icon-only {
  width: 36px;
  min-width: 36px;
  padding: 0;
  justify-content: center;
  margin-inline: auto;
}

.archive-toggle-btn.icon-only .sidebar-action-icon {
  margin-right: 0;
}

.archive-toggle-btn:hover,
.archive-toggle-btn.active {
  background: var(--chat-session-active-bg);
}

.new-chat-btn :deep(.v-btn__content),
.settings-btn :deep(.v-btn__content) {
  min-width: 0;
  font-size: 14px;
  line-height: 20px;
}

.chat-sidebar.collapsed .sidebar-footer {
  display: flex;
  justify-content: center;
}

.new-chat-btn:hover,
.settings-btn:hover {
  background: var(--chat-session-active-bg);
}

.sidebar-workspace-btn--active {
  background: var(--chat-session-active-bg);
  color: rgb(var(--v-theme-on-surface));
}

.sidebar-content {
  flex: 1;
  overflow-y: auto;
  padding: 2px 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sidebar-section {
  flex: 0 0 auto;
}

.sidebar-section-header {
  min-height: 24px;
  display: flex;
  align-items: center;
  padding: 0 10px 4px;
  color: var(--chat-section-label);
  font-size: 12px;
  font-weight: 500;
}

.session-list {
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* 2026-08-13 session drag: the unsorted session list is the drop target
   for moving sessions out of projects. */
.session-list.drop-unsorted {
  outline: 1.5px dashed rgba(var(--v-theme-primary), 0.55);
  outline-offset: -2px;
  border-radius: 8px;
}

.session-list-drop-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 2px 10px 6px;
  padding: 6px 10px;
  border-radius: 6px;
  background: rgba(var(--v-theme-primary), 0.1);
  color: rgb(var(--v-theme-primary));
  font-size: 12px;
}

.drag-hint-enter-active,
.drag-hint-leave-active {
  transition: opacity 0.12s ease;
}
.drag-hint-enter-from,
.drag-hint-leave-to {
  opacity: 0;
}

.session-item {
  width: 100%;
  min-height: 30px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  display: flex;
  align-items: center;
  gap: 7px;
  /* Right inset reserves the hover action gutter (rename + overflow = 56px
     plus their 6px edge inset) so revealing the actions never reflows the
     title. Was 88px for four buttons. */
  padding: 4px 62px 4px 10px;
  position: relative;
  box-sizing: border-box;
  cursor: pointer;
  text-align: left;
}

.session-item:hover,
.session-item.active {
  background: var(--chat-session-active-bg);
}

/* 2026-08-13 (elecvoid243): highlight sessions with an unanswered
   ask_user_choice prompt. A subtle amber tint + pulsing dot keeps the
   signal visible without clashing with the `active` background. */
.session-item.needs-choice {
  background: rgba(245, 158, 11, 0.14);
}

.session-choice-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f59e0b;
  flex-shrink: 0;
  animation: session-choice-pulse 1.2s ease-in-out infinite;
}

@keyframes session-choice-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

/* 2026-09-01 (elecvoid243): calmer marker for sessions whose run finished
   while the user was elsewhere — steady green dot + faint tint, visually
   distinct from the pulsing amber pending-choice highlight. Cleared as
   soon as the user opens the session. Since 2026-09-14 it also covers
   sessions manually marked unread from the sidebar context menu. */
.session-item.has-finished-run {
  background: rgba(16, 185, 129, 0.1);
}

.session-finished-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #10b981;
  flex-shrink: 0;
}

/* 2026-09-18 (elecvoid243): persisted star marker. Uses the theme primary
   colour on purpose: amber already means "waiting for a choice" and green
   means "unread / run finished", so a fourth status colour would be noise.
   Filled (not outlined) so it reads as a marker at 13px. */
.session-star-badge {
  flex-shrink: 0;
  color: rgb(var(--v-theme-primary));
  fill: currentColor;
}

.session-title {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 500;
}

.session-progress {
  position: absolute;
  /* Shares the overflow-button slot (right 6px + 26px box) so the running
     spinner and the actions swap without moving. */
  right: 11px;
  top: 50%;
  transform: translateY(-50%);
  flex-shrink: 0;
  transition:
    opacity 0.14s ease,
    visibility 0.14s ease;
}

.session-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  opacity: 0;
  pointer-events: none;
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  visibility: hidden;
}

.session-item:hover .session-actions,
.session-item:focus-within .session-actions {
  opacity: 1;
  pointer-events: auto;
  visibility: visible;
}

/* Branch meta (badge + jump-to-source ≈ 62px) stays visible without hover and
   sits left of the hover actions, so the row inset has to clear both clusters:
   66 + 62 + 4. The old 92px offset let the jump button overlap the title. */
.session-item.has-branch-meta {
  padding-right: 132px;
}

.session-branch-meta {
  position: absolute;
  right: 66px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  gap: 2px;
}

.session-branch-badge {
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

.session-branch-badge:hover {
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgb(var(--v-theme-on-surface));
}

.session-item:hover .session-progress,
.session-item:focus-within .session-progress {
  opacity: 0;
  visibility: hidden;
}

.session-action-btn {
  width: 26px;
  height: 26px;
  min-width: 26px;
  border-radius: 8px;
  color: var(--chat-muted);
}

.session-action-btn:hover {
  color: rgb(var(--v-theme-on-surface));
}

/* 2026-08-13 (elecvoid243): batch-manage / search buttons live at the top
   of the sidebar (above the project section). */
.sidebar-top-actions {
  display: flex;
  gap: 6px;
  margin-top: 2px;
}

.sidebar-top-action-btn {
  flex: 1;
  min-width: 0;
  min-height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}

.sidebar-top-action-btn:hover,
.sidebar-top-action-btn.active {
  color: rgb(var(--v-theme-on-surface));
  background: rgba(var(--v-theme-on-surface), 0.07);
}

.batch-select-bar {
  position: sticky;
  top: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 0 4px 6px;
  padding: 2px 6px;
  border-radius: 8px;
  /* Opaque backdrop so scrolled sessions don't show through the stuck bar
     (dark-mode --chat-session-active-bg is translucent). */
  background: var(--chat-sidebar-bg);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.14);
  font-size: 12px;
  color: var(--chat-muted);
}

.batch-select-count {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.batch-select-all,
.session-select-checkbox {
  flex: 0 0 auto;
}

.batch-select-all :deep(.v-selection-control),
.session-select-checkbox :deep(.v-selection-control) {
  min-height: 24px;
}

/* 2026-08-09 (elecvoid243): larger batch-bar action labels, smaller
   per-row checkboxes. */
.batch-select-archive,
.batch-select-delete,
.batch-select-exit {
  font-size: 13px;
}

.session-select-checkbox :deep(.v-selection-control__input) {
  width: 16px;
  height: 16px;
}

.session-select-checkbox :deep(.v-icon) {
  font-size: 16px;
}

.session-item.selection {
  padding-right: 10px;
}

.session-item.checked {
  background: var(--chat-session-active-bg);
}

.empty-sessions {
  padding: 12px;
  color: var(--chat-muted);
  font-size: 13px;
}

.sidebar-footer {
  margin-top: auto;
  padding: 10px 16px 14px;
}

.chat-sidebar.collapsed .sidebar-footer {
  width: 56px;
  box-sizing: border-box;
  padding-inline: 10px;
}

.settings-menu-content {
  min-width: 270px;
  padding: 6px;
}

.settings-menu-item {
  min-height: 42px;
}

.settings-menu-content :deep(.settings-menu-item .v-list-item__prepend) {
  width: 28px;
  margin-inline-end: 12px;
  align-self: center;
}

.settings-menu-content :deep(.settings-menu-item .v-list-item__content) {
  min-width: 0;
}

.settings-menu-content :deep(.settings-menu-item .v-list-item-title) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.settings-menu-content :deep(.settings-menu-item .v-list-item__append) {
  margin-inline-start: auto;
  padding-inline-start: 18px;
  gap: 8px;
  align-self: center;
}

.styled-menu-lucide-icon {
  flex: 0 0 auto;
  color: currentcolor;
  stroke-width: 2;
}

.settings-menu-value {
  color: var(--chat-muted);
  font-size: 12px;
  margin-right: 4px;
  max-width: 92px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.language-flag {
  display: inline-block;
  width: 20px;
  margin-right: 8px;
}

.chat-main {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  position: relative;
  box-sizing: border-box;
  padding-top: 50px;
}

/* 2026-10-22 side-chat: floating entry in the messages area. right: 44px
   clears the 28px marker rail (+4px inset) pinned to the panel edge. */
.side-chat-entry {
  position: absolute;
  top: 12px;
  right: 44px;
  z-index: 7;
}

.side-chat-fab {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 999px;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
  cursor: pointer;
  transition:
    background 0.14s ease,
    opacity 0.14s ease;
}

.side-chat-fab:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.side-chat-fab:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}

.side-chat-fab:disabled {
  opacity: 0.38;
  cursor: default;
}

.side-chat-fab.is-busy :deep(.v-icon) {
  animation: side-chat-spin 1s linear infinite;
}

.side-chat-menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 280px;
  max-height: 380px;
  overflow-y: auto;
  padding: 6px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 12px;
  background: rgb(var(--v-theme-surface));
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.14);
  z-index: 8;
}

/* Bridge the 8px gap so moving from the button to the menu keeps it open. */
.side-chat-menu::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  top: -8px;
  height: 8px;
}

.side-chat-menu-new {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  border: 0;
  border-radius: 8px;
  padding: 8px 10px;
  background: transparent;
  color: rgb(var(--v-theme-primary));
  font: inherit;
  font-size: 12.5px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
}

.side-chat-menu-new:hover {
  background: rgba(var(--v-theme-primary), 0.08);
}

.side-chat-menu-item {
  display: flex;
  align-items: center;
  gap: 2px;
  border-radius: 8px;
}

.side-chat-menu-item.is-active {
  background: rgba(var(--v-theme-primary), 0.08);
}

.side-chat-menu-title {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 6px;
  min-width: 0;
  border: 0;
  border-radius: 8px;
  padding: 8px 6px 8px 10px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.78);
  font: inherit;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}

.side-chat-menu-title span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.side-chat-menu-title:hover {
  color: rgb(var(--v-theme-on-surface));
}

.side-chat-menu-delete {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  margin-right: 4px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.42);
  cursor: pointer;
  opacity: 0;
  transition:
    opacity 0.14s ease,
    color 0.14s ease;
}

.side-chat-menu-item:hover .side-chat-menu-delete,
.side-chat-menu-delete:focus-visible {
  opacity: 1;
}

.side-chat-menu-delete:hover {
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.08);
}

.side-chat-menu-state {
  padding: 8px 10px;
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.side-chat-menu-enter-active,
.side-chat-menu-leave-active {
  transition:
    opacity 0.14s ease,
    transform 0.14s ease;
}

.side-chat-menu-enter-from,
.side-chat-menu-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@keyframes side-chat-spin {
  to {
    transform: rotate(360deg);
  }
}

.provider-workspace-shell {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.provider-workspace-page {
  height: 100%;
  min-height: 0;
}

.conversation-stack {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}

/* 全区域拖拽上传遮罩 */
.chat-drop-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 100;
  pointer-events: none;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: rgba(var(--v-theme-primary), 0.12);
  border: 2px dashed rgba(var(--v-theme-primary), 0.45);
  border-radius: 16px;
}

.chat-drop-overlay-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.chat-drop-text {
  font-size: 16px;
  font-weight: 500;
  color: rgb(var(--v-theme-primary));
}

.drop-fade-enter-active,
.drop-fade-leave-active {
  transition: opacity 0.2s ease;
}

.drop-fade-enter-from,
.drop-fade-leave-to {
  opacity: 0;
}

.conversation-stack.is-empty {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 28px;
}

.messages-panel {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* The reader's text anchor, follow, or an explicit jump owns the viewport. */
  overflow-anchor: none;
  /* 2026-10-22 (elecvoid243): reserve the scrollbar gutter. The paged history
     window often sits right at the viewport height, so a scrollbar that
     appeared or hid mid-stream used to narrow .messages-list-shell (80% of the
     panel) by ~12px and re-wrap every message — a whole-document height jump
     and a flickering scrollbar. The gutter is reserved unconditionally, which
     also keeps the marker rail's measured offset stable. */
  scrollbar-gutter: stable;
  padding: 24px 0 0;
}

.conversation-stack.is-empty .messages-panel {
  flex: none;
  min-height: auto;
  overflow: visible;
  padding: 0;
}

.messages-list-shell {
  width: var(--chat-content-width);
  max-width: var(--chat-content-max-width);
  margin: 0 auto;
}

/* Jump-to-bottom affordance (2026-10-22, elecvoid243). The row keeps the
   composer's column so the button lines up with the message edge, but it is
   zero height: it only exists to anchor the floating button just above the
   composer, which is why no measured composer height is involved. The button
   is centred rather than right-aligned: the bottom-right corner of the panel
   already carries the context-injection marker, and the two overlapped. */
.jump-to-bottom-row {
  position: relative;
  height: 0;
  width: var(--chat-content-width);
  max-width: var(--chat-content-max-width);
  margin: 0 auto;
  display: flex;
  justify-content: center;
  pointer-events: none;
  z-index: 6;
}

.jump-to-bottom {
  position: absolute;
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 999px;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.14);
  cursor: pointer;
  pointer-events: auto;
}

.jump-to-bottom:hover {
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.jump-to-bottom:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}

/* Touch: keep the tap target near the platform minimum. */
@media (pointer: coarse) {
  .jump-to-bottom {
    width: 40px;
    height: 40px;
  }
}

/* 2026-11-07 (elecvoid243): output is still arriving while the reader is away,
   so the button face becomes the "still writing" dots. The arrow stays in the
   DOM and returns on hover/focus, so the control's meaning and hit target never
   change — only its face does, which is what the corner pip used to tell. */
.jump-to-bottom-arrow {
  transition: opacity 0.12s ease;
}

.jump-to-bottom--live .jump-to-bottom-arrow {
  opacity: 0;
}

.jump-to-bottom--live:hover .jump-to-bottom-arrow,
.jump-to-bottom--live:focus-visible .jump-to-bottom-arrow {
  opacity: 1;
}

.jump-to-bottom-typing {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  pointer-events: none;
  transition: opacity 0.12s ease;
}

.jump-to-bottom--live:hover .jump-to-bottom-typing,
.jump-to-bottom--live:focus-visible .jump-to-bottom-typing {
  opacity: 0;
}

.jump-to-bottom-typing span {
  width: 4px;
  height: 4px;
  border-radius: 999px;
  background: rgb(var(--v-theme-primary));
  animation: jump-to-bottom-bounce 1.1s ease-in-out infinite;
}

.jump-to-bottom-typing span:nth-child(2) {
  animation-delay: 0.15s;
}

.jump-to-bottom-typing span:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes jump-to-bottom-bounce {
  0%,
  80%,
  100% {
    transform: translateY(0);
    opacity: 0.45;
  }
  40% {
    transform: translateY(-3px);
    opacity: 1;
  }
}

.jump-to-bottom-enter-active,
.jump-to-bottom-leave-active {
  transition:
    opacity 0.16s ease,
    transform 0.16s ease;
}

.jump-to-bottom-enter-from,
.jump-to-bottom-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

@media (prefers-reduced-motion: reduce) {
  .jump-to-bottom-enter-active,
  .jump-to-bottom-leave-active {
    transition: none;
  }

  .jump-to-bottom-typing span {
    animation: none;
    opacity: 1;
  }
}

.center-state,
.welcome-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
}

.conversation-stack.is-empty .welcome-state {
  height: auto;
}

.welcome-title {
  font-family: "Outfit", "Noto Sans", sans-serif;
  font-size: 28px;
  font-weight: 800;
}

.welcome-subtitle {
  margin-top: 8px;
  color: var(--chat-muted);
  font-size: 16px;
}

.thread-selection-action {
  position: fixed;
  z-index: 1200;
  pointer-events: auto;
}

.thread-selection-button {
  min-height: 34px;
  padding: 0 14px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 999px;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.14);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}

.composer-shell {
  position: relative;
  background: transparent;
  padding: 0 0 18px;
}

/* 2026-08-13 (elecvoid243): read-only bar replacing the composer while an
   archived session is open. */
.readonly-session-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-radius: 10px;
  background: var(--chat-session-active-bg);
  color: var(--chat-muted);
  font-size: 13px;
}

.readonly-session-text {
  flex: 1;
  min-width: 0;
}

.composer-shell :deep(.input-area) {
  padding-top: 0;
  border-top: 0;
}

.conversation-stack.is-empty .composer-shell {
  padding-bottom: 0;
}

kbd {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.08);
  font: inherit;
}

:deep(.hr-node) {
  margin-top: 1.25rem;
  margin-bottom: 1.25rem;
  opacity: 0.5;
  border-top-width: 0.3px;
}

:deep(.paragraph-node) {
  margin: 0.5rem 0;
  line-height: 1.7;
}

:deep(.list-node) {
  margin-top: 0.5rem;
  margin-bottom: 0.5rem;
}

@media (max-width: 760px) {
  .side-chat-entry {
    top: 10px;
    right: 38px;
  }

  .side-chat-menu {
    width: min(280px, calc(100vw - 24px));
  }

  .chat-sidebar {
    top: 50px !important;
    height: calc(100vh - 50px) !important;
  }

  .messages-panel {
    padding: 18px 0 0;
  }

  .conversation-stack.is-empty .messages-panel {
    padding: 0;
  }

  .messages-list-shell {
    width: calc(100% - 20px);
    max-width: calc(100% - 72px);
  }

  .composer-shell {
    padding: 0;
  }
}

@media (pointer: coarse) {
  .messages-list-shell {
    /* Keep the wider touch targets clear of message text and media. */
    max-width: min(var(--chat-content-max-width), calc(100% - 104px));
  }
}
</style>
