<template>
  <div
    class="input-area fade-in"
    :class="{ 'is-dark': isDark }"
    @dragover.prevent="handleDragOver"
    @dragleave.prevent="handleDragLeave"
    @drop.prevent="handleDrop"
  >
    <!-- Host slot (2026-11-07): lets the chat page pin status strips (the
         standing-goal bar) above the composer while inheriting the exact
         content width and padding the input uses. -->
    <slot name="above-status" />

    <!--
      Status row above the composer (2026-10-03 redesign, elecvoid243):
      right cluster = file-access-mode capsule + standalone workspace entry
      capsule. The workspace entry was briefly folded into the mode menu and
      is independent again so the menu stays purely about permissions.
    -->
    <div class="input-area__status-row">
      <div v-if="showSpcodeIndicator" class="input-area__status-row__left">
        <SpcodeProjectIndicator
          :consumer-only="isOrdinaryUser"
          @open-load-dialog="openLoadDialog"
          @work-without-project="handleProjectLoadSubmit({ mode: 'unload', legacyText: `${wakePrefixes[0] || '/'}project unload` })"
          @open-codegraph-dialog="openCodegraphLoadDialog"
        />
      </div>
      <div class="input-area__status-row__right">
        <div class="input-area__status-row__chips-stack">
          <!-- Managed background shell sessions (2026-10-05): lives in the
               composer status row next to the other ambient chips; the
               component hides itself when no sessions exist. -->
          <ShellSessionIndicator :conversation-id="sessionId" />
          <FileAccessModeChip v-if="!isOrdinaryUser"
            :umo="currentSessionUmo"
            @change="handleFileAccessModeChange"
          />
          <!-- Workspace entry — standalone again (2026-10-03, elecvoid243):
               the permission menu governs writes, this opens the read view.
               Without a project, the same entry opens host files and the Agent browser.
               Icon-only below the 768px breakpoint. -->
          <GitDiffChip
            v-if="
              showWorkspaceChip &&
              (spcodeStatus.status.value.loaded || sessionId)
            "
            :browser-only="!spcodeStatus.status.value.loaded"
            :active="props.workspacePanelOpen"
            @toggle-diff-sidebar="emit('toggle-diff-sidebar')"
          />
        </div>
        <!--
              Pending inline file-comments chip. Hidden on mobile
              (< md breakpoint) to keep the input row uncluttered.
              The main button opens the preview dialog; the inline
              ✕ button (visible only on chip hover) clears all
              comments via the confirmDialog plugin.
            -->
        <div
          v-if="showFileComments && fileComments.totalCount.value > 0"
          class="comment-count-chip d-none d-md-flex"
          :class="{ 'comment-count-chip--hovered': chipHovered }"
          @mouseenter="chipHovered = true"
          @mouseleave="chipHovered = false"
        >
          <button
            type="button"
            class="comment-count-chip__main"
            :aria-label="
              tm(
                'spcodeProjectLoad.fileBrowser.comment.previewDialog.openWithCount',
                { count: fileComments.totalCount.value },
              )
            "
            @click="openPreview"
          >
            <v-icon size="14" start>mdi-comment-text-outline</v-icon>
            {{
              tm("spcodeProjectLoad.fileBrowser.comment.countLabel", {
                count: fileComments.totalCount.value,
              })
            }}
            <v-tooltip activator="parent" location="top">
              {{ tm("spcodeProjectLoad.fileBrowser.comment.countTooltip") }}
            </v-tooltip>
          </button>
          <button
            v-if="chipHovered"
            type="button"
            class="comment-count-chip__clear"
            :aria-label="
              tm('spcodeProjectLoad.fileBrowser.comment.chip.clearAll')
            "
            @click.stop="onRequestClearAll"
          >
            <v-icon size="14">mdi-close</v-icon>
          </button>
        </div>
      </div>
    </div>
    <!--
      ZCode-style pending follow-up queue (2026-09-01): messages sent
      while an agent run is active are held here instead of being
      dispatched. Each card exposes two explicit actions (2026-09-17):
      "追加" hands the message to the running turn right away — the
      backend injects it at the end of that turn's next tool call (the
      legacy follow-up timing), or it starts a fresh turn when the run
      ends first; "强制打断" stops the active run and resends as fresh
      requests.

      Both also have a composer shortcut (2026-09-18), active only while
      the input is empty — any typed text keeps the normal send path:
      Enter confirms the oldest queued message, Ctrl/Cmd+Enter interrupts.

      Nothing is dispatched automatically at a tool-call boundary
      anymore: that made the card flash by unread whenever the model
      happened to call a tool right after the user typed. Items still
      queued when the run ends are dispatched as normal messages by
      Chat.vue (see flushPendingFollowUps).
    -->
    <!-- 2026-11-07 (elecvoid243): one tray for the whole follow-up queue.
         Append / interrupt act on every queued item, so they live once in the
         header; rows only keep their per-item edit / delete. Shortcut hints
         sit under each action, and the queue is entered one message at a time
         with Enter (see handleKeyDown). -->
    <transition
      v-if="props.sessionId && pendingFollowUpItems.length"
      name="followup"
    >
      <section
        class="pending-follow-ups"
        :aria-label="tm('input.followUpTitle')"
      >
        <div class="pending-follow-ups__head">
          <div class="pending-follow-ups__title">
            <v-icon size="14" class="pending-follow-ups__clock"
              >mdi-clock-outline</v-icon
            >
            <span>{{ tm("input.followUpTitle") }}</span>
            <span class="pending-follow-ups__count" aria-live="polite">{{
              pendingFollowUpItems.length
            }}</span>
          </div>
          <div class="pending-follow-ups__actions">
            <button
              type="button"
              class="pending-action pending-action--send"
              :aria-label="tm('input.followUpSend')"
              @click="emit('flushPending')"
            >
              <span class="pending-action__main">
                <v-icon size="14">mdi-arrow-up</v-icon>
                <span class="pending-action__label">{{
                  tm("input.followUpSend")
                }}</span>
              </span>
              <span class="pending-action__key">Enter</span>
            </button>
            <button
              type="button"
              class="pending-action pending-action--interrupt"
              :aria-label="tm('input.followUpInterrupt')"
              @click="emit('interruptPending')"
            >
              <span class="pending-action__main">
                <v-icon size="14">mdi-stop-circle-outline</v-icon>
                <span class="pending-action__label">{{
                  tm("input.followUpInterrupt")
                }}</span>
              </span>
              <span class="pending-action__key">Shift + Enter</span>
            </button>
          </div>
        </div>

        <ul class="pending-follow-ups__list">
          <li
            v-for="(item, index) in pendingFollowUpItems"
            :key="item.id"
            class="pending-follow-up-row"
          >
            <template v-if="editingFollowUpId === item.id">
              <span class="pending-follow-up-row__index">{{ index + 1 }}</span>
              <v-textarea
                v-model="editingFollowUpText"
                density="compact"
                variant="outlined"
                hide-details
                auto-grow
                rows="1"
                class="pending-follow-up-row__editor"
                @keydown.enter.prevent="saveFollowUpEdit(item.id)"
                @keydown.esc="editingFollowUpId = null"
              />
              <div class="pending-follow-up-row__edit-actions">
                <button
                  type="button"
                  class="pending-row-action"
                  @click="editingFollowUpId = null"
                >
                  {{ tm("input.cancel") }}
                </button>
                <button
                  type="button"
                  class="pending-row-action pending-row-action--primary"
                  @click="saveFollowUpEdit(item.id)"
                >
                  {{ tm("input.save") }}
                </button>
              </div>
            </template>
            <template v-else>
              <span class="pending-follow-up-row__index">{{ index + 1 }}</span>
              <span class="pending-follow-up-row__text" :title="item.text">{{
                item.text
              }}</span>
              <span class="pending-follow-up-row__actions">
                <button
                  type="button"
                  class="pending-row-icon"
                  :aria-label="tm('input.followUpEdit')"
                  @click="startFollowUpEdit(item)"
                >
                  <v-icon size="14">mdi-pencil-outline</v-icon>
                </button>
                <button
                  type="button"
                  class="pending-row-icon"
                  :aria-label="tm('input.followUpDelete')"
                  @click="removeFollowUp(item.id)"
                >
                  <v-icon size="14">mdi-trash-can-outline</v-icon>
                </button>
              </span>
            </template>
          </li>
        </ul>
      </section>
    </transition>
    <div
      class="input-container"
      :class="{
        'is-multiline': inputIsMultiline,
        'has-attachments': hasStagedAttachments,
      }"
      :style="{
        width: 'var(--chat-content-width, 80%)',
        maxWidth: 'var(--chat-content-max-width, 960px)',
        margin: '0 auto',
        border: isDark ? 'none' : '1px solid #e0e0e0',
        borderRadius: '24px',
        boxShadow: isDark ? 'none' : '0px 2px 2px rgba(0, 0, 0, 0.1)',
        backgroundColor: isDark ? '#2d2d2d' : '#fff',
        position: 'relative',
        transition: 'min-height 0.2s ease, padding 0.2s ease',
      }"
    >
      <!-- 拖拽遮罩：仅 sidebar 拖入时显示引用遮罩；原生文件拖入由
           Chat.vue 的全区域拖拽热区（useDragUpload）统一处理。 -->
      <transition name="fade">
        <div v-if="isDragging && dragKind === 'reference'" class="drop-overlay">
          <div class="drop-overlay-content">
            <v-icon size="48" color="primary">mdi-file-link-outline</v-icon>
            <span class="drop-text">{{ tm("input.dropToReference") }}</span>
          </div>
        </div>
      </transition>
      <!-- 引用预览区 -->
      <transition name="slideReply" @after-leave="handleReplyAfterLeave">
        <div class="reply-preview" v-if="props.replyTo && !isReplyClosing">
          <div class="reply-content">
            <v-icon size="small" class="reply-icon">mdi-reply</v-icon>
            "<span class="reply-text">{{ props.replyTo.selectedText }}</span
            >"
          </div>
          <v-btn
            @click="handleClearReply"
            class="remove-reply-btn"
            icon="mdi-close"
            size="x-small"
            color="grey"
            variant="text"
          />
        </div>
      </transition>

      <!-- 2026-08-09 drag-reference: staged file-reference chips. Each
           chip shows the basename (full path in the title tooltip) and
           removes itself on ✕. The block is appended to the outgoing
           message at send time and cleared on success (spec D3). -->
      <transition name="attachments">
        <div
          v-if="!isOrdinaryUser && fileReferences.totalCount.value > 0"
          class="references-preview"
        >
          <div
            v-for="r in fileReferences.references"
            :key="r.id"
            class="reference-chip"
            :title="r.path"
          >
            <v-icon size="14" class="reference-chip__icon">
              mdi-file-outline
            </v-icon>
            <span class="reference-chip__name">{{ r.name }}</span>
            <button
              type="button"
              class="reference-chip__remove"
              :aria-label="tm('input.references.removeAria', { name: r.name })"
              @click="fileReferences.removeReference(r.id)"
            >
              <v-icon size="12">mdi-close</v-icon>
            </button>
          </div>
        </div>
      </transition>

      <transition name="attachments">
        <div class="attachments-preview" v-if="hasStagedAttachments">
          <div
            v-for="(img, index) in stagedImagesUrl"
            :key="'img-' + index"
            class="attachment-card image-preview"
          >
            <img
              :src="img"
              class="preview-image"
              alt="attachment preview"
              @click="imagePreview = { visible: true, url: img }"
            />
            <v-btn
              @click="$emit('removeImage', index)"
              class="remove-attachment-btn"
              icon="mdi-close"
              size="x-small"
              color="error"
              variant="tonal"
            />
          </div>

          <div v-if="stagedAudioUrl" class="attachment-card audio-preview">
            <div class="attachment-icon attachment-icon--audio">
              <v-icon icon="mdi-microphone" size="24"></v-icon>
            </div>
            <span class="attachment-name">{{ tm("voice.recording") }}</span>
            <v-btn
              @click="$emit('removeAudio')"
              class="remove-attachment-btn"
              icon="mdi-close"
              size="x-small"
              color="error"
              variant="tonal"
            />
          </div>

          <div
            v-for="(file, index) in stagedFiles"
            :key="'file-' + index"
            class="attachment-card file-preview"
          >
            <div
              class="attachment-icon"
              :style="{ '--attachment-color': filePresentation(file).color }"
            >
              <v-icon :icon="filePresentation(file).icon" size="24"></v-icon>
              <span class="attachment-ext">{{
                filePresentation(file).label
              }}</span>
            </div>
            <span class="attachment-name">{{ file.original_name }}</span>
            <v-btn
              @click="$emit('removeFile', index)"
              class="remove-attachment-btn"
              icon="mdi-close"
              size="x-small"
              color="error"
              variant="tonal"
            />
          </div>

          <div
            v-for="(active, index) in activeUploads"
            :key="'active-' + index"
            class="attachment-card file-preview attachment-card--active"
          >
            <div
              class="attachment-icon"
              :style="{ '--attachment-color': activePresentation(active).color }"
            >
              <v-icon :icon="activePresentation(active).icon" size="24"></v-icon>
              <span class="attachment-ext">{{
                activePresentation(active).label
              }}</span>
            </div>
            <span class="attachment-name">{{ active.name }}</span>
            <span class="attachment-progress-text">{{ active.percent }}%</span>
            <v-btn
              @click="$emit('cancelActiveUpload', index)"
              class="cancel-active-btn"
              icon="mdi-close"
              size="x-small"
              color="grey-darken-1"
              variant="text"
            />
            <div class="attachment-progress-track">
              <v-progress-linear
                :model-value="active.percent"
                color="primary"
                height="3"
                rounded
              />
            </div>
          </div>

          <div
            v-for="(failed, index) in failedUploads"
            :key="'failed-' + index"
            class="attachment-card file-preview attachment-card--failed"
          >
            <div class="attachment-icon attachment-icon--failed">
              <v-icon icon="mdi-alert-circle-outline" size="24"></v-icon>
            </div>
            <span class="attachment-name" :title="failed.error">{{
              failed.name
            }}</span>
            <v-btn
              @click="$emit('retryFailedUpload', index)"
              class="remove-attachment-btn retry-attachment-btn"
              icon="mdi-refresh"
              size="x-small"
              color="primary"
              variant="tonal"
            />
            <v-btn
              @click="$emit('discardFailedUpload', index)"
              class="remove-attachment-btn"
              icon="mdi-close"
              size="x-small"
              color="error"
              variant="tonal"
            />
          </div>
        </div>
      </transition>

      <!--
        2026-08-16 skill-guide: pending one-shot skill nudges, shown above
        the composer exactly like staged uploads. Each badge mirrors a skill
        queued via POST /skill-guide/load; it is consumed when the message
        is sent (the plugin drains the queue on the next LLM request).

        2026-09-08 (elecvoid243): this is the single rendering for BOTH
        queue paths ("+" menu and the "/" palette). Chips wrap onto extra
        lines instead of being clipped inside the composer.
      -->
      <transition name="attachments">
        <div
          v-if="skillGuide.queued.value.length > 0"
          class="skill-guide-preview"
        >
          <span class="skill-guide-preview__label">
            <v-icon icon="mdi-lightbulb-on-outline" size="14"></v-icon>
            {{ tm("input.skillGuide.pendingLabel") }}
          </span>
          <div
            v-for="name in skillGuide.queued.value"
            :key="name"
            class="skill-guide-chip"
          >
            <span class="skill-guide-chip__name" :title="name">{{ name }}</span>
            <button
              type="button"
              class="skill-guide-chip__remove"
              :aria-label="tm('input.skillGuide.removeTitle', { name })"
              :title="tm('input.skillGuide.removeTitle', { name })"
              @click="skillGuide.toggleSkill(name)"
            >
              <v-icon icon="mdi-close" size="12"></v-icon>
            </button>
          </div>
        </div>
      </transition>

      <CommandSuggestion
        :visible="showCommandSuggestion"
        :commands="filteredCommands"
        :selected-index="selectedCommandIndex"
        :is-dark="isDark"
        @select="handleCommandSelect"
        @update-selected-index="selectedCommandIndex = $event"
      />

      <!--
        2026-10-03 composer redesign (elecvoid243): always-stacked layout.
        Row 1 = the text field alone (the old single-row layout crowded
        three selectors + four action buttons onto one line). Row 2 = a
        dedicated toolbar of ghost controls: left cluster is "+", the
        config-profile capsule and the thinking-effort capsule; right
        cluster is actions only (token ring, mic, send). The model
        selector was removed from the composer — switching lives in the
        page header (ProviderSelectMenu writes the same localStorage
        keys, so getCurrentSelection() below still resolves it).
      -->
      <div class="composer-row">
        <div class="input-field-shell">
          <input
            v-if="!inputIsMultiline"
            ref="inputField"
            v-model="localPrompt"
            @keydown="handleKeyDown"
            @input="handleInput"
            @compositionstart="handleCompositionStart"
            @compositionend="handleCompositionEnd"
            @compositioncancel="handleCompositionEnd"
            @blur="handleBlur"
            @paste="handlePaste"
            :disabled="disabled"
            :placeholder="props.placeholder || tm('input.placeholder')"
            class="chat-text-input"
            autocomplete="off"
            autocorrect="off"
            autocapitalize="sentences"
            spellcheck="false"
            type="text"
          />
          <textarea
            v-else
            ref="inputField"
            v-model="localPrompt"
            @keydown="handleKeyDown"
            @input="handleInput"
            @compositionstart="handleCompositionStart"
            @compositionend="handleCompositionEnd"
            @compositioncancel="handleCompositionEnd"
            @blur="handleBlur"
            @paste="handlePaste"
            :disabled="disabled"
            :placeholder="props.placeholder || tm('input.placeholder')"
            class="chat-textarea"
            autocomplete="off"
            autocorrect="off"
            autocapitalize="sentences"
            spellcheck="false"
          ></textarea>
        </div>
        <div class="composer-toolbar">
          <div class="composer-toolbar__left" :class="{ 'composer-toolbar__left--user': isOrdinaryUser }">
            <StyledMenu v-if="isOrdinaryUser" offset="8" location="top start" :close-on-content-click="false">
              <template #activator="{ props: activatorProps }">
                <v-btn v-bind="activatorProps" icon="mdi-plus" variant="text" class="composer-ghost-btn" />
              </template>
              <GoalMenuItem :session-id="sessionId || null" :disabled="disabled" />
              <SkillGuideMenuItem :session-id="sessionId || null" :disabled="disabled" />
              <v-list-item class="styled-menu-item" rounded="md" prepend-icon="mdi-microphone" :title="tm('voice.liveMode')" @click="emit('openLiveMode')" />
            </StyledMenu>
            <input
              type="file"
              ref="imageInputRef"
              @change="handleFileSelect"
              style="display: none"
              multiple
            />
            <v-btn
              v-if="isOrdinaryUser"
              icon="mdi-paperclip"
              variant="text"
              :aria-label="tm('input.upload')"
              @click="triggerImageInput"
            />
            <!-- "+" ghost button: upload / skill / project / config / streaming -->
            <StyledMenu v-if="!isOrdinaryUser"
              offset="8"
              location="top start"
              :close-on-content-click="false"
            >
              <template v-slot:activator="{ props: activatorProps }">
                <v-btn
                  v-bind="activatorProps"
                  icon="mdi-plus"
                  variant="text"
                  class="composer-ghost-btn"
                />
              </template>

              <!-- Upload Files -->
              <v-list-item
                class="styled-menu-item"
                rounded="md"
                @click="triggerImageInput"
              >
                <template v-slot:prepend>
                  <v-icon icon="mdi-file-upload" size="small"></v-icon>
                </template>
                <v-list-item-title>
                  {{ tm("input.upload") }}
                </v-list-item-title>
              </v-list-item>

              <!--
                2026-08-16 skill-guide: "手动加载 Skill" entry. Rendered as a
                regular "+" menu item; hovering/clicking it opens a SEPARATE
                compact popover with the session's skills (see
                SkillGuideMenuItem) — the popover is absolutely positioned, so
                it never changes the "+" menu's width. The plugin is gated on
                GET /skill-guide/active having answered once
                (useSkillGuide.available); queued skills are mirrored by the
                pending badges row above the composer.
              -->
              <SkillGuideMenuItem
                v-if="skillGuide.available.value"
                :session-id="sessionId || null"
                :is-group="sessionIsGroup"
                :disabled="disabled"
              />

              <!--
                2026-10-25 goal entry: "设定目标" menu item (kernel /goal
                discovery). Without a standing goal it opens the set-goal
                card (text + turn budget); with one it opens the GoalBar's
                edit dialog. State lives in the chatHeader store (pushed by
                useSessionGoal), never here.
              -->
              <GoalMenuItem :session-id="sessionId || null" :disabled="disabled" />
              <v-list-item class="styled-menu-item" rounded="md" prepend-icon="mdi-microphone" :title="tm('voice.liveMode')" @click="emit('openLiveMode')" />

              <!--
                spcode project load trigger (lives inside the + menu's
                popover slot, which is mounted lazily). It only emits
                "open"; the dialog itself is mounted at the ChatInput
                level in <ProjectLoadDialog/> below so it survives the
                menu's lifecycle and is reachable from the chip too.
              -->
              <ProjectLoadMenuItem
                :commands="allCommands"
                @open="openProjectLoadDialog"
              />

              <!-- Config Selector in Menu (state lives in the shared
                   useChatConfigSelection singleton; the composer capsule
                   renders the same selection). -->
              <ConfigSelector variant="menu" />

              <!-- Streaming Toggle in Menu -->
              <v-list-item
                class="styled-menu-item"
                rounded="md"
                @click="$emit('toggleStreaming')"
              >
                <template v-slot:prepend>
                  <v-icon icon="mdi-lightning-bolt" size="small"></v-icon>
                </template>
                <v-list-item-title>
                  {{
                    enableStreaming
                      ? tm("streaming.enabled")
                      : tm("streaming.disabled")
                  }}
                </v-list-item-title>
              </v-list-item>
            </StyledMenu>
            <span class="composer-toolbar__divider" aria-hidden="true"></span>
            <!-- Config profile quick-switch capsule: its label is the live
                 current config profile; switching is one click. -->
            <ConfigSelector variant="chip" />
            <!-- Thinking effort capsule: renders the active preset (a level
                 list or a free numeric track). Presets live in
                 cmd_config.json; only config editors may rewrite them. -->
            <ThinkingEffortChip
              v-model="thinkingEffort"
              :levels="userEffortLevels"
              :mode="effortMode"
              :slider="effortSlider"
              :preset-name="activeEffortPreset?.name ?? ''"
              :presets="effortSettings.presets"
              :active-preset="activeEffortPreset?.id || ''"
              @select-preset="selectEffortPreset"
              :can-edit="chatUiCanEdit"
              @edit="effortLevelsDialogOpen = true"
            />
            <!-- Click-to-send phrases are editable by their account owner. -->
            <QuickMessagesChip
              :items="quickMessageItems"
              :can-edit="true"
              @send="handleQuickMessageSend"
              @edit="quickMessagesDialogOpen = true"
            />
          </div>
          <div class="composer-toolbar__right">
            <v-progress-circular
              v-if="!isOrdinaryUser && disabled && !mobile"
              indeterminate
              size="16"
              class="mr-1"
              width="1.5"
            />
            <!-- 2026-10-08 (elecvoid243): context compression notice, in the
                 actions cluster immediately left of the token-usage ring. The
                 slot span stays mounted so the tooltip always has an
                 activator; the readout itself is transitioned in and out.
                 2026-10-18: borderless text readout — in this toolbar only
                 actionable things may look like chips. -->
            <ChatContextUsage :token-usage="tokenUsage" :compression-notice="compressionNotice" :is-dark="isDark" />
            <v-btn
              @click="handleRecordClick"
              icon
              variant="text"
              class="composer-ghost-btn"
            >
              <v-icon
                :icon="isRecording ? 'mdi-stop-circle' : 'mdi-microphone'"
                variant="text"
                plain
              ></v-icon>
              <v-tooltip activator="parent" location="top">
                {{
                  isRecording ? tm("voice.speaking") : tm("voice.startRecording")
                }}
              </v-tooltip>
            </v-btn>
            <v-btn
              icon
              v-if="isRunning && !canSend"
              @click="$emit('stop')"
              variant="tonal"
              class="send-btn input-action-btn"
            >
              <v-icon icon="mdi-stop" variant="text" plain></v-icon>
              <v-tooltip activator="parent" location="top">
                {{ tm("input.stopGenerating") }}
              </v-tooltip>
            </v-btn>
            <v-tooltip location="top" :disabled="!sendLocked">
              <template #activator="{ props: tipProps }">
                <span v-bind="tipProps" class="send-btn-wrap">
                  <v-btn
                    v-if="!(isRunning && !canSend)"
                    @click="handleSendClick"
                    :aria-label="tm('input.send')"
                    icon="mdi-arrow-up"
                    variant="tonal"
                    :disabled="!canSend || sendLocked"
                    class="send-btn input-action-btn"
                  />
                </span>
              </template>
              <span>{{ tm("spcodeProjectLoad.indicator.sendLocked") }}</span>
            </v-tooltip>
          </div>
        </div>
      </div>
    </div>

    <!--
      The spcode project-load dialog. Mounted at the ChatInput level
      (NOT inside the + menu's popover slot) so it survives the
      menu's lazy-mount lifecycle. The chip in the status row and the
      + menu's trigger both call this dialog's `openLoadDialog()` to
      show the same UI. `v-dialog` teleports its content to <body>,
      so the DOM placement here is purely for component lifetime.
    -->
    <ProjectLoadDialog v-if="!isOrdinaryUser"
      ref="projectLoadDialogRef"
      :wake-prefixes="wakePrefixes"
      @submit="handleProjectLoadSubmit"
    />

    <ProjectLoadDialog
      ref="codegraphLoadDialogRef"
      :wake-prefixes="wakePrefixes"
      command-mode="codegraph"
      @submit="handleProjectLoadSubmit"
    />

    <CommentsPreviewDialog
      v-model="previewDialogOpen"
      :groups="previewGroups"
      @delete-comment="onDeleteComment"
      @request-clear-all="onRequestClearAll"
    />

    <ThinkingEffortLevelsDialog
      v-model="effortLevelsDialogOpen"
      :presets="effortSettings.presets"
      :active-preset="effortSettings.active_preset"
      @save="handleEffortEditorSave"
    />

    <QuickMessagesDialog
      v-model="quickMessagesDialogOpen"
      :items="quickMessageItems"
      @save="handleQuickMessagesSave"
    />

    <v-overlay
      v-model="imagePreview.visible"
      class="image-preview-overlay"
      scrim="rgba(0, 0, 0, 0.86)"
      @click="imagePreview = { visible: false, url: '' }"
    >
      <img
        :src="imagePreview.url"
        class="preview-image-large"
        alt="preview"
        @click.stop
      />
    </v-overlay>
  </div>
</template>

<script setup lang="ts">
import {
  ref,
  computed,
  watch,
  nextTick,
  onMounted,
  onBeforeUnmount,
} from "vue";
import { useDisplay } from "vuetify";
import { useModuleI18n } from "@/i18n/composables";
import type { ThinkingEffort } from "@/composables/useMessages";
import { useCustomizerStore } from "@/stores/customizer";
import { usePendingFollowUps } from "@/composables/usePendingFollowUps";
import type { PendingFollowUp } from "@/composables/usePendingFollowUps";
import { isComposingEnter } from "@/utils/imeInput.mjs";
import { buildWebchatUmoDetails } from "@/utils/chatConfigBinding";
import { useAuthStore } from "@/stores/auth";
import { chatApi, commandApi } from "@/api/v1";
import type { CommandItem } from "@/components/extension/componentPanel/types";
import ConfigSelector from "./ConfigSelector.vue";
import { useProviderModelSelection } from "@/composables/useProviderModelSelection";
import { provideChatConfigSelection } from "@/composables/useChatConfigSelection";
import ThinkingEffortChip from "./ThinkingEffortChip.vue";
import ThinkingEffortLevelsDialog from "./ThinkingEffortLevelsDialog.vue";
import type { ThinkingEffortEditorPayload } from "./ThinkingEffortLevelsDialog.vue";
import QuickMessagesChip from "./QuickMessagesChip.vue";
import QuickMessagesDialog from "./QuickMessagesDialog.vue";
import type { QuickMessagesEditorPayload } from "./QuickMessagesDialog.vue";
import {
  DEFAULT_THINKING_EFFORT_SLIDER,
  type ThinkingEffortSliderConfig,
} from "@/composables/thinkingEffortSlider";
import {
  DEFAULT_EFFORT_VALUE,
  findActivePreset,
  normalizeEffortSettings,
  settleEffortValue,
  type ThinkingEffortLevel,
  type ThinkingEffortSettings,
} from "@/composables/thinkingEffortPresets";
import {
  normalizeQuickMessages,
  type QuickMessage,
} from "@/composables/quickMessages";
import StyledMenu from "@/components/shared/StyledMenu.vue";
import CommandSuggestion from "./CommandSuggestion.vue";
import {
  mergeSuggestions,
  normalizeCommandSearchText as normalizeCommandSearchTextBase,
  stripLeadingTriggerToken,
  stripWakePrefix as stripWakePrefixBase,
} from "./suggestionMerge";
import ProjectLoadMenuItem from "./ProjectLoadMenuItem.vue";
import ProjectLoadDialog from "./ProjectLoadDialog.vue";
import type { ProjectLoadSubmitPayload } from "./ProjectLoadDialog.vue";
import SpcodeProjectIndicator from "./SpcodeProjectIndicator.vue";
import FileAccessModeChip from "./FileAccessModeChip.vue";
import GitDiffChip from "./GitDiffChip.vue";
import ShellSessionIndicator from "./ShellSessionIndicator.vue";
import SkillGuideMenuItem from "./SkillGuideMenuItem.vue";
import GoalMenuItem from "./GoalMenuItem.vue";
import { useSkillGuide } from "@/composables/useSkillGuide";
import type { ContextCompressionEntry } from "@/composables/contextCompressionNotice";
import ChatContextUsage from "./ChatContextUsage.vue";
import CommentsPreviewDialog from "./CommentsPreviewDialog.vue";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { useSpcodeCodegraphStatus } from "@/composables/useSpcodeCodegraphStatus";
import { useTcMemoryStatus } from "@/composables/useTcMemoryStatus";
import { useSpcodeVivadoStatus } from "@/composables/useSpcodeVivadoStatus";
import { useSpcodeOperationProgress } from "@/composables/useSpcodeOperationProgress";
import {
  ProjectLoadError,
  useSpcodeSilentOps,
  clearSessionLoadedTag,
} from "@/composables/useSpcodeProjectAutoLoad";
import { useToastStore } from "@/stores/toast";
import { useFileComments } from "@/composables/useFileComments";
import { useFileReferences } from "@/composables/useFileReferences";
import { useConfirmDialog } from "@/utils/confirmDialog";
import { useSpcodeProjectLoad } from "@/composables/useSpcodeProjectLoad";
import { useFileAccessMode } from "@/composables/useFileAccessMode";
import type { FileAccessMode } from "@/composables/useFileAccessMode";
import { attachmentPresentation } from "./attachmentPresentation";
import type { Session } from "@/composables/useSessions";
import type { FailedUploadView, ActiveUploadView } from "@/composables/useMediaHandling";
import type { SuggestionCommand } from "./CommandSuggestion.vue";

interface StagedFileInfo {
  attachment_id: string;
  filename: string;
  original_name: string;
  url: string;
  type: string;
}

interface ReplyInfo {
  messageId: string | number;
  selectedText?: string;
}

interface TokenUsageInfo {
  used: number;
  limit: number;
  percent: number;
  tooltip: string;
}

interface Props {
  prompt: string;
  stagedImagesUrl: string[];
  stagedAudioUrl: string;
  stagedFiles?: StagedFileInfo[];
  failedUploads?: FailedUploadView[];
  activeUploads?: ActiveUploadView[];
  disabled: boolean;
  enableStreaming: boolean;
  isRecording: boolean;
  isRunning: boolean;
  sessionId?: string | null;
  /**
   * 2026-10-23 side-chat: session/UMO that owns the config-profile chip.
   * Defaults to ``sessionId``. A side panel passes the thread id here while
   * ``currentSession`` stays the main session for project/file-access chips.
   */
  configSessionId?: string | null;
  /**
   * 2026-10-23 side-chat: a side composer resolves the config route of its
   * own thread UMO but must not overwrite the main chat's stored default.
   */
  persistConfigSelection?: boolean;
  currentSession?: Session | null;
  ensureProjectSession?: () => Promise<string>;
  configId?: string | null;
  replyTo?: ReplyInfo | null;
  sendShortcut?: "enter" | "shift_enter";
  showProviderSelector?: boolean;
  tokenUsage?: TokenUsageInfo | null;
  /**
   * Last context compression of the session on screen; null after a session
   * switch. Chat.vue owns it because the notice arrives on the stream, this
   * component only renders and times it.
   */
  compressionNotice?: ContextCompressionEntry | null;
  placeholder?: string;
  /**
   * 2026-10-23 side-chat: the inline file-comment store is a main-session
   * singleton; a side composer must not surface the main chat's comments.
   */
  showFileComments?: boolean;
  /**
   * 2026-10-23 side-chat: the workspace entry opens the main workspace
   * sidebar, which would displace the side panel; side composers hide it.
   */
  showWorkspaceChip?: boolean;
  /** Git-diff / workspace sidebar is open — tints the workspace capsule. */
  workspacePanelOpen?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  sessionId: null,
  currentSession: null,
  configId: null,
  persistConfigSelection: true,
  stagedFiles: () => [],
  failedUploads: () => [],
  activeUploads: () => [],
  replyTo: null,
  sendShortcut: "shift_enter",
  showProviderSelector: true,
  tokenUsage: null,
  compressionNotice: null,
  showFileComments: true,
  showWorkspaceChip: true,
  workspacePanelOpen: false,
});

