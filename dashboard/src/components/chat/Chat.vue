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
            icon
            rounded="sm"
            variant="text"
            @click.stop="toggleChatSidebar"
          >
            <PanelLeft :size="20" class="sidebar-panel-toggle-icon" />
          </v-btn>
        </div>

        <button
          v-if="isSidebarCollapsed"
          class="new-chat-btn sidebar-provider-btn icon-only chat-sidebar-rail-btn"
          :class="{ 'sidebar-workspace-btn--active': isProviderWorkspace }"
          type="button"
          :title="tm('actions.providerConfig')"
          @click="openProviderWorkspace"
        >
          <Box :size="18" class="sidebar-action-icon" />
        </button>
        <v-btn
          v-else
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
          @edit-project="openEditProjectDialog"
          @delete-project="handleDeleteProject"
          @toggle-project="handleProjectToggle"
          @select-project="selectProject"
          @select-session="selectProjectSession"
          @edit-session-title="editProjectSessionTitle"
          @delete-session="deleteProjectSession"
          @toggle-session-checked="toggleSessionChecked"
          @archive-session="archiveProjectSession"
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
                :title="tm('conversation.archive')"
                @click="archiveSidebarSession(session)"
              >
                <Archive :size="15" />
              </v-btn>
              <v-btn
                icon
                size="x-small"
                variant="text"
                class="session-action-btn"
                :title="tm('actions.deleteChat')"
                @click="deleteSidebarSession(session)"
              >
                <Trash2 :size="15" />
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
        <section
          ref="messagesContainer"
          class="messages-panel"
          @scroll="handleMessagesScroll"
        >
          <!-- 可拖动的 todo summary 浮窗 + 可展开悬浮菜单:
               初始位置: 页面顶部正中;
               拖动范围: 不得超出 .chat-main 边界,不得进入 .composer-shell 区域;
               位置持久化: localStorage。
               键盘 a11y: tabindex=0 让 button 可被 Tab 聚焦;Enter/Space 自动触发 click
               (浏览器对 <button> 的默认行为,会调用 onTodoBarClick → toggleTodoMenu);
               方向键移动位置 (8px/次),复用 clampBarPos + 同一个 localStorage key。
               点击胶囊展开悬浮菜单 (TodoListPanel);菜单由 placeTodoMenu 做
               锚定 + 双向翻转 + 边界夹取,胶囊贴边时菜单向内侧展开,永不越界。-->
          <transition name="todo-bar-fade">
            <button
              v-if="currentTodoSnapshot"
              type="button"
              class="todo-summary-bar"
              :class="{
                'todo-summary-bar--active': todoMenuOpen,
                'todo-summary-bar--dragging': isDraggingTodoBar,
                'todo-summary-bar--centered': todoBarPos === null,
                'todo-summary-bar--gitdiff-fullscreen':
                  gitDiffSidebarOpen && gitDiffFullscreen,
              }"
              :style="todoBarStyle"
              tabindex="0"
              :aria-label="tm('todo.summary')"
              :aria-expanded="todoMenuOpen"
              :aria-keyshortcuts="todoBarKeyShortcuts"
              @mousedown="startDragTodoBar"
              @click="onTodoBarClick"
              @keydown="onTodoBarKeydown"
            >
              <v-icon size="16" class="todo-summary-icon"
                >mdi-format-list-checks</v-icon
              >
              <v-icon size="14" class="todo-summary-drag-handle"
                >mdi-drag-horizontal-variant</v-icon
              >
              <span class="todo-summary-text">
                {{ currentTodoSnapshot.stats?.done || 0 }}/{{
                  currentTodoSnapshot.stats?.effective_total || 0
                }}
                <template v-if="currentTodoSnapshot.stats?.in_progress">
                  ·
                  <span class="todo-summary-progress"
                    >{{ currentTodoSnapshot.stats.in_progress }} in
                    progress</span
                  >
                </template>
              </span>
              <v-progress-circular
                v-if="
                  currentTodoSnapshot.stats?.progress_pct > 0 &&
                  currentTodoSnapshot.stats?.progress_pct < 100
                "
                :model-value="currentTodoSnapshot.stats.progress_pct"
                :size="16"
                :width="2"
                class="todo-summary-circular"
              >
                {{ currentTodoSnapshot.stats.progress_pct }}%
              </v-progress-circular>
              <v-icon
                v-if="currentTodoSnapshot.attentionItems?.length"
                size="10"
                color="warning"
                class="todo-summary-attention"
                :title="
                  tm('todo.attentionHint', {
                    count: currentTodoSnapshot.attentionItems.length,
                  })
                "
                >mdi-circle-medium</v-icon
              >
            </button>
          </transition>

          <!-- todo summary 悬浮菜单: 与胶囊同层 (position: fixed) 的兄弟
               面板, 位置完全由 placeTodoMenu 计算的 todoMenuStyle 决定。
               z-index 略低于胶囊, 随 gitdiff 全屏一起降级。 -->
          <transition name="todo-menu-pop">
            <div
              v-if="todoMenuOpen && currentTodoSnapshot"
              ref="todoMenuRef"
              class="todo-summary-menu"
              :class="{
                'todo-summary-menu--gitdiff-fullscreen':
                  gitDiffSidebarOpen && gitDiffFullscreen,
                'todo-summary-menu--above':
                  todoMenuPlacement?.anchor === 'bottom',
              }"
              :style="todoMenuStyle"
              @click.stop
              @keydown.esc.stop="closeTodoMenu"
            >
              <div class="todo-menu-body">
                <TodoListPanel
                  :list="currentTodoSnapshot.list"
                  :stats="currentTodoSnapshot.stats"
                  :attention-items="currentTodoSnapshot.attentionItems || []"
                  collapsible
                  :show-header="false"
                />
              </div>
              <div
                v-if="currentTodoSnapshot.list?.updated_at"
                class="todo-menu-footer"
              >
                <v-icon size="12" class="todo-menu-footer-icon"
                  >mdi-clock-outline</v-icon
                >
                {{
                  tm("todo.updatedAt", {
                    time: formatTodoUpdatedAt(
                      currentTodoSnapshot.list.updated_at,
                    ),
                  })
                }}
              </div>
            </div>
          </transition>

          <!-- 加载中 / 消息流 / 欢迎区 主体内容 -->
          <div v-if="loadingMessages" class="center-state">
            <v-progress-circular indeterminate size="32" width="3" />
          </div>
          <div v-else-if="!activeMessages.length" class="welcome-state">
            <div class="welcome-title">{{ tm("welcome.title") }}</div>
          </div>

          <div v-else-if="activeMessages.length" class="messages-list-shell">
            <ChatMessageList
              v-model:edit-draft="messageEditDraft"
              :messages="activeMessages"
              :history-has-more="Boolean(historyPaging?.hasMore)"
              :history-loading-older="Boolean(historyPaging?.loadingOlder)"
              :history-offset="historyOffset"
              :current-umo="currentUmo ?? undefined"
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
              enable-regenerate
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
              @load-older="loadOlderHistory"
            />
          </div>

          <!-- 空消息时的欢迎区 (非 sticky,自然居中) -->
          <div v-else-if="!loadingMessages" class="welcome-state">
            <div class="welcome-title">{{ tm("welcome.title") }}</div>
          </div>
        </section>

        <div
          v-if="scrollMarkers.length"
          class="scroll-marker-strip"
          :class="{ 'is-jumping': jumpInProgress }"
          :style="{
            height: stripHeight + 'px',
            right: stripRightOffset + 'px',
          }"
          @click="onStripClick"
        >
          <div v-if="jumpInProgress" class="scroll-marker-loading">
            <v-progress-circular indeterminate size="14" width="2" />
            <span>{{ tm("history.jumpLoading") }}</span>
          </div>
          <div
            v-for="marker in scrollMarkers"
            :key="`sm-${marker.id}`"
            class="scroll-marker-dot"
            :class="{ 'scroll-marker-dot--inherited': marker.inherited }"
            :style="{ top: marker.topPct + '%' }"
            @click.stop="onDotClick(marker)"
            @mouseenter="onDotEnter(marker.preview, $event)"
            @mousemove="onDotMove($event)"
            @mouseleave="onDotLeave"
          />
        </div>

        <section ref="composerShell" class="composer-shell">
          <template v-if="!isReadonlySession">
            <ChatInput
              ref="inputRef"
              v-model:prompt="draft"
              :staged-images-url="stagedImagesUrl"
              :staged-audio-url="stagedAudioUrl"
              :staged-files="stagedNonImageFiles"
              :disabled="sending"
              :enable-streaming="enableStreaming"
              :is-recording="isRecording"
              :is-running="
                Boolean(
                  currSessionId &&
                    (isSessionRunning(currSessionId) ||
                      hasLiveSystemRecord(currSessionId)),
                )
              "
              :token-usage="tokenUsageIndicator"
              :session-id="currSessionId || null"
              :current-session="currentSession"
              :reply-to="chatInputReplyTarget"
              :send-shortcut="sendShortcut"
              :show-provider-selector="false"
              :placeholder="
                activeProject ? tm('input.projectPlaceholder') : undefined
              "
              @send="sendCurrentMessage"
              @send-command="sendSystemCommand"
              @stop="stopCurrentSession"
              @flush-pending="flushPendingFromInput"
              @toggle-streaming="toggleStreaming"
              @remove-image="removeImage"
              @remove-audio="removeAudio"
              @remove-file="removeFile"
              @start-recording="startRecording"
              @stop-recording="stopRecording"
              @paste-image="handlePaste"
              @file-select="handleFilesSelected"
              @file-reference-drop="handleSidebarFileDrop"
              @clear-reply="replyTarget = null"
              @open-diff-sidebar="openGitDiffSidebar"
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
        </section>
      </div>
    </main>

    <Teleport to="body">
      <div
        v-if="dotTooltip.visible"
        ref="dotTooltipRef"
        class="scroll-dot-tooltip"
        :class="{ 'is-dark': isDark }"
        :style="{
          position: 'fixed',
          right: dotTooltip.right + 'px',
          top: dotTooltip.y + 'px',
          zIndex: 10000,
        }"
      >
        <span class="scroll-dot-tooltip-text">{{ dotTooltip.text }}</span>
      </div>
    </Teleport>

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
      v-model="threadPanelOpen"
      :thread="activeThread"
      :is-dark="isDark"
      :deleting="deletingThread"
      @delete="deleteThread"
    />
    <ReasoningSidebar
      v-model="reasoningPanelOpen"
      :parts="activeReasoningParts"
      :is-dark="isDark"
      :focus-call-id="activeReasoningTarget?.callId ?? null"
    />
    <RefsSidebar
      v-model="refsSidebarOpen"
      :refs="selectedRefs"
      @update:model-value="onRefsToggle"
    />
    <GoalSidebar v-model="goalSidebarOpen" :goal="currentGoal" />
    <GitDiffSidebar
      v-model="gitDiffSidebarOpen"
      :is-dark="isDark"
      @fullscreen-change="gitDiffFullscreen = $event"
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
import { useRoute, useRouter } from "vue-router";
import { useDisplay } from "vuetify";
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
  PanelLeft,
  Pencil,
  Search,
  Settings,
  SquarePen,
  Sun,
  Trash2,
} from "@lucide/vue";
import { chatApi, providerApi } from "@/api/v1";
import { useSessionGoal } from "@/composables/useSessionGoal";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import {
  useSpcodeProjectAutoLoad,
  ProjectLoadError,
} from "@/composables/useSpcodeProjectAutoLoad";
import {
  isSessionLoadedTag,
  markSessionLoadedTag,
  clearSessionLoadedTag,
} from "@/composables/useSpcodeProjectAutoLoad";
import { useSpcodeOperationProgress } from "@/composables/useSpcodeOperationProgress";
import { useSpcodeCodegraphStatus } from "@/composables/useSpcodeCodegraphStatus";
import { useSpcodeVivadoStatus } from "@/composables/useSpcodeVivadoStatus";
import { useSpcodePlanMode } from "@/composables/useSpcodePlanMode";
import { useFileAccessMode } from "@/composables/useFileAccessMode";
import StyledMenu from "@/components/shared/StyledMenu.vue";
import ProjectDialog, {
  type ProjectFormData,
} from "@/components/chat/ProjectDialog.vue";
import ProjectList, { type Project } from "@/components/chat/ProjectList.vue";
import ProjectView from "@/components/chat/ProjectView.vue";
import ChatInput from "@/components/chat/ChatInput.vue";
import ChatMessageList from "@/components/chat/ChatMessageList.vue";
import ChatUILogo from "@/components/chat/ChatUILogo.vue";
import type { RegenerateModelSelection } from "@/components/chat/RegenerateMenu.vue";
import ReasoningSidebar from "@/components/chat/ReasoningSidebar.vue";
import ThreadPanel from "@/components/chat/ThreadPanel.vue";
import WorkspaceFilesPanel from "@/components/chat/WorkspaceFilesPanel.vue";
import RefsSidebar from "@/components/chat/message_list_comps/RefsSidebar.vue";
import GoalSidebar from "@/components/chat/message_list_comps/GoalSidebar.vue";
import TodoListPanel from "@/components/chat/message_list_comps/spcode_tools/TodoListPanel.vue";
import GitDiffSidebar from "@/components/chat/GitDiffSidebar.vue";
import ChatMessageSearchDialog from "@/components/chat/ChatMessageSearchDialog.vue";
import ArchivedSessionsDialog from "@/components/chat/ArchivedSessionsDialog.vue";
import {
  useSessions,
  type ArchivedSession,
  type Session,
} from "@/composables/useSessions";
import { useFileComments } from "@/composables/useFileComments";
import { useFileReferences } from "@/composables/useFileReferences";
import { useInlineAnnotations } from "@/composables/useInlineAnnotations";
import {
  NEW_CHAT_DRAFT_KEY,
  useChatDrafts,
} from "@/composables/useChatDrafts";
import { usePendingFollowUps } from "@/composables/usePendingFollowUps";
import { resolveSessionUmo } from "@/utils/resolveSessionUmo";
import {
  messageBlocks as buildMessageBlocks,
  useMessages,
  type ChatRecord,
  type ChatThread,
  type MessagePart,
  type ThinkingEffort,
  type TransportMode,
} from "@/composables/useMessages";
import { useMediaHandling } from "@/composables/useMediaHandling";
import { useRecording } from "@/composables/useRecording";
import { useProjects } from "@/composables/useProjects";
import { useDragUpload } from "@/composables/useDragUpload";
import { useChatHeaderStore } from "@/stores/chatHeader";
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
// Operation-progress poller: started around silentLoad so the input-area
// chip shows live step progress during auto-load too (2026-08-06).
const operationProgress = useSpcodeOperationProgress();
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
  updateSessionTitle,
} = useSessions(props.chatboxMode);

