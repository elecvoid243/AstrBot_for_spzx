<!-- Author: elecvoid243, 2026-06-28
     Added viewMode toggle (Unified / Split) for diff rendering.
     The split mode aligns del/add lines into a two-column layout
     (old on the left, new on the right), matching the standard
     side-by-side diff view (e.g. GitHub's split diff).

     All five call sites (ToolCallCard, GitDiffFileItem,
     FilePatchPanel, FileDiffResult, ThemeAwareMarkdownCodeBlock)
     automatically pick up the new toggle since it lives inside
     DiffPreview itself. The user choice is persisted to
     localStorage so it survives reloads. -->
<template>
  <div
    class="diff-preview"
    :class="{ 'is-dark': isDark, collapsed: isCollapsed, 'is-split': viewMode === 'split' }"
  >
    <!-- Summary header — always visible, clickable to toggle -->
    <button
      v-if="summary || filePath || statsAdds || statsDels"
      type="button"
      class="diff-header"
      @click="toggleCollapsed"
    >
      <div class="diff-header-left">
        <v-icon size="16" class="diff-header-icon"
          >mdi-file-document-edit-outline</v-icon
        >
        <span v-if="filePath" class="diff-file-path">{{ filePath }}</span>
      </div>
      <div class="diff-header-right">
        <template v-if="statsAdds || statsDels">
          <span v-if="statsAdds" class="diff-stats diff-stats-add"
            >+{{ statsAdds }}</span
          >
          <span v-if="statsDels" class="diff-stats diff-stats-del"
            >−{{ statsDels }}</span
          >
        </template>
        <!-- Unified / Split segmented toggle. Sits inside the header
             so it is always reachable, even when the body is collapsed.
             Stops propagation so clicking a button does not also toggle
             the body collapse. -->
        <div
          v-if="!overlayActive"
          class="diff-view-toggle"
          role="group"
          :aria-label="viewModeAriaLabel"
        >
          <button
            type="button"
            class="diff-view-toggle-btn"
            :class="{ active: viewMode === 'unified' }"
            :aria-pressed="viewMode === 'unified'"
            :title="unifiedLabel"
            @click.stop="setViewMode('unified')"
          >
            <v-icon size="14">mdi-format-align-justify</v-icon>
          </button>
          <button
            type="button"
            class="diff-view-toggle-btn"
            :class="{ active: viewMode === 'split' }"
            :aria-pressed="viewMode === 'split'"
            :title="splitLabel"
            @click.stop="setViewMode('split')"
          >
            <v-icon size="14">mdi-view-split-vertical</v-icon>
          </button>
        </div>
        <!-- Fullscreen button (spec 2026-06-30-diff-fullscreen-design.md §3.1) -->
        <button
          ref="fullscreenBtnRef"
          type="button"
          class="diff-fullscreen-btn"
          :title="tm('diffPreview.fullscreen.enter')"
          :aria-label="tm('diffPreview.fullscreen.enter')"
          @click.stop="enterFullscreen"
        >
          <v-icon size="14">mdi-fullscreen</v-icon>
        </button>
        <v-icon
          v-if="collapsible"
          size="18"
          class="diff-chevron"
          :class="{ expanded: !isCollapsed }"
        >
          mdi-chevron-right
        </v-icon>
      </div>
    </button>

    <!-- Summary text (e.g. "Replaced 1 occurrence(s)...") -->
    <div v-if="summary && !isCollapsed" class="diff-summary-text">
      {{ summary }}
    </div>

    <!-- Diff hunks — hidden when collapsed -->
    <div v-if="!isCollapsed" class="diff-body">
      <div v-if="showTruncationWarning" class="diff-truncation-warning">
        ⚠ Diff truncated (showing first
        {{ maxChars.toLocaleString() }} characters)
      </div>

      <!-- Unified mode: the original single-column layout.
           UI #8: hunk headers are now clickable buttons that fold /
           unfold the hunk. State is local to the component so a
           re-mount (file change) resets all folds — matches the
           "expansion state is per-file" mental model. -->
      <!-- 2026-10-06 overlay 模式:全文件 + diff 叠加(对齐成功时) -->
      <div v-if="overlayActive" class="overlay-body">
        <template v-for="row in overlayRows" :key="row.key">
          <div
            v-if="row.kind === 'gap'"
            class="overlay-gap"
            role="button"
            tabindex="0"
            @click="expandGap(row.gap.afterBaseLineno)"
            @keydown.enter.prevent="expandGap(row.gap.afterBaseLineno)"
          >
            {{ tm("diffPreview.overlay.expandLines", { n: row.gap.hiddenCount }) }}
          </div>
          <div
            v-else
            class="diff-line"
            :class="overlayLineClass(row.line)"
          >
            <span class="line-number old">{{ row.line.baseLineno ?? "" }}</span>
            <span class="line-prefix">{{ overlayPrefix(row.line.kind) }}</span>
            <span class="line-content" v-html="overlayLineHtml(row.line)"></span>
          </div>
        </template>
      </div>

      <template v-if="!overlayActive && viewMode === 'unified'">
        <div
          v-for="(hunk, hi) in parsedHunks"
          :key="hi"
          class="diff-hunk"
          :class="{ 'is-hunk-folded': collapsedHunks.has(hi) }"
        >
          <div
            class="hunk-header"
            role="button"
            tabindex="0"
            :aria-expanded="!collapsedHunks.has(hi)"
            @click="toggleHunk(hi)"
            @keydown="(e) => onHunkHeaderKeydown(hi, e)"
          >
            <v-icon
              size="12"
              class="hunk-chevron"
              :class="{ expanded: !collapsedHunks.has(hi) }"
            >
              mdi-chevron-right
            </v-icon>
            <span class="hunk-header-text">{{ hunk.header }}</span>
            <span class="hunk-header-count">
              {{ hunk.lines.length }}
            </span>
            <!-- Spec §6.1.2: per-hunk discard button. opt-in via `discardable` prop;
                 the other 4 DiffPreview callsite don't pass this prop, so the button
                 is not rendered there. -->
            <button
              v-if="discardable && onDiscardHunk"
              type="button"
              class="hunk-discard"
              :class="{
                'is-loading': isCurrentHunkDiscarding(hi),
                'is-confirming': confirmingHunkIndex === hi,
              }"
              :disabled="isCurrentHunkDiscarding(hi)"
              :aria-label="discardHunkAriaLabel(hi, hunk.header)"
              :aria-busy="isCurrentHunkDiscarding(hi) ? 'true' : 'false'"
              :title="discardHunkTitle(hi, hunk.header)"
              @click.stop="onDiscardHunkClick(hi, $event)"
            >
              <v-progress-circular
                v-if="isCurrentHunkDiscarding(hi)"
                indeterminate
                :size="12"
                :width="2"
              />
              <template v-else>
                <v-icon :size="14">
                  {{ confirmingHunkIndex === hi ? 'mdi-alert-circle' : 'mdi-content-cut' }}
                </v-icon>
                <span v-if="confirmingHunkIndex === hi" class="hunk-discard-confirm-label">
                  {{ tm('spcodeProjectLoad.diffPreview.hunkDiscard.confirmLabel') }}
                </span>
              </template>
            </button>
          </div>
          <div
            v-show="!collapsedHunks.has(hi)"
            class="diff-hunk-body"
          >
            <div
              v-for="(line, li) in hunk.lines"
              :key="li"
              class="diff-line"
              :class="[
                line.type,
                {
                  // 2026-08-13: del rows carry old-side comments.
                  'has-comment': isCommentable &&
                    (commentOnLine(line) !== undefined),
                  'is-hovered': isCommentable &&
                    hoveredUnifiedHunk === hi &&
                    ((!!line.newNo && hoveredUnifiedSide === 'new' &&
                      hoveredUnifiedLine === Number(line.newNo)) ||
                     (!!line.oldNo && hoveredUnifiedSide === 'old' &&
                      hoveredUnifiedLine === Number(line.oldNo))),
                },
              ]"
              @mouseenter="onUnifiedRowEnter(line, hi)"
              @mouseleave="onUnifiedRowLeave"
            >
              <!-- Inline-comment gutter: a fixed 24px column on EVERY
                   line (ctx / add / del). The outer v-if must NOT
                   gate on `line.newNo` — del rows have newNo="" but
                   still need the 24px placeholder so the line-prefix
                   and content stay column-aligned with ctx / add
                   rows. The inner <button> elements gate on the row's
                   own side (new for add/ctx, old for del). -->
              <span
                v-if="isCommentable"
                class="diff-line-gutter"
              >
                <button
                  v-if="unifiedGutterAddVisible(line, hi)"
                  type="button"
                  class="diff-comment-add"
                  :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.addButtonAria', { line: unifiedAnchorLine(line) })"
                  @click.stop="openEditorForLine(line)"
                >+</button>
                <button
                  v-else-if="commentOnLine(line)"
                  type="button"
                  class="diff-comment-indicator"
                  :title="commentOnLine(line)?.text ?? ''"
                  :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.indicatorAria', {
                    line: unifiedAnchorLine(line),
                    preview: commentOnLine(line)?.text ?? '',
                  })"
                  @click.stop="openEditorForComment(line)"
                >
                  <v-icon size="12">mdi-comment-text-outline</v-icon>
                </button>
              </span>
              <span class="line-number old">{{ line.oldNo }}</span>
              <span class="line-number new">{{ line.newNo }}</span>
              <span class="line-prefix">{{ line.prefix }}</span>
              <span class="line-content" v-html="lineContentHtml(line)"></span>
            </div>
          </div>
        </div>
      </template>

      <!-- Split mode: two-column layout, del/add lines are paired
           into rows by alignHunkLines. A row with a missing side
           keeps the cell empty so the visual columns stay aligned.
           The inline-comment gutter is anchored to the RIGHT cell
           (the new-side column) so we don't have to decide what
           "the line" means on a row with two distinct contents.

           UI #8 (symmetric to unified mode): the hunk header is a
           button that folds/unfolds the body. We share the same
           `collapsedHunks` Set with the unified view, so a fold
           decision carries over when the user toggles view modes
           mid-review (no UX surprise). The row v-for is wrapped in
           `.diff-hunk-body` so a single `v-show` collapses the
           whole hunk at once — rows stay in the DOM, preserving
           scroll position and any in-progress comment hover state
           when the user re-expands. -->
      <template v-else-if="!overlayActive">
        <div
          v-for="(hunk, hi) in splitHunks"
          :key="hi"
          class="diff-hunk diff-hunk-split"
          :class="{ 'is-hunk-folded': collapsedHunks.has(hi) }"
        >
          <div
            class="hunk-header"
            role="button"
            tabindex="0"
            :aria-expanded="!collapsedHunks.has(hi)"
            @click="toggleHunk(hi)"
            @keydown="(e) => onHunkHeaderKeydown(hi, e)"
          >
            <v-icon
              size="12"
              class="hunk-chevron"
              :class="{ expanded: !collapsedHunks.has(hi) }"
            >
              mdi-chevron-right
            </v-icon>
            <span class="hunk-header-text">{{ hunk.header }}</span>
            <span class="hunk-header-count">{{ hunk.rows.length }}</span>
            <!-- Spec §6.1.2: per-hunk discard button. opt-in via `discardable` prop;
                 the other 4 DiffPreview callsite don't pass this prop, so the button
                 is not rendered there. -->
            <button
              v-if="discardable && onDiscardHunk"
              type="button"
              class="hunk-discard"
              :class="{
                'is-loading': isCurrentHunkDiscarding(hi),
                'is-confirming': confirmingHunkIndex === hi,
              }"
              :disabled="isCurrentHunkDiscarding(hi)"
              :aria-label="discardHunkAriaLabel(hi, hunk.header)"
              :aria-busy="isCurrentHunkDiscarding(hi) ? 'true' : 'false'"
              :title="discardHunkTitle(hi, hunk.header)"
              @click.stop="onDiscardHunkClick(hi, $event)"
            >
              <v-progress-circular
                v-if="isCurrentHunkDiscarding(hi)"
                indeterminate
                :size="12"
                :width="2"
              />
              <template v-else>
                <v-icon :size="14">
                  {{ confirmingHunkIndex === hi ? 'mdi-alert-circle' : 'mdi-content-cut' }}
                </v-icon>
                <span v-if="confirmingHunkIndex === hi" class="hunk-discard-confirm-label">
                  {{ tm('spcodeProjectLoad.diffPreview.hunkDiscard.confirmLabel') }}
                </span>
              </template>
            </button>
          </div>
          <div
            v-show="!collapsedHunks.has(hi)"
            class="diff-hunk-body"
          >
            <div
              v-for="(row, ri) in hunk.rows"
              :key="ri"
              class="diff-row-split"
              :class="[
                row.kind,
                {
                  // 2026-08-13: del-only rows carry old-side comments.
                  'has-comment': isCommentable &&
                    ((!!row.right?.newNo &&
                      commentsByNewLine.has(Number(row.right.newNo))) ||
                     (!!row.left?.oldNo && !row.right &&
                      commentsByOldLine.has(Number(row.left.oldNo)))),
                  // Hunk index guard: `ri` is the row index WITHIN
                  // this hunk, so two hunks with a row at the same
                  // offset would otherwise both light up. Pairing
                  // (hi, ri) makes the key globally unique.
                  'is-hovered': isCommentable &&
                    (!!row.left || !!row.right) &&
                    hoveredSplitRow === ri &&
                    hoveredSplitHunk === hi,
                },
              ]"
              @mouseenter="onSplitRowEnter(ri, row, hi)"
              @mouseleave="onSplitRowLeave"
            >
              <div class="diff-cell left">
                <!-- 2026-08-13: inline-comment gutter for del-only rows
                     (no new-side line) — anchors to the OLD side. -->
                <span
                  v-if="isCommentable && !row.right && row.left?.oldNo"
                  class="diff-line-gutter"
                >
                  <button
                    v-if="hoveredSplitRow === ri &&
                      hoveredSplitHunk === hi &&
                      !commentsByOldLine.has(Number(row.left.oldNo))"
                    type="button"
                    class="diff-comment-add"
                    :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.addButtonAria', { line: row.left.oldNo })"
                    @click.stop="openNewEditor(Number(row.left.oldNo), 'old')"
                  >+</button>
                  <button
                    v-else-if="commentsByOldLine.has(Number(row.left.oldNo))"
                    type="button"
                    class="diff-comment-indicator"
                    :title="commentsByOldLine.get(Number(row.left.oldNo))?.text ?? ''"
                    :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.indicatorAria', {
                      line: row.left.oldNo,
                      preview: commentsByOldLine.get(Number(row.left.oldNo))?.text ?? '',
                    })"
                    @click.stop="openEditEditor(commentsByOldLine.get(Number(row.left.oldNo))?.id ?? '')"
                  >
                    <v-icon size="12">mdi-comment-text-outline</v-icon>
                  </button>
                </span>
                <span class="line-number">{{ row.left?.oldNo ?? '' }}</span>
                <span class="line-prefix">{{ row.left?.prefix ?? '' }}</span>
                <span class="line-content" v-html="lineContentHtml(row.left)"></span>
              </div>
              <div class="diff-cell right">
                <!-- Inline-comment gutter: first child of the right
                     cell, absolutely positioned to the left edge via
                     CSS. Only on rows that have a new-side line. -->
                <span
                  v-if="isCommentable && row.right?.newNo"
                  class="diff-line-gutter"
                >
                  <button
                    v-if="hoveredSplitRow === ri &&
                      hoveredSplitHunk === hi &&
                      !commentsByNewLine.has(Number(row.right!.newNo))"
                    type="button"
                    class="diff-comment-add"
                    :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.addButtonAria', { line: row.right!.newNo })"
                    @click.stop="openNewEditor(Number(row.right!.newNo), 'new')"
                  >+</button>
                  <button
                    v-else-if="commentsByNewLine.has(Number(row.right!.newNo))"
                    type="button"
                    class="diff-comment-indicator"
                    :title="commentsByNewLine.get(Number(row.right!.newNo))?.text ?? ''"
                    :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.indicatorAria', {
                      line: row.right!.newNo,
                      preview: commentsByNewLine.get(Number(row.right!.newNo))?.text ?? '',
                    })"
                    @click.stop="openEditEditor(commentsByNewLine.get(Number(row.right!.newNo))?.id ?? '')"
                  >
                    <v-icon size="12">mdi-comment-text-outline</v-icon>
                  </button>
                </span>
                <span class="line-number">{{ row.right?.newNo ?? '' }}</span>
                <span class="line-prefix">{{ row.right?.prefix ?? '' }}</span>
                <span class="line-content" v-html="lineContentHtml(row.right)"></span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <div v-if="collapsedOverflow > 0" class="diff-overflow-bar">
        <button
          type="button"
          class="diff-show-more"
          @click="showAllLines = true"
        >
          Show all {{ totalLines.toLocaleString() }} lines ({{
            collapsedOverflow
          }}
          more)
        </button>
      </div>
    </div>

    <!-- Inline-comment editor dock. The editor is wrapped in a
         `position: sticky; bottom: 0` dock so it pins to the bottom
         of the *visible* scroll viewport instead of sitting at the
         very end of the diff. Previously the editor rendered as a
         plain sibling after the body; opening it focused the
         textarea, and the browser's default focus-scroll yanked the
         viewport down to the diff tail — disorienting on long diffs.
         With the dock sticky, the editor is already inside the
         viewport when it mounts, so the same focus() no longer
         triggers a jump and the user keeps reading the line they
         clicked. The dock carries the v-if (not the editor) so a
         closed editor leaves no empty sticky bar behind. Hidden
         when collapsed — no point editing a comment whose context
         the user can't see. `v-if` (not v-show) fully tears down
         the editor DOM (and its textarea keydown listener). -->
    <Teleport :to="sidebarScrollContainer" :disabled="!sidebarScrollContainer">
      <div
        v-if="!isCollapsed && activeEditLine !== null && isCommentable"
        class="diff-comment-dock"
      >
        <FileCommentEditor
          :line="activeEditLine"
          :comment-id="activeEditCommentId"
          :side="activeEditSide"
          :initial-text="editorInitialText"
          :line-content="editorContext?.lineContent ?? null"
          :context-before="editorContext?.contextBefore ?? null"
          :context-after="editorContext?.contextAfter ?? null"
          :file-path="filePath"
          @save="onSaveComment"
          @cancel="closeEditor"
          @delete="onDeleteComment"
        />
      </div>
    </Teleport>
  </div>

  <!-- Fullscreen overlay — Teleported to <body> to escape fixed-position
       stacking contexts. Shares same reactive refs as normal view.
       Spec 2026-06-30-diff-fullscreen-design.md §3.2 -->
  <Teleport to="body">
    <div
      v-if="isFullscreen"
      ref="overlayRef"
      class="diff-fullscreen-overlay"
      role="dialog"
      aria-modal="true"
      :aria-label="tm('diffPreview.fullscreen.ariaLabel')"
      tabindex="-1"
      @keydown.escape="exitFullscreen"
    >
      <!-- Back button: fixed top-right corner -->
      <button
        type="button"
        class="diff-fullscreen-back-btn"
        :title="tm('diffPreview.fullscreen.exit')"
        :aria-label="tm('diffPreview.fullscreen.exit')"
        @click="exitFullscreen"
      >
        <v-icon size="20">mdi-fullscreen-exit</v-icon>
        <span class="diff-fullscreen-back-label">{{
          tm("diffPreview.fullscreen.exitLabel")
        }}</span>
      </button>

      <!-- Fullscreen diff content — same refs as normal view.
           The header and diff body are re-rendered here so they appear
           inside the overlay. The isFullscreen ref is shared, so
           clicking the fullscreen button inside the overlay is a
           no-op (the button is at the normal view and is not rendered
           here because it is behind the overlay). -->
      <div ref="fullscreenBodyRef" class="diff-fullscreen-body">
        <div
          class="diff-preview is-fullscreen"
          :class="{ 'is-dark': isDark, collapsed: isCollapsed, 'is-split': viewMode === 'split' }"
        >
          <!-- Same header — the fullscreen button in the normal header
               is behind the overlay, so it doesn't appear here. -->
          <button
            v-if="summary || filePath || statsAdds || statsDels"
            type="button"
            class="diff-header"
            @click="toggleCollapsed"
          >
            <div class="diff-header-left">
              <v-icon size="16" class="diff-header-icon"
                >mdi-file-document-edit-outline</v-icon
              >
              <span v-if="filePath" class="diff-file-path">{{ filePath }}</span>
            </div>
            <div class="diff-header-right">
              <template v-if="statsAdds || statsDels">
                <span v-if="statsAdds" class="diff-stats diff-stats-add"
                  >+{{ statsAdds }}</span
                >
                <span v-if="statsDels" class="diff-stats diff-stats-del"
                  >−{{ statsDels }}</span
                >
              </template>
              <div
                v-if="!overlayActive"
                class="diff-view-toggle"
                role="group"
                :aria-label="viewModeAriaLabel"
              >
                <button
                  type="button"
                  class="diff-view-toggle-btn"
                  :class="{ active: viewMode === 'unified' }"
                  :aria-pressed="viewMode === 'unified'"
                  :title="unifiedLabel"
                  @click.stop="setViewMode('unified')"
                >
                  <v-icon size="14">mdi-format-align-justify</v-icon>
                </button>
                <button
                  type="button"
                  class="diff-view-toggle-btn"
                  :class="{ active: viewMode === 'split' }"
                  :aria-pressed="viewMode === 'split'"
                  :title="splitLabel"
                  @click.stop="setViewMode('split')"
                >
                  <v-icon size="14">mdi-view-split-vertical</v-icon>
                </button>
              </div>
              <!-- 2026-08-09 (elecvoid243): bulk fold controls. Only in
                   fullscreen — the inline card stays lightweight; folding
                   every hunk is the fullscreen use-case (long diffs). -->
              <span
                v-if="parsedHunks.length > 0"
                class="diff-hunk-bulk"
                role="group"
                :aria-label="tm('diffPreview.fullscreen.bulkAria')"
              >
                <button
                  type="button"
                  class="diff-hunk-bulk-btn"
                  :title="tm('diffPreview.fullscreen.expandAll')"
                  @click.stop="expandAllHunks"
                >
                  <v-icon size="14">mdi-chevron-double-down</v-icon>
                  <span>{{ tm("diffPreview.fullscreen.expandAll") }}</span>
                </button>
                <button
                  type="button"
                  class="diff-hunk-bulk-btn"
                  :title="tm('diffPreview.fullscreen.collapseAll')"
                  @click.stop="collapseAllHunks"
                >
                  <v-icon size="14">mdi-chevron-double-right</v-icon>
                  <span>{{ tm("diffPreview.fullscreen.collapseAll") }}</span>
                </button>
              </span>
              <!-- Fullscreen button is intentionally omitted here:
                   the overlay has its own "Back" exit button above. -->
              <v-icon
                v-if="collapsible"
                size="18"
                class="diff-chevron"
                :class="{ expanded: !isCollapsed }"
              >
                mdi-chevron-right
              </v-icon>
            </div>
          </button>

          <!-- Summary text -->
          <div v-if="summary && !isCollapsed" class="diff-summary-text">
            {{ summary }}
          </div>

          <!-- Diff body -->
          <div v-if="!isCollapsed" class="diff-body">
            <div v-if="showTruncationWarning" class="diff-truncation-warning">
              ⚠ Diff truncated (showing first
              {{ maxChars.toLocaleString() }} characters)
            </div>

            <!-- Unified mode (fullscreen copy) -->
            <!-- 2026-10-06 overlay 模式:全文件 + diff 叠加(对齐成功时) -->
      <div v-if="overlayActive" class="overlay-body">
        <template v-for="row in overlayRows" :key="row.key">
          <div
            v-if="row.kind === 'gap'"
            class="overlay-gap"
            role="button"
            tabindex="0"
            @click="expandGap(row.gap.afterBaseLineno)"
            @keydown.enter.prevent="expandGap(row.gap.afterBaseLineno)"
          >
            {{ tm("diffPreview.overlay.expandLines", { n: row.gap.hiddenCount }) }}
          </div>
          <div
            v-else
            class="diff-line"
            :class="overlayLineClass(row.line)"
          >
            <span class="line-number old">{{ row.line.baseLineno ?? "" }}</span>
            <span class="line-prefix">{{ overlayPrefix(row.line.kind) }}</span>
            <span class="line-content" v-html="overlayLineHtml(row.line)"></span>
          </div>
        </template>
      </div>

      <template v-if="!overlayActive && viewMode === 'unified'">
              <div
                v-for="(hunk, hi) in parsedHunks"
                :key="hi"
                class="diff-hunk"
                :class="{ 'is-hunk-folded': collapsedHunks.has(hi) }"
              >
                <div
                  class="hunk-header"
                  role="button"
                  tabindex="0"
                  :aria-expanded="!collapsedHunks.has(hi)"
                  @click="toggleHunk(hi)"
                  @keydown="(e) => onHunkHeaderKeydown(hi, e)"
                >
                  <v-icon
                    size="12"
                    class="hunk-chevron"
                    :class="{ expanded: !collapsedHunks.has(hi) }"
                  >
                    mdi-chevron-right
                  </v-icon>
                  <span class="hunk-header-text">{{ hunk.header }}</span>
                  <span class="hunk-header-count">{{
                    hunk.lines.length
                  }}</span>
                  <!-- Spec §6.1.2: per-hunk discard button. opt-in via `discardable` prop;
                       the other 4 DiffPreview callsite don't pass this prop, so the button
                       is not rendered there. -->
                  <button
                    v-if="discardable && onDiscardHunk"
                    type="button"
                    class="hunk-discard"
                    :class="{
                      'is-loading': isCurrentHunkDiscarding(hi),
                      'is-confirming': confirmingHunkIndex === hi,
                    }"
                    :disabled="isCurrentHunkDiscarding(hi)"
                    :aria-label="discardHunkAriaLabel(hi, hunk.header)"
                    :aria-busy="isCurrentHunkDiscarding(hi) ? 'true' : 'false'"
                    :title="discardHunkTitle(hi, hunk.header)"
                    @click.stop="onDiscardHunkClick(hi, $event)"
                  >
                    <v-progress-circular
                      v-if="isCurrentHunkDiscarding(hi)"
                      indeterminate
                      :size="12"
                      :width="2"
                    />
                    <template v-else-if="!overlayActive">
                      <v-icon :size="14">
                        {{ confirmingHunkIndex === hi ? 'mdi-alert-circle' : 'mdi-content-cut' }}
                      </v-icon>
                      <span v-if="confirmingHunkIndex === hi" class="hunk-discard-confirm-label">
                        {{ tm('spcodeProjectLoad.diffPreview.hunkDiscard.confirmLabel') }}
                      </span>
                    </template>
                  </button>
                </div>
                <div
                  v-show="!collapsedHunks.has(hi)"
                  class="diff-hunk-body"
                >
                  <div
                    v-for="(line, li) in hunk.lines"
                    :key="li"
                    class="diff-line"
                    :class="[
                      line.type,
                      {
                        'has-comment': isCommentable &&
                          (commentOnLine(line) !== undefined),
                        // Fullscreen copy mirrors the normal view:
                        // hunk guard scopes the hover to the
                        // hunk the cursor is in.
                        'is-hovered': isCommentable &&
                          hoveredUnifiedHunk === hi &&
                          ((!!line.newNo && hoveredUnifiedSide === 'new' &&
                            hoveredUnifiedLine === Number(line.newNo)) ||
                           (!!line.oldNo && hoveredUnifiedSide === 'old' &&
                            hoveredUnifiedLine === Number(line.oldNo))),
                      },
                    ]"
                    @mouseenter="onUnifiedRowEnter(line, hi)"
                    @mouseleave="onUnifiedRowLeave"
                  >
                  <span
                    v-if="isCommentable"
                    class="diff-line-gutter"
                  >
                    <button
                      v-if="unifiedGutterAddVisible(line, hi)"
                      type="button"
                      class="diff-comment-add"
                      :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.addButtonAria', { line: unifiedAnchorLine(line) })"
                      @click.stop="openEditorForLine(line)"
                    >+</button>
                    <button
                      v-else-if="commentOnLine(line)"
                      type="button"
                      class="diff-comment-indicator"
                      :title="commentOnLine(line)?.text ?? ''"
                      :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.indicatorAria', {
                        line: unifiedAnchorLine(line),
                        preview: commentOnLine(line)?.text ?? '',
                      })"
                      @click.stop="openEditorForComment(line)"
                    >
                      <v-icon size="12">mdi-comment-text-outline</v-icon>
                    </button>
                  </span>
                    <span class="line-number old">{{ line.oldNo }}</span>
                    <span class="line-number new">{{ line.newNo }}</span>
                    <span class="line-prefix">{{ line.prefix }}</span>
                    <span class="line-content" v-html="lineContentHtml(line)"></span>
                  </div>
                </div>
              </div>
            </template>

            <!-- Split mode (fullscreen copy). Mirrors the normal
                 view: same `collapsedHunks` Set, same toggleHunk
                 handler, same hunk-header button. Keeping the two
                 views symmetric means the user's fold decisions
                 survive the fullscreen toggle, and full-screen +
                 folded hunks give a "scannable overview of hunks"
                 mode not available elsewhere. -->
            <template v-else>
              <div
                v-for="(hunk, hi) in splitHunks"
                :key="hi"
                class="diff-hunk diff-hunk-split"
                :class="{ 'is-hunk-folded': collapsedHunks.has(hi) }"
              >
                <div
                  class="hunk-header"
                  role="button"
                  tabindex="0"
                  :aria-expanded="!collapsedHunks.has(hi)"
                  @click="toggleHunk(hi)"
                  @keydown="(e) => onHunkHeaderKeydown(hi, e)"
                >
                  <v-icon
                    size="12"
                    class="hunk-chevron"
                    :class="{ expanded: !collapsedHunks.has(hi) }"
                  >
                    mdi-chevron-right
                  </v-icon>
                  <span class="hunk-header-text">{{ hunk.header }}</span>
                  <span class="hunk-header-count">{{ hunk.rows.length }}</span>
                  <!-- Spec §6.1.2: per-hunk discard button. opt-in via `discardable` prop;
                       the other 4 DiffPreview callsite don't pass this prop, so the button
                       is not rendered there. -->
                  <button
                    v-if="discardable && onDiscardHunk"
                    type="button"
                    class="hunk-discard"
                    :class="{
                      'is-loading': isCurrentHunkDiscarding(hi),
                      'is-confirming': confirmingHunkIndex === hi,
                    }"
                    :disabled="isCurrentHunkDiscarding(hi)"
                    :aria-label="discardHunkAriaLabel(hi, hunk.header)"
                    :aria-busy="isCurrentHunkDiscarding(hi) ? 'true' : 'false'"
                    :title="discardHunkTitle(hi, hunk.header)"
                    @click.stop="onDiscardHunkClick(hi, $event)"
                  >
                    <v-progress-circular
                      v-if="isCurrentHunkDiscarding(hi)"
                      indeterminate
                      :size="12"
                      :width="2"
                    />
                    <template v-else>
                      <v-icon :size="14">
                        {{ confirmingHunkIndex === hi ? 'mdi-alert-circle' : 'mdi-content-cut' }}
                      </v-icon>
                      <span v-if="confirmingHunkIndex === hi" class="hunk-discard-confirm-label">
                        {{ tm('spcodeProjectLoad.diffPreview.hunkDiscard.confirmLabel') }}
                      </span>
                    </template>
                  </button>
                </div>
                <div
                  v-show="!collapsedHunks.has(hi)"
                  class="diff-hunk-body"
                >
                  <div
                    v-for="(row, ri) in hunk.rows"
                    :key="ri"
                    class="diff-row-split"
                    :class="[
                      row.kind,
                      {
                        'has-comment': isCommentable &&
                          ((!!row.right?.newNo &&
                            commentsByNewLine.has(Number(row.right.newNo))) ||
                           (!!row.left?.oldNo && !row.right &&
                            commentsByOldLine.has(Number(row.left.oldNo)))),
                        // Fullscreen copy mirrors the normal view:
                        // hunk guard scopes the hover to the hunk
                        // the cursor is in. (Bugfix for per-hunk
                        // `ri` collision when multiple hunks share
                        // the same row offset.)
                        'is-hovered': isCommentable &&
                          (!!row.left || !!row.right) &&
                          hoveredSplitRow === ri &&
                          hoveredSplitHunk === hi,
                      },
                    ]"
                    @mouseenter="onSplitRowEnter(ri, row, hi)"
                    @mouseleave="onSplitRowLeave"
                  >
                  <div class="diff-cell left">
                    <!-- 2026-08-13: gutter for del-only rows (old side). -->
                    <span
                      v-if="isCommentable && !row.right && row.left?.oldNo"
                      class="diff-line-gutter"
                    >
                      <button
                        v-if="hoveredSplitRow === ri &&
                          hoveredSplitHunk === hi &&
                          !commentsByOldLine.has(Number(row.left.oldNo))"
                        type="button"
                        class="diff-comment-add"
                        :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.addButtonAria', { line: row.left.oldNo })"
                        @click.stop="openNewEditor(Number(row.left.oldNo), 'old')"
                      >+</button>
                      <button
                        v-else-if="commentsByOldLine.has(Number(row.left.oldNo))"
                        type="button"
                        class="diff-comment-indicator"
                        :title="commentsByOldLine.get(Number(row.left.oldNo))?.text ?? ''"
                        :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.indicatorAria', {
                          line: row.left.oldNo,
                          preview: commentsByOldLine.get(Number(row.left.oldNo))?.text ?? '',
                        })"
                        @click.stop="openEditEditor(commentsByOldLine.get(Number(row.left.oldNo))?.id ?? '')"
                      >
                        <v-icon size="12">mdi-comment-text-outline</v-icon>
                      </button>
                    </span>
                    <span class="line-number">{{ row.left?.oldNo ?? '' }}</span>
                    <span class="line-prefix">{{ row.left?.prefix ?? '' }}</span>
                    <span class="line-content" v-html="lineContentHtml(row.left)"></span>
                  </div>
                  <div class="diff-cell right">
                    <span
                      v-if="isCommentable && row.right?.newNo"
                      class="diff-line-gutter"
                    >
                      <button
                        v-if="hoveredSplitRow === ri &&
                          hoveredSplitHunk === hi &&
                          !commentsByNewLine.has(Number(row.right!.newNo))"
                        type="button"
                        class="diff-comment-add"
                        :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.addButtonAria', { line: row.right!.newNo })"
                        @click.stop="openNewEditor(Number(row.right!.newNo), 'new')"
                      >+</button>
                      <button
                        v-else-if="commentsByNewLine.has(Number(row.right!.newNo))"
                        type="button"
                        class="diff-comment-indicator"
                        :title="commentsByNewLine.get(Number(row.right!.newNo))?.text ?? ''"
                        :aria-label="tm('spcodeProjectLoad.fileBrowser.comment.indicatorAria', {
                          line: row.right!.newNo,
                          preview: commentsByNewLine.get(Number(row.right!.newNo))?.text ?? '',
                        })"
                        @click.stop="openEditEditor(commentsByNewLine.get(Number(row.right!.newNo))?.id ?? '')"
                      >
                        <v-icon size="12">mdi-comment-text-outline</v-icon>
                      </button>
                    </span>
                      <span class="line-number">{{ row.right?.newNo ?? '' }}</span>
                      <span class="line-prefix">{{ row.right?.prefix ?? '' }}</span>
                      <span class="line-content" v-html="lineContentHtml(row.right)"></span>
                    </div>
                  </div>
                </div>
              </div>
            </template>

            <div v-if="collapsedOverflow > 0" class="diff-overflow-bar">
              <button
                type="button"
                class="diff-show-more"
                @click="showAllLines = true"
              >
                Show all {{ totalLines.toLocaleString() }} lines ({{
                  collapsedOverflow
                }}
                more)
              </button>
            </div>
          </div>

          <!-- Inline-comment editor dock (fullscreen copy),
               teleported to the fullscreen scroll container
               (.diff-fullscreen-body) for the same reason as the
               normal view: a `position: sticky` element is
               constrained to its *parent's* box, and in place the
               parent is `.diff-preview.is-fullscreen` with the dock
               sitting at its very end — so the sticky window
               collapses to the document tail and the editor's
               focus() yanks the viewport there. Teleporting makes
               the scroller the dock's parent, restoring the
               standard sticky-to-viewport-bottom behaviour. The
               overlay (and therefore this ref) only exists while
               fullscreen, so `:disabled` falls back to in-place
               rendering for the single frame before the ref binds.
               Same reactive state as the normal view, so toggling
               fullscreen migrates the editor between the two docks
               without re-creating state. -->
          <Teleport :to="fullscreenBodyRef" :disabled="!fullscreenBodyRef">
            <div
              v-if="!isCollapsed && activeEditLine !== null && isCommentable"
              class="diff-comment-dock"
            >
              <FileCommentEditor
                :line="activeEditLine"
                :comment-id="activeEditCommentId"
                :side="activeEditSide"
                :initial-text="editorInitialText"
                :line-content="editorContext?.lineContent ?? null"
                :context-before="editorContext?.contextBefore ?? null"
                :context-after="editorContext?.contextAfter ?? null"
                :file-path="filePath"
                @save="onSaveComment"
                @cancel="closeEditor"
                @delete="onDeleteComment"
              />
            </div>
          </Teleport>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import {
  computed,
  ref,
  nextTick,
  watch,
  onBeforeUnmount,
  onMounted,
  inject,
  type Ref,
} from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type { GitDiffScope } from "@/composables/parseSpcodeGitDiff";
import {
  ensureShikiLanguages,
  escapeHtml,
  SHIKI_THEMES,
} from "@/utils/shiki";
import { detectLanguage } from "@/utils/codeLanguage";
import {
  computeIntraLineSegments,
  type IntraLineSegment,
} from "@/utils/intraLineDiff";
import {
  extractLineContext,
  useFileComments,
  type DiffHunkContext,
  type FileComment,
  type LineContext,
} from "@/composables/useFileComments";
import {
  buildHunkPatchText,
  parseUnifiedDiff,
  extractDiffContent,
  type DiffHunk,
  type DiffLine,
} from "@/utils/diffHunkPatch";
import {
  alignOverlay,
  type OverlayGap,
  type OverlayLine,
} from "@/utils/diffOverlayAlign";
import FileCommentEditor from "./FileCommentEditor.vue";