const emit = defineEmits<{
  "update:prompt": [value: string];
  // skillNames: one-shot skill nudges consumed by this send (2026-10-01,
  // elecvoid243) — Chat.vue bakes them into a "[Requested skills]" block
  // so history shows what was queued.
  send: [skillNames: string[]];
  "send-command": [command: string];
  "project-changed": [sessionId: string, path: string | null];
  stop: [];
  toggleStreaming: [];
  removeImage: [index: number];
  removeAudio: [];
  removeFile: [index: number];
  retryFailedUpload: [index: number];
  discardFailedUpload: [index: number];
  cancelActiveUpload: [index: number];
  startRecording: [];
  stopRecording: [];
  pasteImage: [event: ClipboardEvent];
  fileSelect: [files: FileList | File[]];
  // 2026-08-09 drag-reference (elecvoid243): sidebar file-browser drop.
  // The payload carries the remote absolute path + the basename the user
  // saw in the list. Parent (Chat.vue) adds a *reference* via
  // useFileReferences; paths are appended to the outgoing message text
  // at send time — nothing is uploaded.
  fileReferenceDrop: [payload: { path: string; name: string }];
  clearReply: [];
  openLiveMode: [];
  "toggle-diff-sidebar": [];
  // ZCode-style follow-up queue actions (2026-09-17): `flushPending`
  // dispatches the pending messages above the input right away — the
  // backend captures them into the active run and injects them at the
  // end of its next tool call (or they start a fresh turn when the run
  // ends first). `flushPendingOldest` does the same for the OLDEST
  // message only (composer Enter shortcut), so repeated presses confirm
  // the queue one message at a time. `interruptPending` stops the active
  // run and resends the pending messages as fresh requests.
  flushPending: [];
  flushPendingOldest: [];
  interruptPending: [];
}>();