// ── goal 循环状态 (右上角 Goal 按钮 + GoalSidebar) ─────────────
// The kernel goal loop has no push channel: state is fetched per session
// on switch (watch inside useSessionGoal) and refreshed after each run
// stream ends (onStreamEnd → refreshGoalAfterRun, converging on late
// judge writes) so /goal set|pause|resume|clear reflect within one turn.
const { currentGoal, refreshGoalAfterRun } = useSessionGoal(currSessionId);
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
  processAndUploadImage,
  processAndUploadFile,
  handlePaste,
  removeImage,
  removeAudio,
  removeFile,
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
const sessionTitleDraft = ref("");
const editingSessionTitleId = ref("");
const refreshProjectSessionsAfterTitleSave = ref(false);
const savingSessionTitle = ref(false);
const messageEditDraft = ref("");
const editingMessage = ref<ChatRecord | null>(null);
const savingMessageEdit = ref(false);
const scrollMarkers = ref<
  Array<{
    id: string | number;
    // Absolute index within the session history; -1 when unknown (the
    // loaded-window fallback markers still scroll by message id).
    index: number;
    topPct: number;
    preview: string;
    inherited: boolean;
  }>
>([]);
const stripHeight = ref(0);
const stripRightOffset = ref(0); // scrollbar width (px) so the yellow strip sits flush with the scrollbar's left edge
// A marker jump that has to page older history is in flight — suppress
// duplicate clicks and show the strip loading hint.
const jumpInProgress = ref(false);
// Mirror of ChatMessageList's collapsed branch history (default collapsed
// there too): inherited scroll markers stay hidden while collapsed.
const branchHistoryCollapsed = ref(true);
// While a programmatic scroll animation runs, keep the scroll-top auto-load
// from firing mid-animation (it would prepend + anchor and cancel the
// smooth scroll, leaving the viewport off the target).
const suppressHistoryAutoLoad = ref(false);
const dotTooltip = reactive({
  visible: false,
  text: "",
  right: 0,
  y: 0,
});
const dotTooltipRef = ref<HTMLElement | null>(null);
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
const selectedTokenProviderId = ref("");
const commandSending = ref(false);
const messagesContainer = ref<HTMLElement | null>(null);
const composerShell = ref<HTMLElement | null>(null);
const inputRef = ref<InstanceType<typeof ChatInput> | null>(null);
const shouldStickToBottom = ref(true);
const replyTarget = ref<ChatRecord | null>(null);
const threadPanelOpen = ref(false);
const activeThread = ref<ChatThread | null>(null);
const reasoningPanelOpen = ref(false);
const activeReasoningTarget = ref<{
  message: ChatRecord;
  blockIndex: number;
  callId?: string;
} | null>(null);
const deletingThread = ref(false);
const refsSidebarOpen = ref(false);
// 2026-09-09: TodoSidebar removed. The floating summary bar now expands
// in place into a floating menu; the open state is local (the app-bar
// button no longer opens the todo view — it became the Goal entry).
const todoMenuOpen = ref(false);
const todoMenuRef = ref<HTMLElement | null>(null);

/** 悬浮菜单定位结果 (viewport 坐标, 面板为 position: fixed)。
 *  anchor "top"    = 挂在胶囊下方 (edge = 距视口顶部的 px);
 *  anchor "bottom" = 向上翻开 (edge = 距视口底部的 px)。 */
const todoMenuPlacement = ref<{
  left: number;
  edge: number;
  anchor: "top" | "bottom";
  maxHeight: number;
} | null>(null);

const TODO_MENU_WIDTH = 340;
const TODO_MENU_GAP = 8;
const TODO_MENU_MIN_HEIGHT = 160;
const TODO_MENU_EDGE_MARGIN = 8;