// A single visual row in split mode: holds the old-side line (or null
// when the row is a pure addition) and the new-side line (or null
// when the row is a pure deletion). `kind` drives the background tint
// so the user can tell ctx / modified / del-only / add-only apart at
// a glance.
type SplitRowKind = "ctx" | "modified" | "del-only" | "add-only";

interface SplitRow {
  left: DiffLine | null;
  right: DiffLine | null;
  kind: SplitRowKind;
}

interface SplitHunk {
  header: string;
  rows: SplitRow[];
}

type ViewMode = "unified" | "split";

// ── Props ──────────────────────────────────────────────────────────

const props = withDefaults(
  defineProps<{
    content: string;
    filePath?: string;
    summary?: string;
    /**
     * 2026-10-06 range-compare overlay: 提供时进入「全文件 + diff 叠加」
     * 模式——以该基准全文为骨架渲染,`content` 的 hunk 叠加到对应行。
     * 对齐失败(hunk 与基准行不匹配)自动降级为普通 patch 渲染。
     * overlay 模式仅 unified、无评论 gutter、隐藏 unified/split 切换。
     */
    baseContent?: string;
    maxLines?: number;
    maxChars?: number;
    collapsible?: boolean;
    isDark?: boolean;
    /**
     * When true (default), lines that exist in the new file (ctx +
     * add) expose an inline-comment gutter; lines that have a
     * comment already expose an indicator. Set to false to disable
     * the comment UI for a particular DiffPreview instance (e.g. for
     * a synthetic stub the user shouldn't be reviewing).
     */
    commentable?: boolean;
    /**
     * Pre-filtered comments for this file. When omitted, DiffPreview
     * pulls from the global useFileComments() store on its own.
     * Exposed as a prop so a parent that already maintains a scoped
     * comment list can pass it in without re-deriving.
     */
    comments?: FileComment[];
    /**
     * Active diff scope that produced `content`. Forwarded verbatim
     * through `onDiscardHunk` so the backend can pick the right
     * `git apply --reverse[ --cached]` path. Optional — when omitted,
     * `onDiscardHunk` is called without a `scope` and the backend
     * falls back to porcelain auto-detect (v1 behaviour).
     */
    scope?: GitDiffScope;
    onDiscardHunk?: (params: {
      file: string;
      hunkIndex: number;
      patchText: string;
      scope: GitDiffScope;
    }) => void;
    /** Set of `${discardKeyPrefix}#${hunkIndex}` keys currently in flight. */
    discardingHunks?: ReadonlySet<string>;
    /** Used as the key prefix when checking `discardingHunks`. Defaults to filePath. */
    discardKeyPrefix?: string;
    /** When true (and onDiscardHunk is set), render the per-hunk discard button. */
    discardable?: boolean;
  }>(),
  {
    filePath: "",
    summary: "",
    baseContent: "",
    maxLines: 30,
    maxChars: 2000,
    collapsible: true,
    isDark: false,
    commentable: true,
    comments: () => [],
    discardingHunks: () => new Set<string>(),
    discardKeyPrefix: "",
    discardable: false,
    // No default for `scope` — the optional prop intentionally
    // doesn't fall back to a single scope, because the discard button
    // is only rendered in sidebar-driven contexts where the parent
    // always supplies a real scope (GitDiffFileItem → BodyContent →
    // Sidebar). Leaving it undefined keeps legacy ToolCallCard /
    // FilePatchPanel callers (no scope) untouched.
  },
);