const { tm } = useModuleI18n("features/chat");
const isOrdinaryUser = computed(() => useAuthStore().role === "user");

// ZCode-style pending follow-up queue (singleton store shared with
// Chat.vue, which owns the flush triggers and dispatch path).
const pendingFollowUps = usePendingFollowUps();
const pendingFollowUpItems = computed(() =>
  pendingFollowUps.itemsFor(props.sessionId),
);
const editingFollowUpId = ref<string | null>(null);
const editingFollowUpText = ref("");

function startFollowUpEdit(item: PendingFollowUp) {
  editingFollowUpId.value = item.id;
  editingFollowUpText.value = item.text;
}

function saveFollowUpEdit(id: string) {
  if (props.sessionId) {
    pendingFollowUps.updateText(props.sessionId, id, editingFollowUpText.value);
  }
  editingFollowUpId.value = null;
}

function removeFollowUp(id: string) {
  if (props.sessionId) pendingFollowUps.remove(props.sessionId, id);
}
const isDark = computed(
  () => useCustomizerStore().uiTheme === "PurpleThemeDark",
);

const inputField = ref<HTMLInputElement | HTMLTextAreaElement | null>(null);
const imageInputRef = ref<HTMLInputElement | null>(null);
const providerSelectorAvailable = ref(true);
const isReplyClosing = ref(false);
const isDragging = ref(false);

// Preset definitions remain shared. Ordinary conversations store their own
// provider-specific value and preset on the server; other composers retain
// their existing local/account selection behavior.
const effortSettings = ref<ThinkingEffortSettings>({
  active_preset: "",
  value: DEFAULT_EFFORT_VALUE,
  presets: [],
});
const quickMessageItems = ref<QuickMessage[]>([]);
// Both editors write into the same `chatui` section, so one permission bit
// covers them.
const chatUiCanEdit = ref(false);

const defaultThinkingEffortLevels = computed<ThinkingEffortLevel[]>(() => [
  { name: tm("input.thinkingEffortOptions.low"), value: "low" },
  { name: tm("input.thinkingEffortOptions.high"), value: "high" },
  { name: tm("input.thinkingEffortOptions.max"), value: "max" },
]);

const providerModelSelection = useProviderModelSelection();
const sessionEffortEnabled = computed(() => props.persistConfigSelection !== false);
const activeEffortPreset = computed(() => findActivePreset({
  ...effortSettings.value,
  active_preset: sessionEffortEnabled.value
    ? providerModelSelection.effortPreset.value ?? providerModelSelection.defaultEffortPreset.value
    : effortSettings.value.active_preset,
}));

// An empty preset list is a legitimate state (the user deleted them all):
// the row falls back to the localized built-in levels.
const userEffortLevels = computed<ThinkingEffortLevel[]>(() => {
  const levels = activeEffortPreset.value?.levels ?? [];
  return levels.length > 0 ? levels : defaultThinkingEffortLevels.value;
});

const effortMode = computed<"levels" | "slider">(
  () => activeEffortPreset.value?.mode ?? "levels",
);
const effortSlider = computed<ThinkingEffortSliderConfig>(
  () => activeEffortPreset.value?.slider ?? DEFAULT_THINKING_EFFORT_SLIDER,
);

const localThinkingEffort = ref<string>(DEFAULT_EFFORT_VALUE);
const thinkingEffort = computed({
  get: () => sessionEffortEnabled.value
    ? providerModelSelection.thinkingEffort.value ?? providerModelSelection.defaultThinkingEffort.value
    : localThinkingEffort.value,
  set: (value: string) => {
    if (sessionEffortEnabled.value) {
      void providerModelSelection.setThinkingEffort(value, activeEffortPreset.value?.id || "");
    } else localThinkingEffort.value = value;
  },
});
const savedEffortPreset = ref("");

/** Load the input-row settings once; a failure keeps the built-in defaults. */
async function loadChatUiSettings() {
  try {
    const response = await chatApi.getUiSettings();
    const data = response.data?.data ?? {};
    effortSettings.value = normalizeEffortSettings(data.thinking_effort);
    savedEffortPreset.value = effortSettings.value.active_preset;
    if (!props.sessionId && sessionEffortEnabled.value) {
      providerModelSelection.defaultThinkingEffort.value = effortSettings.value.value;
      providerModelSelection.defaultEffortPreset.value = effortSettings.value.active_preset;
    }
    quickMessageItems.value = normalizeQuickMessages(data.quick_messages);
    chatUiCanEdit.value = Boolean(data.can_edit);
    localThinkingEffort.value = settleEffortValue(
      effortSettings.value.value,
      activeEffortPreset.value,
    );
  } catch (error) {
    console.error("Failed to load ChatUI settings:", error);
  }
}

let effortValueTimer: number | null = null;