const todoMenuStyle = computed(() => {
  if (!todoMenuPlacement.value) {
    // v-if 刚翻转的第一帧: 先隐藏渲染, 等 placeTodoMenu 量完再显示,
    // 避免菜单在 (0,0) 闪一下。
    return { visibility: "hidden" as const };
  }
  const p = todoMenuPlacement.value;
  return {
    left: `${p.left}px`,
    maxHeight: `${p.maxHeight}px`,
    ...(p.anchor === "top"
      ? { top: `${p.edge}px` }
      : { bottom: `${p.edge}px` }),
  };
});

/**
 * 悬浮菜单定位: 锚定 + 双向翻转 + 边界夹取。
 *
 * 水平: 默认与胶囊左缘对齐; 右侧放不下 → 改为右缘与胶囊右缘对齐
 * (左翻); 最后整体夹取进 .chat-main, 胶囊贴任何边都不会越界。
 * 垂直: 默认挂在胶囊下方; 下方空间不足 (会压到 composer 区) → 向上
 * 翻开 (bottom 锚定); 两侧都不够 → 选空间大的一侧并压缩 max-height
 * (菜单内部滚动兜底)。
 *
 * 位置读 todoBarPos (拖动中的响应式值, 无 DOM 渲染滞后), 尺寸读胶囊
 * rect; 居中态 (CSS translateX(-50%)) 的 rect 已含 transform, 直接可用。
 */
function placeTodoMenu() {
  if (!todoMenuOpen.value) return;
  const bar = document.querySelector(
    ".todo-summary-bar",
  ) as HTMLElement | null;
  const main = document.querySelector(".chat-main") as HTMLElement | null;
  if (!bar || !main) return;
  const barRect = bar.getBoundingClientRect();
  const mainRect = main.getBoundingClientRect();
  const composer = document.querySelector(
    ".chat-main .composer-shell",
  ) as HTMLElement | null;
  const bottomBound = composer
    ? composer.getBoundingClientRect().top - 4
    : mainRect.bottom;
  // 窄屏下 CSS 会把菜单收缩到视口内, 用实测宽度而不是常量
  const menuWidth = todoMenuRef.value?.offsetWidth || TODO_MENU_WIDTH;

  const pillLeft = todoBarPos.value ? todoBarPos.value.left : barRect.left;
  const pillTop = todoBarPos.value ? todoBarPos.value.top : barRect.top;
  const pillRight = pillLeft + barRect.width;
  const pillBottom = pillTop + barRect.height;

  // 水平: 左对齐 → 右对齐翻转 → 夹取
  let left = pillLeft;
  if (left + menuWidth > mainRect.right - TODO_MENU_EDGE_MARGIN) {
    left = pillRight - menuWidth;
  }
  left = Math.max(
    mainRect.left + TODO_MENU_EDGE_MARGIN,
    Math.min(left, mainRect.right - TODO_MENU_EDGE_MARGIN - menuWidth),
  );

  // 垂直: 先下方, 空间不足上翻, 两侧都不够取大者并压缩高度
  const spaceBelow = bottomBound - (pillBottom + TODO_MENU_GAP);
  const spaceAbove = pillTop - TODO_MENU_GAP - mainRect.top;
  let anchor: "top" | "bottom";
  let edge: number;
  let maxHeight: number;
  if (spaceBelow >= TODO_MENU_MIN_HEIGHT || spaceBelow >= spaceAbove) {
    anchor = "top";
    edge = pillBottom + TODO_MENU_GAP;
    maxHeight = Math.max(TODO_MENU_MIN_HEIGHT, spaceBelow);
  } else {
    anchor = "bottom";
    edge = window.innerHeight - pillTop + TODO_MENU_GAP;
    maxHeight = Math.max(TODO_MENU_MIN_HEIGHT, spaceAbove);
  }
  todoMenuPlacement.value = { left, edge, anchor, maxHeight };
}

function toggleTodoMenu() {
  if (todoMenuOpen.value) {
    closeTodoMenu();
    return;
  }
  todoMenuOpen.value = true;
  // 等菜单渲染出来再测量定位 (真实盒高/宽度)
  nextTick(placeTodoMenu);
}

function closeTodoMenu() {
  todoMenuOpen.value = false;
  todoMenuPlacement.value = null;
}

/** 点击胶囊和菜单以外的区域时关闭 (capture 阶段, 抢在其它 handler 前)。 */
function onDocMouseDownForTodoMenu(e: MouseEvent) {
  const target = e.target as Node;
  const menu = todoMenuRef.value;
  const bar = document.querySelector(".todo-summary-bar");
  if (menu && (menu.contains(target) || bar?.contains(target))) return;
  closeTodoMenu();
}

watch(todoMenuOpen, (open) => {
  if (open) {
    document.addEventListener("mousedown", onDocMouseDownForTodoMenu, true);
    window.addEventListener("resize", placeTodoMenu);
  } else {
    document.removeEventListener("mousedown", onDocMouseDownForTodoMenu, true);
    window.removeEventListener("resize", placeTodoMenu);
    todoMenuPlacement.value = null;
  }
});

/** Render list.updated_at ISO string as a compact time label
 *  ("HH:MM" same day, "MM-DD HH:MM" otherwise) for the menu footer. */