const isCurrentHunkDiscarding = (hi: number): boolean => {
  const prefix = props.discardKeyPrefix || props.filePath;
  return props.discardingHunks.has(`${prefix}#${hi}`);
};

// ─── 2026-10-06 overlay mode (range-compare full-file overlay) ─────
// Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-frontend-design.md §4
const overlayResult = computed(() =>
  props.baseContent ? alignOverlay(props.baseContent, props.content) : null,
);
/** overlay 仅在对齐成功时激活;失败自动降级普通 patch 渲染 */
const overlayActive = computed(
  () => overlayResult.value !== null && overlayResult.value.ok,
);
/** 已展开的间隙(afterBaseLineno 集合);文件/patch 变化时重置 */
const expandedGaps = ref<Set<number>>(new Set());
watch(
  () => [props.baseContent, props.content],
  () => {
    expandedGaps.value = new Set();
  },
);
function expandGap(afterBaseLineno: number): void {
  const s = new Set(expandedGaps.value);
  s.add(afterBaseLineno);
  expandedGaps.value = s;
}

type OverlayRow =
  | { kind: "line"; line: OverlayLine; key: string }
  | { kind: "gap"; gap: OverlayGap; key: string };

/** 渲染行序列:未展开间隙的隐藏行被剔除,原位插入间隙折叠条 */
const overlayRows = computed<OverlayRow[]>(() => {
  const r = overlayResult.value;
  if (!r || !r.ok) return [];
  const hidden = new Set<number>();
  for (const g of r.gaps) {
    if (expandedGaps.value.has(g.afterBaseLineno)) continue;
    for (
      let i = g.afterBaseLineno + 1;
      i <= g.afterBaseLineno + g.hiddenCount;
      i++
    ) {
      hidden.add(i);
    }
  }
  const gapAfter = new Map(r.gaps.map((g) => [g.afterBaseLineno, g]));
  const rows: OverlayRow[] = [];
  // afterBaseLineno=0 的前导间隙:锚点行不存在,插在最前
  const leadGap = gapAfter.get(0);
  if (leadGap && !expandedGaps.value.has(0)) {
    rows.push({ kind: "gap", gap: leadGap, key: "g-0" });
  }
  for (const l of r.lines) {
    if (l.baseLineno !== null && hidden.has(l.baseLineno)) continue;
    rows.push({
      kind: "line",
      line: l,
      key: `l-${l.baseLineno ?? "add"}-${rows.length}`,
    });
    if (l.baseLineno !== null) {
      const g = gapAfter.get(l.baseLineno);
      if (g && !expandedGaps.value.has(g.afterBaseLineno)) {
        rows.push({ kind: "gap", gap: g, key: `g-${g.afterBaseLineno}` });
      }
    }
  }
  return rows;
});