async function persistEffortValue(value: string, activePreset: string) {
  try {
    const response = await chatApi.updateThinkingEffortValue(value, activePreset);
    if (response.data?.status !== "ok") {
      throw new Error(response.data?.message || "update failed");
    }
    savedEffortPreset.value = response.data?.data?.active_preset || "";
    if (effortSettings.value.active_preset !== activePreset) return;
    effortSettings.value = normalizeEffortSettings(response.data?.data);
    if (thinkingEffort.value === value) thinkingEffort.value = effortSettings.value.value;
  } catch (error) {
    console.error("Failed to persist thinking-effort value:", error);
  }
}

// Dragging the slider emits one value per pixel, so selection changes are
// debounced into a single account preference write. The equality guard swallows the echo
// from a fresh load and from the server-normalized value written back after
// a save.
watch([thinkingEffort, () => effortSettings.value.active_preset], ([value, activePreset]) => {
  if (sessionEffortEnabled.value) return;
  if (value === effortSettings.value.value && activePreset === savedEffortPreset.value) return;
  if (effortValueTimer !== null) window.clearTimeout(effortValueTimer);
  effortValueTimer = window.setTimeout(() => {
    void persistEffortValue(value, activePreset);
  }, 500);
});

// Only an explicit preset switch settles the value. Loading a saved free-form
// provider value must never rewrite it just because a preset has changed.
function selectEffortPreset(id: string) {
  const preset = effortSettings.value.presets.find((item) => item.id === id) ?? null;
  const value = settleEffortValue(thinkingEffort.value, preset);
  if (sessionEffortEnabled.value) {
    void providerModelSelection.setThinkingEffort(value, id);
  } else {
    effortSettings.value.active_preset = id;
    localThinkingEffort.value = value;
  }
}

const effortLevelsDialogOpen = ref(false);
const quickMessagesDialogOpen = ref(false);

async function handleEffortEditorSave(payload: ThinkingEffortEditorPayload) {
  const nextPreset =
    payload.presets.find((preset) => preset.id === payload.activePreset) ?? null;
  try {
    const response = await chatApi.updateThinkingEffortPresets({
      presets: payload.presets,
      active_preset: payload.activePreset,
      // The new shape has to accept the current selection: levels fall back
      // to a level the preset actually offers, slider values land on the track.
      value: settleEffortValue(thinkingEffort.value, nextPreset),
    });
    if (response.data?.status !== "ok") {
      throw new Error(response.data?.message || "update failed");
    }
    effortSettings.value = normalizeEffortSettings(response.data?.data);
    thinkingEffort.value = effortSettings.value.value;
  } catch (error) {
    toastStore.add({
      message: tm("input.thinkingEffortSaveFailed"),
      color: "error",
    });
    console.error("Failed to save thinking-effort presets:", error);
  }
}

/**
 * Send a quick phrase through the normal send path: write it into the prompt
 * first, then let the existing "send" emit run, so stop / streaming / resend
 * state all behave exactly as if the text had been typed. The tick matters —
 * Chat.vue reads its own prompt ref (and the canSend guard) when handling the
 * event.
 */
function handleQuickMessageSend(content: string) {
  emit("update:prompt", content);
  void nextTick(() => sendMessage());
}

async function handleQuickMessagesSave(payload: QuickMessagesEditorPayload) {
  try {
    const response = await chatApi.updateQuickMessages({
      items: payload.items,
    });
    if (response.data?.status !== "ok") {
      throw new Error(response.data?.message || "update failed");
    }
    quickMessageItems.value = normalizeQuickMessages(response.data?.data);
  } catch (error) {
    toastStore.add({
      message: tm("input.quickMessagesSaveFailed"),
      color: "error",
    });
    console.error("Failed to save quick messages:", error);
  }
}

/** 2026-08-09 drag-reference: which drop overlay to show. "Files" drags
 *  upload; sidebar MIME drags reference. */
const dragKind = ref<"upload" | "reference">("upload");
const isComposing = ref(false);
const inputIsMultiline = ref(false);
const lastCompositionEndAt = ref<number | null>(null);
const longPasteThreshold = 10_000;
let dragLeaveTimeout: number | null = null;

// Inline comment-chip state. ChatInput owns the chip interaction
// surface; it reads the comment store directly (same singleton that
// FileBrowserFilePreview writes to) so it does not need a prop.
const fileComments = useFileComments();
// 2026-08-09 drag-reference (elecvoid243): sidebar file drops become
// references (not uploads). Same singleton pattern as fileComments —
// read directly, no prop. See useFileReferences.ts header.
const fileReferences = useFileReferences();
const previewDialogOpen = ref(false);
const chipHovered = ref(false);

/** 30 s polling timer for codegraph MCP status. */
let codegraphPollTimer: number | null = null;

/** Auto-close preview dialog when the last comment is cleared, e.g.
 *  after clearAll() or a session reset. Avoids a stale empty state
 *  inside an open dialog. */
watch(
  () => fileComments.totalCount.value,
  (n) => {
    if (n === 0) previewDialogOpen.value = false;
  },
);

const previewGroups = computed(() => fileComments.commentsByFile());

function openPreview(): void {
  previewDialogOpen.value = true;
  chipHovered.value = false;
}

function onDeleteComment(id: string): void {
  fileComments.deleteComment(id);
}

const confirmDialog = useConfirmDialog();
async function onRequestClearAll(): Promise<void> {
  const count = fileComments.totalCount.value;
  if (!confirmDialog) {
    // Plugin not registered; fall back to immediate delete.
    fileComments.clearAll();
    return;
  }
  const ok = await confirmDialog({
    title: tm("spcodeProjectLoad.fileBrowser.comment.confirmClear.title"),
    message: tm("spcodeProjectLoad.fileBrowser.comment.confirmClear.message", {
      count,
    }),
  });
  if (ok) {
    fileComments.clearAll();
  }
}

const imagePreview = ref({ visible: false, url: "" });

// Attachment removal revokes its blob URL; do not leave a stale preview open.
watch(
  [() => props.sessionId, () => props.stagedImagesUrl],
  ([sessionId, images], [previousSessionId]) => {
    if (
      imagePreview.value.visible &&
      (sessionId !== previousSessionId || !images.includes(imagePreview.value.url))
    ) {
      imagePreview.value = { visible: false, url: "" };
    }
  },
  { deep: true },
);

// 命令提示相关状态
const allCommands = ref<CommandItem[]>([]);
const showCommandSuggestion = ref(false);
const selectedCommandIndex = ref(0);
const commandSuggestionLoading = ref(false);
// Guards the skill auto-trigger against re-entrancy while the queue POST
// is in flight (the prompt is rewritten right after it resolves).
const skillAutoQueueBusy = ref(false);

// Template ref to the spcode project-load dialog. The dialog is
// mounted at the ChatInput level (outside any popover) so this ref
// is always populated as soon as `showSpcodeIndicator` is true. The
// chip's click handler and the + menu's trigger both call its
// exposed `openLoadDialog()` to surface the same dialog.
const projectLoadDialogRef = ref<{
  openLoadDialog: () => void;
  closeLoadDialog: () => void;
} | null>(null);

const codegraphLoadDialogRef = ref<{
  openLoadDialog: () => void;
  closeLoadDialog: () => void;
} | null>(null);

// Unified visibility gate for the spcode project-load entry points —
// the "加载项目" chip in this component AND the "+" popover menu's
// "加载项目目录" item (in :class:`ProjectLoadMenuItem`) — read from
// the same composable so the two can never disagree. The gate is the
// AND of:
//   1. the spcode plugin is currently enabled (`activated === true`),
//   2. at least one `/project*` command is registered.
// See :func:`useSpcodeProjectLoad` for the full rationale.
const { isProjectLoadAvailable, refreshPluginState } =
  useSpcodeProjectLoad(allCommands);
const showSpcodeIndicator = computed(() => isOrdinaryUser.value ? spcodeStatus.status.value.loaded : isProjectLoadAvailable.value);

// Singleton state for the file access mode chip (full / readonly /
// workspace), keyed per-umo by AstrBot core. Exposed here so the
// change handler can optimistically flip it for instant feedback.
// The authoritative refresh runs from the session watcher below and
// from Chat.vue's watchers so the chip converges with the backend
// on every session switch and stream end.
const fileAccessMode = useFileAccessMode();
// Full unified_msg_origin of the current session (backend state key), or
// null when no session is active. Feeds the chip's whitelist dialog.
const currentSessionUmo = computed<string | null>(() => {
  const session = props.currentSession;
  if (!session) return null;
  return buildWebchatUmoDetails(session.session_id, Boolean(session.is_group))
    .umo;
});
const wakePrefixes = ref<string[]>(["/"]);
const currentConfigId = ref((props.configId as string) || "default");

/** 检查文本是否以任意一个唤醒词前缀开头 */
function hasWakePrefix(text: string): boolean {
  return wakePrefixes.value.some((p) => text.startsWith(p));
}

/** 去掉文本开头匹配的任意唤醒词前缀，返回剥离后的文本 */
function stripWakePrefix(text: string): string {
  return stripWakePrefixBase(text, wakePrefixes.value);
}

function normalizeCommandSearchText(value: string) {
  return normalizeCommandSearchTextBase(value, wakePrefixes.value);
}

/** 从所有指令中展平获取启用的普通指令和子指令 */
const enabledCommands = computed(() => {
  const result: SuggestionCommand[] = [];
  const seen = new Set<string>();
  // 使用第一个唤醒词前缀作为指令的展示前缀
  const displayPrefix = wakePrefixes.value[0] || "/";

  function addCommand(cmd: CommandItem) {
    if (!cmd.enabled) return;
    if (cmd.type === "group") {
      // 指令组本身不加入，但其子指令加入
      cmd.sub_commands?.forEach(addCommand);
      return;
    }
    // 统一添加唤醒词前缀（子命令的 effective_command 如 "music play" 需要变成 "/music play"）
    const displayCmd = hasWakePrefix(cmd.effective_command)
      ? cmd.effective_command
      : `${displayPrefix}${cmd.effective_command}`;
    if (!seen.has(displayCmd)) {
      seen.add(displayCmd);
      result.push({
        handler_full_name: cmd.handler_full_name,
        effective_command: displayCmd,
        description: cmd.description,
        plugin_display_name: cmd.plugin_display_name,
        enabled: cmd.enabled,
        reserved: cmd.reserved,
      });
    }
    // 同时加入别名（别名也需要加上唤醒词前缀）
    cmd.aliases?.forEach((alias) => {
      const aliasBase = cmd.parent_signature
        ? `${cmd.parent_signature} ${alias}`
        : alias;
      const aliasKey = hasWakePrefix(aliasBase)
        ? aliasBase
        : `${displayPrefix}${aliasBase}`;
      if (!seen.has(aliasKey)) {
        seen.add(aliasKey);
        result.push({
          handler_full_name: cmd.handler_full_name,
          effective_command: aliasKey,
          description: cmd.description,
          plugin_display_name: cmd.plugin_display_name,
          enabled: cmd.enabled,
          reserved: cmd.reserved,
        });
      }
    });
  }

  allCommands.value.forEach(addCommand);
  return result;
});

/** 根据当前输入过滤候选指令（命令 + skill），见 suggestionMerge.ts */
const filteredCommands = computed(() => {
  const text = props.prompt;
  if (!text || !hasWakePrefix(text)) return [];
  return mergeSuggestions({
    commands: enabledCommands.value,
    skills: skillCommands.value,
    text,
    wakePrefixes: wakePrefixes.value,
  });
});

const localPrompt = computed({
  get: () => props.prompt,
  set: (value) => {
    // Suppress v-model sync during IME composition to avoid a reactive
    // feedback loop. Vue's :value binding overwrites the native textarea
    // DOM state mid-composition, which interferes with IME insertion at
    // non-terminal cursor positions (alternating character loss).
    // The final value is synced manually in handleCompositionEnd.
    if (!isComposing.value) emit("update:prompt", value);
  },
});

const sessionPlatformId = computed(
  () => props.currentSession?.platform_id || "webchat",
);
const sessionIsGroup = computed(() => Boolean(props.currentSession?.is_group));

// 2026-08-16 skill-guide: singleton state for the Skill Guide plugin.
// ChatInput owns `setSession` (session switch → re-fetch the active
// skill list for the new umo); the SkillGuideMenuItem and the pending
// badges row above the composer read the same refs, so the popover ✓
// marks and the badges can never diverge.
const skillGuide = useSkillGuide();

/**
 * 2026-09-08 (elecvoid243): skill pseudo-commands for the "/" palette.
 * Sources: session-effective skills + globally enabled skills (see
 * useSkillGuide.candidateSkills). A skill whose name collides with a real
 * command is dropped — the command wins.
 */
const skillCommands = computed<SuggestionCommand[]>(() => {
  const prefix = wakePrefixes.value[0] || "/";
  const commandNames = new Set(
    enabledCommands.value.map((cmd) =>
      normalizeCommandSearchText(cmd.effective_command),
    ),
  );
  return skillGuide.candidateSkills.value
    .filter((skill) => !commandNames.has(skill.name.toLowerCase()))
    .map((skill) => ({
      handler_full_name: `skill:${skill.name}`,
      effective_command: `${prefix}${skill.name}`,
      description: skill.description || "",
      plugin_display_name: null,
      enabled: true,
      reserved: false,
      kind: "skill" as const,
      queued: skillGuide.queued.value.includes(skill.name),
    }));
});

watch(
  () => [props.sessionId, sessionIsGroup.value] as const,
  async ([sessionId, isGroup]) => {
    if (!sessionId) {
      await skillGuide.setSession(null);
      return;
    }
    const umo = buildWebchatUmoDetails(sessionId, isGroup).umo;
    await skillGuide.setSession(umo);
  },
  { immediate: true },
);