function formatTodoUpdatedAt(value: string | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  const hm = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (sameDay) return hm;
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${hm}`;
}
const gitDiffSidebarOpen = ref(false);
const gitDiffFullscreen = ref(false);

/* ── todo summary bar 拖动 ───────────────────────────
 * 浮窗可拖动,位置持久化到 localStorage。
 * 边界:不得超出 .chat-main 矩形,不得进入 .composer-shell 区域(按 max 高度算)。
 */
type BarPos = { left: number; top: number };
const TODO_BAR_POS_KEY = "chatui.todoBarPos.v1";
const todoBarPos = ref<BarPos | null>(null);
const isDraggingTodoBar = ref(false);
let dragState: {
  mouseStartX: number;
  mouseStartY: number;
  barStartLeft: number;
  barStartTop: number;
  didMove: boolean;
} | null = null;
let suppressNextClick = false;

/** 把 (left, top) 限制在 chat-main 内, 不与 composer-shell 重叠。 */
function clampBarPos(
  desiredLeft: number,
  desiredTop: number,
  barRect: DOMRect,
  mainRect: DOMRect,
): BarPos {
  // 边界: 不得超出 main 矩形
  const minLeft = mainRect.left;
  const maxLeft = Math.max(minLeft, mainRect.right - barRect.width);
  const minTop = mainRect.top;
  // 输入框区域: chat-main 内 .composer-shell 的顶部。
  // composer 在多行内容时会增高, 这里直接以"main 底部减 bar 高度"作为最底,
  // 避免压到任何状态下的输入框 (单行/多行均不会越界)。
  const composer = document.querySelector(
    ".chat-main .composer-shell",
  ) as HTMLElement | null;
  let maxTop: number;
  if (composer) {
    const composerRect = composer.getBoundingClientRect();
    // composer 区域可能因为多行内容上下扩张, 取其 top 减 bar 高度
    maxTop = composerRect.top - barRect.height - 4;
  } else {
    maxTop = mainRect.bottom - barRect.height;
  }
  maxTop = Math.max(minTop, maxTop);

  return {
    left: Math.max(minLeft, Math.min(desiredLeft, maxLeft)),
    top: Math.max(minTop, Math.min(desiredTop, maxTop)),
  };
}

/** 记录当前 CSS 居中渲染出的视觉位置,作为首次 mousedown 的拖动起点。
 *  直接复用 .todo-summary-bar--centered (left: 50% / top: 60px + translateX(-50%))
 *  此刻的 getBoundingClientRect —— 它已经包含了 transform,
 *  因此后续切到内联 left/top 像素值时,坐标完全一致,不会瞬移。
 *  仅在 todoBarPos === null 时调用。
 */
function initTodoBarPos() {
  nextTick(() => {
    const bar = document.querySelector(
      ".todo-summary-bar",
    ) as HTMLElement | null;
    if (!bar) return;
    const rect = bar.getBoundingClientRect();
    todoBarPos.value = { left: rect.left, top: rect.top };
  });
}

/** 计算最终应用的 inline style: 初始居中 / 拖动后 absolute。 */
const todoBarStyle = computed(() => {
  if (todoBarPos.value === null) {
    return {}; // 由 CSS .todo-summary-bar--centered 居中
  }
  return {
    left: `${todoBarPos.value.left}px`,
    top: `${todoBarPos.value.top}px`,
  };
});

function startDragTodoBar(e: MouseEvent) {
  if (!todoBarPos.value) {
    // 第一次出现: 同步初始化位置,然后进入拖动
    initTodoBarPos();
    // 等下一帧位置就绪后再开始 drag,否则 mouseStart 基准是错的
    nextTick(() => {
      if (todoBarPos.value) actuallyStartDrag(e);
    });
    return;
  }
  actuallyStartDrag(e);
}

function actuallyStartDrag(e: MouseEvent) {
  if (!todoBarPos.value) return;
  isDraggingTodoBar.value = true;
  dragState = {
    mouseStartX: e.clientX,
    mouseStartY: e.clientY,
    barStartLeft: todoBarPos.value.left,
    barStartTop: todoBarPos.value.top,
    didMove: false,
  };
  document.addEventListener("mousemove", onDragTodoBarMove);
  document.addEventListener("mouseup", endDragTodoBar);
  // 阻止默认文本选择
  e.preventDefault();
}

function onDragTodoBarMove(e: MouseEvent) {
  if (!dragState || !todoBarPos.value) return;
  const deltaX = e.clientX - dragState.mouseStartX;
  const deltaY = e.clientY - dragState.mouseStartY;
  // 超过阈值才算"拖动",否则视为准备 click
  if (!dragState.didMove && (Math.abs(deltaX) > 3 || Math.abs(deltaY) > 3)) {
    dragState.didMove = true;
    suppressNextClick = true; // 一旦真拖动, 屏蔽紧随其后的 click
  }
  if (!dragState.didMove) return;

  const bar = document.querySelector(".todo-summary-bar") as HTMLElement | null;
  const main = document.querySelector(".chat-main") as HTMLElement | null;
  if (!bar || !main) return;
  const mainRect = main.getBoundingClientRect();
  const barRect = bar.getBoundingClientRect();
  const desiredLeft = dragState.barStartLeft + deltaX;
  const desiredTop = dragState.barStartTop + deltaY;
  todoBarPos.value = clampBarPos(desiredLeft, desiredTop, barRect, mainRect);
  // 菜单跟随胶囊 (placeTodoMenu 读的是响应式 todoBarPos, 无渲染滞后)
  placeTodoMenu();
}

function endDragTodoBar() {
  isDraggingTodoBar.value = false;
  if (dragState?.didMove && todoBarPos.value) {
    // 持久化拖动后的位置
    try {
      localStorage.setItem(TODO_BAR_POS_KEY, JSON.stringify(todoBarPos.value));
    } catch {
      /* 忽略 localStorage 写入失败 (隐私模式等) */
    }
  }
  dragState = null;
  document.removeEventListener("mousemove", onDragTodoBarMove);
  document.removeEventListener("mouseup", endDragTodoBar);
}

/** 区分 click 和 drag 结束: 仅当未发生实际拖动时触发 toggle。 */
function onTodoBarClick(e: MouseEvent) {
  if (suppressNextClick) {
    e.preventDefault();
    e.stopPropagation();
    suppressNextClick = false;
    return;
  }
  toggleTodoMenu();
}

// localStorage 恢复已禁用: 每次居中,避免缓存污染。
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
 * 模式与同文件 todoBarPos 拖拽一致: dragState 单例 + 模块级 flag,
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

const activeReasoningParts = computed<MessagePart[]>(() => {
  if (!activeReasoningTarget.value) return [];
  const blocks = buildMessageBlocks(
    activeReasoningTarget.value.message.content || { type: "bot", message: [] },
  );
  const block = blocks[activeReasoningTarget.value.blockIndex];
  return block?.kind === "thinking" ? block.parts : [];
});

watch(reasoningPanelOpen, (open) => {
  if (!open) {
    activeReasoningTarget.value = null;
  }
});

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
  onSessionsChanged: getSessions,
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
    if (sessionId === currSessionId.value && shouldStickToBottom.value) {
      scrollToBottom();
    }
  },
  // Tool-call boundary of the active run: flush queued follow-ups now
  // so the backend captures them and injects them into THIS tool's
  // result — the same timing the old send-while-running path produced.
  onToolCallActivity: (sessionId) => {
    void flushPendingFollowUps(sessionId);
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
    // Goal state changes at turn boundaries (judge runs on on_agent_done);
    // refresh immediately plus a trailing fetch for late judge writes.
    refreshGoalAfterRun(sessionId);
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
      if (resolvedUmo) {
        void fileAccessMode.refresh(resolvedUmo);
      } else {
        fileAccessMode.reset();
      }
      // Codegraph MCP state is global (not per-umo), so no umo arg.
      // Mirrors project/plan: stream-end is the authoritative sync
      // point — the bot has just finished processing any
      // `/codegraph start|stop|set` command the user dispatched.
      void codegraphStatus.refresh();
      // Vivado MCP state is also global. Refresh on stream-end so the
      // chip catches up after the bot processes any `/vivado` command.
      void vivadoStatus.refresh();
    }
  },
});

// Inline file-review comments (Chunk 4). The store is a module-level
// singleton (see useFileComments.ts header); FileBrowserFilePreview
// (inside GitDiffSidebar → FileBrowserView) and this ChatInput share
// the same instance. resetForSession() drops the current session's
// comments so they don't leak across sessions (spec §2).
const fileComments = useFileComments();
// 2026-08-09 drag-reference (elecvoid243): sidebar file drops become
// references. Same singleton pattern as fileComments.
const fileReferences = useFileReferences();
const inlineAnnotations = useInlineAnnotations();
watch(currSessionId, (newId, oldId) => {
  if (oldId && newId !== oldId) {
    fileComments.resetForSession();
    fileReferences.resetForSession();
    inlineAnnotations.resetForSession();
  }
});

// 2026-09-01 (elecvoid243): opening a session acknowledges its
// run-finished marker — the output is right there in the message list.
// 2026-09-14: it reads the session, so a manual unread mark (context
// menu) is dropped the same way.
watch(currSessionId, (sessionId) => {
  if (!sessionId) return;
  finishedAttention.clear(sessionId);
  unreadAttention.markRead(sessionId);
});

const transportMode = ref<TransportMode>(
  (localStorage.getItem("chat.transportMode") as TransportMode) === "websocket"
    ? "websocket"
    : "sse",
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
  localStorage.setItem("chat.transportMode", mode);
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
  currSessionId.value
    ? historyOffsetBySession[currSessionId.value] || 0
    : 0,
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
            cachePercent: formatUsagePercent(cacheHitPercent),
          })
        : tm("tokenUsage.tooltip", tooltipParams),
  };
});

function getSelectedProviderSelection() {
  const inputSelection = inputRef.value?.getCurrentSelection();
  if (inputSelection?.providerId) {
    selectedTokenProviderId.value = inputSelection.providerId;
    return inputSelection;
  }
  if (typeof window === "undefined") {
    return { providerId: "", modelName: "" };
  }
  syncSelectedTokenProvider();
  return {
    providerId: localStorage.getItem("selectedProvider") || "",
    modelName: localStorage.getItem("selectedProviderModel") || "",
  };
}

/** Per-message thinking-effort override from the chat input (auto by default). */
function getCurrentThinkingEffort(): ThinkingEffort {
  return inputRef.value?.getThinkingEffort() ?? "auto";
}

provide("isDark", isDark);

async function scrollToMessageFromQuery() {
  const idxStr = route.query.scrollToIndex as string | undefined;
  if (!idxStr) return;
  const target = parseInt(idxStr, 10);
  if (isNaN(target) || target < 0) return;
  await nextTick();
  await new Promise((r) => setTimeout(r, 300));
  // Windowed history: a search result may live outside the loaded window —
  // jumpToIndex pages older history until the target is rendered. If the
  // target cannot be reached (no more history), leave the query so the next
  // session open retries.
  if (await jumpToIndex(target)) {
    const { scrollToIndex: _, ...rest } = route.query;
    router.replace({ query: rest });
  }
}

watch(
  [chatHeaderTitle, chatHeaderSubtitle, activeProject],
  ([title, subtitle, project]) => {
    chatHeader.SET_CONTEXT({
      title,
      subtitle,
      projectId: project?.project_id,
    });
  },
  { immediate: true },
);

watch(
  () => chatHeader.workspaceFilesOpen,
  (open) => {
    if (!open) return;
    threadSelection.visible = false;
    threadPanelOpen.value = false;
    activeThread.value = null;
    reasoningPanelOpen.value = false;
    activeReasoningTarget.value = null;
    refsSidebarOpen.value = false;
    selectedRefs.value = null;
    closeTodoMenu();
    chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
    gitDiffSidebarOpen.value = false;
  },
);

onMounted(async () => {
  if (typeof ResizeObserver !== "undefined") {
    composerResizeObserver = new ResizeObserver(([entry]) => {
      const container = messagesContainer.value;
      if (!entry || !container) return;
      const height = Math.ceil(entry.target.getBoundingClientRect().height);
      container.style.setProperty("--chat-composer-height", `${height}px`);
      if (shouldStickToBottom.value) scrollToBottom();
    });
    if (composerShell.value) {
      composerResizeObserver.observe(composerShell.value);
    }
  }

  loadingSessions.value = true;
  try {
    await Promise.all([
      getSessions(),
      getArchivedSessions(),
      getProjects(),
      loadTokenProviders(),
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
    if (shouldStickToBottom.value) {
      scrollToBottom();
    }
    nextTick(() => updateScrollMarkers());
  },
  { deep: true },
);

// Scroll marker strip: keep markers updated when container size changes
let markerResizeObserver: ResizeObserver | null = null;

watch(
  messagesContainer,
  (container, _oldContainer) => {
    if (markerResizeObserver) {
      markerResizeObserver.disconnect();
      markerResizeObserver = null;
    }
    if (container) {
      updateScrollMarkers();
      markerResizeObserver = new ResizeObserver(() => {
        updateScrollMarkers();
      });
      markerResizeObserver.observe(container);
    }
  },
  { immediate: true },
);

// Scroll marker strip: once the session marker index arrives, switch the
// strip from the loaded-window fallback to the full-session positions.
watch(
  () =>
    currSessionId.value
      ? sessionMarkersBySession[currSessionId.value]
      : null,
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
    if (resolvedUmo) {
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
  const project = resolveProjectForAutoLoad(sessionId);
  if (!project) return;
  if (project.workspace_type !== "custom") return;
  if (!project.workspace_path) return;

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
  await spcodeStatus.refresh(umo);
  if (
    isSessionLoadedTag(
      sessionId,
      project.project_id,
      spcodeStatus.status.value.bootId,
    )
  ) {
    return;
  }

  try {
    operationProgress.startPolling(umo);
    const data = await silentLoad({ project, umo });
    if (data?.loaded) {
      await spcodeStatus.refresh(umo);
      markSessionLoadedTag(
        sessionId,
        project.project_id,
        spcodeStatus.status.value.bootId,
      );
    }
  } catch (err) {
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
      toast.error(msg);
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
  threadPanelOpen.value = false;
  activeThread.value = null;
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

function syncSelectedTokenProvider() {
  if (typeof window === "undefined") return;
  selectedTokenProviderId.value =
    localStorage.getItem("selectedProvider") || "";
}

async function loadTokenProviders() {
  syncSelectedTokenProvider();
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

function openCreateProjectDialog() {
  editingProject.value = null;
  projectDialogError.value = "";
  projectDialogOpen.value = true;
}

function openEditProjectDialog(project: Project) {
  editingProject.value = project;
  projectDialogError.value = "";
  projectDialogOpen.value = true;
}

async function selectProject(projectId: string) {
  showChatWorkspace();
  selectedProjectId.value = projectId;
  currSessionId.value = "";
  replyTarget.value = null;
  await router.push(basePath());
  await loadProjectSessions(projectId);
  closeMobileSidebar();
}

async function loadProjectSessions(projectId = selectedProjectId.value) {
  if (!projectId) {
    projectSessions.value = [];
    return [];
  }
  const sessions = await getProjectSessions(projectId);
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
    await chatApi.updateSession(sessionId, {
      display_name: displayName,
    });
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
  sessionContextMenu.target = [event.clientX, event.clientY];
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
      getArchivedSessions(),
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
  const title = sessionTitle(session);
  const message = tm("conversation.confirmDelete", { name: title });
  if (!(await askForConfirmation(message, confirmDialog))) return;

  // 2026-09-01: session deleted — drop its "already loaded" dirty tag.
  clearSessionLoadedTag(session.session_id);
  const wasCurrent = currSessionId.value === session.session_id;
  await deleteSession(session.session_id);
  if (wasCurrent) {
    selectedProjectId.value = null;
    await router.push(basePath());
  }
}

async function selectProjectSession(sessionId: string) {
  selectedProjectId.value = null;
  await selectSession(sessionId);
}

/** 2026-09-01 (elecvoid243): project page "create new session" button —
 * creates a session immediately, links it to the current project and jumps
 * into the new conversation. Mirrors the send-message creation path so the
 * reverse map / spcode auto-load behave identically. */
async function createProjectSession() {
  const projectId = selectedProjectId.value;
  if (!projectId) return;
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
      const targetProject = selectedProject.value;
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
  // 2026-09-01: session deleted — drop its "already loaded" dirty tag.
  clearSessionLoadedTag(sessionId);
  await deleteSession(sessionId);
  if (projectId) {
    await loadProjectSessions(projectId);
  } else {
    await loadProjectSessions();
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
        formData.spcode_no_agentsmd,
        formData.spcode_no_codegraph,
      );
    } else {
      await createProject(
        formData.title,
        formData.emoji,
        formData.description,
        formData.workspace_type,
        formData.workspace_path,
        formData.spcode_no_agentsmd,
        formData.spcode_no_codegraph,
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
  showChatWorkspace();
  clearChoiceAttention(sessionId);
  selectedProjectId.value = null;
  currSessionId.value = sessionId;
  // Per-session drafts: swap in the target session's saved composer text
  // ("" when it has none) — unsent text never leaks across sessions.
  draft.value = chatDrafts.draftFor(sessionId);
  replyTarget.value = null;
  if (pushRoute && route.path !== `${basePath()}/${sessionId}`) {
    await router.push(`${basePath()}/${sessionId}`);
  }
  // 2026-08-27: always refresh, even for already-loaded sessions. The
  // loadedSessions skip left stale content when background turns (goal-loop,
  // primary runs from another device) advanced or finished
  // while the user was in another session — switching back showed a frozen
  // bubble plus everything from the switch moment onward. The merge inside
  // loadSessionMessages reconciles live records with the persisted snapshot.
  await loadSessionMessages(sessionId, true, false);
  // Timing-safe backstop for spcode auto-load: the currSessionId watcher can
  // fire before loadSessionMessages populates the session→project reverse-map,
  // so resolveProjectForAutoLoad would miss on that tick. Re-evaluate now that
  // the map is filled. silentLoad is idempotent, so a duplicate trigger (when
  // the watcher already succeeded) is a harmless no-op on the server.
  {
    const umo = resolveCurrentUmo(sessionId);
    if (umo) void tryAutoLoadSpcodeForSession(umo, sessionId);
  }
  scrollToBottom();
  closeMobileSidebar();
  await focusChatInput();
}

async function sendCurrentMessage() {
  // D13 guard: allow sending when draft is empty if there are staged
  // files OR file-review comments. Otherwise return.
  if (
    !canSend.value &&
    !stagedFiles.value.length &&
    fileComments.totalCount.value === 0 &&
    fileReferences.totalCount.value === 0
  ) {
    return;
  }

  sending.value = true;
  try {
    let sessionId = currSessionId.value;
    const targetProjectId = selectedProjectId.value;
    const targetProject = selectedProject.value;
    if (!sessionId) {
      sessionId = await newSession();
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
    // Concatenate user text + comment block + references block with
    // blank lines. The bot's first message will show the block headers
    // even when userText is empty.
    const text = [userText, commentText, referenceText]
      .filter(Boolean)
      .join("\n\n");

    // ZCode-style follow-up queue (2026-09-01): while an agent run is
    // active, hold text-only messages in the pending list above the
    // input instead of dispatching them. Flush triggers (tool-call
    // boundary / stream end / the "send now" button) dispatch them
    // later; see flushPendingFollowUps below. Messages with staged
    // attachments keep the legacy immediate-send path so media keeps
    // flowing through the backend's own capture pipeline.
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
    const outgoingParts = buildOutgoingParts(text);
    const selection = getSelectedProviderSelection();
    const { userRecord, botRecord } = createLocalExchange({
      sessionId,
      messageId,
      parts: outgoingParts,
    });
    updateTitleFromText(sessionId, text);

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
  // bottom, so live stick-to-bottom snaps must not fight the animation.
  shouldStickToBottom.value = false;
  const rows = messagesContainer.value?.querySelectorAll(".message-row");
  rows?.[index]?.scrollIntoView({ behavior: "smooth", block: "center" });
}

// Jump scroll lock: a marker jump animates the viewport away from the
// bottom and (when paging older history) prepends pages mid-flight. Both
// would otherwise be fought by the stick-to-bottom snaps and the
// scroll-top auto-load. The lock is released once the scroll animation
// has actually settled (no scroll events for a moment) — a fixed delay
// released it mid-animation on long jumps and deflected the landing.
const JUMP_SCROLL_SETTLE_MS = 160;
const JUMP_SCROLL_MAX_LOCK_MS = 2500;
let jumpScrollSettleTimer = 0;
let jumpScrollCapTimer = 0;
let jumpScrollContainer: HTMLElement | null = null;
let jumpScrollOnScroll: (() => void) | null = null;

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

/** Smooth-scroll to the row whose absolute data-message-index is target. */
function scrollToMessageIndex(targetIndex: number) {
  const row = messagesContainer.value?.querySelector(
    `[data-message-index="${targetIndex}"]`,
  ) as HTMLElement | null;
  if (!row) return false;
  row.scrollIntoView({ behavior: "smooth", block: "center" });
  return true;
}

/**
 * Jump to an absolute history index. When the target sits outside the loaded
 * window, page older history until it is covered, then scroll once. Both
 * directions wait out an in-flight older-page load first: its prepend
 * re-anchors scrollTop and re-keys the rows, which would cancel or deflect
 * a smooth scroll started before it.
 *
 * @returns True when the target row was found and scrolled to.
 */
async function jumpToIndex(targetIndex: number): Promise<boolean> {
  const sessionId = currSessionId.value;
  if (!sessionId || jumpInProgress.value) return false;
  shouldStickToBottom.value = false;
  const container = messagesContainer.value;
  if (container) beginJumpScrollLock(container);
  let scrolled = false;
  jumpInProgress.value = true;
  try {
    let guard = 0;
    while (guard++ < 100) {
      if (currSessionId.value !== sessionId) return false;
      const paging = historyPagingBySession[sessionId];
      const offset = historyOffsetBySession[sessionId] ?? 0;
      if (paging?.loadingOlder) {
        await new Promise((r) => setTimeout(r, 120));
        continue;
      }
      if (targetIndex >= offset) {
        // Let the post-prepend re-render flush so data-message-index
        // attributes match the new offsets before querying the row.
        await nextTick();
        scrolled = scrollToMessageIndex(targetIndex);
        return scrolled;
      }
      if (!paging?.hasMore) return false;
      await loadOlderMessages(sessionId);
      // No progress (e.g. failed request) — stop instead of spinning.
      if ((historyOffsetBySession[sessionId] ?? 0) >= offset) return false;
    }
    scrolled = scrollToMessageIndex(targetIndex);
    return scrolled;
  } finally {
    jumpInProgress.value = false;
    // On success the lock is owned by the settle listener; a failed
    // attempt (row missing, history exhausted, session switched) must
    // release it right away.
    if (!scrolled) endJumpScrollLock();
  }
}

function onDotClick(marker: {
  id: string | number;
  index: number;
}) {
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
  // Detect native scrollbar width so the yellow strip sits flush against
  // its LEFT edge (no gap, no overlap). offsetWidth includes the scrollbar;
  // clientWidth does not.
  const scrollbarWidth = container.offsetWidth - container.clientWidth;
  stripRightOffset.value = scrollbarWidth > 0 ? scrollbarWidth : 0;
  const scrollable = container.scrollHeight - container.clientHeight;
  if (scrollable <= 0) {
    scrollMarkers.value = [];
    return;
  }
  // Session-wide marker index: one dot per user message, positioned by its
  // absolute index so the strip covers the whole session even while the
  // history window only holds the newest pages. Inherited (branched-source)
  // markers render red and are hidden while the branch history is
  // collapsed — matching the visible rows.
  const markerIndex = currSessionId.value
    ? sessionMarkersBySession[currSessionId.value]
    : null;
  if (markerIndex?.loaded && markerIndex.markers.length) {
    const total = Math.max(1, markerIndex.totalMessages);
    scrollMarkers.value = markerIndex.markers
      .filter((m) => !m.inherited || !branchHistoryCollapsed.value)
      .map((m) => ({
        id: m.id,
        index: m.index,
        topPct: total > 1 ? (m.index / (total - 1)) * 100 : 0,
        preview: m.snippet || "…",
        inherited: m.inherited,
      }));
    return;
  }
  // Fallback: measure the loaded rows (marker index failed to load).
  const rows = container.querySelectorAll(".message-row");
  const containerRect = container.getBoundingClientRect();
  const markers: Array<{
    id: string | number;
    index: number;
    topPct: number;
    preview: string;
    inherited: boolean;
  }> = [];
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
      topPct: (offsetTop / scrollable) * 100,
      preview: truncate(plainTextFromMessage(msg), 40),
      inherited: row.classList.contains("inherited-row"),
    });
  }
  scrollMarkers.value = markers;
}

function onBranchToggle(collapsed: boolean) {
  // Branch history expand/collapse only toggles v-show inside
  // ChatMessageList, so neither the messages watcher nor the container
  // ResizeObserver fires. Track the state for the marker filter and
  // recompute markers after the layout settles.
  branchHistoryCollapsed.value = collapsed;
  nextTick(() => updateScrollMarkers());
}

function onStripClick(event: MouseEvent) {
  const container = messagesContainer.value;
  if (!container) return;
  const strip = event.currentTarget as HTMLElement;
  const rect = strip.getBoundingClientRect();
  const pct = (event.clientY - rect.top) / rect.height;
  // Full-session marker index: treat a background click as a jump to the
  // proportional absolute index (paging older history on demand).
  const markerIndex = currSessionId.value
    ? sessionMarkersBySession[currSessionId.value]
    : null;
  if (markerIndex?.loaded && markerIndex.totalMessages > 1) {
    const target = Math.round(pct * (markerIndex.totalMessages - 1));
    void jumpToIndex(target);
    return;
  }
  container.scrollTop = pct * (container.scrollHeight - container.clientHeight);
}

async function onDotEnter(text: string, event: MouseEvent) {
  const strip = (event.currentTarget as HTMLElement).closest(
    ".scroll-marker-strip",
  );
  if (!strip) return;
  const stripRect = strip.getBoundingClientRect();
  dotTooltip.text = text;
  // Anchor tooltip's RIGHT edge 8px to the left of the strip.
  // Tooltip grows LEFT from the strip, so it never overflows the right
  // page boundary (strip is always at the panel's right edge).
  dotTooltip.right = window.innerWidth - stripRect.left + 8;
  dotTooltip.y = event.clientY - 20;
  dotTooltip.visible = true;
  await nextTick();
  const el = dotTooltipRef.value;
  if (!el) return;
  // Determine available horizontal space: from the tooltip's right edge
  // to the chat panel's left edge (NOT viewport's left edge), so the
  // tooltip can use the full chat panel width when the strip sits at the
  // viewport's right edge (no sidebar scenario).
  const stack = strip.closest(".conversation-stack") as HTMLElement | null;
  const stackRect = stack?.getBoundingClientRect();
  const minLeft = (stackRect?.left ?? 16) + 16;
  const tooltipRightEdgeX = stripRect.left - 8;
  const maxAllowedWidth = Math.max(120, tooltipRightEdgeX - minLeft);
  const actualWidth = el.offsetWidth;
  if (actualWidth > maxAllowedWidth) {
    el.style.maxWidth = `${maxAllowedWidth}px`;
    const inner = el.querySelector(".scroll-dot-tooltip-text") as HTMLElement;
    if (inner) inner.style.whiteSpace = "normal";
  }
  // Vertical clamp: keep tooltip fully on-screen vertically.
  const tooltipHeight = el.offsetHeight;
  let y = event.clientY - 20;
  const maxY = window.innerHeight - tooltipHeight - 16;
  if (y > maxY) y = Math.max(16, maxY);
  if (y < 16) y = 16;
  dotTooltip.y = y;
}

function onDotMove(event: MouseEvent) {
  const el = dotTooltipRef.value;
  if (el) {
    const tooltipHeight = el.offsetHeight;
    let y = event.clientY - 20;
    const maxY = window.innerHeight - tooltipHeight - 16;
    if (y > maxY) y = Math.max(16, maxY);
    if (y < 16) y = 16;
    dotTooltip.y = y;
  } else {
    dotTooltip.y = event.clientY - 20;
  }
}

function onDotLeave() {
  dotTooltip.visible = false;
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
    const result = await editMessage(
      currSessionId.value,
      target,
      messageEditDraft.value,
    );
    cancelMessageEdit();

    if (result.needsRegenerate && result.truncatedAfterMessage) {
      const selection = getSelectedProviderSelection();
      continueEditedMessage({
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
  message.threads = [];
  const effectiveSelection = selection ?? getSelectedProviderSelection();
  await regenerateMessage(
    currSessionId.value,
    message,
    effectiveSelection?.providerId || "",
    effectiveSelection?.modelName || "",
    enableStreaming.value,
    getCurrentThinkingEffort(),
  );
}

async function handleBranch(message: ChatRecord) {
  const sessionId = currSessionId.value;
  if (!sessionId || message.id == null) return;
  try {
    const response = await chatApi.branchMessage(sessionId, message.id);
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
    const response = await chatApi.createThread({
      session_id: currSessionId.value,
      parent_message_id: message.id,
      selected_text: threadSelection.selectedText,
    });
    if (response.data?.status !== "ok") {
      toast.error(response.data?.message || tm("thread.createFailed"));
      return;
    }
    const thread = response.data?.data as ChatThread | undefined;
    if (!thread) {
      toast.error(tm("thread.createFailed"));
      return;
    }
    message.threads = message.threads || [];
    if (!message.threads.some((item) => item.thread_id === thread.thread_id)) {
      message.threads.push(thread);
    }
    openThreadPanel(thread);
    window.getSelection()?.removeAllRanges();
  } catch (error) {
    toast.error(
      isAxiosError(error)
        ? error.response?.data?.message || error.message
        : tm("thread.createFailed"),
    );
    console.error("Failed to create thread:", error);
  } finally {
    threadSelection.visible = false;
  }
}

function openThreadPanel(thread: ChatThread) {
  chatHeader.SET_WORKSPACE_FILES_OPEN(false);
  reasoningPanelOpen.value = false;
  activeReasoningTarget.value = null;
  refsSidebarOpen.value = false;
  activeThread.value = thread;
  threadPanelOpen.value = true;
}

function openRefsSidebar(refs: unknown) {
  chatHeader.SET_WORKSPACE_FILES_OPEN(false);
  threadPanelOpen.value = false;
  activeThread.value = null;
  reasoningPanelOpen.value = false;
  activeReasoningTarget.value = null;
  selectedRefs.value =
    refs && typeof refs === "object" ? (refs as Record<string, unknown>) : null;
  refsSidebarOpen.value = true;
}

function openReasoningPanel(payload: {
  message: ChatRecord;
  blockIndex: number;
  // 2026-08-11 file-change visibility: optional locate target when the
  // user clicked a file-change chip on the reasoning bar.
  callId?: string;
}) {
  chatHeader.SET_WORKSPACE_FILES_OPEN(false);
  threadPanelOpen.value = false;
  activeThread.value = null;
  refsSidebarOpen.value = false;
  selectedRefs.value = null;
  closeTodoMenu();
  chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
  gitDiffSidebarOpen.value = false;
  activeReasoningTarget.value = payload;
  reasoningPanelOpen.value = true;
}

function openGitDiffSidebar(): void {
  // Mutual exclusion: close every other sidebar before opening the
  // Git Diff sidebar. The watch in GitDiffSidebar will also auto-close
  // the sidebar when the underlying spcode project is unloaded.
  chatHeader.SET_WORKSPACE_FILES_OPEN(false);
  threadPanelOpen.value = false;
  activeThread.value = null;
  reasoningPanelOpen.value = false;
  activeReasoningTarget.value = null;
  refsSidebarOpen.value = false;
  selectedRefs.value = null;
  closeTodoMenu();
  chatHeader.SET_GOAL_SIDEBAR_OPEN(false);
  gitDiffSidebarOpen.value = true;
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

/** summary bar 出现时,如果持久化的位置已超出当前窗口, 则重置居中。
 *  同时: 快照被清空(todo_clear / 全删光)时同步关闭悬浮菜单,
 *  避免出现"bar 没了但菜单还开着显示空状态"的尴尬。
 */
watch(
  currentTodoSnapshot,
  (snap) => {
    // 1) 快照为 null → 关菜单 (清空/全删空场景)
    if (snap === null && todoMenuOpen.value) {
      closeTodoMenu();
    }
    // 2) 快照非空且 bar 位置已确定 → 位置越界时回弹
    if (!snap || todoBarPos.value === null) return;
    nextTick(() => {
      const main = document.querySelector(".chat-main") as HTMLElement | null;
      const bar = document.querySelector(
        ".todo-summary-bar",
      ) as HTMLElement | null;
      if (!main || !bar) return;
      const mainRect = main.getBoundingClientRect();
      const barRect = bar.getBoundingClientRect();
      const clamped = clampBarPos(
        todoBarPos.value!.left,
        todoBarPos.value!.top,
        barRect,
        mainRect,
      );
      if (
        clamped.left !== todoBarPos.value!.left ||
        clamped.top !== todoBarPos.value!.top
      ) {
        todoBarPos.value = clamped;
      }
    });
  },
  { immediate: true },
);

// 与 RefsSidebar 互斥: 展开 todo 菜单 / 打开 Goal 抽屉时收起 refs;
// 反向亦然。todo 菜单与 Goal 抽屉互不相干 (浮层 vs 侧栏), 不互斥。
watch(todoMenuOpen, (open) => {
  if (open) refsSidebarOpen.value = false;
});
watch(goalSidebarOpen, (open) => {
  if (open) refsSidebarOpen.value = false;
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

/** 键盘焦点落在 bar 上时的快捷键声明(用于 a11y 屏幕阅读器)。
 *
 * 实际行为:
 * - Enter / Space  → 浏览器对 <button> 的默认行为 → 触发 @click → toggleTodoMenu()
 * - Arrow 方向键  → onTodoBarKeydown → 移动位置 8px
 */
const todoBarKeyShortcuts =
  "Enter Space ArrowLeft ArrowRight ArrowUp ArrowDown";

/** 键盘移动 bar 位置。Shift 加速为 32px/次;Home 复位到居中。 */
const TODO_BAR_KEY_STEP = 8;
function onTodoBarKeydown(e: KeyboardEvent) {
  // 防御:只有 bar 可见且有快照时才进入(理论上 v-if 已 guard,但 keydown 仍要防)
  if (!currentTodoSnapshot.value) return;

  // 方向键移动
  let dx = 0;
  let dy = 0;
  if (e.key === "ArrowLeft") dx = -1;
  else if (e.key === "ArrowRight") dx = 1;
  else if (e.key === "ArrowUp") dy = -1;
  else if (e.key === "ArrowDown") dy = 1;
  else if (e.key === "Home") {
    // 复位:清空位置让 CSS centered 样式接管
    todoBarPos.value = null;
    try {
      localStorage.removeItem(TODO_BAR_POS_KEY);
    } catch {
      /* ignore */
    }
    // 菜单跟随胶囊回居中位 (等 centered 样式应用后再量)
    nextTick(placeTodoMenu);
    e.preventDefault();
    return;
  } else {
    // 其它键不拦(让 Enter/Space 等透传给 button 默认行为)
    return;
  }

  // 阻止页面方向键滚动
  e.preventDefault();
  e.stopPropagation();
  const step = (e.shiftKey ? 4 : 1) * TODO_BAR_KEY_STEP;

  // 第一次方向键按下:如果位置未初始化,先按当前 CSS centered 位置算一个起点
  if (todoBarPos.value === null) {
    const bar = document.querySelector(
      ".todo-summary-bar",
    ) as HTMLElement | null;
    const main = document.querySelector(".chat-main") as HTMLElement | null;
    if (!bar || !main) return;
    const mainRect = main.getBoundingClientRect();
    const barRect = bar.getBoundingClientRect();
    const startLeft =
      mainRect.left + Math.max(16, (mainRect.width - barRect.width) / 2);
    const startTop = mainRect.top + 16;
    todoBarPos.value = clampBarPos(startLeft, startTop, barRect, mainRect);
  }

  const current = todoBarPos.value!;
  const bar = document.querySelector(".todo-summary-bar") as HTMLElement | null;
  const main = document.querySelector(".chat-main") as HTMLElement | null;
  if (!bar || !main) return;
  const mainRect = main.getBoundingClientRect();
  const barRect = bar.getBoundingClientRect();
  todoBarPos.value = clampBarPos(
    current.left + dx * step,
    current.top + dy * step,
    barRect,
    mainRect,
  );
  // 菜单跟随胶囊
  placeTodoMenu();
  // 持久化(与鼠标拖动 endDragTodoBar 共用同一 key,策略一致)
  try {
    localStorage.setItem(TODO_BAR_POS_KEY, JSON.stringify(todoBarPos.value));
  } catch {
    /* ignore quota / private mode */
  }
}

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
      threadPanelOpen.value = false;
      activeThread.value = null;
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

function handleMessagesScroll() {
  threadSelection.visible = false;
  const container = messagesContainer.value;
  if (!container) return;
  const distance =
    container.scrollHeight - container.scrollTop - container.clientHeight;
  shouldStickToBottom.value = distance < 80;
  // History windowing: reached the top boundary while older history exists
  // → load the next page (the loadOlderHistory anchor keeps the viewport).
  // Suppressed while a programmatic jump scrolls (see suppressHistoryAutoLoad).
  const paging = currSessionId.value
    ? historyPagingBySession[currSessionId.value]
    : null;
  if (
    container.scrollTop < 80 &&
    !suppressHistoryAutoLoad.value &&
    paging?.hasMore &&
    !paging.loadingOlder
  ) {
    void loadOlderHistory();
  }
}

/**
 * Prepend an older history page, anchoring the scroll height so the
 * viewport does not jump after the prepend.
 */
async function loadOlderHistory() {
  if (!currSessionId.value) return;
  const container = messagesContainer.value;
  const prevHeight = container ? container.scrollHeight : null;
  await loadOlderMessages(currSessionId.value);
  if (container && prevHeight !== null) {
    await nextTick();
    container.scrollTop += container.scrollHeight - prevHeight;
  }
}

function scrollToBottom() {
  nextTick(() => {
    const container = messagesContainer.value;
    if (!container) return;
    // Re-check: a snap scheduled before the flag flipped (e.g. by a marker
    // jump taking over the viewport) must not fire and cancel the jump
    // animation.
    if (!shouldStickToBottom.value) return;
    container.scrollTop = container.scrollHeight;
    shouldStickToBottom.value = true;
  });
}

async function focusChatInput() {
  await nextTick();
  window.requestAnimationFrame(() => {
    inputRef.value?.focusInput();
  });
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
// the input (usePendingFollowUps) instead of being dispatched. They are
// dispatched by three triggers:
// 1. onToolCallActivity — flushed at the tool-call boundary, so the
//    backend captures them and injects them into that tool's result
//    (the exact timing the old send-while-running path produced).
// 2. onStreamEnd — the run ended without consuming the queue; items
//    start new runs as normal messages (mirrors how the backend
//    activates unconsumed follow-up tickets).
// 3. The "send now" button on a pending card — stop the active run,
//    then dispatch; the queued text rides on top of full history as a
//    fresh request.
const pendingFollowUps = usePendingFollowUps();
const flushingFollowUps = new Set<string>();

async function flushPendingFollowUps(sessionId: string) {
  if (!sessionId || flushingFollowUps.has(sessionId)) return;
  const items = pendingFollowUps.takeAll(sessionId);
  if (!items.length) return;
  flushingFollowUps.add(sessionId);
  try {
    for (const item of items) {
      await sendQueuedFollowUp(sessionId, item.text);
    }
  } finally {
    flushingFollowUps.delete(sessionId);
  }
}

/** Interrupt the active run and immediately dispatch the queued
    follow-ups as fresh requests (equivalent to /stop + resend). */
async function flushPendingFollowUpsNow(sessionId: string | null) {
  if (!sessionId) return;
  const items = pendingFollowUps.takeAll(sessionId);
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
    }
  } finally {
    flushingFollowUps.delete(sessionId);
  }
}

async function sendQueuedFollowUp(sessionId: string, text: string) {
  const trimmed = text.trim();
  if (!trimmed) return;
  const messageId = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const outgoingParts = buildOutgoingParts(text);
  const selection = getSelectedProviderSelection();
  const { userRecord, botRecord } = createLocalExchange({
    sessionId,
    messageId,
    parts: outgoingParts,
  });
  updateTitleFromText(sessionId, text);
  sendMessageStream({
    sessionId,
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
}

function flushPendingFromInput() {
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
  --chat-content-width: 76%;
  /* 2026-07-22 widen-chat-column: previous 760 px cap on the chat
       column was the real bottleneck — .messages-list-shell and the
       input box both consumed this var, so widening only the inner
       .from-bot .message-stack had no visible effect on the chat bar.
       860 px aligns the outer shell with the inner bubble target so
       longer assistant replies actually get the room we wanted. */
  --chat-content-max-width: 860px;
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
  padding: 4px 88px 4px 10px;
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
  right: 4px;
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

.session-item:hover .session-actions,
.session-item:focus-within .session-actions {
  opacity: 1;
  pointer-events: auto;
  visibility: visible;
}

/* Branch meta (badge + jump-to-source) sits left of the hover actions and
   stays visible without hover. */
.session-item.has-branch-meta {
  padding-right: 132px;
}

.session-branch-meta {
  position: absolute;
  right: 92px;
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

/* Todo summary bar — 浮窗式 (position: fixed)
   初始位置: 顶部工具栏下方 64px 处的页面正中 (避开 50px v-app-bar + 14px buffer)
   拖动后: 改用 inline style (left/top px), 通过 clampBarPos 限制在 chat-main 内
   z-index 分层契约 (2026-07-26, elecvoid243):
   - 高于侧栏 fullscreen 层 (ReasoningSidebar / GitDiffSidebar: 1300)
   - 低于 Vuetify v-dialog (VDialog 默认 zIndex 2400, 每多一层 overlay +10),
     使对话框弹出时 scrim 盖住气泡, 气泡不可点击 */
.todo-summary-bar {
  position: fixed;
  z-index: 1400;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border: 1px solid rgba(var(--v-border-color), 0.18);
  border-radius: 999px;
  background: rgba(var(--v-theme-surface), 0.78);
  color: rgba(var(--v-theme-on-surface), 0.82);
  font-size: 12.5px;
  font-weight: 500;
  line-height: 1;
  cursor: grab;
  user-select: none;
  -webkit-user-select: none;
  transition:
    background 0.18s ease,
    border-color 0.18s ease,
    color 0.18s ease,
    box-shadow 0.18s ease;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  max-width: calc(100vw - 24px);
}
.todo-summary-bar:hover {
  background: rgba(var(--v-theme-primary), 0.1);
  border-color: rgba(var(--v-theme-primary), 0.35);
  color: rgb(var(--v-theme-on-surface));
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);
}
.todo-summary-bar--active {
  background: rgba(var(--v-theme-primary), 0.14);
  border-color: rgba(var(--v-theme-primary), 0.5);
  color: rgb(var(--v-theme-on-surface));
  box-shadow: 0 0 0 2px rgba(var(--v-theme-primary), 0.15);
}
.todo-summary-bar--dragging {
  cursor: grabbing;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  transition: none; /* 拖动中不要 transition 跟手 */
}
/* 初始居中状态: 拖动后会被 inline left/top 覆盖
   注意: top 必须大于 v-app-bar 的 50px,否则会被顶部工具栏遮挡;
   z-index 抬高到 1201 略高于 .v-toolbar 的 1200,作为防御。*/
.todo-summary-bar--centered {
  left: 50% !important;
  top: 60px !important;
  z-index: 1201;
  transform: translateX(-50%);
  animation: todo-bar-fade-in 0.2s ease;
}
.todo-summary-bar--gitdiff-fullscreen {
  z-index: 1200;
}

@keyframes todo-bar-fade-in {
  from {
    opacity: 0;
    transform: translateX(-50%) translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
}

/* 整体淡入/淡出: 跟随 currentTodoSnapshot 的 v-if 切换。
   故意只动 opacity,不碰 transform — 避免和 --centered 的
   translateX(-50%) 互相覆盖造成"漂"的感觉。
   leave 时短暂禁用 pointer-events,防止用户在淡出过程中误点。 */
.todo-bar-fade-enter-active,
.todo-bar-fade-leave-active {
  transition: opacity 0.2s ease;
}
.todo-bar-fade-enter-from,
.todo-bar-fade-leave-to {
  opacity: 0;
}
.todo-bar-fade-leave-to {
  pointer-events: none;
}
.todo-summary-icon {
  color: rgba(var(--v-theme-primary), 0.85);
  flex-shrink: 0;
}
.todo-summary-drag-handle {
  color: rgba(var(--v-theme-on-surface), 0.35);
  flex-shrink: 0;
  margin-right: 2px;
}
.todo-summary-text {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.todo-summary-progress {
  color: #b58400;
  font-weight: 500;
}
.todo-summary-circular {
  flex-shrink: 0;
  font-size: 9px;
  font-weight: 600;
}
.todo-summary-attention {
  flex-shrink: 0;
  margin-left: 2px;
}

@media (max-width: 760px) {
  .todo-summary-text {
    /* 移动端隐藏冗长文字, 只留进度环 + 图标 */
    display: none;
  }
  .todo-summary-drag-handle {
    display: none;
  }
  .todo-summary-menu {
    /* 窄屏收缩菜单宽度, placeTodoMenu 会按实测宽度夹取定位 */
    width: min(340px, calc(100vw - 24px));
  }
}

/* 悬浮菜单: position: fixed 的兄弟面板, 位置/最大高度由 placeTodoMenu
   的 inline style 决定。z-index 分层契约与胶囊一致:
   - 低于胶囊 (1400) 让胶囊保持可点;
   - 高于侧栏 fullscreen 层 (1300);
   - gitdiff 全屏时随胶囊一起降到 1200 档 (1190 < 1200)。 */
.todo-summary-menu {
  position: fixed;
  z-index: 1390;
  width: 340px;
  display: flex;
  flex-direction: column;
  padding: 12px 14px 8px;
  border: 1px solid rgba(var(--v-border-color), 0.18);
  border-radius: 12px;
  background: rgba(var(--v-theme-surface), 0.96);
  color: rgba(var(--v-theme-on-surface), 0.87);
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.16);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  overscroll-behavior: contain;
  /* 展开动画默认从胶囊方向 (下挂) 缩放 */
  transform-origin: top left;
}
.todo-summary-menu--gitdiff-fullscreen {
  z-index: 1190;
}
.todo-summary-menu--above {
  /* 上翻时动画锚点换到左下角 */
  transform-origin: bottom left;
}
.todo-menu-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.todo-menu-footer {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--chat-border, rgba(var(--v-border-color), 0.08));
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.45);
}
.todo-menu-footer-icon {
  color: rgba(var(--v-theme-on-surface), 0.35);
}

/* 展开动画: 缩放 + 淡入; 上翻 (--above) 时位移方向反向 */
.todo-menu-pop-enter-active,
.todo-menu-pop-leave-active {
  transition:
    opacity 0.16s ease,
    transform 0.16s ease;
}
.todo-menu-pop-enter-from,
.todo-menu-pop-leave-to {
  opacity: 0;
  transform: scale(0.96) translateY(-4px);
}
.todo-summary-menu--above.todo-menu-pop-enter-from,
.todo-summary-menu--above.todo-menu-pop-leave-to {
  transform: scale(0.96) translateY(4px);
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
    max-width: 100%;
  }

  .composer-shell {
    padding: 0;
  }
}

/* Scroll marker strip — overlays the native scrollbar track */
.scroll-marker-strip {
  position: absolute;
  top: 0;
  right: 0;
  width: 14px;
  cursor: pointer;
  z-index: 5;
  pointer-events: none;
}
.scroll-marker-strip .scroll-marker-loading {
  position: absolute;
  right: 8px;
  top: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 6px;
  background: rgba(var(--v-theme-surface), 0.95);
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.75);
  white-space: nowrap;
  pointer-events: auto;
}
.scroll-marker-strip.is-jumping .scroll-marker-dot {
  opacity: 0.35;
}
.scroll-marker-strip .scroll-marker-dot {
  position: absolute;
  right: 1px;
  width: 12px;
  height: 4px;
  border-radius: 2px;
  background: #f5c518;
  opacity: 0.75;
  pointer-events: auto;
  transition:
    width 0.18s ease,
    height 0.18s ease,
    opacity 0.2s ease,
    box-shadow 0.2s ease,
    border-color 0.2s ease;
  cursor: pointer;
  border: 1px solid rgba(0, 0, 0, 0.1);
}
.scroll-marker-strip .scroll-marker-dot--inherited {
  background: #e5484d;
}
.scroll-marker-strip .scroll-marker-dot:hover {
  opacity: 1;
  width: 24px; /* 2x width on hover, expands leftward (right edge pinned) */
  height: 6px;
  box-shadow: 0 0 0 3px rgba(124, 77, 255, 0.25);
  border-color: rgb(var(--v-theme-primary));
}
.is-dark .scroll-marker-strip .scroll-marker-dot {
  border-color: rgba(255, 255, 255, 0.15);
}
</style>

<!-- Teleporter tooltip: 全局样式 (非 scoped, 因 Teleport 到 body) -->
<style>
.scroll-dot-tooltip {
  max-width: 600px;
  padding: 8px 12px;
  background: #ffffff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  pointer-events: none;
  text-align: left;
  box-sizing: border-box;
}
.scroll-dot-tooltip.is-dark {
  background: #2d2d2d;
  border-color: #404040;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
}
.scroll-dot-tooltip-text {
  display: inline-block;
  font-size: 13px;
  color: #333;
  line-height: 1.5;
  white-space: nowrap;
  overflow: visible;
}
.scroll-dot-tooltip.is-dark .scroll-dot-tooltip-text {
  color: #e0e0e0;
}
</style>