function overlayPrefix(kind: OverlayLine["kind"]): string {
  return kind === "add" ? "+" : kind === "del" ? "−" : " ";
}

/** 复用统一视图的语法高亮:OverlayLine → DiffLine 形状适配 */
function overlayLineHtml(l: OverlayLine): string {
  return lineContentHtml({
    type: l.kind === "add" ? "add" : l.kind === "del" ? "del" : "ctx",
    prefix: overlayPrefix(l.kind),
    content: l.text,
    oldNo: l.baseLineno !== null ? String(l.baseLineno) : "",
    newNo: "",
  });
}

function overlayLineClass(l: OverlayLine): string {
  return l.kind === "context" ? "ctx" : l.kind;
}

const { tm } = useModuleI18n("features/chat");

// Scroll container that owns the diff in the sidebar view, injected
// from GitDiffSidebar. The comment dock <Teleport>s to it so it
// becomes a direct child of the scroller and `position: sticky;
// bottom: 0` sticks reliably. In-place sticky does NOT work here
// because the dock is nested deep inside GitDiffFileItem /
// DiffDirectoryNode, and those layout ancestors disrupt the sticky
// scroll-ancestor / containing-block relationship — the symptom was
// the dock sitting at the document tail and the editor's focus()
// yanking the viewport down there on long diffs. When there is no
// provider (DiffPreview embedded in a ToolCallCard / file preview /
// document manager) the inject yields null, the Teleport is
// disabled, and the dock falls back to in-place rendering — i.e.
// prior behaviour, no regression for those callers.
const sidebarScrollContainer = inject<Ref<HTMLElement | null>>(
  "diffScrollContainer",
  ref<HTMLElement | null>(null),
);

// Template ref for the fullscreen overlay's scroll container
// (.diff-fullscreen-body). The fullscreen comment dock <Teleport>s
// to it (see the fullscreen dock in the template) for the same
// reason as the normal view: a `position: sticky` element is
// constrained to its parent's box, and in place that parent is
// `.diff-preview.is-fullscreen` with the dock at its very end, so
// the sticky window collapses to the document tail. Null until the
// overlay renders; the dock's `:disabled` falls back to in-place
// for the single frame before the ref binds.
const fullscreenBodyRef = ref<HTMLElement | null>(null);

// ── State ──────────────────────────────────────────────────────────

const isCollapsed = ref(false);
const showAllLines = ref(false);
// Fullscreen is declared next to the inline state so the effective
// line cap can treat both as independent expand signals (fullscreen
// always renders the full diff; the inline "show all" state stays
// untouched when entering or leaving fullscreen).
const isFullscreen = ref(false);
const effectiveMaxLines = computed(() =>
  isFullscreen.value || showAllLines.value ? Infinity : props.maxLines,
);

// UI #8: per-hunk fold state. Local to the component, so re-mounting
// the diff (e.g. switching files) resets all folds — matches the
// "expansion state is per-file" mental model. We use a Set<number>
// of hunk indices; small memory footprint even for 100+ hunks.
const collapsedHunks = ref<Set<number>>(new Set());

// Spec §2 decision #3 + §6.1.3: two-click inline confirmation. First click
// sets `confirmingHunkIndex`; second click within 3s executes the discard.
// The setTimeout handle is kept on a module-local variable so onBeforeUnmount
// can clear it even if the component tears down mid-confirmation.
const confirmingHunkIndex = ref<number | null>(null);
let confirmTimer: ReturnType<typeof setTimeout> | null = null;

function toggleHunk(idx: number): void {
  // Spec §2 decision #12 + §6.1.4: confirmation state locks hunk folding.
  // Clicking the header in "Confirm?" state is silently dropped so the
  // user's confirm intent isn't accidentally flipped to "cancel".
  if (confirmingHunkIndex.value !== null) return;
  const next = new Set(collapsedHunks.value);
  if (next.has(idx)) next.delete(idx);
  else next.add(idx);
  collapsedHunks.value = next;
}

// 2026-08-09 (elecvoid243): bulk fold controls (fullscreen only).
// Expand / collapse every hunk at once; shares toggleHunk's guard so
// a pending discard confirmation can't be interrupted by a bulk flip.
function expandAllHunks(): void {
  if (confirmingHunkIndex.value !== null) return;
  collapsedHunks.value = new Set();
}

function collapseAllHunks(): void {
  if (confirmingHunkIndex.value !== null) return;
  collapsedHunks.value = new Set(parsedHunks.value.map((_, i) => i));
}

function onDiscardHunkClick(hi: number, e: MouseEvent): void {
  e.stopPropagation();   // prevent bubbling to header's toggleHunk
  if (!props.onDiscardHunk || !props.discardable) return;
  if (isCurrentHunkDiscarding(hi)) return;
  if (confirmingHunkIndex.value === hi) {
    // Second click: execute the discard.
    if (confirmTimer) {
      clearTimeout(confirmTimer);
      confirmTimer = null;
    }
    confirmingHunkIndex.value = null;
    const patchText = buildHunkPatchText(props.content, props.filePath, hi);
    if (!patchText) return;   // defensive: empty patch, don't fire
    // Forward the active scope so the backend can pick the right
    // `git apply --reverse[ --cached]` path. Legacy callers
    // (ToolCallCard, FilePatchPanel) don't pass `scope`; in that
    // case we surface the discard error via the parent's natural
    // callback chain (the backend falls back to porcelain
    // auto-detect when scope is missing). To keep types tight we
    // cast: the callback signature now requires scope, but the
    // runtime contract is "missing => backend fallback".
    props.onDiscardHunk({
      file: props.filePath,
      hunkIndex: hi,
      patchText,
      scope: props.scope as GitDiffScope,
    });
  } else {
    // First click: enter confirmation state.
    confirmingHunkIndex.value = hi;
    confirmTimer = setTimeout(() => {
      confirmingHunkIndex.value = null;
      confirmTimer = null;
    }, 3000);
  }
}

function discardHunkAriaLabel(hi: number, header: string): string {
  return tm("spcodeProjectLoad.diffPreview.hunkDiscard.buttonAria", {
    hunk: header,
    file: props.filePath,
  });
}

function discardHunkTitle(hi: number, header: string): string {
  if (confirmingHunkIndex.value === hi) {
    return tm(
      "spcodeProjectLoad.diffPreview.hunkDiscard.confirmingAria",
      { hunk: header },
    );
  }
  return tm("spcodeProjectLoad.diffPreview.hunkDiscard.buttonTitle", {
    hunk: header,
  });
}

function onHunkHeaderKeydown(idx: number, e: KeyboardEvent): void {
  // Spec §6.1.3: Enter / Space toggles the hunk; mirrors the file row pattern
  // in GitDiffFileItem (outer row was refactored from <button> to <div>).
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    toggleHunk(idx);
  }
}