const canSend = computed(() => {
  return (
    skillGuide.queued.value.length > 0 ||
    (props.prompt && props.prompt.trim()) ||
    props.stagedImagesUrl.length > 0 ||
    props.stagedAudioUrl ||
    (props.stagedFiles && props.stagedFiles.length > 0)
  );
});

const hasStagedAttachments = computed(() => {
  return (
    props.stagedImagesUrl.length > 0 ||
    props.stagedAudioUrl ||
    (props.stagedFiles && props.stagedFiles.length > 0) ||
    (props.failedUploads && props.failedUploads.length > 0) ||
    (props.activeUploads && props.activeUploads.length > 0)
  );
});

function filePresentation(file: StagedFileInfo) {
  return attachmentPresentation(file);
}

// In-flight uploads are typed by their local filename already; no need to
// wait for the server-side classification.
function activePresentation(active: ActiveUploadView) {
  return attachmentPresentation({ original_name: active.name });
}

// Ctrl+B 长按录音相关
const ctrlKeyDown = ref(false);
const ctrlKeyTimer = ref<number | null>(null);
const ctrlKeyLongPressThreshold = 300;

// 处理清除引用 - 触发关闭动画
function handleClearReply() {
  isReplyClosing.value = true;
}

// 动画完成后发送clearReply事件
function handleReplyAfterLeave() {
  emit("clearReply");
  isReplyClosing.value = false;
}

const { mobile } = useDisplay();

// Auto-resize textarea
function autoResize() {
  const el = inputField.value;
  if (!el) return;
  if (!(el instanceof HTMLTextAreaElement)) {
    const shouldExpand =
      localPrompt.value.includes("\n") ||
      (el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 4);
    if (shouldExpand) {
      const cursor = el.selectionStart ?? localPrompt.value.length;
      inputIsMultiline.value = true;
      nextTick(() => {
        inputField.value?.focus();
        inputField.value?.setSelectionRange(cursor, cursor);
        autoResize();
      });
    }
    return;
  }
  const isMobileViewport =
    typeof window !== "undefined" &&
    window.matchMedia("(max-width: 768px)").matches;
  const viewportHeight =
    typeof window !== "undefined" ? window.innerHeight : 900;
  const minHeight = isMobileViewport ? 56 : 52;
  const maxHeight = isMobileViewport
    ? Math.min(220, Math.round(viewportHeight * 0.42))
    : Math.min(420, Math.round(viewportHeight * 0.48));
  if (!localPrompt.value) {
    inputIsMultiline.value = false;
    el.style.height = minHeight + "px";
    return;
  }
  el.style.height = "auto";
  el.style.setProperty("min-height", "0", "important");
  const measuredHeight = el.scrollHeight;
  el.style.removeProperty("min-height");
  const computed = getComputedStyle(el);
  let lineHeight = parseFloat(computed.lineHeight);
  if (!Number.isFinite(lineHeight)) {
    lineHeight = parseFloat(computed.fontSize) * 1.2;
  }
  const paddingVertical =
    parseFloat(computed.paddingTop) + parseFloat(computed.paddingBottom);
  const shouldUseMultiline =
    localPrompt.value.includes("\n") ||
    measuredHeight > lineHeight + paddingVertical + 0.5;
  if (inputIsMultiline.value !== shouldUseMultiline) {
    const cursor = el.selectionStart ?? localPrompt.value.length;
    inputIsMultiline.value = shouldUseMultiline;
    nextTick(() => {
      inputField.value?.focus();
      inputField.value?.setSelectionRange(cursor, cursor);
      autoResize();
    });
    return;
  }
  el.style.height = shouldUseMultiline
    ? Math.min(Math.max(measuredHeight, minHeight), maxHeight) + "px"
    : minHeight + "px";
}

watch(
  () => props.prompt,
  (value) => {
    if (!value) {
      inputIsMultiline.value = false;
    }
    nextTick(autoResize);
  },
);

watch(inputIsMultiline, () => {
  nextTick(autoResize);
});

function handleKeyDown(e: KeyboardEvent) {
  // 命令提示激活时，拦截方向键和 Enter/Esc
  if (showCommandSuggestion.value && filteredCommands.value.length > 0) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      selectedCommandIndex.value =
        (selectedCommandIndex.value + 1) % filteredCommands.value.length;
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      selectedCommandIndex.value =
        (selectedCommandIndex.value - 1 + filteredCommands.value.length) %
        filteredCommands.value.length;
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const cmd = filteredCommands.value[selectedCommandIndex.value];
      if (cmd) {
        handleCommandSelect(cmd);
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      showCommandSuggestion.value = false;
      return;
    }
  }

  const isEnter = e.key === "Enter";
  if (!isEnter) {
    // Ctrl+B 录音
    if (e.ctrlKey && e.keyCode === 66) {
      e.preventDefault();
      if (ctrlKeyDown.value) return;

      ctrlKeyDown.value = true;
      ctrlKeyTimer.value = window.setTimeout(() => {
        if (ctrlKeyDown.value && !props.isRecording) {
          emit("startRecording");
        }
      }, ctrlKeyLongPressThreshold);
    }
    return;
  }

  if (isComposingEnter(e, isComposing.value, lastCompositionEndAt.value)) {
    return;
  }

  // 2026-09-18 (elecvoid243): follow-up queue shortcuts. With an empty
  // composer Enter has nothing to send (it would only start a newline), so
  // it confirms the OLDEST queued message instead — one Enter per message —
  // and Shift+Enter forces an interrupt (renamed from Ctrl/Cmd+Enter on
  // 2026-11-07 to match the hints under the tray's actions). Any typed text
  // keeps the normal send path, which may queue the message as yet another
  // follow-up. The project-op send lock still wins: the composer stays inert
  // while a load/unload rewrites the system prompt.
  if (
    !localPrompt.value.trim() &&
    skillGuide.queued.value.length === 0 &&
    !sendLocked.value &&
    pendingFollowUpItems.value.length
  ) {
    if (e.shiftKey) {
      e.preventDefault();
      emit("interruptPending");
      return;
    }
    if (!e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      emit("flushPendingOldest");
      return;
    }
  }

  const isSendHotkey =
    e.ctrlKey ||
    e.metaKey ||
    (props.sendShortcut === "enter" ? !e.shiftKey : e.shiftKey);

  if (isSendHotkey) {
    e.preventDefault();
    if (localPrompt.value.trim() === "/astr_live_dev") {
      emit("openLiveMode");
      localPrompt.value = "";
      return;
    }
    if (canSend.value && !sendLocked.value) {
      if (!isOrdinaryUser.value) applyOptimisticCodegraphStatus(localPrompt.value);
      sendMessage();
    }
    return;
  }

  if (!inputIsMultiline.value) {
    e.preventDefault();
    const target = e.target as HTMLInputElement;
    const start = target.selectionStart ?? localPrompt.value.length;
    const end = target.selectionEnd ?? start;
    localPrompt.value =
      localPrompt.value.slice(0, start) + "\n" + localPrompt.value.slice(end);
    inputIsMultiline.value = true;
    nextTick(() => {
      inputField.value?.focus();
      inputField.value?.setSelectionRange(start + 1, start + 1);
      autoResize();
    });
  }
}

/**
 * 2026-09-08 (elecvoid243): freshest composer text. `props.prompt` lags by
 * one tick (v-model emit -> parent re-render), so the skill auto-trigger
 * reads the DOM value; otherwise a token stripped right after a keystroke
 * would also drop the character the user typed in that same event.
 */
function currentInputText(): string {
  return inputField.value?.value ?? props.prompt;
}

/** 匹配 "<前缀><skill 名><空白>" 形式的精确 skill token（忽略大小写） */
function matchExactSkillToken(text: string): string | null {
  if (!hasWakePrefix(text)) return null;
  const stripped = stripWakePrefix(text.trimStart());
  const match = /^([^\s]+)\s/.exec(stripped);
  if (!match) return null;
  const token = match[1].toLowerCase();
  const skill = skillCommands.value.find(
    (cmd) => stripWakePrefix(cmd.effective_command).toLowerCase() === token,
  );
  return skill ? skill.effective_command : null;
}

/**
 * 摘掉输入框头部的触发 token（面板查询串，如 "/wr"），保留用户已输入的正文。
 * 注意不能拿它和完整 skill 名比较——查询串只是前缀匹配片段。
 */
function consumeSkillToken(): void {
  const text = currentInputText();
  const rest = stripLeadingTriggerToken(text, wakePrefixes.value);
  if (rest === text) return;
  localPrompt.value = rest;
  nextTick(() => {
    const el = inputField.value;
    el?.setSelectionRange(rest.length, rest.length);
    autoResize();
  });
}

/**
 * 排队一个 skill（一次性引导注入）并摘掉输入框里的触发 token，让用户
 * 可以继续输入正文。面板选中与"键入即触发"共用此路径。
 */
async function applySkillCommand(displayCommand: string): Promise<void> {
  const name = stripWakePrefix(displayCommand).trim();
  if (!name || skillAutoQueueBusy.value) return;
  skillAutoQueueBusy.value = true;
  try {
    const ok = await skillGuide.queueSkill(name);
    if (ok) {
      consumeSkillToken();
    } else {
      toastStore.add({
        message: tm("input.skillGuide.queueFailed", { name }),
        color: "error",
        timeout: 4000,
      });
    }
  } finally {
    skillAutoQueueBusy.value = false;
    showCommandSuggestion.value = false;
    nextTick(() => {
      inputField.value?.focus();
      autoResize();
    });
  }
}

/** 处理输入变化，控制命令提示显示；skill 名后跟空格时自动加载 */
function handleInput() {
  const text = currentInputText();
  if (text && hasWakePrefix(text) && !isComposing.value) {
    const autoSkill = matchExactSkillToken(text);
    if (autoSkill) {
      void applySkillCommand(autoSkill);
      return;
    }
    showCommandSuggestion.value = filteredCommands.value.length > 0;
    selectedCommandIndex.value = 0;
  } else {
    showCommandSuggestion.value = false;
  }
}

/** 处理 blur 事件，延迟关闭命令提示以允许点击 */
function handleBlur() {
  clearCompositionState();
  // 延迟关闭，避免点击候选项时面板已消失
  setTimeout(() => {
    showCommandSuggestion.value = false;
  }, 200);
}

/** 选择命令：普通命令填入输入框；skill 排队后摘掉 token，正文保留 */
function handleCommandSelect(cmd: SuggestionCommand) {
  if (cmd.kind === "skill") {
    void applySkillCommand(cmd.effective_command);
    return;
  }
  localPrompt.value = cmd.effective_command + " ";
  showCommandSuggestion.value = false;
  nextTick(() => {
    inputField.value?.focus();
    autoResize();
  });
}

/** 获取指令列表 */
async function fetchCommands() {
  if (isOrdinaryUser.value) return;
  if (commandSuggestionLoading.value) return;
  commandSuggestionLoading.value = true;
  try {
    const cid = currentConfigId.value;
    const res = await commandApi.list(
      cid && cid !== "default" ? cid : undefined,
    );
    if (res.data.status === "ok") {
      allCommands.value = res.data.data.items || [];
      // 读取当前配置的唤醒词列表，用于指令候选的触发前缀
      const prefixes: string[] = res.data.data.wake_prefix || [];
      if (prefixes && prefixes.length > 0) {
        wakePrefixes.value = prefixes;
      }
    }
  } catch (err) {
    // 静默失败，不影响聊天功能
    console.warn("Failed to fetch commands for suggestion:", err);
  } finally {
    commandSuggestionLoading.value = false;
  }
}

/**
 * Best-effort detection of `/project load|unload` at the start of
 * `text`, used to update the spcode chip *before* the bot's response
 * arrives. The path parser is intentionally lenient — the
 * authoritative state is re-fetched via ``onStreamEnd`` in Chat.vue
 * once the bot's response completes, so any drift between the
 * optimistic value and the server's truth is corrected within a
 * few hundred milliseconds.
 *
 * Path-extraction rules (mirrors ProjectLoadDialog.buildLoadCommand):
 *   - Wake prefix is any single non-space token at the start
 *     (`/`, `!`, etc., or whatever the user's wakePrefixes say).
 *   - After `project load` we take the rest of the line, trimmed.
 *   - If the rest is wrapped in double quotes (the dialog auto-quotes
 *     whitespace-containing paths), the quotes are stripped.
 *
 * Args:
 *   text: The exact command string submitted to the chat.
 */
function applyOptimisticProjectStatus(text: string): void {
  // Session identity is required to write optimistically; without it
  // (brand-new chat) there is nothing to attach the state to.
  if (!props.currentSession) return;
  const umo = buildWebchatUmoDetails(
    props.currentSession.session_id,
    Boolean(props.currentSession.is_group),
  ).umo;
  const trimmed = text.trim();
  // load: <prefix>project load <path...>
  const loadMatch = trimmed.match(/^\S+\s+project\s+load\s+(\S[\s\S]*)$/);
  if (loadMatch) {
    let path = loadMatch[1].trim();
    if (path.length >= 2 && path.startsWith('"') && path.endsWith('"')) {
      path = path.slice(1, -1);
    }
    if (path) {
      spcodeStatus.setLoaded(umo, path);
    }
    return;
  }
  // unload: <prefix>project unload (optionally followed by an arg)
  if (/^\S+\s+project\s+unload(?:\s|$)/.test(trimmed)) {
    spcodeStatus.setUnloaded(umo);
  }
}

/**
 * Send button click handler. Applies optimistic codegraph updates
 * before emitting the send event.
 */