const VIEW_MODE_STORAGE_KEY = "astrbot.diff.viewMode";

function safeGetItem(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSetItem(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* no-op */
  }
}

// Restore the previously chosen view mode (Unified by default so
// existing users see no change on first load after this feature
// lands).
const viewMode = ref<ViewMode>(
  safeGetItem(VIEW_MODE_STORAGE_KEY) === "split" ? "split" : "unified",
);

function setViewMode(mode: ViewMode): void {
  if (viewMode.value === mode) return;
  viewMode.value = mode;
  safeSetItem(VIEW_MODE_STORAGE_KEY, mode);
}

// i18n labels for the toggle. The tm() values are evaluated lazily
// (inside computeds) so locale changes propagate without reload.
const unifiedLabel = computed(() => tm("diffPreview.viewMode.unified"));
const splitLabel = computed(() => tm("diffPreview.viewMode.split"));
const viewModeAriaLabel = computed(() => tm("diffPreview.viewMode.ariaLabel"));

const toggleCollapsed = () => {
  if (props.collapsible) {
    isCollapsed.value = !isCollapsed.value;
  }
};

// ── Fullscreen state (spec 2026-06-30-diff-fullscreen-design.md §4) ─
// `isFullscreen` is declared with the other reactive state above
// (next to `showAllLines`) so `effectiveMaxLines` can include it in
// the cap. The button / overlay refs and the enter / exit functions
// stay colocated with the fullscreen-specific helpers below.
const fullscreenBtnRef = ref<HTMLElement | null>(null);
const overlayRef = ref<HTMLElement | null>(null);

function enterFullscreen(): void {
  isFullscreen.value = true;
  nextTick(() => overlayRef.value?.focus());
}

function exitFullscreen(): void {
  isFullscreen.value = false;
  nextTick(() => fullscreenBtnRef.value?.focus());
}

// Body scroll lock while fullscreen (spec §3.4)
watch(isFullscreen, (v) => {
  document.body.style.overflow = v ? "hidden" : "";
});

// Cleanup on unmount (spec §8 — edge case: component unmounts while fullscreen)
onBeforeUnmount(() => {
  if (isFullscreen.value) {
    document.body.style.overflow = "";
  }
  // Spec §2 decision #12 + §6.1.5: clear confirm timer so a pending
  // timeout can't fire after teardown and touch a destroyed ref.
  if (confirmTimer) {
    clearTimeout(confirmTimer);
    confirmTimer = null;
  }
});

// Spec §2 decision #12 + §6.1.5: clear residual confirm state when filePath
// or content changes (parsedHunks may re-parse, indices may shift).
watch(
  () => [props.filePath, props.content],
  () => {
    if (confirmTimer) {
      clearTimeout(confirmTimer);
      confirmTimer = null;
    }
    confirmingHunkIndex.value = null;
  },
);

// ── Inline comments (spec 2026-06-30-diff-inline-comments) ─────────
// Comments are anchored to the NEW file. A diff has up to 4 kinds of
// lines:
//   - ctx   — exists on both sides; newNo is set
//   - add   — only in new file; newNo is set
//   - del   — only in old file; newNo is ""  (we skip these)
//   - split del-only rows — right cell is null (we skip these)
//
// To avoid polluting useFileComments.contentCache (which is reserved
// for the real on-disk file content seen in the file browser), we
// reconstruct the "post-change file content" from the parsed diff and
// hand it directly to addCommentWithContext.

const fileComments = useFileComments();

/** Per-row hover state.
 *
 *  Unified mode keys off the new-side absolute line number — newNo
 *  is unique per file, so one number identifies one row.
 *
 *  Split mode keys off the per-hunk row index `ri` plus the hunk
 *  index `hi` — `ri` alone is naturally ambiguous because each
 *  hunk restarts its row counter from 0, so two hunks with the
 *  same number of rows would have a `ri` collision at every
 *  position. The pair (hi, ri) is unique.
 *
 *  We carry the hunk index in unified mode too for symmetry, even
 *  though `hoveredUnifiedLine` is already unambiguous — it makes
 *  the v-if's intent explicit ("the line in the same hunk") and
 *  shields against any future change to the newNo assignment.
 *
 *  `null` means "no row hovered". */
const hoveredUnifiedLine = ref<number | null>(null);
/** 2026-08-13: which side the unified hover currently points at.
 *  add/ctx rows → "new"; del rows → "old". Split mode needs no side
 *  state — the gutter lives in the left/right cell itself. */
const hoveredUnifiedSide = ref<"new" | "old" | null>(null);
const hoveredUnifiedHunk = ref<number | null>(null);
const hoveredSplitRow = ref<number | null>(null);
const hoveredSplitHunk = ref<number | null>(null);

/** Editor state (mirrors FileBrowserFilePreview's pattern). When
 *  `activeEditLine` is non-null the editor is visible at the bottom
 *  of the preview. */
const activeEditLine = ref<number | null>(null);
/** 2026-08-13: which side the open editor anchors to (del rows = old). */
const activeEditSide = ref<"new" | "old">("new");
const activeEditCommentId = ref<string | null>(null);
const editorContext = ref<LineContext | null>(null);
const editorInitialText = ref<string>("");

/** Effective opt-in: must be enabled AND a filePath must be set.
 *  filePath is empty for markdown ```diff``` blocks, so the comment
 *  UI silently disappears there. */
const isCommentable = computed<boolean>(
  () => props.commentable !== false && props.filePath.length > 0,
);

/** Resolved comment list: prefer the parent-supplied `props.comments`,
 *  fall back to querying the store. Splitting this from
 *  `commentsByNewLine` means a parent can pre-filter or scope the
 *  list (e.g. by worktree) without going through the store. */
const visibleComments = computed<FileComment[]>(() => {
  if (props.comments.length > 0) return props.comments;
  if (!isCommentable.value) return [];
  return fileComments.commentsForFile(props.filePath);
});

/** newNo → existing comment (new-side only, 2026-08-13). First-wins:
 *  if a parent injects duplicates (shouldn't happen, but be defensive)
 *  the first one renders the indicator and the others are ignored. */
const commentsByNewLine = computed<Map<number, FileComment>>(() => {
  const m = new Map<number, FileComment>();
  for (const c of visibleComments.value) {
    if (c.side === "old") continue;
    if (Number.isInteger(c.line) && c.line > 0 && !m.has(c.line)) {
      m.set(c.line, c);
    }
  }
  return m;
});

/** 2026-08-13: oldNo → existing comment (old-side only, del lines). */
const commentsByOldLine = computed<Map<number, FileComment>>(() => {
  const m = new Map<number, FileComment>();
  for (const c of visibleComments.value) {
    if (c.side !== "old") continue;
    if (Number.isInteger(c.line) && c.line > 0 && !m.has(c.line)) {
      m.set(c.line, c);
    }
  }
  return m;
});

/** Reconstruct the post-change file content by joining all ctx + add
 *  lines in order. This is the synthetic "current file" the diff
 *  describes, and it's what we hand to extractLineContext when the
 *  user opens a new comment. We deliberately do NOT register this
 *  in useFileComments.contentCache (see addCommentWithContext docs). */
const newFileContent = computed<string>(() => {
  const out: string[] = [];
  for (const hunk of parsedHunks.value) {
    for (const line of hunk.lines) {
      if (line.type === "ctx" || line.type === "add") {
        out.push(line.content);
      }
    }
  }
  return out.join("\n");
});

/**
 * Editor state carries the optional diff-hunk context for the line
 * being edited. We snapshot the hunk at the moment the editor opens
 * (NOT at save time) so the user can see the surrounding patch in
 * the comment preview, and so the saved comment reflects the patch
 * as the user saw it when they decided to write.
 *
 * `null` for file-browser paths and for synthetic diffs that have no
 * parseable hunk header.
 */
const pendingDiffHunk = ref<DiffHunkContext | null>(null);

/**
 * Find the parsed DiffHunk whose side-specific range contains `line`.
 * The hunk header `@@ -X,A +Y,B @@` gives both ranges; we match by
 * counting ctx+add lines (new side) or ctx+del lines (old side) from
 * the parsed lines — O(hunk-lines), no re-parse.
 */