function handleSendClick(): void {
  if (sendLocked.value) return; // project load/unload in flight
  applyOptimisticCodegraphStatus(localPrompt.value);
  sendMessage();
}

/**
 * 2026-08-16 skill-guide: single send funnel for every path that
 * dispatches the staged prompt (click / Enter hotkey / legacy project
 * load). Consumes the pending skill-guide queue — the plugin drains it
 * on the next LLM request, so the pending badges have served their
 * purpose once a message actually goes out (one-shot semantics).
 * 2026-10-01: snapshot the queued names BEFORE consuming and forward
 * them with the send event, so Chat.vue can persist a
 * "[Requested skills]" block into the message text (visible in history).
 */
function sendMessage(): void {
  if (isOrdinaryUser.value) {
    if (canSend.value) {
      const skills = [...skillGuide.queued.value];
      skillGuide.consumeQueued();
      emit("send", skills);
    }
    return;
  }
  const skillNames = [...skillGuide.queued.value];
  skillGuide.consumeQueued();
  emit("send", skillNames);
}

/**
 * Parse the text being sent for codegraph MCP commands and apply
 * optimistic updates so the chip reflects the new state immediately.
 *
 * Also handles `/project unload`, which on the backend may trigger
 * `codegraph set` with the configured default project. Since the
 * frontend cannot know the default path, it clears the displayed
 * project path — the 30 s poll will correct it if needed.
 *
 * Matched commands:
 *   - `<prefix>codegraph start`       → set MCP running
 *   - `<prefix>codegraph stop`        → set MCP stopped
 *   - `<prefix>codegraph set <path>`  → update active project path
 *   - `<prefix>project unload`        → clear active project path
 */
function applyOptimisticCodegraphStatus(text: string): void {
  const trimmed = text.trim();
  // start
  if (/^\S+\s+codegraph\s+start(?:\s|$)/.test(trimmed)) {
    codegraphStatus.setRunning(true);
    return;
  }
  // stop
  if (/^\S+\s+codegraph\s+stop(?:\s|$)/.test(trimmed)) {
    codegraphStatus.setRunning(false);
    return;
  }
  // set <path>
  const setMatch = trimmed.match(/^\S+\s+codegraph\s+set\s+(\S[\s\S]*)$/);
  if (setMatch) {
    let path = setMatch[1].trim();
    if (path.length >= 2 && path.startsWith('"') && path.endsWith('"')) {
      path = path.slice(1, -1);
    }
    if (path) {
      codegraphStatus.setProject(path);
    }
    return;
  }
  // project unload → the backend may reset codegraph to the configured
  // default project. Clear our local copy; polling catches up.
  if (/^\S+\s+project\s+unload(?:\s|$)/.test(trimmed)) {
    codegraphStatus.setProject("");
  }
}

/**
 * Handle a spcode project-load dialog submission: dispatch the silent
 * webapi call (no chat-history pollution).
 *
 * Project load no longer runs multi-minute codegraph / AGENTS.md
 * generation steps, so it does not start the operation-progress poller
 * and produces no chip bubble. A local busy flag still locks sending
 * while the system prompt is being rewritten.
 *
 * Fallback: without a current session there is no umo to address the
 * silent POST at, so we keep the previous behavior — write the legacy
 * command text into the prompt and emit `send` (Chat.vue.sendSystemCommand
 * creates the session lazily).
 */
async function handleProjectLoadSubmit(
  payload: ProjectLoadSubmitPayload,
): Promise<void> {
  const session = props.currentSession;
  if (!session && !props.sessionId && payload.mode === "unload") {
    emit("project-changed", "", null);
    return;
  }
  if (!session && (!props.ensureProjectSession || payload.mode === "codegraph" || payload.mode === "codegraph-init")) {
    if (payload.mode === "codegraph" || payload.mode === "codegraph-init") {
      applyOptimisticCodegraphStatus(payload.legacyText);
    } else {
      applyOptimisticProjectStatus(payload.legacyText);
    }
    localPrompt.value = payload.legacyText;
    sendMessage();
    return;
  }
  let sessionId: string;
  try {
    sessionId = session?.session_id ?? props.sessionId ?? await props.ensureProjectSession!();
  } catch (err) {
    toastStore.add({ message: String((err as Error)?.message ?? err), color: "error" });
    return;
  }
  const umo = buildWebchatUmoDetails(sessionId, Boolean(session?.is_group)).umo;
  const locksSend = payload.mode === "project" || payload.mode === "unload";
  if (locksSend) projectOpBusy.value = true;
  try {
    if (payload.mode === "unload") {
      operationProgress.startPolling(umo);
      if (spcodeStatus.status.value.umo !== umo || spcodeStatus.status.value.loaded) {
        await silentOps.silentUnload(umo);
      }
      // 2026-09-01: manual unload invalidates the "already loaded" tag.
      clearSessionLoadedTag(sessionId);
      emit("project-changed", sessionId, null);
    } else if (payload.mode === "codegraph-init") {
      // codegraph init 后端最长 300s + --force 重试 180s,轮询预算单独放大。
      operationProgress.startPolling(umo, 600_000);
      await silentOps.silentCodegraphInit(umo, payload.path!);
      await codegraphStatus.refresh();
    } else if (payload.mode === "codegraph") {
      operationProgress.startPolling(umo);
      await silentOps.silentCodegraphSet(umo, payload.path!);
      await codegraphStatus.refresh();
    } else {
      const result = await silentOps.silentLoadDirectory({
        umo,
        directory: payload.path!,
        force: payload.force,
      });
      // 2026-09-01: manual load may point at a directory other than the
      // project binding; drop the tag so the next auto-load re-evaluates
      // against the binding (the backend idempotent check keeps it cheap).
      clearSessionLoadedTag(sessionId);
      if (result.loaded) {
        emit("project-changed", sessionId, result.directory || payload.path!);
      }
    }
  } catch (err) {
    // The chip's failed state + detail popover come from the progress ref;
    // the toast carries the one-line summary (last ❌ message) for
    // immediate visibility.
    const messages = (err as ProjectLoadError)?.data?.substep_messages ?? [];
    const summary =
      [...messages].reverse().find((m) => m.startsWith("❌")) ??
      String((err as Error)?.message ?? err);
    toastStore.add({
      message: summary,
      color: "error",
      multiLine: true,
      timeout: 6000,
    });
  } finally {
    if (locksSend) projectOpBusy.value = false;
    // Authoritative chip state, whatever the outcome.
    await spcodeStatus.refresh(umo);
  }
}

/**
 * Handle a mode change from the file access chip.
 *
 * Primary path: POST the new mode to AstrBot core, keyed by the
 * session's full unified_msg_origin. Fallbacks:
 *   - no current session yet — only readonly has a command fallback
 *     (``/plan``); the other modes need a umo and are dropped until
 *     a session exists;
 *   - the POST fails — resync from the backend, and readonly
 *     additionally falls back to the ``/plan`` chat command.
 *
 * The optimistic ``setOptimistic`` flip happens first so the chip
 * highlights immediately; failures are corrected by the resync.
 */
function handleFileAccessModeChange(mode: FileAccessMode): void {
  // Optimistic flip so the chip highlights immediately.
  fileAccessMode.setOptimistic(mode);

  const umo = currentSessionUmo.value;
  if (!umo) {
    // No session yet → no umo. Only readonly has a command fallback.
    if (mode === "readonly") {
      emit("send-command", "/plan");
    }
    return;
  }
  void fileAccessMode.setMode(umo, mode).then((ok) => {
    if (!ok) {
      // Resync on failure; readonly additionally falls back to /plan.
      void fileAccessMode.refresh(umo);
      if (mode === "readonly") {
        emit("send-command", "/plan");
      }
    }
  });
}

function handleCompositionStart() {
  isComposing.value = true;
  lastCompositionEndAt.value = null;
}

function handleCompositionEnd(e: CompositionEvent) {
  lastCompositionEndAt.value = e.timeStamp;
  clearCompositionState({ keepLastEndAt: true });

  // Manually sync the final composited text to the parent component
  // after the IME commits. The v-model setter is suppressed during
  // composition (see localPrompt computed), so we must explicitly
  // propagate the DOM value once composition ends.
  //
  // Capture the DOM value at compositionend to guard against a race
  // where props.prompt is externally updated between now and nextTick.
  const endValue = inputField.value?.value;

  nextTick(() => {
    const el = inputField.value;
    // Only sync if the DOM hasn't been changed externally in the meantime.
    if (el && el.value === endValue && el.value !== props.prompt) {
      emit("update:prompt", el.value);
      // Re-evaluate command suggestions that were suppressed during IME
      // composition (handleInput checks isComposing). Only needed when
      // the value actually changed. Runs in a nested nextTick so
      // props.prompt reflects the emit above.
      nextTick(() => {
        handleInput();
      });
    }
  });
}

function clearCompositionState({ keepLastEndAt = false } = {}) {
  isComposing.value = false;
  if (!keepLastEndAt) {
    lastCompositionEndAt.value = null;
  }
}

function handleKeyUp(e: KeyboardEvent) {
  if (e.keyCode === 66) {
    ctrlKeyDown.value = false;

    if (ctrlKeyTimer.value) {
      clearTimeout(ctrlKeyTimer.value);
      ctrlKeyTimer.value = null;
    }

    if (props.isRecording) {
      emit("stopRecording");
    }
  }
}

function handlePaste(e: ClipboardEvent) {
  const pastedText = e.clipboardData?.getData("text/plain") || "";
  if (!isOrdinaryUser.value && pastedText.length > longPasteThreshold) {
    e.preventDefault();
    emit("fileSelect", [
      new File([pastedText], `pasted-text-${Date.now()}.txt`, {
        type: "text/plain;charset=utf-8",
      }),
    ]);
    return;
  }

  if (!inputIsMultiline.value && pastedText.includes("\n")) {
    e.preventDefault();
    const target = e.target as HTMLInputElement;
    const start = target.selectionStart ?? localPrompt.value.length;
    const end = target.selectionEnd ?? start;
    localPrompt.value =
      localPrompt.value.slice(0, start) +
      pastedText +
      localPrompt.value.slice(end);
    inputIsMultiline.value = true;
    nextTick(() => {
      inputField.value?.focus();
      const cursor = start + pastedText.length;
      inputField.value?.setSelectionRange(cursor, cursor);
      autoResize();
    });
  }
  emit("pasteImage", e);
}

// 2026-07-18 drag-to-chat (elecvoid243): custom MIME type used by
// the sidebar file browser to mark a drag payload as a remote file
// path. The chat input's drop handler checks for this FIRST so we
// can route sidebar file drops to the reference store instead of the
// native FileList path. Kept as a module-local
// constant (not exported) because the only consumer is the sidebar
// file list which lives in the same Vue tree.
const SIDEBAR_FILE_MIME = "application/x-astrbot-file-path";

function handleDragOver(e: DragEvent) {
  // 清除之前的 leave timeout
  if (dragLeaveTimeout) {
    clearTimeout(dragLeaveTimeout);
    dragLeaveTimeout = null;
  }

  // 显示拖拽遮罩的判定：既支持原生文件拖入（"Files" → 上传），也支持
  // sidebar 文件浏览器拖入（自定义 MIME → 引用）。两种共用一个
  // isDragging overlay，但图标与文案按 dragKind 分流。
  const types = e.dataTransfer?.types;
  if (types?.includes("Files")) {
    dragKind.value = "upload";
    isDragging.value = true;
  } else if (!isOrdinaryUser.value && types?.includes(SIDEBAR_FILE_MIME)) {
    dragKind.value = "reference";
    isDragging.value = true;
  }
}

function handleDragLeave(e: DragEvent) {
  // 使用 timeout 避免在子元素间移动时闪烁
  dragLeaveTimeout = window.setTimeout(() => {
    isDragging.value = false;
  }, 50);
}

function handleDrop(e: DragEvent) {
  if (isOrdinaryUser.value) return;
  isDragging.value = false;

  // 优先处理 sidebar 文件拖入：自定义 MIME 携带的是远端文件路径，
  // 父组件把它登记为引用（useFileReferences），发送时拼接到消息末尾。
  // 如果 dataTransfer 同时携带 Files（极少见,例如从外部桌面把同一个
  // 文件既通过路径也通过 File 拖入），优先信任自定义类型 — 它意味着
  // 这是 sidebar 的拖拽。
  // 原生 FileList 拖入不在此处理：事件冒泡到 Chat.vue 的全局拖拽热区
  // （useDragUpload on <main>）统一走上传链路，避免双重上传。
  const sidebarPayload = e.dataTransfer?.getData(SIDEBAR_FILE_MIME);
  if (sidebarPayload) {
    try {
      const parsed = JSON.parse(sidebarPayload) as {
        path?: unknown;
        name?: unknown;
      };
      if (typeof parsed.path === "string" && typeof parsed.name === "string") {
        emit("fileReferenceDrop", { path: parsed.path, name: parsed.name });
        return;
      }
    } catch {
      // JSON 解析失败 → 回退到原生 FileList 流程（冒泡给全局热区）
    }
  }
}

function triggerImageInput() {
  imageInputRef.value?.click();
}

function handleFileSelect(event: Event) {
  const target = event.target as HTMLInputElement;
  const files = target.files;
  if (files) {
    emit("fileSelect", files);
  }
  target.value = "";
}

function handleRecordClick() {
  if (props.isRecording) {
    emit("stopRecording");
  } else {
    emit("startRecording");
  }
}

function handleConfigChange(payload: {
  configId: string;
  agentRunnerType: string;
}) {
  const runnerType = (payload.agentRunnerType || "").toLowerCase();
  const isInternal = runnerType === "internal" || runnerType === "local";
  providerSelectorAvailable.value = isInternal;
  // 配置切换后重新获取指令列表和唤醒词
  if (payload.configId && payload.configId !== currentConfigId.value) {
    currentConfigId.value = payload.configId;
    fetchCommands();
  }
}