function findHunkForLine(line: number, side: "new" | "old"): DiffHunk | null {
  for (const hunk of parsedHunks.value) {
    if (side === "new") {
      const m = hunk.header.match(
        /^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/,
      );
      if (!m) continue;
      const startNew = parseInt(m[1], 10);
      const newCount = hunk.lines.filter((l) => l.type !== "del").length;
      if (line >= startNew && line < startNew + newCount) {
        return hunk;
      }
    } else {
      const m = hunk.header.match(
        /^@@\s+-(\d+)(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@/,
      );
      if (!m) continue;
      const startOld = parseInt(m[1], 10);
      const oldCount = hunk.lines.filter((l) => l.type !== "add").length;
      if (line >= startOld && line < startOld + oldCount) {
        return hunk;
      }
    }
  }
  return null;
}

/**
 * Convert a parsed DiffHunk into the serializable DiffHunkContext
 * that gets stored on the FileComment. The hunk's `oldNo`/`newNo`
 * strings are parsed back to numbers so the LLM-facing renderer
 * can format them without re-parsing. `header-file` lines are
 * dropped — they don't appear inside hunks in practice but we
 * filter defensively.
 */
function buildDiffHunkContext(
  hunk: DiffHunk,
  anchorLine: number,
  side: "new" | "old",
): DiffHunkContext {
  return {
    header: hunk.header,
    lines: hunk.lines
      .filter((l) => l.type !== "header-file")
      .map((l) => ({
        type: l.type as "add" | "del" | "ctx",
        content: l.content,
        oldNo: l.oldNo ? Number(l.oldNo) : null,
        newNo: l.newNo ? Number(l.newNo) : null,
      })),
    newLine: side === "new" ? anchorLine : null,
    side,
    oldLine: side === "old" ? anchorLine : null,
  };
}

/** 2026-08-13: the line a unified row anchors to (new for add/ctx,
 *  old for del) — mirrors onUnifiedRowEnter's side resolution. */
function unifiedAnchorSide(line: DiffLine): "new" | "old" {
  return line.newNo ? "new" : "old";
}
function unifiedAnchorLine(line: DiffLine): number {
  return Number(line.newNo ? line.newNo : line.oldNo);
}

/** 2026-08-13: existing comment on a unified row (either side). */
function commentOnLine(line: DiffLine): FileComment | undefined {
  if (line.newNo) return commentsByNewLine.value.get(Number(line.newNo));
  if (line.oldNo) return commentsByOldLine.value.get(Number(line.oldNo));
  return undefined;
}

/** 2026-08-13: open a new editor for a unified row, picking the side
 *  from the row type (add/ctx → new, del → old). */
function openEditorForLine(line: DiffLine): void {
  if (line.newNo) openNewEditor(Number(line.newNo), "new");
  else if (line.oldNo) openNewEditor(Number(line.oldNo), "old");
}

/** 2026-08-13: open the existing comment's editor for a unified row. */
function openEditorForComment(line: DiffLine): void {
  const c = commentOnLine(line);
  if (c) openEditEditor(c.id);
}

/** 2026-08-13: whether the unified "+" chip should show for this row. */
function unifiedGutterAddVisible(line: DiffLine, hi: number): boolean {
  if (!isCommentable.value) return false;
  if (hoveredUnifiedHunk.value !== hi) return false;
  if (line.newNo) {
    return (
      hoveredUnifiedSide.value === "new" &&
      hoveredUnifiedLine.value === Number(line.newNo) &&
      !commentsByNewLine.value.has(Number(line.newNo))
    );
  }
  if (line.oldNo) {
    return (
      hoveredUnifiedSide.value === "old" &&
      hoveredUnifiedLine.value === Number(line.oldNo) &&
      !commentsByOldLine.value.has(Number(line.oldNo))
    );
  }
  return false;
}

/** 2026-08-13: the content of a del line by old-side line number.
 *  Spec decision (B): del-line comments do NOT rebuild the old file —
 *  the line's own content comes straight from the hunk; surrounding
 *  context stays null. */
function delLineContent(line: number): string {
  for (const hunk of parsedHunks.value) {
    for (const l of hunk.lines) {
      if (l.type === "del" && l.oldNo && Number(l.oldNo) === line) {
        return l.content;
      }
    }
  }
  return "";
}

/** Open the editor for a brand-new comment on the given side. */
function openNewEditor(line: number, side: "new" | "old"): void {
  activeEditLine.value = line;
  activeEditSide.value = side;
  activeEditCommentId.value = null;
  editorInitialText.value = "";
  // extractLineContext may return null when the line is out of range
  // (e.g. a stub diff that only contains del lines). The editor
  // handles a null context gracefully (no preview snippet shown).
  editorContext.value =
    side === "new"
      ? extractLineContext(newFileContent.value, line)
      : {
          lineContent: delLineContent(line),
          contextBefore: null,
          contextAfter: null,
        };
  // Snapshot the surrounding diff hunk so the saved comment can
  // include the patch in the LLM-facing output. Falls back to null
  // when the line isn't in any parseable hunk (very rare: only
  // happens if the diff text is malformed and the parser couldn't
  // recover the @@ header).
  const hunk = findHunkForLine(line, side);
  pendingDiffHunk.value = hunk ? buildDiffHunkContext(hunk, line, side) : null;
}

/** Open the editor for an existing comment (look up by id in the
 *  global store so the comment's own lineContent/context is used —
 *  not whatever the current diff happens to contain). */
function openEditEditor(commentId: string): void {
  const existing = fileComments.findCommentById(commentId);
  if (!existing) return;
  activeEditLine.value = existing.line;
  activeEditSide.value = existing.side ?? "new";
  activeEditCommentId.value = existing.id;
  editorInitialText.value = existing.text;
  editorContext.value = {
    lineContent: existing.lineContent,
    contextBefore: existing.contextBefore,
    contextAfter: existing.contextAfter,
  };
}

function onSaveComment(payload: {
  text: string;
  commentId: string | null;
  line: number;
}): void {
  if (payload.commentId) {
    fileComments.updateComment(payload.commentId, payload.text);
  } else {
    if (!isCommentable.value) {
      closeEditor();
      return;
    }
    // Fall back to a minimal context if the diff couldn't synthesize
    // a real one (e.g. out-of-range line). The comment is still
    // useful — the LLM can locate the line by number + file path.
    const ctx = editorContext.value ?? {
      lineContent: "",
      contextBefore: null,
      contextAfter: null,
    };
    fileComments.addCommentWithContext({
      filePath: props.filePath,
      line: payload.line,
      text: payload.text,
      context: ctx,
      // 2026-08-13: del-line comments anchor to the old side.
      side: activeEditSide.value,
      // Attach the diff hunk so the LLM sees the surrounding
      // patch (with the target line marked) instead of just the
      // one line of context around the comment. `null` for
      // file-browser paths and for hunks that couldn't be parsed.
      diffHunk: pendingDiffHunk.value ?? undefined,
    });
  }
  closeEditor();
}

function onDeleteComment(commentId: string): void {
  fileComments.deleteComment(commentId);
  closeEditor();
}

function closeEditor(): void {
  activeEditLine.value = null;
  activeEditSide.value = "new";
  activeEditCommentId.value = null;
  editorContext.value = null;
  // Drop the snapshotted hunk — it's only meaningful while the
  // editor is open. Leaving it would leak memory if the editor is
  // never re-opened.
  pendingDiffHunk.value = null;
}

/** Hover handlers — using @mouseenter/@mouseleave on each row gives
 *  us a stable "which row is the mouse over" signal without the
 *  mousemove handler that FileBrowserCodeView needs (Shiki outputs
 *  one span per line so the file browser has to compute the hovered
 *  line from clientY; here we get it for free from the row element). */
function onUnifiedRowEnter(line: DiffLine, hi: number): void {
  if (!isCommentable.value) return;
  // 2026-08-13: del rows now arm the gutter too — they anchor to the
  // OLD side (oldNo); ctx/add rows keep anchoring to the NEW side.
  if (line.newNo) {
    hoveredUnifiedLine.value = Number(line.newNo);
    hoveredUnifiedSide.value = "new";
  } else if (line.oldNo) {
    hoveredUnifiedLine.value = Number(line.oldNo);
    hoveredUnifiedSide.value = "old";
  } else {
    return;
  }
  hoveredUnifiedHunk.value = hi;
}
function onUnifiedRowLeave(): void {
  hoveredUnifiedLine.value = null;
  hoveredUnifiedSide.value = null;
  hoveredUnifiedHunk.value = null;
}
function onSplitRowEnter(ri: number, row: SplitRow, hi: number): void {
  if (!isCommentable.value) return;
  // 2026-08-13: del-only rows (left cell only) arm the gutter too —
  // the cell itself decides the side (left = old, right = new).
  if (!row.left && !row.right) return;
  hoveredSplitRow.value = ri;
  hoveredSplitHunk.value = hi;
}
function onSplitRowLeave(): void {
  hoveredSplitRow.value = null;
  hoveredSplitHunk.value = null;
}

/** Close the editor when the user switches files in the parent — the
 *  editor's line number is for a different file, so keeping it open
 *  would be confusing. */
watch(
  () => props.filePath,
  () => {
    closeEditor();
  },
);

// ── Parse unified diff ─────────────────────────────────────────────

const parsedHunks = computed<DiffHunk[]>(() => {
  const text = extractDiffContent(props.content);
  return parseUnifiedDiff(text, effectiveMaxLines.value);
});

const totalLines = computed(() =>
  parsedHunks.value.reduce((sum, h) => sum + h.lines.length, 0),
);

const truncated = computed(() => {
  const raw = extractDiffContent(props.content);
  return raw.length > props.maxChars;
});

// Yellow "Diff truncated" banner is only useful when there is
// actually hidden content the user can recover by expanding.
// `truncated` alone fires whenever the raw text exceeds `maxChars`,
// which would keep the warning visible inside the fullscreen
// overlay (and after a non-fullscreen "show all"). Combine it with
// the existing `collapsedOverflow` signal so the banner follows
// the effective rendering cap (fullscreen OR showAll lifts the
// cap → banner disappears → no misleading warning).
const showTruncationWarning = computed(
  () => truncated.value && collapsedOverflow.value > 0,
);

const collapsedOverflow = computed(() => {
  if (showAllLines.value) return 0;
  const fullHunks = parseUnifiedDiff(
    extractDiffContent(props.content),
    Infinity,
  );
  const fullTotal = fullHunks.reduce((sum, h) => sum + h.lines.length, 0);
  return Math.max(0, fullTotal - totalLines.value);
});

// ── Split-view alignment ──────────────────────────────────────────

// Walk each parsed hunk and pair its del/add lines into rows of
// {left, right}. The hunk header is preserved unchanged. Truncation
// is applied here (after pairing) so we never split a del/add pair
// across the cutoff — paired rows stay together or are dropped
// together.
const splitHunks = computed<SplitHunk[]>(() => {
  const out: SplitHunk[] = [];
  let consumed = 0;
  const cap = effectiveMaxLines.value;
  for (const hunk of parsedHunks.value) {
    const allRows = alignHunkLines(hunk.lines);
    const rows: SplitRow[] = [];
    for (const row of allRows) {
      if (consumed >= cap) break;
      // ctx rows count as 1 line; a modified/del-only/add-only row
      // also counts as 1 visual row in the split layout.
      rows.push(row);
      consumed++;
    }
    out.push({ header: hunk.header, rows });
  }
  return out;
});

// Pair del/add lines inside a single hunk into visual rows.
//
// Algorithm (matches GitHub's split view):
//   - ctx lines are emitted as-is (both sides the same).
//   - dels are buffered.
//   - adds are buffered.
//   - when one of the buffers is non-empty and the other changes
//     type (or a ctx / hunk boundary is reached), flushPair() pairs
//     min(dels, adds) into "modified" rows, then drains leftover
//     dels into "del-only" rows (right=null) and leftover adds into
//     "add-only" rows (left=null).
function alignHunkLines(lines: DiffLine[]): SplitRow[] {
  const rows: SplitRow[] = [];
  let dels: DiffLine[] = [];
  let adds: DiffLine[] = [];

  const flush = (): void => {
    const pairCount = Math.min(dels.length, adds.length);
    for (let i = 0; i < pairCount; i++) {
      rows.push({ left: dels[i], right: adds[i], kind: "modified" });
    }
    for (let i = pairCount; i < dels.length; i++) {
      rows.push({ left: dels[i], right: null, kind: "del-only" });
    }
    for (let i = pairCount; i < adds.length; i++) {
      rows.push({ left: null, right: adds[i], kind: "add-only" });
    }
    dels = [];
    adds = [];
  };

  for (const line of lines) {
    if (line.type === "ctx") {
      flush();
      rows.push({ left: line, right: line, kind: "ctx" });
    } else if (line.type === "del") {
      dels.push(line);
    } else if (line.type === "add") {
      adds.push(line);
    }
    // header-file lines are skipped — they never appear inside a
    // hunk's `lines` array in practice (the parser filters them).
  }
  flush();
  return rows;
}

// ── 2026-08-14 diff syntax highlighting ──────────────────────────
// Diff lines only carry red/green tints by default. Here we
// additionally tokenize the old-side (ctx + del) and new-side
// (ctx + add) text with the shared Shiki highlighter as if each were
// the real file, then map per-line token HTML back onto the parsed
// DiffLine objects. Multiline constructs interrupted by elided hunk
// context can mis-highlight slightly — the standard approximation
// for diff viewers that highlight without the full file at hand.

const shikiHighlighter = ref<any>(null);
const shikiReady = ref(false);

/** Shiki language for the diffed file; "text" disables highlighting. */
const highlightLanguage = computed(() => detectLanguage(props.filePath || ""));

/** Safety cap: skip highlighting for very large diffs. */
const HIGHLIGHT_MAX_CHARS = 200_000;

onMounted(async () => {
  if (!props.filePath || highlightLanguage.value === "text") return;
  try {
    shikiHighlighter.value = await ensureShikiLanguages();
    shikiReady.value = true;
  } catch (err) {
    console.error("Failed to initialize Shiki for diff highlighting:", err);
  }
});

interface LineHighlight {
  light: string;
  dark: string;
}

/** Render one line's tokens as colored spans (content is escaped). */
function tokensToHtml(
  tokens: Array<{ content: string; color?: string; fontStyle?: number }>,
): string {
  let out = "";
  for (const token of tokens) {
    const text = escapeHtml(token.content);
    if (!text) continue;
    const styles: string[] = [];
    if (token.color) styles.push(`color:${token.color}`);
    if (token.fontStyle) {
      // Shiki FontStyle bitmask: 1 = italic, 2 = bold, 4 = underline.
      if (token.fontStyle & 1) styles.push("font-style:italic");
      if (token.fontStyle & 2) styles.push("font-weight:600");
      if (token.fontStyle & 4) styles.push("text-decoration:underline");
    }
    out += styles.length
      ? `<span style="${styles.join(";")}">${text}</span>`
      : text;
  }
  return out;
}

/** Tokenize one side (old or new) and store per-line HTML for both
 *  themes in `map`. */
function assignSideHighlights(
  map: Map<DiffLine, LineHighlight>,
  sideLines: DiffLine[],
  lang: string,
): void {
  if (!sideLines.length) return;
  const text = sideLines.map((l) => l.content).join("\n");
  const light = shikiHighlighter.value.codeToTokens(text, {
    lang,
    theme: SHIKI_THEMES.light,
  });
  const dark = shikiHighlighter.value.codeToTokens(text, {
    lang,
    theme: SHIKI_THEMES.dark,
  });
  sideLines.forEach((line, i) => {
    map.set(line, {
      light: tokensToHtml(light.tokens[i] ?? []),
      dark: tokensToHtml(dark.tokens[i] ?? []),
    });
  });
}

const highlightedLines = computed<Map<DiffLine, LineHighlight> | null>(() => {
  if (!shikiReady.value || !shikiHighlighter.value) return null;
  const lang = highlightLanguage.value;
  if (lang === "text") return null;
  const lines = parsedHunks.value
    .flatMap((h) => h.lines)
    .filter((l) => l.type !== "header-file");
  const totalChars = lines.reduce((sum, l) => sum + l.content.length, 0);
  if (!lines.length || totalChars > HIGHLIGHT_MAX_CHARS) return null;
  const map = new Map<DiffLine, LineHighlight>();
  try {
    // ctx lines belong to both sides; the new-side pass runs second
    // so a ctx line's colors match the new file (what the user is
    // about to have), not the old one.
    assignSideHighlights(
      map,
      lines.filter((l) => l.type !== "add"),
      lang,
    );
    assignSideHighlights(
      map,
      lines.filter((l) => l.type !== "del"),
      lang,
    );
  } catch (err) {
    console.error("Failed to highlight diff with Shiki:", err);
    return null;
  }
  return map;
});

// ── 2026-08-14 intra-line (word-level) diff highlight ────────────
// Pair del/add lines inside each hunk (same buffer/flush order as
// alignHunkLines, so the pairing matches the split view) and compute
// per-line changed segments so users can spot *what* changed inside
// a modified line. Keyed by DiffLine object identity — the same
// objects flow into both the unified and split renderers.
//
// Performance: computeIntraLineSegments caps the per-pair cost
// (prefix/suffix trim + bounded word LCS); INTRA_LINE_MAX_TOTAL_CHARS
// caps the per-diff total (mirrors HIGHLIGHT_MAX_CHARS). The computed
// re-runs only when parsedHunks changes, never per render.
const INTRA_LINE_MAX_TOTAL_CHARS = 200_000;

const intraLineSegments = computed<Map<DiffLine, IntraLineSegment[]>>(() => {
  const map = new Map<DiffLine, IntraLineSegment[]>();
  let budget = INTRA_LINE_MAX_TOTAL_CHARS;
  for (const hunk of parsedHunks.value) {
    let dels: DiffLine[] = [];
    let adds: DiffLine[] = [];
    const flush = (): void => {
      const pairCount = Math.min(dels.length, adds.length);
      for (let i = 0; i < pairCount; i++) {
        const cost = dels[i].content.length + adds[i].content.length;
        if (cost > budget) continue; // keep earlier pairs, skip the rest
        const { oldSegments, newSegments } = computeIntraLineSegments(
          dels[i].content,
          adds[i].content,
        );
        map.set(dels[i], oldSegments);
        map.set(adds[i], newSegments);
        budget -= cost;
      }
      dels = [];
      adds = [];
    };
    for (const line of hunk.lines) {
      if (line.type === "del") dels.push(line);
      else if (line.type === "add") adds.push(line);
      else flush();
    }
    flush();
  }
  return map;
});

/** HTML for a diff line's content: intra-line segments for paired
 *  del/add lines (2026-08-14), otherwise Shiki-highlighted when the
 *  highlighter is ready and the language is known, otherwise the
 *  escaped plain text. Shared by all six render sites (unified /
 *  split × inline / fullscreen). */
function lineContentHtml(line: DiffLine | null | undefined): string {
  if (!line) return "";
  // Intra-line highlight wins over Shiki for paired changed lines:
  // the changed segments matter more than token colors there
  // (GitHub renders changed lines the same way — plain text with
  // emphasized segments).
  const segs = intraLineSegments.value.get(line);
  if (segs) {
    const cls = line.type === "del" ? "intra-hl-del" : "intra-hl-add";
    return segs
      .map((seg) =>
        seg.changed
          ? `<span class="intra-hl ${cls}">${escapeHtml(seg.text)}</span>`
          : escapeHtml(seg.text),
      )
      .join("");
  }
  const pair = highlightedLines.value?.get(line);
  if (pair) return props.isDark ? pair.dark : pair.light;
  return escapeHtml(line.content);
}

// ── Stats ──────────────────────────────────────────────────────────

const statsAdds = computed(() => {
  let adds = 0;
  for (const hunk of parsedHunks.value) {
    for (const line of hunk.lines) {
      if (line.type === "add") adds++;
    }
  }
  return adds || null;
});

const statsDels = computed(() => {
  let dels = 0;
  for (const hunk of parsedHunks.value) {
    for (const line of hunk.lines) {
      if (line.type === "del") dels++;
    }
  }
  return dels || null;
});

</script>

<style scoped>
.diff-preview {
  --diff-add-bg: rgba(70, 200, 70, 0.12);
  --diff-add-border: rgba(70, 200, 70, 0.35);
  --diff-add-bg-strong: rgba(70, 200, 70, 0.22);
  --diff-del-bg: rgba(255, 100, 100, 0.12);
  --diff-del-border: rgba(255, 100, 100, 0.35);
  --diff-del-bg-strong: rgba(255, 100, 100, 0.22);
  /* 2026-08-14 intra-line highlight: changed-segment background,
     distinctly stronger than the whole-line tint. */
  --diff-add-seg-bg: rgba(70, 200, 70, 0.45);
  --diff-del-seg-bg: rgba(255, 100, 100, 0.45);
  --diff-hunk-bg: #e8f0fe;
  --diff-hunk-border: rgba(100, 150, 220, 0.3);
  --diff-line-no: rgba(0, 0, 0, 0.35);
  --diff-border: rgba(0, 0, 0, 0.08);
  --diff-divider: rgba(0, 0, 0, 0.12);

  margin: 4px 0;
  border: 1px solid var(--diff-border);
  border-radius: 8px;
  overflow: hidden;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
}

.diff-preview.is-dark {
  --diff-add-bg: rgba(70, 200, 70, 0.16);
  --diff-add-border: rgba(70, 200, 70, 0.3);
  --diff-add-bg-strong: rgba(70, 200, 70, 0.3);
  --diff-del-bg: rgba(255, 100, 100, 0.16);
  --diff-del-border: rgba(255, 100, 100, 0.3);
  --diff-del-bg-strong: rgba(255, 100, 100, 0.3);
  --diff-add-seg-bg: rgba(70, 200, 70, 0.5);
  --diff-del-seg-bg: rgba(255, 100, 100, 0.5);
  --diff-hunk-bg: rgba(100, 150, 255, 0.12);
  --diff-hunk-border: rgba(100, 150, 255, 0.2);
  --diff-line-no: rgba(255, 255, 255, 0.35);
  --diff-border: rgba(255, 255, 255, 0.1);
  --diff-divider: rgba(255, 255, 255, 0.14);
}

/* ── Header ─────────────────────────────────────────────────────── */

.diff-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 8px 12px;
  background: rgba(0, 0, 0, 0.02);
  border: 0;
  cursor: pointer;
  font: inherit;
  color: inherit;
  user-select: none;
}