// Chat config-profile selection (2026-10-02, elecvoid243): ChatInput owns
// the single context binding — the "+" menu item and the composer chip are
// pure renderers of the shared singleton; selection changes (including the
// runner-type gate for the provider selector) arrive via this listener.
const chatConfigSelection = provideChatConfigSelection();
chatConfigSelection.bindContext(
  computed(() => ({
    sessionId: props.configSessionId ?? props.sessionId ?? null,
    platformId: sessionPlatformId.value,
    isGroup: sessionIsGroup.value,
    initialConfigId: props.configId ?? null,
    persistGlobal: props.persistConfigSelection !== false,
  })),
);
onBeforeUnmount(chatConfigSelection.onConfigChanged(handleConfigChange));

function getCurrentSelection() {
  if ((!isOrdinaryUser.value && !props.showProviderSelector) || !providerSelectorAvailable.value) {
    return null;
  }
  // Header and ordinary composer share the active session model choice.
  return {
    providerId: providerModelSelection.selectedProviderId.value,
    modelName: providerModelSelection.selectedModelName.value,
  };
}

function getThinkingEffort(): ThinkingEffort {
  return thinkingEffort.value;
}

function focusInput() {
  if (!inputField.value) return;
  inputField.value.focus();
}

/**
 * SpcodeProjectIndicator "open load dialog" handler. The chip is a
 * shortcut for the `+` menu's "加载项目目录" entry — both should
 * surface the same dialog (owned by ``ProjectLoadDialog``), so we
 * delegate to that component's exposed ``openLoadDialog()`` method.
 *
 * Defensive: the dialog is always mounted at the ChatInput level
 * (sibling of the input area), so under normal flows the ref is set.
 * If for some reason it is not, we fall back to focusing the textarea.
 */
function openLoadDialog(): void {
  if (projectLoadDialogRef.value) {
    projectLoadDialogRef.value.openLoadDialog();
    return;
  }
  focusInput();
}

/**
 * ``+`` menu's "加载项目目录" trigger handler. The menu item lives
 * inside the ``+`` popover's lazy-mounted slot, so it can only emit
 * upward; the dialog it points to is the same ``ProjectLoadDialog``
 * the chip uses, reached via the shared template ref.
 */
function openProjectLoadDialog(): void {
  openLoadDialog();
}

/**
 * Project indicator services popover "manage codegraph" handler. Delegates
 * to the second ``ProjectLoadDialog`` instance (``commandMode="codegraph"``)
 * mounted next to the project-load dialog — the same dialog the removed
 * SpcodeCodegraphChip used to open.
 */
function openCodegraphLoadDialog(): void {
  if (codegraphLoadDialogRef.value) {
    codegraphLoadDialogRef.value.openLoadDialog();
    return;
  }
  focusInput();
}

// Pull the spcode status API into scope. The watcher fires an initial
// status fetch once the unified visibility gate (plugin enabled AND
// /project* command present) flips to true. Same shape as before, just
// reading from the unified composable.
const spcodeStatus = useSpcodeProjectStatus();
// Silent-operation driver + clients for the project-load dialog (2026-08-06).
const operationProgress = useSpcodeOperationProgress();
// Send lock (2026-08-07): block sending while a project load/unload is
// rewriting the system prompt, to keep prompt-cache continuity. The local
// flag covers the manual dialog path, which no longer polls progress
// (project load is a single fast AGENTS.md read).
const projectOpBusy = ref(false);
const sendLocked = computed(
  () =>
    !isOrdinaryUser.value &&
    (projectOpBusy.value || operationProgress.isProjectOpRunning.value),
);
const silentOps = useSpcodeSilentOps();
const toastStore = useToastStore();
watch(
  showSpcodeIndicator,
  async (visible) => {
    if (visible) {
      // Bug fix (2026-06-23, elecvoid243): pass the resolved umo so the
      // backend queries THIS session's loaded project (not the global
      // "most-recently-loaded" fallback). Mirrors the Chat.vue watchers
      // and the plan-mode refresh pattern below.
      if (!props.currentSession) {
        // Bug fix (2026-08-15, elecvoid243): with no session there is
        // no umo to address the request at, and a bare refresh() makes
        // the backend return the most-recently-loaded project across
        // ALL umos — the previous session's project. That is exactly
        // the window right after "new chat" is clicked (the session is
        // only created on the first send), so the chip would keep
        // showing the previous session's project until then. Reset to
        // the empty state instead.
        spcodeStatus.reset();
        return;
      }
      const umo = buildWebchatUmoDetails(
        props.currentSession.session_id,
        Boolean(props.currentSession.is_group),
      ).umo;
      await spcodeStatus.refresh(umo);
    }
  },
  { immediate: false },
);

// Singleton codegraph MCP status. Authoritative refresh is driven by
// ``Chat.vue:onStreamEnd`` (same hook that refreshes project status and
// plan mode), so the chip catches up the moment the bot finishes
// processing any ``/codegraph start|stop|set`` command. We only need
// an initial fetch here so the chip has a value to render on first
// paint when the spcode indicator becomes visible.
const codegraphStatus = useSpcodeCodegraphStatus();
const tcMemoryStatus = useTcMemoryStatus();
// Singleton vivado MCP status. Follows the same pattern as codegraph:
// authoritative refresh is driven by ``Chat.vue:onStreamEnd``.
const vivadoStatus = useSpcodeVivadoStatus();
// Fallback sync path for the case where another client / the bot itself
// mutates codegraph state while this tab is in the background. When
// the user brings the tab back to the foreground we re-query the
// authoritative state. ``showSpcodeIndicator`` gates the refresh so
// we never issue a request for users who have the spcode plugin off
// (or who have already logged out / unmounted this component).
const onVisibilityChange = () => {
  if (!isOrdinaryUser.value && document.visibilityState === "visible" && showSpcodeIndicator.value) {
    void codegraphStatus.refresh();
  void tcMemoryStatus.refresh();
    void vivadoStatus.refresh();
  }
};
watch(
  showSpcodeIndicator,
  async (visible) => {
    if (visible && !isOrdinaryUser.value) {
      await codegraphStatus.refresh();
      await vivadoStatus.refresh();
    }
  },
  { immediate: false },
);

// Keep the file access chip in sync with the backend's per-session
// state: refresh whenever the active session changes (and once on
// first paint via `immediate`), reset when no session is active.
//
// CRITICAL: refresh() must receive the full unified_msg_origin (umo)
// string the backend keys its per-session state on — built via
// :func:`buildWebchatUmoDetails` — not the bare webchat conversation
// id. Passing the raw id would make the backend look up a key that
// does not exist and silently fall back to the default mode,
// clobbering the chip's optimistic state right after every change.
watch(
  () => props.currentSession?.session_id ?? null,
  async (sid) => {
    if (isOrdinaryUser.value) return;
    if (!sid || !props.currentSession) {
      fileAccessMode.reset();
      return;
    }
    const umo = buildWebchatUmoDetails(
      props.currentSession.session_id,
      Boolean(props.currentSession.is_group),
    ).umo;
    await fileAccessMode.refresh(umo);
  },
  { immediate: true },
);

onMounted(() => {
  document.addEventListener("keyup", handleKeyUp);
  document.addEventListener("visibilitychange", onVisibilityChange);
  nextTick(autoResize);
  void loadChatUiSettings();
  if (isOrdinaryUser.value) return;
  // Preload commands only for adapted accounts.
  fetchCommands();
  nextTick(autoResize);
  // 预加载 spcode 插件启用状态。`useSpcodeProjectLoad` 是单例,
  // 这里先 fetch 一次以便 onMounted 后 chip / 菜单的可见性判断
  // 不会因为插件状态尚未拉取而误判为"未启用"。后续 onMounted
  // 触发的 fetchCommands() 拿到 commands 列表后,两个判断
  // (activated + hasProjectTreeCommand) 都已经 ready。
  void refreshPluginState();
  // Initial codegraph status fetch: the watcher above (immediate: false)
  // only fires on transition, so on first mount — when showSpcodeIndicator
  // may already be true — we need an explicit call to guarantee the chip
  // has data to render on first paint.
  void codegraphStatus.refresh();
  void tcMemoryStatus.refresh();
  // Live polling every 30 s so the chip stays in sync when codegraph
  // state changes externally (e.g. the user toggles it via another
  // client or the bot restarts its MCP server). The interval is gated
  // by showSpcodeIndicator inside the callback so it is a no-op when
  // the spcode plugin is not active.
  codegraphPollTimer = window.setInterval(() => {
    if (showSpcodeIndicator.value) {
      void codegraphStatus.refresh();
  void tcMemoryStatus.refresh();
    }
  }, 30_000);
});

onBeforeUnmount(() => {
  clearCompositionState();
  document.removeEventListener("keyup", handleKeyUp);
  document.removeEventListener("visibilitychange", onVisibilityChange);
  if (codegraphPollTimer !== null) {
    clearInterval(codegraphPollTimer);
    codegraphPollTimer = null;
  }
  if (effortValueTimer !== null) {
    window.clearTimeout(effortValueTimer);
    effortValueTimer = null;
  }
});

defineExpose({
  getCurrentSelection,
  getThinkingEffort,
  prepareConfig: async () => {
    const configId = await chatConfigSelection.prepareSend();
    if (sessionEffortEnabled.value) await providerModelSelection.prepareSend();
    return configId;
  },
  focusInput,
  openLoadDialog,
});
</script>

<style scoped src="./chatComposer.css"></style>
<style scoped>
.input-area {
  container-type: inline-size;
  padding: 0 16px;
  background-color: transparent;
  position: relative;
  border-top: 1px solid var(--v-theme-border);
  flex-shrink: 0;
}
.input-area__status-row {
  align-items: center;
  display: flex;
  gap: 8px;
  justify-content: space-between;
  margin: 4px auto 0;
  max-width: var(--chat-content-max-width, 960px);
  padding: 0 6px 6px;
  pointer-events: auto;
  width: var(--chat-content-width, 80%);
}

/*
 * Left cluster: the single project capsule (2026-10-03 redesign folded
 * the services + worktree side buttons into its dropdown).
 */
.input-area__status-row__left {
  align-items: center;
  display: flex;
  min-width: 0;
}

/* Right cluster: file-access-mode capsule + the standalone workspace
   entry capsule (icon-only below 768px, see GitDiffChip). */
.input-area__status-row__right {
  align-items: center;
  display: flex;
  flex-shrink: 0;
  gap: 6px;
}

/*
 * Legacy column-stack wrapper kept as display:none because the template
 * still wraps the two right-side chips in `.input-area__status-row__chips-stack`
 * for backwards compatibility with future v-if-on-children use cases.
 */
.input-area__status-row__chips-stack {
  display: contents;
}




.input-area.is-dark .input-action-btn {
  background: rgb(var(--v-theme-on-surface)) !important;
  color: rgb(var(--v-theme-surface)) !important;
}

.input-area.is-dark .input-action-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.86) !important;
}


@container (max-width: 560px) {
  .input-area__status-row {
    box-sizing: border-box;
    width: 100%;
    flex-wrap: wrap;
    justify-content: flex-start;
  }

  .input-area__status-row__left {
    flex: 1 1 120px;
  }

  .input-area__status-row__right {
    flex-wrap: wrap;
    min-width: 0;
  }

  .input-container {
    box-sizing: border-box;
    width: 100% !important;
  }

  .composer-toolbar {
    flex-wrap: wrap;
    row-gap: 4px;
  }

  .composer-toolbar__left {
    flex: 1 1 auto;
    flex-wrap: wrap;
  }

  .composer-toolbar__left > * {
    flex-shrink: 0;
  }

  .composer-toolbar__divider {
    margin: 0 3px;
  }

  .composer-toolbar__left :deep(.config-chip) {
    max-width: 108px;
  }

  .composer-toolbar__left :deep(.effort-chip-btn__label) {
    width: auto;
    max-width: 48px;
  }

  .composer-toolbar__left :deep(.quick-chip-btn__label),
  .composer-toolbar__left :deep(.quick-chip-btn__chevron) {
    display: none;
  }

  .composer-toolbar__left :deep(.quick-chip-btn) {
    min-width: 36px;
    justify-content: center;
  }

  .compression-readout-slot {
    display: none;
  }
}

@container (max-width: 380px) {
  /* 2026-11-07 (elecvoid243): below this width the four status capsules can no
     longer share a row with their labels, so each falls back to its icon. The
     components keep their full text in the DOM for tooltips/tests. */
  .input-area__status-row :deep(.sp-capsule__label),
  .input-area__status-row :deep(.sp-capsule__wt-sep),
  .input-area__status-row :deep(.sp-capsule__wt),
  .input-area__status-row :deep(.sp-capsule__wt-icon),
  .input-area__status-row :deep(.sp-capsule__chevron),
  .input-area__status-row :deep(.sp-capsule__icon),
  .input-area__status-row :deep(.fa-chip-btn__label),
  .input-area__status-row :deep(.fa-chip-btn__chevron),
  .input-area__status-row :deep(.sp-ghost-btn__label) {
    display: none;
  }

  .input-area__status-row :deep(.sp-capsule__compact-icon) {
    display: inline-flex;
  }

  .input-area__status-row :deep(.sp-capsule),
  .input-area__status-row :deep(.fa-chip-btn),
  .input-area__status-row :deep(.sp-ghost-btn) {
    width: 30px;
    min-width: 30px;
    padding: 0;
    justify-content: center;
  }

  .composer-toolbar__left :deep(.config-chip) {
    max-width: 84px;
  }
}




.reply-preview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  margin: 8px 8px 0 8px;
  background-color: rgba(var(--v-theme-primary), 0.06);
  border-radius: 12px;
  gap: 8px;
  max-height: 500px;
  overflow: hidden;
}

/* Transition animations for reply preview */
.slideReply-enter-active {
  animation: slideDown 0.2s ease-out;
}

.slideReply-leave-active {
  animation: slideUp 0.2s ease-out;
}

@keyframes slideDown {
  from {
    max-height: 0;
    opacity: 0;
    margin-top: 0;
    padding-top: 0;
    padding-bottom: 0;
  }

  to {
    max-height: 500px;
    opacity: 1;
    margin-top: 8px;
    padding-top: 8px;
    padding-bottom: 8px;
  }
}

@keyframes slideUp {
  from {
    max-height: 500px;
    opacity: 1;
    margin-top: 8px;
    padding-top: 8px;
    padding-bottom: 8px;
  }

  to {
    max-height: 0;
    opacity: 0;
    margin-top: 0;
    padding-top: 0;
    padding-bottom: 0;
  }
}

.reply-content {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.reply-icon {
  color: var(--v-theme-secondary);
  flex-shrink: 0;
}

.reply-text {
  font-size: 13px;
  color: var(--v-theme-secondaryText);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.remove-reply-btn {
  flex-shrink: 0;
  opacity: 0.6;
}

.attachments-preview {
  display: flex;
  gap: 10px;
  margin: 10px 12px 0;
  padding: 2px 2px 4px;
  flex-wrap: nowrap;
  align-items: center;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: thin;
  max-height: 72px;
}

.input-container.has-attachments .attachments-preview {
  margin: 0 0 8px;
  padding: 0;
}

.attachment-card {
  --attachment-color: #607d8b;
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  width: 210px;
  height: 54px;
  flex: 0 0 auto;
  min-width: 0;
  padding: 7px 32px 7px 10px;
  overflow: hidden;
  color: rgb(var(--v-theme-on-surface));
  background: rgba(var(--v-theme-on-surface), 0.055);
  border: 0;
  border-radius: 8px;
}

.file-preview {
  background: rgba(var(--v-theme-on-surface), 0.055);
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--attachment-color) 14%, transparent),
    rgba(var(--v-theme-on-surface), 0.055) 62%
  );
}

.image-preview {
  width: 54px;
  flex-basis: 54px;
  padding: 0;
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.preview-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 8px;
  cursor: zoom-in;
}

.image-preview-overlay {
  display: flex;
  align-items: center;
  justify-content: center;
}

.preview-image-large {
  max-width: min(92vw, 1200px);
  max-height: 90vh;
  object-fit: contain;
  cursor: zoom-out;
}

.attachment-icon {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  flex-shrink: 0;
  min-width: 34px;
  color: var(--attachment-color);
}

.attachment-icon--audio {
  color: #00897b;
}

.attachment-ext {
  max-width: 58px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
  font-weight: 700;
  line-height: 12px;
  color: var(--attachment-color);
}

.attachment-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 17px;
}

.remove-attachment-btn {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 22px !important;
  height: 22px !important;
  min-width: 22px !important;
  opacity: 0.8;
  transition: opacity 0.2s;
}

.remove-attachment-btn:hover {
  opacity: 1;
}

.attachment-card--failed {
  --attachment-color: rgb(var(--v-theme-error));
  padding-right: 56px;
}

.attachment-icon--failed {
  color: rgb(var(--v-theme-error));
}

.retry-attachment-btn {
  right: 30px;
}

.attachment-card--active {
  padding-right: 10px;
}

.attachment-progress-text {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  color: rgb(var(--v-theme-primary));
}

.cancel-active-btn {
  flex-shrink: 0;
  width: 22px !important;
  height: 22px !important;
  min-width: 22px !important;
  opacity: 0.7;
}

.cancel-active-btn:hover {
  opacity: 1;
}

.attachment-progress-track {
  position: absolute;
  /* Align with the filename text: card padding + icon width + gap. */
  left: 52px;
  right: 10px;
  bottom: 3px;
}

.fade-in {
  animation: fadeIn 0.3s ease-in-out;
}

.attachments-enter-active,
.attachments-leave-active {
  overflow: hidden;
  transition:
    max-height 0.2s ease,
    margin 0.2s ease,
    padding 0.2s ease,
    opacity 0.16s ease,
    transform 0.2s ease;
}

.attachments-enter-from,
.attachments-leave-to {
  max-height: 0;
  margin-top: 0;
  padding-top: 0;
  padding-bottom: 0;
  opacity: 0;
  transform: translateY(6px);
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 768px) {
  .input-area {
    padding: 8px 0 0 !important;
    border-top: 0;
  }

  .input-container {
    width: calc(100% - 20px) !important;
    max-width: 100% !important;
    margin: 0 10px calc(8px + env(safe-area-inset-bottom)) !important;
    padding: 4px 6px 6px !important;
    overflow: hidden;
    border: 1px solid rgba(var(--v-theme-on-surface), 0.14) !important;
    border-radius: 24px !important;
    background: #fff !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08) !important;
  }

  .input-area.is-dark .input-container {
    border-color: rgba(255, 255, 255, 0.16) !important;
    background: #2d2d2d !important;
    box-shadow: none !important;
  }

  .input-field-shell {
    padding: 6px 10px 0;
  }

  .chat-text-input,
  .chat-textarea {
    font-size: 16px !important;
  }

  .chat-textarea {
    max-height: min(42vh, 220px) !important;
    overflow-y: auto;
  }

  .input-action-btn:not(:disabled) {
    background: rgb(var(--v-theme-on-surface)) !important;
    color: rgb(var(--v-theme-surface)) !important;
  }

  .input-action-btn:disabled {
    background: rgba(var(--v-theme-on-surface), 0.04) !important;
    color: rgba(var(--v-theme-on-surface), 0.18) !important;
  }

  .attachments-preview {
    margin: 8px 16px 0;
    gap: 8px;
  }

  .attachment-card {
    width: min(220px, calc(100vw - 28px));
    height: 54px;
  }

  .image-preview {
    width: 54px;
    flex-basis: 54px;
  }
}

/* Inline comments chip. Self-contained pill that mirrors the
   Vuetify "tonal warning" style we replaced. */
.comment-count-chip {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 10px;
  border-radius: 12px;
  background: rgba(var(--v-theme-warning), 0.16);
  color: rgb(var(--v-theme-warning));
  font-size: 12px;
  line-height: 1;
}
.comment-count-chip__main {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  border: 0;
  padding: 0;
  margin: 0;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.comment-count-chip__main:hover,
.comment-count-chip__main:focus-visible {
  filter: brightness(1.1);
}
.comment-count-chip__clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: rgba(var(--v-theme-error), 0.85);
  color: rgb(var(--v-theme-on-error));
  border: 0;
  padding: 0;
  margin-left: 2px;
  cursor: pointer;
  opacity: 0.85;
  transition:
    opacity 0.12s,
    transform 0.12s;
}
.comment-count-chip__clear:hover {
  opacity: 1;
  transform: scale(1.08);
}

/* 2026-08-09 drag-reference: file-reference chips row. Reuses the
   "attachments" transition; each chip is a compact pill showing the
   basename, with the absolute path on the title tooltip. */
.references-preview {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 12px 0;
}
.reference-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 220px;
  padding: 3px 6px 3px 8px;
  border-radius: 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  background: rgba(var(--v-theme-primary), 0.07);
  font-size: 12px;
  color: rgb(var(--v-theme-on-surface));
}
.reference-chip__icon {
  flex: none;
  color: rgb(var(--v-theme-primary));
}
.reference-chip__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.reference-chip__remove {
  display: inline-flex;
  align-items: center;
  border: none;
  background: transparent;
  cursor: pointer;
  padding: 1px;
  border-radius: 50%;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.reference-chip__remove:hover {
  color: rgb(var(--v-theme-error));
}

/* 2026-09-08 (elecvoid243): queued one-shot skill chips, rendered above the
   composer (wrapping onto extra lines) — the single display for BOTH the
   "+" menu and the "/" palette. */
.skill-guide-preview {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  width: 100%;
  flex: 0 0 auto;
  padding: 8px 12px 0;
}
.skill-guide-preview__label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  white-space: nowrap;
}
.skill-guide-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 220px;
  padding: 3px 6px 3px 8px;
  border-radius: 8px;
  border: 1px solid rgba(var(--v-theme-primary), 0.35);
  background: rgba(var(--v-theme-primary), 0.08);
  font-size: 12px;
  color: rgb(var(--v-theme-on-surface));
}
.skill-guide-chip__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-guide-chip__remove {
  display: inline-flex;
  align-items: center;
  border: none;
  background: transparent;
  cursor: pointer;
  padding: 1px;
  border-radius: 50%;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.skill-guide-chip__remove:hover {
  color: rgb(var(--v-theme-error));
}

/* ── Pending follow-up queue (ZCode-style) ── */
/* ── Pending follow-up queue (2026-11-07 redesign, elecvoid243) ──
   One tray for the whole queue instead of one card per message: append /
   interrupt act on every queued item, so they belong once in the header.
   Rows only keep their per-item edit / delete. */
.pending-follow-ups {
  width: var(--chat-content-width, 80%);
  max-width: var(--chat-content-max-width, 960px);
  margin: 0 auto 8px;
  overflow: hidden;
  border: 1px solid var(--sp-chip-border);
  border-radius: 14px;
  /* Near-white so the tray reads as a whisper above the composer; the border
     and divider carry the structure instead of a gray fill. */
  background: #fafaf9;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.input-area.is-dark .pending-follow-ups {
  background: var(--sp-chip-bg);
  box-shadow: none;
}

.pending-follow-ups__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 6px 6px 12px;
}

.pending-follow-ups__title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--sp-text-muted);
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.pending-follow-ups__clock {
  color: var(--sp-text-path);
}

.pending-follow-ups__count {
  min-width: 17px;
  height: 17px;
  padding: 0 5px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: var(--sp-ghost-open-bg);
  color: var(--sp-text-muted);
  font-size: 10.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.pending-follow-ups__actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

/* Stacked label + shortcut hint: the key line is deliberately tiny so it
   never competes with the action name. */
.pending-action {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0;
  padding: 3px 9px 2px;
  border: 1px solid var(--sp-chip-border);
  border-radius: 8px;
  background: transparent;
  color: var(--sp-text-muted);
  font: inherit;
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.pending-action:hover {
  background: var(--sp-ghost-hover-bg);
  color: var(--sp-text-primary);
}

.pending-action:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

.pending-action__main {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12.5px;
  font-weight: 500;
  line-height: 18px;
}

.pending-action__key {
  color: var(--sp-text-path);
  font-size: 9px;
  line-height: 11px;
  letter-spacing: 0.02em;
  font-variant-numeric: tabular-nums;
}

.pending-action--interrupt .pending-action__main {
  color: #8b1a1a;
}

.input-area.is-dark .pending-action--interrupt .pending-action__main {
  color: #e08a8a;
}

.pending-follow-ups__list {
  list-style: none;
  margin: 0;
  /* A hairline separates the queue's header from its items. */
  border-top: 1px solid var(--sp-chip-divider);
  padding: 4px 6px 6px;
}

.pending-follow-up-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 3px 6px;
  border-radius: 8px;
  transition: background-color 0.12s ease;
}

.pending-follow-up-row + .pending-follow-up-row {
  border-top: 1px solid var(--sp-chip-divider);
  border-radius: 0 0 8px 8px;
}

.pending-follow-up-row:hover {
  background: var(--sp-ghost-hover-bg);
}

.pending-follow-up-row__index {
  flex: 0 0 16px;
  color: var(--sp-text-path);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.pending-follow-up-row__text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--sp-text-primary);
  font-size: 13px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending-follow-up-row__actions {
  display: inline-flex;
  gap: 2px;
  flex: 0 0 auto;
  opacity: 0;
  transition: opacity 0.12s ease;
}

.pending-follow-up-row:hover .pending-follow-up-row__actions,
.pending-follow-up-row:focus-within .pending-follow-up-row__actions {
  opacity: 1;
}

.pending-row-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--sp-text-path);
  cursor: pointer;
}

.pending-row-icon:hover {
  background: var(--sp-ghost-open-bg);
  color: var(--sp-text-primary);
}

.pending-row-icon:focus-visible,
.pending-row-action:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

.pending-follow-up-row__edit-actions {
  display: inline-flex;
  gap: 4px;
  flex-shrink: 0;
}

.pending-row-action {
  padding: 4px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--sp-text-muted);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.pending-row-action:hover {
  background: var(--sp-ghost-hover-bg);
}

.pending-row-action--primary {
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}

.pending-follow-up-row__editor {
  flex: 1;
  min-width: 0;
}

.pending-follow-up-row__editor :deep(.v-field__field) {
  font-size: 13px;
}

/* Enter/leave transition for the queue tray itself. */
.followup-enter-active,
.followup-leave-active {
  transition:
    opacity 0.18s ease,
    transform 0.18s ease;
}

.followup-enter-from,
.followup-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

/* Keep the tray aligned with the composer on phones; action labels give way
   to icon + shortcut hint, and row actions stay visible for touch. */
@media (max-width: 768px) {
  .pending-follow-ups {
    width: calc(100% - 20px);
  }

  .pending-action__label {
    display: none;
  }

  .pending-action {
    padding-inline: 6px;
  }

  .pending-follow-up-row__actions {
    opacity: 1;
  }
}

</style>