.diff-preview.is-dark .diff-header {
  background: rgba(255, 255, 255, 0.03);
}

.diff-header:hover {
  background: rgba(0, 0, 0, 0.05);
}

.diff-preview.is-dark .diff-header:hover {
  background: rgba(255, 255, 255, 0.06);
}

.diff-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.diff-header-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.diff-header-icon {
  color: rgba(var(--v-theme-on-surface), 0.55);
  flex-shrink: 0;
}

.diff-file-path {
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.diff-stats {
  font-size: 11px;
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  white-space: nowrap;
}

.diff-stats-add {
  color: #2da44e;
  margin-right: 4px;
}

.diff-preview.is-dark .diff-stats-add {
  color: #57ab5a;
}

.diff-stats-del {
  color: #cf222e;
}

.diff-preview.is-dark .diff-stats-del {
  color: #f47067;
}

/* ── View-mode segmented toggle ─────────────────────────────────── */

.overlay-body {
  display: flex;
  flex-direction: column;
}

.overlay-gap {
  padding: 2px 8px;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-top: 1px dashed rgba(var(--v-theme-on-surface), 0.14);
  border-bottom: 1px dashed rgba(var(--v-theme-on-surface), 0.14);
  cursor: pointer;
  text-align: center;
  user-select: none;
}

.overlay-gap:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.diff-view-toggle {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--diff-border);
  border-radius: 4px;
  overflow: hidden;
  margin: 0 4px;
  background: transparent;
}

.diff-view-toggle-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  background: transparent;
  border: 0;
  border-radius: 0;
  color: rgba(var(--v-theme-on-surface), 0.55);
  cursor: pointer;
  transition:
    background 0.12s ease,
    color 0.12s ease;
}

.diff-view-toggle-btn + .diff-view-toggle-btn {
  border-left: 1px solid var(--diff-border);
}

.diff-view-toggle-btn:hover {
  color: rgba(var(--v-theme-on-surface), 0.85);
  background: rgba(127, 127, 127, 0.08);
}

.diff-view-toggle-btn:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}

.diff-view-toggle-btn.active {
  background: rgba(var(--v-theme-primary), 0.15);
  color: rgb(var(--v-theme-primary));
}

/* 2026-08-09 (elecvoid243): bulk fold controls (fullscreen header).
   Small ghost buttons that share the header's visual language; the
   double-chevron mirrors the hunk chevron metaphor (right = folded,
   down = expanded). The font stack overrides the diff's inherited
   monospace with the AstrBot UI face (scss/_variables.scss
   $body-font-family: Outfit / Noto Sans / CJK fallbacks) so the
   buttons read as UI chrome, not code. */
.diff-hunk-bulk {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: 4px;
  flex-shrink: 0;
}
.diff-hunk-bulk-btn {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 22px;
  padding: 0 6px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-family:
    "Outfit", "Noto Sans", "PingFang SC", "Hiragino Sans GB",
    "Noto Sans CJK SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  font-size: 11px;
  cursor: pointer;
  transition:
    background 0.12s ease,
    color 0.12s ease;
}
.diff-hunk-bulk-btn:hover {
  color: rgba(var(--v-theme-on-surface), 0.85);
  background: rgba(127, 127, 127, 0.08);
}
.diff-hunk-bulk-btn:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}

.diff-chevron {
  color: rgba(var(--v-theme-on-surface), 0.45);
  transition: transform 0.2s ease;
}

.diff-chevron.expanded {
  transform: rotate(90deg);
}

/* ── Summary text ────────────────────────────────────────────────── */

.diff-summary-text {
  padding: 4px 12px 8px;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-family: inherit;
}

/* ── Body ────────────────────────────────────────────────────────── */

.diff-body {
  border-top: 1px solid var(--diff-border);
}

/* ── Inline-comment dock ───────────────────────────────────────────
   The comment editor is wrapped in this dock and pinned to the
   bottom of the scroll viewport with `position: sticky`. This is
   what stops the "open comment → page jumps to the diff tail"
   behaviour: a sticky element that is already inside the viewport
   does not get scrolled into view when its textarea receives focus,
   so the user stays parked on the line they clicked.

   The dock owns all the chrome (opaque surface background, rounded
   corners, layered shadow, top hairline). The editor's own
   border-top and 60%-alpha background are neutralised via :deep so
   the two layers don't double up — the dock reads as a single
   floating card sitting above the diff rows, not a translucent
   strip bleeding the rows through it. `overflow: hidden` clips the
   editor's inner corners to the dock's radius. z-index sits above
   the diff rows (and above the body's sticky summary header, which
   lives at the top and never overlaps the bottom dock). */
.diff-comment-dock {
  position: sticky;
  bottom: 0;
  z-index: 6;
  margin-top: 8px;
  border-radius: 10px;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  box-shadow:
    0 -10px 28px -16px rgba(0, 0, 0, 0.5),
    0 8px 22px -12px rgba(0, 0, 0, 0.3);
  overflow: hidden;
}
.diff-comment-dock :deep(.comment-editor) {
  border-top: 0;
  background: transparent;
}
.diff-preview.is-dark .diff-comment-dock {
  border-color: rgba(255, 255, 255, 0.16);
  box-shadow:
    0 -10px 30px -14px rgba(0, 0, 0, 0.7),
    0 8px 24px -12px rgba(0, 0, 0, 0.6);
}

.diff-truncation-warning {
  padding: 6px 12px;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  background: rgba(var(--v-theme-warning), 0.08);
  border-bottom: 1px solid var(--diff-border);
}

/* ── Hunk (unified) ──────────────────────────────────────────────── */

.diff-hunk + .diff-hunk {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.04);
}

/* UI #8: hunk header is now a button (clickable to fold / unfold).
   Reset default button styles and let it fill the full row. The
   inner flex lays out: chevron | hunk text | line count badge. */
.hunk-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 4px 12px;
  font-size: 11px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.55);
  background: var(--diff-hunk-bg);
  border: 0;
  border-bottom: 1px solid var(--diff-hunk-border);
  font-family: inherit;
  cursor: pointer;
  text-align: left;
  user-select: none;
  transition: background 0.12s ease;
}

.hunk-header:hover {
  background: rgba(var(--v-theme-primary), 0.08);
}

.hunk-header:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}

.hunk-chevron {
  color: rgba(var(--v-theme-on-surface), 0.55);
  flex-shrink: 0;
  transition: transform 0.15s ease;
}

.hunk-chevron.expanded {
  transform: rotate(90deg);
}

.hunk-header-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hunk-header-count {
  font-size: 10px;
  font-weight: 500;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgba(var(--v-theme-on-surface), 0.6);
  flex-shrink: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

/* ── Diff line (unified) ─────────────────────────────────────────── */

.diff-line {
  display: flex;
  align-items: baseline;
  padding: 1px 12px;
  min-height: 20px;
  transition: background 0.1s ease;
}

.diff-line:hover {
  filter: brightness(0.94);
}

.diff-preview.is-dark .diff-line:hover {
  filter: brightness(1.15);
}

/* 2026-08-09 (elecvoid243): unified rows now use the -strong tints so
   their red/green matches the split view's changed cells exactly
   (previously the 0.12-alpha base tints read visibly lighter than the
   split view's 0.22-alpha cells). The base --diff-*-bg vars stay for
   the split view's faint "blank side" cells. */
.diff-line.add {
  background: var(--diff-add-bg-strong);
  border-left: 3px solid var(--diff-add-border);
}

.diff-line.del {
  background: var(--diff-del-bg-strong);
  border-left: 3px solid var(--diff-del-border);
}

.diff-line.ctx {
  border-left: 3px solid transparent;
}

/* ── Line numbers (unified) ──────────────────────────────────────── */

.line-number {
  width: 36px;
  flex-shrink: 0;
  text-align: right;
  padding-right: 8px;
  color: var(--diff-line-no);
  user-select: none;
}

.line-number.new {
  padding-right: 0;
  padding-left: 8px;
}

/* ── Prefix and content (unified) ────────────────────────────────── */

.line-prefix {
  width: 14px;
  flex-shrink: 0;
  text-align: center;
  font-weight: 700;
  user-select: none;
}

.diff-line.add .line-prefix {
  color: #2da44e;
}

.diff-preview.is-dark .diff-line.add .line-prefix {
  color: #57ab5a;
}

.diff-line.del .line-prefix {
  color: #cf222e;
}

/* 2026-08-14 intra-line highlight: the changed word segments inside
   a paired del/add line get a distinctly stronger tint than the
   whole-line background. The spans are injected via v-html, so they
   never carry this component's scoped data-v attribute — :deep() is
   required for the rules to match (same reason the Shiki spans use
   inline styles). The --diff-*-seg-bg custom properties are defined
   on .diff-preview (a template element) and inherit into the
   injected markup, so they work without :deep. */
.line-content :deep(.intra-hl) {
  border-radius: 2px;
  padding: 0 1px;
}

.line-content :deep(.intra-hl-del) {
  background: var(--diff-del-seg-bg);
}

.line-content :deep(.intra-hl-add) {
  background: var(--diff-add-seg-bg);
}

.diff-preview.is-dark .diff-line.del .line-prefix {
  color: #f47067;
}

.line-content {
  white-space: pre-wrap;
  word-break: break-all;
  min-width: 0;
  padding-left: 4px;
}

/* ── Split-view rows ─────────────────────────────────────────────── */

/* In split mode the .diff-hunk itself becomes a vertical stack of
   .diff-row-split; the inner cells lay out as 1fr | 1fr. We omit
   horizontal padding (12px) here because the cells handle their own. */
.diff-hunk-split {
  /* nothing special — the per-row grid does the work */
}

.diff-row-split {
  display: grid;
  grid-template-columns: 1fr 1fr;
  min-height: 20px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.03);
}

.diff-row-split:first-of-type {
  border-top: 0;
}

.diff-row-split .diff-cell {
  display: flex;
  align-items: baseline;
  padding: 1px 12px;
  min-width: 0;
  overflow: hidden;
}

.diff-row-split .diff-cell + .diff-cell {
  border-left: 1px solid var(--diff-divider);
}

/* ctx rows: both sides transparent. Use a subtle alternating shade so
   the eye can track the row across the divider. */
.diff-row-split.ctx .diff-cell {
  background: transparent;
}

/* modified rows: stronger tint on both sides to signal "this row
   changed". The right cell uses the add tint, the left uses the del
   tint — matching GitHub's split view exactly. */
.diff-row-split.modified .diff-cell.left {
  background: var(--diff-del-bg-strong);
}
.diff-row-split.modified .diff-cell.right {
  background: var(--diff-add-bg-strong);
}

/* del-only: left tinted, right blank. The blank cell still renders
   a faint background so the row visually contains two halves. */
.diff-row-split.del-only .diff-cell.left {
  background: var(--diff-del-bg-strong);
}
.diff-row-split.del-only .diff-cell.right {
  background: var(--diff-del-bg);
  opacity: 0.4;
}

/* add-only: right tinted, left blank. */
.diff-row-split.add-only .diff-cell.left {
  background: var(--diff-add-bg);
  opacity: 0.4;
}
.diff-row-split.add-only .diff-cell.right {
  background: var(--diff-add-bg-strong);
}

/* In split mode the line numbers and prefix are slightly tighter
   because we have 4 such spans per row (2 sides × old/new) but the
   cell is half the width. Keep them readable. */
.diff-row-split .line-number {
  width: 30px;
  padding-right: 6px;
  padding-left: 0;
}

.diff-row-split .line-prefix {
  width: 12px;
}

.diff-row-split .line-content {
  padding-left: 4px;
}

/* Prefix color carries over from unified rules via class hooks; in
   split we also need to color the side that holds an add/del. The
   `.left`/`.right` cell can't be styled via .add/.del directly
   (the line is nested), so we re-derive the color from the row kind. */
.diff-row-split.modified .diff-cell.left .line-prefix,
.diff-row-split.del-only .diff-cell.left .line-prefix {
  color: #cf222e;
}
.diff-preview.is-dark .diff-row-split.modified .diff-cell.left .line-prefix,
.diff-preview.is-dark .diff-row-split.del-only .diff-cell.left .line-prefix {
  color: #f47067;
}

.diff-row-split.modified .diff-cell.right .line-prefix,
.diff-row-split.add-only .diff-cell.right .line-prefix {
  color: #2da44e;
}
.diff-preview.is-dark .diff-row-split.modified .diff-cell.right .line-prefix,
.diff-preview.is-dark .diff-row-split.add-only .diff-cell.right .line-prefix {
  color: #57ab5a;
}

/* ── Show more ───────────────────────────────────────────────────── */

.diff-overflow-bar {
  border-top: 1px solid var(--diff-border);
  padding: 6px 12px;
}

.diff-show-more {
  background: none;
  border: 0;
  padding: 0;
  font: inherit;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  cursor: pointer;
}

.diff-show-more:hover {
  color: rgba(var(--v-theme-on-surface), 0.75);
  text-decoration: underline;
}

/* ── Collapsed state ─────────────────────────────────────────────── */

.diff-preview.collapsed .diff-body,
.diff-preview.collapsed .diff-summary-text {
  display: none;
}

/* ══════════════════════════════════════════════════════════════════
   Fullscreen overlay — spec 2026-06-30-diff-fullscreen-design.md §7
   ══════════════════════════════════════════════════════════════════ */

/* Overlay backdrop: fills entire viewport */
.diff-fullscreen-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgb(var(--v-theme-background));
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Back button: small FAB fixed at top-right corner */
.diff-fullscreen-back-btn {
  position: fixed;
  top: 12px;
  right: 12px;
  z-index: 10000;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border: 1px solid rgb(var(--v-theme-outline));
  border-radius: 8px;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  cursor: pointer;
  font-size: 13px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
  transition: background 0.15s;
  white-space: nowrap;
}
.diff-fullscreen-back-btn:hover {
  background: rgb(var(--v-theme-surface-variant));
}

/* Fullscreen body: scrollable container */
.diff-fullscreen-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  padding-top: 52px; /* room for the fixed back button at top-right */
}

/* Override for diff-preview inside fullscreen: wider border,
   no max-width constraint. */
.diff-preview.is-fullscreen {
  max-width: 100%;
  border: 1px solid rgb(var(--v-theme-outline-variant));
  border-radius: 8px;
}

/* Fullscreen button in the normal header. Matches existing
   .diff-view-toggle-btn dimensions (22×22px) and transitions. */
.diff-fullscreen-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: none;
  cursor: pointer;
  color: rgba(var(--v-theme-on-surface), 0.55);
  transition:
    border-color 0.15s,
    color 0.15s,
    background 0.15s;
  flex-shrink: 0;
}
.diff-fullscreen-btn:hover {
  border-color: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.08);
}
.diff-fullscreen-btn:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}

/* ══════════════════════════════════════════════════════════════════
   Inline-comment gutter (spec 2026-06-30-diff-inline-comments)
   ══════════════════════════════════════════════════════════════════

   Mirrors the file-browser gutter (FileBrowserCodeView.vue) so the
   two review surfaces feel identical. The gutter is a 24px-wide slot
   at the right end of each diff row; it stays in the layout (just
   invisible) so hovering doesn't shift the code column. The cell
   shows:
     - the primary-tinted "+" chip on hover (when the line has no
       existing comment)
     - the warning-tinted indicator (when a comment exists) — stays
       visible always, like the file-browser indicator
*/

/* Unified mode: a fixed 24px column at the very LEFT of .diff-line
   (which is a flex row). Mirrors the file-browser code view, which
   also puts the gutter on the left — keeps the two review surfaces
   visually consistent so the user's mouse doesn't have to learn a
   new hot-zone. */
.diff-line {
  position: relative;
}
.diff-line-gutter {
  width: 24px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.1s ease;
}
/* Show on row hover OR when a comment already exists on the line. */
.diff-line.is-hovered .diff-line-gutter,
.diff-line.has-comment .diff-line-gutter {
  opacity: 1;
}

/* The "add comment" chip — matches FileBrowserCodeView .gutter-add-btn. */
.diff-comment-add {
  width: 20px;
  height: 20px;
  background: rgba(var(--v-theme-primary), 0.2);
  border: 1.5px solid rgba(var(--v-theme-primary), 0.7);
  border-radius: 5px;
  cursor: pointer;
  color: rgb(var(--v-theme-primary));
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12);
  transition:
    background 0.12s ease,
    border-color 0.12s ease,
    transform 0.12s ease,
    box-shadow 0.12s ease;
}
.diff-preview.is-dark .diff-comment-add {
  background: rgba(var(--v-theme-primary), 0.28);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.45);
}
.diff-comment-add:hover,
.diff-comment-add:focus {
  background: rgba(var(--v-theme-primary), 0.4);
  border-color: rgb(var(--v-theme-primary));
  transform: scale(1.15);
  box-shadow: 0 2px 6px rgba(var(--v-theme-primary), 0.45);
}
.diff-comment-add:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}

/* The "comment exists" indicator — matches FileBrowserCodeView
   .gutter-comment-indicator. Always visible when present, doesn't
   require hover. */
.diff-comment-indicator {
  width: 18px;
  height: 18px;
  background: rgba(var(--v-theme-warning), 0.15);
  border: none;
  border-radius: 4px;
  cursor: pointer;
  color: rgb(var(--v-theme-warning));
  margin: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.12s ease, transform 0.12s ease;
}
.diff-comment-indicator:hover,
.diff-comment-indicator:focus {
  background: rgba(var(--v-theme-warning), 0.25);
  transform: scale(1.1);
}
.diff-comment-indicator:focus-visible {
  outline: 2px solid rgb(var(--v-theme-warning));
  outline-offset: 2px;
}

/* Split mode: the right cell already fills the right half of the
   row. The gutter is absolutely positioned at the LEFT edge of the
   right cell so it sits in the gap between the two columns' line
   numbers and the code text — the same place the file browser puts
   its gutter. The left cell gets the same treatment for del-only
   rows (2026-08-13). */
.diff-row-split {
  position: relative;
}
.diff-cell.right {
  position: relative;
}
.diff-cell.left {
  position: relative;
}
.diff-cell.right .diff-line-gutter,
.diff-cell.left .diff-line-gutter {
  position: absolute;
  left: 4px;
  top: 50%;
  transform: translateY(-50%);
  width: auto;
  margin-left: 0;
}
/* In split mode the gutter appears as an overlay, not a flex
   column — otherwise it would push the line-number column around
   and break the left/right cell alignment. Override the visibility
   transitions to only apply when hover/has-comment. */
.diff-row-split .diff-line-gutter {
  opacity: 0;
  pointer-events: none;
}
.diff-row-split.is-hovered .diff-line-gutter,
.diff-row-split.has-comment .diff-line-gutter {
  opacity: 1;
  pointer-events: auto;
}

/* ══════════════════════════════════════════════════════════════════
   Per-hunk discard button (spec hunk-level-discard-design.md §6.1.2)
   ══════════════════════════════════════════════════════════════════

   Opt-in rendering — the button is inside <div class="hunk-header">
   (which is role=button) and only shows when the parent passes
   `discardable=true` AND `onDiscardHunk`. The other 4 DiffPreview
   callsites don't pass `discardable`, so the button is invisible there.

   The button itself is hidden by default (opacity: 0) and revealed on
   hunk-header hover / focus-visible / confirmation state, so the
   header remains a clean clickable row until the user signals intent. */

.hunk-discard {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 18px;
  padding: 0 5px 0 3px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 3px;
  color: #ff9800;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  opacity: 0;
  transition:
    opacity 0.12s ease,
    background 0.12s ease,
    border-color 0.12s ease;
  flex-shrink: 0;
}
.hunk-header:hover .hunk-discard,
.hunk-discard:focus-visible,
.hunk-discard.is-confirming {
  opacity: 1;
}
.hunk-discard:hover {
  background: rgba(255, 152, 0, 0.1);
}
.hunk-discard:focus-visible {
  outline: 2px solid #ff9800;
  outline-offset: 1px;
  opacity: 1;
}
.hunk-discard:disabled {
  cursor: not-allowed;
  opacity: 0.3;
}
.hunk-discard.is-loading {
  opacity: 1;
}
.hunk-discard.is-confirming {
  background: rgba(255, 152, 0, 0.15);
  border-color: rgba(255, 152, 0, 0.4);
  animation: hunk-discard-pulse 1s ease-in-out infinite;
}
@keyframes hunk-discard-pulse {
  0%, 100% { background: rgba(255, 152, 0, 0.15); }
  50%      { background: rgba(255, 152, 0, 0.28); }
}
.hunk-discard-confirm-label {
  font-weight: 500;
}
@media (max-width: 760px) {
  .hunk-discard {
    width: 20px;
    padding: 0 2px;
  }
  .hunk-discard-confirm-label { display: none; }
}
</style>
